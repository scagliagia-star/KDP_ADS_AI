import sqlite3
from pathlib import Path

from .decfields import DEC_FIELDS, STATUSES
from .recfields import REC_FIELDS

SCHEMA_VERSION = 3

# Colonne di contesto presenti in imports e in ogni riga dati (denormalizzate per filtrare senza JOIN).
_CTX = """
    book_id TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT, campaign_type TEXT,
    marketplace TEXT, currency TEXT, report_type TEXT NOT NULL, report_scope TEXT,
    date_from TEXT, date_to TEXT, import_timestamp TEXT NOT NULL, source_filename TEXT NOT NULL,
    period_id TEXT, snapshot_id TEXT,
"""
_METRICS = """
    impressions REAL, clicks REAL, spend REAL, sales REAL, orders REAL, units REAL,
    acos_reported_pct REAL, ctr_reported_pct REAL, cpc_reported REAL,
    roas_reported REAL, conv_rate_reported_pct REAL,
    raw_json TEXT
"""

_DEC_DDL = ", ".join(
    f"{n} {ty}" + (" NOT NULL DEFAULT 'PROPOSED' CHECK (status IN (" + ",".join(f"'{s}'" for s in STATUSES) + "))" if n == "status" else "")
    + (" NOT NULL DEFAULT 1 CHECK (manual_approval_required = 1)" if n == "manual_approval_required" else "")
    for n, ty in DEC_FIELDS)
_REC_DDL = ", ".join(f"{n} {ty}" for n, ty in REC_FIELDS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS amazon_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL,
    book_id TEXT NOT NULL, campaign_id TEXT NOT NULL, period_from TEXT NOT NULL, period_to TEXT NOT NULL,
    source TEXT NOT NULL, recommendation_type TEXT NOT NULL,
    target TEXT, target_normalized TEXT NOT NULL DEFAULT '',
    observed_spend REAL, observed_orders REAL, observation TEXT,
    suggested_action TEXT, suggested_direction TEXT NOT NULL DEFAULT 'UNSPECIFIED', suggested_pct REAL, notes TEXT,
    UNIQUE (book_id, campaign_id, period_from, period_to, recommendation_type, target_normalized)
);
CREATE INDEX IF NOT EXISTS ix_amazon_book ON amazon_recommendations(book_id, campaign_id);

CREATE TABLE IF NOT EXISTS decision_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, generated_at TEXT NOT NULL,
    analysis_run_id INTEGER, csv_path TEXT, md_path TEXT, decision_count INTEGER,
    accounting_json TEXT, warnings_json TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    {_DEC_DDL}
);
CREATE INDEX IF NOT EXISTS ix_dec_book ON decisions(book_id, run_id);
CREATE INDEX IF NOT EXISTS ix_dec_key ON decisions(action_key);
CREATE INDEX IF NOT EXISTS ix_dec_status ON decisions(status);
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, generated_at TEXT NOT NULL,
    csv_path TEXT, md_path TEXT, overlaps_csv_path TEXT, row_count INTEGER, warnings_json TEXT, rules_json TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    rec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_REC_DDL}
);
CREATE INDEX IF NOT EXISTS ix_rec_book ON recommendations(book_id, run_id);
CREATE INDEX IF NOT EXISTS ix_rec_campaign ON recommendations(campaign_id);
CREATE INDEX IF NOT EXISTS ix_rec_action ON recommendations(recommended_action);
CREATE TABLE IF NOT EXISTS books (
    book_id TEXT PRIMARY KEY, title TEXT, marketplace TEXT, currency TEXT,
    price REAL, royalty_per_copy REAL,
    break_even_acos_configured REAL, break_even_acos_calculated REAL, break_even_acos REAL,
    target_acos REAL, phase TEXT, active INTEGER, synced_at TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    book_id TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT, campaign_type TEXT,
    marketplace TEXT, currency TEXT, metadata_json TEXT, synced_at TEXT,
    PRIMARY KEY (book_id, campaign_id)
);

CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_CTX}
    file_path TEXT NOT NULL, file_sha256 TEXT NOT NULL,
    attribution_window TEXT, attribution_source TEXT,
    row_count INTEGER,
    UNIQUE (book_id, campaign_id, file_sha256)
);

CREATE TABLE IF NOT EXISTS target_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES imports(import_id),
    {_CTX}
    csv_campaign_name TEXT, ad_group TEXT, target TEXT, match_type TEXT, added_as TEXT, bid REAL,
    {_METRICS}
);

CREATE TABLE IF NOT EXISTS search_term_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES imports(import_id),
    {_CTX}
    csv_campaign_name TEXT, ad_group TEXT, target TEXT, match_type TEXT, added_as TEXT, bid REAL, search_term TEXT,
    {_METRICS}
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_imports_snapshot ON imports(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_imports_period ON imports(book_id, campaign_id, report_type, period_id);
CREATE INDEX IF NOT EXISTS ix_target_period ON target_rows(book_id, campaign_id, period_id);
CREATE INDEX IF NOT EXISTS ix_st_period ON search_term_rows(book_id, campaign_id, period_id);
CREATE INDEX IF NOT EXISTS ix_imports_book ON imports(book_id);
CREATE INDEX IF NOT EXISTS ix_imports_campaign ON imports(book_id, campaign_id);
CREATE INDEX IF NOT EXISTS ix_imports_report ON imports(report_type);
CREATE INDEX IF NOT EXISTS ix_target_book ON target_rows(book_id);
CREATE INDEX IF NOT EXISTS ix_target_campaign ON target_rows(book_id, campaign_id);
CREATE INDEX IF NOT EXISTS ix_target_type ON target_rows(campaign_type);
CREATE INDEX IF NOT EXISTS ix_target_report ON target_rows(report_type);
CREATE INDEX IF NOT EXISTS ix_st_book ON search_term_rows(book_id);
CREATE INDEX IF NOT EXISTS ix_st_campaign ON search_term_rows(book_id, campaign_id);
CREATE INDEX IF NOT EXISTS ix_st_type ON search_term_rows(campaign_type);
CREATE INDEX IF NOT EXISTS ix_st_report ON search_term_rows(report_type);
CREATE INDEX IF NOT EXISTS ix_st_term ON search_term_rows(search_term);
"""

_OLD_TABLES = ("target_rows", "search_term_rows", "imports")


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _upgrade(conn)
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "recommendations", REC_FIELDS)     # colonne aggiunte dopo (nullable, righe storiche = NULL)
    _ensure_columns(conn, "decisions", DEC_FIELDS)
    return conn


def _ensure_columns(conn, table, fields):
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, typ in fields:
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.commit()


_PERIOD_SQL = ("CASE WHEN report_scope='LIFETIME' THEN 'LIFETIME__' || COALESCE(date_to,'NA') "
               "WHEN report_scope='DATE_RANGE' AND date_from IS NOT NULL AND date_to IS NOT NULL THEN date_from || '__' || date_to "
               "ELSE 'UNKNOWN__NA' END")


def _upgrade(conn):
    """v1 (mono-libro) -> v2 (multi-book): ricrea le tabelle SOLO se vuote.
    v2 -> v3 (period snapshot): aggiunge period_id/snapshot_id e li ricalcola dai dati esistenti, SENZA perdere dati."""
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver >= SCHEMA_VERSION:
        return
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if ver < 2:
        for t in _OLD_TABLES:
            if t in existing and conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:
                raise RuntimeError(f"DB con schema v{ver} contiene dati in '{t}': migrazione manuale necessaria, "
                                   "non ricreo le tabelle.")
        for t in _OLD_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    else:
        for t in ("imports", "target_rows", "search_term_rows"):
            if t in existing:
                _ensure_columns(conn, t, [("period_id", "TEXT"), ("snapshot_id", "TEXT")])
        if "imports" in existing:
            conn.execute(f"UPDATE imports SET period_id = {_PERIOD_SQL} WHERE period_id IS NULL")
            conn.execute("UPDATE imports SET snapshot_id = book_id || '|' || campaign_id || '|' || report_type || '|' || period_id "
                         "WHERE snapshot_id IS NULL")
            for t in ("target_rows", "search_term_rows"):
                if t in existing:
                    conn.execute(f"UPDATE {t} SET period_id = (SELECT i.period_id FROM imports i WHERE i.import_id = {t}.import_id), "
                                 f"snapshot_id = (SELECT i.snapshot_id FROM imports i WHERE i.import_id = {t}.import_id) "
                                 f"WHERE period_id IS NULL")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
