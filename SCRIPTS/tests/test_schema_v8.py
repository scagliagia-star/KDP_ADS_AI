"""SCHEMA v8: registro azioni manuali (tabella NUOVA `manual_actions`) + finestre di sostegno 65D / LIFETIME nei report globali (il CHECK su window_label di `global_report_imports`
si allarga: tabella ricostruita, la v7 resta come `global_report_imports_v7_old`). Migrazione v7 -> v8 con backup una sola volta, idempotente, nessun DROP, nessun dato perso.
Un DB nuovo nasce direttamente v8. Una v7 gia' distribuita non viene mai modificata sul posto: il backup pre-schema-v8 e' la copia v7 intatta. Solo DB temporanei."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "SCRIPTS"))

from kdp_ads import globalstore as GS
from kdp_ads.db import SCHEMA_VERSION, connect

TABLES = ("global_report_imports", "global_report_rows", "campaign_book_mapping", "books", "imports", "target_rows", "search_term_rows", "campaigns")


def make_v7(d) -> Path:
    """DB come DISTRIBUITO alla v7: CHECK window_label solo 7D/30D, window_days NOT NULL, indici ux_gri_*_v7, niente manual_actions. Costruito regredendo un DB nuovo (DROP/RENAME solo nel TEST)."""
    p = Path(d) / "v7.db"
    connect(p).close()
    c = sqlite3.connect(p)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("PRAGMA legacy_alter_table=ON")
    c.execute("ALTER TABLE global_report_imports RENAME TO tmp_v8")
    for ix in ("ux_gri_active_v8", "ux_gri_file_mp_v8"):
        c.execute(f"DROP INDEX IF EXISTS {ix}")
    ddl = GS._IMPORTS_DDL.format(name="global_report_imports").replace("'RECENT_7D','RECENT_30D','RECENT_65D','LIFETIME'", "'RECENT_7D','RECENT_30D'").replace(
        "window_days INTEGER,", "window_days INTEGER NOT NULL,")
    c.executescript(ddl + """
CREATE UNIQUE INDEX ux_gri_active_v7 ON global_report_imports(report_type, window_label, date_from, date_to, marketplace, COALESCE(account_id, '')) WHERE status='ACTIVE';
CREATE UNIQUE INDEX ux_gri_file_mp ON global_report_imports(file_sha256, marketplace);""")
    c.execute("DROP TABLE tmp_v8")
    c.execute("PRAGMA legacy_alter_table=OFF")
    c.execute("DROP TABLE manual_actions")
    for i, (rt, wl, days, d1, d2) in enumerate((("TARGETING", "RECENT_30D", 30, "2026-09-14", "2026-10-13"), ("SEARCH_TERM", "RECENT_7D", 7, "2026-10-07", "2026-10-13")), 1):
        c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,"
                  "campaign_count,account_id,status,revision,source_gimport_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',1,?)",
                  (rt, wl, days, d1, d2, f"{d1}__{d2}", "US", "USD", f"f{i}.csv", f"sha{i}", "2026-10-14T10:00:00", 2, 1, "acc", i))
        for cid in ("1", "2"):
            c.execute("INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,account_id,campaign_name,clicks,materialized,raw_json,marketplace) VALUES (?,?,?,?,?,?,?,?)",
                      (i, cid, "acc", "FRASE", 7, 0, "{}", "US"))
    c.execute("INSERT INTO campaign_book_mapping (account_id,amazon_campaign_id,status,marketplace,detected_type) VALUES ('acc','1','UNASSIGNED','US','PHRASE')")
    c.execute("PRAGMA user_version=7")
    c.commit()
    c.close()
    return p


def counts(path, tables=TABLES):
    c = sqlite3.connect(path)
    try:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        c.close()


def insert_window(c, label, days, d_from, d_to, sha):
    c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,file_name,file_sha256,imported_at,account_id) "
              "VALUES ('TARGETING',?,?,?,?,?,'US','x.csv',?,'2026-11-04T10:00:00','acc')", (label, days, d_from, d_to, f"{d_from}__{d_to}", sha))


class SchemaV8Tests(unittest.TestCase):
    def test_schema_version_is_8(self):
        self.assertEqual(SCHEMA_VERSION, 8)
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "db.py").read_text(encoding="utf-8")
        self.assertIn("v7 -> v8", src)

    def test_fresh_database_is_born_v8_without_backup_and_has_the_new_structures(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "new.db"
            c = connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 8)
            cols = {r[1] for r in c.execute("PRAGMA table_info(manual_actions)")}
            self.assertTrue({"action_id", "created_at", "executed_at", "account_id", "marketplace", "book_id", "amazon_campaign_id", "amazon_ad_group_id", "amazon_target_id", "search_term",
                             "target_identity", "action_type", "value_before", "value_after", "status", "source_decision_id", "source_run_id", "note"} <= cols)
            insert_window(c, "RECENT_65D", 65, "2026-08-10", "2026-10-13", "a")
            insert_window(c, "LIFETIME", None, "2026-10-13", "2026-10-13", "b")                    # window_days NULL ammesso per il Lifetime
            with self.assertRaises(sqlite3.IntegrityError):
                insert_window(c, "RECENT_14D", 14, "2026-09-30", "2026-10-13", "c")                # altre finestre restano vietate
            c.close()
            self.assertFalse((Path(d) / "backups").exists())

    def test_v7_migrates_to_v8_with_one_backup_no_loss_and_the_v7_table_kept_as_audit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v7(d)
            before = counts(p, ("global_report_imports", "global_report_rows", "campaign_book_mapping"))
            self.assertEqual(before, {"global_report_imports": 2, "global_report_rows": 4, "campaign_book_mapping": 1})
            c = sqlite3.connect(p)
            with self.assertRaises(sqlite3.IntegrityError):                                        # la v7 NON ammette 65D
                insert_window(c, "RECENT_65D", 65, "2026-08-10", "2026-10-13", "z")
            gi_before = [tuple(r) for r in c.execute("SELECT * FROM global_report_imports ORDER BY gimport_id")]
            rows_before = [tuple(r) for r in c.execute("SELECT * FROM global_report_rows ORDER BY id")]
            c.close()

            c = connect(p)                                                                         # MIGRAZIONE v7 -> v8
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM global_report_imports ORDER BY gimport_id")], gi_before)         # stessi gimport_id, stesse righe
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM global_report_rows ORDER BY id")], rows_before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports_v7_old").fetchone()[0], 2)                             # audit conservato
            self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='manual_actions'").fetchone())
            self.assertEqual(c.execute("SELECT COUNT(*) FROM campaign_book_mapping").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            insert_window(c, "RECENT_65D", 65, "2026-08-10", "2026-10-13", "n1")                  # ora 65D e LIFETIME sono ammessi
            insert_window(c, "LIFETIME", None, "2026-10-13", "2026-10-13", "n2")
            c.commit()
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"ux_gri_active_v8", "ux_gri_file_mp_v8"} <= names)
            c.close()
            backups = list((Path(d) / "backups").glob(f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_*.db"))
            self.assertEqual(len(backups), 1)
            b = sqlite3.connect(backups[0])                                                         # il backup e' la v7 INTATTA
            self.assertEqual(b.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertEqual(b.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 2)
            self.assertIsNone(b.execute("SELECT 1 FROM sqlite_master WHERE name='manual_actions'").fetchone())
            b.close()

    def test_migration_is_idempotent_and_never_repeats_the_backup(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v7(d)
            connect(p).close()
            first = counts(p)
            for _ in range(3):
                c = connect(p)
                GS.ensure_schema(c)
                c.close()
            self.assertEqual(counts(p), first)
            self.assertEqual(len(list((Path(d) / "backups").glob("*.db"))), 1)
            c = sqlite3.connect(p)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports_v7_old").fetchone()[0], 2)
            c.close()

    def test_unique_active_snapshot_still_holds_after_the_migration(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v7(d)
            c = connect(p)
            with self.assertRaises(sqlite3.IntegrityError):                                         # stesso snapshot logico attivo: vietato (indice ricreato sulla tabella nuova)
                c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,file_name,file_sha256,imported_at,account_id,status) "
                          "VALUES ('TARGETING','RECENT_30D',30,'2026-09-14','2026-10-13','2026-09-14__2026-10-13','US','dup.csv','other','2026-11-04','acc','ACTIVE')")
            c.close()

    def test_a_manual_action_survives_reopening_and_a_v8_db_is_not_touched(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v7(d)
            c = connect(p)
            c.execute("INSERT INTO manual_actions (created_at, executed_at, marketplace, book_id, action_type) VALUES ('a','a','US','B','PAUSE')")
            c.commit()
            c.close()
            c = connect(p)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM manual_actions").fetchone()[0], 1)
            c.close()

    def test_no_destructive_statement_in_the_v8_migration(self):
        import re
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "db.py").read_text(encoding="utf-8")
        body = src[src.index("def rebuild_global_imports_window_check"):src.index("def _upgrade")]
        self.assertIsNone(re.search(r"DROP\s+(TABLE|COLUMN|INDEX)", body))
        self.assertIn("global_report_imports_v7_old", body)

    def test_v5_old_db_reaches_v8_in_one_step_with_one_backup(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = make_v7(d)
            c = sqlite3.connect(p)
            c.execute("PRAGMA user_version=5")
            c.commit()
            c.close()
            c = connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 8)
            c.close()
            self.assertEqual(len(list((Path(d) / "backups").glob("*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
