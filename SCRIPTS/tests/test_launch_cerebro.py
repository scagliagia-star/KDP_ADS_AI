"""LANCIO NUOVO LIBRO + HELIUM 10 CEREBRO: la pagina 10 carica Cerebro (e Rocket) con riconoscimento automatico, usa il profilo della BOZZA per la pertinenza e mostra P1/P2/P3 con la STESSA
logica (keyword_research) della pagina «Ricerca keyword / competitor». Workspace temporaneo: mai il DB reale, mai books.json reale, mai la cartella LAUNCH reale, nessuna rete, nessuna Amazon."""
import hashlib
import os
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

import test_keyword_research as TK
from kdp_ads import cerebro as CB
from kdp_ads import competitor as C
from kdp_ads import keyword_research as KR
from ops import launch_ops, workspace
from test_launch_research import xlsx_bytes

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None

PAGE = str(ROOT / "DASHBOARD" / "pages" / "10_Lancio_nuovo_libro.py")
DOWNLOADS = Path("C:/Users/UTENTE/Downloads")
REAL_DRAFT = ROOT / "LAUNCH" / "20261105_short-stories-for-seniors_US.json"
PRIO = ("P1", "P2", "P3")


@unittest.skipIf(AppTest is None, "streamlit non installato")
class LaunchPageBase(unittest.TestCase):
    TITLE = "Short Stories for Seniors"
    SUB = "Large Print Cozy Mystery Collection"
    DESC = "Golden girls style stories to read in one sitting"
    KDP = ["short stories for older adults", "large print stories for elderly"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "CONFIG").mkdir()
        self.books = self.root / "CONFIG" / "books.json"
        self.books.write_text("[]", encoding="utf-8")
        self.books_sha = hashlib.sha256(self.books.read_bytes()).hexdigest()
        self.env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        os.environ.pop("KDP_ADS_DB", None)
        self.addCleanup(self.restore_env)
        self.at = AppTest.from_file(PAGE, default_timeout=120).run()
        f = lambda label: [x for x in self.at.text_input if x.label == label][0]  # noqa: E731
        f("Titolo").set_value(self.TITLE)
        f("Sottotitolo").set_value(self.SUB)
        [x for x in self.at.text_area if x.label == "Descrizione"][0].set_value(self.DESC)
        f("Prezzo").set_value("9.99")
        f("Royalty per copia").set_value("3.50")
        for i, k in enumerate(self.KDP):
            f(f"Keyword {i + 1}").set_value(k)
        [x for x in self.at.text_area if x.label.startswith("Categorie")][0].set_value("FICTION / Short Stories")
        self.btn("SALVA BOZZA LANCIO").click().run()
        self.lid = sorted((self.root / "LAUNCH").glob("*.json"))[0].stem
        self.ws = workspace.from_root(self.root)

    def restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def btn(self, label):
        return [b for b in self.at.button if b.label == label][0]

    def upload(self, files):
        up = self.at.file_uploader[0]
        up.set_value([(n, d, "application/octet-stream") for n, d in files])                       # piu' file insieme, come l'utente
        self.at.run()

    def texts(self):
        return " | ".join([h.value for h in self.at.header] + [h.value for h in self.at.subheader] + [s.value for s in self.at.success] + [w.value for w in self.at.warning]
                          + [e.value for e in self.at.error] + [i.value for i in self.at.info] + [c.value for c in self.at.caption] + [m.value for m in self.at.markdown])

    def import_via_page(self, files):
        self.upload(files)
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

    def import_via_ops(self, files):
        for n, d in files:
            r = launch_ops.import_research(self.ws, self.lid, n, d)
            self.assertTrue(r.ok, r.errors)

    @staticmethod
    def cfiles(ids=None):
        return [(TK.cname(c), TK.c26(rows)) for c, rows in TK.COMPETITORS.items() if not ids or c in ids]


class CerebroOnLaunchPageTests(LaunchPageBase):
    def test_section_is_renamed_and_the_uploader_accepts_rocket_and_cerebro(self):
        self.assertIn("RICERCA KEYWORD PER IL LANCIO", [h.value for h in self.at.header])
        self.assertIn("Publisher Rocket + Helium 10 Cerebro", [h.value for h in self.at.subheader])
        self.assertEqual(list(self.at.file_uploader[0].proto.type), [".csv", ".xlsx", ".xls"])
        self.assertIn("Cerebro (CSV", " ".join(c.value for c in self.at.caption))

    def test_cerebro_file_is_recognised_in_the_dry_run_with_a_check_line(self):
        self.upload(self.cfiles(["B0C87HNVKW"]))
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertIn("✓ Helium 10 Cerebro · Competitor: B0C87HNVKW · 4 keyword", self.texts())
        self.assertFalse((self.root / "LAUNCH" / self.lid / "research").exists())                  # il dry run non scrive nulla

    def test_multi_file_cerebro_through_the_page_then_analysis_with_the_draft_profile(self):
        self.import_via_page(self.cfiles())
        files = launch_ops.get_plan(self.ws, self.lid)["research"]["files"]
        self.assertEqual(len(files), 4)
        self.assertTrue(all(f["source"] == "HELIUM10_CEREBRO" and f["competitor_id"] for f in files))
        self.assertEqual(sorted(f["competitor_id"] for f in files), sorted(TK.COMPETITORS))
        t = self.texts()
        self.assertIn("4 competitor Cerebro", t)
        self.assertIn("keyword uniche", t)
        self.assertIn("Profilo del libro usato (dalla bozza): titolo, sottotitolo, descrizione, keyword KDP, categorie", t)      # nessun dato reinserito a mano
        self.assertIn("PIANO PHRASE-first", t)
        labels = [tab.label for tab in self.at.tabs]
        self.assertTrue(any(l.startswith("P1 CORE") for l in labels) and any(l.startswith("P2 STRONG") for l in labels) and any(l.startswith("P3 TEST") for l in labels), labels)
        self.assertTrue(any(l.startswith("FUORI INTENTO / NON PRIORITARIE") for l in labels))

    def test_profile_comes_from_the_draft_automatically(self):
        self.import_via_ops(self.cfiles())
        an = launch_ops.research_analysis(self.ws, self.lid)
        self.assertTrue(an["has_profile"])
        self.assertEqual(an["profile_fields"], ["titolo", "sottotitolo", "descrizione", "keyword KDP", "categorie"])
        rows = {r["keyword_norm"]: r for r in an["rows"]}
        self.assertIsNotNone(rows["short stories for seniors"]["relevance"])
        self.assertEqual(rows["kids coloring book"]["priority"], "FUORI INTENTO")                    # intento diverso dal libro della bozza
        manual = launch_ops.research_analysis(self.ws, self.lid, "kids coloring")                     # integrazione FACOLTATIVA: sposta il profilo, non e' un requisito
        self.assertNotEqual(manual["rows"], an["rows"])

    def test_rocket_plus_cerebro_in_the_same_launch_session_gives_one_row_per_keyword(self):
        self.import_via_ops(self.cfiles() + [TK.rocket([("Short Stories for Seniors", 1500, 60, 400), ("only rocket keyword", 900, 20, 50)])])
        an = launch_ops.research_analysis(self.ws, self.lid)
        rows = [r for r in an["rows"] if r["keyword_norm"] == "short stories for seniors"]
        self.assertEqual(len(rows), 1)                                                                # una sola riga per keyword
        r = rows[0]
        self.assertEqual(r["source_label"], "ROCKET + CEREBRO")
        self.assertEqual((r["cerebro_search_volume"], r["rocket_search_volume"]), (5200, 1500))        # nessuna sostituzione tra misure diverse
        self.assertEqual(len({x["keyword_norm"] for x in an["rows"]}), len(an["rows"]))
        self.assertEqual(an["summary"]["rocket_files"], 1)
        self.assertEqual(an["summary"]["both_sources"], 1)
        self.assertEqual([f["format"] for f in launch_ops.get_plan(self.ws, self.lid)["research"]["files"]].count("HELIUM10_CEREBRO"), 4)

    def test_rocket_engine_of_the_draft_is_unchanged_by_cerebro_files(self):
        self.import_via_ops(self.cfiles())
        self.assertEqual(launch_ops.load_research(self.ws, self.lid), [])                            # il motore Rocket (piano V2) non vede i file Cerebro
        self.assertEqual(len(launch_ops.load_cerebro(self.ws, self.lid)), 4)
        r = launch_ops.generate_keyword_plan(self.ws, self.lid)
        self.assertTrue(r.ok, r.errors)

    def test_priorities_are_coherent_between_launch_page_and_research_page(self):
        self.import_via_ops(self.cfiles())
        an = launch_ops.research_analysis(self.ws, self.lid)
        plan = launch_ops.get_plan(self.ws, self.lid)
        profile = KR.profile_from_plan(plan)
        other = KR.build(C.aggregate(KR.cerebro_sources(TK.parsed_all())), [], profile)               # stessa logica, aggregato costruito come nella pagina 12
        self.assertEqual([(r["keyword_norm"], r["priority"], r["score"]) for r in an["rows"]], [(r["keyword_norm"], r["priority"], r["score"]) for r in other["rows"]])
        self.assertEqual(an["plan"], other["plan"])

    def test_phrase_first_and_top10_ranks_are_valued(self):
        self.import_via_ops(self.cfiles())
        an = launch_ops.research_analysis(self.ws, self.lid)
        self.assertTrue(an["plan"])
        self.assertTrue(all(i["launch_type"] in ("PHRASE CORE", "PHRASE TEST", "CANDIDATA EXACT — EVIDENZA MOLTO FORTE") for i in an["plan"]))
        self.assertTrue(all(r["recommended_match"] == "PHRASE" for r in an["rows"] if r["priority"] in PRIO))
        top = {r["keyword_norm"]: r for r in an["rows"]}["short stories for seniors"]
        self.assertEqual((top["best_organic_rank"], top["best_sponsored_rank"]), (1, 2))
        self.assertIn("ORGANIC TOP 10", top["badges"])
        self.assertIn("SPONSORED TOP 10", top["badges"])
        self.assertGreater(top["top10_bonus"], 0)
        self.assertGreaterEqual(an["top10_count"], 1)
        self.assertEqual(top["priority"], "P1")

    def test_zero_search_volume_keyword_is_not_eliminated(self):
        files = [(TK.cname(f"B0AAAAAA0{i}"), TK.c26([TK.kw("short stories for elderly", 0, 1, 1), TK.kw(f"filler keyword {i}", 10, 30)])) for i in range(5)]
        self.import_via_ops(files)
        r = {x["keyword_norm"]: x for x in launch_ops.research_analysis(self.ws, self.lid)["rows"]}["short stories for elderly"]
        self.assertEqual((r["competitor_share"], r["cerebro_search_volume"], r["best_organic_rank"]), ("5/5", 0, 1))
        self.assertIn(r["priority"], PRIO)                                                            # volume 0 non e' un motivo di esclusione

    def test_cerebro_xlsx_is_recognised_but_explicitly_not_importable(self):
        head = ["Keyword Phrase", "Search Volume", "Organic Rank", "Cerebro IQ Score"]
        data = xlsx_bytes(head, [["short stories for seniors", "100", "3", "7"]])
        name = "US_AMAZON_cerebro_B0C87HNVKW_2026-11-05.xlsx"
        self.assertEqual(KR.detect_format(name, data)["format"], "HELIUM10_CEREBRO")
        self.upload([(name, data)])
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        t = self.texts()
        self.assertIn("Export Cerebro riconosciuto, ma al momento usa il formato CSV.", t)
        self.assertNotIn("✓ Helium 10 Cerebro", t)                                                    # mai presentato come supportato
        self.assertTrue(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)

    def test_one_file_per_competitor_and_unknown_files_do_not_block_the_others(self):
        good = self.cfiles(["B0C87HNVKW"])[0]
        self.import_via_ops([good])
        again = launch_ops.research_dry_run(self.ws, self.lid, "US_AMAZON_cerebro_B0C87HNVKW_2026-12-01.csv", TK.c26(TK.COMPETITORS["B0C87HNVKW"][:2]))
        self.assertFalse(again.ok)
        self.assertTrue(any("gia' nella bozza" in e for e in again.errors))
        partial = launch_ops.research_dry_run(self.ws, self.lid, "x.csv", b"Keyword Phrase,Search Volume,Cerebro IQ Score\nmystery books,100,7\n")
        self.assertFalse(partial.ok)
        self.assertEqual(partial.data["status"], "UNRECOGNIZED")
        self.assertTrue(any("organic_rank" in e and "Keyword Phrase" in e for e in partial.errors))

    def test_nothing_is_written_to_amazon_or_ads_data_and_books_json_is_untouched(self):
        self.import_via_page(self.cfiles())
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), self.books_sha)
        self.assertFalse((self.root / "DATABASE").exists() and any((self.root / "DATABASE").glob("*.db")))      # la pagina Lancio non apre nemmeno il DB Ads
        written = sorted(p.name for p in (self.root / "LAUNCH").rglob("*") if p.is_file())
        self.assertTrue(all(n.endswith((".json", ".csv")) for n in written), written)                   # solo la bozza e i file di ricerca locali
        for mod in ("decision.py", "analysis.py", "opportunities.py", "escalation.py", "manual_actions.py"):
            self.assertNotIn("keyword_research", (ROOT / "SCRIPTS" / "kdp_ads" / mod).read_text(encoding="utf-8"), mod)


class OnePrimaryPlanTests(LaunchPageBase):
    """UN SOLO piano keyword principale: con file di ricerca e' quello basato sui dati competitor; il piano dalla sola bozza e' un fallback, mai un secondo piano equivalente."""
    BASE_BTN, OLD_BTN, SHOW = "GENERA PIANO BASE DALLA BOZZA", "GENERA PIANO KEYWORD", "Vedi anche piano base"

    def buttons(self):
        return [b.label for b in self.at.button]

    def primary_headers(self):
        return [h.value for h in list(self.at.header) + list(self.at.subheader) if "PIANO KEYWORD BASATO SU DATI COMPETITOR" in h.value]

    def show_checkbox(self):
        return [c for c in self.at.checkbox if c.label.startswith(self.SHOW)]

    def test_without_research_the_base_plan_from_the_draft_is_available(self):
        self.assertIn(self.BASE_BTN, self.buttons())
        self.assertNotIn(self.OLD_BTN, self.buttons())
        self.assertEqual(self.show_checkbox(), [])                                    # nessuna scelta ambigua: e' l'unico piano
        self.assertEqual(self.primary_headers(), [])
        t = self.texts()
        self.assertIn("Piano iniziale ricavato solo da titolo, sottotitolo, descrizione, keyword KDP e categorie. Non utilizza dati competitor o volumi di ricerca.", t)
        self.btn(self.BASE_BTN).click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertIn("keyword_plan", launch_ops.get_plan(self.ws, self.lid))         # il fallback continua a funzionare

    def test_with_cerebro_the_new_plan_is_the_main_one_and_the_base_plan_is_a_fallback(self):
        self.import_via_page(self.cfiles())
        self.assertEqual(len(self.primary_headers()), 1)
        self.assertNotIn(self.BASE_BTN, self.buttons())                               # il piano base non e' proposto come alternativa principale
        self.assertNotIn(self.OLD_BTN, self.buttons())
        cb = self.show_checkbox()
        self.assertEqual(len(cb), 1)
        self.assertFalse(cb[0].value)                                                 # chiuso di default
        self.assertIn("PRINCIPALE", " ".join(i.value for i in self.at.info))
        self.assertTrue(any(t.label.startswith("P1 CORE") for t in self.at.tabs))
        cb[0].check().run()
        self.assertIn(self.BASE_BTN, self.buttons())                                  # accessibile, ma esplicitamente secondario

    def test_with_rocket_only_the_research_plan_is_the_main_one(self):
        self.import_via_page([TK.rocket([("short stories for seniors", 1500, 60, 400), ("large print mystery stories", 800, 30, 90), ("kids coloring book", 5000, 80, 900)])])
        self.assertEqual(len(self.primary_headers()), 1)
        self.assertNotIn(self.BASE_BTN, self.buttons())
        an = launch_ops.research_analysis(self.ws, self.lid)
        self.assertEqual((an["summary"]["cerebro_competitors"], an["summary"]["rocket_files"]), (0, 1))
        self.assertTrue(any(t.label.startswith("P1 CORE") for t in self.at.tabs))

    def test_rocket_plus_cerebro_is_one_single_research_plan(self):
        self.import_via_page(self.cfiles() + [TK.rocket([("Short Stories for Seniors", 1500, 60, 400)])])
        self.assertEqual(len(self.primary_headers()), 1)
        self.assertEqual(len([t for t in self.at.tabs if t.label.startswith("P1 CORE")]), 1)       # un solo gruppo di tab P1/P2/P3
        self.assertEqual(len([b for b in self.buttons() if b.startswith("GENERA PIANO")]), 0)      # nessun secondo comando di generazione in vista
        self.show_checkbox()[0].check().run()
        self.assertEqual([b for b in self.buttons() if b.startswith("GENERA PIANO")], [self.BASE_BTN])
        self.assertEqual(launch_ops.research_analysis(self.ws, self.lid)["summary"]["both_sources"], 1)

    def test_candidata_exact_label_replaces_the_old_one_and_logic_is_unchanged(self):
        self.import_via_ops(self.cfiles())
        an = launch_ops.research_analysis(self.ws, self.lid)
        kinds = {r["launch_type"] for r in an["rows"] if r["launch_type"]}
        self.assertTrue(kinds <= {"PHRASE CORE", "PHRASE TEST", "CANDIDATA EXACT — EVIDENZA MOLTO FORTE"}, kinds)
        self.assertEqual(KR.LAUNCH_EXACT, "CANDIDATA EXACT — EVIDENZA MOLTO FORTE")
        cfg = KR.config()
        strong = {"competitor_count": 5, "best_organic_rank": 1, "best_sponsored_rank": 1, "search_volume_used": 5000, "priority": "P1"}
        self.assertEqual(KR._launch_type(strong, 0.9, cfg), KR.LAUNCH_EXACT)             # stesse soglie di prima
        self.assertEqual(KR._launch_type({**strong, "competitor_count": 3}, 0.9, cfg), "PHRASE CORE")
        self.assertTrue(all(r["recommended_match"] == "PHRASE" for r in an["rows"] if r["priority"] in PRIO))   # PHRASE-first invariato
        self.assertNotIn("EXACT solo con evidenza molto forte", self.texts())


# ------------------------------------------------------------------ i 5 file reali + il profilo REALE della bozza (copiata in un workspace temporaneo)
REAL = {"B0C87HNVKW": 315, "8409903091": 272, "B0FGT8LTXQ": 1289, "B0GFW8236T": 702, "B0GR64286Q": 214}


@unittest.skipUnless(AppTest is not None and REAL_DRAFT.is_file() and all((DOWNLOADS / TK.cname(c)).is_file() for c in REAL), "bozza reale o file Cerebro reali non presenti")
class RealLaunchWorkflowTests(unittest.TestCase):
    def test_five_real_cerebro_files_in_the_launch_workflow(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONFIG").mkdir()
            (root / "CONFIG" / "books.json").write_text("[]", encoding="utf-8")
            (root / "LAUNCH").mkdir()
            shutil.copy(REAL_DRAFT, root / "LAUNCH" / REAL_DRAFT.name)                              # COPIA: la bozza reale non viene toccata
            before = hashlib.sha256(REAL_DRAFT.read_bytes()).hexdigest()
            ws = workspace.from_root(root)
            lid = REAL_DRAFT.stem
            for c in REAL:
                n = TK.cname(c)
                r = launch_ops.import_research(ws, lid, n, (DOWNLOADS / n).read_bytes())
                self.assertTrue(r.ok, r.errors)
            an = launch_ops.research_analysis(ws, lid)
            self.assertEqual({f["competitor"]: f["keywords"] for f in an["cerebro_files"]}, REAL)
            s = an["summary"]
            self.assertEqual(s["unique_keywords"], 2069)
            self.assertEqual(s["by_competitor_count"], {5: 33, 4: 34, 3: 91, 2: 307, 1: 1604})
            self.assertEqual((s["at_least_2"], s["at_least_3"]), (465, 158))
            self.assertEqual(an["profile_fields"], ["titolo", "sottotitolo", "descrizione", "keyword KDP", "categorie"])
            rows = {r["keyword_norm"]: r for r in an["rows"]}
            top = rows["short stories for seniors"]
            self.assertEqual((top["competitor_share"], top["cerebro_search_volume"], top["best_organic_rank"], top["best_sponsored_rank"]), ("5/5", 2649, 2, 2))
            self.assertEqual(top["priority"], "P1")
            eld = rows["short stories for elderly"]
            self.assertEqual((eld["competitor_share"], eld["cerebro_search_volume"], eld["best_organic_rank"]), ("5/5", 0, 1))
            self.assertIn(eld["priority"], ("P1", "P2", "P3"))                                        # volume 0: NON eliminata
            p12 = [r for r in an["rows"] if r["priority"] in ("P1", "P2")]
            self.assertGreaterEqual(len(p12), 20)
            self.assertTrue(all(r["recommended_match"] == "PHRASE" for r in p12))
            self.assertEqual(hashlib.sha256(REAL_DRAFT.read_bytes()).hexdigest(), before)
            bp = s["by_priority"]
            self.assertEqual((bp["P1"], bp["P2"], bp["P3"], bp["NON PRIORITARIA"] + bp["FUORI INTENTO"]), (33, 388, 848, 800))      # ranking invariato

    def test_real_workflow_through_the_launch_page(self):
        import shutil
        from ops import launch_ops as LO
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CONFIG").mkdir()
            (root / "CONFIG" / "books.json").write_text("[]", encoding="utf-8")
            (root / "LAUNCH").mkdir()
            shutil.copy(REAL_DRAFT, root / "LAUNCH" / REAL_DRAFT.name)
            env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
            os.environ["KDP_ADS_ROOT"] = str(root)
            os.environ.pop("KDP_ADS_DB", None)
            try:
                at = AppTest.from_file(PAGE, default_timeout=240).run()
                at.selectbox(key="kb_launch_pick").select(REAL_DRAFT.stem).run()
                self.assertIn("GENERA PIANO BASE DALLA BOZZA", [b.label for b in at.button])         # senza ricerca: piano base disponibile
                at.file_uploader[0].set_value([(TK.cname(c), (DOWNLOADS / TK.cname(c)).read_bytes(), "text/csv") for c in REAL])
                at.run()
                [b for b in at.button if b.label == "ANALIZZA TUTTI / DRY RUN"][0].click().run()
                [b for b in at.button if b.label == "IMPORTA FILE VALIDI NELLA BOZZA"][0].click().run()
                self.assertFalse(at.exception, at.exception)
                allt = " | ".join([h.value for h in list(at.header) + list(at.subheader)] + [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info])
                self.assertIn("PIANO KEYWORD BASATO SU DATI COMPETITOR", allt)
                self.assertIn("5 competitor Cerebro", allt)
                self.assertIn("2069 keyword uniche", allt)
                self.assertEqual([t.label for t in at.tabs], ["P1 CORE (33)", "P2 STRONG (388)", "P3 TEST (848)", "FUORI INTENTO / NON PRIORITARIE (800)"])
                labels = [b.label for b in at.button]
                self.assertEqual([b for b in labels if b.startswith("GENERA PIANO")], [])            # nessun secondo piano concorrente
                self.assertNotIn("GENERA PIANO KEYWORD", labels)
                self.assertTrue(any(c.label.startswith("Vedi anche piano base") for c in at.checkbox))
                top = {r["keyword_norm"]: r for r in LO.research_analysis(workspace.from_root(root), REAL_DRAFT.stem)["rows"]}
                self.assertEqual((top["short stories for seniors"]["cerebro_search_volume"], top["short stories for seniors"]["best_organic_rank"]), (2649, 2))
                self.assertEqual(top["short stories for elderly"]["cerebro_search_volume"], 0)
            finally:
                for k, v in env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
