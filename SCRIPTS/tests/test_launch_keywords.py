"""Lancio nuovo libro V2: piano keyword Ads iniziale (candidati, classificazione, evidenza, piano campagne, salvataggio, pagina).
Solo dati locali e temporanei. Nessuna rete, nessuna Amazon, books.json mai toccato."""
import ast
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

from kdp_ads import launch as L  # noqa: E402
from kdp_ads import launch_keywords as K  # noqa: E402

ASIN_A, ASIN_B = "B0FTFJL6RB", "B0GWWX3MBD"
LISTS = ("exact", "phrase", "discovery", "rejected")


def form(**over):
    f = {"title": "Crochet Flowers: Create Beautiful, Balanced Projects with Confidence",
         "subtitle": "From First Stitches to Sculptural Blooms Without Guesswork or Frustration",
         "description": ("Learn to crochet flowers step by step. This book includes crochet flower patterns for beginners, amigurumi blooms and "
                         "bouquets. Click here to buy now. Perfect gift for crochet lovers."),
         "kdp_keywords": ["crochet flowers", "crochet flower patterns", "crochet bouquet", "beginner crochet book"],
         "categories": "Books > Crafts, Hobbies & Home > Crochet\nCrochet", "marketplace": "US", "currency": "", "price": "16.99",
         "royalty_per_copy": "7.34", "book_id": "", "competitors": []}
    f.update(over)
    return f


def plan_of(**over):
    p, errors, _ = L.build_plan(form(**over), now="2026-10-01T10:00:00")
    assert p, errors
    p["launch_id"] = "20261001_crochet_US"
    return p


def kp_of(**over):
    return K.build_keyword_plan(plan_of(**over), now="2026-10-01T10:00:00")


def find(kp, kw):
    for k in LISTS:
        for e in kp[k]:
            if e["keyword"] == kw:
                return e
    return None


class ClassificationTests(unittest.TestCase):
    def test_exact_from_very_relevant_title_phrase(self):
        e = find(kp_of(), "crochet flowers")
        self.assertEqual(e["recommended_match_type"], "EXACT")
        self.assertEqual(e["confidence"], "HIGH")
        self.assertIn("title", e["source_types"])
        self.assertIn("pertinenza", e["reason"])

    def test_generic_or_broad_keyword_is_phrase_or_discovery_never_exact(self):
        kp = kp_of(kdp_keywords=["crochet flowers", "crochet"])
        e = find(kp, "crochet")
        self.assertIn(e["recommended_match_type"], ("PHRASE", "DISCOVERY"))
        self.assertIn("ampia", e["reason"])

    def test_pure_generic_keyword_is_rejected(self):
        e = find(kp_of(kdp_keywords=["crochet flowers", "best book guide"]), "best book guide")
        self.assertEqual(e["recommended_match_type"], "REJECT")
        self.assertIn("generica", e["reason"])

    def test_off_topic_keyword_is_rejected_with_reason(self):
        kp = kp_of(kdp_keywords=["crochet flowers", "iphone case"])
        e = find(kp, "iphone case")
        self.assertEqual(e["recommended_match_type"], "REJECT")
        self.assertIn("Fuori tema", e["reason"])
        self.assertNotIn("iphone case", kp["campaigns"]["EXACT"]["keywords"] + kp["campaigns"]["PHRASE"]["keywords"])

    def test_promotional_terms_are_rejected(self):
        e = find(kp_of(kdp_keywords=["crochet flowers", "free crochet flowers bestseller"]), "free crochet flowers bestseller")
        self.assertEqual(e["recommended_match_type"], "REJECT")
        self.assertIn("promozionale", e["reason"])

    def test_marketing_phrase_not_listed_as_recommended(self):
        kp = kp_of()
        for e in kp["exact"] + kp["phrase"]:
            self.assertNotIn("balanced", e["keyword"])          # termini presenti in una sola fonte: testo di marketing
        self.assertTrue(any("balanced" in e["keyword"] for e in kp["rejected"]))

    def test_description_only_phrase_is_discovery_at_most(self):
        kp = kp_of()
        for k in ("exact", "phrase"):
            for e in kp[k]:
                self.assertNotEqual(e["source_types"], ["description"])

    def test_relevance_outweighs_source_count(self):
        """Una frase molto pertinente batte una poco pertinente presente in piu' fonti."""
        p = plan_of()
        prof = K._profile(p)
        cands = K.generate_candidates(p)
        good = K.evaluate(cands["crochet flowers"], p, prof, {}, cands)
        self.assertGreater(good["relevance"], 0.9)
        weak = K._Cand("gift book")
        for src, whole in (("title", True), ("subtitle", False), ("description", False), ("category", False)):
            weak.add(src, "x", whole=whole)
        e = K.evaluate(weak, p, prof, {}, cands)
        self.assertLess(e["evidence_score"], good["evidence_score"])
        self.assertEqual(e["recommended_match_type"], "REJECT")
        self.assertGreater(K.RULES["weights"]["relevance"], 2 * K.RULES["weights"]["source_diversity"])

    def test_each_keyword_has_required_fields(self):
        kp = kp_of()
        for k in LISTS:
            for e in kp[k]:
                for f in ("keyword", "sources", "evidence_score", "confidence", "recommended_match_type", "reason"):
                    self.assertTrue(e.get(f) not in (None, "", []), (k, e["keyword"], f))
                self.assertIn(e["confidence"], ("HIGH", "MEDIUM", "LOW"))
                self.assertTrue(0 <= e["evidence_score"] <= 100)


class NormalizationAndSourcesTests(unittest.TestCase):
    def test_dedup_case_spacing_and_punctuation(self):
        p = plan_of(kdp_keywords=["Crochet  Flowers", "crochet flowers!", "CROCHET FLOWERS"], title="Crochet Flowers")
        self.assertEqual(sum(1 for k in K.generate_candidates(p) if k == "crochet flowers"), 1)
        kp = K.build_keyword_plan(p)
        self.assertEqual(sum(1 for k in LISTS for e in kp[k] if e["keyword"] == "crochet flowers"), 1)

    def test_keywords_are_lowercase_and_trimmed(self):
        kp = kp_of(kdp_keywords=["  Crochet   FLOWERS  "])
        for k in LISTS:
            for e in kp[k]:
                self.assertEqual(e["keyword"], e["keyword"].lower().strip())
                self.assertNotIn("  ", e["keyword"])

    def test_sources_and_originals_preserved(self):
        e = find(kp_of(), "crochet flowers")
        pairs = {(s["source"], s.get("original")) for s in e["sources"]}
        self.assertIn(("title", "Crochet Flowers: Create Beautiful, Balanced Projects with Confidence"), pairs)
        self.assertIn(("kdp_keyword", "crochet flowers"), pairs)
        self.assertTrue({"title", "kdp_keyword", "description"} <= set(e["source_types"]))

    def test_singular_and_plural_kept_apart_but_linked(self):
        p = plan_of(kdp_keywords=["crochet flower", "crochet flowers"], title="Crochet Flower Guide")
        cands = K.generate_candidates(p)
        self.assertIn("crochet flower", cands)
        self.assertIn("crochet flowers", cands)
        e = K.evaluate(cands["crochet flowers"], p, K._profile(p), {}, cands)
        self.assertIn("crochet flower", e["variant_of"])
        self.assertIn("non fuse", e["reason"])


class CompetitorTests(unittest.TestCase):
    def test_no_competitor_data_is_ever_invented(self):
        p = plan_of(competitors=[{"asin": ASIN_A}])
        kp = K.build_keyword_plan(p)
        self.assertFalse(kp["competitor_data_available"])
        for k in LISTS:
            for e in kp[k]:
                self.assertEqual(e["competitor_hits"], [])
                self.assertNotIn("competitor", e["source_types"])
        self.assertEqual(kp["profile"]["competitors_with_data"], 0)
        self.assertEqual(kp["campaigns"]["COMPETITOR"]["asins"], [ASIN_A])                  # solo cio' che ha fornito l'utente
        self.assertIsNone(p["competitors"][0]["enrichment"])

    def test_provider_data_adds_evidence_only_for_confirmed_keywords(self):
        p = plan_of(competitors=[{"asin": ASIN_A}])
        base = K.build_keyword_plan(p)
        prov = L.StaticProvider({(ASIN_A, "US"): {"title": "Crochet Flowers for Beginners", "categories": ["Crochet"], "rating": 4.5, "review_count": 120}})
        kp = K.build_keyword_plan(L.enrich_competitors(p, prov, now="2026-10-01T10:00:00"))
        self.assertTrue(kp["competitor_data_available"])
        e, b = find(kp, "crochet flowers"), find(base, "crochet flowers")
        self.assertEqual(e["competitor_hits"], [ASIN_A])
        self.assertIn("competitor", e["source_types"])
        self.assertIn("confermata da competitor reali", e["reason"])
        self.assertGreaterEqual(e["evidence_score"], b["evidence_score"] - 5)               # confermata: mai penalizzata
        other = find(kp, "crochet bouquet")
        self.assertEqual(other["competitor_hits"], [])
        self.assertGreater(e["evidence_score"], other["evidence_score"])
        self.assertIsNone(p["competitors"][0]["enrichment"])                                # enrich lavora su una copia

    def test_unverified_asin_never_counts_for_competitor_campaign(self):
        p = plan_of(competitors=[{"asin": ASIN_A}, {"asin": "ZZZZZZZZZZ"}])
        self.assertEqual(K.build_keyword_plan(p)["campaigns"]["COMPETITOR"]["asins"], [ASIN_A])


class CampaignPlanTests(unittest.TestCase):
    def test_complete_plan_structure(self):
        kp = kp_of(competitors=[{"asin": ASIN_A}, {"asin": ASIN_B}])
        c = kp["campaigns"]
        self.assertEqual(set(c), {"EXACT", "PHRASE", "AUTO", "COMPETITOR"})
        self.assertEqual(c["EXACT"]["keywords"], [e["keyword"] for e in kp["exact"]])
        self.assertEqual(c["PHRASE"]["keywords"], [e["keyword"] for e in kp["phrase"]])
        self.assertTrue(c["COMPETITOR"]["available"])
        self.assertEqual(c["COMPETITOR"]["asins"], [ASIN_A, ASIN_B])
        for k in ("profile", "exact", "phrase", "discovery", "rejected", "campaigns", "limits", "rules", "generated_at", "input_fingerprint"):
            self.assertIn(k, kp)
        self.assertEqual(kp["profile"]["break_even_acos"], 43.2)

    def test_auto_is_always_present_and_never_operational(self):
        for comps in ([], [{"asin": ASIN_A}]):
            kp = kp_of(competitors=comps, kdp_keywords=[])
            a = kp["campaigns"]["AUTO"]
            self.assertFalse(a["operational"])
            self.assertFalse(a["keywords_required"])
            self.assertEqual(a["keywords"], [])
            self.assertEqual(a["role"], "discovery iniziale")
            for name in ("EXACT", "PHRASE", "COMPETITOR"):
                self.assertFalse(kp["campaigns"][name]["operational"])

    def test_competitor_campaign_only_with_validated_asins(self):
        c = kp_of(competitors=[])["campaigns"]["COMPETITOR"]
        self.assertFalse(c["available"])
        self.assertEqual(c["asins"], [])
        self.assertIn("non proposta", c["note"])

    def test_lists_are_not_padded_and_respect_limits(self):
        small = kp_of(kdp_keywords=["crochet flowers"], description="", subtitle="")
        self.assertLess(len(small["exact"]) + len(small["phrase"]), 6)                      # poche keyword valide => poche proposte
        words = ["flowers", "bouquet", "wreath", "garland", "brooch", "headband", "coaster", "blanket", "scarf", "hat", "bag", "toy", "cushion", "doily"]
        kws = [f"crochet {w}" for w in words]
        big = K.build_keyword_plan(plan_of(title="Crochet Mastery: " + ", ".join(kws), description=" ".join(f"{k}." for k in kws), kdp_keywords=kws[:7],
                                           categories="Crochet\n" + "\n".join(f"Crochet {w}" for w in words[:6])))
        self.assertLessEqual(len(big["exact"]), K.RULES["max_exact"])
        self.assertLessEqual(len(big["phrase"]), K.RULES["max_phrase"])
        self.assertGreater(len(big["exact"]) + len(big["phrase"]), 5)

    def test_deterministic(self):
        self.assertEqual(json.dumps(kp_of(), sort_keys=True), json.dumps(kp_of(), sort_keys=True))


CF_KDP = ["crochet flowers", "crochet flower patterns", "crochet flowers for beginners", "crochet bouquet", "crochet book",
          "beginner crochet book", "iphone case"]            # 7 (limite KDP); "crochet" arriva dalla categoria


def cf_plan(**over):
    return plan_of(title="Crochet Flowers", subtitle="Easy projects for every skill level", kdp_keywords=CF_KDP,
                   categories="Books > Crafts, Hobbies & Home > Crochet",
                   description="Learn to crochet flowers and bouquets with clear patterns.", **over)


def cf_cls(plan=None, **kw):
    kp = K.build_keyword_plan(plan or cf_plan(), **kw)
    return {e["keyword"]: e for k in LISTS for e in kp[k]}, kp


class CoreTopicTests(unittest.TestCase):
    """Libro "Crochet Flowers": il tema e' crochet + flower(s), non il solo "crochet"."""

    def test_core_concepts_come_from_title_with_singular_plural_as_one(self):
        kp = K.build_keyword_plan(cf_plan())
        self.assertEqual(kp["profile"]["core_concepts"], ["crochet", "flower/flowers"])

    def test_required_classifications(self):
        by, _ = cf_cls()
        expected = {"crochet flowers": ("EXACT",), "crochet flower patterns": ("EXACT", "PHRASE"), "crochet flowers for beginners": ("EXACT", "PHRASE"),
                    "crochet bouquet": ("PHRASE",), "crochet": ("DISCOVERY",), "crochet book": ("DISCOVERY",),
                    "beginner crochet book": ("DISCOVERY",), "iphone case": ("REJECT",)}
        for kw, ok in expected.items():
            self.assertIn(by[kw]["recommended_match_type"], ok, (kw, by[kw]["recommended_match_type"], by[kw]["reason"]))

    def test_generic_keywords_never_exact_or_phrase_without_external_evidence(self):
        by, kp = cf_cls()
        for kw in ("crochet", "crochet book", "beginner crochet book"):
            self.assertEqual(by[kw]["recommended_match_type"], "DISCOVERY")
            self.assertNotIn(kw, kp["campaigns"]["EXACT"]["keywords"] + kp["campaigns"]["PHRASE"]["keywords"])
            self.assertFalse(by[kw]["core_topic"]["specific"])
            self.assertIn("troppo ampia", by[kw]["reason"])
        self.assertNotEqual(by["crochet"]["confidence"], "HIGH")                           # ne' priorita' alta

    def test_generic_scores_below_specific_ones(self):
        by, _ = cf_cls()
        for g in ("crochet", "crochet book", "beginner crochet book"):
            for s in ("crochet flowers", "crochet flower patterns", "crochet bouquet"):
                self.assertLess(by[g]["evidence_score"], by[s]["evidence_score"], (g, s))

    def test_exact_requires_full_core_topic(self):
        by, _ = cf_cls()
        for e in by.values():
            if e["recommended_match_type"] == "EXACT":
                self.assertTrue(e["core_topic"]["covers_all_core_concepts"], e["keyword"])
                self.assertIn("tema centrale", e["reason"])
        self.assertFalse(by["crochet bouquet"]["core_topic"]["covers_all_core_concepts"])     # PHRASE ma non copre tutto il tema

    def test_singular_plural_same_concept_for_relevance_but_not_merged(self):
        p = cf_plan()
        by, _ = cf_cls(p, )
        self.assertNotEqual(by["crochet flowers"]["keyword"], "crochet flower")
        cands = K.generate_candidates(p)
        self.assertIn("crochet flowers", cands)
        singular = K.evaluate(K._Cand("crochet flower"), p, K._profile(p), {}, cands)
        singular_cand = K._Cand("crochet flower")
        singular_cand.add("kdp_keyword", "crochet flower", kdp_idx=99, whole=True)
        e = K.evaluate(singular_cand, p, K._profile(p), {}, cands)
        self.assertTrue(e["core_topic"]["covers_all_core_concepts"])                          # flower copre il concetto "flowers"
        self.assertEqual(e["recommended_match_type"], "EXACT")
        self.assertIn("crochet flowers", K.build_keyword_plan(p)["campaigns"]["EXACT"]["keywords"])
        self.assertIsNotNone(singular)

    def test_description_mention_is_not_external_confirmation(self):
        p = cf_plan()
        p["book"]["description"] += " This crochet book is for every beginner crochet book lover."
        by, _ = cf_cls(p)
        self.assertEqual(by["crochet book"]["recommended_match_type"], "DISCOVERY")
        self.assertEqual(by["beginner crochet book"]["recommended_match_type"], "DISCOVERY")

    def test_external_evidence_can_lift_general_keyword_to_phrase(self):
        by, _ = cf_cls(external_evidence={"crochet book": ["search_term"], "beginner crochet book": ["research_data", "invalid_kind"]})
        self.assertEqual(by["crochet book"]["recommended_match_type"], "PHRASE")
        self.assertEqual(by["crochet book"]["core_topic"]["external_evidence"], ["search_term"])
        self.assertIn("evidenza esterna forte", by["crochet book"]["reason"])
        self.assertEqual(by["beginner crochet book"]["recommended_match_type"], "PHRASE")
        self.assertEqual(by["beginner crochet book"]["core_topic"]["external_evidence"], ["research_data"])    # tipo non valido ignorato
        self.assertEqual(by["crochet"]["recommended_match_type"], "DISCOVERY")                                # le altre restano ampie
        self.assertNotEqual(by["crochet book"]["recommended_match_type"], "EXACT")                            # mai EXACT
        by2, _ = cf_cls(external_evidence={"crochet book": ["bogus"]})
        self.assertEqual(by2["crochet book"]["recommended_match_type"], "DISCOVERY")

    def test_real_competitor_data_lifts_general_keyword(self):
        p = cf_plan(competitors=[{"asin": ASIN_A}])
        prov = L.StaticProvider({(ASIN_A, "US"): {"title": "The Crochet Book for Beginners", "categories": ["Crochet"]}})
        by, _ = cf_cls(L.enrich_competitors(p, prov, now="2026-10-01T10:00:00"))
        self.assertEqual(by["crochet book"]["recommended_match_type"], "PHRASE")
        self.assertEqual(by["crochet book"]["core_topic"]["external_evidence"], ["competitor"])
        self.assertEqual(by["crochet"]["recommended_match_type"], "DISCOVERY")                 # nessun competitor la conferma
        base, _ = cf_cls(p)                                                                    # senza dati competitor: nessun innalzamento
        self.assertEqual(base["crochet book"]["recommended_match_type"], "DISCOVERY")

    def test_off_topic_still_rejected(self):
        by, kp = cf_cls(external_evidence={"iphone case": ["search_term"]})
        self.assertEqual(by["iphone case"]["recommended_match_type"], "REJECT")               # l'evidenza esterna non salva il fuori tema

    def test_profile_shows_core_concepts_and_is_pure(self):
        p = cf_plan()
        before = json.dumps(p, sort_keys=True)
        kp = K.build_keyword_plan(p)
        self.assertEqual(json.dumps(p, sort_keys=True), before)
        self.assertIn("secondary_anchors", kp["profile"])
        self.assertEqual(kp["ruleset"], "launch_keywords_v3")

    def test_book_without_separator_title_still_has_core_topic(self):
        p = plan_of(title="Crochet Flowers Made Easy for Beginners", subtitle="", kdp_keywords=["crochet flowers", "crochet"])
        kp = K.build_keyword_plan(p)
        self.assertEqual(kp["profile"]["core_concepts"], ["crochet", "flower/flowers"])
        by = {e["keyword"]: e for k in LISTS for e in kp[k]}
        self.assertEqual(by["crochet"]["recommended_match_type"], "DISCOVERY")


class StorageAndPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "CONFIG").mkdir()
        self.books = self.root / "CONFIG" / "books.json"
        self.books.write_text(json.dumps([{"book_id": "X1", "title": "T"}]), encoding="utf-8")
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        self.addCleanup(os.environ.pop, "KDP_ADS_ROOT", None)
        from ops import launch_ops, workspace
        self.ops, self.ws = launch_ops, workspace.from_root(self.root)

    def sha(self):
        return hashlib.sha256(self.books.read_bytes()).hexdigest()

    def saved(self, **over):
        r = self.ops.save_form(self.ws, form(**over))
        self.assertTrue(r.ok, r.errors)
        return r.data["plan"]["launch_id"]

    def test_plan_saved_in_draft_json_only_and_books_json_untouched(self):
        before = self.sha()
        lid = self.saved(competitors=[{"asin": ASIN_A}])
        r = self.ops.generate_keyword_plan(self.ws, lid)
        self.assertTrue(r.ok, r.errors)
        data = json.loads((self.root / "LAUNCH" / f"{lid}.json").read_text(encoding="utf-8"))
        self.assertEqual(data["keyword_plan"]["ruleset"], K.RULESET)
        self.assertIn("campaigns", data["keyword_plan"])
        self.assertEqual(self.sha(), before)                                                 # books.json invariato
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])  # niente INPUT, DB, campagne
        self.assertEqual(sorted(p.name for p in (self.root / "LAUNCH").iterdir()), [f"{lid}.json"])    # nessun file temporaneo residuo

    def test_regenerate_touches_only_that_draft(self):
        a, b = self.saved(), self.saved(title="Altro libro", kdp_keywords=["altro"])
        before_b = (self.root / "LAUNCH" / f"{b}.json").read_bytes()
        self.ops.generate_keyword_plan(self.ws, a)
        self.assertEqual((self.root / "LAUNCH" / f"{b}.json").read_bytes(), before_b)

    def test_missing_draft_is_an_error_not_a_crash(self):
        self.assertFalse(self.ops.generate_keyword_plan(self.ws, "non_esiste").ok)

    def test_stale_detection_and_plan_kept_on_form_update(self):
        lid = self.saved()
        self.ops.generate_keyword_plan(self.ws, lid)
        self.assertFalse(K.is_stale(self.ops.get_plan(self.ws, lid)))
        r = self.ops.save_form(self.ws, form(title="Crochet Flowers: Nuovo sottotitolo"), lid)      # modifica la bozza
        self.assertTrue(r.ok, r.errors)
        plan = self.ops.get_plan(self.ws, lid)
        self.assertIn("keyword_plan", plan)                                                  # il piano resta salvato...
        self.assertTrue(K.is_stale(plan))                                                    # ...ma e' segnalato superato

    def test_generation_makes_no_network_calls(self):
        lid = self.saved(competitors=[{"asin": ASIN_A}])
        with mock.patch("socket.socket", side_effect=AssertionError("rete usata")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("rete usata")):
            self.assertTrue(self.ops.generate_keyword_plan(self.ws, lid).ok)

    def test_page_shows_all_sections_and_saves_plan(self):
        import test_launch as TL
        lid = self.saved(competitors=[{"asin": ASIN_A}])
        page = TL.PageTests()
        st = page.render(self.root, {"Bozza di lancio": lid, "__button__": True})              # clic su GENERA PIANO KEYWORD
        self.assertTrue([c for c in st.method_calls if c[0] == "rerun"])
        self.assertIn("keyword_plan", json.loads((self.root / "LAUNCH" / f"{lid}.json").read_text(encoding="utf-8")))
        st2 = page.render(self.root, {"Bozza di lancio": lid})                                 # il rerun mostra il piano salvato
        txt = str(st2.mock_calls)
        for needle in ("PROFILO DEL LIBRO", "KEYWORD EXACT CONSIGLIATE", "KEYWORD PHRASE CONSIGLIATE", "DISCOVERY", "SCARTATE / DA NON USARE",
                       "COMPETITOR DI RIFERIMENTO", "STRUTTURA CAMPAGNE CONSIGLIATA", "crochet flowers", "Motivo", "Confidenza", "Fonte"):
            self.assertIn(needle, txt)
        self.assertEqual(self.sha(), hashlib.sha256(self.books.read_bytes()).hexdigest())


try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None

PAGE = ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py"
SECTIONS = ("PROFILO DEL LIBRO", "KEYWORD EXACT CONSIGLIATE", "KEYWORD PHRASE CONSIGLIATE", "DISCOVERY", "SCARTATE / DA NON USARE",
            "STRUTTURA CAMPAGNE CONSIGLIATA")


@unittest.skipIf(AppTest is None, "streamlit non installato")
class LaunchPageStateTests(unittest.TestCase):
    """Pagina vera (AppTest): selezione della bozza e piano keyword stabili dopo salvataggio, genera piano e rerun."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "CONFIG").mkdir()
        self.books = self.root / "CONFIG" / "books.json"
        self.books.write_text(json.dumps([{"book_id": "X1", "title": "T"}]), encoding="utf-8")
        self.books_sha = hashlib.sha256(self.books.read_bytes()).hexdigest()
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        self.addCleanup(os.environ.pop, "KDP_ADS_ROOT", None)
        self.at = AppTest.from_file(str(PAGE), default_timeout=60).run()
        self.assertFalse(self.at.exception)

    # --- helper
    def field(self, label):
        return [x for x in self.at.text_input if x.label == label][0]

    def button(self, label):
        return [b for b in self.at.button if b.label == label][0]

    def picker(self):
        return [s for s in self.at.selectbox if s.label == "Bozza di lancio"][0]

    def picked(self):
        return self.picker().value

    def drafts(self):
        d = self.root / "LAUNCH"
        return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

    def headers(self):
        return [h.value for h in self.at.header]

    def fill_and_save(self):
        self.field("Titolo").set_value("Crochet Flowers")
        self.field("Prezzo").set_value("16.99")
        self.field("Royalty per copia").set_value("7.34")
        self.field("Keyword 1").set_value("crochet flowers")
        self.field("Keyword 2").set_value("crochet bouquet")
        self.button("SALVA BOZZA LANCIO").click().run()
        self.assertFalse(self.at.exception)

    def plan_json(self, name):
        return json.loads((self.root / "LAUNCH" / name).read_text(encoding="utf-8"))

    # --- test
    def test_new_draft_save_keeps_the_created_draft_selected(self):
        self.assertIsNone(self.picked())                                                       # all'inizio: "Nuova bozza"
        self.fill_and_save()
        files = self.drafts()
        self.assertEqual(len(files), 1)
        lid = files[0][:-5]
        self.assertEqual(self.picked(), lid)                                                   # la bozza appena creata e' selezionata
        self.assertEqual(self.at.session_state["current_launch_id"], lid)
        self.assertEqual(self.at.session_state["selected_launch_id"], lid)
        self.assertTrue(any("Bozza di lancio salvata" in s.value for s in self.at.success))
        self.assertIn("GENERA PIANO KEYWORD", [b.label for b in self.at.button])               # ora il pulsante e' disponibile

    def test_generate_plan_updates_same_draft_without_duplicates(self):
        self.fill_and_save()
        lid = self.drafts()[0][:-5]
        before = self.plan_json(f"{lid}.json")
        self.assertNotIn("keyword_plan", before)
        self.button("GENERA PIANO KEYWORD").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.picked(), lid)                                                   # stessa bozza, non torna a "Nuova bozza"
        self.assertEqual(self.drafts(), [f"{lid}.json"])                                       # nessun _2, _3
        after = self.plan_json(f"{lid}.json")
        self.assertIn("keyword_plan", after)
        self.assertEqual(after["launch_id"], lid)
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["book"], before["book"])                                        # i dati della bozza non cambiano
        self.assertIs(after["keyword_plan"]["campaigns"]["AUTO"]["keywords_required"], False)

    def test_plan_is_rendered_right_after_generation_and_survives_reruns(self):
        self.fill_and_save()
        lid = self.drafts()[0][:-5]
        self.assertFalse(set(SECTIONS[:-1]) & set(self.headers()))                              # prima: nessuna sezione del piano keyword
        self.button("GENERA PIANO KEYWORD").click().run()
        for _ in range(3):                                                                     # rerun successivi (anche senza clic)
            heads = " | ".join(self.headers())
            for s in SECTIONS:
                self.assertIn(s, heads)
            self.assertEqual(self.picked(), lid)
            self.at.run()
        self.assertEqual(self.drafts(), [f"{lid}.json"])

    def test_generate_twice_still_one_file(self):
        self.fill_and_save()
        lid = self.drafts()[0][:-5]
        for _ in range(2):
            self.button("GENERA PIANO KEYWORD").click().run()
            self.assertEqual(self.picked(), lid)
        self.assertEqual(self.drafts(), [f"{lid}.json"])

    def test_saving_again_updates_the_same_draft(self):
        self.fill_and_save()
        lid = self.drafts()[0][:-5]
        self.field("Sottotitolo").set_value("Easy projects")
        self.button("SALVA BOZZA LANCIO").click().run()                                        # secondo salvataggio = aggiornamento esplicito
        self.assertEqual(self.drafts(), [f"{lid}.json"])
        self.assertEqual(self.plan_json(f"{lid}.json")["book"]["subtitle"], "Easy projects")
        self.assertEqual(self.picked(), lid)

    def test_manual_selection_and_new_draft_still_work(self):
        self.fill_and_save()
        first = self.drafts()[0][:-5]
        self.picker().set_value(None).run()                                                    # l'utente sceglie "Nuova bozza"
        self.assertIsNone(self.picked())
        self.assertNotIn("GENERA PIANO KEYWORD", [b.label for b in self.at.button])
        self.field("Titolo").set_value("Altro libro")
        self.field("Prezzo").set_value("9.99")
        self.field("Royalty per copia").set_value("3.5")
        self.button("SALVA BOZZA LANCIO").click().run()
        files = self.drafts()
        self.assertEqual(len(files), 2)                                                        # una bozza esplicitamente nuova
        second = [f[:-5] for f in files if f[:-5] != first][0]
        self.assertEqual(self.picked(), second)

    def test_validation_error_keeps_state_and_saves_nothing(self):
        self.field("Titolo").set_value("")
        self.button("SALVA BOZZA LANCIO").click().run()
        self.assertEqual(self.drafts(), [])
        self.assertIsNone(self.picked())
        self.assertTrue(self.at.error)

    def test_no_books_json_db_or_network(self):
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("rete usata")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("rete usata")):
            self.fill_and_save()
            self.button("GENERA PIANO KEYWORD").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), self.books_sha)   # books.json invariato
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])     # nessun DB Ads, INPUT, OUTPUT, LOGS
        self.assertEqual(sorted(p.name for p in (self.root / "CONFIG").iterdir()), ["books.json"])


class SafetyTests(unittest.TestCase):
    def test_no_network_or_amazon_code_in_new_modules(self):
        for p in (ROOT / "SCRIPTS" / "kdp_ads" / "launch_keywords.py", ROOT / "DASHBOARD" / "ops" / "launch_ops.py"):
            src = p.read_text(encoding="utf-8")
            imported = set()
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Import):
                    imported |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imported.add(n.module.split(".")[0])
            self.assertFalse(imported & {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp", "boto3", "selenium", "playwright", "bs4", "sqlite3"},
                             (p.name, imported))
            self.assertNotIn("amazon_ads", src)

    def test_keyword_module_never_touches_books_or_input(self):
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "launch_keywords.py").read_text(encoding="utf-8")
        for forbidden in ("books.json", "INPUT", "open(", "write_text", "write_bytes", "registry"):
            self.assertNotIn(forbidden, src)

    def test_pure_functions_do_not_mutate_the_draft(self):
        p = plan_of(competitors=[{"asin": ASIN_A}])
        before = json.dumps(p, sort_keys=True)
        K.build_keyword_plan(p)
        self.assertEqual(json.dumps(p, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
