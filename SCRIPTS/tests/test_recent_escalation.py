"""FINESTRE DI SOSTEGNO (Search Term 65D, Targeting Lifetime) ed ESCALATION RECENTE sui casi reali: 7D = conferma, 30D = finestra decisionale, 65D = contesto, Lifetime = memoria.
Fixture sintetiche basate sui pattern reali (Rare Coins, Route 66, Twenty Women), flusso globale completo (import -> associazione -> analisi -> decisioni) in un workspace
temporaneo: mai il DB reale, mai books.json reale, nessuna rete, nessuna Amazon."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_reports as tg
from data_access import overview as O
from kdp_ads import escalation as ESC
from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads import registry
from kdp_ads.metrics import compute_metrics
from kdp_ads.opportunities import compute_opportunities, load_inputs, make_ctx
from kdp_ads.rules import BookCtx, load_rules
from ops import global_import as GI

RANGE30, RANGE7 = tg.RANGE30, tg.RANGE7
RANGE65 = "10 ago 2026 - 13 ott 2026"                  # 65 giorni
RANGE_LIFE = "1 gen 2025 - 13 ott 2026"
BOOK_RC, BOOK_R66, BOOK_TW = tg.BOOK_B, tg.BOOK_A, "B0TWENTYWM"                       # Rare Coins (royalty 11, pareggio 45,85%), Route 66 (9, 39,15%), Twenty Women (8, 40,02%)
BOOKS = {"books": tg.BOOKS["books"] + [{"book_id": BOOK_TW, "title": "Twenty Women Workouts Over 50", "active": True,
                                         "marketplaces": [{"marketplace": "US", "currency": "USD", "price": 19.99, "royalty_per_copy": 8.0, "target_acos": 35.0, "phase": "GROWTH", "active": True}]}]}

C_RC = tg.camp("555000111222333", "FRASE RARE COINS", "222", "rare coin")
C_RC2 = tg.camp("555000111222555", "FRASE RARE COINS 2", "222", "rare coin")
C_PR = tg.camp("555000111222444", "PRODOTTI RARE COINS", "222", "rare coin")
C_R66 = tg.camp("12345678901234567890", "ESATTA ROUTE66", "111", "ROUTE 66")
C_TW = tg.camp("777000111222333", "FRASE TWENTY WOMEN", "333", "twenty women")

T_COLLECT, T_PAUSED, T_ASIN, T_MAP, T_TW1, T_TW2 = "coin books for collectors", "coin price guide", 'asin="0794851630"', "route 66 map", "twenty women workouts", "twenty women fitness plan"


def tgr(c, target, match, tid, clicks, orders, spend, sales, rng, state="ENABLED", bid="0.50"):
    return tg.tg(c, target, match, tid, clicks, orders, spend, sales, rng, state=state, bid=bid)


def strow(c, term, target, match, tid, clicks, orders, spend, sales, rng):
    return tg.st_row(c, term, target, match, tid, clicks, orders, spend, sales, rng)


def targeting(rng, rows):
    return tg.csv_text(tg.TG_HEAD, rows)


def files(life=False, c65=False, w7=True):
    """Report globali del caso reale. Dati: A) Rare Coins 'coin books for collectors' 18 clic/17,59/0 ordini (7D: 9/8,59/0); B) ASIN 0794851630 target 30D 130 clic, 5 acquisti, 84,00 spesa,
    118,00 vendite (ACOS 71%), 7D 14 clic 0 acquisti, mentre il Search Term dell'ASIN mostra 32 clic/3 acquisti/ACOS 32%; C) Route 66 'route 66 map' storico fortissimo, 30D 14 clic/0 ordini;
    D) Twenty Women: acquisti ma ACOS 56% (F: stesso quadro ma con un Lifetime forte); E) lo stesso quadro di A su un target GIA' IN PAUSA."""
    t30 = [tgr(C_RC, T_COLLECT, "PHRASE", "2101", 18, 0, 17.59, 0.0, RANGE30), tgr(C_RC2, T_PAUSED, "PHRASE", "2201", 18, 0, 17.59, 0.0, RANGE30, state="PAUSED"),
           tgr(C_PR, T_ASIN, "TARGETING_EXPRESSION", "2102", 130, 5, 84.0, 118.0, RANGE30), tgr(C_R66, T_MAP, "EXACT", "1006", 14, 0, 15.16, 0.0, RANGE30),
           tgr(C_TW, T_TW1, "PHRASE", "3001", 40, 4, 36.0, 64.0, RANGE30), tgr(C_TW, T_TW2, "PHRASE", "3002", 40, 4, 36.0, 64.0, RANGE30)]
    t7 = [tgr(C_RC, T_COLLECT, "PHRASE", "2101", 9, 0, 8.59, 0.0, RANGE7), tgr(C_RC2, T_PAUSED, "PHRASE", "2201", 9, 0, 8.59, 0.0, RANGE7, state="PAUSED"),
          tgr(C_PR, T_ASIN, "TARGETING_EXPRESSION", "2102", 14, 0, 9.0, 0.0, RANGE7), tgr(C_R66, T_MAP, "EXACT", "1006", 6, 0, 5.0, 0.0, RANGE7),
          tgr(C_TW, T_TW1, "PHRASE", "3001", 12, 0, 9.0, 0.0, RANGE7), tgr(C_TW, T_TW2, "PHRASE", "3002", 12, 0, 9.0, 0.0, RANGE7)]
    s30 = [strow(C_RC, T_COLLECT, T_COLLECT, "PHRASE", "2101", 18, 0, 17.59, 0.0, RANGE30), strow(C_RC2, T_PAUSED, T_PAUSED, "PHRASE", "2201", 18, 0, 17.59, 0.0, RANGE30),
           strow(C_PR, "0794851630", T_ASIN, "TARGETING_EXPRESSION", "2102", 32, 3, 25.0, 77.97, RANGE30), strow(C_R66, T_MAP, T_MAP, "EXACT", "1006", 14, 0, 15.16, 0.0, RANGE30),
           strow(C_TW, T_TW1, T_TW1, "PHRASE", "3001", 40, 4, 36.0, 64.0, RANGE30), strow(C_TW, T_TW2, T_TW2, "PHRASE", "3002", 40, 4, 36.0, 64.0, RANGE30)]
    out = {"TARGETING_30D": ("t30.csv", targeting(RANGE30, t30)), "SEARCH_TERM_30D": ("s30.csv", tg.csv_text(tg.ST_HEAD, s30))}
    if w7:
        out["TARGETING_7D"] = ("t7.csv", targeting(RANGE7, t7))
    if c65:
        s65 = [strow(C_RC, T_COLLECT, T_COLLECT, "PHRASE", "2101", 30, 0, 28.0, 0.0, RANGE65), strow(C_TW, T_TW1, T_TW1, "PHRASE", "3001", 100, 8, 80.0, 400.0, RANGE65),
               strow(C_TW, T_TW2, T_TW2, "PHRASE", "3002", 60, 5, 50.0, 130.0, RANGE65)]
        out["SEARCH_TERM_65D"] = ("s65.csv", tg.csv_text(tg.ST_HEAD, s65))
    if life:
        tl = [tgr(C_R66, T_MAP, "EXACT", "1006", 80, 8, 50.0, 183.92, RANGE_LIFE), tgr(C_TW, T_TW2, "PHRASE", "3002", 200, 12, 100.0, 400.0, RANGE_LIFE),
              tgr(C_RC2, T_PAUSED, "PHRASE", "2201", 40, 3, 30.0, 100.0, RANGE_LIFE, state="ENABLED")]       # il Lifetime (mensile) dice ABILITATO, il 30D/7D piu' recenti dicono IN PAUSA
        out["TARGETING_LIFETIME"] = ("tl.csv", targeting(RANGE_LIFE, tl))
    return out


class Scenario(unittest.TestCase):
    """Un solo workspace per classe: import + associazione + analisi + decisioni (lento)."""
    LIFE, C65 = False, False

    @classmethod
    def setUpClass(cls):
        cls.base = tg.WsBase()
        cls.base.setUp()
        cls.ws, cls.root = cls.base.ws, cls.base.root
        (cls.root / "CONFIG" / "books.json").write_text(json.dumps(BOOKS), encoding="utf-8")
        cls.imp = GI.run_import(cls.ws, files(cls.LIFE, cls.C65), None, recalc=False)
        assert cls.imp.ok, cls.imp.errors
        for cid, book in (("555000111222333", BOOK_RC), ("555000111222555", BOOK_RC), ("555000111222444", BOOK_RC), ("12345678901234567890", BOOK_R66), ("777000111222333", BOOK_TW)):
            r = GI.associate(cls.ws, cid, book, recalc=False)
            assert r.ok, r.errors
        res = GI.recalculate(cls.ws, {(BOOK_RC, "US"), (BOOK_R66, "US"), (BOOK_TW, "US")})
        assert all(x["ok"] for x in res), res

    @classmethod
    def tearDownClass(cls):
        cls.base.tearDown()

    def q(self, sql, *p):
        return self.base.q(sql, *p)

    def decision(self, book, target):
        rows = self.q("SELECT * FROM decisions WHERE book_id=? AND target=? ORDER BY run_id DESC LIMIT 1", book, target)
        self.assertTrue(rows, f"nessuna decisione per {target}")
        return rows[0]


# ------------------------------------------------------------------ import 65D / Lifetime
class SupportWindowImportTests(Scenario):
    LIFE, C65 = True, True

    def test_65d_and_lifetime_slots_exist_and_the_six_global_files_import(self):
        self.assertEqual(set(GS.SLOTS), {"TARGETING_7D", "TARGETING_30D", "TARGETING_LIFETIME", "SEARCH_TERM_7D", "SEARCH_TERM_30D", "SEARCH_TERM_65D"})
        self.assertEqual(GS.SLOTS["SEARCH_TERM_65D"], ("SEARCH_TERM", "RECENT_65D"))
        self.assertEqual(GS.SLOTS["TARGETING_LIFETIME"], ("TARGETING", "LIFETIME"))
        self.assertTrue(self.imp.ok)
        labels = {(r["report_type"], r["window_label"]) for r in self.q("SELECT report_type, window_label FROM global_report_imports")}
        self.assertEqual(labels, {("TARGETING", "RECENT_30D"), ("TARGETING", "RECENT_7D"), ("TARGETING", "LIFETIME"), ("SEARCH_TERM", "RECENT_30D"), ("SEARCH_TERM", "RECENT_65D")})

    def test_65d_snapshot_dates_and_materialization(self):
        g = self.q("SELECT * FROM global_report_imports WHERE window_label='RECENT_65D'")[0]
        self.assertEqual((g["window_days"], g["date_from"], g["date_to"], g["period_id"]), (65, "2026-08-10", "2026-10-13", "2026-08-10__2026-10-13"))
        rows = self.q("SELECT report_scope, date_from, date_to, period_id FROM search_term_rows WHERE period_id='2026-08-10__2026-10-13'")
        self.assertTrue(rows)
        self.assertEqual({(r["report_scope"], r["date_from"], r["date_to"]) for r in rows}, {("DATE_RANGE", "2026-08-10", "2026-10-13")})

    def test_lifetime_is_stored_as_report_scope_lifetime_without_a_start_date(self):
        g = self.q("SELECT * FROM global_report_imports WHERE window_label='LIFETIME'")[0]
        self.assertEqual((g["window_days"], g["date_to"], g["period_id"]), (None, "2026-10-13", "LIFETIME__2026-10-13"))
        rows = self.q("SELECT report_scope, date_from, date_to, period_id, amazon_campaign_id FROM target_rows WHERE report_scope='LIFETIME'")
        self.assertEqual({(r["report_scope"], r["date_from"], r["date_to"], r["period_id"]) for r in rows}, {("LIFETIME", None, "2026-10-13", "LIFETIME__2026-10-13")})
        self.assertTrue(all(r["amazon_campaign_id"] for r in rows))

    def test_65d_does_not_duplicate_or_replace_the_30d_and_the_30d_stays_the_decisional_window(self):
        d = self.decision(BOOK_TW, T_TW1)
        self.assertEqual(d["tactical_window"], "RECENT_30D")                          # il 30D resta la finestra decisionale primaria
        self.assertEqual(d["recent_window"], "RECENT_65D")                            # il 65D e' il contesto intermedio
        self.assertEqual((d["tactical_clicks"], d["recent_clicks"]), (40.0, 100.0))   # due snapshot distinti, mai sommati
        self.assertEqual(self.q("SELECT COUNT(*) n FROM search_term_rows WHERE search_term=? AND period_id='2026-09-14__2026-10-13'", T_TW1)[0]["n"], 1)

    def test_lifetime_is_historical_memory_not_the_primary_window(self):
        d = self.decision(BOOK_R66, T_MAP)
        self.assertEqual((d["lifetime_orders"], d["lifetime_clicks"]), (8.0, 80.0))
        self.assertEqual(d["tactical_window"], "RECENT_30D")
        self.assertEqual(d["tactical_clicks"], 14.0)

    def test_missing_support_windows_never_block_the_import(self):
        base = tg.WsBase()
        base.setUp()
        try:
            r = GI.run_import(base.ws, files(False, False), None, recalc=False)
            self.assertTrue(r.ok, r.errors)
            self.assertEqual(r.data["imported"], 3)
            self.assertEqual(GS.SUPPORT_SLOTS, ("SEARCH_TERM_65D", "TARGETING_LIFETIME"))
        finally:
            base.tearDown()

    def test_slot_type_is_checked_for_the_new_slots(self):
        p = G.parse_global_report("a.csv", files(False, True)["SEARCH_TERM_65D"][1])
        c = self.base.db()
        try:
            pv = GS.preview_file(c, p, "RECENT_65D", registry.load_books(self.ws.books_path), None, None, "TARGETING_LIFETIME")
        finally:
            c.close()
        self.assertTrue(any("Targeting Lifetime" in x for x in pv["problems"]))


# ------------------------------------------------------------------ casi reali: escalation 0 ordini / product winner / pausa
class RealCasesTests(Scenario):
    LIFE, C65 = False, False

    def test_a_rare_coins_zero_orders_18_clicks_without_65d_is_minus_10_not_monitoring_and_not_a_pause(self):
        d = self.decision(BOOK_RC, T_COLLECT)
        self.assertEqual(d["final_action"], "BID_DOWN_10_CANDIDATE")                       # 15-19 clic, 7D conferma, nessun 65D: scala graduale, pausa non sbloccata
        self.assertEqual(d["decision_rule"], "R1_RECENT_ZERO_ORDER_ESCALATION")
        self.assertEqual(d["operationally_actionable"], 1)

    def test_b_the_asin_with_bad_parent_target_is_not_a_winner(self):
        d = self.decision(BOOK_RC, T_ASIN)
        self.assertTrue(d["final_action"].startswith("BID_DOWN") or d["final_action"] == "PAUSE_CANDIDATE", d["final_action"])
        c = __import__("kdp_ads.db", fromlist=["connect"]).connect(self.ws.db_path)
        try:
            book = registry.book_view(registry.load_books(self.ws.books_path)[BOOK_RC], "US")
            ctx = make_ctx({**book, "royalty_per_copy": 11.0, "break_even_acos": 45.85}, load_rules(self.ws.rules_path))
            data = load_inputs(c, BOOK_RC, "US", "US")
            res = compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], ctx)
        finally:
            c.close()
        prod = [r for r in res["records"] if r["opportunity_type"] == "WINNING_PRODUCT_TARGET"]
        self.assertEqual(len(prod), 1)
        r = prod[0]
        self.assertEqual((r["parent_validation"], r["parent_inefficient"]), ("FAIL", True))
        self.assertAlmostEqual(r["parent_acos_30d"], 71.19, places=1)
        self.assertEqual((r["parent_clicks_30d"], r["parent_clicks_7d"], r["parent_orders_7d"]), (130.0, 14.0, 0.0))
        self.assertIn("PARENT_TARGET_INEFFICIENT", r["flags"])
        items = O.product_winner_items(res["records"], [d], None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["badge"], "TERMINE/PRODOTTO INTERESSANTE, MA TARGET COMPLESSIVO INEFFICIENTE")
        self.assertFalse(items[0]["winner"])
        self.assertNotIn("VINCENTE", items[0]["badge"])
        self.assertIn("inefficiente", items[0]["reason"])

    def test_c_route_66_14_clicks_zero_orders_is_minus_5_even_without_lifetime(self):
        d = self.decision(BOOK_R66, T_MAP)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE report_scope='LIFETIME'")[0]["n"], 0)
        self.assertEqual((d["final_action"], d["decision_rule"]), ("BID_DOWN_5_CANDIDATE", "R1_RECENT_ZERO_ORDER_ESCALATION"))        # 10-14 clic: -5%

    def test_d_twenty_women_with_orders_but_high_acos_is_reduced_not_winning(self):
        d = self.decision(BOOK_TW, T_TW1)
        self.assertEqual(d["decision_rule"], "R3_HIGH_ACOS_WITH_ORDERS")
        self.assertTrue(d["final_action"].startswith("BID_DOWN"), d["final_action"])
        self.assertIn("HIGH_ACOS_WITH_ORDERS", d["flags"])
        self.assertNotEqual(d["final_action"], "HOLD")

    def test_e_already_paused_target_is_not_proposed_for_pause_again(self):
        d = self.decision(BOOK_RC, T_PAUSED)
        self.assertEqual((d["target_status"], d["final_action"], d["operationally_actionable"]), ("PAUSED", "HOLD_PAUSED_TARGET", 0))
        self.assertEqual(d["underlying_candidate_action"], "BID_DOWN_10_CANDIDATE")               # l'economia direbbe -10%: ma e' gia' in pausa (mai METTI IN PAUSA)
        book = {"book_id": BOOK_RC, "marketplace": "US", "target_acos": 35.0, "break_even_acos": 45.85, "royalty_per_copy": 11.0}
        ds = [dict(r) for r in self.q("SELECT * FROM decisions WHERE book_id=? AND run_id=(SELECT MAX(run_id) FROM decision_runs WHERE book_id=?)", BOOK_RC, BOOK_RC)]
        ov = O.build_overview(ds, book, {"actionable": 1}, [], None)
        self.assertNotIn(T_PAUSED, [c["decision"]["target"] for c in ov["actions"]])
        self.assertIn(T_PAUSED, [c["decision"]["target"] for c in ov["paused"]])
        self.assertEqual({c["badge"] for c in ov["paused"] if c["decision"]["target"] == T_PAUSED}, {"GIÀ IN PAUSA"})
        self.assertIn(T_COLLECT, [c["decision"]["target"] for c in ov["actions"]])           # l'altro target resta da fare

    def test_without_the_7d_row_the_escalation_never_fires(self):
        base = tg.WsBase()
        base.setUp()
        try:
            (base.root / "CONFIG" / "books.json").write_text(json.dumps(BOOKS), encoding="utf-8")
            GI.run_import(base.ws, files(False, False, w7=False), None, recalc=False)
            for cid in ("555000111222333", "555000111222555", "555000111222444"):
                GI.associate(base.ws, cid, BOOK_RC, recalc=False)
            GI.recalculate(base.ws, {(BOOK_RC, "US")})
            d = base.q("SELECT decision_rule, final_action FROM decisions WHERE target=? ORDER BY run_id DESC LIMIT 1", T_COLLECT)[0]
            self.assertFalse(d["decision_rule"].startswith("R1"), d["decision_rule"])        # 7D non disponibile: nessuna conferma, nessuna escalation
        finally:
            base.tearDown()


class LifetimeAndContextTests(Scenario):
    LIFE, C65 = True, True

    def test_c_route_66_with_strong_lifetime_gets_a_prudent_cut_never_a_pause(self):
        d = self.decision(BOOK_R66, T_MAP)
        self.assertEqual(d["lifetime_orders"], 8.0)
        self.assertEqual((d["final_action"], d["decision_rule"]), ("BID_DOWN_5_CANDIDATE", "R1_RECENT_ZERO_ORDER_ESCALATION"))
        self.assertEqual(d["operationally_actionable"], 1)

    def test_a_rare_coins_with_a_real_65d_that_confirms_unlocks_the_pause(self):
        d = self.decision(BOOK_RC, T_COLLECT)                 # 18 clic, 0 acquisti nel 30D + 65D con 30 clic e 0 acquisti
        self.assertEqual((d["final_action"], d["decision_rule"]), ("PAUSE_CANDIDATE", "R1P_RECENT_ZERO_ORDER_PAUSE"))
        self.assertIn("65D conferma un problema persistente", d["reason"])

    def test_lifetime_does_not_override_a_confirmed_recent_decline(self):
        with_life = self.decision(BOOK_TW, T_TW2)           # Lifetime fortissimo: 12 acquisti, ACOS 25%
        self.assertEqual((with_life["lifetime_orders"], with_life["lifetime_acos"]), (12.0, 25.0))
        self.assertEqual(with_life["decision_rule"], "R3_HIGH_ACOS_WITH_ORDERS")
        self.assertEqual(with_life["final_action"], "BID_DOWN_10_CANDIDATE")                 # 30D molto negativo + 7D conferma + campione sufficiente
        self.assertNotIn("PAUSE", with_life["final_action"])                                  # ...ma lo storico forte impedisce la pausa
        self.assertNotEqual(with_life["final_action"], "HOLD")

    def test_a_monthly_lifetime_never_hides_the_fresher_paused_status_of_the_weekly_reports(self):
        d = self.decision(BOOK_RC, T_PAUSED)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE report_scope='LIFETIME' AND target=?", T_PAUSED)[0]["n"], 1)
        self.assertEqual((d["target_status"], d["final_action"], d["operationally_actionable"]), ("PAUSED", "HOLD_PAUSED_TARGET", 0))        # nessuna nuova PAUSA proposta

    def test_65d_confirms_a_persistent_problem_but_never_acts_alone(self):
        d = self.decision(BOOK_RC, T_COLLECT)
        self.assertEqual(d["recent_window"], "RECENT_65D")
        self.assertEqual((d["tactical_clicks"], d["recent_clicks"]), (18.0, 30.0))
        self.assertIn("65D conferma un problema persistente", d["reason"])
        # senza i dati 30D/7D il solo 65D non produce nessuna escalation (verificato a livello di funzione pura qui sotto)

    def test_65d_good_means_temporary_dip_and_never_a_pause(self):
        d = self.decision(BOOK_TW, T_TW1)                    # 30D ACOS 56%, ma 65D ottimo (8 acquisti, ACOS 15%): calo temporaneo
        self.assertTrue(d["final_action"].startswith("BID_DOWN"), d["final_action"])
        self.assertNotIn(d["final_action"], ("PAUSE_CANDIDATE", "BID_DOWN_15_CANDIDATE"))
        self.assertEqual(d["recent_window"], "RECENT_65D")


# ------------------------------------------------------------------ funzioni pure
class EscalationUnitTests(unittest.TestCase):
    CTX = BookCtx("B", "Rare Coins", "US", "USD", 23.99, 11.0, 35.0, 45.85, "GROWTH", load_rules(ROOT / "CONFIG" / "rules.json"))
    CFG = ESC.config(CTX.rules)

    def m(self, clicks, orders, spend, sales=None):
        return compute_metrics(clicks * 40, clicks, spend, orders, orders * 22.0 if sales is None else sales, 11.0)

    def ev(self, T, L=None, C=None, m7=None):
        return ESC.evaluate(T, L, C, m7, self.CTX, self.CFG)

    def test_config_has_defaults_and_rules_json_documents_the_section(self):
        self.assertTrue(self.CFG["enabled"])
        self.assertIn("recent_escalation", json.loads((ROOT / "CONFIG" / "rules.json").read_text(encoding="utf-8")))

    def test_zero_order_ladder_is_graduated_and_relative_to_royalty(self):
        self.assertIsNone(self.ev(self.m(12, 0, 10.0), m7=self.m(6, 0, 4.0)))                    # spesa 10 < 1x royalty (11): niente
        self.assertEqual(self.ev(self.m(12, 0, 12.0), m7=self.m(6, 0, 4.0))["step"], 5)         # 10-14 clic: -5%
        self.assertEqual(self.ev(self.m(14, 0, 40.0), m7=self.m(6, 0, 4.0))["step"], 5)         # la spesa alta non salta la scala
        b = self.ev(self.m(16, 0, 20.0), m7=self.m(6, 0, 4.0))
        self.assertEqual((b["step"], b["rule"]), (10, "R1_RECENT_ZERO_ORDER_ESCALATION"))        # 15-19 clic: -10%
        self.assertEqual(self.ev(self.m(18, 0, 17.59), m7=self.m(9, 0, 8.59))["step"], 10)      # senza 65D la pausa NON e' sbloccata
        w = self.ev(self.m(24, 0, 30.0), m7=self.m(9, 0, 8.59))
        self.assertEqual(w["step"], 10)                                                          # nemmeno con 20+ clic
        self.assertIn("manca la conferma del 65D", w["logic"])

    def test_the_7d_must_confirm(self):
        T = self.m(18, 0, 17.59)
        self.assertIsNone(self.ev(T, m7=None))                                                   # nessuna riga 7D
        self.assertIsNone(self.ev(T, m7=self.m(2, 0, 1.0)))                                      # 7D con troppi pochi clic
        self.assertIsNone(self.ev(T, m7=self.m(9, 1, 8.0)))                                      # 7D con un acquisto: non conferma

    def test_strong_history_caps_the_step_and_forbids_the_pause(self):
        L = self.m(80, 8, 50.0, 183.92)                                                          # ACOS 27%, 8 acquisti
        bad65 = self.m(40, 0, 40.0, 0.0)
        self.assertEqual(self.ev(self.m(14, 0, 15.0), L=L, m7=self.m(9, 0, 8.59))["step"], 5)   # 10-14 clic: -5%
        self.assertEqual(self.ev(self.m(18, 0, 17.59), L=L, m7=self.m(9, 0, 8.59))["step"], 10)
        self.assertEqual(self.ev(self.m(24, 0, 30.0), L=L, C=bad65, m7=self.m(9, 0, 8.59))["step"], 10)      # anche con 65D che conferma: storico forte => mai pausa
        weak = self.ev(self.m(24, 0, 30.0), L=self.m(30, 1, 40.0, 22.0), C=bad65, m7=self.m(9, 0, 8.59))
        self.assertEqual(weak["step"], 100)                                                      # storico non forte + 65D che conferma: pausa sbloccata

    def test_65d_context_persistent_vs_temporary_vs_missing(self):
        T, m7 = self.m(18, 0, 17.59), self.m(9, 0, 8.59)
        good65 = self.m(60, 6, 50.0, 220.0)                                                      # ACOS 22,7%, 6 acquisti: calo temporaneo
        bad65 = self.m(40, 0, 40.0, 0.0)
        self.assertEqual(ESC.context_state(good65, self.CTX, self.CFG), "TEMPORARY")
        self.assertEqual(ESC.context_state(bad65, self.CTX, self.CFG), "PERSISTENT")
        self.assertEqual(ESC.context_state(None, self.CTX, self.CFG), "UNKNOWN")
        self.assertEqual(self.ev(T, C=good65, m7=m7)["step"], 5)                                 # calo temporaneo: massimo -5%
        self.assertEqual(self.ev(T, C=None, m7=m7)["step"], 10)                                  # 65D mancante: conservativo, il motore funziona
        self.assertEqual(self.ev(T, C=bad65, m7=m7)["step"], 100)                                # 65D che conferma: puo' sbloccare la pausa gia' da 15 clic
        self.assertEqual(self.ev(self.m(12, 0, 12.0), C=bad65, m7=m7)["step"], 5)                # ma sotto i 15 clic nemmeno il 65D basta
        self.assertIsNone(self.ev(None, C=bad65, m7=m7))                                         # il 65D da solo non reagisce mai

    def test_one_or_two_orders_with_high_acos_prefer_minus_5(self):
        m7 = self.m(12, 0, 10.0)
        one = self.ev(self.m(16, 1, 20.2, 27.6), m7=self.m(9, 0, 11.6))                          # 1 acquisto, ACOS 73%, 16 clic
        self.assertEqual((one["step"], one["rule"]), (5, "R3_HIGH_ACOS_WITH_ORDERS"))
        two = self.ev(self.m(39, 2, 22.5, 34.0), m7=self.m(8, 0, 5.0))                           # 2 acquisti, ACOS 66%, 39 clic: campione ancora limitato
        self.assertEqual(two["step"], 5)
        big = self.ev(self.m(60, 2, 60.0, 90.0), m7=m7)                                          # 60 clic, ACOS 66,7% (>= 1,3x pareggio), 7D a 0 acquisti con 12 clic
        self.assertEqual(big["step"], 10)
        self.assertEqual(self.ev(self.m(60, 2, 60.0, 90.0), m7=self.m(12, 0, 10.0))["step"], 10)
        self.assertEqual(self.ev(self.m(60, 2, 60.0, 90.0), m7=self.m(5, 0, 4.0))["step"], 5)    # 7D con pochi clic: niente -10%
        self.assertEqual(self.ev(self.m(60, 3, 60.0, 90.0), m7=m7)["step"], 10)                  # 3 acquisti: fuori dal campione limitato (fasce ACOS del libro)
        self.assertNotEqual(self.ev(self.m(60, 1, 120.0, 100.0), m7=m7)["step"], 100)            # 1 acquisto: la pausa chiede il doppio dei clic

    def test_orders_with_very_high_acos_are_reduced_and_never_a_winner(self):
        T = self.m(130, 5, 84.0, 118.0)                                                          # ACOS 71%
        r = self.ev(T, m7=self.m(14, 0, 9.0))
        self.assertEqual((r["step"], r["rule"]), (10, "R3_HIGH_ACOS_WITH_ORDERS"))
        self.assertIsNone(self.ev(T, m7=self.m(14, 2, 8.0, 80.0)))                               # il 7D recupera (ACOS 10%): nessuna escalation
        self.assertIsNone(self.ev(self.m(130, 5, 60.0, 150.0), m7=self.m(14, 0, 9.0)))           # ACOS 40% < 1,2x pareggio: non e' «molto alto»

    def test_pause_with_orders_only_with_strong_evidence(self):
        T = self.m(80, 4, 90.0, 80.0)                                                            # ACOS 112% >= 2x pareggio, 80 clic
        r = self.ev(T, m7=self.m(12, 0, 10.0))
        self.assertEqual(r["step"], 100)
        self.assertEqual(self.ev(T, m7=self.m(6, 0, 5.0))["step"], 10)                           # 7D con pochi clic: niente pausa
        self.assertEqual(self.ev(T, L=self.m(100, 10, 60.0, 300.0), m7=self.m(12, 0, 10.0))["step"], 10)    # storico forte: -10 e basta

    def test_disabled_flag_turns_it_off(self):
        self.assertIsNone(ESC.evaluate(self.m(18, 0, 17.59), None, None, self.m(9, 0, 8.59), self.CTX, {**self.CFG, "enabled": False}))


# ------------------------------------------------------------------ Lifetime ricorrente (ottobre, novembre, ...) e gate del target padre su campagne storiche
RANGE_LIFE2 = "1 gen 2025 - 20 ott 2026"


class RecurringLifetimeTests(Scenario):
    LIFE, C65 = True, False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tl2 = [tgr(C_R66, T_MAP, "EXACT", "1006", 95, 9, 60.0, 206.91, RANGE_LIFE2)]
        cls.second = GI.run_import(cls.ws, {"TARGETING_LIFETIME": ("tl_nov.csv", targeting(RANGE_LIFE2, tl2))}, None, recalc=False)
        assert cls.second.ok, cls.second.errors
        GI.recalculate(cls.ws, {(BOOK_R66, "US")})

    def test_every_monthly_lifetime_is_kept_for_audit(self):
        ids = {r["period_id"] for r in self.q("SELECT period_id FROM imports WHERE report_type='TARGETING' AND report_scope='LIFETIME' AND book_id=?", BOOK_R66)}
        self.assertEqual(ids, {"LIFETIME__2026-10-13", "LIFETIME__2026-10-20"})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports WHERE window_label='LIFETIME' AND status='ACTIVE'")[0]["n"], 2)

    def test_the_most_recent_lifetime_is_the_baseline_and_they_are_never_summed(self):
        d = self.decision(BOOK_R66, T_MAP)
        self.assertEqual((d["lifetime_clicks"], d["lifetime_orders"]), (95.0, 9.0))            # solo il piu' recente: 80+95 o 8+9 sarebbe una somma

    def test_more_than_one_lifetime_is_not_a_warning(self):
        runs = self.q("SELECT warnings_json FROM decision_runs WHERE book_id=? ORDER BY run_id DESC LIMIT 1", BOOK_R66)
        self.assertFalse([w for w in json.loads(runs[0]["warnings_json"] or "[]") if "snapshot lifetime" in w])
        an = self.q("SELECT warnings_json FROM analysis_runs WHERE book_id=? ORDER BY run_id DESC LIMIT 1", BOOK_R66)
        self.assertFalse([w for w in json.loads(an[0]["warnings_json"] or "[]") if "lifetime" in w.lower() and "snapshot" in w.lower()])

    def test_same_date_lifetime_with_different_data_is_a_real_revision_not_a_silent_sum(self):
        tl3 = [tgr(C_R66, T_MAP, "EXACT", "1006", 99, 9, 61.0, 206.91, RANGE_LIFE2)]
        r = GI.run_import(self.ws, {"TARGETING_LIFETIME": ("tl_nov_v2.csv", targeting(RANGE_LIFE2, tl3))}, None, recalc=False)
        self.assertFalse(r.ok)
        self.assertEqual(r.data["revisions_pending"], ["TARGETING_LIFETIME"])                   # serve la conferma esplicita: nessun doppio conteggio
        self.assertEqual(self.q("SELECT COUNT(*) n FROM imports WHERE report_scope='LIFETIME' AND book_id=?", BOOK_R66)[0]["n"], 2)


class ParentGateAcrossCampaignsTests(unittest.TestCase):
    """Il padre si giudica sull'ULTIMO 30D/7D del libro, sommando le campagne che hanno lo stesso target: una campagna storica ferma a un periodo vecchio non fa passare il gate."""
    CTX = BookCtx("B", "Rare Coins", "US", "USD", 23.99, 11.0, 35.0, 45.85, "GROWTH", load_rules(ROOT / "CONFIG" / "rules.json"))
    CFG = {"product_parent_min_clicks": 10, "product_parent_recent_min_clicks": 10}

    def row(self, cid, d_from, d_to, clicks, orders, spend, sales, target=T_ASIN):
        return {"campaign_id": cid, "target": target, "report_scope": "DATE_RANGE", "date_from": d_from, "date_to": d_to, "period_id": f"{d_from}__{d_to}", "impressions": clicks * 40,
                "clicks": clicks, "orders": orders, "spend": spend, "sales": sales}

    def test_stale_legacy_campaign_cannot_validate_a_target_that_is_bad_in_the_live_campaign(self):
        from kdp_ads.opportunities import parent_validation
        rows = [self.row("PRODOTTO", "2026-09-06", "2026-10-05", 43, 3, 30.28, 71.97),                      # storica: 43 clic, ACOS 42% (vecchia)
                self.row("PRODOTTI", "2026-10-05", "2026-11-03", 129, 5, 84.3, 118.6),                       # viva: ACOS 71%
                self.row("PRODOTTI", "2026-10-28", "2026-11-03", 14, 0, 8.05, 0.0)]
        pv = parent_validation(rows, "PRODOTTO", T_ASIN, "2026-09-06__2026-10-05", self.CTX, self.CFG)
        self.assertEqual(pv["state"], "FAIL")
        self.assertAlmostEqual(pv["m30"].acos, 71.1, places=1)
        self.assertEqual((pv["m30"].clicks, pv["m7"].clicks, pv["m7"].orders), (129, 14, 0))

    def test_good_parent_passes_and_other_targets_are_ignored(self):
        from kdp_ads.opportunities import parent_validation
        rows = [self.row("PRODOTTI", "2026-10-05", "2026-11-03", 60, 6, 40.0, 140.0), self.row("PRODOTTI", "2026-10-05", "2026-11-03", 200, 0, 150.0, 0.0, target='asin="OTHER"')]
        self.assertEqual(parent_validation(rows, "PRODOTTI", T_ASIN, None, self.CTX, self.CFG)["state"], "PASS")

    def test_no_data_for_the_parent_is_unverified_not_failed(self):
        from kdp_ads.opportunities import parent_validation
        self.assertEqual(parent_validation([], "X", T_ASIN, None, self.CTX, self.CFG)["state"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
