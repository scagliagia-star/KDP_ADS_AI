"""RICERCA COMPETITOR · KEYWORD DI LANCIO: criterio operativo "almeno un competitor con Organic Rank o Sponsored Rank da 1 a 10"."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_competitor_research as T
from kdp_ads import cerebro, competitor as C, competitor_store as S
from kdp_ads.db import connect

r = T.row
FA = [r("short stories for seniors", 3200, 4, None, 7, 100, 1000), r("mystery books for seniors", 125, 1, None, 3, 100, 100),
      r("large print books", 7900, 10, None, 5, 60000, 100), r("cozy tales for seniors", 500, None, 5, 6, 1000, 50), r("sunny days stories for seniors", 300, 5, 8, 4, 900, 50),
      r("rank eleven stories for seniors", 300, 11, 11, 4, 900, 50), r("rank zero stories for seniors", 300, 0, 0, 4, 900, 50), r("null stories for seniors", 300, None, None, 4, 900, 50),
      r("golden girls trivia for seniors", 900, 2, None, 4, 900, 50), r("existing exact phrase for seniors", 400, 3, None, 4, 900, 50),
      r("new phrase for seniors", 400, 6, None, 4, 900, 50), r("negated phrase for seniors", 400, 7, None, 4, 900, 50)]
FB = [r("short stories for seniors", 3200, 7, None, 7, 100, 1000), r("large print books", 7900, 14, 12, 5, 60000, 100), r("cozy tales for seniors", 500, None, 25, 6, 1000, 50)]
FC = [r("short stories for seniors", 3200, 9, None, 7, 100, 1000), r("large print books", 7900, 40, None, 5, 60000, 100)]
FILES = ((T.A1, FA), (T.A2, FB), (T.A3, FC))


def agg_of(files=FILES):
    return C.aggregate([{"asin": a, "rows": cerebro.merge_duplicate_rows(T.parsed(a, rows)["rows"])} for a, rows in files])


class LaunchLogicTests(unittest.TestCase):
    def setUp(self):
        self.agg = agg_of()
        self.k = {x["keyword_norm"]: x for x in self.agg["keywords"]}
        self.items = {i["keyword_norm"]: i for i in C.launch_keywords(self.agg, C.config())}

    def test_rank_boundaries(self):
        self.assertTrue(all(C.is_top10(x) for x in (1, 5, 10)))
        self.assertFalse(any(C.is_top10(x) for x in (11, 0, -1, None)))
        self.assertEqual(C.top10_fields({"A": {"organic_rank": 10, "sponsored_rank": 11}})["top10_category"], "ORGANIC")
        self.assertEqual(C.top10_fields({"A": {"organic_rank": 11, "sponsored_rank": 1}})["top10_category"], "SPONSORED")
        self.assertEqual(C.top10_fields({"A": {"organic_rank": 3}, "B": {"sponsored_rank": 9}})["top10_category"], "BOTH")      # su competitor diversi
        self.assertEqual(C.top10_fields({"A": {"organic_rank": 0, "sponsored_rank": None}})["top10_category"], "NONE")

    def test_categories_organic_sponsored_both_none(self):
        self.assertEqual(self.k["sunny days stories for seniors"]["top10_category"], "BOTH")
        self.assertEqual(self.k["short stories for seniors"]["top10_category"], "ORGANIC")
        self.assertEqual(self.k["cozy tales for seniors"]["top10_category"], "SPONSORED")
        for n in ("rank eleven stories for seniors", "rank zero stories for seniors", "null stories for seniors"):
            self.assertEqual(self.k[n]["top10_category"], "NONE", n)                           # 11, 0 e mancante non sono top 10
            self.assertNotIn(n, self.items)                                                     # e non sono keyword di lancio
        self.assertTrue(self.k["short stories for seniors"]["organic_top10"] and not self.k["short stories for seniors"]["sponsored_top10"])
        self.assertEqual(C.LAUNCH_LETTER, {"BOTH": "A", "ORGANIC": "B", "SPONSORED": "C", "NONE": "D"})

    def test_rank_zero_in_csv_is_not_a_rank(self):
        p = cerebro.parse_cerebro("US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv", b'"Keyword Phrase","Organic Rank","Sponsored Rank"\n"a b",0,-\n"c d",3,0\n')
        self.assertEqual([(x["organic_rank"], x["sponsored_rank"]) for x in p["rows"]], [(None, None), (3, None)])

    def test_priority_order_category_then_multi_competitor(self):
        order = [i["keyword_norm"] for i in C.launch_keywords(self.agg, C.config())]
        self.assertEqual(order[0], "sunny days stories for seniors")                          # BOTH prima di tutto
        a, b = order.index("short stories for seniors"), order.index("mystery books for seniors")
        self.assertLess(a, b)                                                                  # 3/3 (rank 4,7,9) prima di 1/3 (rank 1)...
        self.assertIn("mystery books for seniors", order)                                      # ...ma la 1/3 non e' cancellata
        self.assertEqual(self.k["short stories for seniors"]["competitor_share"], "3/3")
        organic = [x for x in order if self.k[x]["top10_category"] == "ORGANIC" and not self.k[x]["generic_keyword"]]
        self.assertEqual([self.k[x]["competitor_count"] for x in organic], sorted((self.k[x]["competitor_count"] for x in organic), reverse=True))
        self.assertLess(order.index("sunny days stories for seniors"), order.index("cozy tales for seniors"))      # BOTH prima di SPONSORED

    def test_generic_is_visible_but_demoted(self):
        i = self.items["large print books"]
        self.assertTrue(i["generic"])
        self.assertEqual(i["badge"], "TOP 10 · GENERICA")
        self.assertEqual(i["category"], "ORGANIC")
        order = [x["keyword_norm"] for x in C.launch_keywords(self.agg, C.config())]
        self.assertGreater(order.index("large print books"), order.index("mystery books for seniors"))     # dopo le organiche non generiche, anche se 3/3 e volume 7900
        self.assertGreater(order.index("large print books"), order.index("short stories for seniors"))
        self.assertTrue(self.k["large print books"]["generic_keyword"])
        self.assertFalse(self.k["short stories for seniors"]["generic_keyword"])

    def test_filters(self):
        items = list(self.items.values())
        names = lambda l: {x["keyword_norm"] for x in l}                                          # noqa: E731
        self.assertEqual(names(C.filter_launch(items, "BOTH")), {"sunny days stories for seniors"})
        self.assertIn("sunny days stories for seniors", names(C.filter_launch(items, "ORGANIC")))
        self.assertIn("sunny days stories for seniors", names(C.filter_launch(items, "SPONSORED")))
        self.assertNotIn("short stories for seniors", names(C.filter_launch(items, "SPONSORED")))
        self.assertEqual(names(C.filter_launch(items, min_competitors=3)), {"short stories for seniors", "large print books"})
        self.assertIn("cozy tales for seniors", names(C.filter_launch(items, min_competitors=2)))
        self.assertNotIn("mystery books for seniors", names(C.filter_launch(items, min_competitors=2)))
        self.assertNotIn("large print books", names(C.filter_launch(items, exclude_generic=True)))
        self.assertNotIn("golden girls trivia for seniors", names(C.filter_launch(items, exclude_brand=True)))
        self.assertIn("golden girls trivia for seniors", names(C.filter_launch(items)))             # senza filtro resta visibile
        self.assertEqual(names(C.filter_launch(items, min_volume=1000)), {"short stories for seniors", "large print books"})

    def test_brand_is_excluded_from_suggestions(self):
        i = self.items["golden girls trivia for seniors"]
        self.assertEqual((i["status"], i["action"]), ("BRAND", None))
        self.assertEqual(i["score"]["excluded"], "BRAND_OR_IP")


class LaunchStoreTests(T.Base):
    def project(self, book_id=None):
        rid = S.create_project(self.conn, book_id, None, "US", now=T.NOW)
        for asin, rows in FILES:
            S.import_source(self.conn, rid, T.parsed(asin, rows), now=T.NOW)
        return rid

    def test_existing_book_status_against_campaigns(self):
        rid = self.project("B0HLL1Y1CN")
        own = T.own(phrase=["cozy tales for seniors"], exact=["existing exact phrase for seniors"], neg=["negated phrase for seniors"],
                    observed=[{"search_term": "mystery books for seniors", "clicks": 3, "orders": 0, "spend": 1}])
        a = S.analyze(self.conn, rid, own=own)
        st = {i["keyword_norm"]: i for i in a["launch"]}
        self.assertEqual(st["existing exact phrase for seniors"]["status"], "IN_EXACT")
        self.assertEqual(st["negated phrase for seniors"]["status"], "NEGATIVE")
        self.assertEqual(st["cozy tales for seniors"]["status"], "IN_PHRASE")
        self.assertEqual(st["mystery books for seniors"]["status"], "OBSERVED")
        self.assertEqual(st["new phrase for seniors"]["status"], "NEW")
        self.assertEqual(st["new phrase for seniors"]["status_label"], "NUOVA — TESTA IN FRASE")
        self.assertEqual(st["new phrase for seniors"]["action"], "TESTA IN FRASE")
        self.assertEqual(st["large print books"]["status"], "GENERIC")
        self.assertEqual(st["large print books"]["status_label"], "GENERICA / DA VALUTARE")
        self.assertIsNone(st["large print books"]["action"])
        for n in ("existing exact phrase for seniors", "negated phrase for seniors", "cozy tales for seniors"):
            self.assertIsNone(st[n]["action"])                                                     # gia' Exact / negativa / in frase: non proposte
        self.assertTrue(all(i["action"] in (None, "TESTA IN FRASE") for i in a["launch"]))       # mai Exact
        exp = {i["keyword_norm"] for i in C.launch_expansion(a["launch"])}
        self.assertIn("new phrase for seniors", exp)
        self.assertFalse(exp & {"existing exact phrase for seniors", "negated phrase for seniors", "large print books", "golden girls trivia for seniors"})
        self.assertEqual(a["overview"]["launch"]["both"], 1)

    def test_new_opportunity_future_keywords_without_ads_decisions(self):
        rid = self.project(None)
        a = S.analyze(self.conn, rid)
        self.assertIsNone(a["gap"])
        self.assertIsNone(a["ads"])
        launch = {i["keyword_norm"]: i for i in a["launch"]}
        self.assertIn("short stories for seniors", launch)
        self.assertTrue(all(i["action"] is None for i in launch.values()))                        # nessuna decisione Ads
        self.assertEqual(launch["short stories for seniors"]["status_label"], "KEYWORD POTENZIALE PER IL FUTURO LANCIO")
        self.assertEqual(launch["golden girls trivia for seniors"]["status"], "BRAND")

    def test_growth_section_has_launch_expansion_and_leaves_ads_untouched(self):
        self.project("B0HLL1Y1CN")
        tabs = ("decisions", "decision_runs", "recommendations", "target_rows", "search_term_rows", "imports")
        before = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}
        g = S.growth_section(self.conn, "B0HLL1Y1CN", "US", own=T.own(exact=["existing exact phrase for seniors"]))
        launch = {i["keyword_norm"] for i in g["projects"][0]["launch"]}
        self.assertIn("new phrase for seniors", launch)
        self.assertNotIn("existing exact phrase for seniors", launch)
        self.assertNotIn("large print books", launch)
        self.assertGreaterEqual(g["total"], len(launch))
        self.assertEqual({t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}, before)

    def test_engine_files_do_not_reference_launch_keywords_module(self):
        for f in ("analysis.py", "decision.py", "rules.py", "opportunities.py", "growth.py"):
            src = (ROOT / "SCRIPTS" / "kdp_ads" / f).read_text(encoding="utf-8")
            for bad in ("launch_keywords", "competitor_store", "cerebro", "kdp_ads.competitor", "import competitor"):
                self.assertNotIn(bad, src, f"{f}: {bad}")

    def test_books_json_untouched(self):
        bj = ROOT / "CONFIG" / "books.json"
        h = hashlib.sha256(bj.read_bytes()).hexdigest()
        S.analyze(self.conn, self.project("B0HLL1Y1CN"))
        self.assertEqual(hashlib.sha256(bj.read_bytes()).hexdigest(), h)


class ClusterTierTests(T.Base):
    """OPPORTUNITA' PRINCIPALI (specifiche) vs SEGNALI SECONDARI (generici standalone o deboli): nessun cluster cancellato, i generici mai FORTE."""

    def kw(self, text, asins=("A", "B", "C"), vol=90000):
        return {"keyword": text, "keyword_norm": text, "share": len(asins) / 3, "asins": list(asins), "competitor_count": len(asins), "best_organic_rank": 1,
                "search_volume": vol, "title_density": 1, "competing_products": 10, "per_competitor": {}, "brand_or_ip": False, "is_asin_like": False}

    def opp(self, topic, audience=(), words=("a", "b", "c", "d")):
        cl = {"cluster_key": "|".join(topic) + "~" + "|".join(audience), "topic": list(topic), "audience": list(audience),
              "keywords": [self.kw(f"{' '.join(topic)} {w}") for w in words]}
        o = C.book_opportunity(cl, 3, C.config())
        o["tier"], o["secondary_reason"] = C.classify_tier(o)
        return o

    def test_standalone_generic_clusters_are_secondary_and_never_strong(self):
        for topic, aud in ((("story",), ()), (("mystery",), ()), (("book",), ()), ((), ("senior",)), ((), ("woman",)), (("print",), ())):
            o = self.opp(topic, aud)
            self.assertEqual(o["tier"], "SECONDARIO", (topic, aud))
            self.assertNotEqual(o["score_label"], "FORTE", (topic, aud))                 # nemmeno con volume 90000 e 3/3 competitor
            self.assertTrue(o["generic_standalone"])

    def test_specific_clusters_stay_primary(self):
        for topic, aud in ((("short", "story"), ("senior",)), (("mystery",), ("senior",)), (("mystery",), ("senior", "woman")), (("funny", "story"), ("senior",))):
            o = self.opp(topic, aud)
            self.assertEqual(o["tier"], "PRINCIPALE", (topic, aud))
            self.assertIsNone(o["secondary_reason"])
        self.assertEqual(self.opp(("mystery",), ("senior",))["score_label"], "FORTE")           # il volume alto + 3/3 + specifico resta FORTE

    def test_weak_specific_cluster_is_secondary_not_deleted(self):
        weak = C.book_opportunity({"cluster_key": "x|y~", "topic": ["x", "y"], "audience": [], "keywords": [self.kw("x y a", ("A",), 10), self.kw("x y b", ("A",), 10)]}, 3, C.config())
        self.assertEqual(C.classify_tier(weak)[0], "SECONDARIO")

    def test_build_opportunities_splits_without_losing_clusters_and_ui_lists(self):
        agg = agg_of()
        o = C.build_opportunities(agg, C.config())
        self.assertEqual(len(o["opportunities"]), len(o["primary"]) + len(o["secondary"]))
        self.assertEqual({x["cluster_key"] for x in o["opportunities"]}, {x["cluster_key"] for x in o["primary"] + o["secondary"]})
        first_secondary = next((i for i, x in enumerate(o["opportunities"]) if x["tier"] != "PRINCIPALE"), len(o["opportunities"]))
        self.assertTrue(all(x["tier"] == "PRINCIPALE" for x in o["opportunities"][:first_secondary]))   # i principali vengono sempre prima
        ov = C.overview(agg, None, o, "NEW_OPPORTUNITY")
        self.assertEqual((len(ov["clusters"]), ov["secondary_count"], ov["clusters_total"]), (len(o["primary"]), len(o["secondary"]), len(o["opportunities"])))
        self.assertTrue(all(x["tier"] == "PRINCIPALE" for x in o["primary"]))


if __name__ == "__main__":
    unittest.main()
