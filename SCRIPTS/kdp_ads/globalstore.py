"""REPORT GLOBALI AMAZON ADS: persistenza, mappatura Campaign ID -> libro e smistamento verso le tabelle del motore. Solo locale: nessuna rete, nessuna Amazon,
nessuna azione su Amazon Ads (il sistema resta di sola analisi e supporto decisionale).

FLUSSO
  1. i report (Targeting/Search Term, 7D/30D) vengono PARCHEGGIATI cosi' come sono in `global_report_imports` / `global_report_rows` (anche per le campagne sconosciute);
  2. `campaign_book_mapping` lega l'ID campagna Amazon (chiave primaria, MAI il nome) a un libro e a un id campagna interno: UNASSIGNED / ASSIGNED / IGNORED;
  3. per le campagne ASSIGNED le righe vengono "materializzate" in `imports` / `target_rows` / `search_term_rows` (stesse tabelle del flusso legacy: il motore di
     analisi e decisioni non cambia e la logica period-aware resta quella esistente: un periodo = uno snapshot, mai sommato ad altri);
  4. quando una campagna viene associata in seguito, i dati gia' parcheggiati si materializzano senza reimport.

MULTI-ACCOUNT (due o piu' account Amazon Ads nello stesso DB, nessuna collisione)
  - l'ID account inserzionista del CSV e' parte dell'identita' operativa: una campagna e' identificata da (account, ID campagna Amazon), MAI dal solo ID campagna; gli ID di
    campagna / gruppo annunci / target e i termini di ricerca possono ripetersi identici fra account diversi e non si fondono mai;
  - un report appartiene a UN solo account: piu' account nello stesso file, o nessun ID account, sono bloccati; lo snapshot logico include l'account (due account con stesso
    tipo/finestra/date sono due snapshot validi e indipendenti, revisioni comprese); mapping, IGNORED e backfill sono per account;
  - le campagne storiche (legacy) non hanno ne' ID Amazon ne' account: restano "legacy_account_unknown", mai assegnate in automatico a un account.

REGOLE
  - il file intero e' RECENT_7D o RECENT_30D secondo la scelta dell'utente; "Intervallo di date" resta metadato di riga;
  - una riga ASSENTE dal report 7D NON e' zero traffico (vedi MISSING_7D nel decision layer);
  - Search Term con termine vuoto: conservato qui, mai materializzato (nessuna promozione/negativa su un valore vuoto);
  - nessun overwrite silenzioso: stesso file (hash) = nessuna modifica; stesso snapshot logico (tipo, finestra, date, marketplace, account) con contenuto diverso =
    REVISION_AVAILABLE: si propone "USA VERSIONE PIU' RECENTE"; solo se confermata la versione precedente diventa SUPERSEDED (audit conservato), i suoi dati derivati
    si rimuovono dalle tabelle del motore e la nuova si smista (una sola versione attiva, nessun doppio conteggio);
  - un'associazione gia' fatta non si cambia in silenzio; DUE Campaign ID Amazon diverse sono SEMPRE due campagne diverse (mai fuse, nemmeno nello stesso libro);
    un collegamento a una campagna storica e' ammesso solo come BACKFILL confermato dall'utente con prova forte (vedi backfill_candidates);
  - un'associazione per SOMIGLIANZA e' solo un SUGGERIMENTO: diventa definitiva soltanto con la conferma esplicita dell'utente."""
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta

from . import globalreport as G

STATUSES = ("UNASSIGNED", "ASSIGNED", "IGNORED")
SLOTS = {"TARGETING_7D": ("TARGETING", "RECENT_7D"), "TARGETING_30D": ("TARGETING", "RECENT_30D"),
         "SEARCH_TERM_7D": ("SEARCH_TERM", "RECENT_7D"), "SEARCH_TERM_30D": ("SEARCH_TERM", "RECENT_30D")}
SLOT_LABELS = {"TARGETING_7D": "Targeting 7D", "TARGETING_30D": "Targeting 30D", "SEARCH_TERM_7D": "Search Term 7D", "SEARCH_TERM_30D": "Search Term 30D"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS global_report_imports (
    gimport_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL CHECK (report_type IN ('TARGETING','SEARCH_TERM')),
    window_label TEXT NOT NULL CHECK (window_label IN ('RECENT_7D','RECENT_30D')), window_days INTEGER NOT NULL,
    date_from TEXT NOT NULL, date_to TEXT NOT NULL, period_id TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT,
    file_name TEXT NOT NULL, file_sha256 TEXT NOT NULL UNIQUE, imported_at TEXT NOT NULL,
    row_count INTEGER, campaign_count INTEGER, empty_search_term_rows INTEGER, warnings_json TEXT, parser_version INTEGER,
    account_id TEXT, account_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')),     -- UNA sola versione ATTIVA per snapshot logico; le altre restano come audit
    revision INTEGER NOT NULL DEFAULT 1, supersedes_gimport_id INTEGER, superseded_by_gimport_id INTEGER, superseded_at TEXT
);
-- snapshot logico = tipo report + finestra + date dello snapshot + marketplace + account: al massimo UNA versione attiva
CREATE UNIQUE INDEX IF NOT EXISTS ux_gri_active ON global_report_imports(report_type, window_label, date_from, date_to, marketplace, COALESCE(account_id, '')) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS global_report_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gimport_id INTEGER NOT NULL REFERENCES global_report_imports(gimport_id),
    amazon_campaign_id TEXT NOT NULL, account_id TEXT, account_name TEXT, campaign_name TEXT, portfolio_id TEXT, portfolio_name TEXT, ad_group_id TEXT, ad_group_name TEXT,
    target_id TEXT, targeting TEXT, match_type_raw TEXT, search_term TEXT, empty_search_term INTEGER NOT NULL DEFAULT 0,
    row_date_from TEXT, row_date_to TEXT, bid REAL, target_state TEXT,
    impressions REAL, clicks REAL, spend REAL, orders REAL, sales REAL, units REAL,
    materialized INTEGER NOT NULL DEFAULT 0,          -- 0 da smistare, 1 smistata, 2 esclusa (termine di ricerca vuoto)
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_grr_import ON global_report_rows(gimport_id);
-- identita' = (account, ID campagna Amazon): lo stesso ID in due account sono DUE campagne. account_id '' = account non noto (legacy_account_unknown), mai NULL (UNIQUE efficace).
CREATE TABLE IF NOT EXISTS campaign_book_mapping (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT '', amazon_campaign_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNASSIGNED' CHECK (status IN ('UNASSIGNED','ASSIGNED','IGNORED')),
    book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT,
    suggested_book_id TEXT, suggestion_confidence TEXT, suggestion_reason TEXT,
    first_seen TEXT, last_seen TEXT, account_name_last_seen TEXT, campaign_name_last_seen TEXT, portfolio_id_last_seen TEXT, portfolio_last_seen TEXT, ad_group_last_seen TEXT,
    detected_type TEXT, detected_kind TEXT, backfill_of TEXT, legacy_account_confirmed INTEGER NOT NULL DEFAULT 0, assigned_at TEXT, updated_at TEXT,
    UNIQUE (account_id, amazon_campaign_id)
);
CREATE INDEX IF NOT EXISTS ix_cbm_book_acct ON campaign_book_mapping(book_id, status);
"""
# colonne di tracciabilita' aggiunte alle tabelle di riga del motore (nullable: le righe legacy restano NULL)
ROW_EXTRA_COLUMNS = [("amazon_campaign_id", "TEXT"), ("amazon_ad_group_id", "TEXT"), ("amazon_target_id", "TEXT"), ("row_date_from", "TEXT"), ("row_date_to", "TEXT"),
                     ("amazon_account_id", "TEXT")]
CAMPAIGN_EXTRA_COLUMNS = [("amazon_account_id", "TEXT")]                 # account Ads della campagna interna (NULL = legacy_account_unknown)


class GlobalImportError(ValueError):
    """Operazione non valida (messaggio italiano). Nessuna modifica al DB."""


def _cols(conn, table) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn, table, columns) -> None:
    have = _cols(conn, table)
    if have:                                                  # tabella esistente: colonne mancanti (migrazione additiva)
        for name, typ in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def _migrate_mapping_account_scope(conn) -> None:
    """Un DB creato con la PRIMA forma (chiave = solo ID campagna Amazon) viene portato alla chiave (account, ID campagna) ricopiando TUTTE le righe: nessun dato perso.
    Idempotente: con la forma nuova non fa nulla. (Il DB reale dell'utente e' ancora a v5 e non ha mai avuto questa tabella.)"""
    cols = _cols(conn, "campaign_book_mapping")
    if not cols or "mapping_id" in cols:
        return
    n_old = conn.execute("SELECT COUNT(*) FROM campaign_book_mapping").fetchone()[0]
    conn.execute("ALTER TABLE campaign_book_mapping RENAME TO campaign_book_mapping_v6_old")      # la vecchia tabella resta come audit: nessuna cancellazione
    conn.executescript(SCHEMA)
    keep = [c for c in ("amazon_campaign_id", "status", "book_id", "marketplace", "campaign_id", "campaign_type", "suggested_book_id", "suggestion_confidence",
                        "suggestion_reason", "first_seen", "last_seen", "campaign_name_last_seen", "portfolio_id_last_seen", "portfolio_last_seen", "ad_group_last_seen",
                        "detected_type", "assigned_at", "updated_at") if c in cols]
    conn.execute(f"INSERT INTO campaign_book_mapping (account_id, {','.join(keep)}) SELECT COALESCE(account_id, ''), {','.join(keep)} FROM campaign_book_mapping_v6_old")
    if conn.execute("SELECT COUNT(*) FROM campaign_book_mapping").fetchone()[0] != n_old:
        raise sqlite3.DatabaseError("migrazione campaign_book_mapping: conteggio righe diverso")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    _add_columns(conn, "global_report_imports", [("account_name", "TEXT")])
    _add_columns(conn, "global_report_rows", [("account_id", "TEXT"), ("account_name", "TEXT")])
    _migrate_mapping_account_scope(conn)
    _add_columns(conn, "campaign_book_mapping", [("detected_kind", "TEXT")])           # tipo letto (AUTO / PHRASE / EXACT / COMPETITOR / AMBIGUOUS)
    for table in ("target_rows", "search_term_rows"):
        _add_columns(conn, table, ROW_EXTRA_COLUMNS)
    _add_columns(conn, "campaigns", CAMPAIGN_EXTRA_COLUMNS)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_grr_campaign ON global_report_rows(account_id, amazon_campaign_id, gimport_id)")
    conn.commit()


def has_schema(conn) -> bool:
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='campaign_book_mapping'").fetchone())
    except sqlite3.Error:
        return False


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(conn, sql, params=()):
    prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.row_factory = prev


# ------------------------------------------------------------------ suggerimento libro (solo SUGGERIMENTO)
_STOP = frozenset("a an the for of and to in on with by my your from at is are as or day days".split())


def _stem(t: str) -> str:
    if len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def _tokens(text) -> set:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}.*$", " ", s)                      # data/ora automatiche nei nomi campagna Amazon
    return {_stem(t) for t in re.findall(r"[a-z0-9]+", s) if t not in _STOP}


def suggest_book(summary: dict, books: dict, portfolio_book: dict | None = None) -> tuple:
    """(book_id|None, confidenza alta/media/bassa|None, motivo). SOLO un suggerimento: mai un'associazione automatica.
    1) stesso portfolio di campagne gia' ASSEGNATE a un libro => alta;
    2) copertura dei termini del portfolio (o del nome campagna) nel titolo di un libro: tutti i termini e >= 2 => alta; tutti ma un solo termine => media;
       meta' o piu' => bassa. Pareggi fra libri => nessun suggerimento."""
    pid = summary.get("portfolio_id")
    acct = acct_key(summary.get("account_id"))
    if pid and portfolio_book and portfolio_book.get((acct, pid)):                    # l'ID portfolio vale solo dentro lo stesso account
        return portfolio_book[(acct, pid)], "alta", "stesso portfolio di campagne già associate a questo libro"
    pf = _tokens(summary.get("portfolio_name")) if summary.get("portfolio_name") else set()
    name_t = _tokens(summary.get("name"))
    probe, src = (pf, "portfolio") if pf else (name_t - {"auto", "frase", "esatta", "exact", "phrase", "competitor", "prodotto", "broad"}, "nome campagna")
    if not probe:
        return None, None, None
    scored = []
    for bid, b in books.items():
        cover = len(probe & _tokens(b.get("title"))) / len(probe)
        if cover > 0:
            scored.append((cover, len(probe & _tokens(b.get("title"))), bid))
    if not scored:
        return None, None, None
    scored.sort(reverse=True)
    best = scored[0]
    if len(scored) > 1 and scored[1][:2] == best[:2]:
        return None, None, None
    cover, shared, bid = best
    conf = "alta" if cover == 1 and len(probe) >= 2 else "media" if cover == 1 else "bassa" if cover >= 0.5 else None
    if conf is None:
        return None, None, None
    return bid, conf, f"il {src} «{summary.get('portfolio_name') if src == 'portfolio' else summary.get('name')}» condivide {shared} termini con il titolo"


def acct_key(account_id) -> str:
    """Chiave normalizzata dell'account: '' = account non noto (mai NULL, cosi' la UNIQUE funziona)."""
    return str(account_id or "").strip()


def _portfolio_book(conn) -> dict:
    """{(account, portfolio_id): book_id}: gli ID portfolio possono ripetersi fra account diversi."""
    out = {}
    for r in _rows(conn, "SELECT account_id AS a, portfolio_id_last_seen AS p, book_id, COUNT(*) n FROM campaign_book_mapping WHERE status='ASSIGNED' AND portfolio_id_last_seen IS NOT NULL "
                         "GROUP BY 1, 2, 3 ORDER BY n DESC"):
        out.setdefault((r["a"], r["p"]), r["book_id"])
    return out


# ------------------------------------------------------------------ snapshot logico e revisioni
def _file_account(parsed) -> str | None:
    accs = sorted({r.get("account_id") for r in parsed["rows"] if r.get("account_id")})
    return accs[0] if len(accs) == 1 else None


def file_account_problem(parsed) -> str | None:
    """Un report Amazon appartiene a UN solo account. Piu' account, o righe senza ID account, rendono il file non attribuibile: NON si importa."""
    ids = sorted({r.get("account_id") for r in parsed["rows"] if r.get("account_id")})
    if len(ids) > 1:
        return f"Il file contiene {len(ids)} account Amazon Ads diversi ({', '.join(ids)}): ogni report deve appartenere a un solo account. Esporta un report per account"
    if not ids:
        return "Il file non contiene l'ID account inserzionista: non posso sapere da quale account Amazon Ads proviene. Esporta di nuovo il report da Amazon Ads"
    if any(not r.get("account_id") for r in parsed["rows"]):
        return "Alcune righe del file non hanno l'ID account inserzionista: file non attribuibile a un solo account"
    return None


def active_snapshot(conn, report_type, window_label, date_from, date_to, marketplace, account_id=None):
    """Versione ATTIVA dello stesso snapshot logico (tipo report + finestra + date + marketplace + account), oppure None."""
    r = _rows(conn, "SELECT * FROM global_report_imports WHERE status='ACTIVE' AND report_type=? AND window_label=? AND date_from=? AND date_to=? AND marketplace=? "
                    "AND COALESCE(account_id,'')=COALESCE(?, '')", (report_type, window_label, date_from, date_to, marketplace, account_id))
    return r[0] if r else None


def _totals_rows(rows) -> dict:
    return {"rows": len(rows), "clicks": sum(r["clicks"] or 0 for r in rows), "spend": round(sum(r["spend"] or 0 for r in rows), 2),
            "orders": sum(r["orders"] or 0 for r in rows), "sales": round(sum(r["sales"] or 0 for r in rows), 2), "campaigns": len({r["campaign_id"] for r in rows})}


def _revision_info(conn, act: dict, parsed: dict) -> dict:
    o = _rows(conn, "SELECT COUNT(*) rows, COALESCE(SUM(clicks),0) clicks, ROUND(COALESCE(SUM(spend),0),2) spend, COALESCE(SUM(orders),0) orders, ROUND(COALESCE(SUM(sales),0),2) sales, "
                    "COUNT(DISTINCT amazon_campaign_id) campaigns FROM global_report_rows WHERE gimport_id=?", (act["gimport_id"],))[0]
    return {"old": {**o, "file": act["file_name"], "imported_at": act["imported_at"], "gimport_id": act["gimport_id"], "revision": act["revision"]}, "new": _totals_rows(parsed["rows"])}


def retire_import(conn, gimport_id: int, now: str | None = None, by: int | None = None) -> set:
    """Marca SUPERSEDED una versione e rimuove dalle tabelle del MOTORE i dati derivati da quella versione (imports + target_rows/search_term_rows creati dallo smistamento):
    cosi' non c'e' doppio conteggio. I dati originali restano in global_report_rows (audit). Ritorna {(book_id, marketplace)} da ricalcolare. Nessun DROP di tabelle."""
    g = conn.execute("SELECT file_sha256 FROM global_report_imports WHERE gimport_id=?", (gimport_id,)).fetchone()
    affected = set()
    ids = []
    for r in _rows(conn, "SELECT import_id, book_id, marketplace FROM imports WHERE file_sha256 LIKE ? || ':%'", (g["file_sha256"],)):
        ids.append(r["import_id"])
        affected.add((r["book_id"], r["marketplace"]))
    for table in ("target_rows", "search_term_rows"):
        conn.executemany(f"DELETE FROM {table} WHERE import_id=?", [(i,) for i in ids])
    conn.executemany("DELETE FROM imports WHERE import_id=?", [(i,) for i in ids])
    conn.execute("UPDATE global_report_imports SET status='SUPERSEDED', superseded_at=?, superseded_by_gimport_id=? WHERE gimport_id=?", (now or now_iso(), by, gimport_id))
    return affected


# ------------------------------------------------------------------ anteprima (nessuna scrittura)
def preview_file(conn, parsed: dict, window_label: str, books: dict, marketplace: str | None = None, date_to_override: date | None = None, slot: str | None = None) -> dict:
    """Anteprima di UN file: tipo, finestra, snapshot, righe, campagne (associate / da associare / ignorate), duplicati e conflitti. Non scrive nulla."""
    days = G.WINDOWS[window_label]
    d_from, d_to = G.snapshot_dates(parsed["rows"], days, date_to_override)
    mp = (marketplace or parsed.get("marketplace_hint") or "").upper() or None
    summ = G.campaign_summary(parsed["rows"])
    acct = _file_account(parsed)
    mapping = {(m["account_id"], m["amazon_campaign_id"]): m for m in _rows(conn, "SELECT * FROM campaign_book_mapping")} if has_schema(conn) else {}
    st = {"ASSIGNED": 0, "IGNORED": 0, "UNASSIGNED": 0, "NEW": 0}
    for cid in summ:
        m = mapping.get((acct_key(acct), cid))                           # il mapping vale SOLO dentro lo stesso account
        st[m["status"] if m else "NEW"] += 1
    problems, warns = [], list(parsed["warnings"])
    acc_problem = file_account_problem(parsed)
    if acc_problem:
        problems.append(acc_problem)
    if slot and SLOTS[slot][0] != parsed["report_type"]:
        problems.append(f"Il file è un report {G.REPORT_TYPES[parsed['report_type']]}, ma lo slot scelto è «{SLOT_LABELS[slot]}»")
    if not mp:
        problems.append("Marketplace non determinabile dalla valuta: scegli il marketplace")
    status = "NUOVO"
    revision = None
    if has_schema(conn):
        dup = _rows(conn, "SELECT gimport_id, file_name, imported_at, status FROM global_report_imports WHERE file_sha256=?", (parsed["sha256"],))
        if dup:
            status = "DUPLICATO"
            old = " (è una versione già sostituita da una più recente)" if dup[0]["status"] == "SUPERSEDED" else ""
            warns.append(f"File già importato il {dup[0]['imported_at'][:10]} («{dup[0]['file_name']}»){old}: nessuna modifica")
        elif d_to and mp:
            act = active_snapshot(conn, parsed["report_type"], window_label, d_from, d_to, mp, acct)
            if act:
                status = "REVISIONE"
                revision = _revision_info(conn, act, parsed)
                warns.append("Esiste già un report per questa finestra, ma il file contiene dati diversi. "
                             f"Versione attiva: «{act['file_name']}» importata il {act['imported_at'][:16].replace('T', ' ')} ({revision['old']['rows']} righe); "
                             f"nuovo file: {revision['new']['rows']} righe. Puoi usare la versione più recente oppure annullare: nulla viene sovrascritto senza la tua conferma.")
    if d_to and parsed.get("date_min") and parsed["date_min"] < d_from:
        warns.append(f"Alcune righe iniziano prima dell'inizio della finestra ({parsed['date_min']} < {d_from}): controlla di aver scelto la finestra giusta")
    if parsed["empty_search_term_rows"]:
        warns.append(f"{parsed['empty_search_term_rows']} righe con termine di ricerca vuoto: conservate ma escluse dalle logiche sui termini reali")
    others = [c for c in parsed["currencies"] if c]
    if len(others) > 1:
        warns.append("Più valute nel file: " + ", ".join(others))
    portfolios = {(s["portfolio_id"], s["portfolio_name"]) for s in summ.values() if s["portfolio_name"] or s["portfolio_id"]}
    return {"file_name": parsed["file_name"], "report_type": parsed["report_type"], "report_label": G.REPORT_TYPES[parsed["report_type"]], "window_label": window_label,
            "window_days": days, "date_from": d_from, "date_to": d_to, "marketplace": mp, "currency": parsed["currency"], "rows": parsed["row_count"], "campaigns": len(summ),
            "portfolios": len(portfolios), "assigned": st["ASSIGNED"], "unassigned_known": st["UNASSIGNED"], "ignored": st["IGNORED"], "new": st["NEW"],
            "account_id": acct, "account_name": parsed.get("account_name"), "account_ids": list(parsed.get("account_ids") or []),
            "status": status, "problems": problems, "warnings": warns, "empty_search_term_rows": parsed["empty_search_term_rows"], "sha256": parsed["sha256"],
            "importable": not problems and status == "NUOVO", "revision_available": status == "REVISIONE" and not problems, "revision": revision, "campaign_ids": sorted(summ)}


# ------------------------------------------------------------------ import (parcheggio + mappa + smistamento)
def stage_file(conn, parsed: dict, window_label: str, marketplace: str, books: dict, date_to_override: date | None = None, now: str | None = None,
               replace: bool = False) -> dict:
    """Parcheggia UN file (una transazione) e aggiorna la mappa campagne. Ritorna {gimport_id, status, new_campaigns, affected, ...}.
      DUPLICATE_FILE      stesso hash: nessuna modifica
      REVISION_AVAILABLE  stesso snapshot logico ma contenuto diverso e replace=False: NIENTE scritto, serve la conferma esplicita (replace=True)
      REPLACED            replace=True: la versione precedente diventa SUPERSEDED (audit), la nuova e' l'unica ATTIVA, i dati derivati vecchi sono rimossi (`affected` = libri da ricalcolare)
      IMPORTED            snapshot nuovo"""
    ensure_schema(conn)
    now = now or now_iso()
    mp = str(marketplace).upper()
    days = G.WINDOWS[window_label]
    d_from, d_to = G.snapshot_dates(parsed["rows"], days, date_to_override)
    if not d_to:
        raise GlobalImportError("Impossibile determinare la data finale dello snapshot: indicala a mano")
    dup = conn.execute("SELECT gimport_id FROM global_report_imports WHERE file_sha256=?", (parsed["sha256"],)).fetchone()
    if dup:
        return {"status": "DUPLICATE_FILE", "gimport_id": dup[0], "new_campaigns": 0, "rows": 0, "message": f"«{parsed['file_name']}» è già stato importato: nessuna modifica."}
    problem = file_account_problem(parsed)
    if problem:
        raise GlobalImportError(problem)
    acct = _file_account(parsed)
    acct_name = parsed.get("account_name")
    ak = acct_key(acct)
    act = active_snapshot(conn, parsed["report_type"], window_label, d_from, d_to, mp, acct)
    if act and not replace:
        return {"status": "REVISION_AVAILABLE", "gimport_id": None, "new_campaigns": 0, "rows": 0, "affected": set(), "revision": _revision_info(conn, act, parsed),
                "message": "Esiste già un report per questa finestra, ma il file contiene dati diversi: serve la tua conferma per usare la versione più recente."}
    summ = G.campaign_summary(parsed["rows"])
    pb = _portfolio_book(conn)
    pid = f"{d_from}__{d_to}"
    with conn:
        affected = set()
        revision_no = 1
        if act:                                                  # sostituzione CONFERMATA: una sola versione attiva
            affected = retire_import(conn, act["gimport_id"], now)
            revision_no = (act["revision"] or 1) + 1
        cur = conn.execute(
            "INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,"
            "campaign_count,empty_search_term_rows,warnings_json,parser_version,account_id,account_name,status,revision,supersedes_gimport_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)",
            (parsed["report_type"], window_label, days, d_from, d_to, pid, mp, parsed["currency"], parsed["file_name"], parsed["sha256"], now, parsed["row_count"], len(summ),
             parsed["empty_search_term_rows"], json.dumps(parsed["warnings"], ensure_ascii=False), parsed["parser_version"], acct, acct_name, revision_no,
             act["gimport_id"] if act else None))
        gid = cur.lastrowid
        if act:
            conn.execute("UPDATE global_report_imports SET superseded_by_gimport_id=? WHERE gimport_id=?", (gid, act["gimport_id"]))
        conn.executemany(
            "INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,account_id,account_name,campaign_name,portfolio_id,portfolio_name,ad_group_id,ad_group_name,target_id,targeting,"
            "match_type_raw,search_term,empty_search_term,row_date_from,row_date_to,bid,target_state,impressions,clicks,spend,orders,sales,units,materialized,raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(gid, r["campaign_id"], acct, acct_name, r["campaign_name"], r["portfolio_id"], r["portfolio_name"], r["ad_group_id"], r["ad_group_name"], r["target_id"], r["targeting"],
              r["match_type_raw"], r["search_term"], int(r["empty_search_term"]), r["row_from"].isoformat() if r["row_from"] else None,
              r["row_to"].isoformat() if r["row_to"] else None, r["bid"], r["target_state"], r["impressions"], r["clicks"], r["spend"], r["orders"], r["sales"], r["units"],
              2 if r["empty_search_term"] else 0, json.dumps(r["raw"], ensure_ascii=False)) for r in parsed["rows"]])
        new = 0
        for cid, s in summ.items():
            ex = conn.execute("SELECT status FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", (ak, cid)).fetchone()
            sug, conf, why = suggest_book(s, books, pb)
            if ex is None:
                new += 1
                conn.execute(
                    "INSERT INTO campaign_book_mapping (account_id,amazon_campaign_id,status,marketplace,campaign_type,suggested_book_id,suggestion_confidence,suggestion_reason,first_seen,"
                    "last_seen,account_name_last_seen,campaign_name_last_seen,portfolio_id_last_seen,portfolio_last_seen,ad_group_last_seen,detected_type,detected_kind,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ak, cid, "UNASSIGNED", mp, s["campaign_type"], sug, conf, why, s["first"] or d_from, s["last"] or d_to, acct_name, s["name"], s["portfolio_id"],
                     s["portfolio_name"], ", ".join(s["ad_groups"][:3]) or None, s["campaign_type"], s.get("campaign_kind"), now))
            else:
                # il NOME puo' cambiare: la chiave e' l'ID. last_seen e nomi si aggiornano; stato e associazione NON si toccano.
                conn.execute(
                    "UPDATE campaign_book_mapping SET last_seen=MAX(COALESCE(last_seen,''),?), first_seen=MIN(COALESCE(first_seen,?),?), campaign_name_last_seen=?, portfolio_id_last_seen=?, "
                    "portfolio_last_seen=?, ad_group_last_seen=?, detected_type=?, detected_kind=?, updated_at=?, account_name_last_seen=COALESCE(?, account_name_last_seen), "
                    "suggested_book_id=CASE WHEN status='UNASSIGNED' THEN ? ELSE suggested_book_id END, suggestion_confidence=CASE WHEN status='UNASSIGNED' THEN ? ELSE suggestion_confidence END, "
                    "suggestion_reason=CASE WHEN status='UNASSIGNED' THEN ? ELSE suggestion_reason END WHERE account_id=? AND amazon_campaign_id=?",
                    (s["last"] or d_to, s["first"] or d_from, s["first"] or d_from, s["name"], s["portfolio_id"], s["portfolio_name"], ", ".join(s["ad_groups"][:3]) or None,
                     s["campaign_type"], s.get("campaign_kind"), now, acct_name, sug, conf, why, ak, cid))
    return {"status": "REPLACED" if act else "IMPORTED", "gimport_id": gid, "account_id": acct, "account_name": acct_name, "new_campaigns": new, "rows": parsed["row_count"], "campaigns": len(summ), "period_id": pid,
            "affected": affected, "superseded": act["gimport_id"] if act else None, "revision": revision_no,
            "message": f"{parsed['file_name']} [account {acct_name or acct}]: {parsed['row_count']} righe, {len(summ)} campagne ({new} nuove)" + (" · versione precedente archiviata (SUPERSEDED)" if act else "")}


# ------------------------------------------------------------------ smistamento verso le tabelle del motore
def _book_context(settings, book_id, marketplace):
    from . import registry
    books = registry.load_books(settings["books_path"])
    full = books.get(book_id)
    if full is None:
        raise GlobalImportError(f"Libro {book_id} non presente in books.json")
    try:
        view = registry.book_view(full, marketplace)
    except registry.RegistryError as e:
        raise GlobalImportError(str(e)) from e
    errs = registry.validate_book(view)
    if errs:
        raise GlobalImportError(f"Libro {book_id} ({marketplace}) non valido: " + "; ".join(errs))
    eco, _w = registry.book_economics(view, settings.get("break_even_mismatch_tolerance_pp", 0.5))
    return full, view, eco


def materialize(conn, settings, amazon_campaign_id: str | None = None, now: str | None = None, account_id: str | None = None) -> dict:
    """Smista le righe parcheggiate delle campagne ASSIGNED (tutte, o una sola) in imports / target_rows / search_term_rows. Idempotente: le righe gia' smistate non si
    toccano; uno snapshot gia' presente per la stessa campagna interna e periodo (es. import legacy) NON viene sovrascritto (SNAPSHOT_CONFLICT, segnalato).
    Ritorna {imports, rows, books: {(book, mp)}, conflicts[], skipped_empty}."""
    from . import importer
    now = now or now_iso()
    ensure_schema(conn)
    out = {"imports": 0, "rows": 0, "books": set(), "conflicts": [], "skipped_empty": 0}
    where, params = "m.status='ASSIGNED'", []
    if amazon_campaign_id:
        where += " AND m.amazon_campaign_id=?"
        params.append(str(amazon_campaign_id))
    if account_id is not None:
        where += " AND m.account_id=?"
        params.append(acct_key(account_id))
    maps = _rows(conn, f"SELECT m.* FROM campaign_book_mapping m WHERE {where}", params)
    synced = set()
    for m in maps:
        cid, ak = m["amazon_campaign_id"], m["account_id"]
        # SOLO i report dello STESSO account: lo stesso ID campagna in un altro account e' un'altra campagna
        pending = _rows(conn, "SELECT DISTINCT g.* FROM global_report_imports g JOIN global_report_rows r ON r.gimport_id=g.gimport_id WHERE r.amazon_campaign_id=? AND r.materialized=0 "
                              "AND g.status='ACTIVE' AND COALESCE(g.account_id,'')=? ORDER BY g.date_to, g.window_days DESC, g.gimport_id", (cid, ak))
        if not pending:
            continue
        full, view, eco = _book_context(settings, m["book_id"], m["marketplace"])
        key = (m["book_id"], m["marketplace"])
        if key not in synced:
            importer._sync_book(conn, full, view, eco)
            synced.add(key)
        meta = {"book_id": m["book_id"], "campaign_id": m["campaign_id"], "campaign_name": m["campaign_name_last_seen"] or m["campaign_id"], "campaign_type": m["campaign_type"] or "PHRASE",
                "marketplace": m["marketplace"], "currency": view["currency"], "amazon_campaign_id": cid, "source": G.SOURCE}
        if conn.execute("SELECT 1 FROM campaigns WHERE book_id=? AND campaign_id=?", (m["book_id"], m["campaign_id"])).fetchone() is None:
            importer._sync_campaign(conn, meta)                  # una campagna storica gia' registrata (backfill) NON viene sovrascritta: nome, tipo e metadata restano quelli storici
        owner = conn.execute("SELECT COALESCE(amazon_account_id,'') FROM campaigns WHERE book_id=? AND campaign_id=?", (m["book_id"], m["campaign_id"])).fetchone()[0]
        if owner and owner != ak:                                # la campagna interna appartiene a un ALTRO account: mai mescolare
            out["conflicts"].append(f"ACCOUNT_CONFLICT {m['book_id']}|{m['campaign_id']}: la campagna interna appartiene all'account {owner}, non a {ak or 'sconosciuto'} (non smistata)")
            continue
        if ak and not owner:
            with conn:
                conn.execute("UPDATE campaigns SET amazon_account_id=? WHERE book_id=? AND campaign_id=?", (ak, m["book_id"], m["campaign_id"]))
        for g in pending:
            rows = _rows(conn, "SELECT * FROM global_report_rows WHERE gimport_id=? AND amazon_campaign_id=? AND COALESCE(account_id,'')=? AND materialized=0 ORDER BY id",
                         (g["gimport_id"], cid, ak))
            if not rows:
                continue
            snap = f"{m['book_id']}|{m['campaign_id']}|{g['report_type']}|{g['period_id']}"
            sha = f"{g['file_sha256']}:{cid}"
            clash = conn.execute("SELECT import_id, file_sha256, source_filename FROM imports WHERE snapshot_id=?", (snap,)).fetchone()
            if clash and clash["file_sha256"] != sha:
                out["conflicts"].append(f"SNAPSHOT_CONFLICT {snap}: già importato da «{clash['source_filename']}» (non sovrascritto)")
                continue
            usable = [r for r in rows if not (g["report_type"] == "SEARCH_TERM" and r["empty_search_term"])]
            with conn:
                if clash:
                    iid = clash["import_id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO imports (book_id,campaign_id,campaign_name,campaign_type,marketplace,currency,report_type,report_scope,date_from,date_to,import_timestamp,source_filename,"
                        "period_id,snapshot_id,file_path,file_sha256,attribution_window,attribution_source,row_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (m["book_id"], m["campaign_id"], meta["campaign_name"], meta["campaign_type"], m["marketplace"], view["currency"], g["report_type"], "DATE_RANGE", g["date_from"],
                         g["date_to"], now, g["file_name"], g["period_id"], snap, f"GLOBAL:{g['file_name']}", sha, None, "unknown", len(usable)))
                    iid = cur.lastrowid
                    is_st = g["report_type"] == "SEARCH_TERM"
                    table = "search_term_rows" if is_st else "target_rows"
                    cols = ["import_id", "book_id", "campaign_id", "campaign_name", "campaign_type", "marketplace", "currency", "report_type", "report_scope", "date_from", "date_to",
                            "import_timestamp", "source_filename", "period_id", "snapshot_id", "csv_campaign_name", "ad_group", "target", "match_type", "added_as", "bid"] + \
                           (["search_term"] if is_st else []) + ["impressions", "clicks", "spend", "sales", "orders", "units", "raw_json", "amazon_campaign_id", "amazon_ad_group_id",
                                                                 "amazon_target_id", "row_date_from", "row_date_to", "amazon_account_id"]
                    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                    for r in usable:
                        vals = [iid, m["book_id"], m["campaign_id"], meta["campaign_name"], meta["campaign_type"], m["marketplace"], view["currency"], g["report_type"], "DATE_RANGE",
                                g["date_from"], g["date_to"], now, g["file_name"], g["period_id"], snap, r["campaign_name"], r["ad_group_name"], r["targeting"],
                                G.MATCH_IT.get((r["match_type_raw"] or "").upper(), r["match_type_raw"]), None, None if is_st else r["bid"]]
                        if is_st:
                            vals.append(r["search_term"])
                        vals += [r["impressions"], r["clicks"], r["spend"], r["sales"], r["orders"], r["units"], r["raw_json"], cid, r["ad_group_id"], r["target_id"],
                                 r["row_date_from"], r["row_date_to"], ak or None]
                        conn.execute(sql, vals)
                    out["imports"] += 1
                    out["rows"] += len(usable)
                conn.execute("UPDATE global_report_rows SET materialized=1 WHERE gimport_id=? AND amazon_campaign_id=? AND COALESCE(account_id,'')=? AND materialized=0 AND empty_search_term=0",
                             (g["gimport_id"], cid, ak))
                out["skipped_empty"] += conn.execute("SELECT COUNT(*) FROM global_report_rows WHERE gimport_id=? AND amazon_campaign_id=? AND COALESCE(account_id,'')=? AND materialized=2",
                                                     (g["gimport_id"], cid, ak)).fetchone()[0]
            out["books"].add(key)
    return out


# ------------------------------------------------------------------ associazioni
def _slug(name: str) -> str:
    s = re.sub(r"\s*-?\s*\d{1,2}/\d{1,2}/\d{4}.*$", "", str(name or "")).strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()[:40]
    return s or "CAMPAGNA"


def internal_options(conn, book_id: str, marketplace: str, amazon_campaign_id: str) -> list:
    """Campagne INTERNE storiche del libro non ancora legate a un ID Amazon: [{campaign_id, campaign_name, campaign_type}]. Servono SOLO al backfill verificato:
    la UI normale non le offre mai come destinazione (nessuna fusione "per somiglianza")."""
    used = {r["campaign_id"] for r in _rows(conn, "SELECT campaign_id FROM campaign_book_mapping WHERE status='ASSIGNED' AND book_id=? AND amazon_campaign_id<>?", (book_id, amazon_campaign_id))}
    return [r for r in _rows(conn, "SELECT campaign_id, campaign_name, campaign_type FROM campaigns WHERE book_id=? AND UPPER(marketplace)=? ORDER BY campaign_id",
                             (book_id, str(marketplace).upper())) if r["campaign_id"] not in used]


# ------------------------------------------------------------------ BACKFILL: collegare una campagna storica al suo vero Campaign ID (solo con prova forte + conferma)
BACKFILL_RULES = {"min_shared": 3, "min_cont_amazon": 0.9, "min_bid_equal": 0.8, "min_bids_compared": 3, "rival_cont": 0.5}
_AUTO_GROUPS = frozenset({"close-match", "loose-match", "complements", "substitutes"})


def _norm_target(t) -> str:
    return re.sub(r"\s+", " ", str(t or "").strip().strip('"[]').strip().lower())


def _class_legacy(target, match_type) -> str:
    t = _norm_target(target)
    mt = str(match_type or "").lower()
    if t in _AUTO_GROUPS:
        return "AUTO"
    if t.startswith(("asin=", "asin-expanded=")):
        return "PROD"
    return "EXACT" if ("esatt" in mt or "exact" in mt) else "PHRASE"


def _class_amazon(match_raw) -> str:
    m = str(match_raw or "").upper()
    return "AUTO" if "PREDEFINED" in m else "PROD" if m == "TARGETING_EXPRESSION" else "EXACT" if m == "EXACT" else "PHRASE"


def _legacy_campaigns(conn) -> dict:
    """{(book_id, campaign_id): {type, mp, name, targets:{(testo, classe): bid piu' recente}}} delle campagne STORICHE (righe senza ID Amazon) ancora non legate."""
    mapped = {(r["book_id"], r["campaign_id"]) for r in _rows(conn, "SELECT book_id, campaign_id FROM campaign_book_mapping WHERE status='ASSIGNED'")}
    out = {}
    for r in _rows(conn, "SELECT t.book_id, t.campaign_id, t.target, t.match_type, t.bid, t.date_to, c.campaign_type, c.campaign_name, c.marketplace, "
                         "COALESCE(c.amazon_account_id, '') AS account_id FROM target_rows t "
                         "JOIN campaigns c ON c.book_id=t.book_id AND c.campaign_id=t.campaign_id WHERE t.amazon_campaign_id IS NULL ORDER BY COALESCE(t.date_to,'')"):
        k = (r["book_id"], r["campaign_id"])
        if k in mapped:
            continue
        L = out.setdefault(k, {"type": r["campaign_type"], "mp": (r["marketplace"] or "").upper(), "name": r["campaign_name"], "targets": {}, "account_id": r["account_id"]})
        L["targets"][(_norm_target(r["target"]), _class_legacy(r["target"], r["match_type"]))] = r["bid"]
    return out


def _amazon_targets(conn, amazon_campaign_id: str, account_id: str = "") -> dict:
    """Target ATTIVI della campagna Amazon (dello STESSO account) dai report parcheggiati: {(testo, classe): bid piu' recente dal Targeting}."""
    out = {}
    for r in _rows(conn, "SELECT r.targeting, r.match_type_raw, r.bid, g.report_type, g.date_to FROM global_report_rows r JOIN global_report_imports g ON g.gimport_id=r.gimport_id "
                         "WHERE r.amazon_campaign_id=? AND COALESCE(g.account_id,'')=? AND g.status='ACTIVE' ORDER BY g.date_to", (amazon_campaign_id, acct_key(account_id))):
        if not r["targeting"]:
            continue
        k = (_norm_target(r["targeting"]), _class_amazon(r["match_type_raw"]))
        if r["report_type"] == "TARGETING" and r["bid"] is not None:
            out[k] = r["bid"]
        else:
            out.setdefault(k, None)
    return out


def backfill_candidates(conn) -> list:
    """Per ogni campagna Amazon NON associata, la migliore campagna STORICA del DB con prova sui TARGET, MAI sul solo tipo/nome.
    Livello ALTA solo se TUTTO vale: >= 3 target distintivi (non i 4 gruppi Auto) in comune; >= 90% dei target attivi su Amazon esistono nella campagna storica; offerte uguali
    su >= 80% dei target confrontabili (almeno 3); stesso libro del suggerimento da portfolio; stesso tipo campagna e marketplace; nessun rivale (nessun'altra campagna storica
    con >= 50% di sovrapposizione, e nessun'altra campagna Amazon che si sovrappone alla stessa storica). Le campagne AUTO non sono dimostrabili (i 4 gruppi sono uguali ovunque).
    MULTI-ACCOUNT: una campagna dell'account B non e' MAI candidata di una campagna storica dell'account A. Una campagna storica senza account (legacy_account_unknown) puo' essere
    candidata solo con prova forte e va confermata esplicitamente (`needs_account_confirmation`): l'account NON si deduce mai dal nome, dal tipo o dai target.
    Ritorna solo SUGGERIMENTI: collegare richiede conferma esplicita (associate(..., backfill_of=...))."""
    if not has_schema(conn):
        return []
    R = BACKFILL_RULES
    legacy = _legacy_campaigns(conn)
    camps = _rows(conn, "SELECT * FROM campaign_book_mapping WHERE status='UNASSIGNED'")
    mkey = lambda m: (m["account_id"], m["amazon_campaign_id"])         # noqa: E731
    amz = {mkey(m): {k: v for k, v in _amazon_targets(conn, m["amazon_campaign_id"], m["account_id"]).items() if k[1] != "AUTO"} for m in camps}
    overlap = {}                                                    # ((account, campagna), L) -> (shared, cont_a, cmp, eq)
    for aid, TA in amz.items():
        if not TA:
            continue
        for lk, L in legacy.items():
            if L["account_id"] and L["account_id"] != aid[0]:             # account diverso: MAI cross-account
                continue
            TL = {k: v for k, v in L["targets"].items() if k[1] != "AUTO"}
            sh = set(TA) & set(TL)
            if not sh:
                continue
            cmpd = [(TA[k], TL[k]) for k in sh if TA[k] is not None and TL[k] is not None]
            overlap[(aid, lk)] = (len(sh), len(sh) / len(TA), len(cmpd), sum(1 for a, b in cmpd if abs(a - b) < 0.005))
    out = []
    for m in camps:
        aid = mkey(m)
        mine = sorted(((v, lk) for (a, lk), v in overlap.items() if a == aid), key=lambda x: (-x[0][1], -x[0][0]))
        if not mine:
            continue
        (shared, cont, ncmp, neq), lk = mine[0]
        L = legacy[lk]
        rivals_l = [x for x in mine[1:] if x[0][1] >= R["rival_cont"]]
        rivals_a = [a for (a, k2), v in overlap.items() if k2 == lk and a != aid and v[1] >= R["rival_cont"]]
        reasons = []
        if m["detected_type"] == "OTHER" and m.get("detected_kind") != "AMBIGUOUS":
            reasons.append("campagna AUTO: i 4 gruppi sono identici in ogni campagna, non dimostrabile")
        if shared < R["min_shared"]:
            reasons.append(f"solo {shared} target distintivi in comune (servono {R['min_shared']})")
        if cont < R["min_cont_amazon"]:
            reasons.append(f"solo il {cont:.0%} dei target attivi su Amazon esiste nella campagna storica (serve {R['min_cont_amazon']:.0%})")
        if ncmp < R["min_bids_compared"] or neq / max(ncmp, 1) < R["min_bid_equal"]:
            reasons.append(f"offerte uguali su {neq}/{ncmp} target confrontabili (servono almeno {R['min_bids_compared']} e {R['min_bid_equal']:.0%})")
        if m["suggested_book_id"] != lk[0]:
            reasons.append("il libro suggerito dal portfolio non coincide con quello della campagna storica")
        if (m["detected_type"] or "") != (L["type"] or ""):
            reasons.append(f"tipo diverso ({m['detected_type']} vs {L['type']})")
        if (m["marketplace"] or "").upper() != L["mp"]:
            reasons.append("marketplace diverso")
        if rivals_l:
            reasons.append("altre campagne storiche con sovrapposizione rilevante: ambiguo")
        if rivals_a:
            reasons.append("un'altra campagna Amazon si sovrappone alla stessa campagna storica: ambiguo")
        out.append({"amazon_campaign_id": aid[1], "account_id": aid[0], "account_name": m["account_name_last_seen"], "legacy_account_id": L["account_id"] or None,
                    "needs_account_confirmation": not L["account_id"], "amazon_name": m["campaign_name_last_seen"], "portfolio": m["portfolio_last_seen"], "book_id": lk[0], "legacy_campaign_id": lk[1],
                    "legacy_name": L["name"], "legacy_type": L["type"], "amazon_type": m["detected_type"], "shared": shared, "amazon_targets": len(amz[aid]),
                    "cont_amazon": round(cont, 3), "bids_compared": ncmp, "bids_equal": neq, "level": "ALTA" if not reasons else "DA_VERIFICARE", "reasons": reasons})
    return out


def resolve_mapping(conn, amazon_campaign_id, account_id=None):
    """Riga di mapping di (account, ID campagna). Senza `account_id` l'ID deve essere NON ambiguo (presente in un solo account): altrimenti errore esplicito, mai una scelta
    a caso fra due account."""
    cid = str(amazon_campaign_id)
    if account_id is not None:
        return conn.execute("SELECT * FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", (acct_key(account_id), cid)).fetchone()
    rows = conn.execute("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id=?", (cid,)).fetchall()
    if len(rows) > 1:
        raise GlobalImportError(f"L'ID campagna {cid} esiste in più account Amazon Ads ({', '.join(sorted(r['account_id'] or 'sconosciuto' for r in rows))}): indica l'account")
    return rows[0] if rows else None


def associate(conn, settings, amazon_campaign_id: str, book_id: str, marketplace: str | None = None, internal_campaign_id: str | None = None,
              campaign_type: str | None = None, now: str | None = None, backfill_of: str | None = None, account_id: str | None = None,
              confirm_legacy_account: bool = False) -> dict:
    """Associazione CONFERMATA dall'utente: ID campagna Amazon -> libro. REGOLE:
      - l'identita' e' l'ID Amazon: una campagna Amazon NUOVA diventa SEMPRE una campagna interna NUOVA (mai fusa con una storica "simile": stesso libro/tipo/nome non bastano);
        l'id interno e' sempre inedito (collisioni con campagne storiche o gia' associate => suffisso _2, _3...);
      - l'unico modo di collegare una campagna storica e' backfill_of=<id storico>, accettato SOLO se backfill_candidates lo dimostra (livello ALTA) per quella coppia;
      - una campagna gia' ASSIGNED non si riassegna in silenzio; IGNORED -> si puo' associare (rimuove l'ignore).
    Poi smista i dati gia' parcheggiati (nessun reimport)."""
    ensure_schema(conn)
    now = now or now_iso()
    cid = str(amazon_campaign_id)
    m = resolve_mapping(conn, cid, account_id)
    if m is None:
        raise GlobalImportError(f"Campagna {cid} sconosciuta" + (f" per l'account {account_id}" if account_id else "") + ": importa prima un report che la contiene")
    ak = m["account_id"]
    if m["status"] == "ASSIGNED":
        raise GlobalImportError(f"La campagna {cid} è già associata al libro {m['book_id']}: non la riassegno in silenzio")
    mp = str(marketplace or m["marketplace"] or "").upper()
    _full, view, _eco = _book_context(settings, str(book_id), mp)
    if backfill_of:
        ok = [c for c in backfill_candidates(conn) if c["amazon_campaign_id"] == cid and c["account_id"] == ak and c["legacy_campaign_id"] == backfill_of
              and c["book_id"] == str(book_id) and c["level"] == "ALTA"]
        if not ok:
            raise GlobalImportError(f"Corrispondenza con la campagna storica «{backfill_of}» NON dimostrata (o di un altro account): la campagna Amazon {cid} resta una campagna distinta")
        if ok[0]["needs_account_confirmation"] and not confirm_legacy_account:
            raise GlobalImportError(f"La campagna storica «{backfill_of}» non ha un account registrato (legacy_account_unknown): per collegarla all'account {ak or 'n/d'} serve la tua "
                                    "conferma esplicita che appartiene a quell'account")
        internal = backfill_of
        ctype = next((o["campaign_type"] for o in internal_options(conn, str(book_id), mp, cid) if o["campaign_id"] == backfill_of), None) or m["detected_type"] or "PHRASE"
    else:
        base = _slug(internal_campaign_id or m["campaign_name_last_seen"])
        taken = {r[0] for r in conn.execute("SELECT campaign_id FROM campaigns WHERE book_id=?", (str(book_id),))} | \
                {r[0] for r in conn.execute("SELECT campaign_id FROM campaign_book_mapping WHERE book_id=? AND campaign_id IS NOT NULL", (str(book_id),))}
        internal, n = base, 2
        while internal in taken:
            internal, n = f"{base}_{n}", n + 1
        ctype = campaign_type or m["campaign_type"] or m["detected_type"] or "PHRASE"
    with conn:
        conn.execute("UPDATE campaign_book_mapping SET status='ASSIGNED', book_id=?, marketplace=?, campaign_id=?, campaign_type=?, assigned_at=?, updated_at=?, backfill_of=?, "
                     "legacy_account_confirmed=? WHERE account_id=? AND amazon_campaign_id=?",
                     (str(book_id), mp, internal, ctype, now, now, backfill_of or None, int(bool(backfill_of and confirm_legacy_account)), ak, cid))
    res = materialize(conn, settings, cid, now, account_id=ak)
    res["campaign_id"], res["book_id"], res["marketplace"], res["backfill"], res["account_id"] = internal, str(book_id), mp, bool(backfill_of), ak
    return res


def ignore(conn, amazon_campaign_id: str, now: str | None = None, account_id: str | None = None) -> None:
    """IGNORA (per ACCOUNT): nessun dato cancellato; la campagna non compare piu' fra quelle da associare. Ignorare (account A, ID 123) non tocca (account B, ID 123)."""
    m = resolve_mapping(conn, amazon_campaign_id, account_id)
    if m is None:
        raise GlobalImportError("Campagna sconosciuta")
    if m["status"] == "ASSIGNED":
        raise GlobalImportError("La campagna è già associata a un libro: non può essere ignorata")
    with conn:
        conn.execute("UPDATE campaign_book_mapping SET status='IGNORED', updated_at=? WHERE account_id=? AND amazon_campaign_id=?", (now or now_iso(), m["account_id"], str(amazon_campaign_id)))


def unignore(conn, amazon_campaign_id: str, now: str | None = None, account_id: str | None = None) -> None:
    m = resolve_mapping(conn, amazon_campaign_id, account_id)
    with conn:
        n = conn.execute("UPDATE campaign_book_mapping SET status='UNASSIGNED', updated_at=? WHERE account_id=? AND amazon_campaign_id=? AND status='IGNORED'",
                         (now or now_iso(), m["account_id"] if m else acct_key(account_id), str(amazon_campaign_id))).rowcount
    if not n:
        raise GlobalImportError("La campagna non è tra quelle ignorate")


# ------------------------------------------------------------------ letture per la dashboard
def counts(conn) -> dict:
    if not has_schema(conn):
        return {"UNASSIGNED": 0, "ASSIGNED": 0, "IGNORED": 0}
    c = {"UNASSIGNED": 0, "ASSIGNED": 0, "IGNORED": 0}
    for r in _rows(conn, "SELECT status, COUNT(*) n FROM campaign_book_mapping GROUP BY status"):
        c[r["status"]] = r["n"]
    return c


def campaign_list(conn, status: str) -> list:
    """Campagne per stato con statistiche dai report parcheggiati (ultima 30D se c'e', altrimenti tutto) e report in cui compaiono."""
    if not has_schema(conn):
        return []
    out = _rows(conn, "SELECT * FROM campaign_book_mapping WHERE status=? ORDER BY account_id, COALESCE(portfolio_last_seen,''), campaign_name_last_seen", (status,))
    for m in out:
        reps = _rows(conn, "SELECT g.report_type, g.window_label, g.date_to, COUNT(*) rows_, SUM(r.impressions) impressions, SUM(r.clicks) clicks, SUM(r.spend) spend, SUM(r.orders) orders, "
                           "MIN(r.row_date_from) first_, MAX(r.row_date_to) last_ FROM global_report_rows r JOIN global_report_imports g ON g.gimport_id=r.gimport_id "
                           "WHERE r.amazon_campaign_id=? AND COALESCE(g.account_id,'')=? GROUP BY g.gimport_id ORDER BY g.date_to DESC, g.window_days DESC",
                           (m["amazon_campaign_id"], m["account_id"]))
        m["reports"] = reps
        m["report_labels"] = sorted({f"{G.REPORT_TYPES[r['report_type']]} {r['window_label'].replace('RECENT_', '')}" for r in reps})
        best = next((r for r in reps if r["report_type"] == "TARGETING" and r["window_label"] == "RECENT_30D"), reps[0] if reps else None)
        m["stats"] = best
    return out


def book_campaigns(conn, book_id: str) -> list:
    if not has_schema(conn):
        return []
    return _rows(conn, "SELECT account_id, account_name_last_seen, amazon_campaign_id, campaign_id, campaign_name_last_seen, portfolio_last_seen, campaign_type, marketplace, status, "
                       "assigned_at FROM campaign_book_mapping WHERE book_id=? ORDER BY account_id, campaign_id", (str(book_id),))


def book_campaign_counts(conn) -> dict:
    if not has_schema(conn):
        return {}
    return {r["book_id"]: r["n"] for r in _rows(conn, "SELECT book_id, COUNT(*) n FROM campaign_book_mapping WHERE status='ASSIGNED' GROUP BY book_id")}


def list_imports(conn) -> list:
    if not has_schema(conn):
        return []
    return _rows(conn, "SELECT gimport_id, report_type, window_label, date_from, date_to, marketplace, file_name, imported_at, row_count, campaign_count, empty_search_term_rows, "
                       "account_id, account_name, status, revision FROM global_report_imports ORDER BY gimport_id DESC")


def accounts(conn) -> list:
    """Account Amazon Ads noti (da mapping e da report): [{account_id, account_name, campaigns, assigned, unassigned, ignored, imports}]."""
    if not has_schema(conn):
        return []
    out = {}
    for r in _rows(conn, "SELECT account_id, MAX(account_name_last_seen) AS name, COUNT(*) n, SUM(status='ASSIGNED') a, SUM(status='UNASSIGNED') u, SUM(status='IGNORED') i "
                         "FROM campaign_book_mapping GROUP BY account_id"):
        out[r["account_id"]] = {"account_id": r["account_id"], "account_name": r["name"], "campaigns": r["n"], "assigned": r["a"] or 0, "unassigned": r["u"] or 0,
                                "ignored": r["i"] or 0, "imports": 0}
    for r in _rows(conn, "SELECT COALESCE(account_id,'') AS account_id, MAX(account_name) AS name, COUNT(*) n FROM global_report_imports GROUP BY 1"):
        o = out.setdefault(r["account_id"], {"account_id": r["account_id"], "account_name": r["name"], "campaigns": 0, "assigned": 0, "unassigned": 0, "ignored": 0, "imports": 0})
        o["imports"] = r["n"]
        o["account_name"] = o["account_name"] or r["name"]
    return sorted(out.values(), key=lambda x: (x["account_name"] or "", x["account_id"]))


def account_names(conn) -> dict:
    return {a["account_id"]: a["account_name"] for a in accounts(conn) if a["account_id"]}
