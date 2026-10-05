"""Lancio nuovo libro: preset PHRASE_FIRST_AUTO (lista PHRASE selezionata + AUTO, EXACT dopo il lancio), query naturali, famiglie di varianti, bid a fasce,
pagina. Dati di prova (caso "Twenty Women, Twenty Mysteries"). Nessuna rete; books.json / DB Ads / INPUT mai toccati."""
import ast
import hashlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

from kdp_ads import launch_keywords as K  # noqa: E402
from kdp_ads import launch_strategy as S  # noqa: E402
from test_launch_commercial import COMP_A, PR_HEADER, PR_ROWS, TW, plan_of, research_of  # noqa: E402
from test_launch_research import csv_bytes  # noqa: E402

UNNATURAL = [["short stories or seniors", "$ 2,681", "1,258", "Green", "50", "Green"], ["seniors short story", "$ 1,226", "561", "Green", "29", "Green"],
             ["short stories for adults women", "$ 442", "566", "Green", "20", "Green"], ["short stories seniors", "$ 1,789", "424", "Green", "27", "Green"],
             ["short story senior", "$ 2,133", "514", "Green", "32", "Green"], ["mystery large print", "$ 1,607", "315", "Green", "13", "Green"]]
VARIANTS = [["senior short stories", "$ 2,383", "456", "Green", "37", "Green"], ["senior short story", "$ 2,188", "484", "Green", "37", "Green"],
            ["seniors short stories", "$ 1,700", "548", "Green", "30", "Green"], ["short story for seniors", "$ 1,741", "576", "Green", "33", "Green"],
            ["short stories for seniors", "$ 2,503", "684", "Green", "83", "Red"]]


def strategy(rows=PR_ROWS, plan=None, **kw):
    kp = K.build_keyword_plan(plan or plan_of(), research=research_of(rows), now="2026-10-01T10:00:00", **kw)
    return kp, kp["launch_strategy"]


def names(s):
    return [x["keyword"] for x in s["phrase_launch"]]


def entry(**o):
    e = {"keyword": "large print cozy mystery books for seniors", "recommended_match_type": "EXACT", "relevance_score": 100, "market_evidence_score": 70,
         "final_launch_score": 90, "evidence_score": 90, "competitor_hits": [], "core_topic": {"strong_concepts": 5},
         "research": {"records": [{"source_file": "a.csv", "search_volume": 500, "competition": 10}]}}
    e.update(o)
    return e


class NaturalityTests(unittest.TestCase):
    def test_unnatural_queries_are_excluded_from_launch_even_with_great_numbers(self):
        kp, s = strategy(PR_ROWS + UNNATURAL)
        in_plan = {e["keyword"] for k in ("exact", "phrase", "discovery", "rejected") for e in kp[k]}
        excl = {x["keyword"]: x for x in s["excluded"]}
        for row in UNNATURAL:
            k = row[0]
            self.assertNotIn(k, names(s), k)                                           # fuori dalla campagna di lancio
            self.assertIn(k, in_plan, k)                                               # ma restano nei dati del piano keyword
            self.assertEqual(excl[k]["kind"], "UNNATURAL", k)
            self.assertIn("Query innaturale", excl[k]["reason"])

    def test_launch_natural_function(self):
        for bad in ("short stories or seniors", "seniors short story", "short stories for adults women", "short stories seniors", "short story senior",
                    "mystery large print", "short stories for adults mysteries", "short stories books"):
            self.assertFalse(S.launch_natural(bad)[0], bad)
        for good in ("mystery books for senior women", "cozy mystery short stories", "large print mysteries", "large print adult mysteries",
                     "short stories for older women", "senior short stories", "cozy mystery with large print", "large print cozy mystery books for seniors"):
            self.assertTrue(S.launch_natural(good)[0], good)
        self.assertTrue(S.launch_natural("short story senior", user_keyword=True)[0])        # le keyword dell'autore non vengono escluse da questa euristica


class VariantFamilyTests(unittest.TestCase):
    def test_near_identical_variants_collapse_to_the_best_one(self):
        kp, s = strategy(PR_ROWS + VARIANTS)
        fam = [k for k in names(s) if S._family(k) == S._family("short stories for seniors")]
        self.assertEqual(fam, ["short stories for seniors"])                                # solo la forma piu' naturale
        known = {x["keyword"]: x for x in s["excluded"] if x["kind"] == "VARIANT"}
        for v in ("senior short stories", "senior short story", "short story for seniors"):
            self.assertEqual(known[v]["variant_of"], "short stories for seniors", v)       # varianti note, fuori dalla lista iniziale
        self.assertNotIn("seniors short stories", names(s))
        winner = [x for x in s["phrase_launch"] if x["keyword"] == "short stories for seniors"][0]
        self.assertTrue(set(winner["variants"]) >= {"senior short stories", "short story for seniors"})

    def test_family_function_ignores_order_plural_preposition_and_fillers(self):
        f = S._family
        self.assertEqual(f("senior short stories"), f("short story for seniors"))
        self.assertEqual(f("large print mysteries for seniors"), f("large print mystery with seniors"))
        self.assertEqual(f("very short stories for seniors"), f("short stories for seniors"))
        self.assertNotEqual(f("cozy mystery short stories"), f("cozy mystery short story series"))   # "series" cambia il prodotto


class SelectionTests(unittest.TestCase):
    def test_analytical_exact_is_launched_as_phrase_and_both_are_kept(self):
        kp, s = strategy()
        self.assertTrue(s["phrase_launch"])
        exact_in_launch = [x for x in s["phrase_launch"] if x["analytical_classification"] == "EXACT"]
        self.assertTrue(exact_in_launch)
        for x in s["phrase_launch"]:
            self.assertEqual(x["recommended_launch_match"], "PHRASE")                    # sempre PHRASE nel preset
            self.assertIn(x["analytical_classification"], ("EXACT", "PHRASE", "DISCOVERY"))
        by = {e["keyword"]: e for k in ("exact", "phrase", "discovery") for e in kp[k]}
        x = exact_in_launch[0]
        self.assertEqual(by[x["keyword"]]["recommended_match_type"], "EXACT")             # la classificazione analitica del piano resta EXACT
        self.assertNotIn("EXACT", s["campaigns_at_launch"])

    def test_never_more_than_twenty_keywords(self):
        many = [[f"large print cozy mystery books for {w}", "$ 100", "300", "Green", str(10 + i), "Green"]
                for i, w in enumerate("seniors women grandmothers retirees readers aunts mothers sisters friends ladies neighbors nurses teachers".split())]
        many += [[f"cozy mystery short stories about {w}", "$ 100", "300", "Green", "10", "Green"] for w in "tea cats baking gardens knitting books villages".split()]
        kp, s = strategy(PR_ROWS + many)
        self.assertLessEqual(len(names(s)), 20)
        self.assertEqual(s["phrase"]["count"], len(names(s)))
        self.assertLessEqual(len(s["phrase"]["keywords"]), 20)
        over = [x for x in s["excluded"] if x["kind"] == "OVER_LIMIT"]
        self.assertTrue(over or len(names(s)) < 20)                                        # se superano il limite, finiscono in "oltre il limite"

    def test_no_artificial_filling(self):
        p = plan_of(kdp_keywords=["cozy mystery short stories", "mystery books for senior women"])
        kp = K.build_keyword_plan(p, now="x")                                              # niente Publisher Rocket: poche keyword valide
        s = kp["launch_strategy"]
        self.assertLess(len(names(s)), S.CFG["target_min"])
        self.assertIn("non viene riempita artificialmente", s["phrase"]["note"])
        self.assertTrue(all(x["relevance_score"] >= S.CFG["min_relevance"] for x in s["phrase_launch"]))
        _, s2 = strategy([["iphone case", "$ 90", "99,999", "Green", "3", "Green"]])
        self.assertNotIn("iphone case", names(s2))                                          # mai fuori tema, anche con ottimi numeri

    def test_rejected_and_title_query_never_in_launch(self):
        kp, s = strategy(PR_ROWS + [["twenty women twenty mysteries", "$ 10", "<100", "Red", "5", "Green"]])
        self.assertNotIn("twenty women twenty mysteries", names(s))
        for bad in ("mystery coloring book for kids", "mystery thrillers for seniors", "short stories for elderly free", "iphone case"):
            self.assertNotIn(bad, names(s), bad)

    def test_relevance_counts_more_than_volume(self):
        high_vol_low_rel = entry(keyword="mystery stories for seniors", relevance_score=60, core_topic={"strong_concepts": 2},
                                 research={"records": [{"source_file": "a.csv", "search_volume": 50000, "competition": 5}]})
        high_rel_low_vol = entry(keyword="large print cozy mystery books for seniors", relevance_score=100,
                                 research={"records": [{"source_file": "a.csv", "search_volume_text": "<100", "competition": 40}]})
        a, b = S.launch_priority(high_vol_low_rel)[0], S.launch_priority(high_rel_low_vol)[0]
        self.assertGreater(b, a)
        w = S.CFG["weights"]
        self.assertGreater(w["relevance"] + w["natural"] + w["coverage"], 2 * w["evidence"])

    def test_priority_parts_are_auditable(self):
        kp, s = strategy()
        x = s["phrase_launch"][0]
        for k in ("relevance", "natural", "coverage", "evidence", "evidence_parts"):
            self.assertIn(k, x["priority_parts"])
        self.assertEqual([y["launch_priority_score"] for y in s["phrase_launch"]], sorted((y["launch_priority_score"] for y in s["phrase_launch"]), reverse=True))

    def test_result_resembles_the_validated_manual_selection(self):
        kp, s = strategy(PR_ROWS + VARIANTS + UNNATURAL)
        manual = {"mystery books for senior women", "cozy mystery short stories", "large print mystery stories for adults",
                  "large print cozy mystery books for seniors", "cozy mystery short stories for seniors", "short stories for senior women",
                  "short stories for seniors", "short stories for older women"}
        got = set(names(s))
        self.assertGreaterEqual(len(got & manual), 5)
        self.assertLessEqual(len(got), 20)


class StructureAndBidTests(unittest.TestCase):
    def setUp(self):
        self.kp, self.s = strategy()

    def test_phrase_plus_auto_and_exact_deferred(self):
        s = self.s
        self.assertEqual((s["preset"], s["campaigns_at_launch"]), ("PHRASE_FIRST_AUTO", ["PHRASE", "AUTO"]))
        self.assertTrue(s["auto"]["recommended"])
        self.assertFalse(s["auto"]["keywords_required"])
        self.assertFalse(s["exact"]["needed_at_launch"])
        self.assertEqual(s["exact"]["status"], "Da creare/promuovere dai Search Term vincitori")
        self.assertIn("Valuta promozione in EXACT", s["exact"]["how"])
        self.assertIn("Opportunità Search Term", s["exact"]["how"])

    def test_bid_tiers_and_auto_bid(self):
        tiers = {"TOP_TIER": 0.90, "CORE": 0.85, "BROAD_STRONG_MARKET": 0.80, "BROAD": 0.75}
        self.assertEqual(self.s["bid_tiers"], tiers)
        for x in self.s["phrase_launch"]:
            self.assertEqual(x["suggested_bid"], tiers[x["tier"]])
            self.assertEqual(x["bid_currency"], "USD")
        self.assertEqual(self.s["auto"]["suggested_bid"], 0.60)
        self.assertLess(self.s["auto"]["suggested_bid"], min(tiers.values()))              # AUTO piu' prudente
        tops = [x for x in self.s["phrase_launch"] if x["tier"] == "TOP_TIER"]
        for x in tops:
            self.assertGreaterEqual(x["relevance_score"], 90)
            self.assertGreaterEqual(x["market_evidence_score"], 65)

    def test_tier_function(self):
        self.assertEqual(S._tier(entry(relevance_score=95), 80, "EXACT"), "TOP_TIER")
        self.assertEqual(S._tier(entry(relevance_score=95), 40, "EXACT"), "CORE")          # nessuna evidenza di mercato forte
        self.assertEqual(S._tier(entry(relevance_score=95), None, "PHRASE"), "CORE")
        self.assertEqual(S._tier(entry(relevance_score=70), 80, "DISCOVERY"), "BROAD_STRONG_MARKET")
        self.assertEqual(S._tier(entry(relevance_score=70), 30, "DISCOVERY"), "BROAD")
        self.assertEqual(S._tier(entry(relevance_score=84), 90, "PHRASE"), "BROAD_STRONG_MARKET")

    def test_strong_discovery_can_enter_as_broad(self):
        kp = {"exact": [], "phrase": [], "rejected": [], "title_queries": [], "discovery": [
            entry(keyword="short stories for older adults to read", recommended_match_type="DISCOVERY", relevance_score=92, market_evidence_score=80,
                  core_topic={"strong_concepts": 3}),
            entry(keyword="short stories for adults", recommended_match_type="DISCOVERY", relevance_score=85, market_evidence_score=95,
                  core_topic={"strong_concepts": 2})]}
        s = S.build_strategy(kp, {"book": {"currency": "USD", "kdp_keywords": []}})
        self.assertEqual(names(s), ["short stories for older adults to read"])              # DISCOVERY forte si', quella ampia no
        self.assertEqual(s["phrase_launch"][0]["tier"], "BROAD_STRONG_MARKET")
        self.assertEqual(s["phrase_launch"][0]["suggested_bid"], 0.80)

    def test_copy_text_one_keyword_per_line(self):
        txt = self.s["phrase"]["copy_text"]
        self.assertEqual(txt.split("\n"), names(self.s))
        self.assertEqual(len(set(txt.split("\n"))), len(txt.split("\n")))                  # nessun duplicato
        self.assertTrue(all(line == line.strip() and line for line in txt.split("\n")))

    def test_bids_are_suggestions_only(self):
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "launch_strategy.py").read_text(encoding="utf-8")
        imported = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported.add(n.module.split(".")[0])
        self.assertFalse(imported & {"requests", "urllib", "urllib3", "http", "socket", "sqlite3", "subprocess", "os", "shutil", "pathlib"}, imported)
        code = src.replace(ast.get_docstring(ast.parse(src), clean=False) or "", "")
        for forbidden in ("amazon_ads", "books.json", "INPUT", "write_text", "write_bytes"):
            self.assertNotIn(forbidden, code)
        self.assertIsNone(re.search(r"(?<![\w.])open\(", code))
        self.assertIn("mai applicat", src)
        self.assertTrue(any("mai applicati" in n for n in self.s["notes"]))


try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None

PAGE = ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py"


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PageTests(unittest.TestCase):
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
        rows = PR_ROWS + VARIANTS + UNNATURAL
        self.assertTrue(self.ops.import_research(self.ws, self.lid, "ANALYZED REVERSE ASIN LOOKUP - BOOK B0H8YTL55T x.csv", csv_bytes(PR_HEADER, rows)).ok)
        self.at = AppTest.from_file(str(PAGE), default_timeout=90).run()
        self.at.selectbox[0].set_value(self.lid).run()
        [b for b in self.at.button if b.label == "GENERA PIANO KEYWORD"][0].click().run()
        self.assertFalse(self.at.exception)
        self.saved = self.ops.get_plan(self.ws, self.lid)["keyword_plan"]["launch_strategy"]

    def test_strategy_section_content(self):
        heads = [h.value for h in self.at.header]
        self.assertIn("STRATEGIA DI LANCIO", heads)
        subs = [h.value for h in self.at.subheader]
        self.assertTrue(any(s.startswith("A · PHRASE DA LANCIARE") for s in subs))
        self.assertIn("B · AUTO", subs)
        self.assertIn("C · EXACT", subs)
        tbl = [t.value for t in self.at.table]
        top = [t for t in tbl if "Campagna iniziale consigliata" in t.index][0]
        self.assertEqual(top.loc["Campagna iniziale consigliata"].iloc[0], "PHRASE + AUTO")
        self.assertIn("NON necessaria al lancio", top.loc["Campagna EXACT"].iloc[0])
        self.assertTrue(any("Da alimentare dai vincitori reali dopo il lancio" in i.value for i in self.at.info))
        df = [d.value for d in self.at.dataframe if "Match di lancio consigliato" in d.value.columns][0]
        for c in ("Keyword", "Classificazione analitica", "Match di lancio consigliato", "Pertinenza", "Mercato", "Punteggio finale", "Volume", "Competizione",
                  "Competitor a supporto", "Bid suggerito", "Motivo"):
            self.assertIn(c, df.columns)
        self.assertLessEqual(len(df), 20)
        self.assertEqual(set(df["Match di lancio consigliato"]), {"PHRASE"})
        self.assertEqual(list(df["Keyword"]), [x["keyword"] for x in self.saved["phrase_launch"]])
        auto = [t for t in tbl if "Consigliata" in t.index][0]
        self.assertEqual(auto.loc["Consigliata"].iloc[0], "Sì")

    def test_copy_box_has_one_keyword_per_line(self):
        box = [t for t in self.at.text_area if t.label == "Copia keyword PHRASE"][0]
        self.assertEqual(box.value.split("\n"), self.saved["phrase"]["keywords"])
        self.assertEqual(box.value, self.saved["phrase"]["copy_text"])

    def test_unnatural_queries_are_not_in_the_copy_box(self):
        box = [t for t in self.at.text_area if t.label == "Copia keyword PHRASE"][0].value.split("\n")
        for bad in ("short stories or seniors", "seniors short story", "short story senior", "mystery large print"):
            self.assertNotIn(bad, box)

    def test_nothing_else_touched(self):
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), hashlib.sha256(b"[]").hexdigest())


class OpportunitiesLinkTests(unittest.TestCase):
    def test_standard_label_for_exact_promotion(self):
        import ui_labels as U
        self.assertEqual(U.label_opportunity("PROMOTE_TO_EXACT"), "Valuta promozione in EXACT")
        self.assertEqual(U.LAUNCH_STRATEGY_EXACT_TEXT, "Da alimentare dai vincitori reali dopo il lancio")
        self.assertEqual(U.LAUNCH_STRATEGY_SECTION, "STRATEGIA DI LANCIO")


if __name__ == "__main__":
    unittest.main()
