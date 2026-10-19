"""Correzioni dal libro reale Rare Coins Book (B0GY42H3W4): promozione PHRASE->EXACT coerente, ASIN mai keyword EXACT, AUTO con scostamento minimo
da monitorare, testi neutri per i target in pausa, erogazione senza riferimento nella vista principale. Dati sintetici + COPIA del DB reale."""
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

import realdata  # noqa: E402

import test_opportunities as to
import test_overview as tov
from kdp_ads.decision import _mild_deterioration, map_target_status
from kdp_ads.opportunities import (NEGATIVE, PRODUCT_WINNER, PROMOTE, PROMOTED, REVIEW_CONFLICT, compute_opportunities, is_asin_term, kw_norm,
                                   unique_terms)

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

NEG = to.NEG_EXACT


def tgt_state(campaign, ctype, target, state="ABILITATO", match="Esatta"):
    r = to.tgt(campaign, ctype, target, match)
    r["raw_json"] = json.dumps({"Stato": state})
    return r


def run_st(rows, targets):
    return compute_opportunities(list(rows), list(targets), to.CAMPS, to.CTX, status_of=map_target_status)


def sells_with_negative(term="coin collection supplies"):
    return to.with_added(to.both("FRASE", "PHRASE", "p", term, 40, 4, 12.0), NEG)


class PromotedInExactTests(unittest.TestCase):
    def test_negative_exact_in_phrase_plus_active_exact_is_promoted_not_conflict(self):
        res = run_st(sells_with_negative(), [tgt_state("EXACT", "EXACT", "coin collection supplies")])
        r = to.only(res, "coin collection supplies")
        self.assertEqual(r["opportunity_type"], PROMOTED)
        self.assertFalse(r["promoted_exact_paused"])
        self.assertEqual(r["promoted_in"], ["EXACT"])
        self.assertFalse(r["operationally_actionable"])
        self.assertEqual((unique_terms(res["records"], PROMOTE), unique_terms(res["records"], REVIEW_CONFLICT)), ([], []))

    def test_negative_exact_in_phrase_plus_paused_exact_is_promoted_but_paused(self):
        res = run_st(sells_with_negative(), [tgt_state("EXACT", "EXACT", "coin collection supplies", "IN PAUSA")])
        r = to.only(res, "coin collection supplies")
        self.assertEqual(r["opportunity_type"], PROMOTED)
        self.assertTrue(r["promoted_exact_paused"])
        self.assertIn("PROMOTED_EXACT_PAUSED", r["flags"])

    def test_negative_exact_in_phrase_without_exact_is_a_real_conflict(self):
        r = to.only(run_st(sells_with_negative(), []), "coin collection supplies")
        self.assertEqual(r["opportunity_type"], REVIEW_CONFLICT)
        self.assertFalse(r["operationally_actionable"])

    def test_match_is_cross_campaign_and_normalizes_quotes_case_spaces(self):
        res = run_st(sells_with_negative(), [tgt_state("EXACT", "EXACT", '"Coin  Collection Supplies"')])
        self.assertEqual(to.only(res, "coin collection supplies")["opportunity_type"], PROMOTED)
        self.assertEqual(kw_norm("“Coin Collection   Supplies”"), "coin collection supplies")

    def test_similar_keywords_are_not_merged(self):
        self.assertNotEqual(kw_norm("coin collection supplies"), kw_norm("coin collectors supplies"))
        res = run_st(sells_with_negative(), [tgt_state("EXACT", "EXACT", "coin collectors supplies")])
        self.assertEqual(to.only(res, "coin collection supplies")["opportunity_type"], REVIEW_CONFLICT)

    def test_promoted_does_not_count_as_exact_or_negative_opportunity(self):
        res = run_st(sells_with_negative(), [tgt_state("EXACT", "EXACT", "coin collection supplies")])
        self.assertEqual((unique_terms(res["records"], PROMOTE), unique_terms(res["records"], NEGATIVE)), ([], []))
        self.assertIsNone(res["new_exact_campaign"])


class ProductTargetTests(unittest.TestCase):
    def test_isbn_style_asin_is_an_asin_not_a_keyword(self):
        self.assertTrue(all(is_asin_term(x) for x in ("0794851630", "B0FT2T32WY", 'asin="0794851630"', "1441312315")))
        self.assertFalse(is_asin_term("rare coin book"))

    def test_asin_with_good_results_is_a_winning_product_target_never_promote(self):
        rows = to.both("PRODOTTO", "COMPETITOR", 'asin="0794851630"', "0794851630", 32, 3, 15.0)
        res = run_st(rows, [tgt_state("PRODOTTO", "COMPETITOR", 'asin="0794851630"', "ABILITATO", "")])
        self.assertEqual([r["opportunity_type"] for r in res["records"]], [PRODUCT_WINNER])
        r = res["records"][0]
        self.assertEqual((r["product_asin"], r["target_status"]), ("0794851630", "ENABLED"))
        self.assertEqual(unique_terms(res["records"], PROMOTE), [])
        self.assertFalse(r["operationally_actionable"])

    def test_real_keyword_query_from_a_product_target_is_still_a_keyword(self):
        rows = to.both("PRODOTTO", "COMPETITOR", 'asin="0794851630"', "coin identifier book", 20, 3, 15.0)
        res = run_st(rows, [])
        self.assertEqual(res["records"][0]["opportunity_type"], PROMOTE)


class DecisionMildDeteriorationTests(unittest.TestCase):
    """C4B: soglie documentate in rules.json -> decision (mild_*); nessun valore legato a un libro."""
    CTX = SimpleNamespace(target_acos=35.0, break_even_acos=45.9, royalty=11.0)
    CFG = {"mild_monitor_enabled": True, "mild_max_gap_pct": 15, "mild_low_bid_royalty_frac": 0.05, "mild_recent_spend_mult": 2.0}

    @staticmethod
    def ev(acos=None, spend=0.0):
        return SimpleNamespace(m=SimpleNamespace(acos=acos, spend=spend))

    def check(self, life_acos=35.9, bid=0.25, spend=12.4):
        return _mild_deterioration(self.ev(life_acos), self.ev(None, spend), bid, self.CTX, self.CFG)

    def test_small_gap_profitable_low_bid_little_recent_evidence_is_monitored(self):
        self.assertTrue(self.check())

    def test_each_condition_is_needed(self):
        self.assertFalse(self.check(life_acos=34.0))        # sotto l'obiettivo: non e' questo caso
        self.assertFalse(self.check(life_acos=44.0))        # scostamento grande (> 15% dell'obiettivo)
        self.assertFalse(self.check(life_acos=46.5))        # sopra il pareggio
        self.assertFalse(self.check(bid=0.90))              # offerta non bassa
        self.assertFalse(self.check(spend=40.0))            # recente con evidenza sufficiente (>= 2x royalty)
        self.assertFalse(self.check(bid=None))
        self.assertFalse(_mild_deterioration(None, self.ev(None, 1.0), 0.2, self.CTX, self.CFG))
        self.assertFalse(_mild_deterioration(self.ev(35.9), self.ev(None, 1.0), 0.2, self.CTX, {**self.CFG, "mild_monitor_enabled": False}))

    def test_thresholds_are_in_rules_json(self):
        rules = json.loads((ROOT / "CONFIG" / "rules.json").read_text(encoding="utf-8"))["decision"]
        for k in ("mild_monitor_enabled", "mild_max_gap_pct", "mild_low_bid_royalty_frac", "mild_recent_spend_mult"):
            self.assertIn(k, rules)


class OverviewTextTests(unittest.TestCase):
    def test_paused_text_is_neutral_unless_inefficiency_is_provable(self):
        book = {**tov.BOOK, "royalty_per_copy": 11.0}
        n = tov.dec(1, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_acos=None, lifetime_spend=5.0, lifetime_orders=0.0)
        self.assertEqual(tov.O.short_reason(n, book), "È già in pausa. Nessuna azione necessaria.")
        n2 = tov.dec(2, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_acos=38.0, lifetime_spend=40.0, lifetime_orders=3.0)
        self.assertEqual(tov.O.short_reason(n2, book), "È già in pausa. Nessuna azione necessaria.")          # sopra obiettivo ma sotto pareggio
        bad = tov.dec(3, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_acos=80.0, lifetime_spend=40.0, lifetime_orders=3.0)
        self.assertEqual(tov.O.short_reason(bad, book), "Era inefficiente ed è già in pausa.")
        waste = tov.dec(4, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_acos=None, lifetime_spend=30.0, lifetime_orders=0.0)
        self.assertEqual(tov.O.short_reason(waste, book), "Era inefficiente ed è già in pausa.")                # 0 acquisti, spesa >= 2x royalty

    def test_mild_monitor_reason(self):
        d = tov.dec(1, final_action="WATCH", flags="MILD_DETERIORATION_MONITOR", lifetime_clicks=100.0, recent_vs_historical_signal="DECLINING_SIGNAL")
        self.assertEqual(tov.O.short_reason(d, tov.BOOK), "Storicamente è ancora profittevole, ma il rendimento recente è peggiorato. "
                                                          "Il bid è già basso: monitora prima di ridurlo.")
        self.assertEqual(tov.build([d])["monitor"][0]["reason"], tov.O.short_reason(d, tov.BOOK))

    def test_delivery_item_main_text_has_no_reference_per_day(self):
        d = tov.dec(1, tactical_impressions_per_day=0.0, reason="x")
        item = tov.O.delivery_item(d, tov.parse_delivery_detail)
        self.assertEqual(item["klass"], "NO_DELIVERY")
        self.assertNotIn("riferimento", item["reason"].lower())
        dec_item = tov.O.delivery_item(tov.dec(2, delivery_source="TARGET", delivery_signal="DELIVERY_COLLAPSED", delivery_ratio=0.1, tactical_impressions_per_day=50.0,
                                               reason="impressions/giorno: recente 5 (a, 30g) vs riferimento 50 (b, 65g) = x0.1".replace("impressions", "impressions")),
                                       tov.parse_delivery_detail)
        if dec_item:
            self.assertNotIn("riferimento", dec_item["reason"].lower())

    def test_promoted_conflict_and_product_items(self):
        base = dict(search_term="coin collection supplies", campaign_id="FRASE", negative_match="EXACT", orders=4.0, acos=38.1, clicks=43.0, spend=36.5,
                    sales=95.9, promoted_in=["ESATTA"])
        recs = [tov.opp("coin collection supplies", "PROMOTED_TO_EXACT", promoted_exact_paused=True, **{k: v for k, v in base.items() if k != "search_term"}),
                tov.opp("other", "REVIEW_NEGATIVE_CONFLICT", negative_match="EXACT", orders=2.0, acos=6.8),
                tov.opp("0794851630", "WINNING_PRODUCT_TARGET", orders=3.0, acos=32.1, product_asin="0794851630", target_status="ENABLED")]
        ov = tov.build([tov.dec(1)], recs)
        p = ov["promoted"][0]
        self.assertEqual((p["badge"], p["text"]), ("PROMOSSA IN EXACT · ATTUALMENTE IN PAUSA",
                                                   "Questa keyword è stata promossa in EXACT ed esclusa dalla FRASE, ma il target EXACT è attualmente in pausa."))
        recs[0]["promoted_exact_paused"] = False
        p = tov.build([tov.dec(1)], recs)["promoted"][0]
        self.assertEqual((p["badge"], p["text"]), ("PROMOSSA IN EXACT", "Questa keyword è già stata promossa in EXACT ed esclusa dalla FRASE con negativa esatta. Configurazione coerente."))
        c = ov["conflicts"][0]
        self.assertEqual(c["text"], "Il termine è negativa esatta nella campagna FRASE, ma non risulta presente in una campagna EXACT. Verifica se la promozione è stata completata.")
        w = ov["products"][0]
        self.assertEqual((w["badge"], w["asin"]), ("TARGET PRODOTTO VINCENTE", "0794851630"))
        self.assertEqual(w["reason"], "Ha generato 3 acquisti con ACOS 32,1% nei termini/prodotti recenti.")
        self.assertIsNone(w["operational"])                                                          # senza decisione: nessuna etichetta inventata
        self.assertEqual((ov["kpi"]["exact"], ov["kpi"]["negatives"]), (0, 0))                       # ne' promosse ne' prodotti gonfiano i KPI
        self.assertEqual(ov["kpi"]["conflicts"], 1)


PA = 'asin="0794851630"'


def winner_ov(decision, **rec_over):
    recs = [tov.opp("0794851630", "WINNING_PRODUCT_TARGET", campaign_id="PRODOTTO", campaign_type="COMPETITOR", parent_target=PA, orders=3.0, acos=32.1,
                    product_asin="0794851630", target_status="ENABLED", **rec_over)]
    return tov.build([decision] if decision else [tov.dec(99)], recs)


def pdec(**o):
    base = dict(campaign_id="PRODOTTO", target=PA, target_type="PRODUCT_TARGET", search_term=None, lifetime_clicks=87.0, lifetime_orders=7.0, lifetime_spend=62.6,
                lifetime_acos=37.3, tactical_clicks=43.0, tactical_orders=3.0, tactical_acos=42.1)
    base.update(o)
    return tov.dec(1, **base)


class ProductWinnerStateTests(unittest.TestCase):
    """La card TARGET PRODOTTO VINCENTE e' informativa: lo stato operativo e' quello del decision layer, mai una decisione propria."""

    def op(self, d):
        return winner_ov(d)["products"][0]["operational"]

    def test_hold_is_do_not_touch(self):
        op = self.op(pdec(final_action="HOLD", tactical_acos=30.0, lifetime_acos=30.0))
        self.assertEqual(op["word"], "NON TOCCARE")

    def test_watch_is_monitor(self):
        self.assertEqual(self.op(pdec(final_action="WATCH"))["word"], "MONITORA")

    def test_hold_shown_by_the_page_as_monitor_stays_monitor_with_target_acos(self):
        d = pdec(final_action="HOLD", recent_vs_historical_signal="STABLE_SIGNAL")           # HOLD con recente debole: la pagina lo mostra in DA MONITORARE
        ov = winner_ov(d)
        self.assertEqual([c["badge"] for c in ov["monitor"]], ["MONITORA"])
        op = ov["products"][0]["operational"]
        self.assertEqual(op["word"], "MONITORA")
        self.assertEqual(op["text"], "Il target complessivo ha ACOS 42,1% negli ultimi 30 giorni. Per ora non modificare l'offerta.")

    def test_bid_down_is_lower_bid(self):
        op = self.op(pdec(final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, operationally_actionable=1))
        self.assertEqual(op["word"], "ABBASSA OFFERTA")
        self.assertIn("Decisione sul target: abbassa offerta del 5%.", op["text"])

    def test_bid_up_and_pause_words(self):
        self.assertEqual(self.op(pdec(final_action="BID_UP_5_CANDIDATE", bid_change_pct=5.0, operationally_actionable=1))["word"], "ALZA OFFERTA")
        self.assertEqual(self.op(pdec(final_action="PAUSE_CANDIDATE", operationally_actionable=1))["word"], "METTI IN PAUSA")

    def test_paused_target_is_already_paused(self):
        op = self.op(pdec(final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_spend=62.6))
        self.assertEqual((op["word"], op["text"]), ("GIÀ IN PAUSA", "Il target è già in pausa. Nessuna azione necessaria."))

    def test_without_decision_only_information(self):
        ov = winner_ov(None)
        self.assertIsNone(ov["products"][0]["operational"])
        self.assertEqual(ov["products"][0]["orders"], 3.0)

    def test_decision_of_the_other_targeting_type_is_not_used_and_no_duplicate_decision(self):
        other = pdec(target='asin-expanded="0794851630"', final_action="WATCH")
        ov = winner_ov(other)
        self.assertIsNone(ov["products"][0]["operational"])                                          # asin-expanded != asin
        ov2 = winner_ov(pdec(final_action="WATCH"))
        self.assertEqual(len(ov2["monitor"]) + len(ov2["actions"]) + len(ov2["good"]) + len(ov2["paused"]), len(ov2["all_ids"]))   # nessuna decisione in piu'
        self.assertEqual(ov2["kpi"]["exact"], 0)


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(tov.REAL_DB.is_file(), "DB reale assente")
class RealCoinBookTests(unittest.TestCase):
    """Rare Coins Book for Beginners (B0GY42H3W4, US, run #12): SOLA LETTURA su una COPIA del database reale."""

    @classmethod
    def setUpClass(cls):
        src = sqlite3.connect(f"{tov.REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        dst.close()
        src.close()
        chk = sqlite3.connect(cls.db)
        cls.available = bool(chk.execute("SELECT 1 FROM decision_runs WHERE run_id=12 AND book_id='B0GY42H3W4'").fetchone())
        chk.close()
        if not cls.available:
            return
        cls.books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        cls.old = os.environ.get("KDP_ADS_DB")
        os.environ["KDP_ADS_DB"] = str(cls.db)
        cls.hash_before = hashlib.sha256(cls.db.read_bytes()).hexdigest()
        at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
        tov.select(at, "Libro", "B0GY42H3W4")
        tov.select(at, "Run decisionale", "#12")
        cls.at = at
        cls.text = "\n".join(t for t, in_exp, _ in tov.page_text(at) if not in_exp)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "old", None) is None:
            os.environ.pop("KDP_ADS_DB", None)
        else:
            os.environ["KDP_ADS_DB"] = cls.old
        cls.tmp.cleanup()

    def setUp(self):
        if not self.available:
            self.skipTest("libro reale B0GY42H3W4 / run #12 non presente")

    def block(self, start, end):
        return self.text.split(start)[1].split(end)[0]

    def test_page_ok_and_coin_collection_supplies_is_promoted_not_conflict(self):
        self.assertFalse(self.at.exception)
        self.assertNotIn("CONFLITTO DA VERIFICARE", self.text)
        promoted = self.block("KEYWORD GIÀ PROMOSSE IN EXACT", "TARGET PRODOTTO VINCENTE")
        self.assertIn("**coin collection supplies**", promoted)
        self.assertRegex(promoted, r"PROMOSSA IN EXACT( · ATTUALMENTE IN PAUSA)?")
        self.assertIn("attualmente in pausa", promoted)                       # l'EXACT reale e' IN PAUSA

    def test_asin_is_not_an_exact_keyword_but_a_winning_product_target(self):
        exact = self.block("KEYWORD DA METTERE IN EXACT", "KEYWORD DA VALUTARE COME NEGATIVE")
        self.assertNotIn("0794851630", exact)
        self.assertIn("**rare coin book**", exact)                              # resta una vera opportunita' EXACT
        prod = self.block("TARGET PRODOTTO VINCENTE\n", "EROGAZIONE DA VERIFICARE")
        self.assertIn("ASIN 0794851630", prod)
        self.assertIn("Ha generato 3 acquisti con ACOS 32,1% nei termini/prodotti recenti.", prod)
        self.assertNotIn("STATO OPERATIVO", prod)                    # il run #12 salvato non ha una decisione per asin= (bug identita' storico)

    def test_delivery_main_view_has_no_reference_impressions(self):
        deliv = self.block("EROGAZIONE DA VERIFICARE\nTarget attivi", "TARGET IN PAUSA RILEVANTI")
        self.assertIn("NESSUNA EROGAZIONE", deliv)
        self.assertNotIn("Impressioni di riferimento", self.text)
        self.assertNotIn("Impressioni di riferimento", deliv)

    def test_paused_targets_without_proof_get_the_neutral_text(self):
        self.assertIn("È già in pausa. Nessuna azione necessaria.", self.text)
        self.assertNotIn("Era inefficiente, ma", self.text)

    def test_auto_substitutes_is_monitored_with_the_new_rule_on_a_db_copy(self):
        from kdp_ads.config import CONFIG_DIR, load_settings
        from kdp_ads.decision import build_decisions
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            ds = build_decisions(conn, realdata.settings(), CONFIG_DIR / "rules.json", "B0GY42H3W4", None, logging.getLogger("t"), "US")
        finally:
            conn.close()
        d = [x for x in ds.decisions if x["campaign_id"] == "AUTO" and x["target"] == "substitutes"][0]
        self.assertEqual((d["final_action"], d["decision_rule"]), ("WATCH", "C4B_MILD_DETERIORATION_MONITOR"))
        self.assertIsNone(d["bid_change_pct"])
        self.assertFalse(d["operationally_actionable"])

    def test_nothing_written_anywhere(self):
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.hash_before)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)


if __name__ == "__main__":
    unittest.main()
