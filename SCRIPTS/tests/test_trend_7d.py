"""TREND OVERLAY 7D (Lifetime + 30D + 7D): supporto snapshot SHORT_TREND, evidenza 7D, trend_state, effetti sulle decisioni, regressioni Route 66 / Rare Coins
(fixture che riproducono i comportamenti reali). Il 30D resta la finestra decisionale; il 7D e' un overlay e non decide mai da solo."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_importer as ti
import test_rules as tr
import test_snapshots as ts
from kdp_ads import trend as TR, windows as win
from kdp_ads.analysis import analyze_book
from kdp_ads.decision import build_decisions
from kdp_ads.decisions_report import save_decisions_to_db
from kdp_ads.report import save_to_db
from kdp_ads.rules import load_rules
from kdp_ads.snapshots import window_label, window_role

BOOK = "1805566563"
LIFE = ("LIFETIME", None, "2026-10-12")
D30 = ("DATE_RANGE", "2026-09-13", "2026-10-12")
D7 = ("DATE_RANGE", "2026-10-06", "2026-10-12")
CFG = load_rules(tr.RULES_PATH)
from kdp_ads.registry import break_even_calc  # noqa: E402
PRICE, ROYALTY, TARGET = 19.99, 8.55, 35.0
BE = break_even_calc(PRICE, ROYALTY)                         # = royalty / prezzo x 100: mai hardcodato
ROUTE66 = {**ti.PILOT, "price": 22.99, "royalty_per_copy": 9.00}          # Route 66 Day by Day: pareggio ~39.15%
RARE = {**ti.PILOT, "price": 23.99, "royalty_per_copy": 11.00}            # Rare Coins Book for Beginners: pareggio ~45.85%


def row(kw, clicks, orders, acos, price=PRICE):
    """(kw, clic, ordini, spesa, vendite) con l'ACOS richiesto; senza ordini la spesa e' clic x 0.60."""
    sales = orders * price
    spend = sales * acos / 100.0 if orders else clicks * 0.60
    return (kw, clicks, orders, round(spend, 2), round(sales, 2))


def zero_click_target_csv(kw, other):
    """Report Target 7D in cui il target e' PRESENTE con impressioni ma 0 clic (come negli export reali Amazon)."""
    base = tr.it_rows_target([other]).rstrip(chr(10))
    return base + chr(10) + f'ABILITATO,{kw},Esatta,Attivo,1.00,40,0,"0,0000",0.00,0.00,0,0.00,' + chr(10)


class TBase(ts.SBase):
    BOOKDEF = ti.PILOT
    UNIT_PRICE = PRICE

    def setUp(self):
        ti.Base.setUp(self)
        self.add_book(self.BOOKDEF)

    def scenario(self, cid, ctype, life, d30, d7=None, kw="kw", extra7=True, st=None):
        """life/d30/d7 = (clic, ordini, acos). d7 None = nessuno snapshot 7D. d7 'ABSENT' = snapshot 7D presente ma SENZA la riga del target (dato mancante, NON zero traffico).
        d7 'ZERO_CLICKS' = report 7D che CONTIENE la riga del target con 0 clic (zero traffico reale)."""
        r = lambda k, *a: row(k, *a, price=self.UNIT_PRICE)                                        # noqa: E731
        specs = [("t_life.csv", tr.it_rows_target([r(kw, *life)]), "TARGETING", LIFE), ("t30.csv", tr.it_rows_target([r(kw, *d30)]), "TARGETING", D30)]
        if d7 == "ABSENT":
            specs.append(("t7.csv", tr.it_rows_target([r("other kw", 12, 1, 30)]), "TARGETING", D7))
        elif d7 == "ZERO_CLICKS":
            specs.append(("t7.csv", zero_click_target_csv(kw, r("other kw", 12, 1, 30)), "TARGETING", D7))
        elif d7:
            rows7 = [r(kw, *d7)] + ([r("other kw", 12, 1, 30)] if extra7 else [])
            specs.append(("t7.csv", tr.it_rows_target(rows7), "TARGETING", D7))
        if st:
            specs.append(("s30.csv", tr.it_rows_st([(kw, kw) + r(kw, *st)[1:]]), "SEARCH_TERM", D30))
        self.snap_campaign(cid, ctype, specs)

    def go(self):
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [(r.filename, r.problems) for r in res])
        an = analyze_book(self.conn, self.settings, self.mapping, tr.RULES_PATH, BOOK, self.logger)
        run = save_to_db(self.conn, an, "a", "b", "c", an.ctx.rules)
        return build_decisions(self.conn, self.settings, tr.RULES_PATH, BOOK, run)

    def decide(self, **kw):
        self.scenario(kw.pop("cid", "C_PHRASE"), kw.pop("ctype", "PHRASE"), **kw)
        ds = self.go()
        return self.dec(ds, kw.get("kw", "kw"), "C_PHRASE" if True else None) if False else ds


def one(ds, campaign="C_PHRASE", target="kw"):
    m = [d for d in ds.decisions if d["campaign_id"] == campaign and d["target"] == target and "negterm" not in d["action_key"]]
    assert len(m) == 1, [d["action_key"] for d in ds.decisions]
    return m[0]


class WithAndWithout(TBase):
    """Esegue lo STESSO scenario senza 7D (prima) e con 7D (dopo) su due database separati."""

    def before_after(self, life, d30, d7, **kw):
        self.scenario("C_PHRASE", kw.get("ctype", "PHRASE"), life, d30, None, kw=kw.get("kw", "kw"), st=kw.get("st"))
        before = one(self.go(), target=kw.get("kw", "kw"))
        self.tearDown()
        self.setUp()
        self.scenario("C_PHRASE", kw.get("ctype", "PHRASE"), life, d30, d7, kw=kw.get("kw", "kw"), st=kw.get("st"))
        after = one(self.go(), target=kw.get("kw", "kw"))
        return before, after


# ------------------------------------------------------------------ evidenza e stato (funzioni pure)
class EvidenceTests(unittest.TestCase):
    def test_evidence_classes_and_configurable_thresholds(self):
        c = TR.config(CFG)
        self.assertEqual((c["strong_min_clicks"], c["strong_spend_royalty_mult"], c["medium_min_clicks"]), (10, 1.0, 5))      # da rules.json
        e = lambda clicks, spend=0: TR.evidence_7d(clicks, spend, ROYALTY, c)                                                    # noqa: E731
        self.assertEqual([e(0), e(1), e(4), e(5), e(9), e(10), e(60)], ["NO_TRAFFIC", "LOW", "LOW", "MEDIUM", "MEDIUM", "STRONG", "STRONG"])
        self.assertEqual(e(3, ROYALTY), "STRONG")                                   # spesa >= 1 royalty anche con pochi clic
        self.assertEqual(e(3, ROYALTY - 0.01), "LOW")
        self.assertEqual(e(0, 50), "NO_TRAFFIC")                                    # 0 clic = nessun traffico
        c2 = TR.config({"trend_7d": {"strong_min_clicks": 20, "medium_min_clicks": 8}})
        self.assertEqual([TR.evidence_7d(x, 0, ROYALTY, c2) for x in (7, 8, 19, 20)], ["LOW", "MEDIUM", "MEDIUM", "STRONG"])

    def test_trend_state_matrix(self):
        cs = lambda ev, acos, orders, weak, pause=False: TR.classify_trend(ev, acos, orders, weak, TARGET, pause)[0]             # noqa: E731
        self.assertEqual(cs("NO_TRAFFIC", None, 0, True), "NO_TRAFFIC_7D")
        self.assertEqual(cs("STRONG", 85, 1, True), "CONFIRMED_DECLINE")
        self.assertEqual(cs("STRONG", None, 0, True), "CONFIRMED_DECLINE")           # zero-order con evidenza forte
        self.assertEqual(cs("STRONG", 19, 2, True), "RECENT_RECOVERY")
        self.assertEqual(cs("STRONG", 19, 2, False), "STABLE")
        self.assertEqual(cs("STRONG", 85, 1, False), "WATCH_RECENT")                 # 30D sano: il 7D da solo non dichiara un peggioramento confermato
        self.assertEqual(cs("MEDIUM", 85, 1, True), "WATCH_RECENT")                  # 30D debole + 7D limitato: monitora
        self.assertEqual(cs("LOW", None, 0, True), "WATCH_RECENT")
        self.assertEqual(cs("LOW", None, 0, False), "INSUFFICIENT_7D")
        self.assertIsNone(cs("STRONG", 85, 1, True, pause=True))                      # storico gia' da pausa: il 7D non aggiunge nulla
        self.assertEqual(set(TR.STATES), {"STABLE", "CONFIRMED_DECLINE", "RECENT_RECOVERY", "WATCH_RECENT", "INSUFFICIENT_7D", "NO_TRAFFIC_7D", "MISSING_7D"})

    def test_weak_30d(self):
        self.assertTrue(TR.weak_30d(42, 5, 60, 0, "HOLD", TARGET))
        self.assertFalse(TR.weak_30d(30, 5, 60, 0, "HOLD", TARGET))
        self.assertTrue(TR.weak_30d(30, 5, 60, 5, "BID_DOWN_5_CANDIDATE", TARGET))
        self.assertTrue(TR.weak_30d(None, 0, 12, 0, "WATCH", TARGET))
        self.assertFalse(TR.weak_30d(None, 0, 12, 0, "HOLD", TARGET))


class WindowSupportTests(TBase):
    def test_7d_role_label_and_never_tactical(self):
        s = CFG["snapshots"]
        self.assertEqual(s["short_trend_max_days"], 10)
        self.assertEqual([window_role(*w, s) for w in (LIFE, D30, D7)], ["HISTORICAL_CONTEXT", "TACTICAL", "SHORT_TREND"])
        self.assertEqual(window_label(*D7), "RECENT_7D")
        self.assertEqual(window_role("DATE_RANGE", "2026-09-03", "2026-09-30", s), "TACTICAL")             # il 28D resta tattico
        self.assertEqual(window_role("DATE_RANGE", "2026-07-28", "2026-09-30", s), "RECENT_CONTEXT")      # il 65D resta contesto

    def test_7d_is_not_the_tactical_or_comparable_period_even_if_newer(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 30), (20, 3, 25), st=(60, 5, 30))
        self.go()
        recs = [dict(r) for r in self.conn.execute("SELECT * FROM recommendations")]
        cc = win.campaign_context(recs, CFG["snapshots"])["C_PHRASE"]
        self.assertEqual(cc["tactical_pid"], "2026-09-13__2026-10-12")                                    # il 30D, non il 7D
        self.assertEqual(cc["trend_pid"], "2026-10-06__2026-10-12")
        self.assertNotIn("2026-10-06__2026-10-12", cc["comparable"])
        self.assertEqual({r["window_role"] for r in recs if r["period_id"] == "2026-10-06__2026-10-12"}, {"SHORT_TREND"})

    def test_7d_rows_do_not_change_30d_evidence_signals_or_accounting(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 30), (20, 3, 25))
        ds = self.go()
        d = one(ds)
        self.assertEqual((d["tactical_window"], d["tactical_clicks"], d["tactical_orders"]), ("RECENT_30D", 60, 5))
        self.assertEqual(d["trend_period"], "2026-10-06__2026-10-12")
        acc = ds.accounting
        self.assertEqual(acc["trend_7d_rows"], 2)                                                      # kw + other kw
        self.assertEqual(acc["evidence_rows_total"], acc["analytic_recommendations"])                  # nessuna riga persa o contata due volte
        self.assertNotEqual(d["recent_vs_historical_signal"], None)
        other = [x for x in ds.decisions if x["target"] == "other kw"]
        self.assertEqual(other, [])                                                                     # il solo 7D non crea mai una decisione

    def test_without_7d_snapshot_nothing_changes_and_state_is_null(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 42))
        d = one(self.go())
        self.assertIsNone(d["trend_state"])
        self.assertIsNone(d["trend_clicks"])

    def test_trend_columns_are_persisted(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 42), (24, 1, 85))
        ds = self.go()
        save_decisions_to_db(self.conn, ds, "a.csv", "a.md")
        r = self.conn.execute("SELECT trend_state, trend_evidence, trend_clicks, trend_orders, trend_acos FROM decisions WHERE target='kw'").fetchone()
        self.assertEqual((r[0], r[1], r[2], r[3]), ("CONFIRMED_DECLINE", "STRONG", 24, 1))
        self.assertGreater(r[4], 80)

    def test_7d_search_term_snapshot_does_not_alter_opportunities(self):
        from kdp_ads.opportunities import compute_opportunities
        from kdp_ads.opportunities import load_inputs, make_ctx
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 30), None, st=(60, 5, 30))
        self.go()
        books = ti.registry.load_books(self.settings["books_path"])
        ctx = make_ctx({**books[BOOK], "marketplace": "US", "royalty_per_copy": ROYALTY, "target_acos": TARGET, "break_even_acos": BE}, CFG)
        data = load_inputs(self.conn, BOOK, "US", "US")
        base = compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], ctx)
        extra = dict(data["st_rows"][0], id=999999, period_id="2026-10-06__2026-10-12", date_from="2026-10-06", date_to="2026-10-12", clicks=40.0, orders=9.0,
                     spend=20.0, sales=180.0)
        with7 = compute_opportunities(data["st_rows"] + [extra], data["target_rows"], data["campaigns"], ctx)
        strip = lambda o: [(r["search_term"], r["opportunity_type"], r["clicks"], r["orders"]) for r in o["records"]]            # noqa: E731
        self.assertEqual(strip(base), strip(with7))                                                                                # il 7D non promuove ne' nega nulla


# ------------------------------------------------------------------ effetti sulle decisioni
class ScenarioTests(WithAndWithout):
    def test_recovery_blocks_a_bid_down_that_only_comes_from_the_30d(self):
        # storico sano, 30D molto sopra l'obiettivo (bid-down C4) ma 7D credibile e di nuovo entro l'obiettivo con conversioni
        b, a = self.before_after((300, 30, 22), (60, 6, 52), (14, 2, 19))
        self.assertTrue(b["final_action"].startswith("BID_DOWN"), b["final_action"])          # prima: riduzione dal solo peggioramento 30D
        self.assertEqual(b["decision_rule"], "C4_RECENT_DETERIORATION")
        self.assertEqual(a["trend_state"], "RECENT_RECOVERY")
        self.assertEqual(a["final_action"], "WATCH")                                            # dopo: bid-down sospeso, mai un aumento
        self.assertEqual(a["decision_rule"], "T2_RECENT_RECOVERY_NO_DOWN")
        self.assertIn("RECENT_RECOVERY_7D", a["flags"])
        self.assertNotIn("RECENT_DETERIORATION", a["flags"])
        print("\nRECOVERY blocca C4  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"])

    def test_bad_7d_with_few_clicks_does_not_destroy_a_winning_target(self):
        b, a = self.before_after((300, 40, 20), (60, 8, 22), (3, 0, None))
        self.assertEqual(a["trend_evidence"], "LOW")
        self.assertEqual(a["trend_state"], "INSUFFICIENT_7D")                                    # 3 clic, 0 ordini: nessuna lettura
        self.assertEqual(a["final_action"], b["final_action"])
        self.assertFalse(a["final_action"].startswith("BID_DOWN"))

    def test_low_or_medium_7d_with_weak_30d_is_watch_recent_never_a_cut(self):
        for i, clicks in enumerate((3, 8)):
            if i:
                self.tearDown()
                self.setUp()
            b, a = self.before_after((300, 30, 28), (60, 5, 42), (clicks, 0, None))
            self.assertEqual(a["trend_state"], "WATCH_RECENT", clicks)
            self.assertFalse(a["final_action"].startswith("BID_DOWN"), (clicks, a["final_action"]))
            self.assertIn(a["final_action"], ("WATCH", "HOLD"))
            if b["final_action"] == "HOLD":
                self.assertEqual(a["final_action"], "WATCH")                                      # da HOLD a MONITORA
                self.assertEqual(a["decision_rule"], "T3_RECENT_WATCH")

    def test_healthy_30d_never_gets_a_bid_down_from_7d_alone(self):
        b, a = self.before_after((300, 40, 22), (60, 8, 24), (40, 1, 95))
        self.assertEqual(a["trend_state"], "WATCH_RECENT")
        self.assertFalse(a["final_action"].startswith("BID_DOWN"))
        self.assertNotIn("TREND_CONFIRMED_DECLINE", a["flags"])

    def test_7d_alone_never_cuts_more_than_5(self):
        b, a = self.before_after((300, 30, 35), (60, 5, 38), (300, 1, 500), kw="close-match", ctype="AUTO")
        self.assertEqual(a["trend_state"], "CONFIRMED_DECLINE")
        self.assertEqual(a["final_action"], "BID_DOWN_5_CANDIDATE")                               # mai -10/-15 solo per il 7D

    def test_7d_zero_orders_with_enough_clicks_is_confirmed_decline(self):
        b, a = self.before_after((300, 30, 28), (60, 5, 42), (25, 0, None))
        self.assertEqual((a["trend_evidence"], a["trend_state"]), ("STRONG", "CONFIRMED_DECLINE"))

    def test_strong_historic_pause_is_not_changed_by_7d(self):
        b, a = self.before_after((200, 0, None), (60, 0, None), (30, 0, None))
        self.assertEqual(a["final_action"], b["final_action"])
        self.assertIsNone(a["trend_state"])                                                       # storico gia' da pausa: nessuna lettura 7D aggiuntiva


class Route66Tests(WithAndWithout):
    """Route 66 Day by Day: prezzo 22.99, royalty 9.00 => pareggio DERIVATO = royalty / prezzo (~39.15%), non hardcodato."""
    BOOKDEF = ROUTE66
    UNIT_PRICE = 22.99

    def test_economics_are_the_real_ones_and_derived(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 30))
        self.go()
        ctx = analyze_book(self.conn, self.settings, self.mapping, tr.RULES_PATH, BOOK, self.logger).ctx
        self.assertAlmostEqual(ctx.break_even_acos, round(9.00 / 22.99 * 100, 2), places=2)
        self.assertAlmostEqual(ctx.break_even_acos, 39.15, places=2)
        self.assertEqual((ctx.price, ctx.royalty, ctx.target_acos), (22.99, 9.0, 35.0))

    def test_phrase_confirmed_decline_prudent_bid_down_5(self):
        b, a = self.before_after((300, 30, 28), (60, 5, 42), (24, 1, 85))                       # 42% ora e' SOPRA il pareggio 39.15%
        self.assertIsNone(b["trend_state"])
        self.assertEqual(a["trend_state"], "CONFIRMED_DECLINE")
        self.assertEqual(a["trend_evidence"], "STRONG")
        self.assertEqual(a["final_action"], "BID_DOWN_5_CANDIDATE")                              # prudente: mai -10/-15 dal 7D
        self.assertEqual(a["bid_change_pct"], -5)
        self.assertEqual((a["trend_clicks"], a["trend_orders"]), (24, 1))
        print("\nROUTE66 FRASE  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"], a["trend_state"])

    def test_phrase_between_target_and_break_even_goes_from_hold_to_bid_down_5(self):
        b, a = self.before_after((300, 30, 28), (60, 5, 37), (24, 1, 85))                        # sopra l'obiettivo 35 ma sotto il pareggio 39.15
        self.assertIn(b["final_action"], ("HOLD", "WATCH"))
        self.assertEqual((a["trend_state"], a["final_action"]), ("CONFIRMED_DECLINE", "BID_DOWN_5_CANDIDATE"))

    def test_auto_recent_recovery_no_general_bid_down(self):
        b, a = self.before_after((300, 30, 22), (60, 6, 36), (14, 2, 19), kw="close-match", ctype="AUTO")
        self.assertEqual(a["trend_state"], "RECENT_RECOVERY")
        self.assertNotIn(a["final_action"], ("BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE", "PAUSE_CANDIDATE", "BID_UP_5_CANDIDATE"))
        print("\nROUTE66 AUTO  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"], a["trend_state"])

    def test_competitor_row_present_with_zero_clicks_is_no_traffic(self):
        b, a = self.before_after((200, 20, 26), (40, 4, 28), "ZERO_CLICKS", ctype="COMPETITOR")
        self.assertEqual(a["trend_state"], "NO_TRAFFIC_7D")                                       # la riga 7D esiste e ha 0 clic
        self.assertEqual((a["trend_clicks"], a["trend_acos"], a["trend_evidence"]), (0, None, "NO_TRAFFIC"))
        self.assertEqual(a["final_action"], b["final_action"])                                  # nessun effetto: non e' inefficienza
        self.assertFalse(a["final_action"].startswith("BID_DOWN"))
        print("\nROUTE66 COMPETITOR (riga 7D con 0 clic)  prima:", b["final_action"], "| dopo:", a["final_action"], a["trend_state"])

    def test_competitor_row_absent_is_missing_7d_not_no_traffic(self):
        b, a = self.before_after((200, 20, 26), (40, 4, 28), "ABSENT", ctype="COMPETITOR")
        self.assertEqual(a["trend_state"], "MISSING_7D")                                         # riga assente != zero traffico
        self.assertNotEqual(a["trend_state"], "NO_TRAFFIC_7D")
        self.assertIsNone(a["trend_clicks"])                                                     # niente "0 clic" inventato
        self.assertIsNone(a["trend_evidence"])
        self.assertEqual((a["final_action"], a["decision_rule"], a["bid_change_pct"]), (b["final_action"], b["decision_rule"], b["bid_change_pct"]))
        self.assertEqual(a["delivery_signal"], b["delivery_signal"])                             # nessun falso problema di delivery
        self.assertIn("NON significa zero traffico", a["trend_note"])
        print("\nROUTE66 COMPETITOR (riga 7D assente)  prima:", b["final_action"], "| dopo:", a["final_action"], a["trend_state"])

    def test_missing_7d_never_changes_a_decision_even_with_a_weak_30d(self):
        for i, kind in enumerate(("PHRASE", "COMPETITOR")):
            if i:
                self.tearDown()
                self.setUp()
            b, a = self.before_after((300, 30, 28), (60, 5, 45), "ABSENT", ctype=kind)
            self.assertEqual(a["trend_state"], "MISSING_7D")
            self.assertEqual((a["final_action"], a["decision_rule"], a["flags"]), (b["final_action"], b["decision_rule"], b["flags"]), kind)

    def test_campaign_absent_from_the_7d_report_is_missing_7d(self):
        # due campagne dello stesso libro: il report 7D contiene solo C_OTHER; C_PHRASE non c'e' => dati 7D non disponibili (non zero traffico)
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 28), (60, 5, 42), None)
        self.scenario("C_OTHER", "PHRASE", (300, 30, 28), (60, 5, 30), (20, 2, 30))
        ds = self.go()
        self.assertEqual(one(ds, "C_PHRASE")["trend_state"], "MISSING_7D")
        self.assertIsNotNone(one(ds, "C_OTHER")["trend_state"])
        self.assertNotEqual(one(ds, "C_PHRASE")["trend_state"], "NO_TRAFFIC_7D")

    def test_search_term_7d_without_the_target_does_not_imply_no_traffic(self):
        # nessun Targeting 7D: solo un Search Term 7D (un solo termine) che NON contiene il target => MISSING_7D
        self.snap_campaign("C_PHRASE", "PHRASE", [("t_life.csv", tr.it_rows_target([row("kw", 300, 30, 28, price=22.99)]), "TARGETING", LIFE),
                                                  ("t30.csv", tr.it_rows_target([row("kw", 60, 5, 42, price=22.99)]), "TARGETING", D30),
                                                  ("s7.csv", tr.it_rows_st([("some other query", "kw", 12, 1, 9.0, 22.99)]), "SEARCH_TERM", D7)])
        d = one(self.go())
        self.assertEqual(d["trend_state"], "MISSING_7D")
        self.assertIsNone(d["trend_clicks"])


class RareCoinsTests(WithAndWithout):
    """Rare Coins Book for Beginners: prezzo 23.99, royalty 11.00 => pareggio DERIVATO (~45.85%)."""
    BOOKDEF = RARE
    UNIT_PRICE = 23.99

    def test_economics(self):
        self.scenario("C_PHRASE", "PHRASE", (300, 30, 35), (60, 5, 30))
        self.go()
        ctx = analyze_book(self.conn, self.settings, self.mapping, tr.RULES_PATH, BOOK, self.logger).ctx
        self.assertAlmostEqual(ctx.break_even_acos, round(11.0 / 23.99 * 100, 2), places=2)
        self.assertAlmostEqual(ctx.break_even_acos, 45.85, places=2)

    def test_auto_confirmed_decline(self):
        b, a = self.before_after((300, 30, 35), (60, 5, 38), (60, 2, 73), kw="close-match", ctype="AUTO")
        self.assertEqual(a["trend_state"], "CONFIRMED_DECLINE")
        self.assertEqual(a["final_action"], "BID_DOWN_5_CANDIDATE")
        self.assertNotEqual(a["final_action"], "PAUSE_CANDIDATE")
        print("\nRARE AUTO  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"], a["trend_state"])

    def test_competitor_confirmed_decline_prudent_not_pause(self):
        b, a = self.before_after((300, 30, 37), (60, 5, 37), (39, 2, 56), ctype="COMPETITOR")
        self.assertEqual(a["trend_state"], "CONFIRMED_DECLINE")
        self.assertEqual(a["final_action"], "BID_DOWN_5_CANDIDATE")
        self.assertNotEqual(a["final_action"], "PAUSE_CANDIDATE")
        print("\nRARE COMPETITOR  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"], a["trend_state"])

    def test_phrase_historic_problem_stays_visible_with_negative_trend(self):
        b, a = self.before_after((300, 30, 52), (60, 5, 58), (40, 2, 62))                       # storico e 30D SOPRA il pareggio 45.85, 7D sopra il pareggio
        self.assertTrue(b["final_action"].startswith("BID_DOWN"), b["final_action"])             # il problema storico esiste gia' senza 7D
        self.assertEqual(a["trend_state"], "CONFIRMED_DECLINE")
        self.assertEqual(a["final_action"], b["final_action"])                                    # il 7D non lo nasconde e non lo peggiora
        print("\nRARE FRASE  prima:", b["final_action"], b["decision_rule"], "| dopo:", a["final_action"], a["decision_rule"], a["trend_state"])


class TrendUiTests(unittest.TestCase):
    D = {"lifetime_acos": 28.0, "tactical_acos": 42.0, "tactical_clicks": 60, "trend_state": "CONFIRMED_DECLINE", "trend_evidence": "STRONG", "trend_clicks": 24,
         "trend_orders": 1, "trend_spend": 20.0, "trend_sales": 20.0, "trend_acos": 85.0, "trend_period": "2026-10-06__2026-10-12", "trend_note": "peggioramento confermato"}

    def render(self, d, fn="line"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "DASHBOARD"))
        import test_dashboard_labels as tl
        from components import trend_ui
        log = []
        getattr(trend_ui, fn)(tl.Rec(log), d)
        return chr(10).join(x for _n, a, k in log for x in tl.strings([a, k]))

    def test_labels(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "DASHBOARD"))
        import ui_labels as U
        self.assertEqual([U.label_trend(x) for x in ("STABLE", "CONFIRMED_DECLINE", "RECENT_RECOVERY", "INSUFFICIENT_7D", "NO_TRAFFIC_7D")],
                         ["STABILE", "PEGGIORAMENTO CONFERMATO", "RECUPERO RECENTE", "MONITORA — DATI 7D LIMITATI", "NESSUN TRAFFICO 7D"])
        self.assertEqual(U.label_trend("WATCH_RECENT", "LOW"), "MONITORA — DATI 7D LIMITATI")
        self.assertEqual(set(U.TREND_LABELS), set(TR.STATES))
        for f in ("TREND_CONFIRMED_DECLINE", "RECENT_RECOVERY_7D", "TREND_WATCH_RECENT"):
            self.assertIn(f, U.FLAG_LABELS)

    def test_card_shows_three_acos_state_and_sample(self):
        t = self.render(self.D)
        for need in ("ACOS storico completo", "28.0%", "ACOS ultimi 30 giorni", "42.0%", "ACOS ultimi 7 giorni", "85.0%", "24 clic · 1 acquisti", "PEGGIORAMENTO CONFERMATO"):
            self.assertIn(need, t, need)

    def test_no_traffic_and_missing_7d(self):
        t = self.render({**self.D, "trend_state": "NO_TRAFFIC_7D", "trend_clicks": 0, "trend_orders": 0, "trend_acos": None, "trend_evidence": "NO_TRAFFIC"})
        self.assertIn("NESSUN TRAFFICO 7D", t)
        self.assertIn("0 clic · 0 acquisti", t)
        self.assertNotIn("85.0%", t)
        self.assertEqual(self.render({"lifetime_acos": 28.0}), "")                      # nessuno snapshot 7D: la card non cambia
        t = self.render({**self.D, "trend_state": "MISSING_7D", "trend_clicks": None, "trend_orders": None, "trend_acos": None, "trend_evidence": None})
        self.assertIn("DATI 7D NON DISPONIBILI", t)
        self.assertNotIn("NESSUN TRAFFICO 7D", t)

    def test_operational_cards_render_trend(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "DASHBOARD"))
        import test_dashboard_labels as tl
        from components import cards
        log = []
        cards.decision_card(tl.Rec(log), {**self.D, "final_action": "BID_DOWN_5_CANDIDATE", "campaign_id": "C", "target": "kw", "reason": "x"})
        t = chr(10).join(x for _n, a, k in log for x in tl.strings([a, k]))
        self.assertIn("Ultimi 7 giorni".upper(), t)
        self.assertIn("PEGGIORAMENTO CONFERMATO", t)


if __name__ == "__main__":
    unittest.main()
