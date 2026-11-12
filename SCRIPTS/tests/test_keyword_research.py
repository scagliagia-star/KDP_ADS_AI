"""RICERCA KEYWORD / COMPETITOR: riconoscimento automatico Publisher Rocket / Helium 10 Cerebro, multi-file Cerebro, unione Rocket + Cerebro per keyword, priorita' P1/P2/P3, piano
PHRASE-first. Fixture SINTETICHE (intestazioni reali Cerebro a 26 colonne). Un test facoltativo usa i 5 file reali Cerebro del 2026-11-05 se presenti in Downloads (saltato altrimenti).
Mai il DB reale, mai books.json reale, nessuna rete, nessuna Amazon."""
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
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_competitor_research as T
from kdp_ads import cerebro as CB
from kdp_ads import competitor as C
from kdp_ads import competitor_store as S
from kdp_ads import keyword_research as KR
from kdp_ads import launch_research as LR
from kdp_ads.db import connect
from ops import competitor_ops as ops
from ops import workspace

CEREBRO_26 = ["Keyword Phrase", "ABA Total Click Share", "ABA Total Conv. Share", "Keyword Sales", "Cerebro IQ Score", "Search Volume", "Search Volume Trend", "H10 PPC Sugg. Bid",
              "H10 PPC Sugg. Min Bid", "H10 PPC Sugg. Max Bid", "Sponsored ASINs", "Competing Products", "CPR", "Title Density", "Organic", "Sponsored Product", "Amazon Recommended",
              "Amazon Choice", "Highly Rated", "Sponsored Brand Header", "Sponsored Brand Video", "Top Rated From Our Brand", "Trending Now", "Amazon Rec. Rank", "Sponsored Rank",
              "Organic Rank"]
ROCKET_HEAD = ["Keyword", "Search Volume", "Competition", "Number of Competitors"]
DOWNLOADS = Path("C:/Users/UTENTE/Downloads")


def c26(rows) -> bytes:
    """CSV Cerebro a 26 colonne. Ogni riga = dict parziale; mancante = '-' (come nell'export reale)."""
    out = io.StringIO()
    out.write(",".join(f'"{h}"' for h in CEREBRO_26) + "\n")
    for r in rows:
        out.write(",".join('"' + str(r.get(h, "-")) + '"' for h in CEREBRO_26) + "\n")
    return out.getvalue().encode("utf-8")


def kw(phrase, vol="-", org="-", spo="-", iq="-", sales="-", cp="-", td="-", bid="-", bmin="-", bmax="-", amz="-"):
    return {"Keyword Phrase": phrase, "Search Volume": vol, "Organic Rank": org, "Sponsored Rank": spo, "Cerebro IQ Score": iq, "Keyword Sales": sales, "Competing Products": cp,
            "Title Density": td, "H10 PPC Sugg. Bid": bid, "H10 PPC Sugg. Min Bid": bmin, "H10 PPC Sugg. Max Bid": bmax, "Amazon Rec. Rank": amz}


def rocket(rows, name="rocket_keywords.csv") -> tuple:
    out = io.StringIO()
    out.write(",".join(ROCKET_HEAD) + "\n")
    for r in rows:
        out.write(",".join(str(x) for x in r) + "\n")
    return name, out.getvalue().encode("utf-8")


def cname(cid, date="2026-11-05"):
    return f"US_AMAZON_cerebro_{cid}_{date}.csv"


COMPETITORS = {
    "B0C87HNVKW": [kw("short stories for seniors", 5200, 1, 5, 7, 300, 4000, 12, 0.9, 0.7, 1.1), kw("Mystery  Books ", 0, 3, "-", "-", "-", 1000, 2), kw("golden girls trivia", 900, 2),
                   kw("kids coloring book", 3000, 6)],
    "8409903091": [kw("short stories for seniors", 5200, 3, 8, 7, 280, 4000, 12, 1.0, 0.8, 1.2), kw("mystery books", 0, 16), kw("large print puzzles", 800, 4, 2)],
    "B0FGT8LTXQ": [kw("Short Stories for Seniors", 5200, 2, 2, 8, 310, 4100, 12, 0.95, 0.7, 1.0), kw("mystery books", 0, "-", 12), kw("short story books", 400, 25)],
    "B0GFW8236T": [kw("short stories for seniors", 5200, 5, "-", 7, 290, 4000, 12, 1.0, 0.7, 1.1), kw("mystery books", "-", 40), kw("books", 100000, 80, "-", 3, 90000, 5000000, 400)],
}


def parsed_all():
    return [CB.parse_cerebro(cname(c), c26(rows)) for c, rows in COMPETITORS.items()]


def agg_all():
    return C.aggregate(KR.cerebro_sources(parsed_all()))


# ------------------------------------------------------------------ riconoscimento
class DetectTests(unittest.TestCase):
    def test_cerebro_is_recognised_automatically_with_26_columns(self):
        d = KR.detect_format(cname("B0C87HNVKW"), c26(COMPETITORS["B0C87HNVKW"]))
        self.assertEqual(d["format"], "HELIUM10_CEREBRO")
        self.assertEqual(len(d["columns"]), 26)
        self.assertEqual(d["preferred_missing"], [])

    def test_cerebro_needs_keyword_search_volume_and_organic_rank(self):
        head = "Keyword Phrase,Search Volume,Organic Rank,Cerebro IQ Score\nmystery books,100,3,7\n"
        self.assertEqual(KR.detect_format("x.csv", head.encode())["format"], "HELIUM10_CEREBRO")
        no_org = "Keyword Phrase,Search Volume,Cerebro IQ Score\nmystery books,100,7\n"
        d = KR.detect_format("x.csv", no_org.encode())
        self.assertEqual(d["format"], "UNKNOWN")                                  # colonne Cerebro ma incomplete: non si forza in Rocket
        self.assertEqual(d["missing"], ["organic_rank"])
        self.assertIn("Keyword Phrase", d["message"])

    def test_publisher_rocket_detection_is_unchanged(self):
        name, data = rocket([("short stories for seniors", 1200, 55, 300), ("mystery books", "<100", 30, 80)])
        d = KR.detect_format(name, data)
        self.assertEqual(d["format"], "PUBLISHER_ROCKET")
        p = KR.parse_any(name, data)
        ref = LR.parse_file(name, data, now=p["rocket"]["parsed_at"])
        self.assertEqual({k: v for k, v in p["rocket"].items()}, ref)                # identico al parser Rocket esistente
        self.assertEqual(p["rocket"]["keywords"][1]["search_volume_text"], "<100")  # un limite resta testo

    def test_unknown_format_shows_the_detected_columns_and_is_never_forced_into_rocket(self):
        data = b"Foo,Bar,Baz\n1,2,3\n"
        d = KR.detect_format("strano.csv", data)
        self.assertEqual(d["format"], "UNKNOWN")
        self.assertEqual(d["columns"], ["Foo", "Bar", "Baz"])
        self.assertIn("Foo, Bar, Baz", d["message"])
        p = KR.parse_any("strano.csv", data)
        self.assertIsNone(p["rocket"])
        self.assertIsNone(p["cerebro"])
        self.assertTrue(p["error"])
        self.assertFalse(KR.file_summary(p)["ok"])
        self.assertEqual(KR.detect_format("vuoto.csv", b"")["format"], "UNKNOWN")

    def test_cerebro_file_is_never_read_by_the_rocket_parser(self):
        with self.assertRaises(LR.ResearchError):
            LR.parse_file(cname("B0C87HNVKW"), c26(COMPETITORS["B0C87HNVKW"]))       # l'aliasing Rocket non conosce «Keyword Phrase»


# ------------------------------------------------------------------ competitor id dal nome file
class CompetitorIdTests(unittest.TestCase):
    def test_alphanumeric_asin_numeric_isbn_and_marketplace(self):
        a = KR.parse_any(cname("B0C87HNVKW"), c26(COMPETITORS["B0C87HNVKW"]))
        b = KR.parse_any(cname("8409903091"), c26(COMPETITORS["8409903091"]))
        self.assertEqual((a["competitor_id"], a["competitor_id_source"], a["marketplace"]), ("B0C87HNVKW", "FILENAME", "US"))
        self.assertEqual((b["competitor_id"], b["competitor_id_source"], b["marketplace"]), ("8409903091", "FILENAME", "US"))   # ISBN/ASIN numerico, non solo B0...
        self.assertEqual(a["format"], "HELIUM10_CEREBRO")
        self.assertEqual(a["cerebro"]["file_name"], cname("B0C87HNVKW"))

    def test_the_date_in_the_filename_is_not_mistaken_for_an_isbn(self):
        p = KR.parse_any("US_AMAZON_cerebro__2026-05-11.csv", c26(COMPETITORS["B0C87HNVKW"]))
        self.assertIsNone(p["competitor_id"])
        self.assertTrue(p["needs_competitor"])
        p2 = KR.parse_any("US_AMAZON_cerebro__2026-05-11.csv", c26(COMPETITORS["B0C87HNVKW"]), "B0AAAAAAA1")
        self.assertEqual((p2["competitor_id"], p2["competitor_id_source"]), ("B0AAAAAAA1", "MANUAL"))


# ------------------------------------------------------------------ valori mancanti, zero, normalizzazione
class NormalisationTests(unittest.TestCase):
    def rows(self, cid="B0C87HNVKW"):
        return {r["keyword_norm"]: r for r in CB.parse_cerebro(cname(cid), c26(COMPETITORS[cid]))["rows"]}

    def test_dash_is_missing_not_zero(self):
        r = self.rows()["mystery books"]
        self.assertIsNone(r["cerebro_iq"])
        self.assertIsNone(r["keyword_sales"])
        self.assertIsNone(r["sponsored_rank"])
        self.assertIsNone(r["ppc_bid"])
        self.assertIsNone(r["amazon_recommended_rank"])

    def test_zero_search_volume_stays_a_real_zero(self):
        r = self.rows()["mystery books"]
        self.assertEqual(r["search_volume"], 0)
        self.assertIsNotNone(r["search_volume"])
        missing = self.rows("B0GFW8236T")["mystery books"]
        self.assertIsNone(missing["search_volume"])                                  # '-' != 0
        agg = {k["keyword_norm"]: k for k in agg_all()["keywords"]}["mystery books"]
        self.assertEqual(agg["search_volume"], 0)
        u = {r["keyword_norm"]: r for r in KR.unify(agg_all())["rows"]}["mystery books"]
        self.assertEqual(u["cerebro_search_volume"], 0)
        self.assertEqual(u["search_volume_used"], 0)

    def test_keyword_normalisation_dedups_spaces_and_case_without_stemming(self):
        rows = self.rows()
        self.assertIn("mystery books", rows)                                         # «Mystery  Books » -> chiave interna
        self.assertEqual(rows["mystery books"]["keyword"], "Mystery  Books")                  # la forma ORIGINALE resta per la visualizzazione
        u = {r["keyword_norm"]: r for r in KR.unify(agg_all())["rows"]}
        self.assertEqual(u["short stories for seniors"]["competitor_count"], 4)         # «Short Stories for Seniors» + minuscole = una keyword
        self.assertIn("short story books", u)                                         # niente stemming
        self.assertIn("mystery books", u)
        self.assertNotIn("mystery book", u)
        self.assertEqual(CB.normalize_keyword("  Large   Print  BOOKS "), "large print books")


# ------------------------------------------------------------------ aggregazione multi-file
class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.u = {r["keyword_norm"]: r for r in KR.unify(agg_all())["rows"]}

    def test_competitor_count_and_share(self):
        k = self.u["short stories for seniors"]
        self.assertEqual((k["competitor_count"], k["competitor_total"], k["competitor_share"]), (4, 4, "4/4"))
        self.assertEqual(self.u["mystery books"]["competitor_share"], "4/4")
        self.assertEqual(self.u["golden girls trivia"]["competitor_share"], "1/4")

    def test_organic_rank_aggregation_lower_is_better(self):
        k = self.u["short stories for seniors"]
        self.assertEqual(k["best_organic_rank"], 1)                                   # rank 1 e' MIGLIORE di rank 5
        self.assertEqual(k["median_organic_rank"], 2.5)                               # 1,2,3,5
        m = self.u["mystery books"]
        self.assertEqual(m["best_organic_rank"], 3)                                   # 3,16,40 (un competitor senza rank): 3 migliore di 16 e 40
        self.assertEqual(m["median_organic_rank"], 16)
        self.assertEqual(m["organic_rank_count"], 3)

    def test_sponsored_rank_aggregation_and_presence(self):
        k = self.u["short stories for seniors"]
        self.assertEqual(k["best_sponsored_rank"], 2)                                 # 5, 8, 2 (un competitor senza sponsored)
        self.assertEqual(k["sponsored_competitor_count"], 3)
        self.assertEqual(self.u["mystery books"]["best_sponsored_rank"], 12)
        self.assertIsNone(self.u["golden girls trivia"]["best_sponsored_rank"])

    def test_other_metrics_and_bids(self):
        k = self.u["short stories for seniors"]
        self.assertEqual(k["cerebro_search_volume"], 5200)                            # lo stesso valore in tutti i file: usato una sola volta
        self.assertEqual(k["cerebro_iq"], 7)
        self.assertEqual(k["title_density"], 12)
        self.assertEqual((k["bid_min"], k["bid_max"]), (0.7, 1.2))
        self.assertAlmostEqual(k["suggested_bid"], 0.975, places=3)
        self.assertEqual(k["competing_products"], 4000)

    def test_summary_counts(self):
        s = KR.unify(agg_all())["summary"]
        self.assertEqual(s["cerebro_competitors"], 4)
        self.assertEqual(s["unique_keywords"], len({k for rows in COMPETITORS.values() for r in rows for k in [CB.normalize_keyword(r["Keyword Phrase"])]}))
        self.assertEqual(s["at_least_2"], 2)                                          # «short stories for seniors» e «mystery books» (4/4)
        self.assertEqual(s["at_least_3"], 2)
        self.assertEqual(sum(s["by_competitor_count"].values()), s["cerebro_unique_keywords"])


# ------------------------------------------------------------------ unione Rocket + Cerebro
class UnionTests(unittest.TestCase):
    def test_rocket_and_cerebro_are_one_row_with_both_sources_and_separate_volumes(self):
        name, data = rocket([("Short Stories for Seniors", 1500, 60, 400), ("only rocket keyword", 900, 20, 50)])
        rec = KR.parse_any(name, data)["rocket"]
        res = KR.unify(agg_all(), [rec])
        rows = [r for r in res["rows"] if r["keyword_norm"] == "short stories for seniors"]
        self.assertEqual(len(rows), 1)                                                # nessuna keyword doppia
        r = rows[0]
        self.assertEqual(r["source_label"], "ROCKET + CEREBRO")
        self.assertEqual(r["sources"], ["HELIUM10_CEREBRO", "PUBLISHER_ROCKET"])
        self.assertEqual(r["cerebro_search_volume"], 5200)                            # il dato Cerebro NON sovrascrive quello Rocket (e viceversa)
        self.assertEqual(r["rocket_search_volume"], 1500)
        self.assertEqual(r["rocket"]["competition"], 60)
        self.assertEqual(r["search_volume_used"], 5200)
        only = next(x for x in res["rows"] if x["keyword_norm"] == "only rocket keyword")
        self.assertEqual((only["source_label"], only["competitor_count"], only["rocket_search_volume"]), ("ROCKET", 0, 900))
        self.assertEqual(res["summary"]["both_sources"], 1)
        self.assertEqual(res["summary"]["rocket_unique_keywords"], 2)

    def test_rocket_only_project_works_and_a_rocket_zero_text_limit_stays_text(self):
        name, data = rocket([("mystery books for seniors", "<100", 30, 80)])
        res = KR.unify(None, [KR.parse_any(name, data)["rocket"]])
        r = res["rows"][0]
        self.assertIsNone(r["rocket_search_volume"])                                  # «<100» non diventa un numero
        self.assertEqual(r["rocket"]["search_volume_text"], "<100")
        self.assertEqual(res["summary"]["cerebro_competitors"], 0)


# ------------------------------------------------------------------ priorita' e piano di lancio
class PriorityAndPlanTests(unittest.TestCase):
    PROFILE = KR.build_profile("Short Stories for Seniors", "Large Print Cozy Mystery Collection", ["short stories seniors", "large print mystery"], None, "Golden girls style stories")

    def unify(self, profile=PROFILE):
        return {r["keyword_norm"]: r for r in KR.unify(agg_all(), None, profile)["rows"]}

    def test_core_keyword_is_p1_phrase_core_with_badges(self):
        r = self.unify()["short stories for seniors"]
        self.assertEqual(r["priority"], "P1")
        self.assertEqual(r["priority_label"], "P1 CORE")
        self.assertEqual(r["badges"], ["ORGANIC TOP 10", "SPONSORED TOP 10", "MULTI-COMPETITOR"])
        self.assertEqual(r["recommended_match"], "PHRASE")
        self.assertIn(r["launch_type"], ("PHRASE CORE", "CANDIDATA EXACT — EVIDENZA MOLTO FORTE"))
        self.assertIn("presente su 4/4", r["reason"])

    def test_low_volume_keyword_with_strong_ranks_on_many_competitors_beats_a_generic_high_volume_one(self):
        agg = C.aggregate(KR.cerebro_sources([
            CB.parse_cerebro(cname(f"B0AAAAAA0{i}"), c26([kw("cozy mystery short stories", 30, i + 1, 3 + i), kw("books", 5000, 90)] if i < 4 else [kw("books", 5000, 90)])) for i in range(5)]))
        prof = KR.build_profile("Cozy Mystery Short Stories")
        rows = {r["keyword_norm"]: r for r in KR.unify(agg, None, prof)["rows"]}
        low, generic = rows["cozy mystery short stories"], rows["books"]
        self.assertEqual(low["search_volume_used"], 30)
        self.assertNotEqual(low["priority"], "FUORI INTENTO")                         # il volume basso non esclude
        self.assertLess(KR.PRIORITY_ORDER[low["priority"]], KR.PRIORITY_ORDER[generic["priority"]])
        self.assertGreater(low["score"], generic["score"])

    def test_out_of_intent_brand_and_asin_keywords_are_excluded_with_a_reason(self):
        rows = self.unify()
        kids = rows["kids coloring book"]
        self.assertEqual((kids["priority"], kids["priority_label"]), ("FUORI INTENTO", "ESCLUDI / FUORI INTENTO"))
        self.assertIn("intento", kids["priority_reason"])
        self.assertIsNone(kids["launch_type"])
        asin = KR.unify(C.aggregate([{"asin": "B0AAAAAAA1", "rows": CB.merge_duplicate_rows(CB.parse_cerebro(cname("B0AAAAAAA1"), c26([kw("B0H5MZP4QW", 10, 1)]))["rows"])}]), None, self.PROFILE)["rows"][0]
        self.assertEqual(asin["priority"], "FUORI INTENTO")

    def test_without_a_book_profile_relevance_is_not_evaluated_and_nothing_is_excluded_for_it(self):
        rows = self.unify(None)
        self.assertIsNone(rows["kids coloring book"]["relevance"])
        self.assertNotEqual(rows["kids coloring book"]["priority"], "FUORI INTENTO")

    def test_launch_plan_is_phrase_first_and_has_all_required_fields(self):
        res = KR.unify(agg_all(), None, self.PROFILE)
        plan = KR.launch_plan(res["rows"])
        self.assertTrue(plan)
        need = {"keyword", "priority", "source", "competitor_count", "search_volume", "best_organic_rank", "best_sponsored_rank", "suggested_bid", "reason", "launch_type"}
        for i in plan:
            self.assertTrue(need <= set(i), need - set(i))
            self.assertIn(i["launch_type"], ("PHRASE CORE", "PHRASE TEST", "CANDIDATA EXACT — EVIDENZA MOLTO FORTE"))
        self.assertEqual([i["priority"] for i in plan], sorted((i["priority"] for i in plan), key=lambda p: ["P1 CORE", "P2 STRONG", "P3 TEST"].index(p)))
        self.assertNotIn("kids coloring book", [i["keyword"] for i in plan])
        rows = {r["keyword_norm"]: r for r in res["rows"]}
        self.assertTrue(all(r["recommended_match"] == "PHRASE" for r in rows.values() if r["priority"] in ("P1", "P2", "P3")))

    def test_exact_needs_very_strong_evidence(self):
        cfg = KR.config()
        weak = {"competitor_count": 2, "best_organic_rank": 1, "best_sponsored_rank": 1, "search_volume_used": 5000, "priority": "P1"}
        self.assertEqual(KR._launch_type(weak, 0.9, cfg), "PHRASE CORE")
        strong = {**weak, "competitor_count": 5}
        self.assertEqual(KR._launch_type(strong, 0.9, cfg), "CANDIDATA EXACT — EVIDENZA MOLTO FORTE")
        self.assertEqual(KR._launch_type({**strong, "priority": "P2"}, 0.9, cfg), "PHRASE TEST")
        self.assertEqual(KR._launch_type({**strong, "search_volume_used": 10}, 0.9, cfg), "PHRASE CORE")


# ------------------------------------------------------------------ progetto: import misto, persistenza, nessuna scrittura su Amazon / report Ads
class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "CONFIG").mkdir()
        (root / "CONFIG" / "books.json").write_text(json.dumps({"books": [{"book_id": "B0HLL1Y1CN", "title": "Short Stories for Seniors", "marketplace": "US", "currency": "USD", "price": 9.99,
                                                                          "royalty_per_copy": 3.0, "active": True}]}), encoding="utf-8")
        (root / "DATABASE").mkdir()
        self.root, self.ws = root, workspace.from_root(root)
        c = connect(self.ws.db_path)
        c.execute("INSERT INTO books (book_id,title,marketplace) VALUES ('B0HLL1Y1CN','Short Stories for Seniors','US')")
        c.commit()
        c.close()
        self.books_hash = hashlib.sha256((root / "CONFIG" / "books.json").read_bytes()).hexdigest()
        self.files = [(cname(c), c26(rows)) for c, rows in COMPETITORS.items()]

    def tearDown(self):
        self.tmp.cleanup()

    def ads_counts(self):
        c = sqlite3.connect(self.ws.db_path)
        try:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("target_rows", "search_term_rows", "decisions", "decision_runs", "campaigns", "imports", "manual_actions",
                                                                                      "campaign_book_mapping", "global_report_rows", "global_report_imports")}
        finally:
            c.close()

    def test_multi_file_cerebro_import_and_mixed_with_rocket_without_touching_ads_data(self):
        before = self.ads_counts()
        rk = rocket([("Short Stories for Seniors", 1500, 60, 400), ("only rocket keyword", 900, 20, 50)])
        r = ops.import_research(self.ws, "B0HLL1Y1CN", "US", None, self.files + [rk], book_title="Short Stories for Seniors")
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["imported"], 4)                                       # 4 competitor Cerebro salvati; il file Rocket NON e' salvato nel DB (solo sessione)
        self.assertEqual(len(r.data["rocket_records"]), 1)
        a = ops.analyze(self.ws, r.data["research_id"], None, r.data["rocket_records"])
        self.assertEqual(a["overview"]["total"], 4)
        res = a["research"]
        self.assertEqual(res["summary"]["cerebro_competitors"], 4)
        self.assertEqual(res["summary"]["rocket_files"], 1)
        self.assertEqual(len([x for x in res["rows"] if x["keyword_norm"] == "short stories for seniors"]), 1)
        self.assertTrue(res["has_profile"])                                           # il titolo del libro associato alimenta la pertinenza
        self.assertTrue(res["plan"])
        self.assertEqual(self.ads_counts(), before)                                   # nessuna riga dei report/decisioni Amazon Ads toccata
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        again = ops.import_research(self.ws, None, "US", None, [rk], research_id=r.data["research_id"])
        self.assertEqual(again.data["imported"], 0)                                   # un file Rocket da solo non scrive nulla nel DB
        self.assertEqual(self.ads_counts(), before)
        c = sqlite3.connect(self.ws.db_path)
        try:                                                                          # nessuna tabella Rocket persistente: nessuna modifica di schema
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%rocket%'").fetchone()[0], 0)
        finally:
            c.close()
        self.assertEqual(ops.analyze(self.ws, r.data["research_id"])["research"]["summary"]["rocket_files"], 0)         # senza sessione: solo Cerebro

    def test_no_schema_change_and_no_rocket_table(self):
        from kdp_ads import db as D
        self.assertEqual(D.SCHEMA_VERSION, 9)                                         # nessuna tabella Rocket: Rocket resta nella bozza di lancio (file) o nella sessione
        self.assertNotIn("rocket", S.SCHEMA.lower())
        fresh = tempfile.TemporaryDirectory()
        try:
            c = connect(Path(fresh.name) / "k.db")
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], D.SCHEMA_VERSION)
            c.close()
            self.assertFalse([n for n in names if "rocket" in n])
        finally:
            try:
                fresh.cleanup()
            except OSError:
                pass

    def test_unknown_file_blocks_nothing_else_and_explains_itself(self):
        r = ops.import_research(self.ws, None, "US", "x", [("strano.csv", b"Foo,Bar\n1,2\n"), self.files[0]])
        self.assertTrue(r.ok)
        self.assertEqual(r.data["imported"], 1)
        self.assertTrue(any("Foo, Bar" in e for e in r.errors))

    def test_preview_summary_before_import(self):
        plans = ops.plan_any(self.files)
        self.assertTrue(all(p["format"] == "HELIUM10_CEREBRO" for p in plans))
        s = ops.preview_summary(plans)
        self.assertEqual((s["cerebro_competitors"], s["at_least_3"]), (4, 2))

    def test_pure_module_has_no_dependency_on_ads_decisions_or_writes(self):
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "keyword_research.py").read_text(encoding="utf-8")
        for bad in ("import decision", "from . import decision", "from .decision", "opportunities", "sqlite3", "open(", "execute(", "requests"):
            self.assertNotIn(bad, src.split('"""', 2)[2], bad)
        for mod in ("decision.py", "analysis.py", "opportunities.py", "escalation.py"):
            self.assertNotIn("keyword_research", (ROOT / "SCRIPTS" / "kdp_ads" / mod).read_text(encoding="utf-8"), mod)       # le decisioni Ads non importano la ricerca


# ------------------------------------------------------------------ 5 file reali (facoltativo)
REAL = {"B0C87HNVKW": 315, "8409903091": 272, "B0FGT8LTXQ": 1289, "B0GFW8236T": 702, "B0GR64286Q": 214}


@unittest.skipUnless(all((DOWNLOADS / cname(c)).is_file() for c in REAL), "i 5 file Cerebro reali non sono in Downloads")
class RealFilesTests(unittest.TestCase):
    def test_five_real_cerebro_files(self):
        plans = [KR.parse_any(cname(c), (DOWNLOADS / cname(c)).read_bytes()) for c in REAL]
        self.assertTrue(all(p["format"] == "HELIUM10_CEREBRO" for p in plans))
        self.assertEqual({p["competitor_id"]: len(p["cerebro"]["rows"]) for p in plans}, REAL)
        self.assertTrue(all(len(p["cerebro"]["columns"]) == 26 for p in plans))
        res = KR.unify(C.aggregate(KR.cerebro_sources([p["cerebro"] for p in plans])))
        s = res["summary"]
        self.assertEqual(s["cerebro_competitors"], 5)
        self.assertEqual(sum(s["by_competitor_count"].values()), s["unique_keywords"])
        self.assertEqual(len({r["keyword_norm"] for r in res["rows"]}), s["unique_keywords"])            # nessuna keyword doppia
        for r in res["rows"]:
            if r["best_organic_rank"] is not None:
                self.assertLessEqual(r["best_organic_rank"], r["median_organic_rank"])


if __name__ == "__main__":
    unittest.main()
