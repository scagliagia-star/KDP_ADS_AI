"""Giro "Suggerimenti di crescita": gate zero-order su EXACT/AUTO, GOOD vs MONITORA, Search Term con metriche impossibili, funnel AUTO -> FRASE -> EXACT,
scaling +5%, SVILUPPA CAMPAGNA, card GUARDA ANCHE e pagina dedicata. Dati sintetici + COPIA del DB reale (sola lettura)."""
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

import pandas as pd
import pyarrow as pa

import test_opportunities as to
import test_overview as tov
from kdp_ads import growth as G
from kdp_ads.dataquality import impossible_warnings, is_impossible_search_term, split_search_terms
from kdp_ads.decision import make_ev
from kdp_ads.opportunities import NEGATIVE, PROMOTE, PRODUCT_WINNER, SUGGEST_PHRASE, WATCH, compute_opportunities, unique_terms

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

RULES = json.loads((ROOT / "CONFIG" / "rules.json").read_text(encoding="utf-8"))
BOOK = {"target_acos": 35.0, "break_even_acos": 39.1, "royalty_per_copy": 9.0, "currency": "USD"}
CFG = G.config(RULES)


# ------------------------------------------------------------------ A. zero-order sotto i gate: mai bid-down (EXACT, PHRASE, AUTO)
class ZeroOrderGateInDecisionLayerTests(unittest.TestCase):
    CTX = SimpleNamespace(royalty=9.0, rules=RULES, bands={"mild": 50.0, "unprofitable": 60.0}, break_even_acos=39.1, target_acos=35.0)

    @staticmethod
    def rec(clicks, spend, action="BID_DOWN_5_CANDIDATE", cls="ZERO_ORDER_WASTE"):
        return {"impressions": clicks * 40, "clicks": clicks, "spend": spend, "orders": 0, "sales": 0, "recommended_action": action, "confidence": "MEDIUM",
                "rule_classification": cls, "recommended_bid_change_pct": -5}

    def test_stale_zero_order_bid_down_under_the_gates_becomes_watch_for_any_campaign_type(self):
        for clicks, spend in ((13, 10.39), (12, 8.64), (19, 30.0), (40, 9.0)):      # clic < 20 oppure spesa < 1,25x royalty
            ev = make_ev(self.rec(clicks, spend), self.CTX, RULES["decision"], False)
            self.assertEqual((ev.action, ev.step), ("WATCH", 0), (clicks, spend))
            self.assertIn("zero-order sotto i gate", ev.note)

    def test_above_both_gates_the_prudent_bid_down_is_kept(self):
        ev = make_ev(self.rec(35, 11.5), self.CTX, RULES["decision"], False)
        self.assertEqual((ev.action, ev.step), ("BID_DOWN_5_CANDIDATE", 5))

    def test_pause_candidates_and_non_zero_order_rules_are_untouched(self):
        self.assertEqual(make_ev(self.rec(15, 20.0, "PAUSE_CANDIDATE"), self.CTX, RULES["decision"], False).action, "PAUSE_CANDIDATE")
        ev = make_ev(self.rec(13, 10.39, "BID_DOWN_5_CANDIDATE", "HIGH_VOLUME_INEFFICIENT"), self.CTX, RULES["decision"], False)
        self.assertEqual(ev.action, "BID_DOWN_5_CANDIDATE")


# ------------------------------------------------------------------ B. GOOD / NON TOCCARE vs MONITORA
def hold(i, **o):
    base = dict(final_action="HOLD", target="military workout", campaign_id="ESATTA", lifetime_clicks=122.0, lifetime_orders=27.0, lifetime_spend=179.0,
                lifetime_acos=33.2, lifetime_estimated_profit=40.0, tactical_clicks=5.0, tactical_orders=1.0, tactical_acos=34.6,
                recent_clicks=None, recent_orders=None, recent_acos=None, recent_vs_historical_signal="UNKNOWN")
    base.update(o)
    return tov.dec(i, **base)


def ov(ds):
    return tov.O.build_overview(ds, BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)


class GoodVsWatchTests(unittest.TestCase):
    def test_historical_good_and_recent_still_under_target_stays_good_even_if_relatively_worse(self):
        d = hold(1, lifetime_acos=14.0, lifetime_orders=13.0, tactical_clicks=13.0, tactical_orders=3.0, tactical_acos=20.4, recent_vs_historical_signal="DECLINING_SIGNAL")
        o = ov([d])
        self.assertEqual(([c["badge"] for c in o["good"]], o["monitor"]), (["NON TOCCARE"], []))
        self.assertEqual(o["good"][0]["reason"], "Sta funzionando bene.")                     # niente "peggiorato" solo perche' 20,4 > 14,0

    def test_historical_good_and_recent_above_target_with_enough_evidence_is_watch(self):
        d = hold(1, tactical_clicks=5.0, tactical_orders=1.0, recent_clicks=19.0, recent_orders=2.0, recent_acos=67.9)     # 30D minuscolo -> usa 65D
        o = ov([d])
        self.assertEqual(([c["badge"] for c in o["monitor"]], o["good"]), (["MONITORA"], []))
        self.assertIn("negli ultimi 65 giorni l'ACOS è 67,9%, sopra l'obiettivo 35,0%", o["monitor"][0]["reason"])
        self.assertEqual(ov([hold(2, tactical_clicks=20.0, tactical_orders=3.0, tactical_acos=53.9)])["monitor"][0]["reason"].count("30 giorni"), 1)

    def test_historical_good_and_recent_above_target_but_tiny_sample_does_not_overreact(self):
        d = hold(1, tactical_clicks=4.0, tactical_orders=1.0, tactical_acos=80.0, recent_clicks=6.0, recent_orders=1.0, recent_acos=90.0)
        o = ov([d])
        self.assertEqual((o["monitor"], len(o["good"])), ([], 1))                             # campioni minuscoli (< 10 clic): nessuna reazione

    def test_30d_preferred_when_usable_otherwise_65d(self):
        w = tov.O.recent_window_used(hold(1, tactical_clicks=12.0, tactical_orders=1.0, tactical_acos=20.0, recent_clicks=40.0, recent_orders=5.0, recent_acos=60.0))
        self.assertEqual((w["days"], w["acos"]), (30, 20.0))
        w = tov.O.recent_window_used(hold(1, tactical_clicks=5.0, recent_clicks=40.0, recent_orders=5.0, recent_acos=60.0))
        self.assertEqual((w["days"], w["acos"]), (65, 60.0))
        self.assertIsNone(tov.O.recent_window_used(hold(1, tactical_clicks=5.0, recent_clicks=9.0, recent_orders=2.0, recent_acos=60.0)))

    def test_historical_above_target_keeps_the_previous_prudent_rule(self):
        d = hold(1, lifetime_acos=35.7, lifetime_orders=5.0, tactical_clicks=9.0, tactical_orders=1.0, tactical_acos=55.0)
        self.assertEqual([c["badge"] for c in ov([d])["monitor"]], ["MONITORA"])


# ------------------------------------------------------------------ C. Search Term con metriche impossibili
def strow(campaign, ctype, term, win, clicks, orders, spend, sales=0.0, target="p"):
    r = to.st(campaign, ctype, target, term, win, clicks, orders, spend, sales)
    r.update(clicks=clicks, orders=orders, spend=spend, sales=sales)
    return r


class ImpossibleSearchTermTests(unittest.TestCase):
    def test_definition(self):
        self.assertTrue(is_impossible_search_term({"clicks": 0, "spend": 0.44, "orders": 0}))
        self.assertTrue(is_impossible_search_term({"clicks": 0, "spend": 0.0, "orders": 1}))
        self.assertFalse(is_impossible_search_term({"clicks": 0, "spend": 0.0, "orders": 0}))
        self.assertFalse(is_impossible_search_term({"clicks": 3, "spend": 1.0, "orders": 1}))
        self.assertFalse(is_impossible_search_term({"clicks": None, "spend": None, "orders": None}))

    def test_split_keeps_valid_rows_untouched_and_warns_per_campaign_and_period(self):
        rows = [strow("AUTO", "OTHER", "a", to.D30, 0, 1, 0.5), strow("AUTO", "OTHER", "b", to.D30, 0, 0, 0.4), strow("AUTO", "OTHER", "c", to.D30, 5, 1, 3.0),
                strow("FRASE", "PHRASE", "d", to.D65, 0, 2, 1.0)]
        before = [dict(r) for r in rows]
        good, bad = split_search_terms(rows)
        self.assertEqual((len(good), len(bad)), (1, 3))
        self.assertEqual(rows, before)                                                           # dati originali non modificati
        w = impossible_warnings(bad)
        self.assertEqual(len(w), 2)
        self.assertTrue(any("AUTO" in x and "2 righe" in x for x in w) and any("FRASE" in x and "1 righe" in x for x in w))
        self.assertTrue(all(x.startswith("DATA_WARNING IMPOSSIBLE_SEARCH_TERM_METRICS") for x in w))

    def test_impossible_rows_never_feed_exact_phrase_or_negative_opportunities(self):
        bad_exact = [dict(r, clicks=0, orders=3, spend=15.0) for r in to.both("FRASE", "PHRASE", "p", "ghost winner", 20, 3, 15.0)]
        bad_neg = [dict(r, clicks=0, orders=0, spend=40.0) for r in to.both("FRASE", "PHRASE", "p", "ghost waste", 20, 0, 40.0)]
        bad_auto = [dict(r, clicks=0, orders=2, spend=2.0) for r in to.both("AUTO", "OTHER", "loose-match", "ghost auto", 5, 2, 2.0)]
        good = to.both("FRASE", "PHRASE", "p", "real winner", 20, 3, 15.0)
        res = to.run(bad_exact + bad_neg + bad_auto + good)
        self.assertEqual(unique_terms(res["records"], PROMOTE), ["real winner"])
        self.assertEqual(unique_terms(res["records"], NEGATIVE), [])
        self.assertEqual(unique_terms(res["records"], SUGGEST_PHRASE), [])
        terms = {r["search_term"] for r in res["records"]}
        self.assertFalse({"ghost winner", "ghost waste", "ghost auto"} & terms)
        self.assertEqual(res["excluded"]["righe_impossibili"], 6)                          # 3 termini x 2 finestre
        self.assertTrue(res["data_warnings"])

    def test_target_report_rows_are_not_touched_by_the_search_term_check(self):
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "analysis.py").read_text(encoding="utf-8")
        self.assertIn("srows, bad_st = split_search_terms(srows)", src)
        self.assertNotIn("split_search_terms(trows)", src)
        res = to.run(to.both("AUTO", "OTHER", "loose-match", "t", 5, 1, 3.0), [dict(to.tgt("EXACT", "EXACT", "x"), clicks=0, spend=1.0, orders=1)])
        self.assertNotIn("righe_impossibili", res["excluded"])


# ------------------------------------------------------------------ D. funnel AUTO -> FRASE -> EXACT
class AutoFunnelTests(unittest.TestCase):
    def auto(self, term, clicks, orders, spend, **kw):
        return to.run(to.both("AUTO", "OTHER", "loose-match", term, clicks, orders, spend), **kw)

    def test_moderate_auto_winner_goes_to_phrase_not_exact(self):
        res = self.auto("glute book", 4, 1, 1.70)
        r = to.only(res, "glute book")
        self.assertEqual(r["opportunity_type"], SUGGEST_PHRASE)
        self.assertIn("AUTO_MODERATE_WINNER", r["flags"])
        self.assertEqual((r["suggested_campaigns"], unique_terms(res["records"], PROMOTE)), (["FRASE"], []))
        self.assertFalse(r["operationally_actionable"])

    def test_strong_auto_winner_can_still_jump_straight_to_exact(self):
        r = to.only(self.auto("dry mix recipe book", 100, 7, 31.56), "dry mix recipe book")
        self.assertEqual(r["opportunity_type"], PROMOTE)
        self.assertIn("AUTO_STRONG_WINNER", r["flags"])
        self.assertEqual(to.only(self.auto("rare coin book", 5, 3, 3.10), "rare coin book")["opportunity_type"], PROMOTE)

    def test_moderate_requires_all_conditions(self):
        self.assertEqual(to.only(self.auto("x", 2, 1, 1.0), "x")["opportunity_type"], WATCH)                 # < 3 clic
        self.assertEqual(to.only(self.auto("x", 5, 0, 1.0), "x")["opportunity_type"], WATCH)                 # nessun ordine
        self.assertEqual(to.only(self.auto("x", 20, 1, 12.0), "x")["opportunity_type"], WATCH)               # ACOS sopra l'obiettivo
        already = to.run(to.both("AUTO", "OTHER", "loose-match", "x", 4, 1, 1.7), [to.tgt("FRASE", "PHRASE", "X", "A frase")])
        self.assertNotEqual(to.only(already, "x")["opportunity_type"], SUGGEST_PHRASE)                       # gia' presente come keyword
        neg = to.run(to.with_added(to.both("AUTO", "OTHER", "loose-match", "x", 4, 1, 1.7), to.NEG_EXACT))
        self.assertNotEqual(to.only(neg, "x")["opportunity_type"], SUGGEST_PHRASE)                           # gia' negativa

    def test_phrase_campaign_terms_are_not_auto_to_phrase(self):
        r = to.only(to.run(to.both("FRASE", "PHRASE", "p", "y", 4, 1, 1.7)), "y")
        self.assertEqual(r["opportunity_type"], WATCH)

    def test_asin_never_becomes_phrase_or_exact(self):
        res = to.run(to.both("AUTO", "OTHER", "loose-match", "0794851630", 6, 2, 3.0) + to.both("AUTO", "OTHER", "loose-match", "b0abc12345", 4, 1, 1.5))
        self.assertEqual([r["opportunity_type"] for r in res["records"]].count(SUGGEST_PHRASE), 0)
        self.assertEqual(unique_terms(res["records"], PROMOTE), [])
        self.assertTrue(all(r["opportunity_type"] == PRODUCT_WINNER for r in res["records"]) or not res["records"])


# ------------------------------------------------------------------ E. scaling +5%
def scale_dec(i=1, **o):
    base = dict(final_action="HOLD", target="route 66 map", campaign_id="ESATTA", campaign_type="EXACT", target_type="KEYWORD_TARGET", target_status="ENABLED",
                current_bid=1.11, lifetime_clicks=44.0, lifetime_orders=13.0, lifetime_spend=39.1, lifetime_sales=280.0, lifetime_acos=14.0,
                tactical_clicks=13.0, tactical_orders=3.0, tactical_acos=20.4, recent_clicks=None, recent_orders=None, recent_acos=None, flags="",
                delivery_signal="DELIVERY_STABLE")
    base.update(o)
    return tov.dec(i, **base)


class ScalingTests(unittest.TestCase):
    def blockers(self, d, warnings=()):
        return G.scale_blockers(d, BOOK, CFG, warnings)

    def test_positive_case_suggests_plus_5_percent_bid(self):
        self.assertEqual(self.blockers(scale_dec()), [])
        items = G.scale_items([scale_dec()], BOOK, CFG)
        self.assertEqual((items[0]["bid"], items[0]["suggested_bid"], items[0]["step_pct"]), (1.11, 1.17, 5))
        self.assertEqual(CFG["scale_step_pct"], 5)

    def test_travel_guide_case_with_2_recent_orders_under_target(self):
        d = scale_dec(target="route 66 travel guide", lifetime_orders=46.0, lifetime_acos=23.3, tactical_clicks=18.0, tactical_orders=2.0, tactical_acos=32.4)
        self.assertEqual(self.blockers(d), [])                                                    # 23,3% <= 75% di 35% (26,25%)

    def test_blocked_when_historical_acos_not_far_enough_below_target(self):
        self.assertIn("ACOS storico non abbastanza sotto l'obiettivo", self.blockers(scale_dec(lifetime_acos=33.2)))
        self.assertIn("acquisti storici insufficienti", self.blockers(scale_dec(lifetime_orders=4.0)))

    def test_blocked_when_recent_above_target(self):
        self.assertTrue(self.blockers(scale_dec(tactical_acos=53.9, tactical_orders=3.0, tactical_clicks=67.0)))
        self.assertIn("ACOS recente sopra l'obiettivo", self.blockers(scale_dec(tactical_acos=36.0)))

    def test_blocked_when_recent_orders_below_2(self):
        self.assertIn("nessun periodo recente valido (acquisti recenti insufficienti)", self.blockers(scale_dec(tactical_orders=1.0)))

    def test_blocked_by_recent_zero_order_and_by_non_hold_states(self):
        self.assertIn("zero-order recente", self.blockers(scale_dec(recent_clicks=20.0, recent_orders=0.0, recent_acos=None)))
        for fa in ("WATCH", "BID_DOWN_5_CANDIDATE", "HOLD_PAUSED_TARGET"):
            self.assertTrue(self.blockers(scale_dec(final_action=fa)))
        self.assertIn("target non attivo", self.blockers(scale_dec(target_status="PAUSED")))

    def test_blocked_with_data_warnings(self):
        self.assertTrue(self.blockers(scale_dec(flags="ABSENT_IN_LATEST_SNAPSHOT")))
        self.assertTrue(self.blockers(scale_dec(), [{"text": "DATA_WARNING ordini > 0 con vendite = 0 (ESATTA, 'route 66 map')"}]))
        only65 = scale_dec(tactical_clicks=3.0, tactical_orders=1.0, recent_clicks=20.0, recent_orders=3.0, recent_acos=20.0)
        self.assertEqual(self.blockers(only65), [])
        self.assertTrue(self.blockers(only65, [{"text": "DATA_WARNING IMPOSSIBLE_SEARCH_TERM_METRICS ESATTA periodo x: 3 righe"}]))
        # il 30 giorni viene dal report Target: le righe Search Term impossibili (escluse dai calcoli) non lo rendono inaffidabile
        self.assertEqual(self.blockers(scale_dec(), [{"text": "DATA_WARNING IMPOSSIBLE_SEARCH_TERM_METRICS ESATTA periodo x: 3 righe"}]), [])

    def test_impression_decline_never_drives_scaling(self):
        for sig in ("DELIVERY_COLLAPSED", "DELIVERY_DECLINING", "DELIVERY_STABLE", "UNKNOWN"):
            self.assertEqual(self.blockers(scale_dec(delivery_signal=sig, tactical_impressions_per_day=0.5)), [], sig)
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "growth.py").read_text(encoding="utf-8")
        self.assertNotIn("impressions", src.split("def scale_blockers")[1].split("def suggested_bid")[0])

    def test_military_and_glutes_cases_are_not_scaled(self):
        self.assertTrue(self.blockers(scale_dec(target="military workout", lifetime_acos=33.2, lifetime_orders=27.0, tactical_orders=1.0)))
        self.assertTrue(self.blockers(scale_dec(target="loose-match", lifetime_acos=32.9, tactical_clicks=67.0, tactical_orders=3.0, tactical_acos=53.9)))


# ------------------------------------------------------------------ F/G. SVILUPPA CAMPAGNA + card + pagina
def camp_dec(i, campaign, ctype, clicks=0.0, orders=0.0, spend=0.0, status="ENABLED", **o):
    return tov.dec(i, campaign_id=campaign, campaign_type=ctype, target=f"kw{i}", target_status=status, lifetime_clicks=clicks, lifetime_orders=orders,
                   lifetime_spend=spend, lifetime_sales=orders * 20.0, **o)


def phrase_candidate(term="glute book"):
    return {**tov.opp(term, "SUGGEST_IN_PHRASE", campaign_id="AUTO", campaign_type="OTHER", parent_target="loose-match", parent_target_type="AUTO_TARGETING_GROUP",
                      clicks=4.0, orders=1.0, spend=1.7, sales=19.99, acos=8.5, confidence="LOW", flags=["AUTO_MODERATE_WINNER"], suggested_campaigns=["FRASE"]),
            "windows_agreement": "SOLO_UNA_FINESTRA"}


class DevelopCampaignTests(unittest.TestCase):
    def grow(self, ds, recs=()):
        return G.build_growth(ds, list(recs), BOOK, CFG)

    def test_not_suggested_for_a_high_volume_zero_order_inefficient_campaign(self):
        ds = [camp_dec(1, "FRASE", "PHRASE", clicks=200.0, orders=0.0, spend=150.0)]
        self.assertEqual(self.grow(ds)["DEVELOP"], [])
        self.assertEqual(self.grow([camp_dec(1, "FRASE", "PHRASE", clicks=25.0, orders=0.0, spend=40.0)])["DEVELOP"], [])      # spesa gia' alta
        self.assertEqual(self.grow([camp_dec(1, "FRASE", "PHRASE", clicks=10.0, orders=2.0, spend=9.0)])["DEVELOP"], [])       # vende gia'

    def test_suggested_for_insufficient_coverage_and_concrete_when_candidates_exist(self):
        ds = [camp_dec(1, "FRASE", "PHRASE", clicks=6.0, spend=5.88)]
        g = self.grow(ds, [phrase_candidate()])
        self.assertEqual([(i["campaign"], i["candidates"], i["secondary"]) for i in g["DEVELOP"]], [("FRASE", ["glute book"], False)])
        self.assertIn("glute book → aggiungili in FRASE", g["DEVELOP"][0]["reason"])

    def test_without_concrete_candidates_it_is_a_secondary_generic_suggestion(self):
        g = self.grow([camp_dec(1, "FRASE", "PHRASE", clicks=6.0, spend=5.88)])
        self.assertTrue(g["DEVELOP"][0]["secondary"])
        self.assertIn("Copertura limitata: valuta ricerca di nuove keyword.", g["DEVELOP"][0]["reason"])
        self.assertNotIn("aggiungi più keyword", g["DEVELOP"][0]["reason"].lower())

    def test_auto_campaigns_and_paused_only_campaigns_are_never_developed(self):
        self.assertEqual(self.grow([camp_dec(1, "AUTO", "OTHER", clicks=1.0, spend=0.5)])["DEVELOP"], [])
        self.assertEqual(self.grow([camp_dec(1, "FRASE", "PHRASE", clicks=1.0, spend=0.5, status="PAUSED")])["DEVELOP"], [])


class GrowthCardAndPageTests(unittest.TestCase):
    def growth(self):
        ds = [camp_dec(1, "FRASE", "PHRASE", clicks=6.0, spend=5.88), scale_dec(2)]
        recs = [phrase_candidate(), tov.opp("rv camping bible", "PROMOTE_TO_EXACT", flags=[]),
                tov.opp("0988924676", "WINNING_PRODUCT_TARGET", campaign_id="AUTO", product_asin="0988924676", asin_targeted=False, acos=9.0, orders=2.0)]
        return G.build_growth(ds, recs, BOOK, CFG)

    def test_counts_and_summary_lines_only_for_categories_above_zero(self):
        g = self.growth()
        self.assertEqual(g["counts"], {"SCALE": 1, "AUTO_TO_PHRASE": 1, "TO_EXACT": 1, "DEVELOP": 1, "NEW_PRODUCT": 1})
        self.assertEqual(g["total"], 5)
        self.assertEqual(G.summary_lines(g), ["1 da testare in FRASE", "1 candidato da scalare", "1 campagna da sviluppare", "1 keyword da mettere in EXACT",
                                              "1 nuovo target prodotto da valutare"])
        empty = G.build_growth([], [], BOOK, CFG)
        self.assertEqual((empty["total"], G.summary_lines(empty)), (0, []))

    def test_new_product_targets_only_from_observed_untargeted_asins(self):
        g = self.growth()
        self.assertEqual(g["NEW_PRODUCT"][0]["asin"], "0988924676")
        targeted = G.build_growth([], [tov.opp("0988924676", "WINNING_PRODUCT_TARGET", product_asin="0988924676", asin_targeted=True)], BOOK, CFG)
        self.assertEqual(targeted["NEW_PRODUCT"], [])

    def test_growth_does_not_change_the_defensive_overview(self):
        ds = [scale_dec(1)]
        a = tov.O.build_overview(ds, BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        G.build_growth(ds, [], BOOK, CFG)
        self.assertEqual((a["kpi"]["actions"], len(a["actions"])), (0, 0))                        # DA VALUTARE ORA non include la crescita
        self.assertNotIn("growth", a["kpi"])

    def test_home_shows_only_top_delivery_rows_but_kpi_keeps_the_total(self):
        ds = [tov.dec(i, target=f"kw{i}", tactical_impressions_per_day=0.0, final_action="NO_ACTION") for i in range(1, 16)]
        o = tov.O.build_overview(ds, BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        self.assertEqual((o["kpi"]["delivery"], len(o["delivery"]), len(o["delivery_shown"])), (15, 15, tov.O.MAX_DELIVERY_HOME))


# ------------------------------------------------------------------ libri reali (COPIA del DB): rigenerazione + regressioni
REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"
BOOKS = {"rare": "B0GY42H3W4", "dry": "1805566598", "cal": "B0GNZFLXCV", "rv": "B0GZNKDLGM", "r66": "B0GZNG6N1D", "mil": "B0GQFR3M1K", "milw": "B0GRP7LZ9J",
         "glu": "B0GSVGRV56", "np": "B0H7P3C4D7"}


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealBooksGrowthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        cls.books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        inp = ROOT / "INPUT"
        cls.inp_state = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        src.close()
        dst.row_factory = sqlite3.Row
        from kdp_ads.config import CONFIG_DIR, load_mapping, load_settings
        from kdp_ads.pipeline import run_analysis, run_decisions
        lg, s = logging.getLogger("t"), load_settings()
        cls.runs, cls.dec = {}, {}
        for key, book in BOOKS.items():
            if not dst.execute("SELECT 1 FROM decision_runs WHERE book_id=?", (book,)).fetchone():
                continue
            ra = run_analysis(s, load_mapping(), CONFIG_DIR / "rules.json", book, Path(cls.tmp.name) / "out", lg, save_db=True, conn=dst, marketplace="US")
            rd = run_decisions(s, CONFIG_DIR / "rules.json", book, Path(cls.tmp.name) / "out", lg, ra["run_id"], save_db=True, conn=dst, marketplace="US")
            cls.runs[key] = rd["run_id"]
            cls.dec[key] = {(r["campaign_id"], r["target"]): dict(r) for r in dst.execute("SELECT * FROM decisions WHERE run_id=?", (rd["run_id"],))}
        dst.commit()
        dst.close()
        from data_access.repository import Repo
        cls.repo = Repo(str(cls.db))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def need(self, key):
        if key not in self.runs:
            self.skipTest(f"libro {key} non presente")

    def d(self, key, campaign, target):
        self.need(key)
        return self.dec[key][(campaign, target)]

    def page(self, key, which):
        old = os.environ.get("KDP_ADS_DB")
        os.environ["KDP_ADS_DB"] = str(self.db)
        try:
            f = ROOT / "DASHBOARD" / ("panoramica.py" if which == "home" else "pages/11_Suggerimenti_di_crescita.py")
            at = AppTest.from_file(str(f), default_timeout=120).run()
            tov.select(at, "Libro", BOOKS[key])
            tov.select(at, "Run decisionale", f"#{self.runs[key]}")
        finally:
            if old is None:
                os.environ.pop("KDP_ADS_DB", None)
            else:
                os.environ["KDP_ADS_DB"] = old
        return at, "\n".join(re.sub(r"<[^>]+>", "", t) for t, in_exp, _ in tov.page_text(at) if not in_exp)

    def overview(self, key):
        self.need(key)
        b = self.repo.book(BOOKS[key])
        b = {**b, **[m for m in self.repo.marketplaces(BOOKS[key]) if m["marketplace"] == "US"][0]}
        run = [r for r in self.repo.decision_runs(BOOKS[key]) if r["run_id"] == self.runs[key]][0]
        D, warns = self.repo.decisions(self.runs[key]), self.repo.warnings(run, None)
        opp = self.repo.search_term_opportunities(b, "US")
        o = tov.O.build_overview(D, b, {"actionable": 1}, warns, opp, tov.parse_delivery_detail, "$")
        return o, G.build_growth(D, opp["records"], b, G.config(self.repo.rules()), warns), opp, D

    # ---- Route 66
    def test_route_66_false_bid_downs_fixed(self):
        a = self.d("r66", "ESATTA", "route 66 100th anniversary")
        self.assertEqual((a["final_action"], a["operationally_actionable"]), ("WATCH", 0))
        self.assertIn(a["decision_rule"], ("C2D_ZERO_ORDER_UNDER_GATE_WATCH",))
        c = self.d("r66", "AUTO", "close-match")
        self.assertEqual((c["final_action"], c["operationally_actionable"]), ("WATCH", 0))
        self.assertEqual(self.d("r66", "FRASE", "route 66")["final_action"], "BID_DOWN_5_CANDIDATE")        # accettato
        self.assertEqual(self.d("r66", "AUTO", "loose-match")["final_action"], "HOLD")

    def test_route_66_overview_and_scaling(self):
        o, g, opp, D = self.overview("r66")
        self.assertEqual([c["title"] for c in o["actions"]], ["FRASE · route 66"])
        mon = [c["title"] for c in o["monitor"]]
        for t in ("ESATTA · route 66 100th anniversary", "AUTO · Corrispondenza stretta", "AUTO · Corrispondenza generica"):
            self.assertIn(t, mon)
        self.assertIn("ESATTA · route 66 map", [c["title"] for c in o["good"]])
        scaled = {(i["campaign"], i["target"]) for i in g["SCALE"]}
        self.assertIn(("ESATTA", "route 66 map"), scaled)
        self.assertIn(("COMPETITOR", 'asin="B0GRMRV9C1"'), scaled)
        self.assertIn(("ESATTA", "route 66 travel guide"), scaled)
        self.assertTrue(all(i["step_pct"] == 5 for i in g["SCALE"]))
        self.assertTrue(any("IMPOSSIBLE_SEARCH_TERM_METRICS" in w["text"] for w in self.repo.warnings(
            [r for r in self.repo.decision_runs(BOOKS["r66"]) if r["run_id"] == self.runs["r66"]][0], None)))

    def test_route_66_product_identities_and_target_recs_unchanged(self):
        self.need("r66")
        a, b = self.d("r66", "COMPETITOR", 'asin="B0GRMRV9C1"'), self.d("r66", "COMPETITOR", 'asin="0988924676"')
        self.assertNotEqual(a["action_key"], b["action_key"])
        self.assertEqual(a["lifetime_orders"], 14.0)

    # ---- Military Workout
    def test_military_workout(self):
        o, g, opp, D = self.overview("mil")
        self.assertEqual(o["kpi"]["actions"], 0)
        mon = [c["title"] for c in o["monitor"]]
        for t in ("AUTO · Corrispondenza generica", "ESATTA · military workout", "ESATTA · military workout book"):
            self.assertIn(t, mon)
        self.assertEqual([i for i in g["SCALE"] if "military workout" in str(i["target"])], [])
        self.assertEqual(len(o["delivery_shown"]), min(len(o["delivery"]), tov.O.MAX_DELIVERY_HOME))
        self.assertGreater(o["kpi"]["delivery"], tov.O.MAX_DELIVERY_HOME)                          # molte verifiche: la Home ne mostra solo le piu' rilevanti

    # ---- Military Calisthenics Women
    def test_military_calisthenics_women(self):
        o, g, opp, D = self.overview("milw")
        self.assertEqual((o["kpi"]["actions"], o["kpi"]["exact"]), (0, 0))
        self.assertIn("AUTO · Corrispondenza generica", [c["title"] for c in o["good"]])
        mon = [c["title"] for c in o["monitor"]]
        self.assertIn("FRASE · military calisthenics", mon)
        self.assertIn("AUTO · Corrispondenza stretta", mon)

    # ---- Glutes
    def test_glutes_funnel_and_card(self):
        o, g, opp, D = self.overview("glu")
        self.assertIn("AUTO · Corrispondenza generica", [c["title"] for c in o["monitor"]])
        by = {(r["campaign_id"], r["search_term"]): r["opportunity_type"] for r in opp["records"]}
        self.assertEqual(by[("AUTO", "glute book")], SUGGEST_PHRASE)
        self.assertNotIn("glute book", [i["term"] for i in o["exact"]])
        self.assertEqual(g["SCALE"], [])
        self.assertEqual(G.summary_lines(g), ["1 da testare in FRASE", "1 campagna da sviluppare"])
        at, text = self.page("glu", "home")
        self.assertFalse(at.exception)
        card = text.split("GUARDA ANCHE")[1].split("AVVISI DATI")[0]
        self.assertIn("Hai 2 opportunità di crescita", card)
        self.assertIn("1 da testare in FRASE", card)
        self.assertIn("1 campagna da sviluppare", card)
        self.assertNotIn("da scalare", card)                                                       # solo categorie con count > 0
        self.assertEqual([m.value for m in at.metric if m.label == "DA VALUTARE ORA"], ["0"])
        gat, gtext = self.page("glu", "growth")
        self.assertFalse(gat.exception)
        for need in ("1 · SCALA TARGET VINCENTI", "2 · AUTO → FRASE", "3 · FRASE → EXACT", "4 · SVILUPPA CAMPAGNE", "5 · NUOVI TARGET PRODOTTO"):
            self.assertIn(need, gtext)
        order = [gtext.index(x) for x in ("1 · SCALA", "2 · AUTO", "3 · FRASE", "4 · SVILUPPA", "5 · NUOVI")]
        self.assertEqual(order, sorted(order))
        self.assertIn("AGGIUNGI IN FRASE **glute book**", gtext)
        self.assertIn("Clic 4 · Ordini 1 · Spesa $1.70 · Vendite $19.99 · ACOS 8.5%", gtext)

    def test_home_card_for_a_book_without_growth_says_so(self):
        self.need("cal")
        at, text = self.page("cal", "home")
        card = text.split("GUARDA ANCHE")[1].split("AVVISI DATI")[0]
        self.assertIn("Al momento non ci sono suggerimenti di crescita affidabili.", card)

    def test_growth_page_shows_current_and_suggested_bid_for_scaling(self):
        self.need("r66")
        at, text = self.page("r66", "growth")
        self.assertFalse(at.exception)
        self.assertIn("ALZA OFFERTA 5%", text)
        self.assertIn("Offerta attuale $1.11 → suggerita **$1.17** (+5%)", text)

    # ---- regressioni sugli altri libri
    def test_regression_dry_mix(self):
        self.assertEqual(self.d("dry", "FRASE", "dry mix pantry cookbook")["final_action"], "BID_DOWN_15_CANDIDATE")
        r = self.d("dry", "FRASE", "dry mix recipe book")
        self.assertEqual((r["final_action"], r["bid_change_pct"]), ("BID_DOWN_5_CANDIDATE", -5.0))
        self.assertEqual(self.d("dry", "AUTO", "close-match")["final_action"], "WATCH")
        o, g, opp, D = self.overview("dry")
        self.assertEqual([i["term"] for i in o["exact"]], ["dry mix recipe book"])
        self.assertEqual([i["badge"] for i in o["negatives"]], ["VALUTA COME NEGATIVA ESATTA"])
        self.assertEqual(next(r for r in opp["records"] if r["search_term"] == "dry mix recipe book" and r["campaign_id"] == "AUTO")["opportunity_type"], PROMOTE)

    def test_regression_calisthenics(self):
        o, g, opp, D = self.overview("cal")
        self.assertEqual(o["kpi"]["actions"], 0)
        self.assertIn("AUTO · Complementi", [c["title"] for c in o["good"]])
        self.assertEqual(o["exact"], [])
        self.assertTrue(all((r["clicks"] or 0) >= 3 for r in opp["records"] if r["opportunity_type"] == PROMOTE))

    def test_regression_rv_camping(self):
        self.assertEqual(self.d("rv", "ESATTA", "rv camping")["final_action"], "BID_DOWN_5_CANDIDATE")
        self.assertEqual(self.d("rv", "FRASE", "camping books")["final_action"], "BID_DOWN_5_CANDIDATE")
        o, g, opp, D = self.overview("rv")
        mon = [c["title"] for c in o["monitor"]]
        for t in ("FRASE · rv travel book", "AUTO · Corrispondenza generica", "FRASE · rv camping"):
            self.assertIn(t, mon)
        self.assertEqual([i["term"] for i in o["exact"]], ["rv camping bible"])
        self.assertEqual((o["kpi"]["actions"], o["kpi"]["negatives"]), (2, 0))

    def test_regression_rare_coins(self):
        o, g, opp, D = self.overview("rare")
        self.assertEqual([i["term"] for i in o["exact"]], ["rare coin book"])
        self.assertIn("AUTO · Sostituti", [c["title"] for c in o["monitor"]])
        self.assertEqual([i["badge"] for i in o["promoted"]], ["PROMOSSA IN EXACT · ATTUALMENTE IN PAUSA"])
        a, x = self.d("rare", "PRODOTTO", 'asin="0794851630"'), self.d("rare", "PRODOTTO", 'asin-expanded="0794851630"')
        self.assertNotEqual(a["action_key"], x["action_key"])
        self.assertEqual((a["target_status"], x["target_status"]), ("ENABLED", "PAUSED"))

    def test_regression_national_parks(self):
        o, g, opp, D = self.overview("np")
        self.assertEqual([i["term"] for i in o["exact"]], ["national parks", "national park map", "national park guide"])
        self.assertEqual((o["kpi"]["actions"], o["kpi"]["conflicts"]), (1, 2))

    # ---- UI: nessun errore PyArrow, nessuna scrittura
    def test_pages_tables_are_arrow_serializable(self):
        for key, which in (("r66", "home"), ("r66", "growth"), ("glu", "growth")):
            at, _ = self.page(key, which)
            self.assertFalse(at.exception)
            for df in at.dataframe:
                pa.Table.from_pandas(df.value)

    def test_nothing_written(self):
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        inp = ROOT / "INPUT"
        now = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        self.assertEqual(now, self.inp_state)


if __name__ == "__main__":
    unittest.main()
