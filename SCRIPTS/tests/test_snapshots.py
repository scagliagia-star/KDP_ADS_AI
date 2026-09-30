"""Test PERIOD SNAPSHOT: snapshot multipli, finestre, per-giorno, delivery, recency priority. Dati sintetici."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_importer as ti
import test_rules as tr
from kdp_ads import amazon, registry, windows as win
from kdp_ads.analysis import analyze_book
from kdp_ads.db import connect
from kdp_ads.decision import build_decisions
from kdp_ads.report import save_to_db
from kdp_ads.rules import load_rules
from kdp_ads.snapshots import days_inclusive, period_id, snapshot_id, window_label, window_role

BOOK = "1805566563"
LIFE = ("LIFETIME", None, "2026-09-30")
D65 = ("DATE_RANGE", "2026-07-28", "2026-09-30")
D28 = ("DATE_RANGE", "2026-09-03", "2026-09-30")
CFG = load_rules(tr.RULES_PATH)


class SBase(ti.Base):
    def setUp(self):
        super().setUp()
        self.add_book()

    def snap_campaign(self, cid, ctype, specs):
        """specs: [(filename, csv_text, report_type, (scope, date_from, date_to))] -> cartella campagna con metadata.snapshots."""
        snaps, files = [], {}
        for name, text, rt, (scope, f, t) in specs:
            files[name] = text
            snaps.append({"file": name, "report_type": rt, "report_scope": scope, "date_from": f, "date_to": t})
        return self.campaign(BOOK, cid, ctype, files, snapshots=snaps)

    def full(self, cid="C_EXACT", life=None, st65=None, t28=None, st28=None):
        specs = [("t_life.csv", tr.it_rows_target([("kw",) + life]), "TARGETING", LIFE)]
        if st65:
            specs.append(("s65.csv", tr.it_rows_st([("kw", "kw") + st65]), "SEARCH_TERM", D65))
        if t28:
            specs.append(("t28.csv", tr.it_rows_target([("kw",) + t28]), "TARGETING", D28))
        if st28:
            specs.append(("s28.csv", tr.it_rows_st([("kw", "kw") + st28]), "SEARCH_TERM", D28))
        self.snap_campaign(cid, "EXACT", specs)
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [(r.filename, r.problems) for r in res])

    def analyze_and_decide(self):
        an = analyze_book(self.conn, self.settings, self.mapping, tr.RULES_PATH, BOOK, self.logger)
        run = save_to_db(self.conn, an, "a", "b", "c", an.ctx.rules)
        return an, build_decisions(self.conn, self.settings, tr.RULES_PATH, BOOK, run)

    def dec(self, ds, target="kw", campaign="C_EXACT"):
        m = [d for d in ds.decisions if d["target"] == target and d["campaign_id"] == campaign and "negterm" not in d["action_key"]]
        self.assertEqual(len(m), 1, [d["action_key"] for d in ds.decisions])
        return m[0]


# ------------------------------------------------------------------ identita' snapshot e importazione
class PeriodIdTests(unittest.TestCase):
    def test_period_id_formats(self):
        self.assertEqual(period_id(*D28), "2026-09-03__2026-09-30")
        self.assertEqual(period_id("DATE_RANGE", "2026-08-30", "2026-09-26"), "2026-08-30__2026-09-26")
        self.assertEqual(period_id(*LIFE), "LIFETIME__2026-09-30")
        self.assertEqual(period_id("UNKNOWN", None, None), "UNKNOWN__NA")
        self.assertEqual(snapshot_id(BOOK, "C", "TARGETING", *LIFE), f"{BOOK}|C|TARGETING|LIFETIME__2026-09-30")

    def test_windows_and_roles(self):
        self.assertEqual((days_inclusive("2026-08-30", "2026-09-26"), days_inclusive("2026-07-28", "2026-09-30")), (28, 65))
        self.assertIsNone(days_inclusive(None, "2026-09-30"))
        self.assertEqual([window_label(*w) for w in (LIFE, D65, D28)], ["LIFETIME", "RECENT_65D", "RECENT_28D"])
        s = CFG["snapshots"]
        self.assertEqual([window_role(*w, s) for w in (LIFE, D65, D28)], ["HISTORICAL_CONTEXT", "RECENT_CONTEXT", "TACTICAL"])
        self.assertEqual(window_role("DATE_RANGE", "2026-01-01", "2026-09-30", s), "HISTORICAL_CONTEXT")   # finestra molto lunga

    def test_metadata_snapshot_validation(self):
        m = ti.meta_for(BOOK, "C", "EXACT")
        self.assertEqual(registry.validate_metadata({**m, "snapshots": [{"file": "a.csv", "report_scope": "DATE_RANGE",
                                                                          "date_from": "2026-09-03", "date_to": "2026-09-30"}]}), [])
        for bad in ([{"report_scope": "LIFETIME"}], [{"file": "a.csv", "report_scope": "DATE_RANGE", "date_from": None, "date_to": "2026-09-30"}],
                    [{"file": "a.csv", "report_scope": "LIFETIME"}, {"file": "a.csv", "report_scope": "LIFETIME"}],
                    [{"file": "a.csv", "report_scope": "NOPE"}], "not-a-list"):
            self.assertTrue(registry.validate_metadata({**m, "snapshots": bad}), bad)


class MultiSnapshotImportTests(SBase):
    def test_two_snapshots_same_campaign_same_report_type(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500), t28=(40, 4, 110, 200), st28=(40, 4, 110, 200))
        rows = self.conn.execute("SELECT report_type, period_id, row_count FROM imports ORDER BY report_type, period_id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("SEARCH_TERM", "2026-07-28__2026-09-30", 1), ("SEARCH_TERM", "2026-09-03__2026-09-30", 1),
                                                    ("TARGETING", "2026-09-03__2026-09-30", 1), ("TARGETING", "LIFETIME__2026-09-30", 1)])
        self.assertEqual(self.count("target_rows"), 2)
        self.assertEqual(self.count("search_term_rows"), 2)
        self.assertEqual(self.count("search_term_rows", period_id="2026-09-03__2026-09-30"), 1)   # non fusi
        self.assertEqual(self.conn.execute("SELECT COUNT(DISTINCT snapshot_id) FROM imports").fetchone()[0], 4)

    def test_same_report_type_different_dates_never_overwritten(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500), st28=(40, 4, 110, 200), t28=(40, 4, 110, 200))
        st = self.conn.execute("SELECT period_id, clicks FROM search_term_rows ORDER BY clicks").fetchall()
        self.assertEqual([(r[0], r[1]) for r in st], [("2026-09-03__2026-09-30", 40), ("2026-07-28__2026-09-30", 100)])

    def test_duplicate_snapshot_same_file_skipped(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500))
        res = self.run_import()
        self.assertEqual({r.status for r in res}, {"SKIPPED_DUPLICATE"})
        self.assertEqual(self.count("imports"), 2)

    def test_same_snapshot_different_file_is_conflict_not_overwrite(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500))
        d = Path(self.settings["input_dir"]) / BOOK / "C_EXACT"
        (d / "s65_reexport.csv").write_text(tr.it_rows_st([("kw", "kw", 90, 9, 250, 450)]), encoding="utf-8")
        meta = json.loads((d / "metadata.json").read_text())
        meta["snapshots"].append({"file": "s65_reexport.csv", "report_type": "SEARCH_TERM", "report_scope": "DATE_RANGE",
                                  "date_from": D65[1], "date_to": D65[2]})
        (d / "metadata.json").write_text(json.dumps(meta))
        res = {r.filename: r for r in self.run_import()}
        self.assertEqual(res["s65_reexport.csv"].status, "REJECTED")
        self.assertIn("SNAPSHOT_CONFLICT", res["s65_reexport.csv"].problems[0])
        self.assertEqual(self.count("imports"), 2)
        self.assertEqual(self.conn.execute("SELECT clicks FROM search_term_rows").fetchone()[0], 100)   # originale intatto

    def test_snapshot_report_type_mismatch_rejected(self):
        self.snap_campaign("C_EXACT", "EXACT", [("x.csv", tr.it_rows_target([("kw", 10, 1, 10, 40)]), "SEARCH_TERM", D28)])
        self.assertEqual(self.run_import()[0].status, "REJECTED")

    def test_legacy_metadata_without_snapshots_still_works(self):
        r = self.one(name="t.csv", text=tr.it_rows_target([("kw", 10, 1, 10, 40)]))
        self.assertEqual((r.status, r.period_id), ("IMPORTED", "LIFETIME__2026-09-30"))

    def test_migration_v2_to_v3_backfills_without_losing_data(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500))
        self.conn.execute("UPDATE imports SET period_id=NULL, snapshot_id=NULL")
        self.conn.execute("UPDATE target_rows SET period_id=NULL, snapshot_id=NULL")
        self.conn.execute("UPDATE search_term_rows SET period_id=NULL, snapshot_id=NULL")
        self.conn.execute("PRAGMA user_version=2")
        self.conn.commit()
        self.conn.close()
        self.conn = connect(self.settings["db_path"])
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual({r[0] for r in self.conn.execute("SELECT period_id FROM imports")}, {"LIFETIME__2026-09-30", "2026-07-28__2026-09-30"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM target_rows WHERE period_id='LIFETIME__2026-09-30'").fetchone()[0], 1)
        self.assertEqual(self.count("search_term_rows"), 1)


# ------------------------------------------------------------------ finestre, per-giorno, delivery
class WindowLogicTests(unittest.TestCase):
    def rec(self, rt, scope, f, t, clicks=10, impr=500, spend=10, orders=1, sales=40, cid="C"):
        return {"campaign_id": cid, "report_type": rt, "report_scope": scope, "date_from": f, "date_to": t, "period_id": None,
                "impressions": impr, "clicks": clicks, "spend": spend, "orders": orders, "sales": sales}

    def test_latest_snapshot_selection(self):
        recs = [self.rec("TARGETING", *LIFE), self.rec("SEARCH_TERM", *D65), self.rec("TARGETING", *D65),
                self.rec("TARGETING", "DATE_RANGE", "2026-08-30", "2026-09-26"), self.rec("SEARCH_TERM", "DATE_RANGE", "2026-08-30", "2026-09-26"),
                self.rec("TARGETING", *D28), self.rec("SEARCH_TERM", *D28)]
        c = win.campaign_context(recs, CFG["snapshots"])["C"]
        self.assertEqual(c["comparable"], {"2026-07-28__2026-09-30", "2026-08-30__2026-09-26", "2026-09-03__2026-09-30"})
        self.assertEqual((c["latest_comparable"], c["tactical_pid"], c["tactical_comparable"]), ("2026-09-03__2026-09-30",) * 2 + (True,))

    def test_no_comparable_falls_back_to_latest_tactical_target_else_none(self):
        recs = [self.rec("TARGETING", *LIFE), self.rec("SEARCH_TERM", *D65), self.rec("TARGETING", *D28)]
        c = win.campaign_context(recs, CFG["snapshots"])["C"]
        self.assertEqual((c["latest_comparable"], c["tactical_pid"], c["tactical_comparable"]), (None, "2026-09-03__2026-09-30", False))
        c = win.campaign_context([self.rec("TARGETING", *LIFE), self.rec("SEARCH_TERM", *D65)], CFG["snapshots"])["C"]
        self.assertEqual((c["latest_comparable"], c["tactical_pid"]), (None, None))       # situazione attuale reale

    def test_per_day_normalization(self):
        a = win.agg_of(self.rec("SEARCH_TERM", *D28, impr=2800, clicks=56, spend=28, orders=7))
        pd_ = win.per_day_metrics(a)
        self.assertEqual((pd_["impressions_per_day"], pd_["clicks_per_day"], pd_["spend_per_day"], pd_["orders_per_day"]), (100, 2, 1, 0.25))
        lt = win.per_day_metrics(win.agg_of(self.rec("TARGETING", *LIFE)))
        self.assertTrue(all(v is None for v in lt.values()))                        # lifetime: giorni non noti

    def test_delivery_thresholds(self):
        cfg = CFG["delivery"]
        ref = {"period_id": "ref", "days": 65, "impressions": 6500, "clicks": 130, "spend": 100, "orders": 10, "sales": 400}   # 100 impr/giorno

        def sig(per_day_impr, days=28, clicks=50):
            latest = {"period_id": "lat", "days": days, "impressions": per_day_impr * days, "clicks": clicks, "spend": 1, "orders": 0, "sales": 0}
            return win.classify_delivery(latest, ref, cfg)
        self.assertEqual(sig(150)[0], "DELIVERY_INCREASING")
        self.assertEqual(sig(100)[0], "DELIVERY_STABLE")
        self.assertEqual(sig(60)[0], "DELIVERY_DECLINING")
        self.assertEqual(sig(10)[0], "DELIVERY_COLLAPSED")
        self.assertEqual(sig(10)[1], 0.1)
        # UNKNOWN: riferimento con pochi clic / finestre troppo brevi / mancanti
        self.assertEqual(win.classify_delivery({"period_id": "l", "days": 28, "impressions": 1, "clicks": 1}, {**ref, "clicks": 3}, cfg)[0], "UNKNOWN")
        self.assertEqual(win.classify_delivery({"period_id": "l", "days": 3, "impressions": 1, "clicks": 1}, ref, cfg)[0], "UNKNOWN")
        self.assertEqual(win.classify_delivery(None, ref, cfg)[0], "UNKNOWN")

    def test_delivery_compares_same_report_type_only_and_absent_row_is_unknown(self):
        cc = win.campaign_context([self.rec("TARGETING", *LIFE), self.rec("SEARCH_TERM", *D65), self.rec("SEARCH_TERM", *D28)], CFG["snapshots"])["C"]
        ref = win.agg_of(self.rec("SEARCH_TERM", *D65, impr=6500, clicks=130))
        sig, ratio, src, detail, flags = win.delivery_from_rows({"SEARCH_TERM": {ref["period_id"]: ref}}, cc["snaps"], None, CFG["delivery"])
        self.assertEqual((sig, ratio, flags), ("UNKNOWN", None, ["ABSENT_IN_LATEST_SNAPSHOT"]))
        # un Target lifetime non e' mai confrontato con un Search Term
        life = win.agg_of(self.rec("TARGETING", *LIFE, impr=10 ** 6))
        sig2 = win.delivery_from_rows({"TARGETING": {life["period_id"]: life}}, cc["snaps"], None, CFG["delivery"])[0]
        self.assertEqual(sig2, "UNKNOWN")


# ------------------------------------------------------------------ audit: finestre mai sommate, Amazon
class AuditWindowsTests(SBase):
    def test_overlapping_windows_never_summed(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500), t28=(40, 4, 110, 200), st28=(40, 4, 110, 200))
        an, ds = self.analyze_and_decide()
        tot = {(t["report_type"], t["period_id"]): t for t in an.audit["totals"]}
        self.assertEqual(len(tot), 4)
        self.assertEqual(tot[("SEARCH_TERM", "2026-07-28__2026-09-30")]["spend"], 275.0)
        self.assertEqual(tot[("SEARCH_TERM", "2026-09-03__2026-09-30")]["spend"], 110.0)
        self.assertEqual(tot[("TARGETING", "LIFETIME__2026-09-30")]["spend"], 550.0)
        self.assertNotIn(385.0, [t["spend"] for t in an.audit["totals"]])          # 275 + 110 non compare mai
        self.assertNotIn(660.0, [t["spend"] for t in an.audit["totals"]])          # 550 + 110
        self.assertEqual(an.warnings, [])
        camp = win.campaign_aggregates([r for r in an.recs])
        self.assertEqual(len(camp), 4)
        self.assertEqual(camp[("C_EXACT", "SEARCH_TERM", "2026-09-03__2026-09-30")]["spend"], 110)

    def test_recs_carry_period_window_and_per_day(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500), t28=(40, 4, 110, 200), st28=(40, 4, 110, 200))
        an, _ = self.analyze_and_decide()
        r28 = [r for r in an.recs if r["period_id"] == "2026-09-03__2026-09-30" and r["report_type"] == "TARGETING"][0]
        self.assertEqual((r28["window_id"], r28["window_role"], r28["window_days"], r28["comparable_period"]), ("RECENT_28D", "TACTICAL", 28, 1))
        self.assertAlmostEqual(r28["spend_per_day"], 110 / 28)
        self.assertAlmostEqual(r28["clicks_per_day"], 40 / 28)
        life = [r for r in an.recs if r["report_scope"] == "LIFETIME"][0]
        self.assertEqual((life["window_id"], life["comparable_period"], life["spend_per_day"]), ("LIFETIME", 0, None))
        r65 = [r for r in an.recs if r["period_id"] == "2026-07-28__2026-09-30"][0]
        self.assertEqual((r65["window_id"], r65["window_days"]), ("RECENT_65D", 65))

    def test_window_containment_warning(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500), st28=(140, 4, 110, 200), t28=(140, 4, 110, 200))
        an, _ = self.analyze_and_decide()
        self.assertTrue([w for w in an.warnings if "clicks" in w and "RECENT" not in w and "2026-09-03__2026-09-30" in w])

    def test_amazon_manual_vs_imported_snapshot_totals(self):
        for spend, expect_warning in ((27.5, False), (100.0, True)):
            self.tearDown()
            self.setUp()
            self.snap_campaign("C_EXACT", "EXACT", [("t_life.csv", tr.it_rows_target([("kw", 400, 20, 550, 1000)]), "TARGETING", LIFE),
                                                     ("t28.csv", tr.it_rows_target([("kw", 40, 0, spend, 0)]), "TARGETING", D28)])
            self.run_import()
            amazon.record(self.conn, {"book_id": BOOK, "campaign_id": "C_EXACT", "period_from": D28[1], "period_to": D28[2],
                                      "source": "Amazon Campaign Overview Beta", "recommendation_type": "CAMPAIGN_OBSERVATION",
                                      "target": None, "observed_spend": 27.56, "observed_orders": 0, "suggested_direction": "NO_ACTION"},
                          self.settings)
            an, _ = self.analyze_and_decide()
            hit = [w for w in an.warnings if "osservazione Amazon" in w]
            self.assertEqual(bool(hit), expect_warning, (spend, an.warnings))

    def test_amazon_pending_until_same_period_report_imported(self):
        self.full(life=(400, 20, 550, 1000), st65=(100, 10, 275, 500))
        amazon.record(self.conn, {"book_id": BOOK, "campaign_id": "C_EXACT", "period_from": "2026-08-30", "period_to": "2026-09-26",
                                  "source": "Amazon", "recommendation_type": "CAMPAIGN_OBSERVATION", "target": None,
                                  "observed_spend": 27.56, "observed_orders": 0, "suggested_direction": "NO_ACTION"}, self.settings)
        an, ds = self.analyze_and_decide()
        self.assertEqual(len(an.audit["amazon_pending"]), 1)
        self.assertEqual([w for w in an.warnings if "Amazon" in w], [])
        self.assertTrue(any("INFORMATIVO" in n and "DELIVERY_COLLAPSED" in n for n in ds.amazon_campaign_notes))   # 0.98/g vs 4.23/g (65g)


# ------------------------------------------------------------------ decision layer: recency, delivery safety
class RecencyAndDeliveryTests(SBase):
    LIFE_BAD = (400, 20, 550, 1000)          # ACOS 55%, 20 ordini (high volume)
    ST65 = (100, 10, 275, 500)

    def test_no_snapshots_keeps_previous_behaviour(self):
        self.full(life=self.LIFE_BAD, st65=self.ST65)
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual((d["final_action"], d["decision_rule"], d["delivery_signal"], d["tactical_period"], d["comparable_period"]),
                         ("BID_DOWN_10_CANDIDATE", "C1_DOWN_CONFIRMED", "UNKNOWN", None, 0))
        self.assertEqual(d["recent_window"], "RECENT_65D")

    def test_acos_high_delivery_collapsed_gives_review_not_bid_down(self):
        self.full(life=self.LIFE_BAD, st65=self.ST65, t28=(3, 0, 3, 0), st28=(3, 0, 3, 0))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual(d["delivery_signal"], "DELIVERY_COLLAPSED")
        self.assertEqual((d["final_action"], d["bid_change_pct"]), ("REVIEW_DELIVERY_BEFORE_BID_CHANGE", None))
        self.assertEqual(d["final_consolidated_action"], "REVIEW_DELIVERY_BEFORE_BID_CHANGE")
        self.assertIn("REVIEW_DELIVERY", d["flags"])
        self.assertIn("candidato sottostante: BID_DOWN_10_CANDIDATE", d["reason"])
        self.assertEqual(d["priority"], "P1")                       # priorita' del candidato sottostante (impatto economico)
        self.assertEqual((d["comparable_period"], d["latest_comparable_snapshot"]), (1, "2026-09-03__2026-09-30"))
        self.assertAlmostEqual(d["delivery_ratio"], (150 / 28) / (5000 / 65), places=3)

    def test_same_acos_with_normal_delivery_still_bids_down(self):
        self.full(life=self.LIFE_BAD, st65=self.ST65, t28=(40, 4, 110, 200), st28=(40, 4, 110, 200))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual(d["delivery_signal"], "DELIVERY_STABLE")
        self.assertEqual((d["final_action"], d["bid_change_pct"]), ("BID_DOWN_10_CANDIDATE", -10))
        self.assertEqual((d["tactical_window"], d["tactical_source"]), ("RECENT_28D", "T"))
        self.assertAlmostEqual(d["tactical_spend_per_day"], 110 / 28)
        self.assertAlmostEqual(d["tactical_clicks_per_day"], 40 / 28)
        self.assertIsNone(d["lifetime_spend_per_day"])

    def test_delivery_declining_caps_step(self):
        # 28d: 30 clic (1500 impr -> 53.6/g) vs 65d: 5000 impr (76.9/g) = 0.70 => DECLINING
        self.full(life=self.LIFE_BAD, st65=self.ST65, t28=(30, 3, 90, 150), st28=(30, 3, 90, 150))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual(d["delivery_signal"], "DELIVERY_DECLINING")
        self.assertEqual((d["final_action"], d["bid_change_pct"]), ("BID_DOWN_5_CANDIDATE", -5))
        self.assertIn("DELIVERY_DECLINING_CAP", d["flags"])

    def test_lifetime_good_and_28d_bad_recent_priority(self):
        self.full(life=(300, 30, 330, 1000), st65=(100, 15, 150, 500), t28=(60, 8, 88, 130), st28=(50, 6, 70, 100))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual((d["lifetime_action"], d["latest_period_action"]), ("HOLD", "BID_DOWN_15_CANDIDATE"))
        self.assertEqual((d["final_action"], d["decision_rule"]), ("BID_DOWN_5_CANDIDATE", "C4_RECENT_DETERIORATION"))
        self.assertIn(d["confidence"], ("LOW", "MEDIUM"))
        self.assertIn("RECENT_DETERIORATION", d["flags"])
        self.assertIn("lifetime HOLD", d["historical_context"])
        self.assertEqual(d["delivery_signal"], "DELIVERY_STABLE")

    def test_lifetime_bad_and_28d_good_no_bid_down(self):
        self.full(life=self.LIFE_BAD, st65=(100, 10, 200, 500), t28=(80, 12, 60, 400), st28=(50, 8, 40, 260))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual(d["lifetime_action"], "BID_DOWN_10_CANDIDATE")
        self.assertEqual((d["final_action"], d["decision_rule"], d["bid_change_pct"]), ("HOLD", "C6_TACTICAL_RECOVERED", None))
        self.assertIn("TACTICAL_RECOVERED", d["flags"])
        self.assertIn("priorita'", d["reason"])
        self.assertEqual(json.loads(d["tactical_evidence"])["tactical"]["report_type"], "TARGETING")
        self.assertIsNone(d["growth_opportunity"])

    def test_tactical_evidence_prefers_latest_comparable_and_keeps_65d_as_context(self):
        self.full(life=self.LIFE_BAD, st65=self.ST65, t28=(40, 4, 110, 200), st28=(40, 4, 110, 200))
        _, ds = self.analyze_and_decide()
        d = self.dec(ds)
        self.assertEqual(d["recent_period_id"], "2026-07-28__2026-09-30")       # contesto 65 giorni
        self.assertEqual(d["tactical_period"], "2026-09-03__2026-09-30")
        self.assertEqual(d["recent_window"], "RECENT_65D")
        self.assertEqual(d["recent_clicks"], 100)
        self.assertEqual(d["tactical_clicks"], 40)
        self.assertEqual(ds.accounting["evidence_rows_total"], ds.accounting["analytic_recommendations"])

    def test_outputs_have_period_fields(self):
        from kdp_ads.decfields import DEC_COLUMNS
        for c in ("delivery_signal", "latest_period_action", "historical_context", "final_consolidated_action",
                  "latest_comparable_snapshot", "tactical_spend_per_day", "recent_orders_per_day", "lifetime_clicks_per_day"):
            self.assertIn(c, DEC_COLUMNS)


if __name__ == "__main__":
    unittest.main()
