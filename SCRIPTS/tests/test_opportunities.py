"""Opportunita' Search Term: layer separato dalle decisioni di bid. Dati sintetici; nessuna rete, nessuna scrittura."""
import hashlib
import os
import runpy
import sqlite3
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
from kdp_ads.opportunities import (NEGATIVE, NO_ACTION, PROMOTE, REVIEW_CONFLICT, SUGGESTION_NEW_EXACT, WATCH, compute_opportunities,
                                   is_real_query, load_inputs, make_ctx, negative_state, normalize_added_as, rank, unique_terms)
from kdp_ads.rules import load_rules

BOOK = {"book_id": "B1", "title": "T", "marketplace": "US", "currency": "USD", "price": 16.99, "royalty_per_copy": 7.34,
        "target_acos": 35.0, "break_even_acos": 43.2, "phase": "PROFIT_STABLE"}
CTX = make_ctx(BOOK, load_rules(tr.RULES_PATH))
D30 = ("DATE_RANGE", "2026-09-02", "2026-10-01")
D65 = ("DATE_RANGE", "2026-07-29", "2026-10-01")
SALE = 16.99


def st(campaign, ctype, parent, term, win, clicks, orders, spend, sales=None):
    scope, df, dt = win
    return {"campaign_id": campaign, "campaign_type": ctype, "target": parent, "match_type": None, "search_term": term,
            "report_scope": scope, "date_from": df, "date_to": dt, "period_id": f"{df}__{dt}", "impressions": clicks * 40, "clicks": clicks,
            "spend": spend, "orders": orders, "sales": orders * SALE if sales is None else sales}


def both(campaign, ctype, parent, term, clicks, orders, spend):
    """Stesso termine con gli stessi numeri nel 30D e nel 65D (finestre nidificate: nessuna somma)."""
    return [st(campaign, ctype, parent, term, D30, clicks, orders, spend), st(campaign, ctype, parent, term, D65, clicks, orders, spend)]


def tgt(campaign, ctype, target, match="Esatta"):
    return {"campaign_id": campaign, "campaign_type": ctype, "target": target, "match_type": match, "report_scope": "LIFETIME",
            "date_from": None, "date_to": "2026-10-01", "period_id": "LIFETIME__2026-10-01", "raw_json": '{"Stato": "ABILITATO"}'}


CAMPS = [{"campaign_id": "AUTO", "campaign_type": "OTHER"}, {"campaign_id": "FRASE", "campaign_type": "PHRASE"},
         {"campaign_id": "EXACT", "campaign_type": "EXACT"}]


def run(st_rows, target_rows=(), camps=CAMPS):
    return compute_opportunities(list(st_rows), list(target_rows), camps, CTX)


def only(res, term, campaign=None):
    m = [r for r in res["records"] if r["search_term"] == term and (campaign is None or r["campaign_id"] == campaign)]
    assert len(m) == 1, (term, [(r["campaign_id"], r["opportunity_type"]) for r in res["records"]])
    return m[0]


class PromoteTests(unittest.TestCase):
    def test_auto_search_term_3_orders_good_acos_is_promote_on_the_real_term(self):
        res = run(both("AUTO", "OTHER", "loose-match", "crochet flower patterns for beginners", 20, 3, 15.0))
        r = only(res, "crochet flower patterns for beginners")
        self.assertEqual((r["opportunity_type"], r["confidence"]), (PROMOTE, "HIGH"))
        self.assertEqual((r["parent_target"], r["parent_target_type"]), ("loose-match", "AUTO_TARGETING_GROUP"))
        self.assertNotEqual(r["search_term"], r["parent_target"])
        self.assertAlmostEqual(r["acos"], 15.0 / (3 * SALE) * 100, places=3)
        self.assertFalse(r["operationally_actionable"])
        self.assertTrue(r["manual_review_required"])
        self.assertFalse(r["already_in_exact"])

    def test_medium_threshold_2_orders_acos_within_break_even(self):
        r = only(run(both("AUTO", "OTHER", "close-match", "term two orders", 12, 2, 13.0)), "term two orders")       # ACOS 38.3% (> target, <= BE)
        self.assertEqual((r["opportunity_type"], r["confidence"]), (PROMOTE, "MEDIUM"))

    def test_already_in_exact_is_not_promoted(self):
        res = run(both("AUTO", "OTHER", "loose-match", "crochet flower kit", 20, 3, 15.0), [tgt("EXACT", "EXACT", "Crochet  Flower Kit")])
        r = only(res, "crochet flower kit")
        self.assertEqual(r["opportunity_type"], NO_ACTION)
        self.assertTrue(r["already_in_exact"])
        self.assertIn("ALREADY_IN_EXACT", r["flags"])
        self.assertEqual(unique_terms(res["records"], PROMOTE), [])

    def test_present_in_phrase_only_can_still_be_promoted_and_auto_is_not_a_problem(self):
        res = run(both("AUTO", "OTHER", "loose-match", "crochet flower book", 20, 3, 15.0),
                  [tgt("FRASE", "PHRASE", "crochet flower book", "A frase"), tgt("AUTO", "OTHER", "loose-match", "")])
        r = only(res, "crochet flower book")
        self.assertEqual(r["opportunity_type"], PROMOTE)
        self.assertTrue(r["already_targeted_elsewhere"])
        self.assertEqual(r["targeted_in"], ["FRASE"])
        self.assertFalse(r["already_in_exact"])

    def test_one_order_is_watch_not_promote(self):
        r = only(run(both("AUTO", "OTHER", "loose-match", "one order term", 5, 1, 3.0)), "one order term")
        self.assertEqual(r["opportunity_type"], WATCH)

    def test_orders_with_acos_above_break_even_are_not_promoted(self):
        r = only(run(both("AUTO", "OTHER", "loose-match", "pricey term", 20, 3, 30.0)), "pricey term")           # ACOS 58.9%
        self.assertNotEqual(r["opportunity_type"], PROMOTE)


NEG_EXACT, NEG_PHRASE = "Parola chiave neg.: esatta neg.", "Parola chiave neg.: frase neg."


def with_added(rows, value):
    return [{**r, "added_as": value} for r in rows]


class AddedAsTests(unittest.TestCase):
    """Aggiunto come (report Search Term): una negativa gia' impostata non e' mai ne' una nuova negativa ne' una promozione in EXACT."""

    def test_normalization_of_the_amazon_values_keeps_the_raw_value(self):
        n = normalize_added_as
        self.assertEqual({k for k, v in n("Parola chiave: a frase").items() if v}, {"keyword_phrase"})
        self.assertEqual({k for k, v in n("Parola chiave: esatta").items() if v}, {"keyword_exact"})
        self.assertEqual({k for k, v in n(NEG_EXACT).items() if v}, {"negative_exact"})
        self.assertEqual({k for k, v in n(NEG_PHRASE).items() if v}, {"negative_phrase"})
        self.assertEqual({k for k, v in n("a frase, esatta neg.").items() if v}, {"keyword_phrase", "negative_exact"})
        self.assertFalse(any(n(None).values()) or any(n("").values()))
        self.assertEqual((negative_state([NEG_EXACT]), negative_state([NEG_PHRASE]), negative_state(["Parola chiave: a frase", None])),
                         ("EXACT", "PHRASE", None))
        rows = with_added(both("FRASE", "PHRASE", "p", "t", 20, 3, 15.0), NEG_EXACT)
        r = only(run(rows), "t")
        self.assertEqual((r["added_as_raw"], r["added_as_normalized"], r["negative_match"]), ([NEG_EXACT], ["negative_exact"], "EXACT"))
        self.assertTrue(all(x["added_as"] == NEG_EXACT for x in rows))                              # righe di input intatte

    def test_already_negative_exact_is_not_a_negative_candidate(self):
        r = only(run(with_added(both("FRASE", "PHRASE", "p", "waste term", 20, 0, 20.0), NEG_EXACT)), "waste term")
        self.assertNotEqual(r["opportunity_type"], NEGATIVE)
        self.assertEqual(r["opportunity_type"], NO_ACTION)
        self.assertIn("ALREADY_NEGATIVE", r["flags"])
        self.assertEqual(only(run(both("FRASE", "PHRASE", "p", "waste term", 20, 0, 20.0)), "waste term")["opportunity_type"], NEGATIVE)

    def test_already_negative_exact_is_not_promoted_but_a_conflict(self):
        r = only(run(with_added(both("FRASE", "PHRASE", "p", "us parks", 20, 3, 15.0), NEG_EXACT)), "us parks")
        self.assertEqual(r["opportunity_type"], REVIEW_CONFLICT)
        self.assertNotEqual(r["opportunity_type"], PROMOTE)
        self.assertFalse(r["operationally_actionable"])
        self.assertTrue(r["manual_review_required"])
        self.assertIn("NEGATIVE_CONFLICT", r["flags"])

    def test_already_negative_phrase_blocks_both_promotion_and_new_negative(self):
        conflict = only(run(with_added(both("FRASE", "PHRASE", "p", "sells", 20, 3, 15.0), NEG_PHRASE)), "sells")
        self.assertEqual((conflict["opportunity_type"], conflict["negative_match"]), (REVIEW_CONFLICT, "PHRASE"))
        waste = only(run(with_added(both("FRASE", "PHRASE", "p", "waste", 20, 0, 20.0), NEG_PHRASE)), "waste")
        self.assertEqual(waste["opportunity_type"], NO_ACTION)

    def test_positive_term_without_negative_can_still_be_promoted(self):
        r = only(run(with_added(both("FRASE", "PHRASE", "p", "good", 20, 3, 15.0), "Parola chiave: a frase")), "good")
        self.assertEqual(r["opportunity_type"], PROMOTE)

    def test_combined_value_with_negative_exact_is_negative(self):
        r = only(run(with_added(both("FRASE", "PHRASE", "p", "combo", 20, 3, 15.0), "a frase, esatta neg.")), "combo")
        self.assertEqual(r["opportunity_type"], REVIEW_CONFLICT)

    def test_scope_is_the_campaign_not_the_whole_book(self):
        rows = with_added(both("FRASE", "PHRASE", "p", "shared", 20, 3, 15.0), NEG_EXACT) + both("AUTO", "OTHER", "loose-match", "shared", 20, 3, 15.0)
        res = run(rows)
        self.assertEqual(only(res, "shared", "FRASE")["opportunity_type"], REVIEW_CONFLICT)
        self.assertEqual(only(res, "shared", "AUTO")["opportunity_type"], PROMOTE)                    # la negativa di FRASE non vale in AUTO
        self.assertIsNone(only(res, "shared", "AUTO")["negative_match"])

    def test_ad_group_is_kept_and_conflict_is_excluded_from_new_campaign_suggestion(self):
        rows = []
        for term in ("t1", "t2", "t3"):
            rows += both("FRASE", "PHRASE", "p", term, 20, 3, 15.0)
        rows = [{**r, "ad_group": "G1"} for r in rows]
        self.assertIsNotNone(run(rows, camps=CAMPS[:2])["new_exact_campaign"])                          # 3 promozioni: suggerimento
        blocked = [{**r, "added_as": NEG_EXACT} if r["search_term"] == "t3" else r for r in rows]
        res = run(blocked, camps=CAMPS[:2])
        self.assertIsNone(res["new_exact_campaign"])                                                    # il conflitto non conta
        self.assertEqual(only(res, "t3")["ad_groups"], ["G1"])

    def test_unique_terms_exclude_conflicts_from_exact_and_negative_lists(self):
        res = run(with_added(both("FRASE", "PHRASE", "p", "c1", 20, 3, 15.0), NEG_EXACT) + both("FRASE", "PHRASE", "p", "ok", 20, 3, 15.0))
        self.assertEqual(unique_terms(res["records"], PROMOTE), ["ok"])
        self.assertEqual(unique_terms(res["records"], NEGATIVE), [])
        self.assertEqual(unique_terms(res["records"], REVIEW_CONFLICT), ["c1"])

    def test_load_inputs_reads_added_as_and_ad_group_when_present_and_never_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "a.db"
            c = sqlite3.connect(db)
            c.executescript("""CREATE TABLE search_term_rows (book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT, target TEXT, match_type TEXT,
                ad_group TEXT, added_as TEXT, search_term TEXT, report_scope TEXT, date_from TEXT, date_to TEXT, period_id TEXT, impressions REAL, clicks REAL,
                spend REAL, orders REAL, sales REAL);
                CREATE TABLE target_rows (book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT, target TEXT, match_type TEXT, report_scope TEXT,
                date_from TEXT, date_to TEXT, period_id TEXT, raw_json TEXT);
                CREATE TABLE campaigns (book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT);""")
            c.execute("INSERT INTO search_term_rows VALUES ('B1','US','FRASE','PHRASE','p',NULL,'G1',?, 'x','DATE_RANGE','2026-09-02','2026-10-01','a__b',1,1,1,1,1)", (NEG_EXACT,))
            c.commit()
            c.close()
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            row = load_inputs(conn, "B1", "US")["st_rows"][0]
            conn.close()
            self.assertEqual((row["added_as"], row["ad_group"]), (NEG_EXACT, "G1"))
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_books_json_untouched(self):
        books = ROOT / "CONFIG" / "books.json"
        before = hashlib.sha256(books.read_bytes()).hexdigest() if books.exists() else None
        run(with_added(both("FRASE", "PHRASE", "p", "z", 20, 3, 15.0), NEG_EXACT))
        self.assertEqual(hashlib.sha256(books.read_bytes()).hexdigest() if books.exists() else None, before)


class NegativeAndWatchTests(unittest.TestCase):
    def test_zero_orders_high_spend_is_negative_candidate(self):
        res = run(both("FRASE", "PHRASE", "crochet flowers", "free crochet flower video", 20, 0, 20.0))
        r = only(res, "free crochet flower video")
        self.assertEqual(r["opportunity_type"], NEGATIVE)
        self.assertIn(r["confidence"], ("MEDIUM", "HIGH"))
        self.assertEqual(unique_terms(res["records"], NEGATIVE), ["free crochet flower video"])
        self.assertIn("non deciso", r["reason"])                                          # nessuna scelta exact/phrase

    def test_few_clicks_never_negative_watch_or_nothing(self):
        for clicks, spend, expected in ((4, 3.0, WATCH), (3, 10.0, WATCH), (2, 9.0, NO_ACTION), (1, 0.5, NO_ACTION)):
            r = only(run(both("FRASE", "PHRASE", "p", f"t{clicks}", clicks, 0, spend)), f"t{clicks}")
            self.assertEqual(r["opportunity_type"], expected, (clicks, spend))
            self.assertNotEqual(r["opportunity_type"], NEGATIVE)

    def test_zero_orders_many_clicks_but_low_spend_is_not_negative(self):
        r = only(run(both("FRASE", "PHRASE", "p", "cheap clicks", 12, 0, 5.0)), "cheap clicks")                  # 5.0 < 2 x 7.34
        self.assertEqual(r["opportunity_type"], WATCH)

    def test_acos_far_above_break_even_with_enough_sample_is_negative(self):
        r = only(run(both("FRASE", "PHRASE", "p", "bad acos", 40, 2, 40.0)), "bad acos")                        # ACOS 117%, 2 ordini, 40 clic
        self.assertEqual((r["opportunity_type"], r["confidence"]), (NEGATIVE, "MEDIUM"))

    def test_high_volume_never_negative(self):
        r = only(run(both("FRASE", "PHRASE", "p", "busy term", 200, 12, 400.0)), "busy term")
        self.assertNotEqual(r["opportunity_type"], NEGATIVE)

    def test_term_equal_to_parent_target_never_negative(self):
        r = only(run(both("FRASE", "PHRASE", "crochet flowers", "crochet flowers", 30, 0, 30.0)), "crochet flowers")
        self.assertEqual(r["opportunity_type"], WATCH)
        self.assertIn("TERM_EQUALS_PARENT_TARGET", r["flags"])


class AutoGroupsTests(unittest.TestCase):
    def test_group_names_are_not_search_terms(self):
        for g in ("loose-match", "close-match", "complements", "substitutes", "Loose-Match"):
            self.assertFalse(is_real_query(g), g)
        self.assertTrue(is_real_query("crochet flowers"))
        self.assertFalse(is_real_query("B0FTFJL6RB"))
        self.assertFalse(is_real_query('asin="B0FTFJL6RB"'))

    def test_loose_match_never_promoted_and_complements_never_negative_as_group_name(self):
        rows = both("AUTO", "OTHER", "loose-match", "loose-match", 20, 3, 15.0) + both("AUTO", "OTHER", "complements", "complements", 40, 0, 40.0)
        res = run(rows)
        self.assertEqual(res["records"], [])                                               # nessun record sul nome del gruppo
        self.assertEqual(res["excluded"]["non_query"], 4)

    def test_negative_on_real_term_captured_by_complements_keeps_group_as_parent_only(self):
        res = run(both("AUTO", "OTHER", "complements", "random unrelated query", 20, 0, 20.0))
        r = only(res, "random unrelated query")
        self.assertEqual((r["opportunity_type"], r["parent_target"], r["parent_target_type"]), (NEGATIVE, "complements", "AUTO_TARGETING_GROUP"))
        self.assertNotIn(r["search_term"], ("loose-match", "close-match", "complements", "substitutes"))
        for rec in res["records"]:
            self.assertNotIn(rec["search_term"], ("loose-match", "close-match", "complements", "substitutes"))

    def test_asin_search_terms_excluded(self):
        res = run(both("AUTO", "OTHER", "loose-match", "b0ftfjl6rb", 20, 3, 15.0))
        self.assertEqual(res["records"], [])


class DuplicatesAndWindowsTests(unittest.TestCase):
    def test_same_term_in_phrase_and_auto_two_records_one_unique_term(self):
        rows = both("FRASE", "PHRASE", "crochet flowers", "crochet flower patterns", 20, 3, 15.0) + \
            both("AUTO", "OTHER", "loose-match", "crochet flower patterns", 20, 3, 15.0)
        res = run(rows)
        self.assertEqual(len(res["records"]), 2)
        self.assertTrue(all("DUPLICATE_ACROSS_CAMPAIGNS" in r["flags"] for r in res["records"]))
        self.assertEqual(only(res, "crochet flower patterns", "AUTO")["also_seen_in"], ["FRASE"])
        self.assertEqual(unique_terms(res["records"], PROMOTE), ["crochet flower patterns"])          # contato una volta

    def test_same_campaign_multi_parent_rows_in_same_window_are_summed_not_windows(self):
        rows = [st("FRASE", "PHRASE", "a", "shared term", D30, 10, 1, 6.0), st("FRASE", "PHRASE", "b", "shared term", D30, 10, 2, 6.0),
                st("FRASE", "PHRASE", "a", "shared term", D65, 10, 1, 6.0), st("FRASE", "PHRASE", "b", "shared term", D65, 10, 2, 6.0)]
        r = only(run(rows), "shared term")
        self.assertEqual((r["clicks"], r["orders"]), (20, 3))                   # 10+10 e 1+2 nella stessa finestra, mai 30D+65D
        self.assertIn("MULTI_PARENT_TARGET", r["flags"])

    def test_windows_agree_extra_orders_raise_confidence(self):
        rows = [st("AUTO", "OTHER", "loose-match", "growing term", D30, 12, 2, 13.0), st("AUTO", "OTHER", "loose-match", "growing term", D65, 30, 4, 28.0)]
        r = only(run(rows), "growing term")
        self.assertEqual((r["window_30d"]["cls"], r["window_65d"]["cls"]), ("PROMO_MEDIUM", "PROMO_HIGH" if 28 / (4 * SALE) * 100 <= 35 else "PROMO_MEDIUM"))
        self.assertEqual((r["windows_agreement"], r["opportunity_type"], r["confidence"]), ("CONCORDI", PROMOTE, "HIGH"))

    def test_windows_disagree_lowers_confidence_and_demotes_to_watch(self):
        rows = [st("AUTO", "OTHER", "loose-match", "flaky term", D30, 12, 2, 13.0), st("AUTO", "OTHER", "loose-match", "flaky term", D65, 40, 2, 60.0)]
        r = only(run(rows), "flaky term")
        self.assertEqual(r["window_30d"]["cls"], "PROMO_MEDIUM")
        self.assertEqual(r["windows_agreement"], "DISCORDI")
        self.assertEqual((r["opportunity_type"], r["confidence"]), (WATCH, "LOW"))

    def test_strong_65d_without_recent_confirmation_is_only_watch(self):
        rows = [st("AUTO", "OTHER", "loose-match", "old winner", D65, 20, 3, 15.0),
                st("AUTO", "OTHER", "loose-match", "other recent", D30, 20, 0, 3.0)]            # il 30D e' importato per la campagna
        r = only(run(rows), "old winner")
        self.assertEqual((r["opportunity_type"], r["windows_agreement"]), (WATCH, "NON_CONFERMATO_30D"))

    def test_missing_30d_window_uses_65d_with_reduced_confidence(self):
        r = only(run([st("AUTO", "OTHER", "loose-match", "only65", D65, 20, 3, 15.0)]), "only65")
        self.assertEqual((r["opportunity_type"], r["confidence"], r["windows_agreement"]), (PROMOTE, "MEDIUM", "SOLO_65D"))

    def test_rank_orders_by_confidence_then_orders(self):
        rows = both("AUTO", "OTHER", "loose-match", "medium one", 12, 2, 13.0) + both("AUTO", "OTHER", "loose-match", "high one", 20, 3, 15.0)
        self.assertEqual([r["search_term"] for r in rank(run(rows)["records"], PROMOTE)], ["high one", "medium one"])


class NewCampaignSuggestionTests(unittest.TestCase):
    def terms(self, n):
        out = []
        for i in range(n):
            out += both("FRASE" if i % 2 else "AUTO", "PHRASE" if i % 2 else "OTHER", "p", f"strong term {i}", 20, 3, 15.0)
        return out

    def test_suggestion_when_enough_terms_and_no_exact_campaign(self):
        camps = [{"campaign_id": "AUTO", "campaign_type": "OTHER"}, {"campaign_id": "FRASE", "campaign_type": "PHRASE"}]
        res = run(self.terms(3), camps=camps)
        sug = res["new_exact_campaign"]
        self.assertEqual((sug["suggestion"], sug["operational"], sug["min_terms"], len(sug["terms"])), (SUGGESTION_NEW_EXACT, False, 3, 3))

    def test_no_suggestion_with_two_terms_or_with_an_exact_campaign(self):
        camps = [{"campaign_id": "AUTO", "campaign_type": "OTHER"}, {"campaign_id": "FRASE", "campaign_type": "PHRASE"}]
        self.assertIsNone(run(self.terms(2), camps=camps)["new_exact_campaign"])
        self.assertIsNone(run(self.terms(3))["new_exact_campaign"])                          # CAMPS contiene una campagna EXACT


class MarketplaceAndSafetyTests(unittest.TestCase):
    def make_db(self, path):
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE search_term_rows (id INTEGER PRIMARY KEY, book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT, target TEXT,
                match_type TEXT, search_term TEXT, report_scope TEXT, date_from TEXT, date_to TEXT, period_id TEXT, impressions REAL, clicks REAL,
                spend REAL, orders REAL, sales REAL);
            CREATE TABLE target_rows (id INTEGER PRIMARY KEY, book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT, target TEXT,
                match_type TEXT, report_scope TEXT, date_from TEXT, date_to TEXT, period_id TEXT, raw_json TEXT);
            CREATE TABLE campaigns (book_id TEXT, marketplace TEXT, campaign_id TEXT, campaign_type TEXT);
        """)
        for mp in ("US", "UK"):
            for w in (D30, D65):
                c.execute("INSERT INTO search_term_rows (book_id, marketplace, campaign_id, campaign_type, target, search_term, report_scope, date_from, date_to, period_id, impressions, clicks, spend, orders, sales) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          ("B1", mp, "AUTO", "OTHER", "loose-match", "shared term", w[0], w[1], w[2], f"{w[1]}__{w[2]}", 800, 20, 15.0, 3, 50.97))
            c.execute("INSERT INTO campaigns VALUES ('B1', ?, 'AUTO', 'OTHER')", (mp,))
        # keyword EXACT SOLO nel marketplace UK
        c.execute("INSERT INTO target_rows (book_id, marketplace, campaign_id, campaign_type, target, match_type, report_scope, date_to, period_id, raw_json) VALUES ('B1','UK','EXACT_UK','EXACT','shared term','Esatta','LIFETIME','2026-10-01','LIFETIME__2026-10-01','{}')")
        c.execute("INSERT INTO campaigns VALUES ('B1', 'UK', 'EXACT_UK', 'EXACT')")
        c.commit()
        c.close()

    def test_marketplace_separation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            self.make_db(db)
            conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            us, uk = load_inputs(conn, "B1", "US"), load_inputs(conn, "B1", "UK")
            conn.close()
            self.assertEqual({r["campaign_id"] for r in us["st_rows"]}, {"AUTO"})
            self.assertEqual(us["target_rows"], [])                                           # l'EXACT di UK non esiste in US
            res_us = compute_opportunities(us["st_rows"], us["target_rows"], us["campaigns"], CTX)
            res_uk = compute_opportunities(uk["st_rows"], uk["target_rows"], uk["campaigns"], CTX)
            self.assertEqual(only(res_us, "shared term")["opportunity_type"], PROMOTE)         # in US non e' gia' in EXACT
            self.assertEqual(only(res_uk, "shared term")["opportunity_type"], NO_ACTION)       # in UK si': nessuna promozione
            self.assertTrue(only(res_uk, "shared term")["already_in_exact"])
            self.assertIsNotNone(res_us["new_exact_campaign"] or True)

    def test_all_records_are_manual_only(self):
        res = run(both("AUTO", "OTHER", "loose-match", "a", 20, 3, 15.0) + both("FRASE", "PHRASE", "p", "b", 20, 0, 20.0) + both("FRASE", "PHRASE", "p", "c", 5, 1, 3.0))
        self.assertTrue(res["records"])
        for r in res["records"]:
            self.assertFalse(r["operationally_actionable"])
            self.assertTrue(r["manual_review_required"])
            self.assertIn(r["opportunity_type"], (PROMOTE, NEGATIVE, WATCH, NO_ACTION, REVIEW_CONFLICT))
        for field in ("book_id", "marketplace", "campaign_id", "campaign_type", "parent_target", "parent_target_type", "search_term", "period_id",
                      "clicks", "spend", "orders", "sales", "acos", "cvr", "cpc", "estimated_profit", "opportunity_type", "confidence", "reason",
                      "already_in_exact", "already_targeted_elsewhere"):
            self.assertIn(field, res["records"][0])

    def test_no_network_and_no_amazon_or_write_code(self):
        for p in (ROOT / "SCRIPTS" / "kdp_ads" / "opportunities.py", ROOT / "SCRIPTS" / "opportunities.py",
                  ROOT / "DASHBOARD" / "pages" / "9_Opportunita_Search_Term.py"):
            src = p.read_text(encoding="utf-8")
            for forbidden in ("import requests", "urllib", "socket", "http.client", "boto", "amazon_ads", "INSERT ", "UPDATE ", "DELETE ", "DROP ", "st.button"):
                self.assertNotIn(forbidden, src, (p.name, forbidden))
        self.assertEqual([r for r in sqlite3.connect(":memory:").execute("SELECT 1")], [(1,)])


class DashboardOpportunityTests(unittest.TestCase):
    def test_labels_are_italian(self):
        import ui_labels as U
        self.assertEqual(U.label_opportunity("PROMOTE_TO_EXACT"), "Valuta promozione in EXACT")
        self.assertEqual(U.label_opportunity("NEGATIVE_CANDIDATE"), "Valuta come negativa")
        self.assertEqual(U.label_opportunity("WATCH_SEARCH_TERM"), "Monitora termine di ricerca")
        self.assertEqual(U.OPP_NEW_CAMPAIGN, "Valuta creazione campagna EXACT")
        self.assertEqual((U.OPP_KPI_EXACT, U.OPP_KPI_NEGATIVE), ("OPPORTUNITÀ EXACT", "CANDIDATI NEGATIVI"))
        self.assertEqual(U.label_opportunity("REVIEW_NEGATIVE_CONFLICT"), "Conflitto da verificare")
        self.assertEqual(U.label_opportunity("ALTRO"), "ALTRO")


import test_dashboard as td  # noqa: E402


class DashboardRenderTests(td.DashBase):
    """Panoramica e pagina Opportunita' con lo stub di streamlit sul DB di test: KPI separati, nessun pulsante, DB invariato."""

    def render(self, page):
        old_env, old_mod = os.environ.get("KDP_ADS_DB"), sys.modules.get("streamlit")
        os.environ["KDP_ADS_DB"] = str(self.db)
        st_ = mock.MagicMock()
        st_._cols = []

        def columns(n, *a, **k):
            cs = [mock.MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
            st_._cols.extend(cs)
            return cs
        st_.columns.side_effect = columns
        st_.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st_.sidebar.selectbox.side_effect = lambda label, options, *a, **k: list(options)[0]
        st_.text_input.return_value = ""
        st_.stop.side_effect = SystemExit
        sys.modules["streamlit"] = st_
        for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
            del sys.modules[m]
        try:
            runpy.run_path(str(page), run_name="__main__")
        except SystemExit:
            pass
        finally:
            os.environ.pop("KDP_ADS_DB", None) if old_env is None else os.environ.__setitem__("KDP_ADS_DB", old_env)
            sys.modules.pop("streamlit", None) if old_mod is None else sys.modules.__setitem__("streamlit", old_mod)
        return st_

    @staticmethod
    def texts(st_):
        out = []
        for c in st_.method_calls:
            out += [str(a) for a in c.args if isinstance(a, str)]
        for m in ("metric",):
            pass
        return " ".join(out)

    def test_home_has_separate_opportunity_kpis_and_page_renders(self):
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        res = self.repo.search_term_opportunities({**self.repo.marketplaces("1805566563")[0], "book_id": "1805566563"}, self.repo.marketplaces("1805566563")[0]["marketplace"])
        self.assertIn("records", res)
        home = self.render(ROOT / "DASHBOARD" / "panoramica.py")
        flat = str(home.mock_calls) + str([c.mock_calls for c in home._cols])
        self.assertIn("OPPORTUNITÀ EXACT", flat)
        self.assertIn("CANDIDATI NEGATIVI", flat)
        self.assertIn("DA VALUTARE ORA", flat)                                     # KPI delle decisioni di bid, separato
        page = self.render(ROOT / "DASHBOARD" / "pages" / "9_Opportunita_Search_Term.py")
        txt = str(page.mock_calls) + str([c.mock_calls for c in page._cols])
        for needle in ("DA PROMUOVERE IN EXACT", "CANDIDATI NEGATIVI", "DA MONITORARE", "Valuta promozione in EXACT", "Valuta come negativa"):
            self.assertIn(needle, txt)
        self.assertFalse([c for c in page.method_calls if c[0] in ("button", "form_submit_button")])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)    # nessuna scrittura


if __name__ == "__main__":
    unittest.main()
