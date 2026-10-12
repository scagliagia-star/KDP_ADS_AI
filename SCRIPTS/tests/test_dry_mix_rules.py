"""Correzioni dal libro reale Dry Mix Pantry Cookbook (1805566598, US): negativa esatta sulla query identica al target FRASE, soglia minima di clic
per la promozione EXACT, ACOS n/d mai confrontato con l'obiettivo, regola zero-order, target storico senza traffico recente, taglio piu' forte
quando il problema e' forte e confermato. Dati sintetici + COPIA del DB reale (sola lettura)."""
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_opportunities as to
import test_overview as tov
import test_rules as tr
from kdp_ads.decision import _no_recent_traffic, _strong_cut
from kdp_ads.opportunities import NEGATIVE, PROMOTE, WATCH, unique_terms

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

RULES = json.loads((ROOT / "CONFIG" / "rules.json").read_text(encoding="utf-8"))
DEC = RULES["decision"]


# ------------------------------------------------------------------ 1. negativa esatta sulla query identica al target
class ExactNegativeOnIdenticalQueryTests(unittest.TestCase):
    def rows(self, ctype="PHRASE", cid="FRASE"):
        return [to.st(cid, ctype, "dry mix pantry cookbook", "dry mix pantry cookbook", to.D65, 151, 5, 75.98, 89.95),
                to.st(cid, ctype, "dry mix pantry cookbook", "dry mix pantry cookbook", to.D30, 38, 1, 16.29, 17.99)]

    def test_real_case_numbers_identical_to_phrase_target_is_an_exact_negative_candidate(self):
        r = to.only(to.run(self.rows()), "dry mix pantry cookbook")
        self.assertEqual((r["opportunity_type"], r["negative_type"]), (NEGATIVE, "EXACT"))
        self.assertIn("TERM_EQUALS_PARENT_TARGET", r["flags"])
        self.assertIn("NEGATIVE_EXACT_ONLY", r["flags"])
        self.assertFalse(r["operationally_actionable"])

    def test_not_suppressed_just_because_the_text_matches_the_target(self):
        res = to.run(self.rows())
        self.assertEqual(unique_terms(res["records"], NEGATIVE), ["dry mix pantry cookbook"])

    def test_exact_campaign_target_is_never_negated_by_its_own_query(self):
        r = to.only(to.run(self.rows("EXACT", "EXACT")), "dry mix pantry cookbook")
        self.assertEqual(r["opportunity_type"], WATCH)

    def test_non_identical_query_keeps_undecided_negative_type(self):
        rows = [{**x, "target": "other kw"} for x in self.rows()]
        rows = [{**x, "search_term": "dry mix pantry cookbook x"} for x in rows]
        r = to.only(to.run(rows), "dry mix pantry cookbook x")
        self.assertEqual((r["opportunity_type"], r["negative_type"]), (NEGATIVE, None))

    def test_already_negative_exact_identical_query_is_not_proposed_again(self):
        r = to.only(to.run(to.with_added(self.rows(), to.NEG_EXACT)), "dry mix pantry cookbook")
        self.assertNotEqual(r["opportunity_type"], NEGATIVE)


# ------------------------------------------------------------------ 2. promozione EXACT: minimo di clic
class PromoteMinClicksTests(unittest.TestCase):
    def test_threshold_is_in_rules_json(self):
        self.assertEqual(RULES["opportunities"]["promote_min_clicks"], 3)

    def test_one_click_two_orders_is_watch_not_exact(self):
        res = to.run(to.both("AUTO", "OTHER", "loose-match", "the super easy dry mix pantry cookbook", 1, 2, 0.33))
        r = to.only(res, "the super easy dry mix pantry cookbook")
        self.assertEqual(r["opportunity_type"], WATCH)
        self.assertEqual(unique_terms(res["records"], PROMOTE), [])

    def test_two_clicks_never_promoted_whatever_the_orders(self):
        for orders in (2, 5, 9):
            r = to.only(to.run(to.both("AUTO", "OTHER", "loose-match", "t", 2, orders, 1.0)), "t")
            self.assertNotEqual(r["opportunity_type"], PROMOTE)

    def test_five_clicks_three_orders_still_promotes(self):
        r = to.only(to.run(to.both("AUTO", "OTHER", "loose-match", "rare coin book", 5, 3, 3.10)), "rare coin book")
        self.assertEqual(r["opportunity_type"], PROMOTE)

    def test_exactly_three_clicks_promotes(self):
        r = to.only(to.run(to.both("AUTO", "OTHER", "loose-match", "edge", 3, 2, 2.0)), "edge")
        self.assertEqual(r["opportunity_type"], PROMOTE)


# ------------------------------------------------------------------ 3-4. zero-order: ACOS n/d, spesa sotto soglia => monitora
class ZeroOrderRuleTests(tr.RuleBase):
    def test_thresholds_in_rules_json(self):
        z = RULES["zero_orders"]
        self.assertEqual((z["bid_down_min_spend_mult"], z["bid_down_min_clicks"]), (1.25, 20))

    def test_zero_orders_spend_above_threshold_is_prudent_bid_down_5(self):
        r = self.d(35, 0, 11.5, 0)                        # royalty 8,55: 1,35x
        self.assertEqual((r.action, r.bid_change_pct, r.rule_classification), ("BID_DOWN_5_CANDIDATE", -5, "ZERO_ORDER_WASTE"))
        self.assertNotIn("ACOS", r.reason.split("[")[0])

    def test_zero_orders_spend_below_threshold_is_watch(self):
        r = self.d(18, 0, 4.43, 0)
        self.assertEqual((r.action, r.bid_change_pct), ("WATCH", None))

    def test_zero_orders_enough_spend_but_under_20_clicks_is_watch(self):
        self.assertEqual(self.d(19, 0, 12.0, 0).action, "WATCH")

    def test_pause_and_negative_thresholds_are_untouched(self):
        self.assertEqual(self.d(20, 0, 17.10, 0).action, "PAUSE_CANDIDATE")
        self.assertEqual(self.d(20, 0, 17.10, 0, rt="SEARCH_TERM").action, "NEGATIVE_CANDIDATE")


class OverviewZeroOrderTextTests(unittest.TestCase):
    BOOK = {**tov.BOOK, "royalty_per_copy": 7.5, "break_even_acos": 41.7}

    def test_bid_down_with_zero_orders_is_never_explained_with_acos(self):
        d = tov.dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, lifetime_clicks=35.0, lifetime_orders=0.0, lifetime_spend=11.12, lifetime_acos=None)
        s = tov.O.short_reason(d, self.BOOK)
        self.assertEqual(s, "Ha accumulato 35 clic e $11,12 di spesa senza acquisti. La spesa è 1,5 volte la royalty: riduzione prudente dell'offerta.")
        self.assertNotIn("ACOS", s)
        self.assertNotIn("n/d", s)

    def test_watch_with_zero_orders_low_spend(self):
        d = tov.dec(1, final_action="WATCH", lifetime_clicks=18.0, lifetime_orders=0.0, lifetime_spend=4.43, lifetime_acos=None)
        self.assertEqual(tov.O.short_reason(d, self.BOOK),
                         "Non ha ancora generato acquisti, ma la spesa è ancora troppo bassa per giustificare una riduzione dell'offerta.")
        self.assertEqual(tov.build([d])["monitor"][0]["reason"], tov.O.short_reason(d, self.BOOK))

    def test_bid_down_with_acos_keeps_the_acos_reason(self):
        d = tov.dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, lifetime_orders=10.0, lifetime_acos=48.0)
        self.assertIn("storico 48,0% contro 35,0%", tov.O.short_reason(d, self.BOOK))

    def test_exact_negative_badge_and_text(self):
        recs = [tov.opp("dry mix pantry cookbook", "NEGATIVE_CANDIDATE", clicks=151.0, orders=5.0, spend=75.98, sales=89.95, acos=84.5, negative_type="EXACT")]
        ov = tov.build([tov.dec(1)], recs)
        n = ov["negatives"][0]
        self.assertEqual(n["badge"], "VALUTA COME NEGATIVA ESATTA")
        self.assertIn("È la stessa query del target: aggiungila come negativa esatta, il target resta attivo.", n["reason"])
        self.assertEqual(ov["kpi"]["negatives"], 1)
        plain = tov.build([tov.dec(1)], [tov.opp("x", "NEGATIVE_CANDIDATE")])["negatives"][0]
        self.assertEqual(plain["badge"], "VALUTA COME NEGATIVA")


# ------------------------------------------------------------------ 5-6. soglie e helper della decisione
class DecisionHelpersTests(unittest.TestCase):
    CTX = SimpleNamespace(target_acos=35.0, break_even_acos=41.7, royalty=7.5)

    @staticmethod
    def ev(acos=None, clicks=0.0, spend=0.0, orders=0.0):
        return SimpleNamespace(m=SimpleNamespace(acos=acos, clicks=clicks, spend=spend, orders=orders))

    def test_strong_confirmed_problem_cuts_more_than_5(self):
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(60.0, 40), self.CTX, DEC), 10)
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(70.45, 88), self.CTX, DEC), 15)

    def test_small_samples_never_trigger_the_strong_cut(self):
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(80.0, 12), self.CTX, DEC), 0)      # pochi clic recenti
        self.assertEqual(_strong_cut(self.ev(51.6, orders=2), self.ev(80.0, 60), self.CTX, DEC), 0)       # pochi acquisti storici
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(50.0, 60), self.CTX, DEC), 0)      # recente < 1,5x obiettivo
        self.assertEqual(_strong_cut(self.ev(40.0, orders=20), self.ev(80.0, 60), self.CTX, DEC), 0)      # storico non sopra il pareggio
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(None, 60), self.CTX, DEC), 0)      # ACOS recente n/d
        self.assertEqual(_strong_cut(None, self.ev(80.0, 60), self.CTX, DEC), 0)
        self.assertEqual(_strong_cut(self.ev(51.6, orders=20), self.ev(75.0, 25), self.CTX, DEC), 10)     # 15% richiede >= 50 clic

    def test_no_recent_traffic_with_low_bid_is_not_a_bid_down(self):
        c = self.CTX
        self.assertTrue(_no_recent_traffic(None, "p", 0.24, "ENABLED", c, DEC))
        self.assertTrue(_no_recent_traffic(self.ev(None, 0, 0.0), "p", 0.24, "ENABLED", c, DEC))
        self.assertFalse(_no_recent_traffic(None, "p", 0.42, "ENABLED", c, DEC))            # bid non basso (> 5% royalty)
        self.assertFalse(_no_recent_traffic(self.ev(None, 3, 1.0), "p", 0.24, "ENABLED", c, DEC))   # c'e' traffico recente
        self.assertFalse(_no_recent_traffic(None, None, 0.24, "ENABLED", c, DEC))           # nessun periodo tattico importato
        self.assertFalse(_no_recent_traffic(None, "p", 0.24, "PAUSED", c, DEC))
        self.assertFalse(_no_recent_traffic(None, "p", None, "ENABLED", c, DEC))

    def test_thresholds_in_rules_json_and_low_bid_reuses_the_mild_one(self):
        for k in ("strong_cut_min_recent_acos_mult", "strong_cut_min_recent_clicks", "strong_cut_min_hist_orders", "strong_cut_15_recent_acos_mult",
                  "strong_cut_15_min_recent_clicks", "no_traffic_monitor_enabled", "mild_low_bid_royalty_frac"):
            self.assertIn(k, DEC)
        self.assertEqual(sum(1 for k in DEC if "low_bid" in k), 1)                          # nessuna soglia duplicata per "bid basso"


# ------------------------------------------------------------------ libro reale (copia): Dry Mix + regressioni
REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"


@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealBooksOnCopyTests(unittest.TestCase):
    """Rianalisi + decisioni rigenerate su una COPIA del DB reale: nessuna scrittura sul DB reale, books.json e INPUT invariati."""

    @classmethod
    def setUpClass(cls):
        cls.real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        cls.books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        inp = ROOT / "INPUT"
        cls.input_state = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        src.close()
        dst.row_factory = sqlite3.Row
        cls.runs = {}
        from kdp_ads.config import CONFIG_DIR, load_mapping, load_settings
        from kdp_ads.pipeline import run_analysis, run_decisions
        lg, s = logging.getLogger("t"), load_settings()
        for book in ("1805566598", "B0GY42H3W4", "B0H7P3C4D7"):
            if not dst.execute("SELECT 1 FROM decision_runs WHERE book_id=?", (book,)).fetchone():
                continue
            ra = run_analysis(s, load_mapping(), CONFIG_DIR / "rules.json", book, Path(cls.tmp.name) / "out", lg, save_db=True, conn=dst, marketplace="US")
            rd = run_decisions(s, CONFIG_DIR / "rules.json", book, Path(cls.tmp.name) / "out", lg, ra["run_id"], save_db=True, conn=dst, marketplace="US")
            cls.runs[book] = rd["run_id"]
        dst.commit()
        cls.conn = dst

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def dec(self, book, campaign, target):
        if book not in self.runs:
            self.skipTest(f"libro {book} non presente")
        r = self.conn.execute("SELECT * FROM decisions WHERE run_id=? AND campaign_id=? AND target=?", (self.runs[book], campaign, target)).fetchone()
        self.assertIsNotNone(r, (campaign, target))
        return r

    def test_dry_mix_decisions(self):
        b = "1805566598"
        top = self.dec(b, "FRASE", "dry mix pantry cookbook")
        self.assertIn(top["final_action"], ("BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE"))
        self.assertTrue(0.34 <= round(top["current_bid"] * (1 + top["bid_change_pct"] / 100), 2) <= 0.38)       # circa $0,36-$0,38
        self.assertIn("STRONG_CONFIRMED_CUT", top["flags"])
        the = self.dec(b, "FRASE", "the dry mix pantry cookbook")
        self.assertEqual((the["final_action"], the["decision_rule"]), ("WATCH", "C2C_NO_RECENT_TRAFFIC"))
        rec = self.dec(b, "FRASE", "dry mix recipe book")
        self.assertEqual((rec["final_action"], rec["bid_change_pct"]), ("BID_DOWN_5_CANDIDATE", -5.0))
        self.assertIsNone(rec["lifetime_acos"])                                                                  # ACOS n/d: motivo zero-order
        self.assertEqual(self.dec(b, "AUTO", "close-match")["final_action"], "WATCH")
        self.assertEqual(self.dec(b, "AUTO", "loose-match")["final_action"], "HOLD")
        self.assertEqual(self.dec(b, "AUTO", "complements")["final_action"], "HOLD")

    def test_regressions_rare_coins(self):
        b = "B0GY42H3W4"
        sub = self.dec(b, "AUTO", "substitutes")
        self.assertEqual((sub["final_action"], sub["decision_rule"]), ("WATCH", "C4B_MILD_DETERIORATION_MONITOR"))
        a, x = self.dec(b, "PRODOTTO", 'asin="0794851630"'), self.dec(b, "PRODOTTO", 'asin-expanded="0794851630"')
        self.assertNotEqual(a["action_key"], x["action_key"])
        self.assertEqual((a["target_status"], x["target_status"], a["lifetime_spend"], x["lifetime_spend"]), ("ENABLED", "PAUSED", 62.63, 5.67))

    def test_opportunities_on_the_real_books(self):
        from data_access.repository import Repo
        repo = Repo(str(self.db))

        def opps(book):
            bk = {**repo.book(book), **[m for m in repo.marketplaces(book) if m["marketplace"] == "US"][0]}
            return repo.search_term_opportunities(bk, "US")["records"]
        if "1805566598" in self.runs:
            recs = opps("1805566598")
            by = {(r["campaign_id"], r["search_term"]): r for r in recs}
            self.assertEqual(by[("AUTO", "dry mix recipe book")]["opportunity_type"], PROMOTE)
            self.assertEqual(by[("AUTO", "the super easy dry mix pantry cookbook")]["opportunity_type"], WATCH)
            neg = by[("FRASE", "dry mix pantry cookbook")]
            self.assertEqual((neg["opportunity_type"], neg["negative_type"]), (NEGATIVE, "EXACT"))
            self.assertEqual(unique_terms(recs, PROMOTE), ["dry mix recipe book"])
        if "B0GY42H3W4" in self.runs:
            recs = opps("B0GY42H3W4")
            self.assertIn("rare coin book", unique_terms(recs, PROMOTE))
            by = {r["search_term"]: r["opportunity_type"] for r in recs}
            self.assertEqual(by["coin collection supplies"], "PROMOTED_TO_EXACT")
            self.assertEqual(by["0794851630"], "WINNING_PRODUCT_TARGET")
        if "B0H7P3C4D7" in self.runs:
            self.assertEqual(unique_terms(opps("B0H7P3C4D7"), PROMOTE), ["national park guide", "national park map", "national parks"])

    @unittest.skipIf(AppTest is None, "streamlit non installato")
    def test_dry_mix_overview_text(self):
        if "1805566598" not in self.runs:
            self.skipTest("Dry Mix non presente")
        old = os.environ.get("KDP_ADS_DB")
        os.environ["KDP_ADS_DB"] = str(self.db)
        try:
            at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
            tov.select(at, "Libro", "1805566598")
            tov.select(at, "Run decisionale", f"#{self.runs['1805566598']}")
            text = "\n".join(re.sub(r"<[^>]+>", "", t) for t, in_exp, _ in tov.page_text(at) if not in_exp)
        finally:
            if old is None:
                os.environ.pop("KDP_ADS_DB", None)
            else:
                os.environ["KDP_ADS_DB"] = old
        self.assertFalse(at.exception)
        self.assertIn("## ABBASSA OFFERTA DEL 15%", text)
        self.assertIn("Ha accumulato 35 clic e $11,12 di spesa senza acquisti.", text)
        self.assertNotIn("storico n/d contro", text)
        self.assertIn("VALUTA COME NEGATIVA ESATTA **dry mix pantry cookbook**", text)
        self.assertIn("METTI IN EXACT **dry mix recipe book**", text)
        self.assertNotIn("the super easy dry mix pantry cookbook**", text.split("KEYWORD DA VALUTARE COME NEGATIVE")[0])
        self.assertIn("MONITORA **AUTO · Corrispondenza stretta**", text)
        self.assertIn("Non ha ancora generato acquisti, ma la spesa è ancora troppo bassa", text)
        self.assertIn("NESSUNA EROGAZIONE **FRASE · the dry mix pantry cookbook**", text)
        self.assertNotIn("Impressioni di riferimento", text)
        self.assertEqual([m.value for m in at.metric if m.label == "CANDIDATI NEGATIVI"], ["1"])

    def test_nothing_written_anywhere(self):
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_hash)                      # DB reale invariato
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        inp = ROOT / "INPUT"
        now = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        self.assertEqual(now, self.input_state)                                                                 # INPUT invariato


if __name__ == "__main__":
    unittest.main()
