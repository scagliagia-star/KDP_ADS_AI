"""Test stato del target (ABILITATO/IN PAUSA), HOLD_PAUSED_TARGET, delivery_source e sicurezza del fallback di campagna."""
import csv
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_rules as tr
from test_snapshots import BOOK, D28, D65, LIFE, SBase
from kdp_ads.decfields import DEC_COLUMNS
from kdp_ads.decision import delivery_source_of, map_target_status
from kdp_ads.decisions_report import decisions_markdown, save_decisions_to_db, write_decisions_csv

LIFE_BAD = (400, 20, 550, 1000)         # ACOS 55%, 20 ordini -> candidato BID_DOWN_10
ST65 = (100, 10, 275, 500)


class StatusBase(SBase):
    def set_status(self, stato, scope="LIFETIME"):
        for r in self.conn.execute("SELECT id, raw_json FROM target_rows WHERE report_scope=?", (scope,)).fetchall():
            raw = json.loads(r["raw_json"])
            raw["Stato"] = stato
            self.conn.execute("UPDATE target_rows SET raw_json=? WHERE id=?", (json.dumps(raw), r["id"]))
        self.conn.commit()


class MappingTests(unittest.TestCase):
    def test_explicit_mapping_only(self):
        self.assertEqual(map_target_status(json.dumps({"Stato": "ABILITATO"})), "ENABLED")
        self.assertEqual(map_target_status(json.dumps({"Stato": "IN PAUSA"})), "PAUSED")
        for raw in (json.dumps({"Stato": "Attivo"}), json.dumps({"Stato": "ARCHIVIATO"}), json.dumps({"Stato__2": "In pausa"}),
                    json.dumps({"Stato": ""}), "{}", None, "not json"):
            self.assertEqual(map_target_status(raw), "UNKNOWN", raw)       # nessun valore dedotto

    def test_delivery_source_values(self):
        self.assertEqual(delivery_source_of("ENTITY:SEARCH_TERM"), "TARGET")
        self.assertEqual(delivery_source_of("CAMPAIGN:TARGETING"), "CAMPAIGN_FALLBACK")
        self.assertEqual(delivery_source_of(None), "UNKNOWN")


class PausedTargetTests(StatusBase):
    def test_enabled_and_paused_status_read_from_lifetime_target(self):
        self.full(life=LIFE_BAD, st65=ST65)
        self.assertEqual(self.dec(self.analyze_and_decide()[1])["target_status"], "ENABLED")
        self.set_status("IN PAUSA")
        self.assertEqual(self.dec(self.analyze_and_decide()[1])["target_status"], "PAUSED")

    def test_no_lifetime_target_row_is_unknown(self):
        self.snap_campaign("C_EXACT", "EXACT", [("t28.csv", tr.it_rows_target([("kw", 40, 4, 110, 200)]), "TARGETING", D28)])
        self.run_import()
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(d["target_status"], "UNKNOWN")

    def test_paused_bid_down_becomes_hold_paused_target_keeping_underlying(self):
        self.full(life=LIFE_BAD, st65=ST65)
        before = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(before["final_action"], "BID_DOWN_10_CANDIDATE")
        self.assertEqual((before["target_status"], before["underlying_candidate_action"], before["operationally_actionable"]), ("ENABLED", None, 1))
        self.set_status("IN PAUSA")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual((d["final_action"], d["final_consolidated_action"], d["bid_change_pct"]), ("HOLD_PAUSED_TARGET", "HOLD_PAUSED_TARGET", None))
        self.assertEqual((d["target_status"], d["underlying_candidate_action"], d["operationally_actionable"]), ("PAUSED", "BID_DOWN_10_CANDIDATE", 0))
        self.assertIn("Target currently paused. Underlying economics would suggest BID_DOWN_10_CANDIDATE if reactivated.", d["reason"])
        # analisi economica conservata e priorita' del candidato sottostante invariata
        self.assertEqual((d["lifetime_action"], d["lifetime_orders"], d["priority"]), (before["lifetime_action"], before["lifetime_orders"], before["priority"]))

    def test_paused_review_becomes_hold_paused_with_review_underlying(self):
        self.full(life=LIFE_BAD, st65=ST65, t28=(3, 0, 3, 0), st28=(3, 0, 3, 0))
        self.set_status("IN PAUSA")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(d["delivery_signal"], "DELIVERY_COLLAPSED")
        self.assertEqual((d["final_action"], d["underlying_candidate_action"], d["operationally_actionable"]),
                         ("HOLD_PAUSED_TARGET", "BID_DOWN_10_CANDIDATE", 0))

    def test_paused_hold_stays_hold_not_actionable(self):
        self.full(life=(300, 30, 330, 1000))
        self.set_status("IN PAUSA")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual((d["final_action"], d["underlying_candidate_action"], d["operationally_actionable"]), ("HOLD", None, 0))

    def test_recent_spend_on_paused_target_flag(self):
        self.full(life=LIFE_BAD, st65=ST65)
        self.set_status("IN PAUSA")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertGreater(d["recent_spend"], 0)
        self.assertIn("RECENT_SPEND_ON_PAUSED_TARGET", d["flags"])
        self.assertIn("la causa non e' dedotta", d["reason"])

    def test_no_recent_spend_flag_when_no_recent_snapshot_or_enabled(self):
        self.full(life=LIFE_BAD)
        self.set_status("IN PAUSA")
        self.assertNotIn("RECENT_SPEND_ON_PAUSED_TARGET", self.dec(self.analyze_and_decide()[1])["flags"])
        self.tearDown()
        self.setUp()
        self.full(life=LIFE_BAD, st65=ST65)
        self.assertNotIn("RECENT_SPEND_ON_PAUSED_TARGET", self.dec(self.analyze_and_decide()[1])["flags"])


class DeliverySourceTests(StatusBase):
    def test_entity_delivery_is_target_and_active_review_is_kept(self):
        self.full(life=LIFE_BAD, st65=ST65, t28=(3, 0, 3, 0), st28=(3, 0, 3, 0))
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual((d["target_status"], d["delivery_signal"], d["delivery_source"]), ("ENABLED", "DELIVERY_COLLAPSED", "TARGET"))
        self.assertEqual((d["final_action"], d["operationally_actionable"]), ("REVIEW_DELIVERY_BEFORE_BID_CHANGE", 1))
        self.assertNotIn("inherited from campaign", d["reason"])

    def collapsed_campaign(self, extra_life=LIFE_BAD):
        """'kw' ha solo il lifetime; il collasso e' dei soli dati di 'kwB' (quindi della campagna)."""
        specs = [("t_life.csv", tr.it_rows_target([("kw",) + extra_life, ("kwB", 100, 10, 100, 500)]), "TARGETING", LIFE),
                 ("s65.csv", tr.it_rows_st([("kwB", "kwB", 100, 10, 100, 500)]), "SEARCH_TERM", D65),
                 ("s28.csv", tr.it_rows_st([("kwB", "kwB", 3, 0, 3, 0)]), "SEARCH_TERM", D28)]
        self.snap_campaign("C_EXACT", "EXACT", specs)
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [(r.filename, r.problems) for r in res])
        return self.analyze_and_decide()[1]

    def test_campaign_fallback_is_labelled_and_never_forces_review(self):
        ds = self.collapsed_campaign()
        d = self.dec(ds)
        self.assertEqual((d["delivery_signal"], d["delivery_source"], d["campaign_delivery_signal"]), ("DELIVERY_COLLAPSED", "CAMPAIGN_FALLBACK", "DELIVERY_COLLAPSED"))
        self.assertNotEqual(d["final_action"], "REVIEW_DELIVERY_BEFORE_BID_CHANGE")
        self.assertNotIn("REVIEW_DELIVERY", d["flags"])
        self.assertIn("DELIVERY_INHERITED_COLLAPSED_NOT_ENFORCED", d["flags"])
        self.assertEqual((d["final_action"], d["bid_change_pct"], d["operationally_actionable"]), ("BID_DOWN_5_CANDIDATE", -5, 1))   # solo cap prudente
        self.assertIn("delivery signal inherited from campaign", d["reason"])
        self.assertIn("inherited from campaign", decisions_markdown(ds))
        # l'entita' con dati propri (kwB) resta TARGET
        self.assertEqual(self.dec(ds, target="kwB")["delivery_source"], "TARGET")

    def test_fallback_does_not_raise_confidence(self):
        d_fb = self.dec(self.collapsed_campaign())
        self.tearDown()
        self.setUp()
        self.snap_campaign("C_EXACT", "EXACT", [("t_life.csv", tr.it_rows_target([("kw",) + LIFE_BAD, ("kwB", 100, 10, 100, 500)]), "TARGETING", LIFE)])
        self.run_import()
        d_none = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(d_none["delivery_source"], "UNKNOWN")
        self.assertEqual((d_fb["confidence"], d_fb["priority"]), (d_none["confidence"], d_none["priority"]))

    def test_unknown_delivery_source_without_snapshots(self):
        self.full(life=LIFE_BAD)
        self.assertEqual(self.dec(self.analyze_and_decide()[1])["delivery_source"], "UNKNOWN")


class UnknownStatusTests(StatusBase):
    MSG = "Target status unknown. Verify current Amazon Ads status before applying any bid action."

    def test_unknown_bid_down_becomes_review_target_status_keeping_underlying(self):
        self.full(life=LIFE_BAD, st65=ST65)
        self.set_status("SCONOSCIUTO")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(d["target_status"], "UNKNOWN")
        self.assertEqual((d["final_action"], d["final_consolidated_action"], d["bid_change_pct"]), ("REVIEW_TARGET_STATUS", "REVIEW_TARGET_STATUS", None))
        self.assertEqual(d["underlying_candidate_action"], "BID_DOWN_10_CANDIDATE")
        self.assertEqual(d["operationally_actionable"], 0)
        self.assertIn(self.MSG, d["reason"])
        self.assertIn("TARGET_STATUS_UNKNOWN", d["flags"])
        self.assertEqual((d["lifetime_action"], d["lifetime_orders"]), ("BID_DOWN_10_CANDIDATE", 20))      # economia invariata

    def test_unknown_delivery_review_becomes_review_target_status(self):
        self.full(life=LIFE_BAD, st65=ST65, t28=(3, 0, 3, 0), st28=(3, 0, 3, 0))
        self.set_status("SCONOSCIUTO")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual(d["delivery_signal"], "DELIVERY_COLLAPSED")
        self.assertEqual((d["final_action"], d["underlying_candidate_action"], d["operationally_actionable"]),
                         ("REVIEW_TARGET_STATUS", "BID_DOWN_10_CANDIDATE", 0))
        self.assertIn(self.MSG, d["reason"])

    def test_unknown_hold_unchanged_but_never_actionable(self):
        self.full(life=(300, 30, 330, 1000))
        self.set_status("SCONOSCIUTO")
        d = self.dec(self.analyze_and_decide()[1])
        self.assertEqual((d["target_status"], d["final_action"], d["underlying_candidate_action"], d["operationally_actionable"]),
                         ("UNKNOWN", "HOLD", None, 0))

    def test_unknown_is_never_operationally_actionable(self):
        for life, extra in ((LIFE_BAD, {"st65": ST65}), ((300, 30, 330, 1000), {}), (LIFE_BAD, {"st65": ST65, "t28": (3, 0, 3, 0), "st28": (3, 0, 3, 0)})):
            self.tearDown()
            self.setUp()
            self.full(life=life, **extra)
            self.set_status("SCONOSCIUTO")
            ds = self.analyze_and_decide()[1]
            self.assertTrue(ds.decisions)
            self.assertEqual({d["operationally_actionable"] for d in ds.decisions if d["target_status"] == "UNKNOWN"}, {0})


class ActionableAndOutputTests(StatusBase):
    def test_operationally_actionable_matrix(self):
        self.full(life=(300, 30, 330, 1000))                                   # ENABLED + HOLD
        self.assertEqual(self.dec(self.analyze_and_decide()[1])["operationally_actionable"], 0)
        self.tearDown()
        self.setUp()
        self.full(life=LIFE_BAD, st65=ST65)                                    # ENABLED + BID_DOWN
        self.assertEqual(self.dec(self.analyze_and_decide()[1])["operationally_actionable"], 1)

    def test_new_fields_in_csv_db_and_markdown(self):
        self.full(life=LIFE_BAD, st65=ST65)
        self.set_status("IN PAUSA")
        ds = self.analyze_and_decide()[1]
        for c in ("target_status", "underlying_candidate_action", "delivery_source", "operationally_actionable"):
            self.assertIn(c, DEC_COLUMNS)
        p = write_decisions_csv(ds, Path(self.tmp.name) / "out", "20260930_120000")
        with open(p, encoding="utf-8-sig", newline="") as fh:
            row = list(csv.DictReader(fh))[0]
        self.assertEqual((row["target_status"], row["final_action"], row["underlying_candidate_action"], row["operationally_actionable"]),
                         ("PAUSED", "HOLD_PAUSED_TARGET", "BID_DOWN_10_CANDIDATE", "false"))
        run = save_decisions_to_db(self.conn, ds, p, "m.md")
        r = self.conn.execute("SELECT target_status, underlying_candidate_action, operationally_actionable, delivery_source FROM decisions WHERE run_id=?", (run,)).fetchone()
        self.assertEqual(tuple(r), ("PAUSED", "BID_DOWN_10_CANDIDATE", 0, "UNKNOWN"))
        md = decisions_markdown(ds)
        self.assertIn("PAUSED TARGETS WITH UNDERLYING ACTION (1 di 1", md)
        self.assertIn("BID_DOWN_10_CANDIDATE", md.split("PAUSED TARGETS WITH UNDERLYING ACTION")[1])
        self.assertIn("Stato target", md)


if __name__ == "__main__":
    unittest.main()
