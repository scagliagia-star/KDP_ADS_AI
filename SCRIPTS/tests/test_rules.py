"""Test del rule engine V1. Dati economici passati esplicitamente (mai hardcodati nel motore); dati sintetici."""
import csv
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_importer as ti
from kdp_ads import registry
from kdp_ads.analysis import analyze_book
from kdp_ads.config import CONFIG_DIR
from kdp_ads.crosscampaign import analyze_overlaps, build_signals
from kdp_ads.metrics import compute_metrics, normalize_term, product_asin, target_type, term_matches_target
from kdp_ads.recfields import REC_COLUMNS
from kdp_ads.report import write_markdown, write_recommendations_csv, save_to_db
from kdp_ads.rules import (BookCtx, calculate_confidence, ctx_from_book, decide, economic_classification, load_rules,
                           priority, sample_classification)

RULES_PATH = CONFIG_DIR / "rules.json"


def make_ctx(book=None):
    book = dict(ti.PILOT if book is None else book)
    eco, _ = registry.book_economics(book)
    return ctx_from_book(book, eco, load_rules(RULES_PATH))


def M(clicks, orders, spend, sales, ctx):
    return compute_metrics(clicks * 50, clicks, spend, orders, sales, ctx.royalty)


class RuleBase(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()

    def d(self, clicks, orders, spend, sales, rt="TARGETING", scope="TARGET", avg=None):
        return decide(M(clicks, orders, spend, sales, self.ctx), self.ctx, rt, scope, avg)


# ------------------------------------------------------------------ economia: target vs break-even
class EconomicTests(RuleBase):
    def test_ctx_reads_book_values_not_hardcoded(self):
        self.assertEqual((self.ctx.royalty, self.ctx.target_acos, self.ctx.break_even_acos, self.ctx.phase),
                         (8.55, 35.0, 42.77, "PROFIT_STABLE"))
        other = make_ctx({**ti.OTHER})
        self.assertEqual((other.royalty, other.target_acos, other.break_even_acos), (4.10, 25.0, 31.56))
        self.assertEqual(other.bands["strong"], 25.0)     # min(30, target 25)

    def test_acos_below_target(self):
        r = self.d(60, 10, 25, 100)               # ACOS 25%
        self.assertEqual((r.rule_classification, r.economic), ("WINNER", "STRONG"))
        r = self.d(60, 10, 33, 100)               # 33% tra 30 e target 35
        self.assertEqual((r.rule_classification, r.action, r.economic), ("ON_TARGET_HOLD", "HOLD", "ON_TARGET"))

    def test_acos_between_target_and_break_even(self):
        r = self.d(60, 10, 40, 100)
        self.assertEqual((r.rule_classification, r.action, r.economic),
                         ("PROFITABLE_HOLD", "HOLD", "PROFITABLE_BUT_ABOVE_TARGET"))
        r = self.d(60, 6, 42.77, 100)             # esattamente break-even: ancora profittevole
        self.assertEqual(r.rule_classification, "PROFITABLE_HOLD")

    def test_acos_above_break_even_steps(self):
        for spend, act, pct, eco in ((45, "BID_DOWN_5_CANDIDATE", -5, "MILDLY_UNPROFITABLE"),
                                     (55, "BID_DOWN_10_CANDIDATE", -10, "UNPROFITABLE"),
                                     (65, "BID_DOWN_15_CANDIDATE", -15, "HIGHLY_UNPROFITABLE")):
            r = self.d(60, 6, spend, 100)         # 6 ordini => LOSER
            self.assertEqual((r.action, r.bid_change_pct, r.economic, r.rule_classification), (act, pct, eco, "LOSER"))

    def test_economic_bands_all(self):
        seq = [(20, "STRONG"), (33, "ON_TARGET"), (40, "PROFITABLE_BUT_ABOVE_TARGET"), (45, "MILDLY_UNPROFITABLE"),
               (55, "UNPROFITABLE"), (70, "HIGHLY_UNPROFITABLE"), (None, "NO_SALES")]
        for a, c in seq:
            self.assertEqual(economic_classification(a, self.ctx), c, a)

    def test_acos_zero_with_zero_sales_is_not_good(self):
        m = M(20, 0, 30, 0, self.ctx)
        self.assertIsNone(m.acos)                 # mai 0
        r = decide(m, self.ctx, "TARGETING")
        self.assertEqual(r.economic, "NO_SALES")
        self.assertIn("N/A (NO_SALES)", r.reason)
        self.assertNotIn("WINNER", r.rule_classification)


# ------------------------------------------------------------------ zero ordini
class ZeroOrderTests(RuleBase):
    def test_few_clicks_insufficient(self):
        r = self.d(2, 0, 1.5, 0)
        self.assertEqual((r.action, r.rule_classification, r.confidence), ("NO_ACTION", "INSUFFICIENT_DATA", "LOW"))
        self.assertEqual(self.d(5, 0, 3, 0).action, "NO_ACTION")

    def test_watch_6_to_9_clicks(self):
        self.assertEqual(self.d(6, 0, 3, 0).action, "WATCH")
        self.assertEqual(self.d(9, 0, 5, 0).action, "WATCH")

    def test_zero_orders_low_spend_or_few_clicks_is_watch_not_bid_down(self):
        r = self.d(10, 0, 6.41, 0)                          # spesa < 1,25x royalty (royalty 8,55 -> 10,69): troppo presto
        self.assertEqual((r.action, r.bid_change_pct, r.rule_id), ("WATCH", None, "Z2B"))
        self.assertEqual(self.d(18, 0, 6.2, 0).action, "WATCH")      # 18 clic, spesa ~0,7x royalty
        self.assertEqual(self.d(15, 0, 12.0, 0).action, "WATCH")     # spesa ok ma < 20 clic
        self.assertNotIn("N/A", self.d(18, 0, 6.2, 0).reason.split("[")[0])

    def test_zero_orders_spend_from_1_25x_royalty_with_20_clicks_is_bid_down_5(self):
        r = self.d(35, 0, 11.5, 0)                          # 1,35x royalty
        self.assertEqual((r.action, r.bid_change_pct, r.rule_id), ("BID_DOWN_5_CANDIDATE", -5, "Z3"))
        self.assertIn("ZERO_ORDER_WASTE", r.flags)

    def test_spend_above_1_5x_royalty_below_double(self):
        r = self.d(25, 0, 16.10, 0)
        self.assertEqual((r.action, r.bid_change_pct), ("BID_DOWN_10_CANDIDATE", -10))

    def test_spend_above_double_royalty_pause_only_as_candidate(self):
        r = self.d(20, 0, 17.10, 0)               # esattamente 2x
        self.assertEqual(r.action, "PAUSE_CANDIDATE")
        self.assertIsNone(r.bid_change_pct)

    def test_search_term_gets_negative_candidate_not_pause(self):
        r = self.d(20, 0, 30, 0, rt="SEARCH_TERM")
        self.assertEqual(r.action, "NEGATIVE_CANDIDATE")
        self.assertNotEqual(self.d(20, 0, 30, 0, rt="SEARCH_TERM").action, "PAUSE_CANDIDATE")

    def test_high_cpc_flag_with_extremely_few_clicks(self):
        r = self.d(3, 0, 12, 0)
        self.assertEqual(r.action, "NO_ACTION")
        self.assertIn("HIGH_CPC", r.flags)
        self.assertNotIn("HIGH_CPC", self.d(3, 0, 2, 0).flags)


# ------------------------------------------------------------------ con ordini: safety, winner, bid-up
class OrderRuleTests(RuleBase):
    def test_high_volume_above_break_even_only_progressive_bid_down(self):
        for spend, pct in ((45, -5), (55, -10), (70, -15), (300, -15)):
            r = self.d(400, 20, spend, 100 if spend < 100 else spend / 0.7)   # 300/0.7 -> ACOS 70%
            self.assertEqual(r.rule_classification, "HIGH_VOLUME_INEFFICIENT")
            self.assertGreaterEqual(r.bid_change_pct, -15)
            self.assertNotIn(r.action, ("PAUSE_CANDIDATE", "NEGATIVE_CANDIDATE"))
        r = self.d(400, 20, 700, 1000)            # ACOS 70%
        self.assertEqual((r.action, r.bid_change_pct), ("BID_DOWN_15_CANDIDATE", -15))
        self.assertIn("HIGH_VOLUME", r.flags)

    def test_winner_low_sample_no_aggressive_increase(self):
        r = self.d(8, 2, 3, 40)                   # ACOS 7.5%, 2 ordini
        self.assertEqual((r.rule_classification, r.action), ("PROMISING_LOW_SAMPLE", "HOLD"))
        r = self.d(5, 3, 3, 60)                   # 3 ordini ma 5 clic: confidence LOW => niente bid up
        self.assertEqual(r.action, "HOLD")
        self.assertEqual(r.rule_classification, "PROMISING_WINNER")
        self.assertNotEqual(self.d(40, 1, 5, 20).action, "BID_UP_5_CANDIDATE")

    def test_never_bid_up_below_3_orders(self):
        for o in (1, 2):
            self.assertNotEqual(self.d(200, o, 2, 200).action, "BID_UP_5_CANDIDATE")

    def test_bid_up_5_only_when_all_conditions(self):
        r = self.d(40, 8, 40, 200, avg=15)        # ACOS 20, CVR 20%, MEDIUM
        self.assertEqual((r.action, r.bid_change_pct, r.rule_classification), ("BID_UP_5_CANDIDATE", 5, "PROMISING_WINNER"))
        self.assertEqual(self.d(40, 8, 40, 200, avg=5).action, "HOLD")             # CVR anomalo vs media campagna
        self.assertEqual(self.d(40, 8, 58, 200).action, "HOLD")                     # ACOS 29: non abbastanza sotto target
        self.assertEqual(self.d(40, 8, 40, 200, scope="PARENT_TARGET").action, "HOLD")
        self.assertEqual(self.d(11, 8, 40, 200, avg=15).action, "HOLD")             # CVR 73%: anomalo

    def test_winner_10_plus_orders(self):
        r = self.d(200, 12, 100, 500)             # ACOS 20%
        self.assertEqual(r.rule_classification, "WINNER")
        self.assertEqual(r.confidence, "HIGH")

    def test_1_to_4_orders_never_bid_down(self):
        r = self.d(60, 4, 200, 80)                # ACOS 250%
        self.assertEqual((r.action, r.rule_classification), ("WATCH", "LOW_SAMPLE_WATCH"))
        self.assertIn("LOSS_UNDER_LOW_SAMPLE", r.flags)

    def test_product_target_asin_stays_asin(self):
        t = 'asin="B0FTFJL6RB"'
        self.assertEqual(target_type(t), "PRODUCT_TARGET")
        self.assertEqual(product_asin(t), "b0ftfjl6rb")
        self.assertEqual(target_type("chair tai chi"), "KEYWORD_TARGET")
        self.assertTrue(term_matches_target("b0ftfjl6rb", t))
        self.assertEqual(normalize_term("b0ftfjl6rb"), "b0ftfjl6rb")      # non diventa keyword
        r = self.d(86, 12, 61.51, 227.88)
        self.assertEqual(r.rule_classification, "WINNER")                 # stesse metriche economiche

    def test_term_normalization_no_fuzzy(self):
        self.assertEqual(normalize_term("  Chair   Tai  CHI "), "chair tai chi")
        self.assertFalse(term_matches_target("tai chi chair", "chair tai chi"))
        self.assertFalse(term_matches_target("chair tai chis", "chair tai chi"))


class ConfidenceAndPriorityTests(RuleBase):
    def test_confidence_levels(self):
        r, R = self.ctx.rules, self.ctx.royalty
        cases = [(0, 2, 1, "LOW"), (0, 10, 6, "MEDIUM"), (0, 25, 10, "HIGH"), (0, 15, 20, "HIGH"), (0, 15, 5, "MEDIUM"),
                 (1, 5, 5, "LOW"), (2, 40, 10, "MEDIUM"), (5, 50, 30, "MEDIUM"), (5, 120, 60, "HIGH"), (5, 8, 8, "LOW"),
                 (12, 200, 100, "HIGH"), (12, 10, 10, "MEDIUM")]
        for o, c, s, exp in cases:
            self.assertEqual(calculate_confidence(o, c, s, R, r), exp, (o, c, s))

    def test_zero_orders_25_clicks_can_be_high(self):
        self.assertEqual(self.d(25, 0, 25, 0).confidence, "HIGH")

    def test_sample_classification_by_orders(self):
        rr = self.ctx.rules
        self.assertEqual([sample_classification(o, rr) for o in (0, 2, 3, 9, 10)], ["LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH"])

    def test_priority_levels(self):
        c = self.ctx
        m = lambda spend, orders: M(100, orders, spend, orders * 20, c)
        self.assertEqual(priority("BID_DOWN_10_CANDIDATE", m(300, 10), "HIGH", c), "P1")   # perdita 214
        self.assertEqual(priority("BID_DOWN_10_CANDIDATE", m(300, 10), "MEDIUM", c), "P2")
        self.assertEqual(priority("BID_DOWN_5_CANDIDATE", m(25, 2), "MEDIUM", c), "P3")    # perdita 7.9: sopra 0.5R, sotto 2R
        self.assertEqual(priority("BID_DOWN_5_CANDIDATE", m(10, 0), "LOW", c), "P3")       # perdita 10 >= 0.5R
        self.assertEqual(priority("BID_DOWN_5_CANDIDATE", m(3, 0), "LOW", c), "P4")
        self.assertEqual(priority("WATCH", m(3, 0), "LOW", c), "P4")
        self.assertEqual(priority("HOLD", m(300, 10), "HIGH", c), "P3")                    # perdita nascosta
        self.assertEqual(priority("BID_UP_5_CANDIDATE", M(40, 8, 40, 200, c), "MEDIUM", c), "P3")
        self.assertEqual(priority("INSUFFICIENT_DATA", m(1, 0), "LOW", c), "P4")


# ------------------------------------------------------------------ cross-campaign
def st(cid, ctype, term, clicks, orders, spend, sales, target=None):
    return {"campaign_id": cid, "campaign_type": ctype, "search_term": term, "target": target or term,
            "target_type": "KEYWORD_TARGET", "impressions": clicks * 50, "clicks": clicks, "spend": spend,
            "orders": orders, "sales": sales}


class CrossCampaignTests(RuleBase):
    def test_exact_worse_than_phrase(self):
        rows = [st("EX", "EXACT", "tai chi chair workout chart", 254, 24, 241.81, 455.76),    # ACOS 53%
                st("PH", "PHRASE", "tai chi chair workout chart", 133, 16, 123.24, 303.84)]   # ACOS 40.6%
        o = analyze_overlaps(rows, self.ctx)[0]
        self.assertEqual(o["classification"], "BETTER_IN_PHRASE")
        self.assertTrue(o["possible_inefficiency"])
        self.assertEqual((o["better"]["campaign_id"], o["worse"]["campaign_id"]), ("PH", "EX"))
        self.assertIn("reduce EXACT bid candidate", o["recommendation"])
        self.assertIn("keep PHRASE", o["recommendation"])

    def test_phrase_worse_than_exact_no_assumption_exact_better(self):
        rows = [st("EX", "EXACT", "kw", 100, 20, 60, 400),        # ACOS 15%
                st("PH", "PHRASE", "kw", 120, 10, 150, 200)]      # ACOS 75%
        o = analyze_overlaps(rows, self.ctx)[0]
        self.assertEqual(o["classification"], "BETTER_IN_EXACT")
        self.assertEqual(o["worse"]["campaign_id"], "PH")
        self.assertIn("reduce PHRASE bid candidate", o["recommendation"])

    def test_similar_performance_and_insufficient(self):
        rows = [st("EX", "EXACT", "kw", 100, 20, 100, 400), st("PH", "PHRASE", "kw", 120, 24, 118, 480)]
        self.assertEqual(analyze_overlaps(rows, self.ctx)[0]["classification"], "SIMILAR_PERFORMANCE")
        rows = [st("EX", "EXACT", "kw", 100, 20, 100, 400), st("PH", "PHRASE", "kw", 4, 0, 3, 0)]
        self.assertEqual(analyze_overlaps(rows, self.ctx)[0]["classification"], "INSUFFICIENT_DATA")

    def test_low_confidence_gap_is_not_flagged_as_inefficiency(self):
        rows = [st("EX", "EXACT", "chair tai chi", 12, 1, 12.1, 18.99), st("PH", "PHRASE", "chair tai chi", 246, 52, 286.55, 968.49)]
        o = analyze_overlaps(rows, self.ctx)[0]
        self.assertEqual(o["classification"], "BETTER_IN_PHRASE")
        self.assertFalse(o["possible_inefficiency"])

    def test_better_in_competitor_and_same_type_uses_campaign_id(self):
        rows = [st("EX", "EXACT", "kw", 100, 5, 150, 100), st("CO", "COMPETITOR", "kw", 100, 25, 60, 500)]
        self.assertEqual(analyze_overlaps(rows, self.ctx)[0]["classification"], "BETTER_IN_COMPETITOR")
        rows = [st("EX_01", "EXACT", "kw", 100, 25, 60, 500), st("EX_02", "EXACT", "kw", 100, 5, 150, 100)]
        self.assertEqual(analyze_overlaps(rows, self.ctx)[0]["classification"], "BETTER_IN_EX_01")

    def test_only_identical_normalized_terms_overlap(self):
        rows = [st("EX", "EXACT", "Chair  Tai Chi ", 50, 5, 40, 100), st("PH", "PHRASE", "chair tai chi", 50, 5, 40, 100),
                st("PH", "PHRASE", "tai chi chair", 50, 5, 40, 100)]
        ov = analyze_overlaps(rows, self.ctx)
        self.assertEqual([o["search_term"] for o in ov], ["chair tai chi"])

    def test_same_term_two_books_never_compared(self):
        # analyze_overlaps lavora su un solo libro: le righe di un altro libro non arrivano mai qui (query per book_id)
        self.assertEqual(analyze_overlaps([st("EX", "EXACT", "kw", 50, 5, 40, 100)], self.ctx), [])


# ------------------------------------------------------------------ segnale recente vs storico, periodi diversi
def _row(i, cid, target, term, clicks, orders, spend, sales, scope, rid_kind):
    m = compute_metrics(clicks * 50, clicks, spend, orders, sales, 8.55)
    return {"id": i, "campaign_id": cid, "target": target, "search_term": term, "report_scope": scope, "_m": m}


class SignalTests(RuleBase):
    def signal(self, life, rec, tterm="kw", sterm="kw", starget="kw", tscope="LIFETIME", sscope="DATE_RANGE"):
        t = _row(1, "C", "kw", None, *life, tscope, "T")
        s = _row(2, "C", starget, sterm, *rec, sscope, "S")
        return build_signals([t], [s], self.ctx)

    def test_improving_stable_declining(self):
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 6, 20, 240))[("S", 2)][0], "IMPROVING_SIGNAL")
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 5, 25, 100))[("S", 2)][0], "STABLE_SIGNAL")
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 4, 40, 100))[("S", 2)][0], "DECLINING_SIGNAL")
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 0, 30, 0))[("S", 2)][0], "DECLINING_SIGNAL")

    def test_same_signal_on_target_row(self):
        sig = self.signal((100, 10, 100, 400), (20, 6, 20, 240))
        self.assertEqual(sig[("T", 1)], sig[("S", 2)])

    def test_unknown_when_not_verifiable(self):
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 2, 20, 100))[("S", 2)][0], "UNKNOWN")           # pochi ordini
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 6, 20, 240), sterm="other kw", starget="kw")[("S", 2)][0], "UNKNOWN")
        self.assertEqual(self.signal((100, 10, 100, 400), (20, 6, 20, 240), sscope="UNKNOWN")[("S", 2)][0], "UNKNOWN")
        # keyword Target vs Search Term diverso non si confrontano mai
        self.assertNotIn(("T", 1), self.signal((100, 10, 100, 400), (20, 6, 20, 240), sterm="another", starget="kw"))

    def test_different_campaign_never_paired(self):
        t = _row(1, "C1", "kw", None, 100, 10, 100, 400, "LIFETIME", "T")
        s = _row(2, "C2", "kw", "kw", 20, 6, 20, 240, "DATE_RANGE", "S")
        self.assertEqual(build_signals([t], [s], self.ctx)[("S", 2)][0], "UNKNOWN")


# ------------------------------------------------------------------ pipeline end-to-end (DB -> analisi -> output)
def it_rows_target(rows):
    hdr = "Stato,Parola chiave,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS"
    out = [hdr]
    for kw, clicks, orders, spend, sales in rows:
        out.append(_it(kw, clicks, orders, spend, sales, target=True))
    return "\n".join(out) + "\n"


def it_rows_st(rows):
    hdr = "Aggiunto come,Termine di ricerca del cliente,Parole chiave,Offerta per il targeting (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS"
    out = [hdr]
    for term, kw, clicks, orders, spend, sales in rows:
        out.append(_it((term, kw), clicks, orders, spend, sales, target=False))
    return "\n".join(out) + "\n"


def _it(key, clicks, orders, spend, sales, target):
    impr = clicks * 50
    ctr = f'"{clicks / impr:.4f}"'.replace(".", ",")
    acos = f'"{spend / sales:.4f}"'.replace(".", ",") if sales else ""
    common = f"1.00,{impr},{clicks},{ctr},{spend:.2f},{spend / clicks:.2f},{orders},{sales:.2f},{acos}"
    if target:
        return f"ABILITATO,{key},Esatta,Attivo,{common}"
    return f"Parola chiave: esatta,{key[0]},{key[1]},{common}"


class PipelineTests(ti.Base):
    def setUp(self):
        super().setUp()
        self.add_book()
        self.rules_path = RULES_PATH

    def analyze(self):
        return analyze_book(self.conn, self.settings, self.mapping, self.rules_path, "1805566563", self.logger)

    def test_lifetime_and_recent_not_summed_and_periods_kept(self):
        tgt = it_rows_target([("kw one", 100, 10, 100, 400), ("kw two", 50, 5, 60, 150)])
        st_ = it_rows_st([("kw one", "kw one", 20, 6, 20, 240)])
        self.campaign("1805566563", "C_EXACT", "EXACT", {"t.csv": tgt, "s.csv": st_})
        self.assertTrue(all(r.status == "IMPORTED" for r in self.run_import()))
        an = self.analyze()
        tot = {t["report_type"]: t for t in an.audit["totals"]}
        self.assertEqual((tot["TARGETING"]["spend"], tot["TARGETING"]["orders"]), (160.0, 15.0))
        self.assertEqual((tot["SEARCH_TERM"]["spend"], tot["SEARCH_TERM"]["orders"]), (20.0, 6.0))
        self.assertEqual(tot["TARGETING"]["report_scope"], "LIFETIME")
        self.assertEqual((tot["SEARCH_TERM"]["date_from"], tot["SEARCH_TERM"]["date_to"]), ("2026-07-28", "2026-09-30"))
        t_rows = [r for r in an.recs if r["report_type"] == "TARGETING"]
        s_rows = [r for r in an.recs if r["report_type"] == "SEARCH_TERM"]
        self.assertEqual({r["date_from"] for r in t_rows}, {None})
        self.assertEqual({r["date_from"] for r in s_rows}, {"2026-07-28"})
        self.assertEqual(len(an.recs), 3)                          # nessuna riga fusa
        kw1 = [r for r in an.recs if r["target"] == "kw one"]
        self.assertEqual({r["recent_vs_historical_signal"] for r in kw1}, {"IMPROVING_SIGNAL"})
        self.assertEqual([r for r in t_rows if r["target"] == "kw two"][0]["recent_vs_historical_signal"], "UNKNOWN")
        self.assertEqual(an.warnings, [])

    def test_outputs_and_markdown_structure(self):
        tgt = it_rows_target([("kw one", 100, 10, 100, 400), ("kw two", 40, 0, 40, 0)])
        st_ = it_rows_st([("kw one", "kw one", 20, 6, 20, 240)])
        self.campaign("1805566563", "C_EXACT", "EXACT", {"t.csv": tgt, "s.csv": st_})
        self.run_import()
        an = self.analyze()
        out = Path(self.tmp.name) / "out"
        p_csv = write_recommendations_csv(an, out / "rec", "20260930_120000")
        p_md = write_markdown(an, out / "rep", "20260930_120000")
        p_csv2 = write_recommendations_csv(an, out / "rec", "20260930_120000")      # non sovrascrive
        self.assertNotEqual(p_csv, p_csv2)
        self.assertEqual(p_csv.name, "recommendations_1805566563_20260930_120000.csv")
        self.assertEqual(p_md.name, "analysis_1805566563_20260930_120000.md")
        with open(p_csv, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(list(rows[0].keys()), REC_COLUMNS)
        for col in ("amazon_recommendation", "amazon_recommendation_period", "amazon_recommendation_source",
                    "recommendation_agreement", "priority", "recent_vs_historical_signal"):
            self.assertIn(col, rows[0])
        zero = [r for r in rows if r["target"] == "kw two"][0]
        self.assertEqual(zero["acos"], "N/A_NO_SALES")                              # mai 0
        md = p_md.read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# KDP Ads analysis"))
        self.assertLess(md.index("BOOK SUMMARY"), md.index("CAMPAIGN SUMMARY"))
        self.assertLess(md.index("LIFETIME TARGET DATA"), md.index("RECENT SEARCH TERM DATA"))
        for n in ("1. EXECUTIVE SUMMARY", "2. WINNERS", "3. PROFITABLE / HOLD", "4. ABOVE TARGET BUT PROFITABLE",
                  "5. HIGH-VOLUME INEFFICIENT", "6. LOSERS", "7. ZERO-ORDER WASTE", "8. WATCHLIST", "9. INSUFFICIENT DATA",
                  "10. PRODUCT TARGETS", "11. CROSS-CAMPAIGN OVERLAPS", "12. RECOMMENDED ACTIONS", "13. DATA WARNINGS",
                  "14. METHODOLOGY NOTES"):
            self.assertIn(n, md)
        self.assertIn("non equivale automaticamente", md)                            # nota sulla stima
        run_id = save_to_db(self.conn, an, p_csv, p_md, p_csv, an.ctx.rules)
        n = self.conn.execute("SELECT COUNT(*) FROM recommendations WHERE run_id=?", (run_id,)).fetchone()[0]
        self.assertEqual(n, len(an.recs))

    def test_data_warning_when_declared_ratio_inconsistent(self):
        tgt = it_rows_target([("kw one", 100, 10, 100, 400)]).replace('"0,2500"', '"0,9000"')
        self.campaign("1805566563", "C_EXACT", "EXACT", {"t.csv": tgt})
        self.run_import()
        an = self.analyze()
        self.assertTrue([w for w in an.warnings if "ACOS dichiarato incoerente" in w])

    def test_data_warning_recent_exceeds_lifetime(self):
        tgt = it_rows_target([("kw one", 10, 1, 10, 40)])
        st_ = it_rows_st([("kw one", "kw one", 20, 6, 20, 240)])
        self.campaign("1805566563", "C_EXACT", "EXACT", {"t.csv": tgt, "s.csv": st_})
        self.run_import()
        self.assertTrue([w for w in self.analyze().warnings if "Search Term recente" in w])

    def test_books_do_not_mix_in_analysis(self):
        self.add_book(ti.OTHER)
        self.campaign("1805566563", "C_EXACT", "EXACT", {"t.csv": it_rows_target([("kw one", 100, 10, 100, 400)])})
        self.campaign("B0OTHERBOOK", "C_EXACT", "EXACT", {"t.csv": it_rows_target([("kw one", 60, 1, 90, 100)])})
        self.run_import()
        a1 = self.analyze()
        self.assertEqual({r["book_id"] for r in a1.recs}, {"1805566563"})
        self.assertEqual(a1.recs[0]["royalty_per_copy"], 8.55)
        a2 = analyze_book(self.conn, self.settings, self.mapping, self.rules_path, "B0OTHERBOOK", self.logger)
        self.assertEqual({r["book_id"] for r in a2.recs}, {"B0OTHERBOOK"})
        self.assertEqual((a2.recs[0]["royalty_per_copy"], a2.recs[0]["break_even_acos"], a2.recs[0]["target_acos"]), (4.10, 31.56, 25.0))

    def test_product_target_rows_flagged(self):
        hdr = "Stato,Categorie e prodotti,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS"
        row = 'ABILITATO,"asin=""B0FTFJL6RB""",—,Attivo,0.90,5000,100,"0,0200",70.00,0.70,12,228.00,"0,3070"'
        self.campaign("1805566563", "C_COMP", "COMPETITOR", {"t.csv": hdr + "\n" + row + "\n"})
        self.run_import()
        r = self.analyze().recs[0]
        self.assertEqual((r["target_type"], r["target"]), ("PRODUCT_TARGET", 'asin="B0FTFJL6RB"'))


if __name__ == "__main__":
    unittest.main()
