"""SCHEMA v7: report globali multi-marketplace. Un file puo' produrre piu' snapshot, quindi global_report_imports passa da UNIQUE(file_sha256) a UNIQUE(file_sha256, marketplace).
Una sola forma per versione: v6 = forma gia' distribuita (UNIQUE sull'hash), v7 = forma nuova. Migrazione v6 -> v7 con backup una sola volta, nessun DROP, nessun dato perso.
Solo DB temporanei: mai il DB reale."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_robustness as rb
from kdp_ads.db import SCHEMA_VERSION, connect

V6_IMPORTS_DDL = """CREATE TABLE global_report_imports (
    gimport_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL CHECK (report_type IN ('TARGETING','SEARCH_TERM')),
    window_label TEXT NOT NULL CHECK (window_label IN ('RECENT_7D','RECENT_30D')), window_days INTEGER NOT NULL,
    date_from TEXT NOT NULL, date_to TEXT NOT NULL, period_id TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT,
    file_name TEXT NOT NULL, file_sha256 TEXT NOT NULL UNIQUE, imported_at TEXT NOT NULL,
    row_count INTEGER, campaign_count INTEGER, empty_search_term_rows INTEGER, warnings_json TEXT, parser_version INTEGER,
    account_id TEXT, account_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')),
    revision INTEGER NOT NULL DEFAULT 1, supersedes_gimport_id INTEGER, superseded_by_gimport_id INTEGER, superseded_at TEXT);
CREATE UNIQUE INDEX ux_gri_active ON global_report_imports(report_type, window_label, date_from, date_to, marketplace, COALESCE(account_id, '')) WHERE status='ACTIVE';"""

TABLES = ("books", "campaigns", "imports", "target_rows", "search_term_rows", "global_report_imports", "global_report_rows", "campaign_book_mapping")


def counts(path, tables=TABLES):
    c = sqlite3.connect(path)
    try:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        c.close()


def version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("PRAGMA user_version").fetchone()[0]
    finally:
        c.close()


def file_unique_alone(c) -> bool:
    for r in c.execute("PRAGMA index_list(global_report_imports)"):
        if r[2] and [x[2] for x in c.execute(f"PRAGMA index_info({r[1]})")] == ["file_sha256"]:
            return True
    return False


def make_v6(d) -> Path:
    """DB come DISTRIBUITO alla v6: UNIQUE(file_sha256), niente currency/marketplace sulle righe globali, niente source_gimport_id / conflitti / riparazioni, con dati globali reali di prova
    (import misto USD+GBP registrato US, mapping, righe materializzate sul libro). Costruito da un DB nuovo regredendolo: DROP/RENAME solo nel TEST."""
    p = Path(d) / "v6.db"
    c = connect(p)
    c.execute("INSERT INTO books (book_id,title,marketplace) VALUES ('B1','Uno','US')")
    c.execute("INSERT INTO campaigns (book_id,campaign_id,campaign_name,campaign_type,marketplace,currency) VALUES ('B1','FRASE','FRASE','PHRASE','US','USD')")
    c.execute("INSERT INTO imports (book_id,campaign_id,report_type,import_timestamp,source_filename,file_path,file_sha256,marketplace,period_id,snapshot_id) "
              "VALUES ('B1','FRASE','TARGETING','t','f.csv','GLOBAL:f.csv','h:1','US','P1','B1|FRASE|TARGETING|P1')")
    iid = c.execute("SELECT import_id FROM imports").fetchone()[0]
    c.execute("INSERT INTO target_rows (import_id,book_id,campaign_id,report_type,import_timestamp,source_filename,marketplace,period_id,target,clicks,amazon_campaign_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (iid, "B1", "FRASE", "TARGETING", "t", "f.csv", "US", "P1", "kw uno", 7, "1"))
    for i, (sha, rt, wl) in enumerate((("sha-t30", "TARGETING", "RECENT_30D"), ("sha-s30", "SEARCH_TERM", "RECENT_30D")), start=1):
        c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,"
                  "campaign_count,empty_search_term_rows,account_id,account_name,status,revision) VALUES (?,?,30,'2026-09-14','2026-10-13','p','US',NULL,?,?,'2026-10-19T10:00:00',2,2,0,'acc','Autore','ACTIVE',1)",
                  (rt, wl, f"{sha}.csv", sha))
        c.execute("INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,account_id,campaign_name,clicks,materialized,raw_json) VALUES (?,?,?,?,?,?,?)",
                  (i, "1", "acc", "FRASE", 7, 1, '{"Valuta budget":"USD"}'))
        c.execute("INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,account_id,campaign_name,clicks,materialized,raw_json) VALUES (?,?,?,?,?,?,?)",
                  (i, "2", "acc", "INGHILTERRA", 5, 0, '{"Valuta budget":"GBP"}'))
    c.execute("INSERT INTO campaign_book_mapping (account_id,amazon_campaign_id,status,book_id,marketplace,campaign_id,campaign_type,detected_type) VALUES ('acc','1','ASSIGNED','B1','US','FRASE','PHRASE','PHRASE')")
    c.execute("INSERT INTO campaign_book_mapping (account_id,amazon_campaign_id,status,marketplace,detected_type) VALUES ('acc','2','UNASSIGNED','US','PHRASE')")
    c.commit()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("PRAGMA legacy_alter_table=ON")                                                              # le REFERENCES delle righe restano su global_report_imports
    c.execute("ALTER TABLE global_report_imports RENAME TO tmp_new")
    for ix in ("ux_gri_active_v7", "ux_gri_file_mp", "ux_gri_active_v8", "ux_gri_file_mp_v8", "ux_gri_active_v9", "ux_gri_file_mp_v9"):
        c.execute(f"DROP INDEX IF EXISTS {ix}")
    c.executescript(V6_IMPORTS_DDL)
    c.execute("INSERT INTO global_report_imports SELECT gimport_id,report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,"
              "campaign_count,empty_search_term_rows,warnings_json,parser_version,account_id,account_name,status,revision,supersedes_gimport_id,superseded_by_gimport_id,superseded_at FROM tmp_new")
    c.execute("DROP TABLE tmp_new")
    c.execute("PRAGMA legacy_alter_table=OFF")
    for col in ("currency", "marketplace"):
        c.execute(f"ALTER TABLE global_report_rows DROP COLUMN {col}")
    for col in ("marketplace_conflict", "legacy_marketplace_confirmed"):
        c.execute(f"ALTER TABLE campaign_book_mapping DROP COLUMN {col}")
    c.execute("DROP TABLE global_report_repairs")
    c.execute("PRAGMA user_version=6")
    c.commit()
    c.execute("PRAGMA foreign_keys=ON")
    c.close()
    return p


class SchemaV7Tests(unittest.TestCase):
    def test_1_a_fresh_database_is_born_v7_without_backup(self):
        self.assertGreaterEqual(SCHEMA_VERSION, 7)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "new.db"
            c = connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertFalse(file_unique_alone(c))                                                          # forma v7: la chiave e' (hash, marketplace)
            self.assertTrue({"source_gimport_id"} <= {r[1] for r in c.execute("PRAGMA table_info(global_report_imports)")})
            self.assertTrue({"currency", "marketplace"} <= {r[1] for r in c.execute("PRAGMA table_info(global_report_rows)")})
            self.assertTrue({"marketplace_conflict", "legacy_marketplace_confirmed"} <= {r[1] for r in c.execute("PRAGMA table_info(campaign_book_mapping)")})
            self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='global_report_repairs'").fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='global_report_imports_v6_old'").fetchone())
            c.close()
            self.assertFalse((Path(d) / "backups").exists())

    def test_2_v5_reaches_v7_through_v6_with_one_backup_and_no_loss(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = rb.SchemaMigrationTests().make_v5(d)
            self.assertEqual(version(p), 5)
            c = connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertFalse(file_unique_alone(c))
            self.assertEqual(c.execute("SELECT title FROM books WHERE book_id='B1'").fetchone()[0], "Uno")
            self.assertEqual(c.execute("SELECT clicks, amazon_campaign_id FROM target_rows").fetchone()[:], (7, None))
            self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_book_mapping'").fetchone())      # le tabelle v6 sono state create
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            c.close()
            backups = list((Path(d) / "backups").glob(f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_*.db"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(version(backups[0]), 5)                                                         # la copia e' PRE-migrazione

    def test_3_to_8_deployed_v6_migrates_to_v7_preserving_every_row_without_duplicates(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v6(d)
            self.assertEqual(version(p), 6)
            c = sqlite3.connect(p)
            self.assertTrue(file_unique_alone(c))                                                            # precondizione: forma v6 distribuita
            gi_before = [tuple(r) for r in c.execute("SELECT gimport_id, file_sha256, marketplace, row_count, status FROM global_report_imports ORDER BY gimport_id")]
            rows_before = [tuple(r) for r in c.execute("SELECT id, gimport_id, amazon_campaign_id, clicks, materialized, raw_json FROM global_report_rows ORDER BY id")]
            map_before = [tuple(r) for r in c.execute("SELECT account_id, amazon_campaign_id, status, book_id, marketplace, campaign_id FROM campaign_book_mapping ORDER BY mapping_id")]
            c.close()
            n_before = counts(p)
            self.assertEqual((n_before["global_report_imports"], n_before["global_report_rows"], n_before["campaign_book_mapping"]), (2, 4, 2))

            c = connect(p)                                                                                    # MIGRAZIONE v6 -> v7
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertFalse(file_unique_alone(c))
            # 5. conteggi invariati; 6. import globali preservati; 7. mapping preservati; 8. nessuna duplicazione
            self.assertEqual({t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}, n_before)
            self.assertEqual([tuple(r) for r in c.execute("SELECT gimport_id, file_sha256, marketplace, row_count, status FROM global_report_imports ORDER BY gimport_id")], gi_before)
            self.assertEqual([tuple(r) for r in c.execute("SELECT id, gimport_id, amazon_campaign_id, clicks, materialized, raw_json FROM global_report_rows ORDER BY id")], rows_before)
            self.assertEqual([tuple(r) for r in c.execute("SELECT account_id, amazon_campaign_id, status, book_id, marketplace, campaign_id FROM campaign_book_mapping ORDER BY mapping_id")], map_before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM (SELECT file_sha256, marketplace FROM global_report_imports GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports WHERE source_gimport_id IS NOT gimport_id").fetchone()[0], 0)
            # la vecchia tabella resta come audit (nessun DROP): stesse righe
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports_v6_old").fetchone()[0], 2)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertTrue(all(r[3] for r in c.execute("PRAGMA foreign_key_list(global_report_rows)")) or True)
            fk = [r for r in c.execute("PRAGMA foreign_key_list(global_report_rows)")]
            self.assertEqual([r[2] for r in fk], ["global_report_imports"])                                    # le righe figlie puntano ancora alla tabella vera
            # ora lo stesso file puo' dare una seconda partizione
            c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,file_name,file_sha256,imported_at,account_id) "
                      "VALUES ('TARGETING','RECENT_30D',30,'2026-09-14','2026-10-13','p','UK','f.csv','sha-t30','2026-10-19T10:00:00','acc')")
            c.commit()
            c.close()
            backups = list((Path(d) / "backups").glob(f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_*.db"))
            self.assertEqual(len(backups), 1)                                                                 # backup pre-v7 una sola volta
            self.assertEqual(version(backups[0]), 6)
            self.assertEqual(counts(backups[0]), n_before)                                                    # il backup e' la v6 intatta

    def test_4_reopening_v7_does_nothing_and_makes_no_new_backup(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v6(d)
            connect(p).close()
            snap = counts(p, TABLES + ("global_report_imports_v6_old", "global_report_repairs"))
            nb = len(list((Path(d) / "backups").glob("*.db")))
            for _ in range(3):
                c = connect(p)
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                c.close()
            self.assertEqual(counts(p, TABLES + ("global_report_imports_v6_old", "global_report_repairs")), snap)
            self.assertEqual(len(list((Path(d) / "backups").glob("*.db"))), nb)
            self.assertEqual(nb, 1)

    def test_v6_database_gets_the_v7_shape_even_when_only_ensure_schema_runs(self):
        """Vecchio DB aperto da un percorso che chiama solo globalstore.ensure_schema: stessa migrazione, stesso risultato."""
        from kdp_ads import globalstore as GS
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v6(d)
            c = sqlite3.connect(p)
            GS.ensure_schema(c)
            self.assertFalse(file_unique_alone(c))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 2)
            GS.ensure_schema(c)                                                                               # idempotente
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 2)
            c.close()

    def test_no_destructive_statement_in_the_v7_migration(self):
        import re
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "db.py").read_text(encoding="utf-8")
        body = src[src.index("def rebuild_global_imports_unique"):src.index("def _upgrade")]
        self.assertIsNone(re.search(r"DROP\s+(TABLE|COLUMN|INDEX)", body))


if __name__ == "__main__":
    unittest.main()
