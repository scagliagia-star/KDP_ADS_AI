"""Test del Decision Layer (consolidamento, Amazon, status). Dati sintetici in workspace temporanei."""
import csv
import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_importer as ti
import test_rules as tr
from kdp_ads import amazon
from kdp_ads.analysis import analyze_book
from kdp_ads.decfields import DEC_COLUMNS, STATUSES
from kdp_ads.decision import action_key, build_decisions
from kdp_ads.decisions_report import save_decisions_to_db, write_combined_markdown, write_decisions_csv
from kdp_ads.report import save_to_db, write_markdown

BOOK = "1805566563"
RULES_PATH = tr.RULES_PATH


class DBase(ti.Base):
    def setUp(self):
        super().setUp()
        self.add_book()

    def prep(self, campaigns):
        """campaigns: {campaign_id: (type, [target rows], [search term rows])} -> importa, analizza, salva le recs."""
        for cid, (ctype, t, s) in campaigns.items():
            files = {"t.csv": tr.it_rows_target(t)}
            if s:
                files["s.csv"] = tr.it_rows_st(s)
            self.campaign(BOOK, cid, ctype, files)
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [r.problems for r in res])
        an = analyze_book(self.conn, self.settings, self.mapping, RULES_PATH, BOOK, self.logger)
        self.assertEqual(an.warnings, [], an.warnings)
        self.run_id = save_to_db(self.conn, an, "a.csv", "a.md", "o.csv", an.ctx.rules)
        return an

    def decisions(self):
        return build_decisions(self.conn, self.settings, RULES_PATH, BOOK, self.run_id)

    @staticmethod
    def by_target(ds, target, campaign=None):
        m = [d for d in ds.decisions if d["target"] == target and (campaign is None or d["campaign_id"] == campaign)
             and not d["action_key"].count("negterm")]
        assert len(m) == 1, [d["action_key"] for d in ds.decisions]
        return m[0]

    def one_campaign(self, target_rows, st_rows=None, cid="C_EXACT"):
        self.prep({cid: ("EXACT", target_rows, st_rows or [])})
        return self.decisions()


class ConsolidationTests(DBase):
    def test_target_and_search_term_same_entity_one_decision(self):
        ds = self.one_campaign([("kw one", 100, 10, 100, 400)],
                               [("kw one", "kw one", 20, 6, 20, 240), ("other term", "kw one", 15, 2, 20, 60)])
        self.assertEqual(ds.accounting["analytic_recommendations"], 3)
        self.assertEqual(len(ds.decisions), 1)                       # niente seconda azione per il figlio
        d = ds.decisions[0]
        self.assertEqual(d["action_key"], f"{BOOK}|C_EXACT|kw:kw one")
        self.assertTrue(d["lifetime_evidence"] and d["recent_evidence"])
        self.assertEqual(json.loads(d["lifetime_evidence"])["orders"], 10)     # valori originali non persi
        self.assertEqual(json.loads(d["recent_evidence"])["orders"], 6)
        self.assertIn("search term figli", d["child_terms_note"])
        self.assertEqual(ds.accounting["evidence_rows_total"], 3)

    def test_two_bid_down_10_do_not_become_minus_20(self):
        ds = self.one_campaign([("kw one", 200, 20, 550, 1000)], [("kw one", "kw one", 60, 8, 88, 160)])
        d = ds.decisions[0]
        self.assertEqual((d["lifetime_action"], d["recent_action"]), ("BID_DOWN_10_CANDIDATE", "BID_DOWN_10_CANDIDATE"))
        self.assertEqual((d["final_action"], d["bid_change_pct"], d["decision_rule"]), ("BID_DOWN_10_CANDIDATE", -10, "C1_DOWN_CONFIRMED"))
        self.assertEqual((d["confidence"], d["priority"]), ("HIGH", "P1"))

    def test_lifetime_hold_recent_deteriorating(self):
        ds = self.one_campaign([("kw one", 300, 30, 330, 1000)], [("kw one", "kw one", 60, 8, 88, 130)])
        d = ds.decisions[0]
        self.assertEqual((d["lifetime_action"], d["recent_action"]), ("HOLD", "BID_DOWN_15_CANDIDATE"))
        self.assertEqual((d["final_action"], d["bid_change_pct"], d["decision_rule"]), ("BID_DOWN_5_CANDIDATE", -5, "C4_RECENT_DETERIORATION"))
        self.assertIn(d["confidence"], ("LOW", "MEDIUM"))              # le evidenze discordano: mai HIGH
        self.assertIn("RECENT_DETERIORATION", d["flags"])
        self.assertIn("DETERIORAMENTO RECENTE", d["reason"])

    def test_deterioration_step10_only_with_high_conf_and_declining(self):
        ds = self.one_campaign([("kw one", 600, 60, 660, 2000)], [("kw one", "kw one", 120, 12, 156, 240)])
        d = ds.decisions[0]
        self.assertEqual((d["recent_vs_historical_signal"], d["decision_rule"]), ("DECLINING_SIGNAL", "C4_RECENT_DETERIORATION"))
        self.assertEqual((d["final_action"], d["confidence"]), ("BID_DOWN_10_CANDIDATE", "MEDIUM"))

    def test_lifetime_down_recent_improving_softened(self):
        ds = self.one_campaign([("kw one", 400, 20, 550, 1000)], [("kw one", "kw one", 30, 6, 30, 300)])
        d = ds.decisions[0]
        self.assertEqual(d["decision_rule"], "C2B_LIFETIME_DOWN_RECOVERING")
        self.assertEqual((d["final_action"], d["confidence"]), ("BID_DOWN_5_CANDIDATE", "MEDIUM"))

    def test_high_volume_never_pause(self):
        # lifetime 20 ordini ACOS 70% + recente a 0 ordini con spesa alta (=> pausa/negative sul target)
        ds = self.one_campaign([("kw one", 400, 20, 700, 1000)], [("kw one", "kw one", 40, 0, 40, 0)])
        d = ds.decisions[0]
        self.assertEqual(d["recent_action"], "PAUSE_CANDIDATE")       # negative su termine == target
        self.assertEqual((d["final_action"], d["bid_change_pct"]), ("BID_DOWN_15_CANDIDATE", -15))
        self.assertIn("HIGH_VOLUME_CAP", d["flags"])
        self.assertNotEqual(d["final_action"], "PAUSE_CANDIDATE")

    def test_zero_order_pause_candidate_allowed_below_high_volume(self):
        d = self.one_campaign([("kw one", 30, 0, 40, 0)]).decisions[0]
        self.assertEqual((d["final_action"], d["bid_change_pct"]), ("PAUSE_CANDIDATE", None))

    def test_low_sample_large_loss_bid_down_low_confidence_no_pause(self):
        d = self.one_campaign([("kw one", 30, 2, 60, 40)]).decisions[0]      # 2 ordini, perdita ~$43 (5x royalty)
        self.assertEqual(d["lifetime_action"], "BID_DOWN_10_CANDIDATE")       # rivalutato dal decision layer
        self.assertEqual((d["final_action"], d["confidence"], d["bid_change_pct"]), ("BID_DOWN_10_CANDIDATE", "LOW", -10))
        self.assertIn("LOW_SAMPLE_LOSS", d["flags"])
        self.assertEqual(d["priority"], "P3")                                 # visibile, non nascosta
        self.assertNotEqual(d["final_action"], "PAUSE_CANDIDATE")
        # la raccomandazione analitica originale resta WATCH
        self.assertEqual(self.conn.execute("SELECT recommended_action FROM recommendations").fetchone()[0], "WATCH")

    def test_low_sample_small_loss_stays_watch(self):
        d = self.one_campaign([("kw one", 10, 1, 12, 20)]).decisions[0]      # perdita $3.45 < 2x royalty
        self.assertEqual(d["final_action"], "WATCH")

    def test_winner_hold_plus_growth_opportunity(self):
        d = self.one_campaign([("kw one", 40, 8, 40, 200)]).decisions[0]
        self.assertEqual(self.conn.execute("SELECT recommended_action FROM recommendations").fetchone()[0], "BID_UP_5_CANDIDATE")
        self.assertEqual((d["final_action"], d["growth_opportunity"], d["bid_change_pct"]), ("HOLD", "BID_UP_5_OPPORTUNITY", None))
        self.assertNotIn("BID_UP", d["final_action"])

    def test_no_growth_when_recent_deteriorates(self):
        d = self.one_campaign([("kw one", 40, 8, 40, 200)], [("kw one", "kw one", 20, 0, 25, 0)]).decisions[0]
        self.assertIsNone(d["growth_opportunity"])
        self.assertTrue(d["final_action"].startswith("BID_DOWN"))

    def test_negative_candidate_for_search_term_is_its_own_decision(self):
        ds = self.one_campaign([("kw one", 100, 10, 100, 400)], [("junk term", "kw one", 20, 0, 30, 0)])
        self.assertEqual(len(ds.decisions), 2)
        neg = [d for d in ds.decisions if "negterm" in d["action_key"]][0]
        self.assertEqual((neg["final_action"], neg["search_term"]), ("NEGATIVE_CANDIDATE", "junk term"))
        self.assertIsNone(neg["bid_change_pct"])
        parent = self.by_target(ds, "kw one")
        self.assertNotEqual(parent["final_action"], "NEGATIVE_CANDIDATE")

    def test_product_target_action_key_and_merge(self):
        hdr_t = "Stato,Categorie e prodotti,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS"
        row_t = 'ABILITATO,"asin=""B0FTFJL6RB""",—,Attivo,0.90,4300,86,"0,0200",61.51,0.72,12,227.88,"0,2699"'
        hdr_s = "Aggiunto come,Prodotto corrispondente,Target per prodotto,Offerta per il targeting (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS"
        row_s = 'Targeting per prodotto,b0ftfjl6rb,"asin=""B0FTFJL6RB""",0.90,320,16,"0,0500",12.93,0.81,5,94.95,"0,1362"'
        self.campaign(BOOK, "C_COMP", "COMPETITOR", {"t.csv": hdr_t + "\n" + row_t + "\n", "s.csv": hdr_s + "\n" + row_s + "\n"})
        self.assertTrue(all(r.status == "IMPORTED" for r in self.run_import()))
        an = analyze_book(self.conn, self.settings, self.mapping, RULES_PATH, BOOK, self.logger)
        self.run_id = save_to_db(self.conn, an, "a", "b", "c", an.ctx.rules)
        ds = self.decisions()
        self.assertEqual(len(ds.decisions), 1)
        d = ds.decisions[0]
        self.assertEqual((d["action_key"], d["target_type"]), (f"{BOOK}|C_COMP|asin:B0FTFJL6RB", "PRODUCT_TARGET"))
        self.assertTrue(d["recent_evidence"])
        self.assertEqual(d["current_bid"], 0.9)

    def test_action_key_format(self):
        self.assertEqual(action_key("B", "C", "  Chair  Tai Chi "), "B|C|kw:chair tai chi")
        self.assertEqual(action_key("B", "C", 'asin="b0ftfjl6rb"'), "B|C|asin:B0FTFJL6RB")

    def test_cross_campaign_confirms_but_does_not_add_step(self):
        ds = None
        self.prep({
            "C_EXACT": ("EXACT", [("kw", 200, 20, 550, 1000)], [("kw", "kw", 100, 10, 275, 500)]),
            "C_PHRASE": ("PHRASE", [("kw phrase", 150, 30, 100, 600)], [("kw", "kw phrase", 100, 20, 60, 400)]),
        })
        ds = self.decisions()
        d = self.by_target(ds, "kw", "C_EXACT")
        self.assertEqual(d["better_campaign_if_any"], "C_PHRASE")
        self.assertIn("POSSIBILE INEFFICIENZA", d["cross_campaign_overlap"])
        self.assertEqual((d["final_action"], d["bid_change_pct"], d["confidence"]), ("BID_DOWN_10_CANDIDATE", -10, "HIGH"))
        self.assertIn("non additivo", d["reason"])
        self.assertEqual(json.loads(d["cross_campaign_evidence"])[0]["classification"], "BETTER_IN_PHRASE")
        p = self.by_target(ds, "kw phrase", "C_PHRASE")
        self.assertNotIn("BID_DOWN", p["final_action"])          # la campagna migliore non viene ridotta


class AmazonTests(DBase):
    REC = {"book_id": BOOK, "campaign_id": "C_EXACT", "period_from": "2026-08-30", "period_to": "2026-09-26",
           "source": "Amazon Campaign Overview Beta", "recommendation_type": "REDUCE_BID_LOW_CONVERSION_KEYWORD",
           "target": "kw one", "observed_spend": 2.04, "observed_orders": 0, "observation": "0 orders",
           "suggested_action": "Reduce bids on low-conversion keywords", "suggested_direction": "REDUCE_BID"}

    def setUp(self):
        super().setUp()
        self.campaign(BOOK, "C_EXACT", "EXACT", {})

    def test_storage_and_validation(self):
        rid = amazon.record(self.conn, dict(self.REC), self.settings)
        row = self.conn.execute("SELECT * FROM amazon_recommendations WHERE id=?", (rid,)).fetchone()
        self.assertEqual((row["target_normalized"], row["suggested_direction"], row["observed_spend"]), ("kw one", "REDUCE_BID", 2.04))
        self.assertIsNone(row["suggested_pct"])                                 # nessuna percentuale inventata
        with self.assertRaises(amazon.AmazonRecError):
            amazon.record(self.conn, dict(self.REC), self.settings)              # duplicato
        for bad in ({"book_id": "NOPE"}, {"campaign_id": "NOPE"}, {"period_from": "2026-13-01"},
                    {"period_from": "2026-10-01"}, {"suggested_direction": "MAKE_MONEY"}):
            with self.assertRaises(amazon.AmazonRecError, msg=bad):
                amazon.record(self.conn, {**self.REC, "target": "other", **bad}, self.settings)

    def test_campaign_level_record_without_target(self):
        rid = amazon.record(self.conn, {**self.REC, "target": None, "recommendation_type": "CAMPAIGN_OBSERVATION",
                                        "suggested_direction": "NO_ACTION"}, self.settings)
        self.assertEqual(self.conn.execute("SELECT target_normalized FROM amazon_recommendations WHERE id=?", (rid,)).fetchone()[0], "")

    def test_matching_and_agreement(self):
        amazon.record(self.conn, dict(self.REC), self.settings)                                       # keyword presente
        amazon.record(self.conn, {**self.REC, "target": "kw two", "recommendation_type": "X"}, self.settings)   # keyword presente, HOLD
        amazon.record(self.conn, {**self.REC, "target": "not a target"}, self.settings)              # non abbinabile
        amazon.record(self.conn, {**self.REC, "target": None, "recommendation_type": "CAMPAIGN_OBSERVATION",
                                  "suggested_direction": "NO_ACTION"}, self.settings)                  # livello campagna
        ds = self.one_campaign([("kw one", 200, 20, 550, 1000), ("kw two", 100, 10, 30, 400)])
        d1, d2 = self.by_target(ds, "kw one"), self.by_target(ds, "kw two")
        self.assertEqual(d1["final_action"], "BID_DOWN_10_CANDIDATE")
        self.assertEqual(d1["recommendation_agreement"], "AGREE")
        self.assertIn("REDUCE_BID", d1["amazon_recommendation"])
        self.assertIn("evidenza Amazon debole", d1["amazon_agreement_note"])
        self.assertIn("periodi diversi", d1["amazon_agreement_note"])
        self.assertEqual(json.loads(d1["amazon_evidence"])[0]["suggested_pct"], None)
        self.assertEqual((d2["final_action"], d2["recommendation_agreement"]), ("HOLD", "DISAGREE"))
        self.assertEqual([a["target"] for a in ds.amazon_unmatched], ["not a target"])
        self.assertEqual(len(ds.amazon_campaign_level), 1)
        self.assertNotIn("Amazon", "".join(d["final_action"] for d in ds.decisions))

    def test_amazon_never_changes_the_decision(self):
        base = self.one_campaign([("kw one", 40, 8, 40, 200)]).decisions[0]["final_action"]
        amazon.record(self.conn, dict(self.REC), self.settings)
        self.assertEqual(self.decisions().decisions[0]["final_action"], base)     # Amazon dice riduci, noi HOLD
        self.assertEqual(self.decisions().decisions[0]["recommendation_agreement"], "DISAGREE")

    def test_agreement_table(self):
        A = amazon.agreement
        self.assertEqual([A("BID_DOWN_5_CANDIDATE", "REDUCE_BID"), A("PAUSE_CANDIDATE", "REDUCE_BID"), A("HOLD", "REDUCE_BID"),
                          A("WATCH", "REDUCE_BID"), A("HOLD", "REDUCE_PLACEMENT_MODIFIER"), A("HOLD", "NO_ACTION"),
                          A("BID_DOWN_10_CANDIDATE", "INCREASE_BID")],
                         ["AGREE", "AGREE", "DISAGREE", "PARTIAL", "NOT_COMPARABLE", "NOT_COMPARABLE", "DISAGREE"])


class StatusAndOutputTests(DBase):
    def test_status_proposed_and_manual_approval_always_true(self):
        ds = self.one_campaign([("kw one", 200, 20, 550, 1000), ("kw two", 40, 8, 40, 200), ("kw three", 3, 0, 1, 0)])
        self.assertEqual(len(ds.decisions), 3)
        for d in ds.decisions:
            self.assertEqual((d["status"], d["manual_approval_required"]), ("PROPOSED", 1))
        out = Path(self.tmp.name) / "o"
        p_csv = write_decisions_csv(ds, out, "20260930_120000")
        with open(p_csv, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(list(rows[0].keys()), DEC_COLUMNS)
        self.assertEqual({r["manual_approval_required"] for r in rows}, {"true"})
        self.assertEqual({r["status"] for r in rows}, {"PROPOSED"})
        for c in ("decision_id", "action_key", "current_bid", "final_action", "bid_change_pct", "lifetime_acos",
                  "recent_estimated_profit", "cross_campaign_overlap", "better_campaign_if_any", "growth_opportunity",
                  "amazon_recommendation", "recommendation_agreement", "reason"):
            self.assertIn(c, rows[0])
        self.assertEqual(rows[0]["priority"], "P1")
        run = save_decisions_to_db(self.conn, ds, p_csv, "m.md")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (run,)).fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(DISTINCT decision_id) FROM decisions").fetchone()[0], 3)

    def test_db_rejects_invalid_status_and_manual_false(self):
        self.assertEqual(STATUSES, ("PROPOSED", "APPROVED", "REJECTED", "EXECUTED", "REVIEW_LATER"))
        for sql in ("INSERT INTO decisions (decision_id, manual_approval_required) VALUES ('x', 0)",
                    "INSERT INTO decisions (decision_id, status) VALUES ('y', 'DONE')"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)
        self.conn.execute("INSERT INTO decisions (decision_id) VALUES ('z')")
        self.assertEqual(tuple(self.conn.execute("SELECT status, manual_approval_required FROM decisions").fetchone()), ("PROPOSED", 1))
        for st in STATUSES:                                                  # gli stati futuri sono supportati
            self.conn.execute("UPDATE decisions SET status=? WHERE decision_id='z'", (st,))

    def test_analytic_recommendations_untouched(self):
        self.prep({"C_EXACT": ("EXACT", [("kw one", 200, 20, 550, 1000), ("kw two", 40, 8, 40, 200)], [("kw one", "kw one", 60, 8, 88, 160)])})

        def snap():
            rows = self.conn.execute("SELECT * FROM recommendations ORDER BY rec_id").fetchall()
            return hashlib.sha256(json.dumps([tuple(r) for r in rows], default=str).encode()).hexdigest(), len(rows)
        before = snap()
        ds = self.decisions()
        save_decisions_to_db(self.conn, ds, "x.csv", "x.md")
        self.assertEqual(snap(), before)
        self.assertEqual(before[1], 3)

    def test_markdown_section_priority_order_and_original_untouched(self):
        an = self.prep({"C_EXACT": ("EXACT", [("kw one", 200, 20, 550, 1000), ("kw two", 40, 8, 40, 200), ("kw three", 3, 0, 1, 0)],
                                    [("kw one", "kw one", 60, 8, 88, 160)])})
        out = Path(self.tmp.name) / "rep"
        orig = write_markdown(an, out, "20260930_120000")
        orig_text = orig.read_text(encoding="utf-8")
        ds = self.decisions()
        new = write_combined_markdown(ds, out, "20260930_120001", str(orig))
        self.assertEqual(orig.read_text(encoding="utf-8"), orig_text)          # report analitico invariato
        md = new.read_text(encoding="utf-8")
        self.assertIn("CONSOLIDATED DECISIONS", md)
        self.assertLess(md.index("CONSOLIDATED DECISIONS"), md.index("## 2. WINNERS"))
        self.assertLess(md.index("### P1"), md.index("### P2"))
        self.assertLess(md.index("### P2"), md.index("### P3"))
        self.assertLess(md.index("### P3"), md.index("### P4"))
        for word in ("Lifetime", "Recent", "Cross campaign", "Amazon AI", "Final recommendation", "Reason", "PROPOSED"):
            self.assertIn(word, md)


if __name__ == "__main__":
    unittest.main()
