"""Dashboard V2: gestione libri/campagne, upload, snapshot, dry-run, import, analisi, decisioni, routine settimanale.
Tutto su workspace e DB temporanei: il progetto reale non viene mai toccato. Nessuna rete, nessuna Amazon."""
import hashlib
import json
import os
import re
import runpy
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import bootstrap  # noqa: E402,F401
import test_rules as tr  # noqa: E402
from kdp_ads import registry  # noqa: E402
from ops import backup, catalog, importing, pipeline_ops, weekly, workspace  # noqa: E402
from ops import ValidationError  # noqa: E402

BOOK = {"title": "Libro di prova", "book_id": "B0TEST0001", "price": "19,99", "royalty": "8.55", "target_acos": "35",
        "marketplace": "US", "currency": "USD", "phase": "PROFIT_STABLE"}
D_END = date(2026, 9, 30)


def tcsv(seed=0):
    return tr.it_rows_target([("kw uno", 100 + seed, 10, 100 + seed, 400), ("kw due", 40 + seed, 5, 30 + seed, 150)]).encode("utf-8")


def scsv(seed=0):
    return tr.it_rows_st([("kw uno", "kw uno", 20 + seed, 6, 20 + seed, 240)]).encode("utf-8")


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class WsBase(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("KDP_ADS_DB", "KDP_ADS_ROOT")}
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("CONFIG", "INPUT", "DATABASE", "LOGS", "OUTPUT"):
            (self.root / d).mkdir()
        for f in ("rules.json", "column_mapping.json"):
            shutil.copy(ROOT / "CONFIG" / f, self.root / "CONFIG" / f)
        (self.root / "CONFIG" / "settings.json").write_text(json.dumps({"read_only": True, "search_term_max_days": 65}), encoding="utf-8")
        self.ws = workspace.from_root(self.root)

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    # helper
    def new_book(self, **over):
        r = catalog.add_book(self.ws, {**BOOK, **over})
        self.assertTrue(r.ok, r.errors)
        return r

    def new_campaign(self, cid="C_EXACT", ctype="EXACT", book="B0TEST0001"):
        r = catalog.add_campaign(self.ws, {"book_id": book, "campaign_id": cid, "campaign_name": cid, "campaign_type": ctype})
        self.assertTrue(r.ok, r.errors)

    def report(self, cid, rtype, kind, data, name, d_to=D_END, commit=True, book="B0TEST0001", d_from=None):
        """Flusso completo: periodo -> piano upload -> anteprima -> dry-run -> (import reale)."""
        folder = self.ws.input_dir / book / cid
        period = importing.resolve_period(kind, d_from, d_to)
        plan = importing.plan_upload(folder, name, data)
        entry = importing.build_entry(plan.final_name, rtype, period)
        dry = importing.dry_run(self.ws, book, cid, plan.final_name, data, entry)
        if not commit:
            return plan, entry, dry, None
        self.assertTrue(dry.ok, (dry.status, dry.problems))
        return plan, entry, dry, importing.commit_import(self.ws, book, cid, name, plan.final_name, data, entry)

    def db(self):
        c = sqlite3.connect(self.ws.db_path)
        c.row_factory = sqlite3.Row
        return c

    def count(self, table, where=""):
        if not self.ws.db_path.is_file():
            return 0
        c = self.db()
        try:
            return c.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]
        except sqlite3.OperationalError:          # tabella non ancora creata (nessuna operazione sul DB eseguita)
            return 0
        finally:
            c.close()


# ------------------------------------------------------------------ libri
class BookTests(WsBase):
    def test_add_book_creates_registry_entry_and_input_folder(self):
        r = self.new_book()
        self.assertEqual(r.message, "Libro aggiunto correttamente")
        b = registry.load_books(self.ws.books_path)["B0TEST0001"]
        self.assertEqual((b["price"], b["royalty_per_copy"], b["target_acos"], b["phase"]), (19.99, 8.55, 35.0, "PROFIT_STABLE"))
        self.assertEqual(b["break_even_acos"], 42.77)                         # calcolato da registry
        self.assertTrue((self.ws.input_dir / "B0TEST0001").is_dir())          # creata da registry.add_book
        lst = catalog.list_books(self.ws)
        self.assertEqual((lst[0]["book_id"], lst[0]["campaigns"], lst[0]["break_even_acos"]), ("B0TEST0001", 0, 42.77))

    def test_duplicate_book_id_rejected_without_overwrite(self):
        self.new_book()
        before = self.ws.books_path.read_text(encoding="utf-8")
        r = catalog.add_book(self.ws, {**BOOK, "title": "Altro titolo", "price": "30"})
        self.assertFalse(r.ok)
        self.assertIn("Esiste già", r.errors[0])
        self.assertEqual(self.ws.books_path.read_text(encoding="utf-8"), before)

    def test_invalid_fields_are_rejected_and_nothing_is_written(self):
        cases = [({"title": ""}, "Titolo"), ({"book_id": ""}, "Book ID"), ({"price": "abc"}, "Prezzo"), ({"price": ""}, "Prezzo"),
                 ({"price": "0"}, "maggiore di zero"), ({"price": "-5"}, "maggiore di zero"),
                 ({"royalty": "0"}, "maggiore di zero"), ({"royalty": "-1"}, "maggiore di zero"), ({"royalty": "x"}, "Royalty"),
                 ({"royalty": "25"}, "non può superare il prezzo"), ({"target_acos": "0"}, "ACOS"), ({"target_acos": "150"}, "ACOS"),
                 ({"target_acos": "boh"}, "ACOS"), ({"phase": "BOH"}, "Fase"), ({"book_id": "id con spazi/slash"}, "book_id"),
                 ({"price": "nan"}, "Prezzo")]
        for over, needle in cases:
            r = catalog.add_book(self.ws, {**BOOK, **over})
            self.assertFalse(r.ok, over)
            self.assertTrue(any(needle in e for e in r.errors), (over, r.errors))
        self.assertEqual(registry.load_books(self.ws.books_path), {})
        self.assertEqual(list(self.ws.input_dir.iterdir()), [])

    def test_decimal_comma_accepted(self):
        self.assertEqual(catalog.parse_decimal("8,55", "x"), 8.55)
        with self.assertRaises(ValidationError):
            catalog.parse_decimal("inf", "x")


# ------------------------------------------------------------------ campagne
class CampaignTests(WsBase):
    def setUp(self):
        super().setUp()
        self.new_book()

    def test_add_campaign_creates_folder_and_metadata(self):
        r = catalog.add_campaign(self.ws, {"book_id": "B0TEST0001", "campaign_id": "TEST_EXACT", "campaign_name": "Test exact", "campaign_type": "exact"})
        self.assertTrue(r.ok, r.errors)
        folder = self.ws.input_dir / "B0TEST0001" / "TEST_EXACT"
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["book_id"], meta["campaign_id"], meta["campaign_name"], meta["campaign_type"], meta["marketplace"], meta["currency"]),
                         ("B0TEST0001", "TEST_EXACT", "Test exact", "EXACT", "US", "USD"))
        self.assertEqual(registry.validate_metadata(meta), [])
        self.assertEqual([c["campaign_id"] for c in catalog.list_campaigns(self.ws, "B0TEST0001")], ["TEST_EXACT"])
        self.assertEqual(catalog.list_books(self.ws)[0]["campaigns"], 1)

    def test_supported_types_and_unknown_book_and_missing_fields(self):
        for t in catalog.CAMPAIGN_TYPES:
            self.assertTrue(catalog.add_campaign(self.ws, {"book_id": "B0TEST0001", "campaign_id": f"C_{t}", "campaign_type": t}).ok, t)
        self.assertFalse(catalog.add_campaign(self.ws, {"book_id": "NOPE", "campaign_id": "X", "campaign_type": "EXACT"}).ok)
        self.assertFalse(catalog.add_campaign(self.ws, {"book_id": "B0TEST0001", "campaign_id": "", "campaign_type": "EXACT"}).ok)
        self.assertFalse(catalog.add_campaign(self.ws, {"book_id": "B0TEST0001", "campaign_id": "bad id/..", "campaign_type": "EXACT"}).ok)

    def test_duplicate_campaign_needs_confirmation_and_keeps_snapshots(self):
        self.new_campaign("C1")
        folder = self.ws.input_dir / "B0TEST0001" / "C1"
        (folder / "x.csv").write_bytes(tcsv())
        registry.add_snapshot(folder, {"file": "x.csv", "report_type": "TARGETING", "report_scope": "LIFETIME", "date_from": None, "date_to": "2026-09-30"})
        f = {"book_id": "B0TEST0001", "campaign_id": "C1", "campaign_name": "Nuovo nome", "campaign_type": "PHRASE"}
        r = catalog.add_campaign(self.ws, f)                                # senza conferma: rifiutato
        self.assertFalse(r.ok)
        self.assertTrue(r.data["duplicate"])
        self.assertEqual(json.loads((folder / "metadata.json").read_text())["campaign_type"], "EXACT")
        self.assertTrue(catalog.add_campaign(self.ws, f, overwrite=True).ok)  # con conferma: aggiorna nome/tipo
        meta = json.loads((folder / "metadata.json").read_text())
        self.assertEqual((meta["campaign_name"], meta["campaign_type"], len(meta["snapshots"])), ("Nuovo nome", "PHRASE", 1))


# ------------------------------------------------------------------ upload, periodo, snapshot
class UploadAndPeriodTests(WsBase):
    def setUp(self):
        super().setUp()
        self.new_book()
        self.new_campaign()
        self.folder = self.ws.input_dir / "B0TEST0001" / "C_EXACT"

    def test_upload_plan_never_overwrites(self):
        data = tcsv()
        self.assertEqual(importing.plan_upload(self.folder, "t.csv", data).status, "NEW")
        (self.folder / "t.csv").write_bytes(data)
        p = importing.plan_upload(self.folder, "t.csv", data)                    # identico: riuso
        self.assertEqual((p.status, p.final_name), ("REUSE_IDENTICAL", "t.csv"))
        p = importing.plan_upload(self.folder, "t.csv", tcsv(1))                 # diverso: nome sicuro
        self.assertEqual((p.status, p.final_name), ("RENAMED", "t__2.csv"))
        (self.folder / "t__2.csv").write_bytes(tcsv(1))
        self.assertEqual(importing.plan_upload(self.folder, "t.csv", tcsv(2)).final_name, "t__3.csv")
        self.assertEqual((self.folder / "t.csv").read_bytes(), data)            # l'originale e' intatto

    def test_filename_sanitised_and_non_csv_rejected(self):
        self.assertEqual(importing.safe_filename("../../evil name.csv"), "evil name.csv")
        self.assertEqual(importing.safe_filename("C:\\x\\a$b.csv"), "a_b.csv")
        for bad in ("report.txt", ".csv", "", None, "x.csv.exe"):
            with self.assertRaises(ValidationError):
                importing.safe_filename(bad)
        with self.assertRaises(ValidationError):
            importing.plan_upload(self.folder, "t.csv", b"")
        self.assertEqual(importing.plan_upload(self.folder, "../../evil.csv", tcsv()).final_name, "evil.csv")

    def test_periods(self):
        P = importing.resolve_period
        self.assertEqual(vars(P("LIFETIME", None, date(2026, 9, 30))), {"scope": "LIFETIME", "date_from": None, "date_to": "2026-09-30"})
        self.assertEqual(vars(P("D65", None, date(2026, 9, 30))), {"scope": "DATE_RANGE", "date_from": "2026-07-28", "date_to": "2026-09-30"})
        self.assertEqual(vars(P("D28", None, date(2026, 9, 26))), {"scope": "DATE_RANGE", "date_from": "2026-08-30", "date_to": "2026-09-26"})
        self.assertEqual(vars(P("CUSTOM", date(2026, 8, 1), date(2026, 8, 15))), {"scope": "DATE_RANGE", "date_from": "2026-08-01", "date_to": "2026-08-15"})
        for args in (("CUSTOM", date(2026, 9, 2), date(2026, 9, 1)), ("CUSTOM", None, date(2026, 9, 1)), ("D28", None, None), ("BOH", None, D_END)):
            with self.assertRaises(ValidationError):
                P(*args)

    def test_preview_and_snapshot_entry(self):
        entry = importing.build_entry("t.csv", "TARGETING", importing.resolve_period("D28", None, date(2026, 9, 26)), " 14 Day ")
        self.assertEqual(entry, {"file": "t.csv", "report_type": "TARGETING", "report_scope": "DATE_RANGE", "date_from": "2026-08-30",
                                 "date_to": "2026-09-26", "attribution_window": "14 Day"})
        pv = importing.preview("B0TEST0001", "C_EXACT", "t.csv", entry)
        self.assertEqual((pv["Libro"], pv["Campagna"], pv["Tipo report"], pv["File"]), ("B0TEST0001", "C_EXACT", "Target", "t.csv"))
        self.assertIn("B0TEST0001|C_EXACT|TARGETING|2026-08-30__2026-09-26", pv["Snapshot ID previsto"])
        self.assertIn("2026-08-30 → 2026-09-26", pv["Periodo"])
        with self.assertRaises(ValidationError):
            importing.build_entry("t.csv", "BOH", importing.resolve_period("LIFETIME", None, D_END))


# ------------------------------------------------------------------ dry-run e import
class ImportTests(WsBase):
    def setUp(self):
        super().setUp()
        self.new_book()
        self.new_campaign()
        self.folder = self.ws.input_dir / "B0TEST0001" / "C_EXACT"

    def test_dry_run_valid_and_has_no_side_effects(self):
        before = tree(self.root / "INPUT")
        plan, entry, dry, _ = self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv", commit=False)
        self.assertEqual((dry.status, dry.ok, dry.rows, dry.report_type, dry.period_id), ("DRY_RUN_OK", True, 2, "TARGETING", "LIFETIME__2026-09-30"))
        self.assertEqual(dry.date_to, "2026-09-30")
        self.assertEqual(tree(self.root / "INPUT"), before)                      # nessun file, nessun metadata toccato
        self.assertEqual(self.count("imports"), 0)
        self.assertEqual(self.count("target_rows"), 0)

    def test_real_import_saves_file_original_snapshot_and_backup(self):
        plan, entry, dry, out = self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        self.assertEqual((out.status, out.rows, out.period_id), ("IMPORTED", 2, "LIFETIME__2026-09-30"))
        self.assertIsNotNone(out.import_id)
        self.assertEqual((self.folder / "t.csv").read_bytes(), tcsv())
        originals = list((self.folder / "_originali").glob("*__t.csv"))
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0].read_bytes(), tcsv())
        meta = json.loads((self.folder / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["snapshots"], [entry])                               # snapshot dichiarato
        c = self.db()
        row = c.execute("SELECT * FROM imports").fetchone()
        c.close()
        self.assertEqual((row["book_id"], row["campaign_id"], row["report_type"], row["report_scope"], row["period_id"], row["source_filename"]),
                         ("B0TEST0001", "C_EXACT", "TARGETING", "LIFETIME", "LIFETIME__2026-09-30", "t.csv"))
        self.assertEqual(row["snapshot_id"], out.snapshot_id)
        self.assertEqual(out.snapshot_id, importing.preview("B0TEST0001", "C_EXACT", "t.csv", entry)["Snapshot ID previsto"].split("  ")[0])
        self.assertTrue(out.backup and Path(out.backup).is_file())               # backup creato prima dell'import

    def test_same_name_different_content_is_renamed_never_overwritten(self):
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        plan, entry, dry, out = self.report("C_EXACT", "TARGETING", "D28", tcsv(7), "t.csv")
        self.assertEqual((plan.status, plan.final_name, out.status), ("RENAMED", "t__2.csv", "IMPORTED"))
        self.assertEqual((self.folder / "t.csv").read_bytes(), tcsv())              # primo file intatto
        self.assertEqual((self.folder / "t__2.csv").read_bytes(), tcsv(7))
        self.assertEqual(self.count("imports"), 2)

    def test_duplicate_report_is_detected_and_changes_nothing(self):
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        before = tree(self.root / "INPUT")
        plan, entry, dry, _ = self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "copia.csv", commit=False)
        self.assertEqual((dry.status, dry.ok), ("SKIPPED_DUPLICATE", False))
        out = importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", "copia.csv", plan.final_name, tcsv(), entry)   # anche se forzato
        self.assertEqual(out.status, "SKIPPED_DUPLICATE")
        self.assertEqual(self.count("imports"), 1)
        after = tree(self.root / "INPUT")
        self.assertEqual({k: v for k, v in after.items() if "_originali" not in k}, {k: v for k, v in before.items() if "_originali" not in k})

    def test_snapshot_conflict(self):
        self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(), "s.csv")
        plan, entry, dry, _ = self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(3), "s_rexport.csv", commit=False)
        self.assertEqual((dry.status, dry.ok), ("SNAPSHOT_CONFLICT", False))
        self.assertTrue(any("SNAPSHOT_CONFLICT" in p for p in dry.problems))
        before = (self.folder / "metadata.json").read_text(encoding="utf-8")
        out = importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", "s_rexport.csv", plan.final_name, scsv(3), entry)   # forzato
        self.assertEqual(out.status, "ERROR")
        self.assertEqual((self.folder / "metadata.json").read_text(encoding="utf-8"), before)    # annullato
        self.assertFalse((self.folder / "s_rexport.csv").exists())
        self.assertEqual(self.count("imports"), 1)

    def test_wrong_report_type_is_an_error(self):
        plan, entry, dry, _ = self.report("C_EXACT", "SEARCH_TERM", "D65", tcsv(), "t.csv", commit=False)      # e' un Target
        self.assertEqual((dry.status, dry.ok), ("ERROR", False))
        self.assertTrue(dry.problems)

    def test_real_import_failure_rolls_back_files_and_metadata(self):
        plan, entry, dry, _ = self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv", commit=False)
        before = tree(self.root / "INPUT")
        with mock.patch.object(importing, "import_all", return_value=[]):
            out = importing.commit_import(self.ws, "B0TEST0001", "C_EXACT", "t.csv", plan.final_name, tcsv(), entry)
        self.assertEqual(out.status, "ERROR")
        self.assertEqual(tree(self.root / "INPUT"), before)

    def test_does_not_import_other_pending_files_in_the_folder(self):
        (self.folder / "altro.csv").write_bytes(tcsv(99))                            # file non dichiarato presente a mano
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        self.assertEqual(self.count("imports"), 1)


# ------------------------------------------------------------------ backup
class BackupTests(WsBase):
    def test_backup_unique_readable_and_none_without_db(self):
        self.assertIsNone(backup.backup_db(self.ws, "x"))
        self.new_book()
        self.new_campaign()
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        a, b = backup.backup_db(self.ws, "pre test!"), backup.backup_db(self.ws, "pre test!")
        self.assertNotEqual(a, b)                                                        # mai sovrascritto
        self.assertTrue(a.name.startswith("kdp_ads_") and "pre_test_" in a.name)
        c = sqlite3.connect(a)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM imports").fetchone()[0], 1)
        c.close()
        self.assertEqual(a.parent, self.ws.backups_dir)


# ------------------------------------------------------------------ analisi, decisioni, flusso completo, sicurezza
class FlowTests(WsBase):
    def full_flow(self):
        self.new_book()
        self.new_campaign()
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(), "t.csv")
        self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(), "s.csv")
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001")
        d = pipeline_ops.run_book_decisions(self.ws, "B0TEST0001", a.data["run_id"] if a.ok else None)
        return a, d

    def test_analysis_and_decision_runs(self):
        a, d = self.full_flow()
        self.assertTrue(a.ok, a.errors)
        self.assertEqual(a.data["rows"], 3)
        self.assertEqual((self.count("analysis_runs"), self.count("recommendations")), (1, 3))
        for k in ("run_id", "rows", "overlaps", "warnings", "generated_at", "csv", "md"):
            self.assertIn(k, a.data)
        self.assertTrue(Path(a.data["csv"]).is_file() and str(a.data["csv"]).startswith(str(self.root / "OUTPUT")))
        self.assertTrue(d.ok, d.errors)
        self.assertEqual((d.data["analysis_run_id"], d.data["decisions"]), (a.data["run_id"], 2))
        self.assertEqual((self.count("decision_runs"), self.count("decisions")), (1, 2))
        for k in ("run_id", "actionable", "paused", "analysis_warnings", "own_warnings"):
            self.assertIn(k, d.data)
        self.assertEqual(self.count("decisions", "WHERE status='PROPOSED' AND manual_approval_required=1"), 2)
        self.assertEqual(pipeline_ops.latest_analysis_run(self.ws, "B0TEST0001")["run_id"], a.data["run_id"])
        self.assertEqual(pipeline_ops.latest_decision_run(self.ws, "B0TEST0001")["run_id"], d.data["run_id"])
        backups = sorted(p.name for p in self.ws.backups_dir.glob("*.db"))
        self.assertTrue(any("pre_import" in n for n in backups) and any("pre_analisi" in n for n in backups)
                        and any("pre_decisioni" in n for n in backups), backups)

    def test_analysis_of_unknown_book_is_a_clean_error(self):
        r = pipeline_ops.run_book_analysis(self.ws, "NOPE")
        self.assertFalse(r.ok)
        self.assertTrue(r.errors)

    def test_no_network_during_the_whole_flow(self):
        def boom(*a, **k):
            raise AssertionError("accesso di rete non consentito")
        with mock.patch("socket.socket", boom), mock.patch("socket.create_connection", boom), mock.patch("socket.getaddrinfo", boom):
            a, d = self.full_flow()
        self.assertTrue(a.ok and d.ok)

    def test_only_allowed_locations_are_written(self):
        before = tree(self.root)
        self.full_flow()
        after = tree(self.root)
        changed = [k for k, v in after.items() if before.get(k) != v]
        allowed = ("INPUT/", "OUTPUT/", "LOGS/", "DATABASE/", "CONFIG/books.json")
        self.assertTrue(changed)
        self.assertEqual([k for k in changed if not k.startswith(allowed)], [])
        for f in ("rules.json", "column_mapping.json", "settings.json"):               # regole e mapping mai modificati
            self.assertEqual(after[f"CONFIG/{f}"], before[f"CONFIG/{f}"])


class SourceSafetyTests(unittest.TestCase):
    V2_FILES = [*(ROOT / "DASHBOARD" / "ops").glob("*.py"), ROOT / "DASHBOARD" / "pages" / "7_Gestione_libri.py",
                ROOT / "DASHBOARD" / "pages" / "8_Importa_report.py", ROOT / "DASHBOARD" / "components" / "ops_ui.py",
                ROOT / "SCRIPTS" / "kdp_ads" / "pipeline.py"]

    def test_no_network_browser_or_amazon_code(self):
        net = re.compile(r"^\s*(?:import|from)\s+(requests|urllib|http|httpx|aiohttp|socket|ftplib|smtplib|selenium|playwright|webbrowser|"
                         r"subprocess|boto3|botocore)\b", re.M)
        amazon = ("amazon_ads", "advertising-api", "advertising.amazon", "sellingpartner", "selling-partner", "api.amazon", "ads-api")
        self.assertTrue(self.V2_FILES)
        for p in self.V2_FILES:
            src = p.read_text(encoding="utf-8")
            self.assertIsNone(net.search(src), p.name)
            for token in amazon:
                self.assertNotIn(token, src, f"{p.name}: {token}")

    def test_v2_never_touches_rules_or_logic_files(self):
        src = "".join(p.read_text(encoding="utf-8") for p in self.V2_FILES)
        for protected in ("rules.json\", \"w", "write_text(json.dumps(rules", "open(self.rules_path, \"w"):
            self.assertNotIn(protected, src)


# ------------------------------------------------------------------ routine settimanale
class WeeklyTests(WsBase):
    REF = date(2026, 10, 1)

    def setUp(self):
        super().setUp()
        self.new_book()
        self.new_campaign("C_EXACT")

    def status(self, ref=None):
        rows = weekly.checklist(self.ws, "B0TEST0001", ref or self.REF)
        return rows, {i["label"]: i["status"] for i in rows[0]["items"]}

    def test_incomplete_set_lists_what_is_missing(self):
        rows, st = self.status()
        self.assertEqual(st, {"Target Lifetime": "MANCANTE", "Search Term 65D": "MANCANTE", "Target 28D": "MANCANTE", "Search Term 28D": "MANCANTE"})
        self.assertFalse(rows[0]["complete"])
        self.assertFalse(weekly.book_complete(rows))
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(1), "a.csv")
        self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(1), "b.csv")
        rows, st = self.status()
        self.assertEqual((st["Target Lifetime"], st["Search Term 65D"], st["Target 28D"], st["Search Term 28D"]),
                         ("PRESENTE", "PRESENTE", "MANCANTE", "MANCANTE"))
        self.assertEqual([m["label"] for m in rows[0]["missing"]], ["Target 28D", "Search Term 28D"])
        self.assertFalse(rows[0]["complete"])

    def test_complete_set(self):
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(1), "a.csv")
        self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(1), "b.csv")
        self.report("C_EXACT", "TARGETING", "D28", tcsv(2), "c.csv")
        self.report("C_EXACT", "SEARCH_TERM", "D28", scsv(2), "d.csv")
        rows, st = self.status()
        self.assertEqual(set(st.values()), {"PRESENTE"})
        self.assertTrue(rows[0]["complete"] and weekly.book_complete(rows))
        self.assertEqual(rows[0]["missing"], [])

    def test_stale_reports_are_not_counted_as_present(self):
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(1), "a.csv")
        rows, st = self.status(ref=date(2026, 10, 20))                      # 20 giorni dopo
        self.assertEqual(st["Target Lifetime"], "OBSOLETO")
        self.assertFalse(rows[0]["complete"])

    def test_custom_window_does_not_count_as_28d(self):
        self.report("C_EXACT", "TARGETING", "CUSTOM", tcsv(5), "x.csv", d_from=date(2026, 9, 10))     # 21 giorni: non e' 28D
        _, st = self.status()
        self.assertEqual(st["Target 28D"], "MANCANTE")


# ------------------------------------------------------------------ pagine V2 (registratore con clic e upload simulati)
class Upload:
    def __init__(self, name, data):
        self.name, self._d = name, data

    def getvalue(self):
        return self._d


class Rec:
    def __init__(self, ctl, name="st"):
        self._c, self._n = ctl, name

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n == "session_state":
            return self._c["state"]
        return Rec(self._c, f"{self._n}.{n}")

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def __getitem__(self, i):
        return Rec(self._c, self._n + "[]")

    def __call__(self, *a, **k):
        c, S, base = self._c, self._c["state"], self._n.split(".")[-1]
        c["log"].append((self._n, a, k))
        label = a[0] if a else None
        key = k.get("key")
        if base == "columns":
            n = a[0]
            return [Rec(c, "col") for _ in range(n if isinstance(n, int) else len(n))]
        if base in ("button", "form_submit_button"):
            return label in c["press"] and not k.get("disabled")
        if base == "text_input":
            return c["inputs"].get(label, S.get(key, k.get("value", "")))
        if base == "selectbox":
            opts = list(a[1])
            if label in c["inputs"] and c["inputs"][label] in opts:
                return c["inputs"][label]
            return S[key] if key in S and S[key] in opts else opts[k.get("index", 0)]
        if base == "radio":
            opts = list(a[1])
            return S[key] if key in S and S[key] in opts else opts[0]
        if base == "date_input":
            return c["inputs"].get(label, S.get(key, k.get("value", date.today())))
        if base == "file_uploader":
            return c["upload"]
        if base == "stop":
            raise SystemExit
        return Rec(c, self._n + "()")


def flat(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from flat(k)
            yield from flat(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from flat(x)
    elif isinstance(obj, (int, float, date)):
        yield str(obj)


class Pages:
    """Esegue una pagina V2 piu' volte condividendo lo stato di sessione (come i rerun di Streamlit)."""

    def __init__(self, root):
        self.state = {}
        self.root = root

    def run(self, page, press=(), inputs=None, upload=None):
        ctl = {"log": [], "state": self.state, "press": set(press), "inputs": inputs or {}, "upload": upload}
        old, env = sys.modules.get("streamlit"), {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        os.environ.pop("KDP_ADS_DB", None)
        sys.modules["streamlit"] = Rec(ctl)
        for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
            del sys.modules[m]
        try:
            runpy.run_path(str(ROOT / "DASHBOARD" / "pages" / page), run_name="__main__")
        except SystemExit:
            pass
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if old is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = old
        texts = [s for name, a, k in ctl["log"] for s in flat([a, {kk: vv for kk, vv in k.items() if kk != "format_func"}])]
        return texts, ctl["log"]


ENGLISH_V2 = ["Add book", "Add campaign", "Import report", "Upload", "Analyze book", "Generate decisions", "Weekly import", "Book ID:",
              "Missing", "Present", "Dry run", "Select book", "Choose", "Period type", "Report type", "Submit"]


class PageTests(WsBase):
    def setUp(self):
        super().setUp()
        self.p = Pages(self.root)

    def test_book_page_is_italian_and_read_only_until_a_button_is_pressed(self):
        before = tree(self.root)
        texts, log = self.p.run("7_Gestione_libri.py")
        self.assertEqual(tree(self.root), before)
        allt = "\n".join(texts)
        for need in (U_NO_AMAZON, "Gestione libri", "Libri esistenti", "Aggiungi nuovo libro", "Titolo", "Book ID / ASIN", "AGGIUNGI LIBRO",
                     "Aggiungi marketplace al libro", "Aggiungi campagna", "Nessun libro registrato"):
            self.assertIn(need, allt, need)
        for bad in ENGLISH_V2:
            self.assertNotIn(bad, allt, bad)
        self.new_book_shell()                                              # con un libro compaiono i form di marketplace e campagna
        texts, _ = self.p.run("7_Gestione_libri.py")
        allt = "\n".join(texts)
        for need in ("Marketplace", "Altro marketplace (codice, facoltativo)", "Valuta", "Prezzo", "Royalty per copia", "ACOS obiettivo (%)",
                     "Fase libro", "AGGIUNGI MARKETPLACE", "Non si può creare una campagna senza marketplace"):
            self.assertIn(need, allt, need)

    def new_book_shell(self, book_id="B0PAGE0001", title="Libro pagina"):
        r = catalog.add_book_shell(self.ws, {"title": title, "book_id": book_id})
        self.assertTrue(r.ok, r.errors)

    MPFORM = {"Prezzo": "19,99", "Royalty per copia": "8.55", "Valuta": "USD"}
    CID = "ID campagna (nome della cartella, es. LIBRO_EXACT; unico nel libro)"

    def test_add_book_marketplace_and_campaign_through_the_page(self):
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI LIBRO"], inputs={"Titolo": "Libro pagina", "Book ID / ASIN": "B0PAGE0001"})
        self.assertIn("Libro aggiunto correttamente", t)
        self.assertTrue((self.ws.input_dir / "B0PAGE0001").is_dir())
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI LIBRO"], inputs={"Titolo": "Altro", "Book ID / ASIN": "B0PAGE0001"})    # duplicato
        self.assertTrue(any("Esiste già" in x for x in t))
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI LIBRO"], inputs={"Titolo": "", "Book ID / ASIN": "B0X"})
        self.assertTrue(any("Titolo: campo obbligatorio" in x for x in t))
        # marketplace: prezzo non valido, poi valido
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI MARKETPLACE"], inputs={**self.MPFORM, "Prezzo": "0"})
        self.assertIn("Il prezzo deve essere maggiore di zero", t)
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI MARKETPLACE"], inputs=self.MPFORM)
        self.assertIn("Marketplace aggiunto correttamente", t)
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI MARKETPLACE"], inputs=self.MPFORM)                 # stesso marketplace
        self.assertTrue(any("ha già il marketplace US" in x for x in t))
        # campagna dentro il marketplace US (nuova struttura nidificata per i libri nuovi)
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI CAMPAGNA"], inputs={self.CID: "PAGE_EXACT"})
        self.assertIn("Campagna aggiunta correttamente", t)
        self.assertTrue((self.ws.input_dir / "B0PAGE0001" / "US" / "PAGE_EXACT" / "metadata.json").is_file())
        t, _ = self.p.run("7_Gestione_libri.py", press=["AGGIUNGI CAMPAGNA"], inputs={self.CID: "PAGE_EXACT"})
        self.assertTrue(any("esiste già" in x for x in t))
        t, _ = self.p.run("7_Gestione_libri.py")                                               # richiede conferma, mostra il pulsante
        self.assertTrue(any("CONFERMA AGGIORNAMENTO CAMPAGNA" in x for x in t))

    def test_import_page_full_guided_flow(self):
        self.new_book()
        self.new_campaign()
        up = Upload("t.csv", tcsv())
        before = tree(self.root / "INPUT")
        t, log = self.p.run("8_Importa_report.py", upload=up)                                     # niente clic: nulla scritto
        self.assertEqual(tree(self.root / "INPUT"), before)
        allt = "\n".join(t)
        for need in (U_NO_AMAZON, "Importa report", "Importazione guidata", "Importazione settimanale", "1 · Libro", "2 · Marketplace", "3 · Campagna",
                     "4 · Tipo di report", "5 · Tipo di periodo", "7 · File CSV", "Marketplace selezionato: US · USD", "Anteprima", "Snapshot ID previsto", "ESEGUI DRY-RUN",
                     "IMPORTA REPORT", "Dry-run (obbligatorio)", "ANALIZZA LIBRO"):
            self.assertIn(need, allt, need)
        for bad in ENGLISH_V2:
            self.assertNotIn(bad, allt, bad)
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["IMPORTA REPORT"])              # senza dry-run il pulsante e' disabilitato
        self.assertNotIn("Report importato", t)
        self.assertEqual(self.count("imports"), 0)
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["ESEGUI DRY-RUN"])
        self.assertTrue(any("DRY_RUN_OK" in x for x in t))
        self.assertEqual(self.count("imports"), 0)                                                 # il dry-run non importa
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["IMPORTA REPORT"])
        self.assertEqual(self.count("imports"), 1)
        t, _ = self.p.run("8_Importa_report.py", upload=up)                                        # dopo l'import mostra l'esito
        self.assertIn("Report importato", t)
        self.assertIn("IMPORTA UN ALTRO REPORT", t)
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["ANALIZZA LIBRO"])
        self.assertIn("Analisi completata", t)
        self.assertTrue(any("GENERA DECISIONI" in x for x in t))
        t, log = self.p.run("8_Importa_report.py", upload=up, press=["GENERA DECISIONI"])
        self.assertIn("Decisioni generate", t)
        self.assertTrue(any("VAI ALLA PANORAMICA" in x for x in t))
        self.assertTrue(any(name.endswith("page_link") and a[0] == "panoramica.py" for name, a, k in log))
        self.assertEqual((self.count("analysis_runs"), self.count("decision_runs")), (1, 1))

    def test_import_page_blocks_import_when_dry_run_is_not_valid(self):
        self.new_book()
        self.new_campaign()
        up = Upload("t.csv", tcsv())
        self.p.state["imp_rtype"] = "SEARCH_TERM"                                                  # tipo sbagliato per un file Target
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["ESEGUI DRY-RUN"])
        self.assertTrue(any(x.startswith("ERROR") for x in t))
        t, _ = self.p.run("8_Importa_report.py", upload=up, press=["IMPORTA REPORT"])
        self.assertEqual(self.count("imports"), 0)
        self.assertEqual(list((self.ws.input_dir / "B0TEST0001" / "C_EXACT").glob("*.csv")), [])

    def test_weekly_mode_page_shows_checklist_and_completion(self):
        self.new_book()
        self.new_campaign()
        self.p.state["imp_mode"] = "Importazione settimanale"
        t, _ = self.p.run("8_Importa_report.py")
        allt = "\n".join(t)
        for need in ("[ ] **Target Lifetime**", "[ ] **Search Term 65D**", "[ ] **Target 28D**", "[ ] **Search Term 28D**", "mancante"):
            self.assertTrue(need in allt or any(need in x for x in t), need)
        self.assertNotIn("Set report settimanale completo", allt)
        today = date.today()
        self.report("C_EXACT", "TARGETING", "LIFETIME", tcsv(1), "a.csv", d_to=today)
        self.report("C_EXACT", "SEARCH_TERM", "D65", scsv(1), "b.csv", d_to=today)
        self.report("C_EXACT", "TARGETING", "D28", tcsv(2), "c.csv", d_to=today)
        t, _ = self.p.run("8_Importa_report.py")
        self.assertTrue(any("[ ] **Search Term 28D**" in x for x in t))
        self.assertNotIn("Set report settimanale completo", t)
        self.report("C_EXACT", "SEARCH_TERM", "D28", scsv(2), "d.csv", d_to=today)
        t, _ = self.p.run("8_Importa_report.py")
        self.assertIn("Set report settimanale completo", t)
        self.assertTrue(any("[x] **Target Lifetime**" in x for x in t))

    def test_weekly_prefill_switches_to_guided_mode(self):
        self.new_book()
        self.new_campaign()
        self.p.state["imp_mode"] = "Importazione settimanale"
        _, log = self.p.run("8_Importa_report.py")
        btn = [(a, k) for name, a, k in log if name.endswith("button") and a and a[0].startswith("Carica Target 28D")][0]
        k = btn[1]
        k["on_click"](*k["args"])
        self.assertEqual((self.p.state["imp_mode"], self.p.state["imp_campaign"], self.p.state["imp_rtype"], self.p.state["imp_period"]),
                         ("Importazione guidata", "C_EXACT", "TARGETING", "D28"))


U_NO_AMAZON = "Nessuna modifica viene eseguita su Amazon Ads."


class NavigationTests(unittest.TestCase):
    def test_new_pages_are_in_the_menu_with_italian_titles(self):
        import ui_labels as U
        titles = dict((p, t) for p, t in U.NAV_PAGES)
        self.assertEqual((titles["pages/7_Gestione_libri.py"], titles["pages/8_Importa_report.py"]), ("Gestione libri", "Importa report"))
        for p, _ in U.NAV_PAGES:
            self.assertTrue((ROOT / "DASHBOARD" / p).is_file(), p)
        self.assertEqual(U.OPS_NO_AMAZON, U_NO_AMAZON)
        self.assertEqual(U.OPS_WEEKLY_COMPLETE, "Set report settimanale completo")


if __name__ == "__main__":
    unittest.main()
