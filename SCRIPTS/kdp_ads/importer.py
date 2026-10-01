"""Importer CSV Amazon Ads -> SQLite, multi-book / multi-campaign.
READ-ONLY rispetto ad Amazon: legge solo file locali. Contesto (libro, campagna, date, scope) SOLO da
metadata.json + books.json, mai dedotto dal nome file."""
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .mapping import CURRENCY_CODES, analyze_headers, dedupe_headers, MappingResult
from .parsing import parse_number, parse_ratio_pct, parse_date, ParseError
from . import registry
from .snapshots import period_id as make_period_id, snapshot_id as make_snapshot_id

NUMERIC = ["impressions", "clicks", "spend", "sales", "orders", "units", "bid",
           "cpc_reported", "roas_reported"]
RATIOS = ["acos_reported", "ctr_reported", "conv_rate_reported"]   # frazioni con virgola decimale nei report IT
CURRENCIES = set(CURRENCY_CODES)
TOLERANCE_PP = 0.5   # coerenza dei rapporti dichiarati (punti percentuali)

CTX_COLS = ["book_id", "campaign_id", "campaign_name", "campaign_type", "marketplace", "currency",
            "report_type", "report_scope", "date_from", "date_to", "import_timestamp", "source_filename",
            "period_id", "snapshot_id"]
DATA_COLS = ["csv_campaign_name", "ad_group", "target", "match_type", "added_as", "bid"]
METRIC_COLS = ["impressions", "clicks", "spend", "sales", "orders", "units", "acos_reported_pct",
               "ctr_reported_pct", "cpc_reported", "roas_reported", "conv_rate_reported_pct", "raw_json"]


@dataclass
class FileResult:
    filename: str
    status: str                       # IMPORTED / DRY_RUN_OK / SKIPPED_DUPLICATE / SKIPPED_INACTIVE / REJECTED
    book_id: str | None = None
    campaign_id: str | None = None
    campaign_type: str | None = None
    report_type: str | None = None
    report_scope: str | None = None
    period_id: str | None = None
    rows: int = 0
    import_id: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    problems: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    mapped: dict = field(default_factory=dict)
    informational: list = field(default_factory=list)
    duplicate_headers: list = field(default_factory=list)
    attribution_window: str | None = None


# ------------------------------------------------------------------ lettura file
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    text = _read_text(path)
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return (rows[0], rows[1:]) if rows else ([], [])


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ import di un file
def import_file(conn, path: Path, book: dict, meta: dict, settings: dict, mapping: dict, logger,
                dry_run: bool = False) -> FileResult:
    path = Path(path)
    res = FileResult(filename=path.name, status="REJECTED", book_id=str(meta["book_id"]),
                     campaign_id=str(meta["campaign_id"]), campaign_type=meta["campaign_type"])
    tag = f"{res.book_id}/{res.campaign_id}/{path.name}"
    digest = sha256_file(path)

    dup = conn.execute("SELECT import_id FROM imports WHERE book_id=? AND campaign_id=? AND file_sha256=?",
                       (res.book_id, res.campaign_id, digest)).fetchone()
    if dup:
        res.status, res.import_id = "SKIPPED_DUPLICATE", dup["import_id"]
        logger.info("SKIP %s: gia' importato (import_id=%s)", tag, dup["import_id"])
        return res

    headers, data = _read_table(path)
    if not headers:
        res.problems.append("file vuoto")
        return _reject(res, tag, logger)

    headers, dup_bases = dedupe_headers(headers)
    m: MappingResult = analyze_headers(headers, mapping, dup_bases)
    res.report_type, res.unmapped = m.report_type, m.unmapped
    res.mapped, res.informational = dict(m.field_to_header), m.informational
    res.duplicate_headers = m.duplicate_headers
    res.problems += m.ambiguous + [f"colonna obbligatoria mancante: {f}" for f in m.missing_required]

    currency = book["currency"]
    for h in headers:
        mm = re.search(r"\(([A-Z]{3})\)\s*$", h)
        if mm and mm.group(1) in CURRENCIES and mm.group(1) != currency:
            res.problems.append(f"header con valuta diversa da {currency}: {h!r}")
    if res.problems:
        return _reject(res, tag, logger)
    if m.duplicate_headers:
        res.warnings.append(f"header duplicati rinominati (valori in raw_json, non usati nei calcoli): "
                            f"{m.duplicate_headers} -> es. '{m.duplicate_headers[0]}__2'")

    # Scope e date: dal metadata, in base al tipo di report. Le colonne data del CSV (se ci sono) devono concordare.
    # Snapshot esplicito (metadata.snapshots[file]) oppure default per tipo di report (chiavi target_*/search_term_*).
    snap = next((s for s in meta.get("snapshots") or [] if s["file"] == path.name), None)
    if snap is not None:
        if snap.get("report_type") and snap["report_type"] != m.report_type:
            res.problems.append(f"snapshot dichiara report_type {snap['report_type']} ma il file e' {m.report_type}")
            return _reject(res, tag, logger)
        res.report_scope, res.date_from, res.date_to = snap["report_scope"], snap.get("date_from"), snap.get("date_to")
    else:
        prefix = "search_term" if m.report_type == "SEARCH_TERM" else "target"
        res.report_scope = meta[f"{prefix}_report_scope"]
        res.date_from = meta.get(f"{prefix}_date_from")
        res.date_to = meta.get(f"{prefix}_date_to")
    res.period_id = make_period_id(res.report_scope, res.date_from, res.date_to)
    snap_id = make_snapshot_id(res.book_id, res.campaign_id, m.report_type, res.report_scope, res.date_from, res.date_to)
    clash = conn.execute("SELECT import_id, source_filename FROM imports WHERE snapshot_id=?", (snap_id,)).fetchone()
    if clash:
        res.problems.append(f"SNAPSHOT_CONFLICT: lo snapshot {res.period_id} ({m.report_type}) di questa campagna e' gia' stato importato "
                            f"da un file diverso (import {clash['import_id']}, {clash['source_filename']}). Non sovrascrivo ne' fondo: "
                            "controlla le date in metadata.json o rimuovi/archivia il file superato.")
        return _reject(res, tag, logger)
    if res.report_scope == "UNKNOWN":
        res.warnings.append("scope/date del report NON noti (metadata UNKNOWN): trend UNKNOWN per questo file")
    if res.report_scope == "LIFETIME" and not res.date_to:
        res.warnings.append("report LIFETIME senza date_to nel metadata")
    if res.date_from and res.date_to:
        days = (date.fromisoformat(res.date_to) - date.fromisoformat(res.date_from)).days + 1
        if m.report_type == "SEARCH_TERM" and days > settings["search_term_max_days"]:
            res.warnings.append(f"Search Term con intervallo di {days} giorni (> {settings['search_term_max_days']})")

    # Finestra di attribuzione: metadata vs header; se entrambi presenti devono coincidere.
    meta_win = (snap or {}).get("attribution_window") or meta.get("attribution_window")
    if meta_win and m.attribution_window and meta_win.strip().lower() != m.attribution_window.lower():
        res.problems.append(f"attribution_window del metadata ({meta_win}) diversa da quella negli header ({m.attribution_window})")
        return _reject(res, tag, logger)
    res.attribution_window = m.attribution_window or meta_win
    win_src = "headers" if m.attribution_window else ("metadata" if meta_win else "unknown")
    if win_src == "unknown":
        res.warnings.append("finestra di attribuzione NON nota (ne' header ne' metadata)")

    f2h = m.field_to_header
    idx = {h: i for i, h in enumerate(headers)}

    def get(row, canon):
        h = f2h.get(canon)
        if h is None:
            return None
        i = idx[h]
        return row[i] if i < len(row) else None

    parsed, errors = [], []
    for n, row in enumerate(data, start=2):
        try:
            rec = {c: parse_number(get(row, c)) for c in NUMERIC}
            rec.update({c: parse_ratio_pct(get(row, c)) for c in RATIOS})
            csv_from, csv_to = parse_date(get(row, "start_date")), parse_date(get(row, "end_date"))
        except ParseError as e:
            errors.append(f"riga {n}: {e}")
            continue
        if csv_from and res.date_from and csv_from.isoformat() != res.date_from:
            errors.append(f"riga {n}: Start Date del CSV ({csv_from}) diversa dal metadata ({res.date_from})")
        if csv_to and res.date_to and csv_to.isoformat() != res.date_to:
            errors.append(f"riga {n}: End Date del CSV ({csv_to}) diversa dal metadata ({res.date_to})")
        for c in ("campaign_name", "ad_group", "target", "match_type", "search_term", "added_as", "currency"):
            v = get(row, c)
            rec[c] = v.strip() if v is not None else None
        for c in ("clicks", "spend", "sales", "orders"):
            if rec[c] is None:
                errors.append(f"riga {n}: '{c}' vuoto")
        if rec["currency"] and rec["currency"] != currency:
            errors.append(f"riga {n}: valuta {rec['currency']} diversa da {currency}")
        rec["raw_json"] = json.dumps(dict(zip(headers, row)), ensure_ascii=False)
        parsed.append(rec)
    if errors:
        res.problems += errors[:20] + ([f"... altri {len(errors) - 20} errori"] if len(errors) > 20 else [])
        return _reject(res, tag, logger)

    csv_camps = sorted({r["campaign_name"] for r in parsed if r["campaign_name"]})
    if csv_camps and csv_camps != [meta["campaign_name"]]:
        res.warnings.append(f"campaign_name nel CSV {csv_camps} diverso dal metadata '{meta['campaign_name']}' (uso il metadata)")
    _check_consistency(parsed, res)

    res.rows = len(parsed)
    if dry_run:
        res.status = "DRY_RUN_OK"
        logger.info("DRY-RUN %s: %s, scope=%s, %d righe, date %s..%s", tag, m.report_type, res.report_scope,
                    res.rows, res.date_from, res.date_to)
        _log_warnings(res, tag, logger)
        return res

    ts = datetime.now().isoformat(timespec="seconds")
    ctx = {"book_id": res.book_id, "campaign_id": res.campaign_id, "campaign_name": meta["campaign_name"],
           "campaign_type": meta["campaign_type"], "marketplace": meta["marketplace"], "currency": currency,
           "report_type": m.report_type, "report_scope": res.report_scope, "date_from": res.date_from,
           "date_to": res.date_to, "import_timestamp": ts, "source_filename": path.name,
           "period_id": res.period_id, "snapshot_id": snap_id}
    with conn:
        cur = conn.execute(
            f"INSERT INTO imports ({','.join(CTX_COLS)},file_path,file_sha256,attribution_window,attribution_source,row_count) "
            f"VALUES ({','.join('?' * (len(CTX_COLS) + 5))})",
            [ctx[c] for c in CTX_COLS] + [str(path), digest, res.attribution_window, win_src, res.rows])
        iid = cur.lastrowid
        is_st = m.report_type == "SEARCH_TERM"
        table = "search_term_rows" if is_st else "target_rows"
        cols = ["import_id"] + CTX_COLS + DATA_COLS + (["search_term"] if is_st else []) + METRIC_COLS
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        for r in parsed:
            vals = [iid] + [ctx[c] for c in CTX_COLS] + [
                r["campaign_name"], r["ad_group"], r["target"], r["match_type"], r["added_as"], r["bid"]]
            if is_st:
                vals.append(r["search_term"])
            vals += [r["impressions"], r["clicks"], r["spend"], r["sales"], r["orders"], r["units"],
                     r["acos_reported"], r["ctr_reported"], r["cpc_reported"], r["roas_reported"],
                     r["conv_rate_reported"], r["raw_json"]]
            conn.execute(sql, vals)
    res.status, res.import_id = "IMPORTED", iid
    logger.info("IMPORTED %s: %s, scope=%s, %d righe, tipo=%s, date %s..%s, finestra=%s",
                tag, m.report_type, res.report_scope, res.rows, meta["campaign_type"], res.date_from, res.date_to,
                res.attribution_window)
    _log_warnings(res, tag, logger)
    return res


def _log_warnings(res, tag, logger):
    for w in res.warnings:
        logger.warning("%s: %s", tag, w)
    if res.unmapped:
        logger.warning("%s: colonne NON interpretate (solo raw_json): %s", tag, res.unmapped)


def _check_consistency(parsed, res: FileResult):
    """ACOS/CTR/CPC dichiarati vs ricalcolati: solo avvisi, nessuna correzione."""
    bad = {"ACOS": 0, "CTR": 0, "CPC": 0}
    for r in parsed:
        if r["acos_reported"] is not None and r["sales"]:
            if abs(r["spend"] / r["sales"] * 100 - r["acos_reported"]) > TOLERANCE_PP:
                bad["ACOS"] += 1
        if r["ctr_reported"] is not None and r["impressions"]:
            if abs(r["clicks"] / r["impressions"] * 100 - r["ctr_reported"]) > TOLERANCE_PP:
                bad["CTR"] += 1
        if r["cpc_reported"] is not None and r["clicks"]:
            if abs(r["spend"] / r["clicks"] - r["cpc_reported"]) > 0.02:
                bad["CPC"] += 1
    for k, n in bad.items():
        if n:
            res.warnings.append(f"{k} dichiarato incoerente con il ricalcolo in {n}/{len(parsed)} righe")


def _reject(res: FileResult, tag: str, logger) -> FileResult:
    res.status = "REJECTED"
    logger.error("REJECTED %s: %s", tag, "; ".join(res.problems))
    if res.unmapped:
        logger.error("%s: colonne non interpretate: %s", tag, res.unmapped)
    return res


# ------------------------------------------------------------------ scansione INPUT
def _sync_book(conn, full, view, eco):
    """Salva l'economia del marketplace in book_marketplaces; la riga `books` (legacy, una per libro) riflette il marketplace
    principale. L'economia di marketplace diversi non si mescola e non si converte."""
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO book_marketplaces (book_id,marketplace,currency,price,royalty_per_copy,break_even_acos_configured,
           break_even_acos_calculated,break_even_acos,target_acos,phase,active,synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(book_id,marketplace) DO UPDATE SET currency=excluded.currency, price=excluded.price,
           royalty_per_copy=excluded.royalty_per_copy, break_even_acos_configured=excluded.break_even_acos_configured,
           break_even_acos_calculated=excluded.break_even_acos_calculated, break_even_acos=excluded.break_even_acos,
           target_acos=excluded.target_acos, phase=excluded.phase, active=excluded.active, synced_at=excluded.synced_at""",
        (str(view["book_id"]), view["marketplace"], view["currency"], float(view["price"]), float(view["royalty_per_copy"]),
         eco["break_even_acos_configured"], eco["break_even_acos_calculated"], eco["break_even_acos"], float(view["target_acos"]),
         view["phase"], 1 if view.get("active", True) else 0, now))
    if view["marketplace"] != registry.primary_marketplace(full):
        conn.execute("INSERT OR IGNORE INTO books (book_id,title,synced_at) VALUES (?,?,?)", (str(view["book_id"]), view["title"], now))
        return
    conn.execute(
        """INSERT INTO books (book_id,title,marketplace,currency,price,royalty_per_copy,break_even_acos_configured,
           break_even_acos_calculated,break_even_acos,target_acos,phase,active,synced_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(book_id) DO UPDATE SET title=excluded.title, marketplace=excluded.marketplace,
           currency=excluded.currency, price=excluded.price, royalty_per_copy=excluded.royalty_per_copy,
           break_even_acos_configured=excluded.break_even_acos_configured,
           break_even_acos_calculated=excluded.break_even_acos_calculated, break_even_acos=excluded.break_even_acos,
           target_acos=excluded.target_acos, phase=excluded.phase, active=excluded.active, synced_at=excluded.synced_at""",
        (str(view["book_id"]), view["title"], view["marketplace"], view["currency"], float(view["price"]),
         float(view["royalty_per_copy"]), eco["break_even_acos_configured"], eco["break_even_acos_calculated"],
         eco["break_even_acos"], float(view["target_acos"]), view["phase"], 1 if view.get("active", True) else 0, now))


def _sync_campaign(conn, meta):
    conn.execute(
        """INSERT INTO campaigns (book_id,campaign_id,campaign_name,campaign_type,marketplace,currency,metadata_json,synced_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(book_id,campaign_id) DO UPDATE SET campaign_name=excluded.campaign_name,
           campaign_type=excluded.campaign_type, marketplace=excluded.marketplace, currency=excluded.currency,
           metadata_json=excluded.metadata_json, synced_at=excluded.synced_at""",
        (str(meta["book_id"]), str(meta["campaign_id"]), meta["campaign_name"], meta["campaign_type"],
         meta["marketplace"], meta["currency"], json.dumps(meta, ensure_ascii=False),
         datetime.now().isoformat(timespec="seconds")))


def import_all(conn, settings, mapping, logger, only_book: str | None = None,
               only_campaign: str | None = None, dry_run: bool = False,
               notes: list | None = None, only_file: str | None = None, only_marketplace: str | None = None) -> list[FileResult]:
    """Scansiona INPUT/<BOOK_ID>/<CAMPAIGN_ID>/ (struttura legacy) e INPUT/<BOOK_ID>/<MARKETPLACE>/<CAMPAIGN_ID>/. Ogni campagna e'
    validata con il suo metadata.json e importata con l'economia (valuta, prezzo, royalty) del SUO marketplace."""
    results: list[FileResult] = []
    books = registry.load_books(settings["books_path"])
    base = Path(settings["input_dir"])
    tol = settings.get("break_even_mismatch_tolerance_pp", 0.5)
    want_mp = str(only_marketplace).strip().upper() if only_marketplace else None

    def rejected(name, book_id, campaign_id, problems):
        r = FileResult(filename=name, status="REJECTED", book_id=book_id, campaign_id=campaign_id, problems=problems)
        logger.error("REJECTED %s/%s/%s: %s", book_id, campaign_id, name, "; ".join(problems))
        results.append(r)

    for book_dir in sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []:
        bid = book_dir.name
        if only_book and bid != only_book:
            continue
        full = books.get(bid)
        mps, book_problem = {}, None
        if full is not None:
            try:
                mps = registry.marketplaces_of(full)
            except registry.RegistryError as e:
                book_problem = [f"libro '{bid}' in books.json non valido: {e}"]
        cache: dict = {}

        def view_for(mp):
            """(vista, problemi, economia) del marketplace; validazione e sync una sola volta per marketplace."""
            if mp not in cache:
                v = mps[mp]
                errs = registry.validate_book(v)
                if errs:
                    cache[mp] = (None, [f"libro '{bid}' marketplace {mp} in books.json non valido: " + "; ".join(errs)], None)
                else:
                    eco, warns = registry.book_economics(v, tol)
                    for w in warns:
                        logger.warning(w)
                        if notes is not None:
                            notes.append(w)
                    if not dry_run:
                        _sync_book(conn, full, v, eco)
                        conn.commit()
                    cache[mp] = (v, [], eco)
            return cache[mp]
        seen_ids: dict = {}
        for loc in registry.iter_campaigns(base, bid, full):
            cid = loc.campaign_id
            if only_campaign and cid != only_campaign:
                continue
            if want_mp and loc.marketplace and loc.marketplace != want_mp:
                continue
            csvs = sorted(loc.path.glob("*.csv"))
            if only_file:
                csvs = [f for f in csvs if f.name == only_file]
            if full is None or book_problem:
                probs = book_problem or [f"book_id '{bid}' (cartella) non presente in books.json"]
                for f in csvs or [Path("(cartella)")]:
                    rejected(f.name, bid, cid, probs)
                continue
            meta, errs = registry.load_metadata(loc.path)
            if meta is not None and not errs:
                errs = registry.check_campaign_against_book(meta, full, bid, cid, loc.marketplace)
            if meta is not None and not errs:
                mp_here = str(meta["marketplace"]).strip().upper()
                if seen_ids.setdefault(cid, mp_here) != mp_here:
                    errs = [f"campaign_id {cid} usato in due marketplace ({seen_ids[cid]} e {mp_here}): gli ID campagna sono unici nel libro"]
            if errs:
                for f in csvs or [Path("(cartella)")]:
                    rejected(f.name, bid, cid, list(errs))
                continue
            mp = str(meta["marketplace"]).strip().upper()
            if want_mp and mp != want_mp:
                continue
            view, vprobs, _eco = view_for(mp)
            if view is None:
                for f in csvs or [Path("(cartella)")]:
                    rejected(f.name, bid, cid, list(vprobs))
                continue
            if not view.get("active", True):
                for f in csvs:
                    r = FileResult(filename=f.name, status="SKIPPED_INACTIVE", book_id=bid, campaign_id=cid,
                                   campaign_type=meta["campaign_type"], warnings=["libro non attivo (active=false)"])
                    logger.info("SKIP %s/%s/%s: libro non attivo", bid, cid, f.name)
                    results.append(r)
                continue
            if not dry_run:
                _sync_campaign(conn, meta)
                conn.commit()
            for f in csvs:
                results.append(import_file(conn, f, view, meta, settings, mapping, logger, dry_run=dry_run))
    return results
