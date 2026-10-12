"""RICERCA COMPETITOR: pagina, servizi della dashboard, integrazione con Suggerimenti di crescita, nessuna regressione su Amazon Ads / books.json / PyArrow."""
import hashlib
import json
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

import pandas as pd
import pyarrow as pa

import test_competitor_research as T
import test_overview as tov
from components import competitor_ui as CU
from kdp_ads import competitor_store as S
from kdp_ads.db import connect
from ops import competitor_ops as ops
from ops import workspace

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

PAGE = ROOT / "DASHBOARD" / "pages" / "12_Ricerca_competitor.py"
GROWTH = ROOT / "DASHBOARD" / "pages" / "11_Suggerimenti_di_crescita.py"
REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"
BOOKS = {"books": [{"book_id": "B0HLL1Y1CN", "title": "Twenty Women, Twenty Mysteries", "active": True,
                    "marketplaces": [{"marketplace": "US", "currency": "USD", "price": 14.99, "royalty_per_copy": 5.0, "target_acos": 35.0,
                                      "phase": "PROFIT_STABLE", "active": True, "break_even_acos": 33.36}]}]}


def mixed_columns(rows):
    out = []
    for k in rows[0]:
        kinds = {isinstance(r[k], str) for r in rows if r[k] is not None}
        if kinds == {True, False}:
            out.append(k)
    return out


def make_root(tmp):
    root = Path(tmp)
    (root / "CONFIG").mkdir()
    (root / "CONFIG" / "books.json").write_text(json.dumps(BOOKS), encoding="utf-8")
    (root / "DATABASE").mkdir()
    return root


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(self.tmp.name)
        self.ws = workspace.from_root(self.root)
        c = connect(self.ws.db_path)
        c.execute("INSERT INTO books (book_id,title,marketplace) VALUES ('B0HLL1Y1CN','Twenty Women, Twenty Mysteries','US')")
        c.commit()
        c.close()
        self.files = [(T.fname(a), T.csv_bytes(r)) for a, r in ((T.A1, T.F1), (T.A2, T.F2), (T.A3, T.F3))]
        self.books_hash = hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def test_book_choices_come_from_books_json_read_only(self):
        self.assertEqual(ops.book_choices(self.ws), [{"book_id": "B0HLL1Y1CN", "title": "Twenty Women, Twenty Mysteries", "marketplaces": ["US"]}])

    def test_import_three_files_with_book_end_to_end(self):
        r = ops.import_research(self.ws, "B0HLL1Y1CN", "US", None, self.files, book_title="Twenty Women, Twenty Mysteries")
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["imported"], 3)
        a = ops.analyze(self.ws, r.data["research_id"])
        self.assertEqual((a["overview"]["total"], a["overview"]["all"]), (3, 4))
        self.assertEqual(len(a["saved"]), 3)                                            # opportunita' salvate DA_APPROFONDIRE
        self.assertTrue(all(o["status"] == "DA_APPROFONDIRE" for o in a["saved"].values()))
        self.assertEqual(len(a["raw"]), sum(len({x["Keyword Phrase"].lower() for x in rows}) for rows in (T.F1, T.F2, T.F3)))
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        self.assertEqual(len(list((self.root / "INPUT").glob("*"))) if (self.root / "INPUT").exists() else 0, 0)          # nessun file in INPUT

    def test_import_without_book_and_manual_asin_for_unrecognised_filename(self):
        files = [("export_competitor.csv", T.csv_bytes(T.F1)), self.files[1]]
        miss = ops.plan_files(files)
        self.assertTrue(miss[0]["needs_asin"] and not miss[1]["needs_asin"])
        bad = ops.import_research(self.ws, None, "US", "mia ricerca", files)
        self.assertTrue(bad.ok)                                                          # il file senza ASIN non blocca l'altro
        self.assertEqual(bad.data["imported"], 1)
        self.assertTrue(any("ASIN competitor mancante" in e for e in bad.errors))
        ok = ops.import_research(self.ws, None, "US", "ok", files, {"export_competitor.csv": "b0aaaaaaa1"})
        self.assertEqual(ok.data["imported"], 2)
        a = ops.analyze(self.ws, ok.data["research_id"])
        self.assertEqual((a["project"]["mode"], a["project"]["name"]), ("NEW_OPPORTUNITY", "ok"))
        self.assertIn("B0AAAAAAA1", a["agg"]["asins"])

    def test_double_click_does_not_create_empty_or_duplicate_data(self):
        first = ops.import_research(self.ws, None, "US", "uno", self.files)
        again = ops.import_research(self.ws, None, "US", "due", self.files, research_id=first.data["research_id"])
        self.assertTrue(again.ok)
        self.assertEqual(again.data["imported"], 0)
        self.assertEqual(len([r for r in again.data["results"] if r["status"] == "DUPLICATE_FILE"]), 3)
        self.assertEqual(len(ops.list_projects(self.ws)), 1)
        n = ops.import_research(self.ws, None, "US", "x", [("bad.csv", b"a,b\n1,2\n")])                      # file non valido: nessun guscio vuoto
        self.assertFalse(n.ok)
        self.assertEqual(len(ops.list_projects(self.ws)), 1)
        self.assertFalse(ops.import_research(self.ws, None, "US", "x", []).ok)

    def test_more_than_five_files_refused(self):
        files = [(f"US_AMAZON_cerebro_B0AAAAAAA{i}_2026-10-12.csv", T.csv_bytes(T.F1[:i + 1])) for i in range(6)]
        r = ops.import_research(self.ws, None, "US", None, files)
        self.assertFalse(r.ok)
        self.assertEqual(ops.list_projects(self.ws), [])

    def test_save_as_project_status_and_brand_via_ops(self):
        r = ops.import_research(self.ws, "B0HLL1Y1CN", "US", None, self.files)
        rid = r.data["research_id"]
        a = ops.analyze(self.ws, rid)
        oid = next(iter(a["saved"].values()))["opportunity_id"]
        s = ops.save_as_project(self.ws, oid, "Provvisorio", "note")
        self.assertTrue(s.ok)
        self.assertEqual(s.data["status"], "REVIEW")
        self.assertTrue(ops.set_opportunity_status(self.ws, oid, "APPROVED").ok)
        self.assertFalse(ops.set_opportunity_status(self.ws, oid, "XXX").ok)
        self.assertTrue(ops.set_brand(self.ws, rid, ["mystery books for seniors"], True).ok)
        self.assertIn("mystery books for seniors", [k["keyword_norm"] for k in ops.analyze(self.ws, rid)["editorial"]["brand_signals"]])
        self.assertTrue(ops.update_project(self.ws, rid, "Nuovo nome", "REVIEW").ok)
        self.assertFalse(ops.update_project(self.ws, rid, "", "REVIEW").ok)
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)

    def test_read_only_connection_cannot_write_and_tolerates_missing_db(self):
        conn = ops.read_conn(self.ws)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM books")
        conn.close()
        empty = workspace.from_root(self.root / "DATABASE")                                      # radice senza DB
        self.assertEqual(ops.list_projects(empty), [])
        self.assertIsNone(ops.analyze(empty, "X"))


class BackupTests(unittest.TestCase):
    def test_multi_file_import_creates_one_backup_before_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace.from_root(make_root(tmp))
            c = connect(ws.db_path)
            c.close()
            files = [(f"US_AMAZON_cerebro_B0AAAAAAA{i}_2026-10-12.csv", T.csv_bytes(T.F1[:i + 1])) for i in range(1, 6)]
            r = ops.import_research(ws, None, "US", None, files)
            self.assertEqual(r.data["imported"], 5)
            backups = list(ws.backups_dir.glob("*competitor_research*.db"))
            self.assertEqual(len(backups), 1)                                      # 5 CSV = 1 backup, non 5
            probe = sqlite3.connect(backups[0])
            tables = {x[0] for x in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("books", tables)
            self.assertEqual(probe.execute("SELECT COUNT(*) FROM competitor_research_sources").fetchone()[0], 0)   # preso PRIMA della prima scrittura
            probe.close()
            add = ops.import_research(ws, None, "US", None, [(f"US_AMAZON_cerebro_B0BBBBBBB1_2026-10-12.csv", T.csv_bytes(T.F2))], research_id=None)
            self.assertTrue(add.ok)
            self.assertEqual(len(list(ws.backups_dir.glob("*.db"))), 2)             # una operazione = un backup

    def test_no_backup_when_nothing_valid_to_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace.from_root(make_root(tmp))
            connect(ws.db_path).close()
            self.assertFalse(ops.import_research(ws, None, "US", None, [("bad.csv", b"a,b\n1,2\n")]).ok)
            self.assertEqual(list(ws.backups_dir.glob("*.db")) if ws.backups_dir.exists() else [], [])


class NavigationTests(unittest.TestCase):
    def test_every_page_and_every_page_link_is_registered_in_the_menu(self):
        import re
        import ui_labels as U
        menu = {p for p, _ in U.NAV_PAGES}
        pages = {f"pages/{p.name}" for p in (ROOT / "DASHBOARD" / "pages").glob("*.py")}
        self.assertEqual(pages | {"panoramica.py"}, menu)                          # nessuna pagina irraggiungibile, nessuna voce fantasma
        for src in (ROOT / "DASHBOARD").rglob("*.py"):
            for target in re.findall(r"page_link\(\s*\"([^\"]+)\"", src.read_text(encoding="utf-8")) + re.findall(r"PAGE = \"([^\"]+)\"", src.read_text(encoding="utf-8")):
                self.assertIn(target, menu, f"{src.name}: page_link verso una pagina non nel menu")
        self.assertIn("pages/11_Suggerimenti_di_crescita.py", menu)
        self.assertIn("pages/12_Ricerca_competitor.py", menu)

    def test_growth_and_research_pages_render_inside_navigation_app(self):
        if AppTest is None or not REAL_DB.is_file():
            self.skipTest("streamlit o DB reale assenti")
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "k.db"
            dst = sqlite3.connect(db)
            src.backup(dst)
            dst.close()
            src.close()
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(db)
            try:
                at = AppTest.from_file(str(ROOT / "DASHBOARD" / "app.py"), default_timeout=120).run()
                self.assertFalse(at.exception)
                for page in ("pages/11_Suggerimenti_di_crescita.py", "pages/12_Ricerca_competitor.py"):
                    a = at.switch_page(page).run()
                    self.assertFalse(a.exception, page)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old


class RowsTests(unittest.TestCase):
    def test_table_rows_are_homogeneous_and_arrow_serializable(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            c = connect(Path(tmp.name) / "k.db")
            rid = S.create_project(c, None, None, "US", now=T.NOW)
            S.import_source(c, rid, T.parsed(T.A1, T.F1), now=T.NOW)
            S.import_source(c, rid, T.parsed(T.A2, T.F2[:2] + [T.row("solo qui")]), now=T.NOW)          # keyword con quasi tutti i dati mancanti
            a = S.analyze(c, rid)
            for rows in (CU.common_rows(a["agg"]["keywords"]), CU.competitor_rows(a["sources"]), CU.raw_rows(S.raw_observations(c, rid))):
                self.assertTrue(rows)
                self.assertEqual(mixed_columns(rows), [])
                pa.Table.from_pandas(pd.DataFrame(rows))
            e = S.analyze(c, S.create_project(c, "BX", None, "US", book_title="X", now=T.NOW))["gap"]
            self.assertEqual(e, [])
            rid2 = S.create_project(c, "BX", None, "US", book_title="X", now=T.NOW)
            S.import_source(c, rid2, T.parsed(T.A1, T.F1), now=T.NOW)
            gap = S.analyze(c, rid2, own=T.own(phrase=["mystery books for seniors"], observed=[{"search_term": "large print mystery books", "clicks": 3, "orders": 0, "spend": 1}]))["gap"]
            rows = CU.gap_rows(gap)
            self.assertEqual(mixed_columns(rows), [])
            pa.Table.from_pandas(pd.DataFrame(rows))
            c.close()
        finally:
            tmp.cleanup()

    def test_ads_and_opportunity_cards_show_external_source_and_factors(self):
        log = []
        from test_dashboard_labels import Rec, strings
        tmp = tempfile.TemporaryDirectory()
        try:
            c = connect(Path(tmp.name) / "k.db")
            c.execute("INSERT INTO books (book_id,title) VALUES ('B1','Twenty Women, Twenty Mysteries')")
            rid = S.create_project(c, "B1", None, "US", now=T.NOW)
            for asin, rows in ((T.A1, T.F1), (T.A2, T.F2), (T.A3, T.F3)):
                S.import_source(c, rid, T.parsed(asin, rows), now=T.NOW)
            a = S.analyze(c, rid)
            st = Rec(log)
            CU.ads_card(st, a["ads"][0])
            CU.opportunity_card(st, a["editorial"]["opportunities"][0])
            text = "\n".join(s for _n, ar, kw in log for s in strings([ar, kw]))
            for need in ("HELIUM 10 / FONTE ESTERNA", "TESTA IN FRASE", "Fonte: Helium 10 · fonte esterna", "Affidabilità", "Fattori del punteggio",
                         "BOOK OPPORTUNITY SCORE", "NUOVA OPPORTUNITÀ DERIVATA", "Non è una raccomandazione a pubblicare"):
                self.assertIn(need, text, need)
            self.assertNotIn("EXACT", text.replace("Exact diretto", ""))
            c.close()
        finally:
            tmp.cleanup()


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(self.tmp.name)
        self.ws = workspace.from_root(self.root)
        c = connect(self.ws.db_path)
        c.execute("INSERT INTO books (book_id,title,marketplace) VALUES ('B0HLL1Y1CN','Twenty Women, Twenty Mysteries','US')")
        c.commit()
        c.close()
        self.old = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        os.environ["KDP_ADS_DB"] = str(self.ws.db_path)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_page(self, **kw):
        return AppTest.from_file(str(PAGE), default_timeout=120).run()

    def texts(self, at):
        return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] + [w.value for w in at.warning]
                         + [h.value for h in at.subheader])

    def test_empty_state(self):
        at = self.run_page()
        self.assertFalse(at.exception)
        t = self.texts(at)
        self.assertIn("Fonte esterna: Helium 10 Cerebro", t)
        self.assertIn("Nessuna ricerca competitor importata.", t)
        self.assertIn("Associa questa ricerca a un libro", [s.label for s in at.selectbox])
        self.assertEqual(at.selectbox[0].options[0], "Nessun libro — ricerca nuova opportunità editoriale")

    def test_page_renders_all_tabs_for_both_modes_and_changes_nothing(self):
        for book in (None, "B0HLL1Y1CN"):
            r = ops.import_research(self.ws, book, "US", f"ricerca {book}", [(T.fname(a), T.csv_bytes(rows)) for a, rows in ((T.A1, T.F1), (T.A2, T.F2), (T.A3, T.F3))],
                                    book_title="Twenty Women, Twenty Mysteries" if book else None)
            self.assertTrue(r.ok, r.errors)
        books_hash = hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest()
        conn = sqlite3.connect(self.ws.db_path)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("competitor_research_sources", "competitor_keyword_observations",
                                                                                      "competitor_opportunities", "decisions", "target_rows")}
        conn.close()
        at = self.run_page()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(len(at.tabs), 8)
        self.assertEqual([t.label for t in at.tabs], ["PANORAMICA", "KEYWORD COMUNI", "KEYWORD DI LANCIO", "KEYWORD GAP", "OPPORTUNITÀ ADS", "OPPORTUNITÀ EDITORIALI", "COMPETITOR", "DATI GREZZI"])
        t1 = self.texts(at)                                            # progetto piu' recente = EXISTING_BOOK
        for need in ("3 competitor analizzati", "4 keyword presenti su 3/3", "keyword da valutare in FRASE", "opportunità principali da approfondire",
                     "NUOVA OPPORTUNITÀ DERIVATA", "TESTA IN FRASE", "Nessuna decisione editoriale automatica", "Segnale di mercato / competitor branded query"):
            self.assertIn(need, t1, need)
        for df in at.dataframe:
            pa.Table.from_pandas(df.value)                              # nessun ArrowTypeError
        self.assertGreaterEqual(len(at.dataframe), 4)
        # NEW_OPPORTUNITY: gap/ads non applicabili
        box = [s for s in at.selectbox if s.label == "Ricerca"][0]
        other = [o for o in box.options if "ricerca None" in str(o)][0]
        box.set_value(other)
        at = at.run()
        self.assertFalse(at.exception, at.exception)
        t2 = self.texts(at)
        self.assertIn("Il gap rispetto alle campagne si applica solo alle ricerche associate a un libro esistente.", t2)
        self.assertNotIn("keyword da valutare in FRASE", t2)
        self.assertIn("opportunità principali da approfondire", t2)                    # le opportunita' editoriali ci sono anche senza libro
        conn = sqlite3.connect(self.ws.db_path)
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in counts}
        conn.close()
        self.assertEqual(after, counts)                                 # visualizzare non scrive nulla
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), books_hash)


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class GrowthIntegrationTests(unittest.TestCase):
    """Suggerimenti di crescita su una COPIA del DB reale: la sezione DA RICERCA COMPETITOR e' separata e non cambia i suggerimenti Amazon Ads."""
    BOOK = "B0HLL1Y1CN"

    def test_growth_page_shows_separate_section_without_changing_ads_suggestions(self):
        real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "k.db"
            dst = sqlite3.connect(db)
            src.backup(dst)
            dst.close()
            src.close()
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(db)
            try:
                before = AppTest.from_file(str(GROWTH), default_timeout=120).run()
                before = tov.select(before, "Libro", self.BOOK)
                self.assertFalse(before.exception)
                head_before = [h.value for h in before.header]
                self.assertFalse(any("DA RICERCA COMPETITOR" in h for h in head_before))
                c = connect(db)
                rid = S.create_project(c, self.BOOK, None, "US", now=T.NOW)
                for asin, rows in ((T.A1, T.F1), (T.A2, T.F2), (T.A3, T.F3)):
                    S.import_source(c, rid, T.parsed(asin, rows), now=T.NOW)
                c.close()
                after = AppTest.from_file(str(GROWTH), default_timeout=120).run()
                after = tov.select(after, "Libro", self.BOOK)
                self.assertFalse(after.exception, after.exception)
                heads = [h.value for h in after.header]
                self.assertTrue(any(h.startswith("DA RICERCA COMPETITOR") for h in heads), heads)
                ads_heads = [h for h in heads if not h.startswith("DA RICERCA COMPETITOR")]
                self.assertEqual(ads_heads, head_before)                                    # sezioni e conteggi Amazon Ads identici
                text = "\n".join(m.value for m in after.markdown) + "\n".join(c.value for c in after.caption)
                self.assertIn("HELIUM 10 / FONTE ESTERNA", text)
                self.assertIn("TESTA IN FRASE", text)
                self.assertIn("non contano nei KPI", text)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), books_hash)


if __name__ == "__main__":
    unittest.main()
