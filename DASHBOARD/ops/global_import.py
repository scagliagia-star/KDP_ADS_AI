"""REPORT GLOBALI AMAZON ADS: servizi della dashboard (anteprima, import, associazione campagne, ricalcolo). Scrivono SOLO nel DB locale (con UN backup per operazione,
prima della prima scrittura), in books.json solo per «Crea nuovo libro» e negli OUTPUT dell'analisi. Nessuna rete, nessuna Amazon: nessuna azione viene eseguita su Amazon Ads
(bid, pause, keyword e negative restano modifiche MANUALI)."""
import re
import sqlite3
from datetime import date

from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads import pipeline, registry
from kdp_ads.db import connect
from kdp_ads.logs import setup_logger

from . import OpResult, catalog
from .backup import backup_db
from .importing import close_logger

SLOTS = GS.SLOTS
SLOT_LABELS = GS.SLOT_LABELS


def read_conn(ws):
    """Connessione in SOLA LETTURA (None se il DB non esiste)."""
    if not ws.db_path.is_file():
        return None
    conn = sqlite3.connect(f"{ws.db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def books_dict(ws) -> dict:
    return registry.load_books(ws.books_path)


# ------------------------------------------------------------------ letture
def counts(ws) -> dict:
    conn = read_conn(ws)
    if conn is None:
        return {"UNASSIGNED": 0, "ASSIGNED": 0, "IGNORED": 0}
    try:
        return GS.counts(conn)
    finally:
        conn.close()


def campaign_list(ws, status="UNASSIGNED") -> list:
    conn = read_conn(ws)
    if conn is None:
        return []
    try:
        return GS.campaign_list(conn, status)
    finally:
        conn.close()


def book_campaigns(ws, book_id) -> list:
    conn = read_conn(ws)
    if conn is None:
        return []
    try:
        return GS.book_campaigns(conn, book_id)
    finally:
        conn.close()


def book_campaign_counts(ws) -> dict:
    conn = read_conn(ws)
    if conn is None:
        return {}
    try:
        return GS.book_campaign_counts(conn)
    finally:
        conn.close()


def backfill_candidates(ws) -> list:
    """Suggerimenti di BACKFILL (campagna Amazon <-> campagna storica) con prova sui target. Solo letture: nulla viene collegato senza conferma."""
    conn = read_conn(ws)
    if conn is None:
        return []
    try:
        return GS.backfill_candidates(conn)
    finally:
        conn.close()


def list_imports(ws) -> list:
    conn = read_conn(ws)
    if conn is None:
        return []
    try:
        return GS.list_imports(conn)
    finally:
        conn.close()


# ------------------------------------------------------------------ anteprima
def preview(ws, files: dict, marketplace: str | None = None, date_to: date | None = None) -> dict:
    """files = {slot: (nome, bytes)}. Nessuna scrittura. Ritorna {slot: {"preview": {...}} | {"error": "..."}}."""
    conn = read_conn(ws)
    books = books_dict(ws)
    out = {}
    for slot, (name, data) in files.items():
        try:
            parsed = G.parse_global_report(name, data)
            rtype, window = SLOTS[slot]
            if conn is not None and GS.has_schema(conn):
                pv = GS.preview_file(conn, parsed, window, books, marketplace, date_to, slot)
            else:                                           # DB senza tabelle: anteprima senza confronto con la mappa
                tmp = sqlite3.connect(":memory:")
                tmp.row_factory = sqlite3.Row
                GS.ensure_schema(tmp)
                pv = GS.preview_file(tmp, parsed, window, books, marketplace, date_to, slot)
                tmp.close()
            out[slot] = {"preview": pv, "parsed_ok": True}
        except G.GlobalReportError as e:
            out[slot] = {"error": str(e)}
    if conn is not None:
        conn.close()
    return out


# ------------------------------------------------------------------ import
def recalculate(ws, pairs, conn=None, logger=None) -> list:
    """Analisi + decisioni per ciascun (libro, marketplace). UN solo backup lo fa il chiamante. Ritorna [{book_id, marketplace, ok, message}]."""
    own = conn is None
    conn = conn or connect(ws.db_path)
    lg, _ = (logger, None) if logger else setup_logger(ws.logs_dir, "global_recalc")
    out = []
    try:
        for book_id, mp in sorted(pairs):
            try:
                a = pipeline.run_analysis(ws.settings, ws.mapping, ws.rules_path, book_id, ws.out_dir, lg, conn=conn, marketplace=mp)
                d = pipeline.run_decisions(ws.settings, ws.rules_path, book_id, ws.out_dir, lg, a["run_id"], conn=conn, marketplace=mp)
                out.append({"book_id": book_id, "marketplace": mp, "ok": True, "analysis_run": a["run_id"], "decision_run": d["run_id"], "message": "analisi e decisioni aggiornate"})
            except Exception as e:                            # un libro che fallisce non blocca gli altri
                out.append({"book_id": book_id, "marketplace": mp, "ok": False, "message": f"{type(e).__name__}: {e}"})
    finally:
        if logger is None:
            close_logger(lg)
        if own:
            conn.close()
    return out


def run_import(ws, files: dict, marketplace: str | None = None, date_to: date | None = None, recalc: bool = True, replace=()) -> OpResult:
    """Importa i report globali (slot -> (nome, bytes)). Importa tutte le campagne note; quelle nuove restano UNASSIGNED; un file non valido NON blocca gli altri.
    UN solo backup prima della prima scrittura, anche con 4 file.
    `replace` = slot per cui l'utente ha CONFERMATO «USA VERSIONE PIU' RECENTE» (stesso snapshot logico, contenuto diverso). Senza conferma una revisione non viene
    importata ne' sovrascritta: resta in `data['revisions_pending']`."""
    replace = set(replace or ())
    pv = preview(ws, files, marketplace, date_to)
    errors = [f"{SLOT_LABELS[s]}: {v['error']}" for s, v in pv.items() if "error" in v]
    pending_rev = [s for s, v in pv.items() if "preview" in v and v["preview"]["status"] == "REVISIONE" and s not in replace]
    todo = {s: v["preview"] for s, v in pv.items() if "preview" in v and not v["preview"]["problems"]
            and (v["preview"]["status"] == "NUOVO" or (v["preview"]["status"] == "REVISIONE" and s in replace))}
    warnings = [f"{SLOT_LABELS[s]}: {p}" for s, v in pv.items() if "preview" in v for p in v["preview"]["problems"]]
    notes = [f"{SLOT_LABELS[s]}: {v['preview']['warnings'][0]}" for s, v in pv.items() if "preview" in v and v["preview"]["status"] == "DUPLICATO"]
    if not todo:
        return OpResult(False, errors + warnings or ["Nessun file da importare"], data={"results": [], "notes": notes, "imported": 0, "revisions_pending": pending_rev})
    bk = backup_db(ws, "report_globali")
    conn = connect(ws.db_path)
    logger, _ = setup_logger(ws.logs_dir, "global_import")
    results, books_to_recalc, conflicts = [], set(), []
    try:
        books = registry.load_books(ws.books_path)
        order = sorted(todo, key=lambda s: (SLOTS[s][0], SLOTS[s][1]))
        for slot in order:
            name, data = files[slot]
            pvw = todo[slot]
            try:
                parsed = G.parse_global_report(name, data)
                r = GS.stage_file(conn, parsed, SLOTS[slot][1], pvw["marketplace"], books, date_to, replace=slot in replace)
                books_to_recalc |= r.get("affected") or set()            # libri che perdono i dati della versione sostituita
                results.append({"slot": slot, "file": name, **r, "preview": pvw})
            except (GS.GlobalImportError, G.GlobalReportError) as e:
                errors.append(f"{SLOT_LABELS[slot]}: {e}")
        mat = GS.materialize(conn, ws.settings)
        books_to_recalc |= mat["books"]
        conflicts += mat["conflicts"]
        recalc_res = recalculate(ws, books_to_recalc, conn, logger) if recalc and books_to_recalc else []
        imported = sum(1 for r in results if r["status"] in ("IMPORTED", "REPLACED"))
        cnt = GS.counts(conn)
        return OpResult(imported > 0 or not errors, errors, f"{imported} file importati",
                        {"results": results, "materialized": {"imports": mat["imports"], "rows": mat["rows"], "skipped_empty": mat["skipped_empty"]}, "conflicts": conflicts,
                         "counts": cnt, "recalc": recalc_res, "notes": notes, "imported": imported, "backup": str(bk) if bk else None,
                         "books": sorted(books_to_recalc), "revisions_pending": pending_rev, "replaced": [r["slot"] for r in results if r["status"] == "REPLACED"]})
    finally:
        close_logger(logger)
        conn.close()


# ------------------------------------------------------------------ associazioni
def associate(ws, amazon_campaign_id, book_id, internal_campaign_id=None, campaign_type=None, marketplace=None, recalc=True, backfill_of=None) -> OpResult:
    """Associazione CONFERMATA dall'utente. Smista i dati parcheggiati e ricalcola il libro (senza reimport). Una campagna Amazon nuova e' SEMPRE una campagna interna nuova;
    `backfill_of` (id storico) e' accettato solo con prova forte (GS.backfill_candidates livello ALTA)."""
    bk = backup_db(ws, "associa_campagna")
    conn = connect(ws.db_path)
    logger, _ = setup_logger(ws.logs_dir, "global_associate")
    try:
        r = GS.associate(conn, ws.settings, amazon_campaign_id, book_id, marketplace, internal_campaign_id, campaign_type, backfill_of=backfill_of)
        rec = recalculate(ws, r["books"], conn, logger) if recalc and r["books"] else []
        return OpResult(True, [], "Campagna collegata alla campagna storica (backfill)" if backfill_of else "Campagna associata", {**r, "recalc": rec, "backup": str(bk) if bk else None})
    except (GS.GlobalImportError, registry.RegistryError) as e:
        return OpResult(False, [str(e)])
    finally:
        close_logger(logger)
        conn.close()


def associate_many(ws, items: list, recalc=True) -> OpResult:
    """items = [{amazon_campaign_id, book_id, internal_campaign_id?, campaign_type?, marketplace?}]. Un backup e un ricalcolo per libro; una campagna non valida non blocca le altre."""
    bk = backup_db(ws, "associa_campagne")
    conn = connect(ws.db_path)
    logger, _ = setup_logger(ws.logs_dir, "global_associate")
    errors, done, books = [], 0, set()
    try:
        for it in items:
            try:
                r = GS.associate(conn, ws.settings, it["amazon_campaign_id"], it["book_id"], it.get("marketplace"), it.get("internal_campaign_id"), it.get("campaign_type"),
                                 backfill_of=it.get("backfill_of"))
                books |= r["books"]
                done += 1
            except (GS.GlobalImportError, registry.RegistryError) as e:
                errors.append(f"{it['amazon_campaign_id']}: {e}")
        rec = recalculate(ws, books, conn, logger) if recalc and books else []
        return OpResult(done > 0, errors, f"{done} campagne associate", {"associated": done, "recalc": rec, "backup": str(bk) if bk else None})
    finally:
        close_logger(logger)
        conn.close()


def ignore(ws, amazon_campaign_id) -> OpResult:
    conn = connect(ws.db_path)
    try:
        GS.ignore(conn, amazon_campaign_id)
        return OpResult(True, [], "Campagna ignorata (nessun dato cancellato)")
    except GS.GlobalImportError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def unignore(ws, amazon_campaign_id) -> OpResult:
    conn = connect(ws.db_path)
    try:
        GS.unignore(conn, amazon_campaign_id)
        return OpResult(True, [], "Campagna di nuovo da associare")
    except GS.GlobalImportError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def validate_new_book(ws, form: dict) -> tuple:
    """Validazione del form «Crea nuovo libro». Ritorna (campi puliti, errori). Nessuna scrittura.
    ASIN: 10 caratteri alfanumerici (ASIN o ISBN-10); marketplace obbligatorio; prezzo > 0; 0 < royalty <= prezzo (con royalty 0 il pareggio non ha senso);
    ACOS obiettivo in (0, 100]; pareggio = royalty / prezzo x 100 (mai fisso). Duplicato ASIN + marketplace: niente scrittura, si associa il libro esistente."""
    errors, out = [], {}
    asin = str(form.get("book_id") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        errors.append("ASIN non valido: servono 10 caratteri alfanumerici (es. B0H5MZP4QW)")
    title = str(form.get("title") or "").strip()
    if not title:
        errors.append("Titolo: campo obbligatorio")
    mp = str(form.get("marketplace") or "").strip().upper()
    if not mp:
        errors.append("Marketplace: campo obbligatorio")
    elif not registry.MP_RE.match(mp):
        errors.append("Marketplace: codice non valido (2-3 lettere, es. US, UK, DE)")

    def num(key, label):
        try:
            return catalog.parse_decimal(form.get(key), label)
        except Exception as e:                           # ValidationError del catalogo: messaggio gia' in italiano
            errors.append(str(e))
            return None
    price, roy, tgt = num("price", "Prezzo"), num("royalty", "Royalty"), num("target_acos", "ACOS obiettivo")
    if price is not None and price <= 0:
        errors.append("Il prezzo deve essere maggiore di zero")
    if roy is not None and (roy <= 0 or (price is not None and price > 0 and roy > price)):
        errors.append("La royalty deve essere maggiore di zero e non superare il prezzo")
    if tgt is not None and not (0 < tgt <= 100):
        errors.append("ACOS obiettivo non valido: deve essere compreso tra 0 (escluso) e 100")
    books = registry.load_books(ws.books_path)
    if asin in books and mp:
        try:
            have = set(registry.marketplaces_of(books[asin]))
        except registry.RegistryError:
            have = set()
        errors.append(f"Il libro {asin} esiste già" + (f" con il marketplace {mp}" if mp in have else f" (aggiungi il marketplace {mp} da «Gestione libri»)")
                      + ": associa le campagne al libro esistente invece di crearne uno nuovo")
    if not errors:
        out = {"book_id": asin, "title": title, "marketplace": mp, "price": price, "royalty": roy, "target_acos": tgt, "break_even_acos": registry.break_even_calc(price, roy)}
    return out, errors


def create_book_and_associate(ws, form: dict, amazon_campaign_ids: list, recalc=True) -> OpResult:
    """CREA NUOVO LIBRO (titolo, ASIN, marketplace, prezzo, royalty, ACOS obiettivo; break-even = royalty / prezzo x 100) e associa le campagne indicate.
    Validazione completa PRIMA di scrivere; scrittura atomica di books.json (nessun file corrotto in caso di errore)."""
    clean, errs = validate_new_book(ws, form)
    if errs:
        return OpResult(False, errs)
    f = {**clean, "currency": form.get("currency") or catalog.CURRENCY_BY_MARKETPLACE.get(clean["marketplace"], ""), "phase": form.get("phase") or "LAUNCH"}
    try:
        r = catalog.add_book(ws, f)
    except OSError as e:                                      # scrittura atomica fallita: books.json originale intatto
        return OpResult(False, [f"Scrittura di books.json non riuscita ({e}): il file originale non è stato modificato"])
    if not r.ok:
        return r
    out = associate_many(ws, [{"amazon_campaign_id": c, "book_id": clean["book_id"], "marketplace": clean["marketplace"]} for c in amazon_campaign_ids], recalc)
    out.data = {**(out.data or {}), "book": r.data}
    out.message = f"Libro creato (pareggio {clean['break_even_acos']:.2f}%) · {out.message}"
    out.ok = True if out.ok or not amazon_campaign_ids else out.ok
    return out
