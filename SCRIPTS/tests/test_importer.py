"""Test con CSV SINTETICI in workspace temporanei. Non toccano INPUT, books.json ne' il DB reali."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kdp_ads.config import load_mapping
from kdp_ads.db import connect
from kdp_ads.importer import import_all
from kdp_ads.logs import setup_logger
from kdp_ads import registry

# ------------------------------------------------------------------ fixture CSV
TARGET_HDR = ("Start Date,End Date,Portfolio name,Currency,Campaign Name,Ad Group Name,Targeting,Match Type,"
              "Impressions,Clicks,Click-Thru Rate (CTR),Cost Per Click (CPC),Spend,14 Day Total Sales ,"
              "Total Advertising Cost of Sales (ACOS),Total Return on Advertising Spend (ROAS),"
              "14 Day Total Orders (#),14 Day Total Units (#),14 Day Conversion Rate")
TARGET_ROW = ('"Jan 1, 2026","Sep 30, 2026",,USD,Chair Tai Chi - EXACT,AG1,tai chi chair workout,EXACT,'
              '"1,200",12,1.00%,$0.85,$10.20,$34.00,30.00%,3.33,1,1,8.33%')


def _csv(*lines):
    return "\n".join(lines) + "\n"


IT_ST_KW = _csv(
    "Aggiunto come,Termine di ricerca del cliente,Parole chiave,Offerta per il targeting (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS",
    'Parola chiave: esatta,tai chi chair workout chart,tai chi chair workout chart,0.95,16872,254,"0,0151",241.81,0.95,24,455.76,"0,5306"',
    ',chair tai chi,tai chi chair,0.75,3375,122,"0,0361",124.99,1.02,17,322.83,"0,3872"')
IT_ST_COMP = _csv(
    "Aggiunto come,Prodotto corrispondente,Target per prodotto,Offerta per il targeting (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS",
    'Targeting per prodotto,b0ftfjl6rb,"asin=""B0FTFJL6RB""",0.9,273,16,"0,0586",12.93,0.81,5,94.95,"0,1362"')
IT_T_KW = _csv(
    "Stato,Parola chiave,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS",
    'ABILITATO,tai chi chair workout chart,Esatta,Attivo,0.95,165996,1761,"0,0106",2040.74,1.16,213,4044.87,"0,5045"')
IT_T_COMP = _csv(
    "Stato,Categorie e prodotti,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS",
    'ABILITATO,"asin=""B0FTFJL6RB""",—,Attivo,0.9,5001,86,"0,0172",61.51,0.72,12,227.88,"0,2699"')

PILOT = {"book_id": "1805566563", "title": "Chair Tai Chi for Weight Loss After 50", "marketplace": "US",
         "currency": "USD", "price": 19.99, "royalty_per_copy": 8.55, "target_acos": 35.0, "phase": "PROFIT_STABLE"}
OTHER = {"book_id": "B0OTHERBOOK", "title": "Another Book", "marketplace": "US", "currency": "USD",
         "price": 12.99, "royalty_per_copy": 4.10, "target_acos": 25.0, "phase": "LAUNCH"}


def meta_for(book_id, campaign_id, ctype, **over):
    m = {"book_id": book_id, "campaign_id": campaign_id, "campaign_name": campaign_id, "campaign_type": ctype,
         "marketplace": "US", "currency": "USD",
         "target_report_scope": "LIFETIME", "target_date_from": None, "target_date_to": "2026-09-30",
         "search_term_report_scope": "DATE_RANGE", "search_term_date_from": "2026-07-28",
         "search_term_date_to": "2026-09-30", "attribution_window": None}
    m.update(over)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = {"read_only": True, "db_path": str(root / "t.db"), "logs_dir": str(root / "logs"),
                         "input_dir": str(root / "INPUT"), "books_path": str(root / "books.json"),
                         "search_term_max_days": 65, "break_even_mismatch_tolerance_pp": 0.5}
        (root / "INPUT").mkdir()
        self.mapping = load_mapping()
        self.logger, _ = setup_logger(root / "logs", "test")
        self.conn = connect(self.settings["db_path"])

    def tearDown(self):
        for h in list(self.logger.handlers):
            h.close()
            self.logger.removeHandler(h)
        self.conn.close()
        self.tmp.cleanup()

    # helpers
    def add_book(self, book=PILOT):
        return registry.add_book(self.settings["books_path"], self.settings["input_dir"], dict(book))

    def campaign(self, book_id, cid, ctype, files=None, write_meta=True, **over):
        d = Path(self.settings["input_dir"]) / book_id / cid
        d.mkdir(parents=True, exist_ok=True)
        if write_meta:
            (d / "metadata.json").write_text(json.dumps(meta_for(book_id, cid, ctype, **over)), encoding="utf-8")
        for name, text in (files or {}).items():
            (d / name).write_text(text, encoding="utf-8")
        return d

    def run_import(self, **kw):
        return import_all(self.conn, self.settings, self.mapping, self.logger, **kw)

    def one(self, book_id="1805566563", cid="C_EXACT", ctype="EXACT", name="x.csv", text=IT_T_KW, **over):
        if book_id not in registry.load_books(self.settings["books_path"]):
            self.add_book()
        self.campaign(book_id, cid, ctype, {name: text}, **over)
        res = self.run_import()
        self.assertEqual(len(res), 1)
        return res[0]

    def count(self, table, **where):
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        return self.conn.execute(sql, list(where.values())).fetchone()[0]


# ------------------------------------------------------------------ importer (English + parsing)
class ImporterTests(Base):
    def test_target_import_and_metadata(self):
        r = self.one(name="t.csv", text=TARGET_HDR + "\n" + TARGET_ROW + "\n")
        self.assertEqual(r.status, "IMPORTED", r.problems)
        imp = self.conn.execute("SELECT * FROM imports").fetchone()
        self.assertEqual((imp["report_type"], imp["campaign_type"], imp["report_scope"]), ("TARGETING", "EXACT", "LIFETIME"))
        self.assertEqual((imp["date_from"], imp["date_to"]), (None, "2026-09-30"))
        self.assertEqual((imp["attribution_window"], imp["attribution_source"]), ("14 Day", "headers"))
        self.assertEqual((imp["book_id"], imp["campaign_id"], imp["source_filename"]), ("1805566563", "C_EXACT", "t.csv"))
        row = self.conn.execute("SELECT * FROM target_rows").fetchone()
        self.assertEqual((row["impressions"], row["spend"], row["sales"], row["orders"]), (1200, 10.20, 34.0, 1))
        self.assertEqual(row["book_id"], "1805566563")

    def test_duplicate_file_skipped(self):
        self.one(name="t.csv", text=TARGET_HDR + "\n" + TARGET_ROW + "\n")
        res = self.run_import()
        self.assertEqual(res[0].status, "SKIPPED_DUPLICATE")
        self.assertEqual(self.count("target_rows"), 1)

    def test_search_term_scope_unknown_warns(self):
        hdr = "Campaign Name,Targeting,Match Type,Customer Search Term,Clicks,Spend,14 Day Total Sales,14 Day Total Orders (#)"
        r = self.one(name="s.csv", text=hdr + "\nC1,kw,PHRASE,my term,10,5.00,0,0\n", ctype="PHRASE", cid="C_PH",
                     search_term_report_scope="UNKNOWN", search_term_date_from=None, search_term_date_to=None)
        self.assertEqual(r.status, "IMPORTED", r.problems)
        self.assertIsNone(r.date_from)
        self.assertTrue([w for w in r.warnings if "UNKNOWN" in w])

    def test_search_term_over_65_days_warns(self):
        hdr = "Campaign Name,Targeting,Customer Search Term,Clicks,Spend,Sales,Orders"
        r = self.one(name="s.csv", text=hdr + "\nC1,kw,t,10,5.00,0,0\n", cid="C_LONG",
                     search_term_date_from="2026-07-27")
        self.assertTrue([w for w in r.warnings if "66 giorni" in w])

    def test_unknown_column_reported_not_guessed(self):
        r = self.one(name="u.csv", text=TARGET_HDR + ",Mystery Col\n" + TARGET_ROW + ",x\n")
        self.assertEqual(r.status, "IMPORTED", r.problems)
        self.assertEqual(r.unmapped, ["Mystery Col"])

    def test_mixed_attribution_windows_rejected(self):
        hdr = "Campaign Name,Targeting,Clicks,Spend,7 Day Total Sales,14 Day Total Orders (#)"
        self.assertEqual(self.one(name="m.csv", text=hdr + "\nC,k,1,1,0,0\n").status, "REJECTED")

    def test_missing_required_rejected(self):
        self.assertEqual(self.one(name="x.csv", text="Campaign Name,Targeting,Clicks\nC,k,1\n").status, "REJECTED")

    def test_non_us_number_rejected(self):
        r = self.one(name="n.csv", text=TARGET_HDR + "\n" + TARGET_ROW.replace("$0.85", '"0,85"') + "\n")
        self.assertEqual(r.status, "REJECTED")
        self.assertEqual(self.count("imports"), 0)

    def test_attribution_window_metadata_vs_header_conflict(self):
        r = self.one(name="t.csv", text=TARGET_HDR + "\n" + TARGET_ROW + "\n", attribution_window="7 Day")
        self.assertEqual(r.status, "REJECTED")

    def test_attribution_window_from_metadata_when_headers_silent(self):
        r = self.one(name="t.csv", text=IT_T_KW, attribution_window="14 Day")
        self.assertEqual(r.status, "IMPORTED", r.problems)
        self.assertEqual(self.conn.execute("SELECT attribution_source FROM imports").fetchone()[0], "metadata")

    def test_csv_date_column_conflicting_with_metadata_rejected(self):
        r = self.one(name="t.csv", text=TARGET_HDR + "\n" + TARGET_ROW.replace("Sep 30, 2026", "Aug 31, 2026") + "\n")
        self.assertEqual(r.status, "REJECTED")


# ------------------------------------------------------------------ report italiani
class ItalianReportTests(Base):
    def one_ok(self, name, text, ctype):
        r = self.one(name=name, text=text, ctype=ctype, cid=f"C_{ctype}")
        self.assertEqual(r.status, "IMPORTED", r.problems)
        return r

    def test_search_term_it_exact_phrase(self):
        r = self.one_ok("st_kw.csv", IT_ST_KW, "EXACT")
        self.assertEqual(r.report_type, "SEARCH_TERM")   # 'Parole chiave' non lo fa diventare Target
        self.assertEqual(r.mapped["search_term"], "Termine di ricerca del cliente")
        self.assertEqual(r.mapped["target"], "Parole chiave")
        rows = self.conn.execute("SELECT * FROM search_term_rows ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        a = rows[0]
        self.assertEqual((a["search_term"], a["target"], a["added_as"], a["bid"]),
                         ("tai chi chair workout chart", "tai chi chair workout chart", "Parola chiave: esatta", 0.95))
        self.assertEqual((a["clicks"], a["orders"], a["spend"], a["sales"]), (254, 24, 241.81, 455.76))
        self.assertAlmostEqual(a["acos_reported_pct"], 53.06, 2)
        self.assertAlmostEqual(a["ctr_reported_pct"], 1.51, 2)
        self.assertFalse(rows[1]["added_as"])
        self.assertEqual((a["date_from"], a["date_to"], a["report_scope"]), ("2026-07-28", "2026-09-30", "DATE_RANGE"))
        self.assertFalse(r.unmapped)
        self.assertFalse([w for w in r.warnings if "incoerente" in w])

    def test_target_it_exact_phrase(self):
        r = self.one_ok("t_kw.csv", IT_T_KW, "EXACT")
        self.assertEqual(r.report_type, "TARGETING")
        self.assertEqual(r.mapped["target"], "Parola chiave")
        self.assertEqual(r.mapped["match_type"], "Tipo di corrispondenza target")
        row = self.conn.execute("SELECT * FROM target_rows").fetchone()
        self.assertEqual((row["target"], row["match_type"], row["bid"], row["orders"]),
                         ("tai chi chair workout chart", "Esatta", 0.95, 213))
        self.assertEqual((row["report_scope"], row["date_from"], row["date_to"]), ("LIFETIME", None, "2026-09-30"))

    def test_search_term_it_competitor(self):
        r = self.one_ok("st_comp.csv", IT_ST_COMP, "COMPETITOR")
        self.assertEqual(r.report_type, "SEARCH_TERM")
        row = self.conn.execute("SELECT * FROM search_term_rows").fetchone()
        self.assertEqual(row["search_term"], "b0ftfjl6rb")          # ASIN non trasformato in keyword
        self.assertEqual(row["target"], 'asin="B0FTFJL6RB"')
        self.assertEqual(row["added_as"], "Targeting per prodotto")

    def test_target_it_competitor(self):
        r = self.one_ok("t_comp.csv", IT_T_COMP, "COMPETITOR")
        self.assertEqual(r.report_type, "TARGETING")
        row = self.conn.execute("SELECT * FROM target_rows").fetchone()
        self.assertEqual(row["target"], 'asin="B0FTFJL6RB"')

    def test_duplicate_stato_header_not_blocking(self):
        r = self.one_ok("t_kw.csv", IT_T_KW, "EXACT")
        self.assertEqual(r.duplicate_headers, ["Stato"])
        self.assertFalse(r.unmapped)
        raw = json.loads(self.conn.execute("SELECT raw_json FROM target_rows").fetchone()[0])
        self.assertEqual((raw["Stato"], raw["Stato__2"]), ("ABILITATO", "Attivo"))

    def test_duplicate_mapped_header_is_blocking(self):
        r = self.one(name="d.csv", text=IT_T_KW.replace("Vendite (USD),ACOS", "Vendite (USD),Clic", 1))
        self.assertEqual(r.status, "REJECTED")

    def test_other_currency_header_rejected(self):
        r = self.one(name="e.csv", text=IT_T_KW.replace("Costo totale (USD)", "Costo totale (EUR)"))
        self.assertEqual(r.status, "REJECTED")


# ------------------------------------------------------------------ registro libri
class BookRegistryTests(Base):
    def test_register_new_book(self):
        b = self.add_book()
        self.assertEqual(b["break_even_acos"], 42.77)
        self.assertTrue((Path(self.settings["input_dir"]) / "1805566563").is_dir())
        self.assertEqual(list(registry.load_books(self.settings["books_path"])), ["1805566563"])

    def test_duplicate_book_rejected(self):
        self.add_book()
        with self.assertRaises(registry.DuplicateError):
            self.add_book()
        self.assertEqual(len(registry.load_books(self.settings["books_path"])), 1)

    def test_invalid_book_values_rejected(self):
        for over in ({"price": 0}, {"price": -1}, {"royalty_per_copy": -0.01}, {"phase": "BOH"}):
            with self.assertRaises(registry.RegistryError, msg=over):
                self.add_book({**PILOT, **over})
        self.assertEqual(registry.load_books(self.settings["books_path"]), {})

    def test_royalty_zero_allowed(self):
        self.assertEqual(self.add_book({**PILOT, "royalty_per_copy": 0})["break_even_acos"], 0.0)

    def test_break_even_calculated_and_mismatch_warning(self):
        eco, warns = registry.book_economics({**PILOT, "break_even_acos": None})
        self.assertEqual((eco["break_even_acos"], warns), (42.77, []))
        eco, warns = registry.book_economics({**PILOT, "break_even_acos": 30.0})
        self.assertEqual((eco["break_even_acos"], eco["break_even_acos_calculated"]), (30.0, 42.77))
        self.assertEqual(len(warns), 1)

    def test_all_phases_supported(self):
        for i, ph in enumerate(registry.PHASES):
            self.add_book({**PILOT, "book_id": f"B{i}", "phase": ph})
        self.assertEqual(len(registry.load_books(self.settings["books_path"])), 3)

    def test_book_economics_synced_to_db(self):
        self.one(name="t.csv", text=IT_T_KW)
        b = self.conn.execute("SELECT * FROM books").fetchone()
        self.assertEqual((b["price"], b["royalty_per_copy"], b["break_even_acos"], b["target_acos"], b["phase"]),
                         (19.99, 8.55, 42.77, 35.0, "PROFIT_STABLE"))


# ------------------------------------------------------------------ campagne / validazioni / multi-book
class CampaignTests(Base):
    def test_create_campaign_and_duplicate_rejected(self):
        self.add_book()
        meta = meta_for("1805566563", "CHAIR_TAI_CHI_EXACT", "EXACT")
        folder = registry.create_campaign(self.settings["input_dir"], self.settings["books_path"], meta)
        self.assertTrue((folder / "metadata.json").exists())
        with self.assertRaises(registry.DuplicateError):
            registry.create_campaign(self.settings["input_dir"], self.settings["books_path"], meta)
        registry.create_campaign(self.settings["input_dir"], self.settings["books_path"],
                                 {**meta, "campaign_name": "renamed"}, overwrite=True)
        self.assertEqual(json.loads((folder / "metadata.json").read_text())["campaign_name"], "renamed")

    def test_create_campaign_unknown_book_or_bad_scope(self):
        with self.assertRaises(registry.RegistryError):
            registry.create_campaign(self.settings["input_dir"], self.settings["books_path"],
                                     meta_for("NOPE", "X", "EXACT"))
        self.add_book()
        with self.assertRaises(registry.RegistryError):
            registry.create_campaign(self.settings["input_dir"], self.settings["books_path"],
                                     meta_for("1805566563", "X", "EXACT", search_term_date_to=None))

    def test_missing_metadata_rejected(self):
        self.add_book()
        self.campaign("1805566563", "NO_META", "EXACT", {"t.csv": IT_T_KW}, write_meta=False)
        res = self.run_import()
        self.assertEqual([r.status for r in res], ["REJECTED"])
        self.assertIn("metadata.json mancante", res[0].problems[0])
        self.assertEqual(self.count("imports"), 0)

    def test_unknown_book_rejected(self):
        self.add_book()
        self.campaign("9999999999", "C1", "EXACT", {"t.csv": IT_T_KW})
        res = self.run_import()
        self.assertEqual(res[0].status, "REJECTED")
        self.assertIn("non presente in books.json", res[0].problems[0])
        self.assertEqual(self.count("target_rows"), 0)

    def test_metadata_ids_must_match_folders(self):
        self.add_book()
        self.add_book(OTHER)
        d = self.campaign("1805566563", "C1", "EXACT", {"t.csv": IT_T_KW})
        (d / "metadata.json").write_text(json.dumps(meta_for("B0OTHERBOOK", "C1", "EXACT")))
        r = self.run_import()[0]
        self.assertEqual(r.status, "REJECTED")
        self.assertTrue(any("book_id del metadata" in p for p in r.problems))
        (d / "metadata.json").write_text(json.dumps(meta_for("1805566563", "OTHER_ID", "EXACT")))
        r = self.run_import()[0]
        self.assertTrue(any("campaign_id del metadata" in p for p in r.problems))

    def test_marketplace_and_currency_must_match_book(self):
        self.add_book()
        self.campaign("1805566563", "C1", "EXACT", {"t.csv": IT_T_KW}, marketplace="DE")
        self.assertEqual(self.run_import()[0].status, "REJECTED")
        self.campaign("1805566563", "C1", "EXACT", {}, currency="EUR")
        self.assertEqual(self.run_import()[0].status, "REJECTED")

    def test_two_campaigns_same_type_same_book(self):
        self.add_book()
        self.campaign("1805566563", "EXACT_01", "EXACT", {"t.csv": IT_T_KW})
        self.campaign("1805566563", "EXACT_02", "EXACT", {"t.csv": IT_T_KW})   # stesso file, campagna diversa
        res = self.run_import()
        self.assertEqual([r.status for r in res], ["IMPORTED", "IMPORTED"])
        self.assertEqual(self.count("target_rows", book_id="1805566563", campaign_type="EXACT"), 2)
        self.assertEqual(self.count("target_rows", campaign_id="EXACT_01"), 1)
        self.assertEqual(self.count("target_rows", campaign_id="EXACT_02"), 1)
        self.assertEqual(self.count("campaigns"), 2)

    def test_same_type_on_two_books_and_separation(self):
        self.add_book()
        self.add_book(OTHER)
        self.campaign("1805566563", "EXACT_A", "EXACT", {"t.csv": IT_T_KW, "s.csv": IT_ST_KW})
        self.campaign("B0OTHERBOOK", "EXACT_A", "EXACT", {"t.csv": IT_T_KW})   # stesso campaign_id su altro libro
        res = self.run_import()
        self.assertTrue(all(r.status == "IMPORTED" for r in res), [r.problems for r in res])
        self.assertEqual(self.count("target_rows", book_id="1805566563"), 1)
        self.assertEqual(self.count("search_term_rows", book_id="1805566563"), 2)
        self.assertEqual(self.count("target_rows", book_id="B0OTHERBOOK"), 1)
        self.assertEqual(self.count("search_term_rows", book_id="B0OTHERBOOK"), 0)
        self.assertEqual(self.count("target_rows", campaign_type="EXACT"), 2)
        self.assertEqual(self.count("imports", book_id="B0OTHERBOOK"), 1)
        eco = {r["book_id"]: r["break_even_acos"] for r in self.conn.execute("SELECT * FROM books")}
        self.assertEqual(eco, {"1805566563": 42.77, "B0OTHERBOOK": 31.56})
        bad = self.conn.execute("""SELECT COUNT(*) FROM target_rows r JOIN imports i USING(import_id)
                                   WHERE r.book_id<>i.book_id OR r.campaign_id<>i.campaign_id""").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_filters_by_book_and_campaign(self):
        self.add_book()
        self.campaign("1805566563", "C1", "EXACT", {"t.csv": IT_T_KW})
        self.campaign("1805566563", "C2", "PHRASE", {"t.csv": IT_T_KW})
        res = self.run_import(only_campaign="C2")
        self.assertEqual([r.campaign_id for r in res], ["C2"])

    def test_inactive_book_skipped(self):
        self.add_book({**PILOT, "active": False})
        self.campaign("1805566563", "C1", "EXACT", {"t.csv": IT_T_KW})
        self.assertEqual(self.run_import()[0].status, "SKIPPED_INACTIVE")
        self.assertEqual(self.count("imports"), 0)

    def test_dry_run_writes_no_rows(self):
        self.add_book()
        self.campaign("1805566563", "C1", "EXACT", {"t.csv": IT_T_KW, "s.csv": IT_ST_KW})
        res = self.run_import(dry_run=True)
        self.assertEqual([r.status for r in res], ["DRY_RUN_OK", "DRY_RUN_OK"])
        self.assertEqual((self.count("imports"), self.count("target_rows"), self.count("search_term_rows")), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
