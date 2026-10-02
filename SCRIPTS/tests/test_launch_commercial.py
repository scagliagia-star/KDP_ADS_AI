"""Lancio nuovo libro: titolo creativo, COMMERCIAL_SEARCH_TOPIC, TITLE_QUERY, NATURAL_QUERY_QUALITY, tre punteggi, riduzione del rumore nella pagina.
Caso reale "Twenty Women, Twenty Mysteries" ricostruito con dati di prova. Solo workspace temporanei: nessuna rete, books.json/DB/INPUT mai toccati."""
import hashlib
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

from kdp_ads import launch as L  # noqa: E402
from kdp_ads import launch_keywords as K  # noqa: E402
from kdp_ads import launch_research as R  # noqa: E402
from test_launch_research import csv_bytes  # noqa: E402

LISTS = ("exact", "phrase", "discovery", "rejected", "title_queries")
PR_HEADER = ["Keyword", "Average Sales", "Search Per Month", "Search Per Month Color", "Competition Score", "Competition Score Color"]
COMP_A, COMP_B = "B0H8YTL55T", "B0GFW8236T"

TW = {"title": "Twenty Women, Twenty Mysteries",
      "subtitle": "20 Large-Print Cozy Mystery Short Stories for Senior Women | Complete Tales to Enjoy in One Sitting, Without Straining to Read Small Print",
      "description": ("A collection of large print cozy mystery short stories for senior women and adults. Each story is a gentle mystery to read in one sitting, "
                      "with an amateur sleuth in a small town. Easy to read short stories for seniors, printed in large print."),
      "kdp_keywords": ["mystery books for senior women", "large print mystery stories for adults", "cozy mystery short stories",
                       "gentle mysteries without graphic violence", "short mystery stories to read in one sitting", "small town mystery stories",
                       "older women amateur sleuth stories"],
      "categories": "Fiction > Mystery & Detective > Cozy\nFiction > Short Stories\nFiction > Women",
      "marketplace": "US", "currency": "", "price": "12.99", "royalty_per_copy": "4.5", "book_id": "",
      "competitors": [{"asin": COMP_A, "note": "reverse ASIN"}, {"asin": COMP_B, "note": "reverse ASIN"}]}

PR_ROWS = [
    ["large print cozy mystery books for seniors", "$ 243", "<100", "Red", "3", "Green"],
    ["large print short mystery stories for adults", "$ 200", "<100", "Red", "40", "Green"],
    ["large print books for seniors paperback mystery", "$ 1,009", "<100", "Red", "18", "Green"],
    ["easy to read short stories for seniors", "$ 521", "<100", "Red", "16", "Green"],
    ["short stories books for adults large print", "$ 1,132", "<100", "Red", "12", "Green"],
    ["mystery books for senior women", "$ 21,423", "689", "Green", "12", "Green"],
    ["short stories for senior women", "$ 2,230", "946", "Green", "42", "Yellow"],
    ["cozy mystery short stories for seniors", "$ 601", "<100", "Red", "2", "Green"],
    ["short stories", "$ 801", "310", "Yellow", "56", "Red"],
    ["large print", "$ 900", "420", "Yellow", "70", "Red"],
    ["senior women", "$ 300", "250", "Yellow", "30", "Green"],
    ["iphone case", "$ 90,000", "99,999", "Green", "3", "Green"],
    ["mystery coloring book for kids", "$ 5,000", "4,000", "Green", "10", "Green"],
    ["mystery thrillers for seniors", "$ 3,890", "513", "Green", "19", "Green"],
    ["short stories for elderly free", "$ 1,070", "394", "Yellow", "11", "Green"],
]
PR_ROWS += [[f"cozy mystery {w}", "$ 100", "<100", "Red", "20", "Green"] for w in "books series novels stories audiobooks club".split()]
# parole per generare tante keyword ampie (rumore) nel test della pagina
NOISE = [[f"short stories for {w}", "$ 100", "<100", "Red", str(10 + i), "Green"]
         for i, w in enumerate("kids teachers nurses dads moms hikers sailors bakers farmers pilots drivers poets chefs monks artists".split())]


def plan_of(**over):
    p, errors, _ = L.build_plan({**TW, **over}, now="2026-10-01T10:00:00")
    assert p, errors
    p["launch_id"] = "20261002_twenty_US"
    return p


def research_of(rows=PR_ROWS, name="ANALYZED REVERSE ASIN LOOKUP - BOOK B0H8YTL55T 2026-10-02 09.22.50.csv", extra_files=()):
    recs = [R.parse_file(name, csv_bytes(PR_HEADER, rows), "US")]
    for n, rws in extra_files:
        recs.append(R.parse_file(n, csv_bytes(PR_HEADER, rws), "US"))
    return R.research_for_plan(recs)


def plan_kp(plan=None, research=None, **kw):
    kp = K.build_keyword_plan(plan or plan_of(), research=research, now="2026-10-01T10:00:00", **kw)
    return kp, {e["keyword"]: (k, e) for k in LISTS for e in kp[k]}


def cls(by, kw):
    return by[kw][0] if kw in by else None


class CommercialTopicTests(unittest.TestCase):
    def test_creative_title_is_distinguished_from_commercial_topic(self):
        kp, _ = plan_kp(research=research_of())
        pr = kp["profile"]
        self.assertTrue(pr["title_creative"])
        for concept in ("cozy", "large", "print", "senior", "short"):
            self.assertTrue(any(concept in c for c in pr["commercial_search_topic"]), concept)
        self.assertTrue(any("mystery" in c for c in pr["commercial_search_topic"]))
        self.assertFalse(any("twenty" in c or "20" in c for c in pr["commercial_search_topic"]))     # un numero non e' un concetto commerciale
        self.assertEqual(pr["commercial_head"], ["mysteries/mystery"])

    def test_descriptive_title_keeps_title_topic_mode(self):
        p = K.build_keyword_plan(L.build_plan({**TW, "title": "Cozy Mystery Short Stories for Senior Women", "subtitle": "Large print"}, now="x")[0])
        self.assertFalse(p["profile"]["title_creative"])
        self.assertFalse(K.title_is_creative(plan_of(title="Cozy Mystery Short Stories for Senior Women")))
        self.assertTrue(K.title_is_creative(plan_of()))

    def test_numbers_are_never_commercial_concepts(self):
        self.assertTrue(K._is_number("twenty") and K._is_number("20"))
        self.assertFalse(K._is_content("twenty"))
        self.assertEqual(K._content_stems("Twenty Women, 20 Mysteries"), ["woman", "mystery"])


class TitleQueryTests(unittest.TestCase):
    def test_own_title_is_separate_and_does_not_dominate(self):
        kp, by = plan_kp(research=research_of(PR_ROWS + [["twenty women twenty mysteries", "$ 10", "<100", "Red", "5", "Green"]]))
        self.assertEqual(cls(by, "twenty women twenty mysteries"), "title_queries")
        self.assertEqual([e["keyword"] for e in kp["title_queries"]], ["twenty women twenty mysteries"])
        for lst in ("exact", "phrase", "discovery"):
            self.assertNotIn("twenty women twenty mysteries", [e["keyword"] for e in kp[lst]])
        for name in ("EXACT", "PHRASE"):
            self.assertNotIn("twenty women twenty mysteries", kp["campaigns"][name]["keywords"])
        e = by["twenty women twenty mysteries"][1]
        self.assertIn("TITLE_QUERY", e["reason"])
        self.assertNotEqual(kp["exact"][0]["keyword"], "twenty women twenty mysteries")

    def test_title_query_found_even_without_research(self):
        kp, by = plan_kp()
        self.assertEqual(cls(by, "twenty women twenty mysteries"), "title_queries")
        self.assertEqual(kp["exact"][0]["recommended_match_type"], "EXACT")


class NaturalQueryTests(unittest.TestCase):
    def test_unnatural_derived_fragments_are_rejected(self):
        kp, by = plan_kp()
        for frag in ("20 large", "mystery short", "cozy mystery short"):
            self.assertEqual(cls(by, frag), "rejected", frag)
            self.assertIn("NATURAL_QUERY_QUALITY", by[frag][1]["reason"])
        self.assertEqual(cls(by, "stories for senior"), "rejected")           # frammento di sottotitolo: scartato e auditabile, mai promosso
        for lst in ("exact", "phrase", "discovery"):
            self.assertNotIn("stories for senior", [e["keyword"] for e in kp[lst]])

    def test_natural_check_function(self):
        mk = lambda k, whole=(), src="subtitle": _cand(k, src, whole)  # noqa: E731
        self.assertIsNotNone(K._natural_fail(mk("20 large")))
        self.assertIsNotNone(K._natural_fail(mk("mystery short")))
        self.assertIsNotNone(K._natural_fail(mk("stories for senior")))
        self.assertIsNotNone(K._natural_fail(mk("mystery mystery")))
        self.assertIsNone(K._natural_fail(mk("large print")))
        self.assertIsNone(K._natural_fail(mk("cozy mystery short stories")))

    def test_user_and_research_keywords_are_not_rejected_by_the_heuristic(self):
        user = _cand("short stories for senior", "kdp_keyword", ("kdp_keyword",))
        pr = _cand("mystery short", "publisher_rocket", ("publisher_rocket",))
        self.assertIsNone(K._natural_fail(user))
        self.assertIsNone(K._natural_fail(pr))
        kp, by = plan_kp(research=research_of([["short stories for senior", "$ 1,749", "499", "Green", "33", "Green"]]))
        self.assertNotEqual(cls(by, "short stories for senior"), "rejected")      # PR: ammessa anche se "incompleta"
        kp2, by2 = plan_kp(plan_of(kdp_keywords=TW["kdp_keywords"][:6] + ["senior mystery short"]))
        self.assertNotEqual(cls(by2, "senior mystery short"), "rejected")           # keyword scritta dall'utente: mai scartata per questo


def _cand(key, src, whole=()):
    c = K._Cand(key)
    c.add(src, key, whole=bool(whole))
    c.whole = set(whole)
    return c


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.kp, self.by = plan_kp(research=research_of())

    def test_broad_keywords_are_discovery(self):
        for kw in ("short stories", "large print", "senior women"):
            self.assertEqual(cls(self.by, kw), "discovery", kw)
            self.assertFalse(self.by[kw][1]["core_topic"]["specific"])
            self.assertIn("Troppo ampia", self.by[kw][1]["reason"])

    def test_good_market_alone_does_not_promote_a_broad_keyword_only_exceptional_does(self):
        e = self.by["large print"][1]                                              # 420 ricerche/mese, mercato discreto
        self.assertGreaterEqual(e["market_evidence_score"], 50)
        self.assertEqual(e["recommended_match_type"], "DISCOVERY")
        rows = [r for r in PR_ROWS if r[0] != "senior women"] + [["senior women", "$ 9,000", "8,000", "Green", "10", "Green"]]   # mercato ECCEZIONALE (>= 1000 ricerche, >= 80)
        _, by = plan_kp(research=research_of(rows))
        self.assertEqual(cls(by, "senior women"), "phrase")
        self.assertIn("evidenza di mercato eccezionale", by["senior women"][1]["reason"])
        off = [r for r in PR_ROWS if r[0] != "iphone case"] + [["iphone case", "$ 9,000", "8,000", "Green", "10", "Green"]]
        self.assertEqual(cls(plan_kp(research=research_of(off))[1], "iphone case"), "rejected")                  # fuori tema: mai promossa

    def test_long_tail_commercial_keywords_are_exact_or_strong_phrase(self):
        for kw in ("large print cozy mystery books for seniors", "large print short mystery stories for adults"):
            self.assertIn(cls(self.by, kw), ("exact", "phrase"), kw)
            e = self.by[kw][1]
            self.assertGreaterEqual(e["final_launch_score"], 70, kw)
            self.assertGreaterEqual(e["relevance_score"], 90, kw)
            self.assertNotEqual(e["recommended_match_type"], "REJECT")
        self.assertIn("large print cozy mystery books for seniors", self.kp["campaigns"]["EXACT"]["keywords"])

    def test_long_tail_is_not_rejected_for_missing_title_words(self):
        rej = {e["keyword"] for e in self.kp["rejected"]}
        for kw in ("large print cozy mystery books for seniors", "large print short mystery stories for adults", "easy to read short stories for seniors",
                   "short stories books for adults large print", "large print books for seniors paperback mystery"):
            self.assertNotIn(kw, rej, kw)
            self.assertFalse({"twenty", "women", "mysteries"} <= set(kw.split()))   # nessuna di queste contiene le parole del titolo
        self.assertEqual(cls(self.by, "easy to read short stories for seniors"), "phrase")

    def test_explicit_head_keywords(self):
        self.assertEqual(cls(self.by, "mystery books for senior women"), "exact")
        self.assertEqual(cls(self.by, "cozy mystery short stories"), "exact")
        self.assertEqual(cls(self.by, "short stories for senior women"), "phrase")      # senza il genere "mystery": PHRASE, non EXACT

    def test_off_topic_and_wrong_intent_are_rejected_even_with_great_market_data(self):
        for kw, word in (("iphone case", "Fuori tema"), ("mystery coloring book for kids", "Intento diverso"), ("mystery thrillers for seniors", "Intento diverso"),
                         ("short stories for elderly free", "promozionale")):
            self.assertEqual(cls(self.by, kw), "rejected", kw)
            self.assertIn(word, self.by[kw][1]["reason"], kw)
        e = self.by["iphone case"][1]
        self.assertGreaterEqual(e["market_evidence_score"], 60)                         # ottimo mercato...
        self.assertLessEqual(e["final_launch_score"], 20)                               # ...ma non puo' salvare una keyword fuori tema
        self.assertIn("il volume non promuove", e["reason"])

    def test_derived_junk_is_not_a_recommended_keyword(self):
        junk = ("print mystery", "short mystery", "print cozy mystery", "without straining to read small print", "stories for senior women", "tales to enjoy")
        for lst in ("exact", "phrase"):
            names = {e["keyword"] for e in self.kp[lst]}
            for j in junk:
                self.assertNotIn(j, names, j)

    def test_quantities_are_not_demoted_and_campaign_lists_are_capped(self):
        self.assertEqual(len(self.kp["exact"]) + len(self.kp["phrase"]) + len(self.kp["discovery"]) + len(self.kp["rejected"]) + len(self.kp["title_queries"]),
                         len(self.by))
        self.assertLessEqual(len(self.kp["campaigns"]["EXACT"]["keywords"]), K.RULES["max_exact"])
        self.assertLessEqual(len(self.kp["campaigns"]["PHRASE"]["keywords"]), K.RULES["max_phrase"])
        self.assertEqual(self.kp["campaigns"]["EXACT"]["candidates_total"], len(self.kp["exact"]))


class ScoresTests(unittest.TestCase):
    def test_three_scores_are_present_and_consistent(self):
        kp, by = plan_kp(research=research_of())
        for lst in LISTS:
            for e in kp[lst]:
                for k in ("relevance_score", "market_evidence_score", "final_launch_score", "market_parts"):
                    self.assertIn(k, e)
                self.assertTrue(0 <= e["relevance_score"] <= 100 and 0 <= e["final_launch_score"] <= 100)
                self.assertEqual(e["evidence_score"], e["final_launch_score"])
                if e["market_evidence_score"] is not None:
                    self.assertTrue(0 <= e["market_evidence_score"] <= 100)
        self.assertIsNone(plan_kp()[1]["cozy mystery short stories"][1]["market_evidence_score"])           # senza dati: nessun mercato inventato
        self.assertEqual(by["mystery books for senior women"][1]["market_parts"].keys() >= {"volume", "competition", "earnings"}, True)

    def test_final_score_puts_relevance_first(self):
        self.assertEqual(K._final_score(80, None), 80)
        self.assertEqual(K._final_score(100, 100), 100)
        self.assertLess(K._final_score(10, 100), 25)                                     # fuori tema: il mercato quasi non conta
        self.assertGreater(K._final_score(90, 100), K._final_score(90, 20))
        self.assertGreater(K._final_score(90, 20), K._final_score(30, 100))              # la pertinenza batte il mercato

    def test_market_score_uses_real_metrics_and_ignores_colors(self):
        rec = lambda **k: {"source_file": "a.csv", **k}  # noqa: E731
        hi, _ = K._market_evidence([rec(search_volume=5000, competition=10, avg_sales=3000)], [], [], 1)
        lo, _ = K._market_evidence([rec(search_volume=150, competition=80, avg_sales=30)], [], [], 1)
        self.assertGreater(hi, lo)
        low_text, _ = K._market_evidence([rec(search_volume_text="<100", competition=10)], [], [], 1)
        self.assertLess(low_text, hi)
        green, _ = K._market_evidence([rec(search_volume=500, competition=20, searches_color="Green", competition_color="Green")], [], [], 1)
        red, _ = K._market_evidence([rec(search_volume=500, competition=20, searches_color="Red", competition_color="Red")], [], [], 1)
        self.assertEqual(green, red)                                                     # i colori non contano
        self.assertIsNone(K._market_evidence([], [], [], 0)[0])
        only_comp, parts = K._market_evidence([rec(competition=5)], [], [], 1)
        self.assertEqual(list(parts), ["competition"])                                   # una metrica assente non penalizza ne' viene inventata
        self.assertGreater(only_comp, 90)

    def test_better_market_data_ranks_higher_at_equal_relevance(self):
        rows = [["large print cozy mystery books for seniors", "$ 5,000", "4,000", "Green", "5", "Green"],
                ["large print cozy mystery books for older readers", "$ 20", "<100", "Red", "90", "Red"]]
        kp, by = plan_kp(research=research_of(rows))
        a, b = by["large print cozy mystery books for seniors"][1], by["large print cozy mystery books for older readers"][1]
        self.assertGreater(a["final_launch_score"], b["final_launch_score"])


class ResearchSignalsTests(unittest.TestCase):
    def test_reverse_asin_file_name_links_keywords_to_validated_competitor(self):
        self.assertEqual(R.asin_from_filename("ANALYZED REVERSE ASIN LOOKUP - BOOK B0H8YTL55T 2026-10-02 09.22.50.csv"), "B0H8YTL55T")
        self.assertEqual(R.asin_from_filename("ANALYZED REVERSE ASIN LOOKUP - BOOK 1997182017 2026-10-02 09.22.39.csv"), "1997182017")
        self.assertIsNone(R.asin_from_filename("ALL KEYWORD SEARCH - BOOK CROCHET CACTUS PATTERN 2026-09-21.csv"))
        self.assertIsNone(R.asin_from_filename("keywords.csv"))
        kp, by = plan_kp(research=research_of())
        e = by["large print cozy mystery books for seniors"][1]
        self.assertEqual(e["competitor_hits"], [COMP_A])                                  # supportata dal reverse ASIN di un competitor validato
        self.assertIn("reverse ASIN", e["reason"])
        self.assertIn("competitor", e["market_parts"])
        recs = research_of()["large print cozy mystery books for seniors"]
        self.assertEqual((recs[0]["competitor_asin"], recs[0]["competitor_asin_source"]), (COMP_A, "file_name"))

    def test_link_does_not_count_for_unverified_or_unknown_asin(self):
        kp, by = plan_kp(research=research_of(name="ANALYZED REVERSE ASIN LOOKUP - BOOK B0ZZZZZZZZ 2026-10-02.csv"))
        self.assertEqual(by["large print cozy mystery books for seniors"][1]["competitor_hits"], [])     # B0ZZZZZZZZ non e' un competitor della bozza

    def test_publisher_rocket_aliases_search_per_month_and_average_sales(self):
        rec = R.parse_file("x.csv", csv_bytes(PR_HEADER, PR_ROWS[:2]), "US")
        k = rec["keywords"][0]
        self.assertEqual((k["avg_sales"], k["search_volume_text"], k["competition"], k["searches_color"]), (243, "<100", 3, "Red"))
        self.assertEqual(rec["unmapped"], [])
        k2 = R.parse_file("y.csv", csv_bytes(PR_HEADER, [PR_ROWS[5]]), "US")["keywords"][0]
        self.assertEqual(k2["search_volume"], 689)

    def test_keyword_in_several_files_merges_metrics_and_gets_multi_file_evidence(self):
        res = research_of(extra_files=[("ANALYZED REVERSE ASIN LOOKUP - BOOK B0GFW8236T 2026-10-02.csv", [PR_ROWS[0], PR_ROWS[8]])])
        self.assertEqual(len(res["large print cozy mystery books for seniors"]), 2)
        kp, by = plan_kp(research=res)
        e = by["large print cozy mystery books for seniors"][1]
        self.assertEqual(len(e["research"]["records"]), 2)
        self.assertIn("multi_file", e["market_parts"])
        self.assertEqual(sorted(e["competitor_hits"]), sorted([COMP_A, COMP_B]))


class NotesTests(unittest.TestCase):
    def test_notes_with_research_mention_available_and_missing_metrics_only(self):
        kp, _ = plan_kp(research=research_of())
        text = " ".join(kp["limits"])
        self.assertNotIn("solo sui testi della bozza", text)
        self.assertNotIn("Nessun volume", text)
        self.assertIn("Dati Publisher Rocket disponibili", text)
        notes = kp["data_notes"]
        self.assertTrue({"search_volume", "competition", "avg_sales"} <= set(notes["metrics_available"]))
        self.assertIn("title_count", notes["metrics_missing"])
        self.assertIn("title_count", text)                                                # le metriche mancanti sono dichiarate
        self.assertIn("Nessun dato competitor reale", text)                               # ASIN non arricchiti

    def test_notes_without_research_and_with_enriched_competitors(self):
        kp, _ = plan_kp()
        self.assertIn("solo sui testi della bozza", " ".join(kp["limits"]))
        p = plan_of()
        p["competitors"][0]["enrichment"] = {"title": "Cozy Mystery Stories", "provider": "PUBLISHER_ROCKET"}
        kp2, _ = plan_kp(p, research_of())
        text = " ".join(kp2["limits"])
        self.assertNotIn("Nessun dato competitor reale", text)
        self.assertIn("Dati competitor reali per 1 ASIN", text)

    def test_ruleset_change_marks_saved_plan_as_stale(self):
        p = plan_of()
        p["keyword_plan"] = K.build_keyword_plan(p)
        self.assertFalse(K.is_stale(p))
        p["keyword_plan"]["ruleset"] = "launch_keywords_v2"
        self.assertTrue(K.is_stale(p))


class NonCreativeRegressionTests(unittest.TestCase):
    def test_descriptive_titles_use_the_previous_title_topic_logic(self):
        p, _, _ = L.build_plan({"title": "Crochet Flowers", "subtitle": "Easy projects", "description": "Learn to crochet flowers.",
                                "kdp_keywords": ["crochet flowers", "crochet bouquet", "crochet book"], "categories": "Crochet", "marketplace": "US",
                                "currency": "", "price": "16.99", "royalty_per_copy": "7.34", "book_id": "", "competitors": []}, now="x")
        kp = K.build_keyword_plan(p)
        by = {e["keyword"]: k for k in LISTS for e in kp[k] for e in [e]}
        self.assertFalse(kp["profile"]["title_creative"])
        self.assertEqual(by["crochet flowers"], "exact")
        self.assertEqual(by["crochet book"], "discovery")
        self.assertEqual(kp["title_queries"], [])
        for e in kp["exact"] + kp["phrase"] + kp["discovery"]:
            self.assertIn("final_launch_score", e)


# ------------------------------------------------------------------ pagina: meno rumore, "Mostra tutte", note
try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None

PAGE = ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py"


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PageNoiseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "CONFIG").mkdir()
        self.books = self.root / "CONFIG" / "books.json"
        self.books.write_text("[]", encoding="utf-8")
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        self.addCleanup(os.environ.pop, "KDP_ADS_ROOT", None)
        from ops import launch_ops, workspace
        self.ops, self.ws = launch_ops, workspace.from_root(self.root)
        self.lid = self.ops.save_form(self.ws, TW).data["plan"]["launch_id"]
        rows = PR_ROWS + NOISE + [[f"short stories about {w}", "$ 50", "<100", "Red", "20", "Green"] for w in "love war food sea space time dogs cats trains".split()]
        r = self.ops.import_research(self.ws, self.lid, "ANALYZED REVERSE ASIN LOOKUP - BOOK B0H8YTL55T 2026.csv", csv_bytes(PR_HEADER, rows))
        self.assertTrue(r.ok, r.errors)
        self.at = AppTest.from_file(str(PAGE), default_timeout=90).run()
        self.at.selectbox[0].set_value(self.lid).run()
        [b for b in self.at.button if b.label == "GENERA PIANO KEYWORD"][0].click().run()
        self.assertFalse(self.at.exception)

    def frames(self):
        return {h.value.split(" (")[0]: (int(h.value.rsplit("(", 1)[1].rstrip(")")), df.value) for h, df in self._pairs()}

    def _pairs(self):
        heads = [h for h in self.at.header if h.value.split(" (")[0] in ("KEYWORD EXACT CONSIGLIATE", "KEYWORD PHRASE CONSIGLIATE", "DISCOVERY", "SCARTATE / DA NON USARE")]
        dfs = [d for d in self.at.dataframe if "Match consigliato" in d.value.columns]
        # una tabella per ogni sezione non vuota, nello stesso ordine
        out, i = [], 0
        for h in heads:
            n = int(h.value.rsplit("(", 1)[1].rstrip(")"))
            if n:
                out.append((h, dfs[i]))
                i += 1
        return out

    def test_initial_view_is_limited_and_show_all_reveals_everything(self):
        fr = self.frames()
        limits = {"KEYWORD EXACT CONSIGLIATE": 15, "KEYWORD PHRASE CONSIGLIATE": 30, "DISCOVERY": 30, "SCARTATE / DA NON USARE": 30}
        for name, (total, df) in fr.items():
            self.assertLessEqual(len(df), limits[name], name)
            self.assertEqual(len(df), min(total, limits[name]), name)
        total_disc = fr["DISCOVERY"][0]
        self.assertGreater(total_disc, 30)                                                  # abbastanza candidati da richiedere il limite
        self.assertTrue(any("Mostro le prime 30" in c.value for c in self.at.caption))
        for c in ("Pertinenza", "Mercato", "Punteggio finale", "Metriche Publisher Rocket", "Competitor a supporto", "Motivo"):
            self.assertIn(c, fr["DISCOVERY"][1].columns)
        self.at.checkbox[0].check().run()                                                    # "Mostra tutte le keyword"
        fr2 = self.frames()
        self.assertEqual(len(fr2["DISCOVERY"][1]), total_disc)
        self.assertFalse(any("Mostro le prime 30" in c.value for c in self.at.caption))

    def test_title_query_section_notes_and_scores_are_shown(self):
        heads = [h.value for h in self.at.header]
        self.assertTrue(any(h.startswith("TITOLO PROPRIO / QUERY DEL TITOLO") for h in heads))
        caps = " | ".join(c.value for c in self.at.caption)
        self.assertIn("Dati Publisher Rocket disponibili", caps)
        self.assertNotIn("solo sui testi della bozza", caps)
        self.assertIn("Pertinenza =", caps)
        tbl = " ".join(" ".join(map(str, t.value.index)) for t in self.at.table)
        self.assertIn("COMMERCIAL_SEARCH_TOPIC", tbl)
        self.assertIn("Titolo creativo", tbl)

    def test_real_files_untouched_and_no_extra_drafts(self):
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])
        self.assertEqual(len(list((self.root / "LAUNCH").glob("*.json"))), 1)
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), hashlib.sha256(b"[]").hexdigest())


class ReparseTests(unittest.TestCase):
    """Un file importato con un parser piu' vecchio viene riletto IN MEMORIA dall'originale conservato (nessuna scrittura)."""

    def test_old_normalized_record_is_reparsed_from_the_stored_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONFIG").mkdir()
            os.environ["KDP_ADS_ROOT"] = str(root)
            try:
                from ops import launch_ops, workspace
                ws = workspace.from_root(root)
                lid = launch_ops.save_form(ws, TW).data["plan"]["launch_id"]
                data = csv_bytes(PR_HEADER, PR_ROWS[:3])
                self.assertTrue(launch_ops.import_research(ws, lid, "ANALYZED REVERSE ASIN LOOKUP - BOOK B0H8YTL55T x.csv", data).ok)
                d = launch_ops.research_dir(ws, lid)
                norm = next(d.glob("*.normalized.json"))
                rec = json.loads(norm.read_text(encoding="utf-8"))
                self.assertEqual(rec["parser_version"], R.PARSER_VERSION)
                # simula un import vecchio: niente alias "Search Per Month", niente ASIN dal nome del file, versione 1
                for k in rec["keywords"]:
                    for m in ("search_volume_text", "avg_sales", "competitor_asin_source"):
                        k.pop(m, None)
                    k["competitor_asin"] = None
                    k["extra"] = {"Search Per Month": "<100"}
                rec["parser_version"] = 1
                norm.write_text(json.dumps(rec), encoding="utf-8")
                before = {p.name: p.read_bytes() for p in d.iterdir()}
                loaded = launch_ops.load_research(ws, lid)[0]
                self.assertEqual(loaded["parser_version"], R.PARSER_VERSION)
                self.assertEqual(loaded["keywords"][0]["avg_sales"], 243)
                self.assertEqual(loaded["keywords"][0]["competitor_asin"], "B0H8YTL55T")
                self.assertEqual({p.name: p.read_bytes() for p in d.iterdir()}, before)      # nulla riscritto su disco
            finally:
                os.environ.pop("KDP_ADS_ROOT", None)


if __name__ == "__main__":
    unittest.main()
