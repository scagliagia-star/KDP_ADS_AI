"""Lancio nuovo libro: modello, validazione ASIN competitor, archiviazione locale, evidenza keyword, provider astratto, pagina. Nessuna rete."""
import ast
import json
import os
import runpy
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

from kdp_ads import launch as L
from kdp_ads.launch import LaunchError

ASIN_A, ASIN_B, ASIN_C = "B0FTFJL6RB", "B0GWWX3MBD", "B0G83R8BG4"


def form(**over):
    f = {"title": "Crochet Flowers Made Easy", "subtitle": "Beautiful flower patterns for beginners", "description": "Learn to crochet flower bouquets step by step.",
         "kdp_keywords": ["crochet flowers", "crochet flower patterns", "beginner crochet book"], "categories": "Crafts & Hobbies\nCrochet",
         "marketplace": "US", "currency": "", "price": "16,99", "royalty_per_copy": "7.34", "book_id": "", "competitors": []}
    f.update(over)
    return f


def plan_of(**over):
    p, errors, _ = L.build_plan(form(**over), now="2026-10-01T10:00:00")
    assert p, errors
    p["launch_id"] = "20261001_crochet-flowers-made-easy_US"
    return p


class AsinTests(unittest.TestCase):
    def test_parse_plain_and_url(self):
        self.assertEqual(L.parse_asin_input(" b0ftfjl6rb ", "US"), ASIN_A)
        self.assertEqual(L.parse_asin_input("https://www.amazon.com/Some-Title/dp/B0FTFJL6RB/ref=sr_1_1?x=1", "US"), ASIN_A)
        self.assertEqual(L.parse_asin_input("https://www.amazon.co.uk/dp/B0FTFJL6RB", "UK"), ASIN_A)

    def test_invalid_inputs_rejected(self):
        for bad in ("", "B0FTFJL6R", "B0FTFJL6RBX", "B0FTF-L6RB", "not an asin", "https://www.amazon.com/", "https://example.com/dp/B0FTFJL6RB"):
            with self.assertRaises(LaunchError, msg=bad):
                L.parse_asin_input(bad, "US")

    def test_url_from_another_marketplace_rejected(self):
        with self.assertRaises(LaunchError) as c:
            L.parse_asin_input("https://www.amazon.de/dp/B0FTFJL6RB", "US")
        self.assertIn("stesso marketplace", str(c.exception))

    def test_status_valid_vs_unverified(self):
        self.assertEqual(L.asin_status("B0FTFJL6RB")[0], "VALIDATO")
        self.assertEqual(L.asin_status("0306406152")[0], "VALIDATO")             # ISBN-10 con checksum valido
        self.assertEqual(L.asin_status("0306406153")[0], "NON VERIFICATO")       # checksum errato
        self.assertEqual(L.asin_status("A1B2C3D4E5")[0], "NON VERIFICATO")
        self.assertTrue(any("NON verificata" in c for c in L.asin_status(ASIN_A)[1]))     # esistenza mai affermata


class PlanTests(unittest.TestCase):
    def test_full_plan_fields(self):
        p, errors, warnings = L.build_plan(form(competitors=[{"asin": ASIN_A, "note": "top seller"}, {"asin": ASIN_B, "note": ""}]), now="2026-10-01T10:00:00")
        self.assertEqual((errors, warnings), ([], []))
        b = p["book"]
        self.assertEqual((b["title"], b["marketplace"], b["currency"], b["price"], b["royalty_per_copy"]), ("Crochet Flowers Made Easy", "US", "USD", 16.99, 7.34))
        self.assertEqual((len(b["kdp_keywords"]), b["categories"]), (3, ["Crafts & Hobbies", "Crochet"]))
        self.assertEqual(p["schema_version"], 1)
        self.assertEqual(p["status"], "DRAFT")
        c = p["competitors"][0]
        self.assertEqual((c["asin"], c["marketplace"], c["source"], c["note"], c["status"], c["enrichment"]),
                         (ASIN_A, "US", "USER_COMPETITOR_ASIN", "top seller", "VALIDATO", None))
        self.assertEqual(json.loads(json.dumps(p)), p)                                         # serializzabile

    def test_zero_competitors_allowed_and_max_five(self):
        self.assertEqual(plan_of()["competitors"], [])
        six = [{"asin": f"B0AAAAAA{i:02d}"} for i in range(6)]
        p, errors, _ = L.build_plan(form(competitors=six))
        self.assertIsNone(p)
        self.assertTrue(any("al massimo 5" in e for e in errors))
        five = [{"asin": f"B0AAAAAA{i:02d}"} for i in range(5)]
        self.assertEqual(len(plan_of(competitors=five)["competitors"]), 5)

    def test_duplicates_and_own_asin_rejected(self):
        p, errors, _ = L.build_plan(form(competitors=[{"asin": ASIN_A}, {"asin": ASIN_A.lower()}]))
        self.assertTrue(any("duplicato" in e for e in errors))
        p, errors, _ = L.build_plan(form(book_id=ASIN_A, competitors=[{"asin": ASIN_A}]))
        self.assertTrue(any("nostro libro" in e for e in errors))

    def test_marketplace_mismatch_rejected(self):
        p, errors, _ = L.build_plan(form(marketplace="UK", currency="", competitors=[{"asin": "https://www.amazon.com/dp/B0FTFJL6RB"}]))
        self.assertIsNone(p)
        self.assertTrue(any("marketplace" in e for e in errors))

    def test_unverified_format_stored_with_warning(self):
        p, errors, warnings = L.build_plan(form(competitors=[{"asin": "A1B2C3D4E5"}]))
        self.assertEqual(errors, [])
        self.assertEqual(p["competitors"][0]["status"], "NON VERIFICATO")
        self.assertTrue(any("NON VERIFICATO" in w for w in warnings))

    def test_validation_errors(self):
        p, errors, _ = L.build_plan(form(title="", price="abc", royalty_per_copy="20", marketplace="x1"))
        self.assertIsNone(p)
        joined = " ".join(errors)
        for needle in ("Titolo", "Prezzo", "Marketplace"):
            self.assertIn(needle, joined)
        _, errors, _ = L.build_plan(form(royalty_per_copy="20"))
        self.assertTrue(any("royalty" in e.lower() and "prezzo" in e.lower() for e in errors))
        _, errors, _ = L.build_plan(form(kdp_keywords=[f"kw {i}" for i in range(8)]))
        self.assertTrue(any("al massimo 7" in e for e in errors))

    def test_keywords_dedupe_and_long_keyword_warning(self):
        p, errors, warnings = L.build_plan(form(kdp_keywords=["Crochet Flowers", "crochet  flowers!", "x" * 60]))
        self.assertEqual((errors, len(p["kdp_keywords"] if "kdp_keywords" in p else p["book"]["kdp_keywords"])), ([], 2))
        self.assertTrue(any("duplicata" in w for w in warnings))
        self.assertTrue(any("50 caratteri" in w for w in warnings))

    def test_update_keeps_id_created_at_and_enrichment(self):
        p = plan_of(competitors=[{"asin": ASIN_A, "note": "a"}])
        p = L.enrich_competitors(p, L.StaticProvider({(ASIN_A, "US"): {"title": "Competitor Title"}}), now="t")
        q, errors, _ = L.build_plan(form(title="New Title", competitors=[{"asin": ASIN_A, "note": "changed"}]), existing=p, now="2026-10-02T10:00:00")
        self.assertEqual((q["launch_id"], q["created_at"], q["updated_at"]), (p["launch_id"], p["created_at"], "2026-10-02T10:00:00"))
        self.assertEqual(q["competitors"][0]["note"], "changed")
        self.assertEqual(q["competitors"][0]["enrichment"]["title"], "Competitor Title")
        self.assertEqual(q["competitors"][0]["added_at"], p["competitors"][0]["added_at"])


class StorageTests(unittest.TestCase):
    def test_save_load_list_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "LAUNCH"
            p = plan_of(competitors=[{"asin": ASIN_A}])
            path = L.save_plan(d, p)
            self.assertTrue(path.is_file())
            self.assertEqual(list(d.glob("*.tmp")), [])                                   # scrittura atomica: nessun temporaneo
            self.assertEqual(L.load_plan(d, p["launch_id"]), p)
            with self.assertRaises(LaunchError):
                L.save_plan(d, p)                                                          # nessuna sovrascrittura silenziosa
            L.save_plan(d, {**p, "updated_at": "later"}, overwrite=True)
            self.assertEqual(L.load_plan(d, p["launch_id"])["updated_at"], "later")
            self.assertEqual([r["launch_id"] for r in L.list_plans(d)], [p["launch_id"]])

    def test_new_launch_id_is_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = []
            for _ in range(3):
                i = L.new_launch_id(tmp, "Crochet Flowers!", "us")
                (Path(tmp) / f"{i}.json").write_text("{}", encoding="utf-8")
                ids.append(i)
            self.assertEqual(len(set(ids)), 3)
            self.assertTrue(ids[0].endswith("_crochet-flowers_US"))

    def test_corrupt_and_unknown_files_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text("{not json", encoding="utf-8")
            (Path(tmp) / "old.json").write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            rows = L.list_plans(tmp)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["error"] for r in rows))
            with self.assertRaises(LaunchError):
                L.load_plan(tmp, "missing")
            with self.assertRaises(LaunchError):
                L.plan_path(tmp, "../evil")
        self.assertEqual(L.list_plans(Path(tempfile.gettempdir()) / "does-not-exist-xyz"), [])


class EvidenceTests(unittest.TestCase):
    def test_own_sources_levels(self):
        p = plan_of()
        e = L.keyword_evidence("crochet flowers", p)               # titolo + sottotitolo? (no) + keyword KDP + descrizione? (no)
        self.assertEqual(e["own_sources"], ["title", "kdp_keywords"])
        self.assertEqual((e["base_level"], e["level"], e["competitor_boost"]), ("MEDIUM", "MEDIUM", False))
        full = plan_of(title="Crochet Flowers", subtitle="Easy crochet flowers", description="crochet flowers for all")
        e = L.keyword_evidence("crochet flowers", full)
        self.assertEqual((e["own_sources"], e["level"]), (["title", "subtitle", "kdp_keywords", "description"], "HIGH"))
        self.assertEqual(L.keyword_evidence("knitting", p)["level"], "NONE")
        self.assertEqual(L.keyword_evidence("flower", p)["own_sources"], ["subtitle", "kdp_keywords", "description"])   # parole intere: nel titolo c'e' solo 'flowers'
        self.assertEqual(L.keyword_evidence("flo", p)["own_sources"], [])

    def test_competitor_boost_only_with_data_and_own_source(self):
        p = plan_of()
        data = {ASIN_A: {"title": "The Best Crochet Flowers Book", "subtitle": "", "categories": ""}}
        e = L.keyword_evidence("crochet flowers", p, data)
        self.assertEqual((e["level"], e["competitor_boost"], e["competitor_hits"]), ("HIGH", True, [ASIN_A]))
        e = L.keyword_evidence("crochet flowers", p, {ASIN_A: {"title": "Knitting"}})
        self.assertEqual((e["level"], e["competitor_boost"]), ("MEDIUM", False))
        self.assertEqual(L.keyword_evidence("knitting", p, {ASIN_A: {"title": "Knitting for all"}})["level"], "NONE")     # nessuna fonte nostra: niente bonus
        full = plan_of(title="Crochet Flowers", subtitle="Easy crochet flowers", description="crochet flowers")
        self.assertEqual(L.keyword_evidence("crochet flowers", full, data)["level"], "VERY_HIGH")

    def test_no_competitor_data_means_no_boost_and_no_invention(self):
        p = plan_of(competitors=[{"asin": ASIN_A}])
        table = L.evidence_table(p)
        self.assertEqual(len(table), 3)
        for e in table:
            self.assertFalse(e["competitor_data_available"])
            self.assertFalse(e["competitor_boost"])
            self.assertEqual(e["competitor_hits"], [])
            self.assertIn("non disponibili", e["note"])

    def test_unverified_competitors_do_not_count(self):
        p = plan_of(competitors=[{"asin": "A1B2C3D4E5"}, {"asin": ASIN_A}])
        p = L.enrich_competitors(p, L.StaticProvider({("A1B2C3D4E5", "US"): {"title": "crochet flowers"}, (ASIN_A, "US"): {"title": "Other Topic"}}))
        self.assertEqual(list(L.competitor_texts(p)), [ASIN_A])
        e = L.evidence_table(p)[0]
        self.assertFalse(e["competitor_boost"])

    def test_evidence_is_pure(self):
        p = plan_of()
        before = json.dumps(p, sort_keys=True)
        L.evidence_table(p)
        L.keyword_evidence("x", p, {ASIN_A: {"title": "x"}})
        self.assertEqual(json.dumps(p, sort_keys=True), before)


class StructureAndProviderTests(unittest.TestCase):
    def test_competitor_campaign_suggestion(self):
        s = L.recommended_structure(plan_of(competitors=[{"asin": ASIN_A}, {"asin": ASIN_B}, {"asin": "A1B2C3D4E5"}]))
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["suggestion"], s[0]["asins"], s[0]["operational"], s[0]["source"]),
                         ("Valuta campagna COMPETITOR", [ASIN_A, ASIN_B], False, "USER_COMPETITOR_ASIN"))
        self.assertEqual(L.recommended_structure(plan_of()), [])                              # nessun competitor
        self.assertEqual(L.recommended_structure(plan_of(competitors=[{"asin": "A1B2C3D4E5"}])), [])   # solo NON VERIFICATO

    def test_provider_interface_null_static_and_no_mutation(self):
        p = plan_of(competitors=[{"asin": ASIN_A}, {"asin": ASIN_B}])
        snap = json.dumps(p, sort_keys=True)
        same = L.enrich_competitors(p, L.NullProvider())
        self.assertTrue(all(c["enrichment"] is None for c in same["competitors"]))            # nessun dato inventato
        rich = L.enrich_competitors(p, L.StaticProvider({(ASIN_A, "US"): {"title": "T", "subtitle": "S", "categories": ["Crafts"], "price": 9.99,
                                                                          "rating": 4.5, "review_count": 120, "junk": "ignored"}}), now="2026-10-01T00:00:00")
        e = rich["competitors"][0]["enrichment"]
        self.assertEqual((e["title"], e["categories"], e["price"], e["provider"]), ("T", ["Crafts"], 9.99, "static"))
        self.assertNotIn("junk", e)
        self.assertIsNone(rich["competitors"][1]["enrichment"])                                # ASIN senza dati: resta None
        self.assertEqual(json.dumps(p, sort_keys=True), snap)                                  # l'originale non cambia
        with self.assertRaises(TypeError):
            L.CompetitorProvider()                                                              # astratta

    def test_marketplace_scoped_provider_lookup(self):
        prov = L.StaticProvider({(ASIN_A, "UK"): {"title": "UK only"}})
        self.assertIsNone(prov.fetch(ASIN_A, "US"))
        self.assertEqual(prov.fetch(ASIN_A, "uk")["title"], "UK only")


class SafetyTests(unittest.TestCase):
    def test_no_network_or_amazon_code(self):
        for p in (ROOT / "SCRIPTS" / "kdp_ads" / "launch.py", ROOT / "DASHBOARD" / "ops" / "launch_ops.py", ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py"):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            imported = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    imported |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imported.add(n.module.split(".")[0])
            self.assertFalse(imported & {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp", "boto3", "selenium", "playwright", "bs4"}, (p.name, imported))
            self.assertNotIn("amazon_ads", p.read_text(encoding="utf-8"))

    def test_url_parsing_makes_no_request(self):
        with mock.patch("socket.socket", side_effect=AssertionError("rete usata")):
            L.parse_asin_input("https://www.amazon.com/dp/B0FTFJL6RB", "US")
            L.enrich_competitors(plan_of(competitors=[{"asin": ASIN_A}]), L.NullProvider())

    def test_no_campaign_created_by_the_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "INPUT").mkdir()
            L.save_plan(root / "LAUNCH", plan_of(competitors=[{"asin": ASIN_A}]))
            self.assertEqual(list((root / "INPUT").iterdir()), [])                              # nessuna cartella campagna/INPUT
            self.assertEqual([p.name for p in root.iterdir()].count("CONFIG"), 0)


class PageTests(unittest.TestCase):
    PAGE = ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py"

    def render(self, root, values=None, submit=False):
        values = values or {}
        st = mock.MagicMock()
        st.session_state = {}
        st.columns.side_effect = lambda n, *a, **k: [mock.MagicMock(**{"text_input.side_effect": lambda label, value="", **kw: values.get(label, value),
                                                                      "selectbox.side_effect": lambda label, options, index=0, **kw: values.get(label, list(options)[index])})
                                                      for _ in range(n if isinstance(n, int) else len(n))]
        st.text_input.side_effect = lambda label, value="", **k: values.get(label, value)
        st.text_area.side_effect = lambda label, value="", **k: values.get(label, value)
        st.selectbox.side_effect = lambda label, options, index=0, **k: values.get(label, list(options)[index])
        st.form_submit_button.return_value = submit
        st.button.return_value = values.get("__button__", False)
        st.file_uploader.return_value = None
        st.stop.side_effect = SystemExit
        old_env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT",)}
        os.environ["KDP_ADS_ROOT"] = str(root)
        old_mod = sys.modules.get("streamlit")
        sys.modules["streamlit"] = st
        for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
            del sys.modules[m]
        try:
            runpy.run_path(str(self.PAGE), run_name="__main__")
        except SystemExit:
            pass
        finally:
            sys.modules.pop("streamlit", None) if old_mod is None else sys.modules.__setitem__("streamlit", old_mod)
            for k, v in old_env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return st

    def test_page_renders_empty_and_saves_locally_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONFIG").mkdir()
            empty = self.render(root)
            self.assertIn("Lancio nuovo libro", str(empty.mock_calls))
            self.assertFalse((root / "LAUNCH").exists())                                        # niente scritto senza invio
            vals = {"Titolo": "Crochet Flowers Made Easy", "Prezzo": "16.99", "Royalty per copia": "7.34", "Keyword 1": "crochet flowers",
                    "ASIN competitor 1": "B0FTFJL6RB", "Nota competitor 1": "da Publisher Rocket"}
            st = self.render(root, vals, submit=True)
            files = list((root / "LAUNCH").glob("*.json"))
            self.assertEqual(len(files), 1)
            plan = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(plan["competitors"][0]["source"], "USER_COMPETITOR_ASIN")
            self.assertEqual(plan["competitors"][0]["status"], "VALIDATO")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["CONFIG", "LAUNCH"])     # niente INPUT/DB/campagne
            self.assertTrue([c for c in st.method_calls if c[0] == "rerun"])                   # dopo il salvataggio: rerun con la bozza selezionata
            st = self.render(root, {"Bozza di lancio": files[0].stem})                        # il rerun mostra la bozza salvata
            txt = str(st.mock_calls)
            for needle in ("COMPETITOR DI RIFERIMENTO", "STRUTTURA CAMPAGNE CONSIGLIATA", "Valuta campagna COMPETITOR", "Fornito dall'utente", "EVIDENZA DELLE KEYWORD", "dati non disponibili"):
                self.assertIn(needle, txt)
            self.assertEqual([c.args[0] for c in st.method_calls if c[0] == "button"], ["GENERA PIANO KEYWORD"])      # unico pulsante: locale

    def test_page_shows_validation_errors_without_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONFIG").mkdir()
            st = self.render(root, {"Titolo": "", "Prezzo": "x"}, submit=True)
            self.assertFalse((root / "LAUNCH").exists())
            self.assertIn("Titolo: campo obbligatorio", str(st.error.mock_calls))

    def test_menu_entry_and_labels(self):
        import ui_labels as U
        self.assertIn(("pages/10_Lancio_nuovo_libro.py", "Lancio nuovo libro"), U.NAV_PAGES)
        self.assertEqual(U.LAUNCH_COMPETITOR_SECTION, "COMPETITOR DI RIFERIMENTO")
        self.assertEqual(U.LAUNCH_COMPETITOR_CAMPAIGN, "Valuta campagna COMPETITOR")
        self.assertEqual(U.label_launch_level("VERY_HIGH"), "Molto alta")
        self.assertEqual(U.label_launch_sources(["title", "kdp_keywords"]), "titolo, keyword KDP")
        self.assertIn("non sono verificate", U.LAUNCH_COMPETITOR_NOTE)


if __name__ == "__main__":
    unittest.main()
