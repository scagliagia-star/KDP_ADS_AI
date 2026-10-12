"""RICERCA COMPETITOR (Helium 10 Cerebro): parser, aggregazione multi-competitor, gap, punteggi, clustering, brand/IP, persistenza, migrazione, non regressione.
Schema Cerebro SINTETICO ma con le intestazioni reali dell'export (Keyword Phrase, Cerebro IQ Score, Search Volume, H10 PPC Sugg. Bid, Title Density, ...)."""
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "SCRIPTS"))

from kdp_ads import cerebro, competitor as C, competitor_store as S
from kdp_ads.db import connect

HEADERS = ["Keyword Phrase", "Cerebro IQ Score", "Search Volume", "Search Volume Trend", "H10 PPC Sugg. Bid", "H10 PPC Sugg. Min Bid", "H10 PPC Sugg. Max Bid",
           "Sponsored ASINs", "Competing Products", "CPR", "Title Density", "Competitor Rank (avg)", "Ranking Competitors (count)", "Keyword Sales",
           "Amazon Recommended Rank", "Sponsored Rank", "Organic Rank"]
A1, A2, A3 = "B0H5MZP4QW", "1997182017", "B0H8YTL55T"          # formato dei tre ASIN dell'esperimento (non usati come valori attesi dal codice)
NOW = "2026-10-12T10:00:00"


def row(kw, vol=None, org=None, spons=None, td=None, cp=None, sales=None, bid=None):
    d = {"Keyword Phrase": kw, "Search Volume": vol, "Organic Rank": org, "Sponsored Rank": spons, "Title Density": td, "Competing Products": cp,
         "Keyword Sales": sales, "H10 PPC Sugg. Bid": bid}
    return d


def csv_bytes(rows, headers=HEADERS):
    out = io.StringIO()
    out.write(",".join(f'"{h}"' for h in headers) + "\n")
    for r in rows:
        out.write(",".join('"' + ("" if r.get(h) is None else str(r.get(h))) + '"' for h in headers) + "\n")
    return out.getvalue().encode("utf-8")


def fname(asin):
    return f"US_AMAZON_cerebro_{asin}_2026-10-12.csv"


# keyword -> (volume, organic rank, sponsored rank, title density, competing products, sales, bid) per competitor
F1 = [row("short stories for seniors", 5200, 4, 12, 8, 4200, 300, 0.9), row("Large Print Short Stories For Seniors", 2100, 7, None, 6, 3900, 120, 0.8),
      row("mystery books for seniors", 3100, 12, 3, 15, 9000, 200, 1.1), row("large print mystery books", 2600, 20, None, 18, 12000, 150, 1.0),
      row("large print mystery for senior women", 400, 9, None, 3, 800, 25, 0.7), row("golden girls books", 1500, 30, None, 12, 3000, 90, 0.6),
      row("books", 100000, 80, None, 400, 5000000, 9000, 0.4), row("short stories for seniors", 5200, 9, 15, 8, 4200, 300, 0.9)]   # doppione nel file
F2 = [row("Short Stories for Seniors", 5000, 6, None, 9, 4300, 280, 1.0), row("large print short stories for seniors", 2000, 11, 8, 7, 4000, 110, 0.9),
      row("mystery books for seniors", 3000, 5, None, 14, 9100, 210, 1.2), row("large print mystery books", 2500, 33, 6, 20, 11000, 140, 1.1),
      row("senior women mystery books", 350, 15, None, 4, 900, 30, 0.7), row("golden girls books", 1400, 22, 18, 11, 3100, 80, 0.7)]
F3 = [row("short stories for seniors", 5100, 15, None, 9, 4100, 310, 1.0), row("large print short stories for seniors", 2150, 25, None, 6, 3950, 130, 0.85),
      row("mystery books for seniors", 3050, 8, 9, 16, 9050, 205, 1.15), row("large print mystery for senior women", 420, 40, None, 4, 850, 20, 0.75),
      row("senior women mystery books", 360, 18, None, 4, 880, 28, 0.7), row("golden girls books", 1450, 19, None, 10, 3050, 85, 0.65)]


def parsed(asin, rows):
    return cerebro.parse_cerebro(fname(asin), csv_bytes(rows))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "k.db"
        self.conn = connect(self.db_path)
        self.conn.execute("INSERT INTO books (book_id,title,marketplace,currency) VALUES ('B0HLL1Y1CN','Twenty Women, Twenty Mysteries','US','USD')")
        self.conn.execute("INSERT INTO books (book_id,title,marketplace,currency) VALUES ('BOOK2','Chair Tai Chi for Weight Loss After 50','US','USD')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def project(self, book_id=None, files=((A1, F1), (A2, F2), (A3, F3)), name=None, title=None):
        rid = S.create_project(self.conn, book_id, name, "US", book_title=title, now=NOW)
        for asin, rows in files:
            r = S.import_source(self.conn, rid, parsed(asin, rows), now=NOW)
            self.assertEqual(r["status"], "IMPORTED", r)
        return rid

    def agg(self, rid):
        return S.analyze(self.conn, rid)


class ParserTests(unittest.TestCase):
    def test_asin_from_filename_and_not_from_date(self):
        self.assertEqual(cerebro.asin_from_filename("US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv"), "B0H5MZP4QW")
        self.assertEqual(cerebro.asin_from_filename("US_AMAZON_cerebro_1997182017_2026-10-12.csv"), "1997182017")
        self.assertEqual(cerebro.asin_from_filename("US_AMAZON_cerebro_B0H8YTL55T_2026-10-12.csv"), "B0H8YTL55T")
        self.assertIsNone(cerebro.asin_from_filename("export_2026-10-12.csv"))
        self.assertEqual(cerebro.marketplace_from_filename("US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv"), "US")

    def test_columns_recognized_values_raw_and_normalized(self):
        p = parsed(A1, F1)
        self.assertEqual((p["asin"], p["asin_source"]), (A1, "FILENAME"))
        canon = {c["canonical"] for c in p["columns"]}
        for c in ("keyword", "search_volume", "cerebro_iq", "competing_products", "title_density", "organic_rank", "sponsored_rank", "ppc_bid", "ppc_bid_min",
                  "ppc_bid_max", "competitor_rank_avg", "ranking_competitors", "keyword_sales", "amazon_recommended_rank", "sponsored_asins"):
            self.assertIn(c, canon)
        r = next(x for x in p["rows"] if x["keyword"] == "Large Print Short Stories For Seniors")
        self.assertEqual(r["keyword_norm"], "large print short stories for seniors")             # normalizzata, originale conservata
        self.assertEqual(r["search_volume"], 2100)
        self.assertEqual(r["raw"]["Search Volume"], "2100")                                        # valore originale
        self.assertIsNone(r["sponsored_rank"])                                                     # mancante = None, mai inventato

    def test_tolerant_to_missing_columns_dashes_and_thousands(self):
        data = b'Keyword Phrase,Search Volume,Organic Rank\n"Mystery  Books","1,234","-"\nfoo,,5\n'
        p = cerebro.parse_cerebro("x.csv", data, asin="b0h5mzp4qw")
        self.assertEqual(p["asin"], "B0H5MZP4QW")
        self.assertEqual(p["asin_source"], "MANUAL")
        self.assertEqual(p["rows"][0]["keyword_norm"], "mystery books")
        self.assertEqual((p["rows"][0]["search_volume"], p["rows"][0]["organic_rank"]), (1234, None))
        self.assertIsNone(p["rows"][0]["title_density"])

    def test_errors(self):
        with self.assertRaises(cerebro.CerebroError):
            cerebro.parse_cerebro("x.csv", b"")
        with self.assertRaises(cerebro.CerebroError):
            cerebro.parse_cerebro("x.csv", b"a,b\n1,2\n")                                          # nessuna colonna keyword
        with self.assertRaises(cerebro.CerebroError):
            cerebro.parse_cerebro("x.xlsx", b"abc")
        with self.assertRaises(cerebro.CerebroError):
            cerebro.parse_cerebro("x.csv", b"Keyword Phrase\nfoo\n", asin="123")

    def test_duplicate_keywords_in_one_file_merge_to_best_rank(self):
        rows = cerebro.merge_duplicate_rows(parsed(A1, F1)["rows"])
        self.assertEqual(len(rows), 7)
        r = next(x for x in rows if x["keyword_norm"] == "short stories for seniors")
        self.assertEqual((r["organic_rank"], r["sponsored_rank"]), (4, 12))


class ImportTests(Base):
    def test_import_with_book_selected(self):
        rid = self.project("B0HLL1Y1CN", name=None)
        p = S.get_project(self.conn, rid)
        self.assertEqual((p["mode"], p["book_id"], p["status"], p["marketplace"]), ("EXISTING_BOOK", "B0HLL1Y1CN", "RESEARCH", "US"))
        self.assertEqual(p["name"], "Competitor Research — Twenty Women, Twenty Mysteries — 2026-10-12")
        self.assertEqual(len(S.list_sources(self.conn, rid)), 3)

    def test_import_without_book(self):
        rid = self.project(None)
        p = S.get_project(self.conn, rid)
        self.assertEqual((p["mode"], p["book_id"]), ("NEW_OPPORTUNITY", None))
        self.assertEqual(p["name"], "Nuova ricerca 2026-10-12")
        S.update_project(self.conn, rid, name="Senior mystery", status="REVIEW")
        self.assertEqual((S.get_project(self.conn, rid)["name"], S.get_project(self.conn, rid)["status"]), ("Senior mystery", "REVIEW"))
        with self.assertRaises(S.StoreError):
            S.update_project(self.conn, rid, status="NOPE")

    def test_book_only_in_books_json_gets_minimal_row_for_fk(self):
        rid = S.create_project(self.conn, "NEWBOOK1", None, "US", book_title="Solo in books.json", now=NOW)
        self.assertEqual(self.conn.execute("SELECT title FROM books WHERE book_id='NEWBOOK1'").fetchone()[0], "Solo in books.json")
        self.assertEqual(S.get_project(self.conn, rid)["book_title"], "Solo in books.json")

    def test_file_provenance_kept(self):
        rid = self.project(None)
        s = S.list_sources(self.conn, rid)
        self.assertEqual({x["asin"] for x in s}, {A1, A2, A3})
        x = next(i for i in s if i["asin"] == A1)
        self.assertEqual((x["file_name"], x["imported_at"], x["status"], x["research_id"]), (fname(A1), NOW, "ACTIVE", rid))
        self.assertEqual(len(x["file_sha256"]), 64)

    def test_import_is_repeatable_and_never_overwrites_silently(self):
        rid = self.project(None)
        before = self.conn.execute("SELECT COUNT(*) FROM competitor_keyword_observations").fetchone()[0]
        again = S.import_source(self.conn, rid, parsed(A1, F1), now=NOW)                          # stesso file
        self.assertEqual(again["status"], "DUPLICATE_FILE")
        other = S.import_source(self.conn, rid, parsed(A1, F1[:3]), now=NOW)                      # stesso ASIN, altro file, senza replace
        self.assertEqual(other["status"], "ASIN_EXISTS")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM competitor_keyword_observations").fetchone()[0], before)
        rep = S.import_source(self.conn, rid, parsed(A1, F1[:3]), replace=True, now="2026-10-13T09:00:00")
        self.assertEqual(rep["status"], "REPLACED")
        st = {(x["asin"], x["status"]) for x in S.list_sources(self.conn, rid)}
        self.assertEqual(st, {(A1, "SUPERSEDED"), (A1, "ACTIVE"), (A2, "ACTIVE"), (A3, "ACTIVE")})  # storico conservato
        self.assertEqual(len(S.list_sources(self.conn, rid, active_only=True)), 3)
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM competitor_keyword_observations").fetchone()[0], before)

    def test_missing_asin_and_max_five(self):
        rid = S.create_project(self.conn, None, None, "US", now=NOW)
        p = cerebro.parse_cerebro("export.csv", csv_bytes(F1))
        with self.assertRaises(S.StoreError):
            S.import_source(self.conn, rid, p, now=NOW)
        self.assertEqual(S.import_source(self.conn, rid, p, asin="B0AAAAAAA1", now=NOW)["status"], "IMPORTED")     # ASIN a mano
        for i in range(2, 6):
            S.import_source(self.conn, rid, cerebro.parse_cerebro(f"e{i}.csv", csv_bytes(F2[:i])), asin=f"B0AAAAAAA{i}", now=NOW)
        with self.assertRaises(S.StoreError):
            S.import_source(self.conn, rid, cerebro.parse_cerebro("e6.csv", csv_bytes(F3)), asin="B0AAAAAAA6", now=NOW)

    def test_same_keyword_across_projects_and_marketplaces_independent(self):
        r1 = self.project(None)
        r2 = S.create_project(self.conn, None, None, "UK", now=NOW)
        S.import_source(self.conn, r2, parsed(A1, F1), now=NOW)
        self.assertEqual(S.get_project(self.conn, r2)["marketplace"], "UK")
        self.assertEqual(len(S.load_agg_sources(self.conn, r1)), 3)
        self.assertEqual(len(S.load_agg_sources(self.conn, r2)), 1)


class AggregationTests(Base):
    def test_dedup_and_competitor_share(self):
        rid = self.project(None)
        agg = self.agg(rid)["agg"]
        kws = {k["keyword_norm"]: k for k in agg["keywords"]}
        self.assertEqual(agg["total"], 3)
        self.assertEqual(len(agg["keywords"]), len({k["keyword_norm"] for k in agg["keywords"]}))          # una riga per keyword
        k = kws["short stories for seniors"]
        self.assertEqual((k["competitor_count"], k["competitor_share"]), (3, "3/3"))
        self.assertEqual(sorted(k["asins"]), sorted([A1, A2, A3]))
        self.assertEqual(kws["large print mystery books"]["competitor_share"], "2/3")                      # A1 + A2
        self.assertEqual(kws["senior women mystery books"]["competitor_share"], "2/3")                     # A2 + A3
        self.assertEqual(kws["books"]["competitor_share"], "1/3")
        self.assertEqual(set(k["per_competitor"]), {A1, A2, A3})                                          # relazione keyword <-> ASIN
        self.assertEqual(k["keyword"], "short stories for seniors")                                       # forma piu' frequente

    def test_rank_aggregation_and_sponsored(self):
        k = {x["keyword_norm"]: x for x in self.agg(self.project(None))["agg"]["keywords"]}["short stories for seniors"]
        self.assertEqual(k["best_organic_rank"], 4)                          # A1 4 (dopo merge del doppione 4/9), A2 6, A3 15
        self.assertEqual(k["median_organic_rank"], 6)
        self.assertEqual(k["organic_rank_count"], 3)
        self.assertEqual(k["sponsored_competitor_count"], 1)                 # solo A1
        self.assertEqual(k["best_sponsored_rank"], 12)
        self.assertEqual(k["search_volume"], 5100)                           # mediana di 5200 / 5000 / 5100
        self.assertEqual(k["search_volume_max"], 5200)
        self.assertEqual(k["title_density"], 9)                              # mediana 8/9/9
        self.assertEqual(k["suggested_bid"], 1.0)

    def test_common_keywords_order_not_by_volume_only(self):
        agg = self.agg(self.project(None))["agg"]
        order = [k["keyword_norm"] for k in agg["keywords"]]
        self.assertEqual(order.index("short stories for seniors") < order.index("large print mystery books"), True)
        top3 = [k for k in agg["keywords"] if k["competitor_count"] == 3]
        self.assertEqual(order[:len(top3)], [k["keyword_norm"] for k in top3])                            # prima chi e' su piu' competitor
        self.assertEqual(order[-1], "books")                                                              # 100000 di volume ma su 1 solo competitor
        ranks = [k["best_organic_rank"] for k in top3]
        self.assertEqual(ranks, sorted(ranks))                                                            # poi rank organico

    def test_filters(self):
        agg = self.agg(self.project(None))["agg"]["keywords"]
        names = lambda l: {k["keyword_norm"] for k in l}                                                 # noqa: E731
        self.assertEqual(len(C.filter_common(agg, min_competitors=3)), 4)
        self.assertIn("large print mystery books", names(C.filter_common(agg, min_competitors=2)))
        self.assertNotIn("books", names(C.filter_common(agg, min_competitors=2)))
        self.assertEqual(names(C.filter_common(agg, min_volume=4000)), {"short stories for seniors", "books"})
        self.assertNotIn("books", names(C.filter_common(agg, max_organic_rank=10)))
        self.assertTrue(all(k["sponsored_competitor_count"] > 0 for k in C.filter_common(agg, sponsored=True)))
        self.assertTrue(all(k["sponsored_competitor_count"] == 0 for k in C.filter_common(agg, sponsored=False)))
        self.assertTrue(all(k["title_density"] is not None and k["title_density"] <= 6 for k in C.filter_common(agg, max_title_density=6)))
        self.assertEqual(names(C.filter_common(agg, text="WOMEN")), {"large print mystery for senior women", "senior women mystery books"})

    def test_reliability_scales_with_competitors(self):
        self.assertEqual([C.reliability_level(n) for n in (1, 2, 3, 4, 5)], ["BASSA", "BASSA", "MEDIA", "ALTA", "ALTA"])
        rid = self.project(None, files=((A1, F1),))
        a = self.agg(rid)
        self.assertEqual(a["overview"]["reliability"], "BASSA")
        self.assertTrue(all(k["share"] == 1 for k in a["agg"]["keywords"]))


def own(phrase=(), exact=(), neg=(), observed=()):
    t = [{"target": x, "match_type": "A frase", "campaign_type": "PHRASE", "campaign_name": "Frase"} for x in phrase]
    t += [{"target": x, "match_type": "Esatta", "campaign_type": "EXACT", "campaign_name": "Exact"} for x in exact]
    return C.build_own_context(t, observed, neg)


class GapAndAdsTests(Base):
    def gap(self, rid, o):
        a = S.analyze(self.conn, rid, own=o)
        return a, {r["keyword_norm"]: r for r in a["gap"]}

    def test_gap_categories(self):
        rid = self.project("B0HLL1Y1CN")
        o = own(phrase=["mystery books for seniors"], exact=["short stories for seniors"], neg=["golden girls books"],
                observed=[{"search_term": "large print mystery books", "clicks": 4, "orders": 1, "spend": 2.0}])
        _, g = self.gap(rid, o)
        self.assertEqual(g["mystery books for seniors"]["gap"]["primary"], "IN_PHRASE")
        self.assertEqual(g["short stories for seniors"]["gap"]["primary"], "IN_EXACT")
        self.assertEqual(g["golden girls books"]["gap"]["primary"], "NEGATIVE")
        self.assertEqual(g["large print mystery books"]["gap"]["primary"], "OBSERVED_SEARCH_TERM")
        self.assertEqual(g["large print short stories for seniors"]["gap"]["primary"], "NOT_PRESENT")
        self.assertEqual({C.GAP_LETTER[c] for r in g.values() for c in r["gap"]["categories"]}, {"A", "B", "C", "D", "E"})

    def test_new_keyword_is_test_in_phrase_never_exact_and_existing_not_suggested(self):
        rid = self.project("B0HLL1Y1CN")
        o = own(phrase=["mystery books for seniors"], exact=["short stories for seniors"], neg=["large print mystery books"])
        a, _ = self.gap(rid, o)
        sug = {s["keyword_norm"]: s for s in a["ads"]}
        self.assertIn("large print short stories for seniors", sug)
        self.assertEqual(sug["large print short stories for seniors"]["action"], "TESTA IN FRASE")
        self.assertEqual(sug["large print short stories for seniors"]["present"], "3/3")
        self.assertNotIn("short stories for seniors", sug)                 # gia' Exact
        self.assertNotIn("large print mystery books", sug)                 # gia' negativa
        self.assertNotIn("mystery books for seniors", sug)                 # gia' in frase
        for s in a["ads"]:
            self.assertNotIn("EXACT", s["action"].upper().replace("TESTA IN FRASE", ""))
            self.assertEqual(s["source"], "Helium 10 · fonte esterna")
        self.assertIn("Ricorre su più competitor", sug["large print short stories for seniors"]["reason"])

    def test_brand_ip_and_generic_never_suggested(self):
        rid = self.project("B0HLL1Y1CN")
        a, g = self.gap(rid, own())
        sug = {s["keyword_norm"] for s in a["ads"]}
        self.assertNotIn("golden girls books", sug)
        self.assertNotIn("books", sug)
        self.assertTrue(g["golden girls books"]["agg"]["brand_or_ip"])
        self.assertEqual(g["golden girls books"]["score"]["excluded"], "BRAND_OR_IP")
        self.assertTrue(any(p["name"] == "generica" for p in g["books"]["score"]["penalties"]))

    def test_score_is_transparent_and_configurable(self):
        rid = self.project("B0HLL1Y1CN")
        a, g = self.gap(rid, own())
        sc = g["short stories for seniors"]["score"]
        self.assertEqual({c["name"] for c in sc["components"]}, {"overlap", "organic", "sponsored", "volume", "relevance", "title_density"})
        self.assertAlmostEqual(sum(c["points"] or 0 for c in sc["components"]) - sum(p["points"] for p in sc["penalties"]), sc["score"], places=0)
        self.assertTrue(all(c["note"] for c in sc["components"]))
        better = g["short stories for seniors"]["score"]["score"]
        worse = g["senior women mystery books"]["score"]["score"]
        self.assertGreater(better, worse)                                  # 3/3 + rank + volume > 2/3 + volume basso
        cfg = C.config({"competitor_research": {"ads_weights": {"overlap": 100, "organic": 0, "sponsored": 0, "volume": 0, "relevance": 0, "title_density": 0}}})
        k = g["senior women mystery books"]["agg"]
        self.assertAlmostEqual(C.ads_opportunity_score(k, cfg)["score"], k["share"] * 100, places=0)

    def test_missing_title_density_is_excluded_not_penalised(self):
        k = {x["keyword_norm"]: x for x in self.agg(self.project(None))["agg"]["keywords"]}["mystery books for seniors"]
        full = C.ads_opportunity_score(k, C.config())
        k2 = dict(k, title_density=None)
        part = C.ads_opportunity_score(k2, C.config())
        self.assertIsNone(next(c for c in part["components"] if c["name"] == "title_density")["points"])
        self.assertGreater(part["score"], full["score"] - 10)

    def test_inefficient_in_real_data_penalised_and_phrase_cover_noted(self):
        rid = self.project("B0HLL1Y1CN")
        o = own(phrase=["short stories"], observed=[{"search_term": "large print mystery books", "clicks": 25, "orders": 0, "spend": 12.0}])
        _, g = self.gap(rid, o)
        self.assertTrue(any(p["name"] == "inefficiente nei tuoi dati" for p in g["large print mystery books"]["score"]["penalties"]))
        self.assertEqual(g["short stories for seniors"]["gap"]["covered_by_phrase"], ["short stories"])

    def test_own_context_reads_db_read_only(self):
        c = self.conn
        c.execute("INSERT INTO imports (book_id,campaign_id,report_type,import_timestamp,source_filename,file_path,file_sha256,marketplace,period_id) "
                  "VALUES ('B0HLL1Y1CN','C1','TARGETING','t','f','p','h','US','P1')")
        iid = c.execute("SELECT import_id FROM imports").fetchone()[0]
        for tgt, mt, ct in (("cozy mystery", "A frase", "PHRASE"), ("senior mystery", "Esatta", "EXACT"), ("b0abcdefgh", "", "COMPETITOR")):
            c.execute("INSERT INTO target_rows (import_id,book_id,campaign_id,report_type,import_timestamp,source_filename,marketplace,period_id,target,match_type,campaign_type) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (iid, "B0HLL1Y1CN", "C1", "TARGETING", "t", "f", "US", "P1", tgt, mt, ct))
        for per, clicks in (("P1", 5), ("P2", 9)):
            c.execute("INSERT INTO search_term_rows (import_id,book_id,campaign_id,report_type,import_timestamp,source_filename,marketplace,period_id,search_term,clicks,orders,spend) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (iid, "B0HLL1Y1CN", "C1", "SEARCH_TERM", "t", "f", "US", per, "mystery for seniors", clicks, 0, 3))
        c.commit()
        o = S.own_context(c, "B0HLL1Y1CN", "US")
        self.assertIn("cozy mystery", o["phrase"])
        self.assertIn("senior mystery", o["exact"])
        self.assertEqual(o["observed"]["mystery for seniors"]["clicks"], 9)             # massimo per finestra, non la somma
        self.assertEqual(o["product_asins"], {"B0ABCDEFGH"})
        self.assertEqual(S.own_context(c, "NOBOOK", "US")["phrase"], {})


class ClusterAndBookScoreTests(Base):
    def opps(self, book_id=None, title=None, files=((A1, F1), (A2, F2), (A3, F3))):
        rid = self.project(book_id, files=files, title=title)
        a = self.agg(rid)
        return rid, a, {o["cluster_key"]: o for o in a["editorial"]["opportunities"]}

    def test_cluster_split_without_merging_everything(self):
        _, a, o = self.opps()
        self.assertEqual(set(o), {"short|story~senior", "mystery~senior", "mystery~senior|woman"})
        self.assertEqual(set(o["short|story~senior"]["keyword_norms"]), {"short stories for seniors", "large print short stories for seniors"})
        self.assertEqual(set(o["mystery~senior"]["keyword_norms"]), {"mystery books for seniors", "large print mystery books"})
        self.assertEqual(set(o["mystery~senior|woman"]["keyword_norms"]), {"large print mystery for senior women", "senior women mystery books"})
        self.assertEqual(o["mystery~senior|woman"]["label"], "Large Print Mystery for Senior Women")
        self.assertIn("Short Stories for Seniors", o["short|story~senior"]["label"])
        for oo in o.values():
            self.assertEqual(oo["status"], "DA APPROFONDIRE")
        self.assertEqual(C.cluster_keywords([], C.config())["clusters"], [])

    def test_clustering_is_deterministic(self):
        r1 = self.agg(self.project(None))["editorial"]["opportunities"]
        r2 = self.agg(self.project(None, name="altra"))["editorial"]["opportunities"]
        self.assertEqual([(o["cluster_key"], o["label"], o["score"]) for o in r1], [(o["cluster_key"], o["label"], o["score"]) for o in r2])

    def test_brand_excluded_from_clusters_but_visible_as_market_signal(self):
        _, a, o = self.opps()
        self.assertTrue(all("golden girls books" not in x["keyword_norms"] for x in o.values()))
        self.assertEqual([k["keyword_norm"] for k in a["editorial"]["brand_signals"]], ["golden girls books"])
        self.assertEqual(a["editorial"]["brand_signals"][0]["brand_term"], "golden girls")

    def test_manual_brand_flag_and_unflag(self):
        rid, a, _ = self.opps()
        S.set_brand_override(self.conn, rid, "mystery books for seniors", True)
        a2 = S.analyze(self.conn, rid)
        self.assertIn("mystery books for seniors", [k["keyword_norm"] for k in a2["editorial"]["brand_signals"]])
        S.set_brand_override(self.conn, rid, "golden girls books", False)                    # sblocco del rilevamento automatico
        a3 = S.analyze(self.conn, rid)
        self.assertNotIn("golden girls books", [k["keyword_norm"] for k in a3["editorial"]["brand_signals"]])
        with self.assertRaises(S.StoreError):
            S.set_brand_override(self.conn, rid, "non esiste", True)

    def test_book_score_factors_and_labels(self):
        _, a, o = self.opps()
        x = o["short|story~senior"]
        self.assertEqual({c["name"] for c in x["components"]}, {"overlap", "organic", "volume", "title_density", "competing", "coherence", "breadth", "distinct"})
        self.assertIsNone(next(c for c in x["components"] if c["name"] == "distinct")["points"])          # nessun libro origine
        self.assertIn(x["score_label"], ("DEBOLE", "DA OSSERVARE", "INTERESSANTE", "FORTE"))
        self.assertEqual((x["competitor_count"], x["competitor_share"]), (3, "3/3"))
        self.assertEqual(x["volume_sum"], 5100 + 2100)                                                   # keyword DISTINTE (mediane), non righe per competitor
        self.assertEqual(x["volume_max"], 5100)                                                         # mediana della keyword piu' forte
        self.assertIn("DISTINTE", x["volume_note"])
        self.assertNotIn("sponsored", {c["name"] for c in x["components"]})                              # sponsored non e' prova di opportunita' editoriale
        self.assertNotIn("profittevole", json.dumps(x, ensure_ascii=False).lower().replace("non è una raccomandazione a pubblicare né una stima di profittabilità", ""))
        self.assertGreater(x["score"], o["mystery~senior|woman"]["score"])                              # 3 competitor + volume > 2/3 e volume basso

    def test_strong_requires_three_keywords_and_two_competitors(self):
        cfg = C.config()
        cl = {"cluster_key": "x~", "topic": ["x", "y"], "audience": [], "keywords": []}
        k = lambda i: {"keyword": f"x {i}", "keyword_norm": f"x {i}", "share": 1.0, "asins": ["A", "B", "C"], "competitor_count": 3, "best_organic_rank": 1,  # noqa: E731
                       "search_volume": 50000, "title_density": 1, "competing_products": 10, "per_competitor": {}}
        cl["keywords"] = [k(1), k(2)]
        self.assertNotEqual(C.book_opportunity(cl, 3, cfg)["score_label"], "FORTE")                      # 2 keyword: mai FORTE
        cl["keywords"] = [k(1), k(2), k(3), k(4)]
        self.assertEqual(C.book_opportunity(cl, 3, cfg)["score_label"], "FORTE")
        self.assertEqual(C.book_opportunity(cl, 2, cfg)["score_label"], "INTERESSANTE")                  # 2 competitor = ricerca poco affidabile

    def test_existing_book_also_generates_editorial_opportunities_with_origin(self):
        _, a, o = self.opps("B0HLL1Y1CN")
        self.assertEqual(a["project"]["mode"], "EXISTING_BOOK")
        self.assertEqual(len(o), 3)
        x = o["mystery~senior|woman"]
        self.assertEqual((x["origin_book_id"], x["origin_title"]), ("B0HLL1Y1CN", "Twenty Women, Twenty Mysteries"))
        self.assertEqual(x["derived_label"], "NUOVA OPPORTUNITÀ DERIVATA")
        self.assertTrue(x["derived"])
        self.assertIsNotNone(next(c for c in x["components"] if c["name"] == "distinct")["points"])
        self.assertIsInstance(a["ads"], list)
        self.assertIsNotNone(a["gap"])

    def test_new_opportunity_generates_editorial_opportunities_without_ads_gap(self):
        _, a, o = self.opps(None)
        self.assertEqual(a["project"]["mode"], "NEW_OPPORTUNITY")
        self.assertEqual(len(o), 3)
        self.assertIsNone(a["gap"])
        self.assertIsNone(a["ads"])
        self.assertTrue(all(x["origin_book_id"] is None and not x["derived"] and x["derived_label"] is None for x in o.values()))

    def test_similar_to_origin_is_labelled_not_deleted(self):
        self.conn.execute("INSERT INTO books (book_id,title) VALUES ('B4','Short Stories for Seniors')")
        self.conn.commit()
        rid = self.project("B4")
        o = {x["cluster_key"]: x for x in S.analyze(self.conn, rid)["editorial"]["opportunities"]}
        x = o["short|story~senior"]
        self.assertTrue(x["similar_to_origin"])
        self.assertEqual(x["derived_label"], "SIMILE AL LIBRO ORIGINE")
        self.assertEqual(len(o), 3)

    def test_overlap_warning_with_existing_title_and_other_research(self):
        self.conn.execute("INSERT INTO books (book_id,title) VALUES ('B3','Short Stories for Seniors Large Print')")
        self.conn.commit()
        rid0 = self.project(None)
        a0 = S.analyze(self.conn, rid0)
        S.sync_opportunities(self.conn, rid0, a0["editorial"]["opportunities"], NOW)
        S.save_as_editorial_project(self.conn, S.list_opportunities(self.conn, rid0)[0]["opportunity_id"], now=NOW)
        rid = self.project("B0HLL1Y1CN", name="seconda")
        o = {x["cluster_key"]: x for x in S.analyze(self.conn, rid)["editorial"]["opportunities"]}
        x = o["short|story~senior"]
        self.assertEqual(x["overlap_warning"], "POTENZIALE SOVRAPPOSIZIONE CON PROGETTO ESISTENTE")
        kinds = {h["kind"] for h in x["overlaps"]}
        self.assertIn("LIBRO", kinds)
        self.assertTrue(kinds & {"RICERCA", "PROGETTO"})
        self.assertEqual(len(o), 3)                                                                      # nessun cluster scartato come duplicato


class PersistenceTests(Base):
    def test_sync_and_save_as_editorial_project(self):
        rid = self.project("B0HLL1Y1CN")
        a = S.analyze(self.conn, rid)
        r = S.sync_opportunities(self.conn, rid, a["editorial"]["opportunities"], NOW)
        self.assertEqual((r["added"], r["updated"], r["removed"]), (3, 0, 0))
        ops = S.list_opportunities(self.conn, rid)
        self.assertTrue(all(o["status"] == "DA_APPROFONDIRE" and not o["saved_as_project"] for o in ops))
        self.assertEqual(sorted(len(o["keywords"]) for o in ops), [2, 2, 2])
        books_before = self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        saved = S.save_as_editorial_project(self.conn, ops[0]["opportunity_id"], title="Titolo provvisorio", notes="da verificare", now=NOW)
        self.assertEqual((saved["status"], saved["saved_as_project"], saved["project_title"], saved["notes"]), ("REVIEW", 1, "Titolo provvisorio", "da verificare"))
        self.assertEqual(saved["origin_book_id"], "B0HLL1Y1CN")
        self.assertIsNone(saved["linked_book_id"])
        self.assertIsNone(saved["linked_asin"])                                                          # nessun ASIN assegnato
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0], books_before)    # nessun libro creato
        self.assertEqual(len(S.list_editorial_projects(self.conn, "B0HLL1Y1CN")), 1)
        again = S.sync_opportunities(self.conn, rid, a["editorial"]["opportunities"], "2026-10-13T00:00:00")
        self.assertEqual((again["added"], again["updated"]), (0, 3))
        kept = [o for o in S.list_opportunities(self.conn, rid) if o["saved_as_project"]][0]
        self.assertEqual((kept["status"], kept["project_title"], kept["notes"]), ("REVIEW", "Titolo provvisorio", "da verificare"))   # scelte dell'utente non sovrascritte
        S.set_opportunity_status(self.conn, kept["opportunity_id"], "REJECTED", NOW)
        with self.assertRaises(S.StoreError):
            S.save_as_editorial_project(self.conn, kept["opportunity_id"])
        with self.assertRaises(S.StoreError):
            S.set_opportunity_status(self.conn, kept["opportunity_id"], "BOH")

    def test_stale_untouched_clusters_removed_user_touched_kept(self):
        rid = self.project(None)
        ops = S.analyze(self.conn, rid)["editorial"]["opportunities"]
        S.sync_opportunities(self.conn, rid, ops, NOW)
        S.save_as_editorial_project(self.conn, S.list_opportunities(self.conn, rid)[0]["opportunity_id"])
        r = S.sync_opportunities(self.conn, rid, [], NOW)
        self.assertEqual(r["removed"], 2)
        self.assertEqual(len(S.list_opportunities(self.conn, rid)), 1)

    def test_raw_observations_keep_original_values(self):
        rid = self.project(None)
        S.import_source(self.conn, rid, parsed(A1, F1[:3]), replace=True, now=NOW)
        raw = S.raw_observations(self.conn, rid)
        self.assertTrue(any(r["source_status"] == "SUPERSEDED" for r in raw))
        self.assertEqual(json.loads(raw[0]["raw_json"])["Keyword Phrase"], raw[0]["keyword_original"])

    def test_growth_section_is_separate_from_ads_data(self):
        rid = self.project("B0HLL1Y1CN")
        g = S.growth_section(self.conn, "B0HLL1Y1CN", "US")
        self.assertEqual(len(g["projects"]), 1)
        p = g["projects"][0]
        self.assertTrue(p["suggestions"])
        self.assertEqual(sorted(p["new_product_targets"]), sorted([A1, A2, A3]))
        self.assertEqual(S.growth_section(self.conn, "B0HLL1Y1CN", "UK")["total"], 0)                  # altro marketplace
        self.assertEqual(S.growth_section(self.conn, "BOOK2", "US")["total"], 0)                       # altro libro
        S.update_project(self.conn, rid, status="REJECTED")
        self.assertEqual(S.growth_section(self.conn, "B0HLL1Y1CN", "US")["total"], 0)
        empty = sqlite3.connect(":memory:")
        self.assertEqual(S.growth_section(empty, "X", "US"), {"projects": [], "total": 0})             # DB senza tabelle: nessun errore

    def test_amazon_ads_tables_untouched(self):
        tabs = ("decisions", "decision_runs", "analysis_runs", "recommendations", "target_rows", "search_term_rows", "imports", "campaigns")
        before = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}
        rid = self.project("B0HLL1Y1CN")
        a = S.analyze(self.conn, rid, own=own(phrase=["x"]))
        S.sync_opportunities(self.conn, rid, a["editorial"]["opportunities"], NOW)
        S.growth_section(self.conn, "B0HLL1Y1CN", "US")
        self.assertEqual({t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}, before)

    def test_cerebro_modules_are_not_imported_by_ads_engine(self):
        for f in ("analysis.py", "decision.py", "rules.py", "opportunities.py", "growth.py", "importer.py", "pipeline.py"):
            src = (ROOT / "SCRIPTS" / "kdp_ads" / f).read_text(encoding="utf-8")
            self.assertNotIn("cerebro", src.lower(), f)
            self.assertNotIn("competitor_store", src, f)
        for f in ("analyze.py", "decide.py"):
            src = (ROOT / "SCRIPTS" / f).read_text(encoding="utf-8")
            self.assertNotIn("competitor", src.lower(), f)

    def test_books_json_not_touched_by_module(self):
        bj = ROOT / "CONFIG" / "books.json"
        h = hashlib.sha256(bj.read_bytes()).hexdigest() if bj.is_file() else None
        rid = self.project("B0HLL1Y1CN")
        S.sync_opportunities(self.conn, rid, S.analyze(self.conn, rid)["editorial"]["opportunities"], NOW)
        self.assertEqual(hashlib.sha256(bj.read_bytes()).hexdigest() if bj.is_file() else None, h)
        for f in ("cerebro.py", "competitor.py", "competitor_store.py"):                                  # il modulo non apre ne' scrive file
            src = (ROOT / "SCRIPTS" / "kdp_ads" / f).read_text(encoding="utf-8")
            for bad in ("open(", "write_text", "write_bytes", "os.remove", "shutil"):
                self.assertNotIn(bad, src, f"{f}: {bad}")


class RealFormatRegressionTests(Base):
    """Regressioni emerse dai CSV reali: colonne indicatore 0/1 ("Organic", "Sponsored Product") NON sono rank, "Competing Products" 0 = non calcolato."""

    def test_flag_columns_are_not_ranks_and_zero_competing_products_is_missing(self):
        head = ["Keyword Phrase", "Search Volume", "Competing Products", "Title Density", "Organic", "Sponsored Product", "Amazon Recommended", "Sponsored Rank", "Organic Rank"]
        lines = [",".join(f'"{h}"' for h in head), '"mystery books",500,0,0,1,0,0,"-",37', '"large print books",900,10000,2,1,1,0,5,"-"']
        data = ("\n".join(lines) + "\n").encode()
        p = cerebro.parse_cerebro("US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv", data)
        a, b = p["rows"]
        self.assertEqual((a["organic_rank"], a["sponsored_rank"]), (37, None))                  # 'Organic'=1 non e' il rank
        self.assertEqual((b["organic_rank"], b["sponsored_rank"]), (None, 5))
        self.assertIsNone(a["competing_products"])                                              # 0 = non calcolato
        self.assertEqual(a["raw"]["Competing Products"], "0")                                   # originale conservato
        self.assertEqual(a["raw"]["Organic"], "1")
        self.assertIn("Organic", p["unmapped"])

    def test_format_only_keyword_is_generic(self):
        self.assertTrue(C._generic_keyword("large print books"))
        self.assertTrue(C._generic_keyword("books"))
        self.assertFalse(C._generic_keyword("large print mystery books"))
        self.assertEqual(C.content_tokens("best sellers books on sale 2026"), ["seller"][:0])

    def test_labels_are_capped_for_weak_signals_and_ordered_strongest_first(self):
        cfg = C.config()
        k = lambda i, asins=("A", "B", "C"), vol=50000: {"keyword": f"x y {i}", "keyword_norm": f"x y {i}", "share": len(asins) / 3, "asins": list(asins),  # noqa: E731
                                                          "competitor_count": len(asins), "best_organic_rank": 1, "search_volume": vol, "title_density": 1,
                                                          "competing_products": 10, "per_competitor": {}}
        mk = lambda ks, topic=("x", "y"): {"cluster_key": "x", "topic": list(topic), "audience": [], "keywords": ks}                                   # noqa: E731
        self.assertEqual(C.book_opportunity(mk([k(i) for i in range(4)]), 3, cfg)["score_label"], "FORTE")
        low = C.book_opportunity(mk([k(i, vol=100) for i in range(4)]), 3, cfg)
        self.assertEqual(low["score_label"], "INTERESSANTE")                                    # volume basso: niente FORTE
        self.assertTrue(any("volume basso" in c for c in low["caps"]))
        one = C.book_opportunity(mk([k(i, asins=("A",)) for i in range(4)]), 3, cfg)
        self.assertEqual(one["score_label"], "DA OSSERVARE")                                    # un solo competitor su 3
        broad = C.book_opportunity(mk([k(i) for i in range(4)], topic=("x",)), 3, cfg)
        self.assertEqual(broad["score_label"], "INTERESSANTE")                                  # un solo termine centrale = categoria
        aud = C.book_opportunity({"cluster_key": "~senior", "topic": [], "audience": ["senior"], "keywords": [k(i) for i in range(4)]}, 3, cfg)
        self.assertEqual(aud["score_label"], "DA OSSERVARE")                                    # solo pubblico, nessun tema
        agg = self.agg(self.project(None))["editorial"]["opportunities"]
        order = {"FORTE": 0, "INTERESSANTE": 1, "DA OSSERVARE": 2, "DEBOLE": 3}
        self.assertEqual([order[o["score_label"]] for o in agg], sorted(order[o["score_label"]] for o in agg))

    def test_sponsored_never_changes_book_score(self):
        base = self.agg(self.project(None))["editorial"]["opportunities"]
        F1b = [dict(r, **{"Sponsored Rank": None}) for r in T_F1()]
        alt = self.agg(self.project(None, files=((A1, F1b), (A2, F2), (A3, F3)), name="senza sponsored"))["editorial"]["opportunities"]
        self.assertEqual({o["cluster_key"]: o["score"] for o in base}, {o["cluster_key"]: o["score"] for o in alt})


def T_F1():
    return F1


REAL_DIR = Path.home() / "Downloads"
REAL_FILES = [REAL_DIR / f"US_AMAZON_cerebro_{a}_2026-10-12.csv" for a in ("B0H5MZP4QW", "1997182017", "B0H8YTL55T")]


@unittest.skipUnless(all(f.is_file() for f in REAL_FILES), "CSV Cerebro reali assenti (Downloads)")
class RealCerebroFilesTests(Base):
    """I tre CSV Cerebro REALI dell'esperimento: nessun valore atteso fissato sui numeri, ma i temi devono EMERGERE dai dati."""

    def test_themes_emerge_from_real_data_and_ranks_are_real(self):
        rid = S.create_project(self.conn, None, None, "US", now=NOW)
        for f in REAL_FILES:
            self.assertEqual(S.import_source(self.conn, rid, cerebro.parse_cerebro(f.name, f.read_bytes()), now=NOW)["status"], "IMPORTED")
        a = S.analyze(self.conn, rid)
        kws = a["agg"]["keywords"]
        ranks = {k["best_organic_rank"] for k in kws if k["best_organic_rank"] is not None}
        self.assertGreater(len(ranks), 10)                                                       # non tutti 1 (bug della colonna indicatore "Organic")
        labels = " ".join(o["label"].lower() for o in a["editorial"]["opportunities"])
        common = " ".join(k["keyword_norm"] for k in kws if k["competitor_count"] >= 2)
        for term in ("short stories", "large print", "mystery", "senior"):
            self.assertIn(term, labels + " " + common, term)
        self.assertTrue(a["editorial"]["brand_signals"])
        self.assertTrue(all(not any(b["keyword_norm"] in o["keyword_norms"] for b in a["editorial"]["brand_signals"]) for o in a["editorial"]["opportunities"]))


class MigrationTests(unittest.TestCase):
    def test_existing_db_gets_new_tables_and_keeps_all_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.db"
            c = connect(p)
            c.execute("INSERT INTO books (book_id,title) VALUES ('B1','Uno')")
            c.commit()
            for t in ("competitor_opportunity_keywords", "competitor_opportunities", "competitor_keyword_observations", "competitor_research_keywords",
                      "competitor_research_sources", "competitor_research_projects"):
                c.execute(f"DROP TABLE {t}")                                                                 # DB com'era PRIMA di questo modulo
            c.commit()
            ver = c.execute("PRAGMA user_version").fetchone()[0]
            c.close()
            c = connect(p)
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"competitor_research_projects", "competitor_research_sources", "competitor_research_keywords", "competitor_keyword_observations",
                             "competitor_opportunities", "competitor_opportunity_keywords"} <= names)
            self.assertEqual(c.execute("SELECT title FROM books WHERE book_id='B1'").fetchone()[0], "Uno")
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], ver)                            # nessun cambio di SCHEMA_VERSION
            self.assertFalse((Path(tmp) / "backups").exists())                                                # nessuna migrazione/backup forzati
            c.close()

    def test_real_db_copy_opens_and_is_unchanged(self):
        real = ROOT / "DATABASE" / "kdp_ads.db"
        if not real.is_file():
            self.skipTest("DB reale assente")
        h = hashlib.sha256(real.read_bytes()).hexdigest()
        src = sqlite3.connect(f"{real.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as tmp:
            dst_path = Path(tmp) / "copy.db"
            dst = sqlite3.connect(dst_path)
            src.backup(dst)
            dst.close()
            src.close()
            c = connect(dst_path)
            n = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            self.assertTrue(S.has_schema(c))
            rid = S.create_project(c, None, None, "US", now=NOW)
            S.import_source(c, rid, parsed(A1, F1), now=NOW)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], n)
            c.close()
        self.assertEqual(hashlib.sha256(real.read_bytes()).hexdigest(), h)


if __name__ == "__main__":
    unittest.main()
