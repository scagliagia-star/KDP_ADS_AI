import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .decfields import DEC_FIELDS, STATUSES
from .recfields import REC_FIELDS
from . import competitor_store, globalstore, manual_actions

SCHEMA_VERSION = 9

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
    accounting_json TEXT, warnings_json TEXT, marketplace TEXT
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
    csv_path TEXT, md_path TEXT, overlaps_csv_path TEXT, row_count INTEGER, warnings_json TEXT, rules_json TEXT,
    marketplace TEXT
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

-- economia di ogni marketplace del libro (valute e prezzi NON si convertono mai fra marketplace)
CREATE TABLE IF NOT EXISTS book_marketplaces (
    book_id TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT, price REAL, royalty_per_copy REAL,
    break_even_acos_configured REAL, break_even_acos_calculated REAL, break_even_acos REAL,
    target_acos REAL, phase TEXT, active INTEGER, synced_at TEXT,
    PRIMARY KEY (book_id, marketplace)
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
    UNIQUE (book_id, campaign_id, report_type, period_id, file_sha256)
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
    manual_actions.ensure_schema(conn)                                      # v8: registro delle azioni manuali (tabella NUOVA, nessuna esistente toccata)
    globalstore.ensure_schema(conn)                                         # Report globali Amazon Ads: tabelle NUOVE + colonne di tracciabilita' (additive)
    competitor_store.ensure_schema(conn)                                    # Ricerca competitor (Cerebro): tabelle NUOVE e additive, nessuna esistente toccata
    _ensure_columns(conn, "recommendations", REC_FIELDS)     # colonne aggiunte dopo (nullable, righe storiche = NULL)
    _ensure_columns(conn, "decisions", DEC_FIELDS)
    _ensure_columns(conn, "imports", [("warnings_json", "TEXT")])          # avvisi strutturati dell'import (es. SAME_CONTENT_DIFFERENT_PERIOD)
    _ensure_columns(conn, "analysis_runs", [("marketplace", "TEXT")])        # run storici: NULL = marketplace legacy del libro
    _ensure_columns(conn, "decision_runs", [("marketplace", "TEXT")])
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


def backup_before_migration(conn, from_version: int) -> Path | None:
    """Backup versionato del DB PRIMA di una migrazione di schema. Vive nel layer DB: vale per qualunque entrypoint che apra il DB
    (dashboard, import_csv.py, analyze.py, decide.py, ...). Mai sovrascrive: kdp_ads_pre_schema_v<N>_<data_ora>[_k].db in
    <cartella del DB>/backups/. Nessun backup per DB in memoria o senza tabelle (DB nuovo). Usa l'API di backup di sqlite (copia
    consistente)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    src = Path(row[2]) if row and row[2] else None
    if src is None or not src.is_file() or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
        return None
    dest_dir = src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dest, n = dest_dir / f"{base}.db", 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{base}_{n}.db"
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
    finally:
        out.close()
    logging.getLogger("kdp_ads.db").warning("Migrazione schema v%s -> v%s: backup creato in %s", from_version, SCHEMA_VERSION, dest)
    return dest


_OLD_IMPORTS_UNIQUE = "UNIQUE (book_id, campaign_id, file_sha256)"
_NEW_IMPORTS_UNIQUE = "UNIQUE (book_id, campaign_id, report_type, period_id, file_sha256)"


def _rebuild_imports_unique(conn):
    """v4 -> v5: la deduplica non e' piu' (libro, campagna, hash) ma (libro, campagna, tipo report, periodo, hash): lo stesso file puo'
    essere uno snapshot valido per una finestra diversa. SQLite non permette di togliere un UNIQUE: si ricopia la tabella imports
    (stesse colonne, stessi import_id, nessuna riga persa) e si ricreano gli indici. Righe figlie e FK non vengono toccate."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='imports'").fetchone()
    if not row or _OLD_IMPORTS_UNIQUE not in row[0]:
        return
    n_before = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    new_sql = re.sub(r'CREATE TABLE (IF NOT EXISTS )?"?imports"?', "CREATE TABLE imports_new", row[0].replace(_OLD_IMPORTS_UNIQUE, _NEW_IMPORTS_UNIQUE), count=1)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP TABLE IF EXISTS imports_new")
        conn.execute(new_sql)
        conn.execute("INSERT INTO imports_new SELECT * FROM imports")
        if conn.execute("SELECT COUNT(*) FROM imports_new").fetchone()[0] != n_before:
            raise RuntimeError("migrazione imports: numero di righe diverso, annullata")
        conn.execute("DROP TABLE imports")
        conn.execute("ALTER TABLE imports_new RENAME TO imports")
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("migrazione imports: violazioni di chiave esterna")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def rebuild_global_imports_unique(conn) -> bool:
    """v6 -> v7 (report globali): un file puo' contenere PIU' marketplace (US + UK) e quindi produrre piu' snapshot. La forma v6 di `global_report_imports` aveva UNIQUE(file_sha256),
    che lo impedisce. SQLite non toglie un UNIQUE: la vecchia tabella viene RINOMINATA `global_report_imports_v6_old` (resta come audit, nessun DROP, nessun dato cancellato) e
    la nuova, con la chiave (file_sha256, marketplace), viene popolata ricopiando TUTTE le righe con gli stessi gimport_id. Le righe figlie e le chiavi esterne non cambiano.
    Tutto in UNA transazione (o tutto o niente) e idempotente: con la forma nuova non fa nulla. Va eseguita PRIMA di creare gli indici della forma v7.
    Ritorna True se ha ricostruito la tabella."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='global_report_imports'").fetchone():
        return False
    uniq = [r for r in conn.execute("PRAGMA index_list(global_report_imports)") if r[2] and r[3] == "u"]
    if not [r for r in uniq if [c[2] for c in conn.execute(f"PRAGMA index_info({r[1]})")] == ["file_sha256"]]:
        return False
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='global_report_imports_v6_old'").fetchone():
        raise RuntimeError("migrazione v7: esiste gia' global_report_imports_v6_old ma global_report_imports ha ancora la forma v6: intervento manuale necessario")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(global_report_imports)")]
    n_before = conn.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")                  # le REFERENCES delle righe figlie restano sul nome `global_report_imports`
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE global_report_imports RENAME TO global_report_imports_v6_old")
        conn.execute(globalstore._IMPORTS_DDL.format(name="global_report_imports").replace("IF NOT EXISTS ", ""))
        keep = [c for c in cols if c in {r[1] for r in conn.execute("PRAGMA table_info(global_report_imports)")}]
        conn.execute(f"INSERT INTO global_report_imports ({','.join(keep)}) SELECT {','.join(keep)} FROM global_report_imports_v6_old ORDER BY gimport_id")
        if conn.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0] != n_before:
            raise RuntimeError("migrazione global_report_imports: numero di righe diverso, annullata")
        conn.execute("UPDATE global_report_imports SET source_gimport_id=gimport_id WHERE source_gimport_id IS NULL")
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("migrazione global_report_imports: violazioni di chiave esterna")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
    return True


def rebuild_global_imports_window_check(conn) -> bool:
    """v7/v8 -> v9 (finestra di CONTESTO 90D): il CHECK su `window_label` di `global_report_imports` ammette ora RECENT_7D / RECENT_30D / RECENT_90D / LIFETIME e CONSERVA RECENT_65D
    (finestra LEGACY: gli snapshot 65D gia' importati restano nel database, leggibili e auditabili; non si possono piu' importare nuovi 65D dalla pagina). SQLite non modifica un CHECK:
    la tabella precedente viene RINOMINATA (`global_report_imports_v7_old` se aveva ancora la forma v7, `global_report_imports_v8_old` se la forma v8: resta come audit, nessun DROP di tabelle,
    nessun dato cancellato) e la nuova, con la forma v9, e' popolata ricopiando TUTTE le righe con gli stessi gimport_id (righe figlie e chiavi esterne invariate). Gli indici univoci della
    vecchia tabella restano attaccati ad essa (nessun DROP): quelli della tabella nuova hanno nomi nuovi (ux_gri_active_v9, ux_gri_file_mp_v9) e li crea globalstore.ensure_schema.
    Una sola transazione (o tutto o niente), idempotente: con la forma v9 non fa nulla. Ritorna True se ha ricostruito la tabella."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='global_report_imports'").fetchone()
    if not row or "RECENT_90D" in row[0]:
        return False
    old_name = "global_report_imports_v8_old" if "RECENT_65D" in row[0] else "global_report_imports_v7_old"
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (old_name,)).fetchone():
        raise RuntimeError(f"migrazione v9: esiste gia' {old_name} ma global_report_imports ha ancora la forma precedente: intervento manuale necessario")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(global_report_imports)")]
    n_before = conn.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0]
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")                  # le REFERENCES delle righe figlie restano sul nome `global_report_imports`
    try:
        conn.execute("BEGIN")
        conn.execute(f"ALTER TABLE global_report_imports RENAME TO {old_name}")
        conn.execute(globalstore._IMPORTS_DDL.format(name="global_report_imports").replace("IF NOT EXISTS ", ""))
        keep = [c for c in cols if c in {r[1] for r in conn.execute("PRAGMA table_info(global_report_imports)")}]
        conn.execute(f"INSERT INTO global_report_imports ({','.join(keep)}) SELECT {','.join(keep)} FROM {old_name} ORDER BY gimport_id")
        if conn.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0] != n_before:
            raise RuntimeError("migrazione global_report_imports (v9): numero di righe diverso, annullata")
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("migrazione global_report_imports (v9): violazioni di chiave esterna")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
    return True


def _upgrade(conn):
    """v8 -> v9 (finestra di contesto 90D al posto del 65D): ricostruzione di `global_report_imports` per allargare il CHECK su window_label a RECENT_90D (RECENT_65D resta ammesso come LEGACY:
    gli snapshot 65D gia' importati restano nel DB); la vecchia tabella resta come global_report_imports_v8_old, nessun dato perso. Backup pre-schema-v9 una sola volta; un DB nuovo nasce v9.
    v7 -> v8 (azioni manuali + finestre 65D / Lifetime nei report globali): tabella NUOVA `manual_actions` (additiva) e ricostruzione di `global_report_imports` per allargare il CHECK su
    window_label (la vecchia tabella resta come global_report_imports_v7_old, nessun dato perso). Backup pre-schema-v8 una sola volta; un DB nuovo nasce direttamente v8.
    v1 (mono-libro) -> v2 (multi-book): ricrea le tabelle SOLO se vuote.
    v2 -> v3 (period snapshot): aggiunge period_id/snapshot_id e li ricalcola dai dati esistenti, SENZA perdere dati.
    v3 -> v4 (multi-marketplace): colonne/tabella nuove, nessuna riga toccata.
    v4 -> v5 (deduplica per snapshot): ricopia imports con UNIQUE (libro, campagna, tipo, periodo, hash), nessuna riga persa.
    v6 -> v7 (report globali multi-marketplace): un file puo' produrre piu' snapshot (US + UK). global_report_imports passa da UNIQUE(file_sha256) a UNIQUE(file_sha256, marketplace)
    (la vecchia tabella resta come global_report_imports_v6_old, nessun DROP); colonne nuove currency/marketplace sulle righe globali, source_gimport_id sugli import,
    marketplace_conflict / legacy_marketplace_confirmed sul mapping, tabella global_report_repairs. Backup pre-schema-v7 una sola volta; un DB nuovo nasce direttamente v7.
    v5 -> v6 (SOLO additiva): tabelle nuove (competitor_*, global_report_imports, global_report_rows, campaign_book_mapping) e colonne nullable di tracciabilita'
    amazon_campaign_id / amazon_ad_group_id / amazon_target_id / amazon_account_id / row_date_from / row_date_to su target_rows e search_term_rows (amazon_account_id anche su campaigns e decisions).
    MULTI-ACCOUNT: la mappa campagne ha chiave (account, ID campagna Amazon); un DB con la prima forma viene portato a quella nuova da globalstore.ensure_schema (ricopia le righe, la vecchia
    tabella resta come audit). Nessun DROP, nessuna riga toccata:
    qui si fa solo il backup e si alza user_version; tabelle e colonne le crea (idempotente) connect() con CREATE ... IF NOT EXISTS / ALTER ... ADD COLUMN.
    Prima di OGNI migrazione di un DB esistente si crea un backup (una volta sola: a schema aggiornato non si ripete)."""
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver >= SCHEMA_VERSION:
        return
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if ver < 2:
        for t in _OLD_TABLES:
            if t in existing and conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:
                raise RuntimeError(f"DB con schema v{ver} contiene dati in '{t}': migrazione manuale necessaria, "
                                   "non ricreo le tabelle.")
        backup_before_migration(conn, ver)
        for t in _OLD_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    else:
        backup_before_migration(conn, ver)
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
    _rebuild_imports_unique(conn)
    rebuild_global_imports_unique(conn)                               # v6 -> v7 (no-op se le tabelle globali non esistono ancora: nascono gia' in forma v7)
    rebuild_global_imports_window_check(conn)                         # v7/v8 -> v9 (no-op se le tabelle globali non esistono ancora o sono gia' v9)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
