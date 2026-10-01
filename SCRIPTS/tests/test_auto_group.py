"""Gruppi di targeting AUTOMATICO (loose-match, close-match, complements, substitutes): tipo dedicato AUTO_TARGETING_GROUP,
mai keyword; economia/bid invariati; fuori dagli overlap keyword e dalle negative sul nome del target; etichetta in dashboard."""
import os
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_rules as tr
from test_snapshots import D65, LIFE, SBase
from kdp_ads.decisions_report import save_decisions_to_db
from kdp_ads.metrics import AUTO_TARGET_GROUPS, AUTO_TARGETING_GROUP, target_type, term_matches_target

GROUPS = ["loose-match", "close-match", "complements", "substitutes"]


class ClassificationTests(unittest.TestCase):
    def test_each_auto_group_is_auto_targeting_group_not_keyword(self):
        self.assertEqual(AUTO_TARGET_GROUPS, set(GROUPS))
        for g in GROUPS:
            for ctype in ("OTHER", "AUTO", "other"):
                t = target_type(g, ctype)
                self.assertEqual(t, "AUTO_TARGETING_GROUP", (g, ctype))
                self.assertNotEqual(t, "KEYWORD_TARGET")
        self.assertEqual(target_type("  Loose-Match ", "OTHER"), "AUTO_TARGETING_GROUP")      # normalizzato come gli altri target

    def test_manual_campaigns_and_other_targets_unchanged(self):
        for ctype in ("EXACT", "PHRASE", "COMPETITOR", None):
            for g in GROUPS:                                  # stessa stringa in una campagna manuale: resta una keyword
                self.assertEqual(target_type(g, ctype), "KEYWORD_TARGET", (g, ctype))
        self.assertEqual(target_type("chair tai chi", "OTHER"), "KEYWORD_TARGET")           # keyword vera in campagna Auto/Other
        self.assertEqual(target_type('asin="B0FTFJL6RB"', "OTHER"), "PRODUCT_TARGET")        # ASIN: tipo gia' esistente
        self.assertEqual(target_type('asin="B0FTFJL6RB"', "COMPETITOR"), "PRODUCT_TARGET")
        self.assertEqual(target_type("category=\"x\""), "PRODUCT_TARGET")
        self.assertEqual(target_type("chair tai chi"), "KEYWORD_TARGET")                      # firma storica a un solo argomento

    def test_search_term_never_equals_an_auto_group(self):
        self.assertFalse(term_matches_target("loose-match", "loose-match", "OTHER"))
        self.assertTrue(term_matches_target("loose-match", "loose-match", "EXACT"))           # keyword manuale: invariato
        self.assertTrue(term_matches_target("chair tai chi", "chair tai chi", "OTHER"))


class AutoBuild(SBase):
    AUTO_LIFE = [("loose-match", 300, 20, 330, 600), ("close-match", 80, 0, 100, 0), ("complements", 100, 15, 90, 600),
                 ("substitutes", 50, 5, 40, 150)]

    def build(self, extra_manual=False):
        specs = [("t_life.csv", tr.it_rows_target(self.AUTO_LIFE), "TARGETING", LIFE),
                 ("s65.csv", tr.it_rows_st([("tai chi for seniors", "loose-match", 60, 0, 80, 0), ("chair tai chi", "close-match", 40, 4, 50, 150)]),
                  "SEARCH_TERM", D65)]
        self.snap_campaign("C_AUTO", "OTHER", specs)
        if extra_manual:                                    # stessi termini reali in due campagne manuali + nella Auto
            for cid, ctype in (("C_EXACT", "EXACT"), ("C_PHRASE", "PHRASE")):
                self.snap_campaign(cid, ctype, [
                    ("t_life.csv", tr.it_rows_target([("kw", 200, 20, 220, 600)]), "TARGETING", LIFE),
                    ("s65.csv", tr.it_rows_st([("tai chi for seniors", "kw", 60, 6, 70, 300), ("chair tai chi", "kw", 40, 4, 50, 150)]),
                     "SEARCH_TERM", D65)])
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [(r.filename, r.problems) for r in res])
        return self.analyze_and_decide()


class AutoPipelineTests(AutoBuild):
    def test_recs_and_decisions_carry_auto_targeting_group(self):
        an, ds = self.build()
        for r in an.recs:
            self.assertEqual(r["target_type"], "AUTO_TARGETING_GROUP", (r["target"], r["report_type"]))
        tg = [r for r in an.recs if r["report_type"] == "TARGETING"]
        self.assertEqual({r["target"] for r in tg}, set(GROUPS))
        st = [r for r in an.recs if r["report_type"] == "SEARCH_TERM"]
        self.assertEqual({(r["search_term"], r["target"]) for r in st}, {("tai chi for seniors", "loose-match"), ("chair tai chi", "close-match")})
        self.assertTrue(all(r["action_scope"] == "PARENT_TARGET" for r in st))      # il termine reale non e' il gruppo che l'ha generato
        groups = [d for d in ds.decisions if "negterm" not in d["action_key"]]
        self.assertEqual({d["target"] for d in groups}, set(GROUPS))
        for d in groups:
            self.assertEqual(d["target_type"], "AUTO_TARGETING_GROUP")
            self.assertNotEqual(d["target_type"], "KEYWORD_TARGET")
            self.assertTrue(d["action_key"].split("|")[-1].startswith("autogroup:"), d["action_key"])

    def test_economics_and_bid_decisions_keep_working(self):
        _, ds = self.build()
        by = {d["target"]: d for d in ds.decisions if "negterm" not in d["action_key"]}
        loose = by["loose-match"]                                  # ACOS 55%, 20 ordini: stessa logica economica delle keyword
        self.assertEqual((loose["final_action"], loose["bid_change_pct"]), ("BID_DOWN_10_CANDIDATE", -10))
        self.assertEqual((loose["lifetime_orders"], loose["lifetime_spend"], round(loose["lifetime_acos"], 1)), (20, 330, 55.0))
        self.assertIsNotNone(loose["lifetime_estimated_profit"])
        self.assertEqual(loose["target_status"], "ENABLED")
        self.assertEqual(by["complements"]["final_action"], "HOLD")      # ACOS 15%
        self.assertIn(loose["delivery_source"], ("UNKNOWN", "TARGET", "CAMPAIGN_FALLBACK"))

    def test_no_negative_candidate_on_the_group_itself(self):
        an, ds = self.build()
        zero = [d for d in ds.decisions if d["target"] == "close-match" and "negterm" not in d["action_key"]][0]
        self.assertNotEqual(zero["final_action"], "NEGATIVE_CANDIDATE")           # 0 ordini, spesa alta: pausa del gruppo, mai negative
        self.assertEqual(zero["final_action"], "PAUSE_CANDIDATE")
        for d in ds.decisions:
            if d["final_action"] == "NEGATIVE_CANDIDATE":
                self.assertNotIn(d["search_term"], GROUPS)                          # eventuali negative sono su search term reali
                self.assertTrue(d["action_key"].split("|")[-1].startswith("negterm:"))
        for r in an.recs:
            if r["recommended_action"] == "NEGATIVE_CANDIDATE":
                self.assertEqual(r["report_type"], "SEARCH_TERM")
                self.assertNotIn(r["search_term"], GROUPS)

    def test_auto_campaign_is_excluded_from_manual_keyword_overlaps(self):
        an, ds = self.build(extra_manual=True)
        for o in an.overlaps:
            self.assertNotIn("C_AUTO", {c["campaign_id"] for c in o["campaigns"]})
            self.assertNotIn(o["classification"], ("BETTER_IN_OTHER",))
        # controllo: EXACT vs PHRASE si sovrappongono ancora normalmente (nessuna regressione sulle campagne manuali)
        terms = {(o["search_term"], tuple(sorted(c["campaign_id"] for c in o["campaigns"]))) for o in an.overlaps}
        self.assertIn(("tai chi for seniors", ("C_EXACT", "C_PHRASE")), terms)
        self.assertIn(("chair tai chi", ("C_EXACT", "C_PHRASE")), terms)
        for d in ds.decisions:
            if d["campaign_id"] == "C_AUTO":
                self.assertFalse(d["cross_campaign_overlap"])
                self.assertIsNone(d["better_campaign_if_any"])

    def test_manual_campaigns_regression(self):
        an, ds = self.build(extra_manual=True)
        manual = [d for d in ds.decisions if d["campaign_id"] in ("C_EXACT", "C_PHRASE") and "negterm" not in d["action_key"]]
        self.assertEqual({d["target_type"] for d in manual}, {"KEYWORD_TARGET"})
        self.assertTrue(all(d["action_key"].split("|")[-1].startswith("kw:") for d in manual))
        # una keyword manuale che si chiama come un gruppo Auto resta keyword
        self.tearDown()
        self.setUp()
        self.snap_campaign("C_EXACT", "EXACT", [("t_life.csv", tr.it_rows_target([("complements", 200, 20, 220, 600)]), "TARGETING", LIFE),
                                                ("s65.csv", tr.it_rows_st([("complements", "complements", 60, 6, 70, 300)]), "SEARCH_TERM", D65)])
        self.run_import()
        _, ds2 = self.analyze_and_decide()
        d = [x for x in ds2.decisions if x["target"] == "complements"][0]
        self.assertEqual(d["target_type"], "KEYWORD_TARGET")
        self.assertTrue(d["action_key"].endswith("|kw:complements"))

    def test_search_terms_from_auto_stay_real_terms_for_future_keyword_opportunities(self):
        """Il modello distingue gia' il termine reale (search_term) dal gruppo Auto che l'ha generato (target + target_type)."""
        an, _ = self.build()
        r = [x for x in an.recs if x["report_type"] == "SEARCH_TERM" and x["search_term"] == "chair tai chi"][0]
        self.assertEqual((r["search_term"], r["target"], r["target_type"]), ("chair tai chi", "close-match", "AUTO_TARGETING_GROUP"))
        self.assertNotEqual(r["search_term"], r["target"])


class DashboardLabelTests(AutoBuild):
    def test_italian_label_and_note(self):
        import ui_labels as U
        self.assertEqual(U.label_target_type("AUTO_TARGETING_GROUP"), "Gruppo targeting automatico")
        self.assertEqual(U.label_target_type("KEYWORD_TARGET"), "Keyword")
        self.assertEqual(U.display_value("target_type", "AUTO_TARGETING_GROUP"), "Gruppo targeting automatico")
        self.assertEqual(U.label_target_type("NUOVO_TIPO"), "NUOVO_TIPO")                    # valore sconosciuto: mostrato, mai nascosto
        for g in GROUPS:
            self.assertIn(g, U.AUTO_GROUPS_NOTE)
        self.assertIn("non keyword manuali", U.AUTO_GROUPS_NOTE)

    def render(self, page, db):
        old_env, old_mod = os.environ.get("KDP_ADS_DB"), sys.modules.get("streamlit")
        os.environ["KDP_ADS_DB"] = str(db)
        st = mock.MagicMock()
        st.columns.side_effect = lambda n, *a, **k: [mock.MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        st.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st.sidebar.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st.text_input.return_value = ""
        st.stop.side_effect = SystemExit
        sys.modules["streamlit"] = st
        for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
            del sys.modules[m]
        try:
            runpy.run_path(str(page), run_name="__main__")
        except SystemExit:
            pass
        finally:
            os.environ.pop("KDP_ADS_DB", None) if old_env is None else os.environ.__setitem__("KDP_ADS_DB", old_env)
            sys.modules.pop("streamlit", None) if old_mod is None else sys.modules.__setitem__("streamlit", old_mod)
        return st

    @staticmethod
    def texts(st):
        out = []
        for c in st.method_calls:
            out += [str(a) for a in c.args if isinstance(a, str)]
            out += [str(v) for v in c.kwargs.values() if isinstance(v, str)]
        return " ".join(out)

    def test_dashboard_shows_group_label_in_cards_and_campaign_page(self):
        an, ds = self.build()
        save_decisions_to_db(self.conn, ds, "d.csv", "d.md")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db = Path(self.settings["db_path"])
        import components.cards as cards  # noqa: E402  (ricaricato dentro render; qui solo per i badge puri)
        d = [x for x in ds.decisions if x["target"] == "loose-match" and "negterm" not in x["action_key"]][0]
        self.assertIn("Gruppo targeting automatico", cards.decision_badges(d))
        home = self.texts(self.render(ROOT / "DASHBOARD" / "panoramica.py", db))
        self.assertIn("Gruppo targeting automatico", home)                                  # card del Centro azioni (loose-match azionabile)
        self.assertIn("loose-match", home)
        camp = self.texts(self.render(ROOT / "DASHBOARD" / "pages" / "4_Campagne.py", db))
        self.assertIn("targeting automatico", camp)
        self.assertIn("non keyword manuali", camp)
        allp = self.texts(self.render(ROOT / "DASHBOARD" / "pages" / "2_Tutte_le_decisioni.py", db))
        self.assertTrue(allp)


if __name__ == "__main__":
    unittest.main()
