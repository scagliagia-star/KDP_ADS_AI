"""Test della dashboard READ-ONLY: data access, contesto run, filtri, compatibilita' con run/schema storici, nessuna scrittura.
Streamlit non e' richiesto: le pagine vengono eseguite con uno stub."""
import hashlib
import json
import os
import runpy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_rules as tr
from test_snapshots import BOOK, D65, LIFE
from test_target_status import LIFE_BAD, ST65, StatusBase
from data_access.context import load_context
from data_access.repository import Repo, RepoError, filter_decisions, parse_delivery_detail, summarize
import formatting as F
from kdp_ads.analysis import analyze_book
from kdp_ads.decision import build_decisions
from kdp_ads.decisions_report import save_decisions_to_db
from kdp_ads.report import save_to_db

OVERLAP_HEADER = ("book_id,period_id,search_term,overlap_classification,possible_inefficiency,better_campaign,worse_campaign,"
                  "recommendation,detail,campaign_id,campaign_type,clicks,cpc,orders,conversion_rate,spend,sales,acos,estimated_direct_ad_profit")
WARNINGS = ["DATA_WARNING C_EXACT: clicks A (27.00) > B (26.00)", "DATA_WARNING C_PAUSED: spesa diversa da Amazon"]


class DashBase(StatusBase):
    """C_EXACT abilitato con bid-down (azionabile); C_PAUSED in pausa con stesso candidato."""

    def setUp(self):
        super().setUp()
        for cid in ("C_EXACT", "C_PAUSED"):
            self.snap_campaign(cid, "EXACT", [("t_life.csv", tr.it_rows_target([("kw",) + LIFE_BAD]), "TARGETING", LIFE),
                                              ("s65.csv", tr.it_rows_st([("kw", "kw") + ST65]), "SEARCH_TERM", D65)])
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [(r.filename, r.problems) for r in res])
        for r in self.conn.execute("SELECT id, raw_json FROM target_rows WHERE campaign_id='C_PAUSED'").fetchall():
            raw = json.loads(r["raw_json"])
            raw["Stato"] = "IN PAUSA"
            self.conn.execute("UPDATE target_rows SET raw_json=? WHERE id=?", (json.dumps(raw), r["id"]))
        self.conn.commit()
        an = analyze_book(self.conn, self.settings, self.mapping, tr.RULES_PATH, BOOK, self.logger)
        self.ov_path = Path(self.tmp.name) / "overlaps.csv"
        self.ov_path.write_text(
            OVERLAP_HEADER + "\n"
            f"{BOOK},2026-07-28__2026-09-30,kw,BETTER_IN_PHRASE,True,C_EXACT,C_PAUSED,reduce,detail text,C_EXACT,EXACT,254,0.95,24,9.45,241.81,455.76,53.06,-36.61\n"
            f"{BOOK},2026-07-28__2026-09-30,kw,BETTER_IN_PHRASE,True,C_EXACT,C_PAUSED,reduce,detail text,C_PAUSED,PHRASE,133,0.93,16,12.03,123.24,303.84,N/A_NO_SALES,13.56\n"
            f"{BOOK},2026-08-30__2026-09-26,other,INSUFFICIENT_DATA,False,,,,few clicks,C_EXACT,EXACT,4,1.02,0,0,4.08,0,N/A_NO_SALES,-4.08\n",
            encoding="utf-8")
        self.analysis_id = save_to_db(self.conn, an, "a.csv", "a.md", str(self.ov_path), an.ctx.rules)
        self.conn.execute("UPDATE analysis_runs SET warnings_json=? WHERE run_id=?", (json.dumps(WARNINGS), self.analysis_id))
        self.conn.commit()
        ds = build_decisions(self.conn, self.settings, tr.RULES_PATH, BOOK, self.analysis_id)
        self.decision_run = save_decisions_to_db(self.conn, ds, "d.csv", "d.md")
        self.db = Path(self.settings["db_path"])
        self.repo = Repo(self.db, root=Path(self.tmp.name))

    def by_campaign(self, ctx, cid):
        return [d for d in ctx.decisions if d["campaign_id"] == cid]


class RepositoryTests(DashBase):
    def test_books_loaded_with_economics(self):
        books = self.repo.list_books()
        self.assertEqual([b["book_id"] for b in books], [BOOK])
        self.assertIsNotNone(books[0].get("royalty_per_copy"))

    def test_latest_decision_run_and_mapping_to_analysis_run(self):
        ds2 = build_decisions(self.conn, self.settings, tr.RULES_PATH, BOOK, self.analysis_id)
        second = save_decisions_to_db(self.conn, ds2, "d2.csv", "d2.md")
        self.assertEqual(self.repo.latest_decision_run(BOOK)["run_id"], second)
        ctx = load_context(self.repo)
        self.assertEqual(ctx.run["run_id"], second)                                   # default = ultimo
        self.assertEqual(ctx.analysis["run_id"], self.analysis_id)                     # decision run -> analysis run
        older = load_context(self.repo, BOOK, self.decision_run)
        self.assertEqual((older.run["run_id"], older.analysis["run_id"]), (self.decision_run, self.analysis_id))
        self.assertEqual([r["run_id"] for r in ctx.runs], [second, self.decision_run])

    def test_actionable_and_paused_counts(self):
        ctx = load_context(self.repo)
        s = ctx.summary
        self.assertEqual(s["total"], len(ctx.decisions))
        self.assertEqual(s["actionable"], 1)
        self.assertEqual(s["paused"], 1)
        self.assertEqual(s["paused_with_underlying"], 1)
        act = [d for d in ctx.decisions if d["operationally_actionable"]]
        self.assertEqual([(d["campaign_id"], d["target_status"]) for d in act], [("C_EXACT", "ENABLED")])
        pz = self.by_campaign(ctx, "C_PAUSED")[0]
        self.assertEqual((pz["final_action"], pz["underlying_candidate_action"]), ("HOLD_PAUSED_TARGET", "BID_DOWN_10_CANDIDATE"))

    def test_inherited_warnings_listed_with_provenance(self):
        ctx = load_context(self.repo)
        self.assertEqual(ctx.summary["warnings"], 2)
        self.assertEqual({w["text"] for w in ctx.warnings}, set(WARNINGS))
        self.assertEqual({w["origin"] for w in ctx.warnings}, {f"analysis_run {self.analysis_id}"})
        self.assertEqual({w["campaign"] for w in ctx.warnings}, {"C_EXACT", "C_PAUSED"})

    def test_filters(self):
        ctx = load_context(self.repo)
        D = ctx.decisions
        self.assertTrue(D)
        self.assertEqual({d["campaign_id"] for d in filter_decisions(D, campaign="C_PAUSED")}, {"C_PAUSED"})
        self.assertEqual([d["campaign_id"] for d in filter_decisions(D, actionable=True)], ["C_EXACT"])
        self.assertEqual({d["campaign_id"] for d in filter_decisions(D, actionable=False)}, {"C_PAUSED"})
        self.assertEqual(len(filter_decisions(D, target_status="PAUSED")), 1)
        self.assertEqual(len(filter_decisions(D, search="KW")), len(D))             # ricerca target case-insensitive
        self.assertEqual(filter_decisions(D, search="zzz"), [])
        self.assertEqual(len(filter_decisions(D, campaign="ALL", priority="ALL")), len(D))

    def test_campaign_overview_never_sums_windows(self):
        camps = {c["campaign_id"]: c for c in self.repo.campaign_overview(BOOK, 8.55)}
        self.assertEqual(set(camps), {"C_EXACT", "C_PAUSED"})
        w = {x["window"]: x for x in camps["C_EXACT"]["windows"]}
        self.assertEqual(set(w), {"LIFETIME", "RECENT_65D"})
        self.assertEqual((w["LIFETIME"]["clicks"], w["RECENT_65D"]["clicks"]), (400, 100))     # mai 500
        self.assertAlmostEqual(w["RECENT_65D"]["spend_per_day"], 275 / 65)

    def test_overlaps_grouped_and_alert(self):
        groups, msg = self.repo.overlaps(self.repo.analysis_run(self.analysis_id))
        self.assertEqual((len(groups), msg), (2, ""))
        self.assertTrue(groups[0]["possible_inefficiency"])                              # gli alert vengono per primi
        self.assertEqual(len(groups[0]["members"]), 2)
        self.assertIsNone(groups[0]["members"][1]["acos_value"])                        # N/A_NO_SALES non diventa 0
        self.assertEqual(self.repo.overlaps(None)[0], [])

    def test_parse_delivery_detail(self):
        d = parse_delivery_detail("x | Delivery: DELIVERY_COLLAPSED (TARGET:SEARCH_TERM) impressions/giorno: recente 24.86 "
                                  "(2026-08-30__2026-09-26, 28g) vs riferimento 259.57 (2026-07-28__2026-09-30, 65g) = x0.10 | y")
        self.assertEqual((d["recent"], d["reference"], d["ratio"], d["recent_days"], d["reference_days"]), (24.86, 259.57, 0.10, 28, 65))
        self.assertIsNone(parse_delivery_detail("nessun dettaglio"))
        self.assertIsNone(parse_delivery_detail(None))


class CompatibilityTests(DashBase):
    def test_historical_run_without_new_fields(self):
        self.conn.execute("UPDATE decisions SET target_status=NULL, underlying_candidate_action=NULL, operationally_actionable=NULL, delivery_source=NULL")
        self.conn.commit()
        ctx = load_context(self.repo)
        s = ctx.summary
        self.assertEqual((s["actionable"], s["paused"], s["paused_with_underlying"], s["has_new_fields"]), (None, None, None, False))
        self.assertEqual(s["total"], len(ctx.decisions))
        self.assertEqual(F.num(s["actionable"]), "n/d")

    def test_legacy_schema_without_new_columns_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            c = sqlite3.connect(db)
            c.executescript("""
                CREATE TABLE decision_runs (run_id INTEGER PRIMARY KEY, book_id TEXT, generated_at TEXT, analysis_run_id INTEGER, warnings_json TEXT);
                CREATE TABLE decisions (pk INTEGER PRIMARY KEY, run_id INTEGER, campaign_id TEXT, target TEXT, priority TEXT, final_action TEXT);
                INSERT INTO decision_runs VALUES (1, 'B1', '2026-01-01T00:00:00', 7, '[]');
                INSERT INTO decisions VALUES (1, 1, 'C1', 'kw', 'P2', 'HOLD');
            """)
            c.commit()
            c.close()
            repo = Repo(db)
            ctx = load_context(repo)                                   # niente tabelle books/analysis_runs/campaigns
            self.assertIsNone(ctx.error, ctx.error)
            self.assertEqual(ctx.book["book_id"], "B1")
            self.assertIsNone(ctx.analysis)
            self.assertEqual((ctx.summary["total"], ctx.summary["actionable"], ctx.summary["paused"]), (1, None, None))
            self.assertEqual(ctx.warnings, [])
            self.assertEqual(repo.campaign_overview("B1", 1.0), [])
            self.assertEqual(repo.overlaps(ctx.analysis)[0], [])
            self.assertEqual(filter_decisions(ctx.decisions, priority="P2", actionable=False)[0]["target"], "kw")

    def test_missing_db_and_empty_db(self):
        self.assertIn("non trovato", load_context(Repo(Path(self.tmp.name) / "nope.db")).error)
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        self.assertTrue(load_context(Repo(empty)).error)              # nessun libro
        only_books = Path(self.tmp.name) / "books.db"
        c = sqlite3.connect(only_books)
        c.execute("CREATE TABLE books (book_id TEXT, title TEXT)")
        c.execute("INSERT INTO books VALUES ('B', 'T')")
        c.commit()
        c.close()
        ctx = load_context(Repo(only_books))
        self.assertIn("decision run", ctx.error)
        self.assertFalse(ctx.ok)

    def test_no_division_by_zero_and_nulls_in_formatting(self):
        self.assertEqual(F.acos(None), "n/d (nessuna vendita)")
        self.assertEqual((F.money(None), F.pct(None), F.num(None), F.bid_change(None)), ("n/d", "n/d", "n/d", "-"))
        self.assertIsNone(F.safe_div(1, 0))
        self.assertIsNone(F.safe_div(None, 2))
        self.assertEqual(F.money(-3.5), "-$3.50")
        self.assertEqual(summarize([], [])["total"], 0)

    def test_old_decision_run_inherits_analysis_warnings_not_saved_in_it(self):
        self.conn.execute("UPDATE decision_runs SET warnings_json='[]'")
        self.conn.commit()
        ctx = load_context(self.repo)
        self.assertEqual(len(ctx.warnings), 2)
        self.assertTrue(all("non salvato" in w["origin"] for w in ctx.warnings))


class ReadOnlyTests(DashBase):
    def digest(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest(), self.db.stat().st_mtime_ns

    def test_reads_never_write_the_database(self):
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = self.digest()
        ctx = load_context(self.repo)
        self.repo.campaign_overview(BOOK, 8.55)
        self.repo.overlaps(ctx.analysis)
        filter_decisions(ctx.decisions, campaign="C_EXACT")
        self.assertEqual(self.digest(), before)

    def test_connection_is_read_only(self):
        conn = self.repo._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE x (a)")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE decisions SET priority='P1'")
        finally:
            conn.close()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='x'").fetchone()[0], 0)

    def test_dashboard_has_no_action_buttons(self):
        # V1 (sola lettura): nessun pulsante/form. Le pagine V2 (gestione/importazione) sono escluse da QUESTO controllo
        # (hanno pulsanti perche' scrivono file locali), ma restano soggette a tutti gli altri divieti (rete, Amazon, SQL di scrittura).
        v2 = ("pages/7_", "pages/8_", "pages/10_", "components/ops_ui.py", "ops/")
        files = [(p, str(p.relative_to(ROOT / "DASHBOARD")).replace("\\", "/")) for p in (ROOT / "DASHBOARD").rglob("*.py")]
        v1_src = "".join(p.read_text(encoding="utf-8") for p, rel in files if not rel.startswith(v2))
        all_src = "".join(p.read_text(encoding="utf-8") for p, _ in files)
        for forbidden in ("st.button", "st.form"):
            self.assertNotIn(forbidden, v1_src, forbidden)
        for forbidden in ("requests", "urllib", "amazon_ads", "INSERT ", "UPDATE ", "DELETE ", "DROP "):
            self.assertNotIn(forbidden, all_src, forbidden)


class UiSmokeTests(DashBase):
    """Esegue app.py e le pagine con uno stub di streamlit sul DB di test: nessuna eccezione, nessun pulsante, DB invariato."""

    @staticmethod
    def stub():
        st = mock.MagicMock()
        st.columns.side_effect = lambda n, *a, **k: [mock.MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        st.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st.sidebar.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st.text_input.return_value = ""
        st.stop.side_effect = SystemExit
        return st

    def test_all_pages_render(self):
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        old_env, old_mod = os.environ.get("KDP_ADS_DB"), sys.modules.get("streamlit")
        os.environ["KDP_ADS_DB"] = str(self.db)
        try:
            for page in [ROOT / "DASHBOARD" / "app.py", ROOT / "DASHBOARD" / "panoramica.py"] + sorted(
                    p for p in (ROOT / "DASHBOARD" / "pages").glob("*.py") if not p.name.startswith(("7_", "8_", "10_"))):
                st = self.stub()
                sys.modules["streamlit"] = st
                for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
                    del sys.modules[m]
                try:
                    runpy.run_path(str(page), run_name="__main__")
                except SystemExit:
                    pass
                self.assertFalse([c for c in st.method_calls if c[0] in ("button", "form_submit_button")], page.name)
                self.assertTrue(st.method_calls, page.name)
        finally:
            if old_env is None:
                os.environ.pop("KDP_ADS_DB", None)
            else:
                os.environ["KDP_ADS_DB"] = old_env
            if old_mod is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = old_mod
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
