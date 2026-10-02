"""Lancio nuovo libro: ricerca Publisher Rocket (CSV / XLSX / XLS), dry run, import nella bozza, competitor, evidenza keyword, pagina.
Solo workspace temporanei, file generati dai test. Nessuna rete, books.json / DB Ads / INPUT mai toccati."""
import ast
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

from kdp_ads import launch as L  # noqa: E402
from kdp_ads import launch_keywords as K  # noqa: E402
from kdp_ads import launch_research as R  # noqa: E402

ASIN_A, ASIN_B, ASIN_C = "B0FTFJL6RB", "B0GWWX3MBD", "B0G83R8BG4"
KW_HEADER = ["Keyword", "Average Pages", "Number Of Competitors", "Average Price", "Average Monthly Earnings", "Est. Amazon Searches/Month",
             "Amazon Searches/Month Color", "Competitive Score", "Competitive Score Color"]
KW_ROWS = [["crochet flowers", "74", "148", "$ 12", "$ 666", "1,248", "Yellow", "25", "Green"],
           ["crochet bouquet", "71", "149", "$ 11", "$ 476", "<100", "Red", "21", "Green"],
           ["crochet book", "128", "35", "$ 9", "$ 5", "310", "Yellow", "10", "Green"],
           ["iphone case", "51", "541", "$ 10", "$ 9", "99,999", "Green", "3", "Green"]]
CP_HEADER = ["ASIN", "Title", "Author", "Price", "Reviews", "Rating", "BSR", "Categories"]
CP_ROWS = [[ASIN_A, "Crochet Flowers for Beginners", "A. Author", "$ 9.99", "1,204", "4.6", "55,000", "Crafts > Crochet; Hobbies"],
           [ASIN_C, "Crochet Flower Bouquets", "B. Author", "$ 12.5", "88", "4.2", "250,000", "Crafts > Crochet"]]


def csv_bytes(header, rows, delim=","):
    q = lambda c: '"' + str(c).replace('"', '""') + '"'  # noqa: E731
    lines = [delim.join(q(c) for c in header)] + [delim.join(q(c) for c in r) for r in rows]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")


def xlsx_bytes(header, rows, shared=True, sheet_name="Foglio1", title_row=None):
    """XLSX minimo e valido costruito con la sola libreria standard (nessuna dipendenza nei test)."""
    strings, cells_rows = [], []

    def cell(ref, v):
        v = str(v)
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            return f'<c r="{ref}"><v>{v}</v></c>'
        if shared:
            if v not in strings:
                strings.append(v)
            return f'<c r="{ref}" t="s"><v>{strings.index(v)}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
    allrows = ([[title_row]] if title_row else []) + [header] + rows
    for i, r in enumerate(allrows, 1):
        cells_rows.append(f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(r)) + "</row>")
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                       f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                  '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData>' + "".join(cells_rows) + "</sheetData></worksheet>")
        if shared:
            z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
    return buf.getvalue()


import re  # noqa: E402


def form(**over):
    f = {"title": "Crochet Flowers", "subtitle": "Easy projects for every skill level", "description": "Learn to crochet flowers and bouquets.",
         "kdp_keywords": ["crochet flowers", "crochet flower patterns", "crochet book"], "categories": "Books > Crafts, Hobbies & Home > Crochet",
         "marketplace": "US", "currency": "", "price": "16.99", "royalty_per_copy": "7.34", "book_id": "", "competitors": [{"asin": ASIN_A, "note": "scelto"}]}
    f.update(over)
    return f


def plan_of(**over):
    p, errors, _ = L.build_plan(form(**over), now="2026-10-01T10:00:00")
    assert p, errors
    p["launch_id"] = "20261001_crochet_US"
    return p


class ParsingTests(unittest.TestCase):
    def test_valid_csv_keywords_and_metrics(self):
        rec = R.parse_file("kw.csv", csv_bytes(KW_HEADER, KW_ROWS), "US")
        self.assertEqual((rec["kind"], rec["rows"], len(rec["keywords"]), rec["format"]), (R.KIND_KEYWORDS, 4, 4, "CSV"))
        k = {x["keyword"]: x for x in rec["keywords"]}["crochet flowers"]
        self.assertEqual((k["source"], k["source_file"], k["marketplace"]), ("PUBLISHER_ROCKET", "kw.csv", "US"))
        self.assertEqual((k["search_volume"], k["competition"], k["title_count"], k["avg_price"], k["avg_pages"], k["avg_monthly_earnings"]),
                         (1248, 25, 148, 12, 74, 666))
        self.assertEqual((k["searches_color"], k["competition_color"]), ("Yellow", "Green"))
        self.assertIn("search_volume", rec["metrics_present"])
        self.assertEqual(rec["unmapped"], [])

    def test_valid_xlsx_matches_csv_content(self):
        for shared in (True, False):                                                         # stringhe condivise e inline
            rec = R.parse_file("kw.xlsx", xlsx_bytes(KW_HEADER, KW_ROWS, shared=shared), "US")
            ref = R.parse_file("kw.csv", csv_bytes(KW_HEADER, KW_ROWS), "US")
            self.assertEqual((rec["format"], rec["sheet"], rec["rows"]), ("XLSX", "Foglio1", 4))
            strip = lambda r: [{k: v for k, v in x.items() if k != "source_file"} for x in r["keywords"]]  # noqa: E731
            self.assertEqual(strip(rec), strip(ref))

    def test_xlsx_with_title_row_above_header(self):
        rec = R.parse_file("kw.xlsx", xlsx_bytes(KW_HEADER, KW_ROWS, title_row="Esportazione Publisher Rocket"), "US")
        self.assertEqual((rec["header_row"], len(rec["keywords"])), (2, 4))

    def test_xls_without_library_gives_clear_error(self):
        try:
            import xlrd  # noqa: F401
        except ImportError:
            with self.assertRaises(R.ResearchError) as cm:
                R.parse_file("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
            self.assertIn("xlrd", str(cm.exception))
            self.assertIn("XLSX o CSV", str(cm.exception))
        else:
            with self.assertRaises(R.ResearchError):
                R.parse_file("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)

    def test_valid_xls_when_supported(self):
        try:
            import xlrd  # noqa: F401
            import xlwt
        except ImportError:
            self.skipTest("xlrd/xlwt non installati: XLS valido non testabile in questo ambiente")
        wb = xlwt.Workbook()
        ws = wb.add_sheet("S")
        for j, h in enumerate(KW_HEADER):
            ws.write(0, j, h)
        for i, r in enumerate(KW_ROWS, 1):
            for j, v in enumerate(r):
                ws.write(i, j, v)
        buf = io.BytesIO()
        wb.save(buf)
        rec = R.parse_file("kw.xls", buf.getvalue(), "US")
        self.assertEqual((rec["format"], len(rec["keywords"])), ("XLS", 4))

    def test_alias_columns_other_names_and_delimiters(self):
        header = ["Search Term", "Search Volume", "Competition", "Reviews", "Extra Colonna"]
        rec = R.parse_file("x.csv", csv_bytes(header, [["crochet flowers", "500", "12", "3", "valore"]], delim=";"), "US")
        k = rec["keywords"][0]
        self.assertEqual((k["keyword"], k["search_volume"], k["competition"], k["reviews"]), ("crochet flowers", 500, 12, 3))
        self.assertEqual(k["extra"], {"Extra Colonna": "valore"})                              # campo reale non riconosciuto: conservato
        self.assertEqual(rec["unmapped"], ["Extra Colonna"])

    def test_unknown_files_are_rejected_with_clear_errors(self):
        for name, data in (("a.csv", csv_bytes(["Nome", "Colore"], [["x", "y"]])), ("b.csv", b""), ("c.csv", b"\x00\x01\x02" * 300),
                           ("d.xlsx", b"non e' uno zip"), ("e.pdf", b"%PDF-1.4"), ("f.xlsx", xlsx_bytes(["Foo", "Bar"], [["1", "2"]]))):
            with self.assertRaises(R.ResearchError, msg=name):
                R.parse_file(name, data)
        with self.assertRaises(R.ResearchError) as cm:
            R.parse_file("a.csv", csv_bytes(["Nome", "Colore"], [["x", "y"]]))
        self.assertIn("File non riconosciuto", str(cm.exception))

    def test_missing_data_is_never_invented(self):
        rec = R.parse_file("kw.csv", csv_bytes(["Keyword", "Est. Amazon Searches/Month", "Competitive Score"],
                                              [["crochet flowers", "", "7"], ["crochet bouquet", "<100", ""], ["crochet book", "", ""]]), "US")
        by = {k["keyword"]: k for k in rec["keywords"]}
        self.assertNotIn("search_volume", by["crochet flowers"])
        self.assertEqual(by["crochet flowers"]["competition"], 7)
        self.assertEqual((by["crochet bouquet"].get("search_volume"), by["crochet bouquet"]["search_volume_text"]), (None, "<100"))   # "<100" non diventa 100
        self.assertNotIn("competition", by["crochet bouquet"])
        for forbidden in ("search_volume", "competition", "title_count", "avg_price", "avg_reviews"):
            self.assertNotIn(forbidden, by["crochet book"])                                      # nessun valore di ripiego (0, None, stima)
        self.assertNotIn("title_count", rec["metrics_present"])

    def test_competitor_rows_and_numbers(self):
        rec = R.parse_file("cp.csv", csv_bytes(CP_HEADER, CP_ROWS), "US")
        self.assertEqual((rec["kind"], len(rec["competitors"]), len(rec["keywords"])), (R.KIND_COMPETITORS, 2, 0))
        c = rec["competitors"][0]
        self.assertEqual((c["asin"], c["title"], c["price"], c["review_count"], c["rating"], c["bsr"]), (ASIN_A, "Crochet Flowers for Beginners", 9.99, 1204, 4.6, 55000))
        self.assertEqual(c["categories"], ["Crafts > Crochet", "Hobbies"])

    def test_duplicates_and_invalid_asin_are_reported(self):
        rec = R.parse_file("cp.csv", csv_bytes(CP_HEADER, CP_ROWS + [CP_ROWS[0], ["NOT-AN-ASIN", "x", "", "", "", "", "", ""]]), "US")
        self.assertEqual(len(rec["competitors"]), 2)
        self.assertTrue(any("non sono ASIN validi" in w for w in rec["warnings"]))
        rec = R.parse_file("kw.csv", csv_bytes(KW_HEADER, KW_ROWS + [KW_ROWS[0]]), "US")
        self.assertTrue(any("duplicate" in w for w in rec["warnings"]))


class WsBase(unittest.TestCase):
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
        r = self.ops.save_form(self.ws, form())
        self.assertTrue(r.ok, r.errors)
        self.lid = r.data["plan"]["launch_id"]

    def sha_books(self):
        return hashlib.sha256(self.books.read_bytes()).hexdigest()

    def tree(self):
        return {str(p.relative_to(self.root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.root.rglob("*")) if p.is_file()}

    def plan(self):
        return self.ops.get_plan(self.ws, self.lid)

    def kp(self):
        r = self.ops.generate_keyword_plan(self.ws, self.lid)
        self.assertTrue(r.ok, r.errors)
        return r.data["plan"]["keyword_plan"]

    @staticmethod
    def by(kp):
        return {e["keyword"]: e for k in ("exact", "phrase", "discovery", "rejected") for e in kp[k]}


class DryRunAndImportTests(WsBase):
    def test_dry_run_reports_everything_and_writes_nothing(self):
        before = self.tree()
        data = csv_bytes(CP_HEADER + ["Keyword"], [r + ["crochet flowers"] for r in CP_ROWS])
        r = self.ops.research_dry_run(self.ws, self.lid, "mix.csv", data)
        self.assertTrue(r.ok, r.errors)
        rep = r.data
        self.assertEqual((rep["status"], rep["file_name"], rep["format"], rep["rows"], rep["kind"]), ("DRY_RUN_OK", "mix.csv", "CSV", 2, R.KIND_BOTH))
        self.assertEqual((rep["keywords_found"], rep["asins_found"]), (1, 2))
        self.assertEqual(rep["keywords_sample"], ["crochet flowers"])
        self.assertEqual(set(rep["asins_sample"]), {ASIN_A, ASIN_C})
        self.assertIn("price", rep["metrics_present"])
        self.assertEqual({c["header"]: c["canonical"] for c in rep["columns"]}["Reviews"], "reviews")
        self.assertEqual(self.tree(), before)                                                 # dry run: nessuna scrittura
        self.assertFalse((self.root / "LAUNCH" / self.lid).exists())

    def test_unknown_file_is_an_error_and_nothing_is_imported(self):
        before = self.tree()
        r = self.ops.import_research(self.ws, self.lid, "x.csv", csv_bytes(["Nome", "Colore"], [["a", "b"]]))
        self.assertFalse(r.ok)
        self.assertIn("non riconosciuto", " ".join(r.errors).lower())
        self.assertEqual(r.data["status"], "UNRECOGNIZED")
        self.assertEqual(self.tree(), before)
        self.assertNotIn("research", self.plan())

    def test_import_saves_files_inside_the_draft_folder_only(self):
        data = csv_bytes(KW_HEADER, KW_ROWS)
        before_books = self.sha_books()
        r = self.ops.import_research(self.ws, self.lid, "ALL KEYWORD SEARCH - X.csv", data)
        self.assertTrue(r.ok, r.errors)
        rdir = self.root / "LAUNCH" / self.lid / "research"
        names = sorted(p.name for p in rdir.iterdir())
        self.assertEqual(len(names), 2)
        self.assertTrue(any(n.endswith("__ALL KEYWORD SEARCH - X.csv") for n in names))      # originale conservato
        self.assertTrue(any(n.endswith(".normalized.json") for n in names))                    # dati normalizzati
        raw = [p for p in rdir.iterdir() if not p.name.endswith(".json")][0]
        self.assertEqual(raw.read_bytes(), data)
        entry = self.plan()["research"]["files"][0]
        self.assertEqual((entry["file_name"], entry["sha256"], entry["format"], entry["keywords"]), ("ALL KEYWORD SEARCH - X.csv", R.sha256(data), "CSV", 4))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])    # niente INPUT / DATABASE / OUTPUT
        self.assertEqual(self.sha_books(), before_books)
        norm = json.loads((rdir / entry["normalized"]).read_text(encoding="utf-8"))
        self.assertEqual({k["source"] for k in norm["keywords"]}, {"PUBLISHER_ROCKET"})

    def test_same_content_twice_is_skipped_and_other_name_same_content_too(self):
        data = csv_bytes(KW_HEADER, KW_ROWS)
        self.assertTrue(self.ops.import_research(self.ws, self.lid, "a.csv", data).ok)
        before = self.tree()
        r = self.ops.import_research(self.ws, self.lid, "a (1).csv", data)
        self.assertFalse(r.ok)
        self.assertEqual(r.data["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(self.tree(), before)
        self.assertEqual(len(self.plan()["research"]["files"]), 1)

    def test_xlsx_import_works_like_csv(self):
        r = self.ops.import_research(self.ws, self.lid, "kw.xlsx", xlsx_bytes(KW_HEADER, KW_ROWS))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.plan()["research"]["files"][0]["format"], "XLSX")
        self.assertEqual(len(self.ops.load_research(self.ws, self.lid)[0]["keywords"]), 4)

    def test_xls_import_without_library_changes_nothing(self):
        try:
            import xlrd  # noqa: F401
            self.skipTest("xlrd installato: il test del messaggio e' per l'ambiente senza libreria")
        except ImportError:
            pass
        before = self.tree()
        r = self.ops.import_research(self.ws, self.lid, "old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
        self.assertFalse(r.ok)
        self.assertIn("xlrd", " ".join(r.errors))
        self.assertEqual(self.tree(), before)


class CompetitorLinkingTests(WsBase):
    def test_known_asin_enriches_the_existing_competitor(self):
        r = self.ops.import_research(self.ws, self.lid, "cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        self.assertTrue(r.ok, r.errors)
        comps = self.plan()["competitors"]
        self.assertEqual([c["asin"] for c in comps], [ASIN_A])                              # nessun competitor aggiunto
        e = comps[0]["enrichment"]
        self.assertEqual((e["provider"], e["title"], e["price"], e["review_count"], e["rating"]), ("PUBLISHER_ROCKET", "Crochet Flowers for Beginners", 9.99, 1204, 4.6))
        self.assertEqual(e["extra"]["bsr"], 55000)
        self.assertEqual(comps[0]["status"], L.STATUS_VALID)                                # stato invariato
        ov = self.ops.research_overview(self.ws, self.lid)
        self.assertEqual([x["asin"] for x in ov["enriched"]], [ASIN_A])

    def test_unknown_asin_is_only_a_candidate_never_a_competitor(self):
        rep = self.ops.research_dry_run(self.ws, self.lid, "cp.csv", csv_bytes(CP_HEADER, CP_ROWS)).data
        self.assertEqual([c["asin"] for c in rep["competitors_enriched"]], [ASIN_A])
        self.assertEqual([c["asin"] for c in rep["competitor_candidates"]], [ASIN_C])
        self.ops.import_research(self.ws, self.lid, "cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        self.assertEqual([c["asin"] for c in self.plan()["competitors"]], [ASIN_A])          # ASIN_C NON aggiunto
        ov = self.ops.research_overview(self.ws, self.lid)
        self.assertEqual([c["asin"] for c in ov["candidates"]], [ASIN_C])
        self.assertNotIn(ASIN_C, K.build_keyword_plan(self.plan())["campaigns"]["COMPETITOR"]["asins"])    # nessuna campagna COMPETITOR su un candidato

    def test_enrichment_feeds_competitor_confirmation(self):
        self.ops.import_research(self.ws, self.lid, "cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        e = self.by(self.kp())["crochet flowers"]
        self.assertEqual(e["competitor_hits"], [ASIN_A])                                    # titolo competitor reale: conferma esterna


class KeywordEvidenceTests(WsBase):
    def test_research_keyword_gets_bonus_and_metrics_when_relevant(self):
        base = self.by(self.kp())["crochet flowers"]
        self.assertEqual(base["research_bonus"], 0)
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        e = self.by(self.kp())["crochet flowers"]
        self.assertGreaterEqual(e["research_bonus"], 4)
        self.assertGreaterEqual(e["evidence_score"], base["evidence_score"])
        self.assertIn("publisher_rocket", e["source_types"])
        rec = e["research"]["records"][0]
        self.assertEqual((e["research"]["source"], rec["source_file"], rec["search_volume"], rec["competition"]), ("PUBLISHER_ROCKET", "kw.csv", 1248, 25))
        self.assertIn("dati Publisher Rocket", e["reason"])
        self.assertIn("ricerche stimate/mese 1248", e["reason"])

    def test_off_topic_keyword_is_not_promoted_even_with_huge_volume(self):
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        e = self.by(self.kp())["iphone case"]
        self.assertEqual(e["recommended_match_type"], "REJECT")
        self.assertEqual(e["research_bonus"], 0)
        self.assertNotIn("research_data", e["core_topic"]["external_evidence"])
        self.assertIn("NON pertinente", e["reason"])
        self.assertEqual(e["research"]["records"][0]["search_volume"], 99999)                # il dato resta visibile, ma non promuove

    def test_general_keyword_lifted_only_with_confirmation_from_another_source(self):
        by0 = self.by(self.kp())
        self.assertEqual(by0["crochet book"]["recommended_match_type"], "DISCOVERY")          # generica
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        by = self.by(self.kp())
        self.assertEqual(by["crochet book"]["recommended_match_type"], "PHRASE")              # keyword KDP + Publisher Rocket pertinente
        self.assertEqual(by["crochet book"]["core_topic"]["external_evidence"], ["research_data"])
        self.assertNotEqual(by["crochet book"]["recommended_match_type"], "EXACT")

    def test_research_only_general_keyword_without_second_source_is_not_lifted(self):
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(["Keyword", "Est. Amazon Searches/Month"], [["crochet patterns", "5,000"]]))
        e = self.by(self.kp())["crochet patterns"]
        self.assertEqual(e["recommended_match_type"], "DISCOVERY")                             # solo Publisher Rocket: niente promozione
        self.assertEqual(e["core_topic"]["external_evidence"], [])
        self.assertIn("nessuna conferma da altra fonte", e["reason"])

    def test_new_relevant_research_keyword_becomes_a_candidate_with_its_source(self):
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(["Keyword", "Competitive Score"], [["crochet flowers for beginners", "9"]]))
        e = self.by(self.kp())["crochet flowers for beginners"]
        self.assertEqual(e["source_types"], ["publisher_rocket"])
        self.assertIn(e["recommended_match_type"], ("EXACT", "PHRASE"))
        self.assertEqual(e["research"]["records"][0]["competition"], 9)
        self.assertNotIn("search_volume", e["research"]["records"][0])                         # colonna assente: nessun dato inventato

    def test_competitor_supported_keyword_has_more_bonus(self):
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        no_comp = self.by(self.kp())["crochet flowers"]["research_bonus"]
        self.ops.import_research(self.ws, self.lid, "cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        self.assertGreater(self.by(self.kp())["crochet flowers"]["research_bonus"], no_comp)

    def test_plan_without_research_is_unchanged(self):
        kp = self.kp()
        self.assertIsNone(kp["research"])
        for e in self.by(kp).values():
            self.assertIsNone(e["research"])
            self.assertEqual(e["research_bonus"], 0)

    def test_import_makes_the_saved_plan_stale_and_regeneration_uses_research(self):
        self.kp()
        self.assertFalse(K.is_stale(self.plan()))
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        self.assertTrue(K.is_stale(self.plan()))
        kp = self.kp()
        self.assertEqual(kp["research"]["files"], ["kw.csv"])
        self.assertFalse(K.is_stale(self.plan()))

    def test_updating_the_form_keeps_imported_research(self):
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        r = self.ops.save_form(self.ws, form(subtitle="Nuovo"), self.lid)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(len(self.plan()["research"]["files"]), 1)


class SafetyTests(WsBase):
    def test_no_network_and_nothing_else_touched(self):
        before_books = self.sha_books()
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("rete usata")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("rete usata")):
            self.assertTrue(self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS)).ok)
            self.assertTrue(self.ops.import_research(self.ws, self.lid, "kw.xlsx", xlsx_bytes(["Keyword"], [["crochet flower bouquet"]])).ok)
            self.assertTrue(self.ops.generate_keyword_plan(self.ws, self.lid).ok)
        self.assertEqual(self.sha_books(), before_books)                                      # books.json invariato
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])    # nessun DB Ads, INPUT, OUTPUT, LOGS
        self.assertEqual(sorted(p.name for p in (self.root / "CONFIG").iterdir()), ["books.json"])

    def test_module_has_no_network_db_or_input_code(self):
        for p in (ROOT / "SCRIPTS" / "kdp_ads" / "launch_research.py",):
            src = p.read_text(encoding="utf-8")
            imported = set()
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Import):
                    imported |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imported.add(n.module.split(".")[0])
            self.assertFalse(imported & {"requests", "urllib", "urllib3", "http", "socket", "httpx", "sqlite3", "subprocess", "pathlib", "os", "shutil"}, imported)
            code = src.replace(ast.get_docstring(ast.parse(src), clean=False) or "", "")        # il docstring puo' citare cio' che il modulo NON fa
            for forbidden in ("books.json", "INPUT", "write_text", "write_bytes", "amazon_ads"):
                self.assertNotIn(forbidden, code)
            self.assertIsNone(re.search(r"(?<![\w.])open\(", code), "il modulo non apre file: lavora solo su byte in memoria")

    def test_real_project_files_are_not_touched(self):
        real = [ROOT / "CONFIG" / "books.json"] + sorted((ROOT / "INPUT").rglob("*")) if (ROOT / "INPUT").exists() else [ROOT / "CONFIG" / "books.json"]
        db = ROOT / "DATABASE" / "kdp_ads.db"
        snap = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in real + [db] if p.is_file()}  # noqa: E731
        before = snap()
        self.ops.import_research(self.ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS))
        self.ops.generate_keyword_plan(self.ws, self.lid)
        self.assertEqual(snap(), before)


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
        self.books_sha = hashlib.sha256(self.books.read_bytes()).hexdigest()
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        self.addCleanup(os.environ.pop, "KDP_ADS_ROOT", None)
        self.at = AppTest.from_file(str(PAGE), default_timeout=60).run()
        f = lambda label: [x for x in self.at.text_input if x.label == label][0]  # noqa: E731
        f("Titolo").set_value("Crochet Flowers")
        f("Prezzo").set_value("16.99")
        f("Royalty per copia").set_value("7.34")
        f("Keyword 1").set_value("crochet flowers")
        f("ASIN competitor 1").set_value(ASIN_A)
        self.btn("SALVA BOZZA LANCIO").click().run()
        self.lid = sorted((self.root / "LAUNCH").glob("*.json"))[0].stem

    def btn(self, label):
        return [b for b in self.at.button if b.label == label][0]

    def upload(self, name, data):
        self.at.file_uploader[0].upload(name, data, "application/octet-stream")
        self.at.run()

    def texts(self):
        return " | ".join([h.value for h in self.at.header] + [s.value for s in self.at.success] + [w.value for w in self.at.warning]
                          + [e.value for e in self.at.error] + [i.value for i in self.at.info] + [c.value for c in self.at.caption] + [m.value for m in self.at.markdown])

    def test_section_present_and_import_requires_dry_run(self):
        self.assertIn("RICERCA COMPETITOR / PUBLISHER ROCKET", [h.value for h in self.at.header])
        self.assertEqual(list(self.at.file_uploader[0].proto.type), [".csv", ".xlsx", ".xls"])
        self.upload("cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        self.assertTrue(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)                              # prima del dry run: import bloccato
        self.assertFalse((self.root / "LAUNCH" / self.lid).exists())

    def test_full_flow_dry_run_import_enrichment_and_plan(self):
        self.upload("cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        txt = self.texts()
        self.assertIn("Dry run riuscito", txt)
        self.assertIn("Competitor arricchito da Publisher Rocket: " + ASIN_A, txt)
        self.assertIn("Competitor candidato: " + ASIN_C, txt)
        self.assertFalse(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)
        self.assertFalse((self.root / "LAUNCH" / self.lid).exists())                           # il dry run non scrive
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.assertFalse(self.at.exception)
        self.assertTrue(any((self.root / "LAUNCH" / self.lid / "research").glob("*__cp.csv")))
        txt = self.texts()
        self.assertIn("File validi importati nella bozza", txt)
        self.assertIn("File importati nella bozza", txt)
        self.assertIn("Competitor arricchito da Publisher Rocket", txt)
        self.assertIn("Competitor candidato", txt)
        self.btn("GENERA PIANO KEYWORD").click().run()
        self.assertFalse(self.at.exception)
        kp = json.loads((self.root / "LAUNCH" / f"{self.lid}.json").read_text(encoding="utf-8"))["keyword_plan"]
        self.assertTrue(kp["competitor_data_available"])
        self.assertEqual(len(list((self.root / "LAUNCH").glob("*.json"))), 1)                  # nessuna bozza duplicata
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), self.books_sha)

    def test_unknown_file_shows_error_and_blocks_import(self):
        self.upload("x.csv", csv_bytes(["Nome", "Colore"], [["a", "b"]]))
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        row = self.at.dataframe[0].value.iloc[0]
        self.assertEqual(row["Stato"], "NON RICONOSCIUTO")
        self.assertIn("File non riconosciuto", row["Errore"])
        self.assertTrue(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)                  # nessun file valido: import bloccato
        self.assertFalse((self.root / "LAUNCH" / self.lid).exists())

    def test_keyword_table_shows_match_sources_metrics_and_reason(self):
        from ops import launch_ops, workspace
        ws = workspace.from_root(self.root)
        self.assertTrue(launch_ops.import_research(ws, self.lid, "kw.csv", csv_bytes(KW_HEADER, KW_ROWS)).ok)
        self.at = AppTest.from_file(str(PAGE), default_timeout=60).run()
        self.at.selectbox[0].set_value(self.lid).run()
        self.btn("GENERA PIANO KEYWORD").click().run()
        cols = set()
        for df in self.at.dataframe:
            cols |= set(df.value.columns)
        for c in ("Keyword", "Match consigliato", "Confidenza", "Fonti", "Metriche Publisher Rocket", "Competitor a supporto", "Motivo"):
            self.assertIn(c, cols)


# ------------------------------------------------------------------ caricamento MULTIPLO
KW2_ROWS = [["crochet flowers", "70", "150", "$ 11", "$ 500", "1,300", "Yellow", "27", "Green"],         # stesso termine del file 1, valori diversi
            ["crochet flower bouquet", "60", "90", "$ 10", "$ 300", "420", "Yellow", "15", "Green"],
            ["crochet patterns", "80", "2,000", "$ 9", "$ 1,200", "5,000", "Green", "70", "Red"]]
KW3_HEADER = ["Keyword", "Competitive Score"]
KW3_ROWS = [["crochet patterns", "65"], ["flower crochet for beginners", "12"]]


def f1():
    return ("pr1.csv", csv_bytes(KW_HEADER, KW_ROWS))


def f2():
    return ("pr2.xlsx", xlsx_bytes(KW_HEADER, KW2_ROWS))


def f3():
    return ("pr3.csv", csv_bytes(KW3_HEADER, KW3_ROWS))


def bad():
    return ("rotto.csv", csv_bytes(["Nome", "Colore"], [["a", "b"]]))


class BatchTests(WsBase):
    def statuses(self, files):
        r = self.ops.research_batch_dry_run(self.ws, self.lid, files)
        self.assertTrue(r.ok, r.errors)
        return [(x["file_name"], x["status"]) for x in r.data["rows"]], r.data

    def test_three_valid_files_together(self):
        files = [f1(), f2(), f3()]
        st, data = self.statuses(files)
        self.assertEqual(st, [("pr1.csv", "DRY_RUN_OK"), ("pr2.xlsx", "DRY_RUN_OK"), ("pr3.csv", "DRY_RUN_OK")])
        self.assertEqual(data["valid"], 3)
        row = data["rows"][1]
        self.assertEqual((row["format"], row["rows"], row["keywords_found"], row["asins_found"]), ("XLSX", 3, 3, 0))     # una riga per file, con i suoi numeri
        self.assertIn("search_volume", row["metrics"])
        before = self.tree()
        r = self.ops.import_research_batch(self.ws, self.lid, files)
        self.assertTrue(r.ok, r.errors)
        s = r.data["summary"]
        self.assertEqual((s["files_loaded"], s["files_valid"], s["files_imported"], s["duplicates"], s["already_imported"], s["errors"]), (3, 3, 3, 0, 0, 0))
        self.assertEqual(len(self.plan()["research"]["files"]), 3)
        self.assertNotEqual(self.tree(), before)
        stored = sorted(p.name for p in (self.root / "LAUNCH" / self.lid / "research").iterdir())
        self.assertEqual(len(stored), 6)                                                       # 3 originali + 3 normalizzati

    def test_dry_run_of_many_files_writes_nothing(self):
        before = self.tree()
        self.statuses([f1(), f2(), bad()])
        self.assertEqual(self.tree(), before)

    def test_two_valid_one_wrong_does_not_block_the_valid_ones(self):
        files = [f1(), bad(), f2()]
        st, data = self.statuses(files)
        self.assertEqual(st, [("pr1.csv", "DRY_RUN_OK"), ("rotto.csv", "UNRECOGNIZED"), ("pr2.xlsx", "DRY_RUN_OK")])
        self.assertIn("File non riconosciuto", data["rows"][1]["error"])
        r = self.ops.import_research_batch(self.ws, self.lid, files)
        self.assertTrue(r.ok, r.errors)
        s = r.data["summary"]
        self.assertEqual((s["files_loaded"], s["files_valid"], s["files_imported"], s["errors"]), (3, 2, 2, 1))
        self.assertEqual([f["file_name"] for f in self.plan()["research"]["files"]], ["pr1.csv", "pr2.xlsx"])      # il file errato resta escluso
        self.assertFalse(any("rotto" in p.name for p in (self.root / "LAUNCH" / self.lid / "research").iterdir()))

    def test_all_files_wrong_imports_nothing(self):
        before = self.tree()
        r = self.ops.import_research_batch(self.ws, self.lid, [bad(), ("x.pdf", b"%PDF")])
        self.assertFalse(r.ok)
        self.assertEqual(self.tree(), before)
        self.assertEqual(r.data["summary"]["files_imported"], 0)

    def test_duplicate_inside_the_same_batch_is_imported_once(self):
        files = [f1(), ("pr1 (1).csv", f1()[1]), f2()]
        st, _ = self.statuses(files)
        self.assertEqual(st, [("pr1.csv", "DRY_RUN_OK"), ("pr1 (1).csv", "DUPLICATE_IN_BATCH"), ("pr2.xlsx", "DRY_RUN_OK")])
        r = self.ops.import_research_batch(self.ws, self.lid, files)
        s = r.data["summary"]
        self.assertEqual((s["files_loaded"], s["files_valid"], s["files_imported"], s["duplicates"]), (3, 2, 2, 1))
        self.assertEqual(len(self.plan()["research"]["files"]), 2)
        self.assertEqual(len(self.ops.load_research(self.ws, self.lid)), 2)                    # i dati del duplicato non sono contati due volte

    def test_file_already_imported_before_is_not_duplicated(self):
        self.assertTrue(self.ops.import_research(self.ws, self.lid, *f1()).ok)
        before = self.tree()
        files = [("altro nome.csv", f1()[1]), f2()]
        st, _ = self.statuses(files)
        self.assertEqual(st, [("altro nome.csv", "ALREADY_IMPORTED"), ("pr2.xlsx", "DRY_RUN_OK")])
        r = self.ops.import_research_batch(self.ws, self.lid, files)
        s = r.data["summary"]
        self.assertEqual((s["files_imported"], s["already_imported"]), (1, 1))
        self.assertEqual(len(self.plan()["research"]["files"]), 2)
        self.assertNotEqual(self.tree(), before)
        only_dup = self.ops.import_research_batch(self.ws, self.lid, [("ancora.csv", f1()[1])])
        self.assertFalse(only_dup.ok)                                                          # solo un gia' importato: nessuna scrittura
        self.assertEqual(len(self.plan()["research"]["files"]), 2)

    def test_keywords_merge_across_files_without_losing_per_file_data(self):
        self.assertTrue(self.ops.import_research_batch(self.ws, self.lid, [f1(), f2(), f3()]).ok)
        res = R.research_for_plan(self.ops.load_research(self.ws, self.lid))
        self.assertEqual(len(res["crochet flowers"]), 2)                                        # stesso termine in 2 file: 2 record
        self.assertEqual({r["source_file"]: r["search_volume"] for r in res["crochet flowers"]}, {"pr1.csv": 1248, "pr2.xlsx": 1300})
        self.assertEqual(len(res["crochet patterns"]), 2)                                       # pr2 + pr3
        self.assertEqual(len(res["crochet flower bouquet"]), 1)
        ov = self.ops.research_overview(self.ws, self.lid)
        self.assertEqual(ov["keywords_found_total"], 4 + 3 + 2)
        self.assertEqual(ov["keywords_total"], len(res))                                        # uniche
        self.assertEqual(len(res), 4 + 3 + 2 - 2)                                               # 2 termini ripetuti

    def test_batch_summary_counts_total_unique_enriched_and_candidates(self):
        cp = ("cp.csv", csv_bytes(CP_HEADER, CP_ROWS))
        r = self.ops.import_research_batch(self.ws, self.lid, [f1(), f2(), f3(), cp, bad()])
        s = r.data["summary"]
        self.assertEqual((s["files_loaded"], s["files_valid"], s["files_imported"], s["errors"]), (5, 4, 4, 1))
        self.assertEqual((s["keywords_total"], s["keywords_unique"]), (9, 7))
        self.assertEqual(s["competitors_enriched"], [ASIN_A])
        self.assertEqual(s["competitors_candidates"], [ASIN_C])
        self.assertEqual([c["asin"] for c in self.plan()["competitors"]], [ASIN_A])             # il candidato non entra nei competitor

    def test_plan_uses_all_imported_files_together(self):
        self.assertTrue(self.ops.import_research_batch(self.ws, self.lid, [f1(), f2(), f3()]).ok)
        kp = self.ops.generate_keyword_plan(self.ws, self.lid).data["plan"]["keyword_plan"]
        self.assertEqual(kp["research"]["files"], ["pr1.csv", "pr2.xlsx", "pr3.csv"])
        by = self.by(kp)
        e = by["crochet flowers"]
        self.assertEqual(sorted(r["source_file"] for r in e["research"]["records"]), ["pr1.csv", "pr2.xlsx"])      # dati di entrambi i file
        self.assertIn("presente in 2 file", e["reason"])
        self.assertIn("crochet flower bouquet", by)                                             # keyword che esiste solo nel secondo file
        self.assertIn("flower crochet for beginners", by)                                       # ... e nel terzo

    def test_general_keyword_in_two_files_counts_as_more_sources(self):
        self.assertTrue(self.ops.import_research_batch(self.ws, self.lid, [f2(), f3()]).ok)     # "crochet patterns" in 2 file, nessuna fonte propria
        e = self.by(self.kp())["crochet patterns"]
        self.assertEqual(len(e["research"]["records"]), 2)
        self.assertEqual(e["core_topic"]["external_evidence"], ["research_data"])               # piu' fonti: puo' salire (se e' ampia)
        self.assertIn("PHRASE", e["recommended_match_type"])

    def test_partial_failure_of_one_file_keeps_the_others(self):
        real = self.ops.import_research
        calls = []

        def flaky(ws, lid, name, data):
            calls.append(name)
            if name == "pr2.xlsx":
                return self.ops.OpResult(False, ["disco pieno (simulato)"])
            return real(ws, lid, name, data)
        with mock.patch.object(self.ops, "import_research", flaky):
            r = self.ops.import_research_batch(self.ws, self.lid, [f1(), f2(), f3()])
        self.assertTrue(r.ok)
        s = r.data["summary"]
        self.assertEqual((s["files_valid"], s["files_imported"], s["errors"]), (3, 2, 1))
        self.assertEqual([f["file_name"] for f in self.plan()["research"]["files"]], ["pr1.csv", "pr3.csv"])
        self.assertIn("disco pieno", [x for x in s["files"] if x["file_name"] == "pr2.xlsx"][0]["error"])

    def test_batch_no_network_and_project_files_untouched(self):
        before_books = self.sha_books()
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("rete usata")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("rete usata")):
            self.assertTrue(self.ops.import_research_batch(self.ws, self.lid, [f1(), f2(), f3(), bad()]).ok)
            self.assertTrue(self.ops.generate_keyword_plan(self.ws, self.lid).ok)
        self.assertEqual(self.sha_books(), before_books)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])     # nessun DB Ads / INPUT / OUTPUT
        self.assertEqual(sorted(p.name for p in (self.root / "CONFIG").iterdir()), ["books.json"])


@unittest.skipIf(AppTest is None, "streamlit non installato")
class BatchPageTests(PageTests):
    """Pagina vera con piu' file: tabella per file, import dei soli validi, riepilogo e stato che sopravvive ai rerun."""

    test_section_present_and_import_requires_dry_run = None          # ereditati da PageTests solo per setUp/helper: non si rieseguono
    test_full_flow_dry_run_import_enrichment_and_plan = None
    test_unknown_file_shows_error_and_blocks_import = None
    test_keyword_table_shows_match_sources_metrics_and_reason = None

    def upload_many(self, *files):
        for name, data in files:
            self.at.file_uploader[0].upload(name, data, "application/octet-stream")
        self.at.run()

    def table(self):
        return self.at.dataframe[0].value

    def test_multi_upload_accepts_several_files_and_types(self):
        self.assertTrue(self.at.file_uploader[0].accept_multiple_files)
        self.assertEqual(list(self.at.file_uploader[0].proto.type), [".csv", ".xlsx", ".xls"])

    def test_three_files_dry_run_table_import_summary(self):
        self.upload_many(f1(), f2(), f3())
        self.assertTrue(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)                   # prima del dry run: bloccato
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        tb = self.table()
        self.assertEqual(list(tb["File"]), ["pr1.csv", "pr2.xlsx", "pr3.csv"])
        self.assertEqual(list(tb["Stato"]), ["VALIDO"] * 3)
        self.assertEqual(list(tb["Keyword trovate"]), [4, 3, 2])
        for c in ("Formato", "Righe", "ASIN trovati", "Metriche riconosciute", "Errore"):
            self.assertIn(c, tb.columns)
        self.assertFalse((self.root / "LAUNCH" / self.lid).exists())                           # il dry run non scrive
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(len(list((self.root / "LAUNCH" / self.lid / "research").glob("*.normalized.json"))), 3)
        metrics = {m.label: m.value for m in self.at.metric}
        self.assertEqual((metrics["File caricati"], metrics["File validi"], metrics["File importati"], metrics["File con errore"]), ("3", "3", "3", "0"))
        self.assertEqual((metrics["Keyword totali trovate"], metrics["Keyword uniche"]), ("9", "7"))

    def test_two_valid_one_wrong_in_the_page(self):
        self.upload_many(f1(), bad(), f2())
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        tb = self.table()
        self.assertEqual(list(tb["Stato"]), ["VALIDO", "NON RICONOSCIUTO", "VALIDO"])
        self.assertIn("File non riconosciuto", tb.iloc[1]["Errore"])
        self.assertFalse(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)                  # i file validi si possono importare
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        metrics = {m.label: m.value for m in self.at.metric}
        self.assertEqual((metrics["File caricati"], metrics["File validi"], metrics["File importati"], metrics["File con errore"]), ("3", "2", "2", "1"))
        names = [f["file_name"] for f in json.loads((self.root / "LAUNCH" / f"{self.lid}.json").read_text(encoding="utf-8"))["research"]["files"]]
        self.assertEqual(names, ["pr1.csv", "pr2.xlsx"])

    def test_duplicate_and_already_imported_labels(self):
        self.upload_many(f1(), ("copia.csv", f1()[1]), f2())
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.assertEqual(list(self.table()["Stato"]), ["VALIDO", "DUPLICATO", "VALIDO"])
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.upload_many(f1(), f3())                                                           # f1 e' gia' nella bozza
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.assertEqual(list(self.table()["Stato"]), ["GIÀ IMPORTATO", "VALIDO"])
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.assertEqual(len(json.loads((self.root / "LAUNCH" / f"{self.lid}.json").read_text(encoding="utf-8"))["research"]["files"]), 3)

    def test_state_survives_reruns_and_draft_stays_selected(self):
        self.upload_many(f1(), f2(), bad())
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        for _ in range(3):                                                                     # rerun senza clic
            self.at.run()
            self.assertEqual([s for s in self.at.selectbox if s.label == "Bozza di lancio"][0].value, self.lid)
            self.assertEqual(list(self.table()["Stato"]), ["VALIDO", "VALIDO", "NON RICONOSCIUTO"])   # risultati del dry run conservati
            self.assertFalse(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        for _ in range(2):
            self.at.run()
            self.assertEqual([s for s in self.at.selectbox if s.label == "Bozza di lancio"][0].value, self.lid)
            self.assertEqual({m.label: m.value for m in self.at.metric}["File importati"], "2")    # riepilogo conservato
        self.assertEqual(self.at.session_state["current_launch_id"], self.lid)
        self.assertEqual(len(list((self.root / "LAUNCH").glob("*.json"))), 1)

    def test_changing_the_files_invalidates_the_old_dry_run(self):
        self.upload_many(f1())
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.assertFalse(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)
        self.at.file_uploader[0].upload(*f2())                                                  # aggiungo un file: va rifatto il dry run
        self.at.run()
        self.assertTrue(self.btn("IMPORTA FILE VALIDI NELLA BOZZA").disabled)

    def test_plan_generated_from_all_files_after_batch_import(self):
        self.upload_many(f1(), f2(), f3())
        self.btn("ANALIZZA TUTTI / DRY RUN").click().run()
        self.btn("IMPORTA FILE VALIDI NELLA BOZZA").click().run()
        self.btn("GENERA PIANO KEYWORD").click().run()
        self.assertFalse(self.at.exception)
        kp = json.loads((self.root / "LAUNCH" / f"{self.lid}.json").read_text(encoding="utf-8"))["keyword_plan"]
        self.assertEqual(kp["research"]["files"], ["pr1.csv", "pr2.xlsx", "pr3.csv"])
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), self.books_sha)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CONFIG", "LAUNCH"])


if __name__ == "__main__":
    unittest.main()
