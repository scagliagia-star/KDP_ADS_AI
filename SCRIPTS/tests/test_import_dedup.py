"""Collaudo Dashboard V2: primo upload, duplicati reali, nomi Amazon "(1)", nome uguale/contenuto diverso, esclusione di _originali e
staging, dry-run senza scritture, periodo (LIFETIME / 30D) stabile dopo upload e rerun. Solo workspace temporanei."""
import os
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_dashboard_v2 as td2  # noqa: E402
from kdp_ads.db import connect  # noqa: E402
from kdp_ads.importer import import_all  # noqa: E402
from kdp_ads.logs import setup_logger  # noqa: E402
from ops import importing  # noqa: E402

NAME = "Sponsored_Products_Target_Oct_1_2026.csv"
END = date(2026, 10, 1)
PAGE = td2.ROOT / "DASHBOARD" / "pages" / "8_Importa_report.py"


class DedupBase(td2.WsBase):
    def setUp(self):
        super().setUp()
        self.new_book()
        self.new_campaign()
        self.folder = self.ws.input_dir / "B0TEST0001" / "C_EXACT"

    def dry(self, name, data, kind="LIFETIME"):
        plan = importing.plan_upload(self.folder, name, data, importing.resolve_period(kind, None, END))
        entry = importing.build_entry(plan.final_name, "TARGETING", importing.resolve_period(kind, None, END))
        return plan, entry, importing.dry_run(self.ws, "B0TEST0001", "C_EXACT", plan.final_name, data, entry)


class FirstUploadAndDuplicates(DedupBase):
    def test_first_upload_is_dry_run_ok_and_writes_nothing(self):
        before = td2.tree(self.root / "INPUT")
        n_imports = self.count("imports")
        _, _, dry = self.dry(NAME, td2.tcsv())
        self.assertEqual((dry.status, dry.ok), ("DRY_RUN_OK", True))
        self.assertEqual(td2.tree(self.root / "INPUT"), before)                        # nessun file, nessun metadata toccato
        self.assertEqual(self.count("imports"), n_imports)                             # nessun import
        self.assertEqual(self.count("target_rows"), 0)
        self.assertFalse((self.folder / "_originali").exists())
        _, _, again = self.dry(NAME, td2.tcsv())                                       # ripetibile: ancora DRY_RUN_OK, mai falso duplicato
        self.assertEqual(again.status, "DRY_RUN_OK")

    def test_same_content_really_imported_is_duplicate(self):
        data = td2.tcsv()
        plan, entry, dry = self.dry(NAME, data)
        out = importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", NAME, plan.final_name, data, entry)
        self.assertEqual(out.status, "IMPORTED")
        _, _, dup = self.dry(NAME, data)
        self.assertEqual(dup.status, "SKIPPED_DUPLICATE")
        self.assertEqual(self.count("imports"), 1)

    def test_amazon_numbered_name_is_accepted_and_identical_copy_is_duplicate(self):
        data = td2.tcsv()
        plan, entry, dry = self.dry("Sponsored_Products_Target_Oct_1_2026 (1).csv", data)          # primo upload con "(1)"
        self.assertEqual((plan.final_name, plan.status, dry.status), ("Sponsored_Products_Target_Oct_1_2026 (1).csv", "NEW", "DRY_RUN_OK"))
        importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", "x (1).csv", plan.final_name, data, entry)
        for name in (NAME, "Sponsored_Products_Target_Oct_1_2026 (2).csv"):                      # stesso contenuto, altro nome Amazon
            p, _, d = self.dry(name, data)
            self.assertEqual((p.status, p.final_name), ("REUSE_IDENTICAL", "Sponsored_Products_Target_Oct_1_2026 (1).csv"))
            self.assertEqual(d.status, "SKIPPED_DUPLICATE")
        self.assertFalse(list(self.folder.glob("*__2*")))                                         # nessuna copia inutile

    def test_same_name_different_content_gets_safe_name(self):
        data = td2.tcsv()
        plan, entry, _ = self.dry(NAME, data)
        importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", NAME, plan.final_name, data, entry)
        p2, _, d2 = self.dry(NAME, td2.tcsv(5))
        self.assertEqual((p2.status, p2.final_name), ("RENAMED", NAME[:-4] + "__2.csv"))
        self.assertNotEqual(d2.status, "ERROR")


class DiscoveryTests(DedupBase):
    def test_originali_and_staging_are_not_discovered(self):
        data = td2.tcsv()
        plan, entry, _ = self.dry(NAME, data)
        importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", NAME, plan.final_name, data, entry)
        self.assertTrue(list((self.folder / "_originali").glob("*.csv")))                         # la copia esiste...
        (self.folder / "_staging").mkdir()
        (self.folder / "_staging" / "tmp.csv").write_bytes(td2.tcsv(9))
        lg, _ = setup_logger(self.ws.logs_dir, "disc")
        conn = connect(self.ws.db_path)
        try:
            res = import_all(conn, self.ws.settings, self.ws.mapping, lg, dry_run=True)
        finally:
            conn.close()
            importing.close_logger(lg)
        self.assertEqual([r.filename for r in res], [NAME])                                        # ...ma non partecipa alla discovery

    def test_dry_run_staging_is_outside_input(self):
        import tempfile
        seen = []
        real = tempfile.TemporaryDirectory

        class Spy(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                seen.append(Path(self.name))
        from unittest import mock
        with mock.patch.object(importing.tempfile, "TemporaryDirectory", Spy):
            self.dry(NAME, td2.tcsv())
        self.assertTrue(seen)
        for s in seen:
            self.assertNotIn(self.ws.input_dir.resolve(), s.resolve().parents)


class SnapshotDedupTests(DedupBase):
    """Deduplica: book + marketplace + campagna + tipo report + periodo + hash."""

    def commit(self, name, data, rtype="SEARCH_TERM", kind="D65"):
        period = importing.resolve_period(kind, None, END)
        plan = importing.plan_upload(self.folder, name, data, period)
        entry = importing.build_entry(plan.final_name, rtype, period)
        dry = importing.dry_run(self.ws, "B0TEST0001", "C_EXACT", plan.final_name, data, entry)
        return plan, dry, (importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", name, plan.final_name, data, entry) if dry.ok else None)

    def test_same_hash_same_snapshot_is_duplicate(self):
        data = td2.scsv()
        self.assertEqual(self.commit("st.csv", data)[2].status, "IMPORTED")
        plan, dry, out = self.commit("st (1).csv", data)
        self.assertEqual((dry.status, out), ("SKIPPED_DUPLICATE", None))
        self.assertEqual(self.count("imports"), 1)

    def test_same_hash_different_snapshot_is_allowed(self):
        data = td2.scsv()
        self.assertEqual(self.commit("st.csv", data, kind="D65")[2].status, "IMPORTED")
        plan, dry, out = self.commit("st (3).csv", data, kind="D30")
        self.assertEqual(dry.status, "DRY_RUN_OK")
        self.assertEqual(out.status, "IMPORTED")
        self.assertTrue(any("SAME_CONTENT_DIFFERENT_PERIOD" in w for w in dry.warnings))                        # segnalato, non bloccato
        self.assertEqual(plan.final_name, "st (3).csv")                                         # mai riscritta la dichiarazione di st.csv
        c = self.db()
        rows = c.execute("SELECT period_id, file_sha256 FROM imports ORDER BY import_id").fetchall()
        c.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["file_sha256"], rows[1]["file_sha256"])
        self.assertNotEqual(rows[0]["period_id"], rows[1]["period_id"])

    def test_same_hash_same_name_other_period_gets_own_file(self):
        data = td2.scsv()
        self.commit("st.csv", data, kind="D65")
        plan, dry, out = self.commit("st.csv", data, kind="D30")
        self.assertEqual((plan.status, plan.final_name, out.status), ("RENAMED", "st__2.csv", "IMPORTED"))

    def test_snapshot_conflict_still_blocks_different_file_same_snapshot(self):
        self.commit("a.csv", td2.scsv(1))
        _, dry, _ = self.commit("b.csv", td2.scsv(2))
        self.assertEqual(dry.status, "SNAPSHOT_CONFLICT")

    def test_30d_is_standard_and_28d_remains_readable(self):
        p30 = importing.resolve_period("D30", None, END)
        self.assertEqual((p30.scope, p30.date_from, p30.date_to), ("DATE_RANGE", "2026-09-02", "2026-10-01"))
        self.assertNotIn("D28", importing.PERIOD_KINDS)
        p28 = importing.resolve_period("D28", None, END)                                        # vecchi 28D ancora gestiti
        self.assertEqual(p28.date_from, "2026-09-04")
        from ops import weekly
        self.assertEqual([x[2] for x in weekly.EXPECTED], ["Target Lifetime", "Search Term 65D", "Target 30D", "Search Term 30D"])
        self.assertEqual(weekly._kind({"report_scope": "DATE_RANGE", "date_from": "2026-09-04", "date_to": "2026-10-01"}), "D28")
        from kdp_ads.snapshots import window_label, window_role
        self.assertEqual(window_label("DATE_RANGE", "2026-09-02", "2026-10-01"), "RECENT_30D")
        self.assertEqual(window_label("DATE_RANGE", "2026-09-04", "2026-10-01"), "RECENT_28D")
        cfg = {"tactical_max_days": 35, "recent_context_max_days": 90}
        self.assertEqual((window_role("DATE_RANGE", "2026-09-02", "2026-10-01", cfg), window_role("DATE_RANGE", "2026-09-04", "2026-10-01", cfg)),
                         ("TACTICAL", "TACTICAL"))

    def test_old_28d_snapshot_keeps_data_and_does_not_fill_30d_slot(self):
        from ops import weekly
        self.commit("old.csv", td2.tcsv(), rtype="TARGETING", kind="D28")

        def dump():
            c = self.db()
            try:
                return [tuple(r) for r in c.execute("SELECT * FROM imports")], c.execute("SELECT COUNT(*) FROM target_rows").fetchone()[0]
            finally:
                c.close()
        before = dump()
        row = weekly.checklist(self.ws, "B0TEST0001", END)[0]
        self.assertEqual({i["label"]: i["status"] for i in row["items"]}["Target 30D"], "MANCANTE")   # 28 != 30: va caricato il 30D
        self.assertEqual(dump(), before)                                                              # nessuna modifica ai vecchi dati
        self.assertEqual(before[0][0][before[0][0].index("2026-09-04__2026-10-01")], "2026-09-04__2026-10-01")

    def test_weekly_checklist_30d(self):
        from ops import weekly
        self.commit("t30.csv", td2.tcsv(), rtype="TARGETING", kind="D30")
        row = weekly.checklist(self.ws, "B0TEST0001", END)[0]
        self.assertEqual({i["label"]: i["status"] for i in row["items"]},
                         {"Target Lifetime": "MANCANTE", "Search Term 65D": "MANCANTE", "Target 30D": "PRESENTE", "Search Term 30D": "MANCANTE"})


try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PeriodStateTests(DedupBase):
    """La pagina vera (AppTest): il periodo scelto non cambia dopo upload, dry-run e rerun."""

    def setUp(self):
        super().setUp()
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        self.addCleanup(os.environ.pop, "KDP_ADS_ROOT", None)
        self.at = AppTest.from_file(str(PAGE), default_timeout=60).run()
        self.assertFalse(self.at.exception)

    def state(self):
        t = [x for x in self.at.table if "Snapshot ID previsto" in x.value.index]
        return self.at.selectbox(key="imp_period").value, (t[0].value.loc["Snapshot ID previsto"].iloc[0] if t else None)

    def run_flow(self, kind, expect_period_id):
        at = self.at
        at.selectbox(key="imp_period").set_value(kind).run()
        at.date_input(key="imp_date_to").set_value(END).run()
        at.file_uploader[0].upload(NAME, td2.tcsv(), "text/csv")
        at.run()
        self.assertEqual(self.state()[0], kind)
        self.assertIn(expect_period_id, self.state()[1])
        n = self.count("imports")
        [b for b in at.button if b.key == "btn_dry"][0].click().run()                          # dry-run
        self.assertEqual(self.state()[0], kind)
        self.assertIn(expect_period_id, self.state()[1])
        at.run()                                                                               # rerun
        self.assertEqual(self.state()[0], kind)
        self.assertIn(expect_period_id, self.state()[1])
        self.assertEqual(self.count("imports"), n)                                             # il dry-run non scrive nel DB

    def test_lifetime_stays_lifetime(self):
        self.run_flow("LIFETIME", "LIFETIME__2026-10-01")

    def test_30d_stays_30d(self):
        self.run_flow("D30", "2026-09-02__2026-10-01")

    def test_period_choices_offer_30d_not_28d(self):
        self.assertEqual([o for o in self.at.selectbox(key="imp_period").options], ["Storico completo (LIFETIME)", "Ultimi 65 giorni", "Ultimi 30 giorni", "Intervallo personalizzato"])

    def test_weekly_prefill_then_manual_lifetime(self):
        at = self.at
        at.radio[0].set_value("Importazione settimanale").run()
        [b for b in at.button if "Target 30D" in (b.label or "")][0].click().run()
        self.assertEqual(at.selectbox(key="imp_period").value, "D30")
        at.selectbox(key="imp_period").set_value("LIFETIME").run()
        at.date_input(key="imp_date_to").set_value(END).run()
        at.file_uploader[0].upload(NAME, td2.tcsv(), "text/csv")
        at.run()
        self.assertEqual(self.state()[0], "LIFETIME")
        self.assertIn("LIFETIME__2026-10-01", self.state()[1])


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ SAME_CONTENT_DIFFERENT_PERIOD
class SameContentWarningTests(DedupBase):
    CODE = "SAME_CONTENT_DIFFERENT_PERIOD"

    def commit(self, name, data, rtype="SEARCH_TERM", kind="D65"):
        period = importing.resolve_period(kind, None, END)
        plan = importing.plan_upload(self.folder, name, data, period)
        entry = importing.build_entry(plan.final_name, rtype, period)
        dry = importing.dry_run(self.ws, "B0TEST0001", "C_EXACT", plan.final_name, data, entry)
        return dry, (importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", name, plan.final_name, data, entry) if dry.ok else None)

    def two_periods(self):
        data = td2.scsv()
        self.commit("a.csv", data, kind="D65")
        return data, self.commit("a (3).csv", data, kind="D30")

    def test_different_period_allowed_with_structured_warning(self):
        _, (dry, out) = self.two_periods()
        self.assertEqual((dry.status, out.status), ("DRY_RUN_OK", "IMPORTED"))                 # NON blocca
        for ws_ in (dry.warnings, out.warnings):                                               # nel dry-run e nell'esito dell'import
            self.assertTrue(any(w.startswith(self.CODE) and "contenuto identico" in w and "periodo selezionato su Amazon" in w for w in ws_))
        c = self.db()
        rows = c.execute("SELECT import_id, period_id, warnings_json FROM imports ORDER BY import_id").fetchall()
        c.close()
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["warnings_json"])                                            # il primo snapshot non viene modificato
        w = __import__("json").loads(rows[1]["warnings_json"])                                 # salvato e auditabile
        self.assertEqual((w[0]["code"], w[0]["same_content_as"][0]["import_id"]), (self.CODE, rows[0]["import_id"]))
        self.assertNotEqual(rows[0]["period_id"], rows[1]["period_id"])                        # periodi non toccati

    def test_same_period_is_skipped_without_warning(self):
        data = td2.scsv()
        self.commit("a.csv", data, kind="D65")
        dry, out = self.commit("a (1).csv", data, kind="D65")
        self.assertEqual((dry.status, out), ("SKIPPED_DUPLICATE", None))
        self.assertFalse(any(self.CODE in w for w in dry.warnings))

    def test_other_report_type_or_other_campaign_does_not_warn(self):
        data = td2.tcsv()
        self.commit("t.csv", data, rtype="TARGETING", kind="LIFETIME")
        dry, _ = self.commit("t2.csv", td2.tcsv(3), rtype="TARGETING", kind="D30")             # contenuto diverso
        self.assertFalse(any(self.CODE in w for w in dry.warnings))

    def test_propagates_to_analysis_decision_and_dashboard(self):
        from ops import pipeline_ops
        self.two_periods()
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001", "US")
        self.assertTrue(a.ok, a.errors)
        c = self.db()
        aw = __import__("json").loads(c.execute("SELECT warnings_json FROM analysis_runs WHERE run_id=?", (a.data["run_id"],)).fetchone()[0])
        c.close()
        hit = [w for w in aw if self.CODE in w]
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0].startswith("DATA_WARNING") and "C_EXACT" in hit[0] and "SEARCH_TERM" in hit[0])
        d = pipeline_ops.run_book_decisions(self.ws, "B0TEST0001", a.data["run_id"], "US")
        self.assertTrue(d.ok, d.errors)
        c = self.db()
        dw = __import__("json").loads(c.execute("SELECT warnings_json FROM decision_runs WHERE run_id=?", (d.data["run_id"],)).fetchone()[0])
        c.close()
        self.assertTrue(any(self.CODE in w and w.startswith("[analysis_run") for w in dw))     # ereditato
        # dashboard: contesto del decision run
        from data_access.context import load_context
        from data_access.repository import Repo
        repo = Repo(self.ws.db_path, root=td2.ROOT)
        ctx = load_context(repo, "B0TEST0001", d.data["run_id"], "US")
        self.assertIsNone(ctx.error)
        shown = [w for w in ctx.warnings if self.CODE in w["text"]]
        self.assertEqual((len(shown), shown[0]["kind"], shown[0]["campaign"]), (1, "DATA_WARNING", "C_EXACT"))

    def test_no_warning_without_same_content(self):
        from ops import pipeline_ops
        self.commit("a.csv", td2.scsv(), kind="D65")
        self.commit("b.csv", td2.scsv(4), kind="D30")
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001", "US")
        c = self.db()
        aw = c.execute("SELECT warnings_json FROM analysis_runs").fetchone()[0]
        c.close()
        self.assertNotIn(self.CODE, aw)

    def test_historical_runs_unchanged(self):
        from ops import pipeline_ops
        self.commit("a.csv", td2.scsv(), kind="D65")
        a1 = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001", "US")
        c = self.db()
        before = c.execute("SELECT run_id, warnings_json, row_count FROM analysis_runs").fetchall()
        c.close()
        self.commit("a (3).csv", td2.scsv(), kind="D30")                                      # import che genera il warning
        c = self.db()
        after = c.execute("SELECT run_id, warnings_json, row_count FROM analysis_runs").fetchall()
        c.close()
        self.assertEqual([tuple(r) for r in after], [tuple(r) for r in before])               # i run gia' fatti non cambiano
        self.assertNotIn(self.CODE, before[0][1])
