"""REPORT GLOBALI AMAZON ADS: parser (formato reale italiano), parcheggio, mappa ID campagna -> libro, smistamento, ignora, libro nuovo, MISSING_7D, dedup period-aware,
parent target dei Search Term, identita' AUTO/prodotto, termine vuoto, non regressione. Workspace temporanei: mai il DB reale ne' books.json reale."""
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads.db import SCHEMA_VERSION, connect
from ops import global_import as GI
from ops import workspace

BOOK_A, BOOK_B = "B0ROUTE660", "B0RARECOIN"
BOOKS = {"books": [
    {"book_id": BOOK_A, "title": "Route 66 Day by Day", "active": True,
     "marketplaces": [{"marketplace": "US", "currency": "USD", "price": 22.99, "royalty_per_copy": 9.0, "target_acos": 35.0, "phase": "GROWTH", "active": True}]},
    {"book_id": BOOK_B, "title": "Rare Coins Book for Beginners", "active": True,
     "marketplaces": [{"marketplace": "US", "currency": "USD", "price": 23.99, "royalty_per_copy": 11.0, "target_acos": 35.0, "phase": "GROWTH", "active": True}]}]}

TG_HEAD = ["Valuta budget", "Intervallo di date", "ID account inserzionista", "Nome account inserzionista", "ID portfolio", "Nome portfolio", "ID della campagna", "Nome della campagna",
           "ID del gruppo di annunci", "Nome del gruppo di annunci", "Targeting", "Tipo di corrispondenza del targeting", "ID target", "Offerta per il targeting", "Tipo di target",
           "Stato del target", "Impressioni", "Clic", "CTR", "Costo totale", "Acquisti", "Vendite", "Unità vendute", "ROAS"]
ST_HEAD = ["Valuta budget", "Intervallo di date", "ID account inserzionista", "Nome account inserzionista", "ID portfolio", "Nome portfolio", "ID della campagna", "Nome della campagna",
           "ID del gruppo di annunci", "Nome del gruppo di annunci", "Termine di ricerca", "ID target", "Targeting", "Tipo di corrispondenza del targeting", "Impressioni", "Clic", "CTR",
           "Costo totale", "Acquisti", "Vendite", "Unità vendute", "ROAS"]
RANGE30, RANGE7 = "14 set 2026 - 13 ott 2026", "7 ott 2026 - 13 ott 2026"


def xl(v):
    return f'="{v}"'


def csv_text(head, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(head)
    for r in rows:
        w.writerow([r.get(h, "") for h in head])
    return ("﻿" + out.getvalue()).encode("utf-8")


def camp(cid, name, pf_id="111", pf="ROUTE 66"):
    return {"ID portfolio": pf_id, "Nome portfolio": pf, "ID della campagna": xl(cid), "Nome della campagna": name, "ID del gruppo di annunci": xl(cid + "9"),
            "Nome del gruppo di annunci": name + " AG", "Valuta budget": "USD", "ID account inserzionista": xl("amzn1.ads-account.g.x"), "Nome account inserzionista": "Acc"}


def tg(c, target, match, tid, clicks, orders=0, spend=None, sales=None, rng=RANGE30, state="ENABLED", bid="0.50", impr=None):
    spend = (clicks * 0.5 if spend is None else spend)
    sales = (orders * 22.99 if sales is None else sales)
    return {**c, "Intervallo di date": rng, "Targeting": target, "Tipo di corrispondenza del targeting": match, "ID target": tid, "Offerta per il targeting": bid, "Stato del target": state,
            "Impressioni": str(impr if impr is not None else max(clicks * 40, 1)), "Clic": str(clicks), "CTR": "0.0000%", "Costo totale": f"{spend:.2f}", "Acquisti": str(orders),
            "Vendite": f"{sales:.2f}", "Unità vendute": str(orders), "ROAS": "0.00000"}


def st_row(c, term, target, match, tid, clicks, orders=0, spend=None, sales=None, rng=RANGE30):
    spend = (clicks * 0.5 if spend is None else spend)
    sales = (orders * 22.99 if sales is None else sales)
    return {**c, "Intervallo di date": rng, "Termine di ricerca": term, "ID target": tid, "Targeting": target, "Tipo di corrispondenza del targeting": match,
            "Impressioni": str(max(clicks * 40, 1)), "Clic": str(clicks), "CTR": "0.0000%", "Costo totale": f"{spend:.2f}", "Acquisti": str(orders), "Vendite": f"{sales:.2f}",
            "Unità vendute": str(orders), "ROAS": "0.00000"}


C_PH = camp("176948463003177", "FRASE ROUTE66 - 5/8/2026 08:11:33.222")           # ID a 15 cifre
C_AU = camp("206528816350500", "AUTO ROUTE66 - 7/9/2026 09:30:04.968")
C_EX = camp("12345678901234567890", "ESATTA ROUTE66", "111", "ROUTE 66")              # 20 cifre: perderebbe precisione come numero
C_RC = camp("555000111222333", "FRASE RARE COINS", "222", "rare coin")
C_NEW = camp("999000999000999", "FRASE SCONOSCIUTA", "333", "Portfolio Nuovo")


def targeting_30(extra=()):
    return csv_text(TG_HEAD, [tg(C_PH, "route 66", "PHRASE", "1001", 80, 7, 90.0, 280.0), tg(C_AU, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 30, 2, 20.0, 60.0),
                              tg(C_AU, "loose-match", "TARGETING_EXPRESSION_PREDEFINED", "1003", 22, 1, 15.0, 22.99), tg(C_AU, "complements", "TARGETING_EXPRESSION_PREDEFINED", "1004", 12, 0, 8.0, 0.0),
                              tg(C_AU, "substitutes", "TARGETING_EXPRESSION_PREDEFINED", "1005", 9, 1, 6.0, 22.99), tg(C_EX, "route 66 map", "EXACT", "1006", 15, 3, 12.0, 70.0),
                              tg(C_RC, "rare coin", "PHRASE", "2001", 40, 4, 40.0, 96.0), tg(C_RC, 'asin="B0ABCDEFGH"', "TARGETING_EXPRESSION", "2002", 25, 3, 20.0, 70.0),
                              *extra])


def targeting_7(clicks_route=24, zero_target=None, skip_au=False):
    rows = [tg(C_PH, "route 66", "PHRASE", "1001", clicks_route, 1, 20.0, 22.99, RANGE7), tg(C_EX, "route 66 map", "EXACT", "1006", 5, 1, 4.0, 22.99, "9 ott 2026 - 13 ott 2026"),
            tg(C_RC, "rare coin", "PHRASE", "2001", 12, 1, 10.0, 23.99, RANGE7)]
    if not skip_au:
        rows.append(tg(C_AU, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 14, 2, 9.0, 46.0, RANGE7))
    if zero_target:
        rows.append(tg(C_AU, "complements", "TARGETING_EXPRESSION_PREDEFINED", "1004", 0, 0, 0.0, 0.0, "13 ott 2026 - 13 ott 2026", impr=30))
    return csv_text(TG_HEAD, rows)


def search_30(extra=()):
    return csv_text(ST_HEAD, [st_row(C_PH, "route 66", "route 66", "PHRASE", "1001", 30, 3, 30.0, 70.0), st_row(C_PH, "route 66 travel guide", "route 66", "PHRASE", "1001", 25, 4, 22.0, 92.0),
                              st_row(C_AU, "route 66 road trip", "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 10, 2, 8.0, 46.0),
                              st_row(C_AU, "state parks guide", "loose-match", "TARGETING_EXPRESSION_PREDEFINED", "1003", 6, 1, 5.0, 22.99),
                              st_row(C_AU, "route 66 books", "complements", "TARGETING_EXPRESSION_PREDEFINED", "1004", 4, 0, 3.0, 0.0),
                              st_row(C_AU, "map of route 66", "substitutes", "TARGETING_EXPRESSION_PREDEFINED", "1005", 5, 1, 4.0, 22.99),
                              st_row(C_AU, "", "loose-match", "TARGETING_EXPRESSION_PREDEFINED", "1003", 3, 0, 2.0, 0.0),                    # termine di ricerca VUOTO
                              st_row(C_RC, "rare coin book", "rare coin", "PHRASE", "2001", 20, 2, 18.0, 48.0), st_row(C_PH, "rare coin book", "route 66", "PHRASE", "1001", 5, 0, 4.0, 0.0),
                              st_row(C_RC, "b0abcdefgh", 'asin="B0ABCDEFGH"', "TARGETING_EXPRESSION", "2002", 20, 6, 15.0, 144.0),
                              *extra])


def search_7():
    return csv_text(ST_HEAD, [st_row(C_PH, "route 66", "route 66", "PHRASE", "1001", 8, 1, 8.0, 22.99, RANGE7), st_row(C_AU, "route 66 road trip", "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 6, 1, 5.0, 23.0, RANGE7)])


class WsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("CONFIG", "DATABASE", "INPUT", "LOGS", "OUTPUT"):
            (self.root / d).mkdir()
        (self.root / "CONFIG" / "books.json").write_text(json.dumps(BOOKS), encoding="utf-8")
        for f in ("rules.json", "settings.json", "column_mapping.json"):
            (self.root / "CONFIG" / f).write_bytes((ROOT / "CONFIG" / f).read_bytes())
        self.old = os.environ.pop("KDP_ADS_DB", None)
        self.ws = workspace.from_root(self.root)
        connect(self.ws.db_path).close()
        self.books_hash = hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest()

    def tearDown(self):
        if self.old is not None:
            os.environ["KDP_ADS_DB"] = self.old
        self.tmp.cleanup()

    def db(self):
        c = sqlite3.connect(self.ws.db_path)
        c.row_factory = sqlite3.Row
        return c

    def q(self, sql, *p):
        c = self.db()
        try:
            return [dict(r) for r in c.execute(sql, p)]
        finally:
            c.close()

    def assoc(self, cid, book, internal=None, ctype=None):
        r = GI.associate(self.ws, cid, book, internal, ctype, recalc=False)
        self.assertTrue(r.ok, r.errors)
        return r


class ParserTests(unittest.TestCase):
    def test_real_italian_format_and_excel_ids_are_strings_without_precision_loss(self):
        p = G.parse_global_report("a.csv", targeting_30())
        self.assertEqual(p["report_type"], "TARGETING")
        self.assertEqual(p["currency"], "USD")
        ids = {r["campaign_id"] for r in p["rows"]}
        self.assertIn("176948463003177", ids)
        self.assertIn("12345678901234567890", ids)                                    # 20 cifre: un float le avrebbe arrotondate
        self.assertTrue(all(isinstance(i, str) and i.isdigit() for i in ids))
        self.assertEqual(G.clean_id('="176948463003177"'), "176948463003177")
        self.assertEqual(G.clean_id("1769"), "1769")
        self.assertIsNone(G.clean_id(""))
        r = next(x for x in p["rows"] if x["target_id"] == "1001")
        self.assertEqual((r["targeting"], r["match_type_raw"], r["match_type"], r["bid"], r["target_state"]), ("route 66", "PHRASE", "A frase", 0.5, "ENABLED"))
        self.assertEqual(r["portfolio_name"], "ROUTE 66")

    def test_report_type_detection_and_errors(self):
        self.assertEqual(G.parse_global_report("s.csv", search_30())["report_type"], "SEARCH_TERM")
        for bad, name in ((b"", "x.csv"), (b"a,b\n1,2\n", "x.csv"), (targeting_30(), "x.xlsx")):
            with self.assertRaises(G.GlobalReportError):
                G.parse_global_report(name, bad)

    def test_italian_dates_and_row_ranges(self):
        self.assertEqual(G.parse_it_date("13 ott 2026"), date(2026, 10, 13))
        self.assertEqual(G.parse_range("7 ott 2026 - 13 ott 2026"), (date(2026, 10, 7), date(2026, 10, 13)))
        self.assertEqual(G.parse_range("13 ott 2026 - 13 ott 2026"), (date(2026, 10, 13), date(2026, 10, 13)))
        self.assertEqual(G.parse_range("boh"), (None, None))

    def test_snapshot_is_the_slot_window_not_the_row_interval(self):
        p = G.parse_global_report("t7.csv", targeting_7())
        self.assertGreater(len({r["row_from"] for r in p["rows"]}), 1)                # righe con intervalli diversi nello stesso file
        self.assertEqual(G.snapshot_dates(p["rows"], 7), ("2026-10-07", "2026-10-13"))
        self.assertEqual(G.snapshot_dates(p["rows"], 30), ("2026-09-14", "2026-10-13"))
        self.assertEqual(G.snapshot_dates(p["rows"], 7, date(2026, 10, 12)), ("2026-10-06", "2026-10-12"))

    def test_numbers_and_percentages(self):
        self.assertEqual((G.num("0.12"), G.num("1,234.50"), G.num("100.0000%"), G.num("0,12"), G.num(""), G.num("x")), (0.12, 1234.5, 100.0, 0.12, None, None))

    def test_empty_search_term_is_kept_and_flagged_never_nan(self):
        p = G.parse_global_report("s.csv", search_30())
        empties = [r for r in p["rows"] if r["empty_search_term"]]
        self.assertEqual(len(empties), 1)
        self.assertIsNone(empties[0]["search_term"])
        self.assertEqual(p["empty_search_term_rows"], 1)
        self.assertFalse(any(str(r["search_term"]).lower() == "nan" for r in p["rows"]))

    def test_campaign_type_detection_and_summary(self):
        s = G.campaign_summary(G.parse_global_report("t.csv", targeting_30())["rows"])
        self.assertEqual((s["176948463003177"]["campaign_type"], s["206528816350500"]["campaign_type"], s["12345678901234567890"]["campaign_type"]), ("PHRASE", "OTHER", "EXACT"))
        self.assertEqual(s["555000111222333"]["campaign_type"], "PHRASE")
        self.assertEqual(s["206528816350500"]["portfolio_name"], "ROUTE 66")


class StageAndMappingTests(WsBase):
    def test_multi_book_file_unknown_campaigns_become_unassigned_and_never_block(self):
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["counts"], {"UNASSIGNED": 4, "ASSIGNED": 0, "IGNORED": 0})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE amazon_campaign_id IS NOT NULL")[0]["n"], 0)       # niente smistato senza associazione
        un = GI.campaign_list(self.ws, "UNASSIGNED")
        self.assertEqual({u["amazon_campaign_id"] for u in un}, {"176948463003177", "206528816350500", "12345678901234567890", "555000111222333"})
        sug = {u["amazon_campaign_id"]: (u["suggested_book_id"], u["suggestion_confidence"]) for u in un}
        self.assertEqual(sug["176948463003177"], (BOOK_A, "alta"))                       # portfolio ROUTE 66 ~ titolo (solo suggerimento)
        self.assertEqual(sug["555000111222333"], (BOOK_B, "alta"))
        self.assertTrue(all(u["status"] == "UNASSIGNED" for u in un))                    # mai associato da solo

    def test_association_is_explicit_persistent_and_materializes_parked_data_without_reimport(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30()), "SEARCH_TERM_30D": ("s30.csv", search_30())}, recalc=False)
        r = self.assoc("176948463003177", BOOK_A)
        self.assertEqual(r.data["campaign_id"], "FRASE_ROUTE66")                        # id interno dal nome (senza data/ora automatica)
        t = self.q("SELECT * FROM target_rows WHERE amazon_campaign_id='176948463003177'")
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["book_id"], t[0]["campaign_id"], t[0]["marketplace"], t[0]["report_scope"], t[0]["date_from"], t[0]["date_to"]),
                         (BOOK_A, "FRASE_ROUTE66", "US", "DATE_RANGE", "2026-09-14", "2026-10-13"))
        self.assertEqual(t[0]["amazon_target_id"], "1001")
        self.assertEqual(self.q("SELECT COUNT(*) n FROM search_term_rows WHERE amazon_campaign_id='176948463003177'")[0]["n"], 3)
        m = self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id='176948463003177'")[0]
        self.assertEqual((m["status"], m["book_id"], m["campaign_id"]), ("ASSIGNED", BOOK_A, "FRASE_ROUTE66"))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE status='ASSIGNED'")[0]["n"], 1)         # le altre restano da associare
        with self.assertRaises(GS.GlobalImportError):                                    # niente riassegnazione silenziosa
            c = connect(self.ws.db_path)
            try:
                GS.associate(c, self.ws.settings, "176948463003177", BOOK_B)
            finally:
                c.close()

    def test_known_campaigns_import_automatically_and_new_ones_do_not_block_them(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        self.assoc("176948463003177", BOOK_A)
        self.assoc("206528816350500", BOOK_A)
        self.assoc("555000111222333", BOOK_B)
        extra = tg(C_NEW, "mystery kw", "PHRASE", "3001", 7, 1, 5.0, 22.99, RANGE7)
        r = GI.run_import(self.ws, {"TARGETING_7D": ("t7.csv", csv_text(TG_HEAD, [tg(C_PH, "route 66", "PHRASE", "1001", 24, 1, 20.0, 22.99, RANGE7),
                                                                                   tg(C_RC, "rare coin", "PHRASE", "2001", 12, 1, 10.0, 23.99, RANGE7), extra]))}, recalc=False)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["materialized"]["imports"], 2)                           # le 2 campagne note sono entrate subito (la terza nota non e' nel file)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE amazon_campaign_id='999000999000999'")[0]["n"], 0)
        new = self.q("SELECT status FROM campaign_book_mapping WHERE amazon_campaign_id='999000999000999'")[0]
        self.assertEqual(new["status"], "UNASSIGNED")                                    # la nuova resta in coda
        self.assertEqual(self.q("SELECT period_id FROM imports WHERE book_id=? AND report_type='TARGETING' ORDER BY period_id", BOOK_A)[-1]["period_id"], "2026-10-07__2026-10-13")

    def test_partial_import_35_known_3_new_1_ignored(self):
        many = [camp(str(700000000000000 + i), f"CAMP {i}", "444", "Calisthenics") for i in range(39)]
        data = csv_text(TG_HEAD, [tg(c, f"kw {i}", "PHRASE", str(5000 + i), 10, 1, 8.0, 22.99) for i, c in enumerate(many)])
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", data)}, recalc=False)
        ids = [str(700000000000000 + i) for i in range(39)]
        c = connect(self.ws.db_path)
        try:
            for i in ids[:35]:
                GS.associate(c, self.ws.settings, i, BOOK_A, "US", None, "PHRASE")
            GS.ignore(c, ids[38])
        finally:
            c.close()
        again = csv_text(TG_HEAD, [tg(c, f"kw {i}", "PHRASE", str(5000 + i), 11, 1, 8.0, 22.99, RANGE7) for i, c in enumerate(many)])
        r = GI.run_import(self.ws, {"TARGETING_7D": ("t7.csv", again)}, recalc=False)
        self.assertTrue(r.ok)
        self.assertEqual(r.data["counts"], {"UNASSIGNED": 3, "ASSIGNED": 35, "IGNORED": 1})
        self.assertEqual(r.data["materialized"]["imports"], 35)

    def test_ignore_is_persistent_reversible_and_deletes_nothing(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        rows_before = self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"]
        self.assertTrue(GI.ignore(self.ws, "555000111222333").ok)
        self.assertEqual(GI.counts(self.ws)["IGNORED"], 1)
        self.assertNotIn("555000111222333", {u["amazon_campaign_id"] for u in GI.campaign_list(self.ws, "UNASSIGNED")})
        GI.run_import(self.ws, {"TARGETING_7D": ("t7.csv", targeting_7())}, recalc=False)           # un nuovo report con la stessa campagna
        self.assertEqual(GI.counts(self.ws)["IGNORED"], 1)                                          # resta ignorata, non riappare come problema
        self.assertGreater(self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], rows_before)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_rows WHERE amazon_campaign_id='555000111222333'")[0]["n"], 3)       # dati conservati (2 righe 30D + 1 riga 7D)
        self.assertFalse(GI.ignore(self.ws, "nonesiste").ok)
        self.assertTrue(GI.unignore(self.ws, "555000111222333").ok)
        self.assertEqual(GI.counts(self.ws), {"UNASSIGNED": 4, "ASSIGNED": 0, "IGNORED": 0})
        self.assertTrue(self.assoc("555000111222333", BOOK_B))                                       # IGNORE rimovibile e poi associabile

    def test_new_book_created_with_derived_break_even_and_campaign_associated(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        r = GI.create_book_and_associate(self.ws, {"title": "Libro Nuovo", "book_id": "B0NEWBOOK1", "marketplace": "US", "price": "22,99", "royalty": "9", "target_acos": "35"},
                                         ["176948463003177"], recalc=False)
        self.assertTrue(r.ok, r.errors)
        book = {b["book_id"]: b for b in json.loads((self.root / "CONFIG" / "books.json").read_text(encoding="utf-8"))["books"]}["B0NEWBOOK1"]
        mp = book["marketplaces"][0] if book.get("marketplaces") else book
        self.assertAlmostEqual(mp["break_even_acos"], round(9 / 22.99 * 100, 2), places=2)
        self.assertAlmostEqual(mp["break_even_acos"], 39.15, places=2)
        m = self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id='176948463003177'")[0]
        self.assertEqual((m["status"], m["book_id"]), ("ASSIGNED", "B0NEWBOOK1"))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE book_id='B0NEWBOOK1'")[0]["n"], 1)
        self.assertEqual(GI.counts(self.ws)["UNASSIGNED"], 3)                                         # le altre restano in attesa
        dup = GI.create_book_and_associate(self.ws, {"title": "X", "book_id": "B0NEWBOOK1", "marketplace": "US", "price": "10", "royalty": "4", "target_acos": "35"}, [], recalc=False)
        self.assertFalse(dup.ok)                                                                       # mai sovrascrivere un libro

    def test_same_campaign_id_with_new_name_keeps_the_association(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        self.assoc("176948463003177", BOOK_A)
        renamed = {**C_PH, "Nome della campagna": "NOME CAMBIATO"}
        GI.run_import(self.ws, {"TARGETING_7D": ("t7.csv", csv_text(TG_HEAD, [tg(renamed, "route 66", "PHRASE", "1001", 24, 1, 20.0, 22.99, RANGE7)]))}, recalc=False)
        m = self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id='176948463003177'")[0]
        self.assertEqual((m["status"], m["book_id"], m["campaign_id"], m["campaign_name_last_seen"]), ("ASSIGNED", BOOK_A, "FRASE_ROUTE66", "NOME CAMBIATO"))
        self.assertEqual(self.q("SELECT COUNT(DISTINCT campaign_id) n FROM target_rows WHERE book_id=?", BOOK_A)[0]["n"], 1)          # stessa campagna interna
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping")[0]["n"], 4)


class DedupAndWindowTests(WsBase):
    def test_same_file_twice_never_duplicates(self):
        files = {"TARGETING_30D": ("t30.csv", targeting_30()), "SEARCH_TERM_30D": ("s30.csv", search_30())}
        GI.run_import(self.ws, files, recalc=False)
        self.assoc("176948463003177", BOOK_A)
        n = (self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"], self.q("SELECT COUNT(*) n FROM imports")[0]["n"])
        r = GI.run_import(self.ws, files, recalc=False)
        self.assertFalse(r.ok)                                                                         # nessun file nuovo
        self.assertTrue(any("già importato" in x for x in r.data["notes"]))
        self.assertEqual((self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"], self.q("SELECT COUNT(*) n FROM imports")[0]["n"]), n)
        c = connect(self.ws.db_path)
        try:
            self.assertEqual(GS.materialize(c, self.ws.settings)["rows"], 0)                          # smistamento idempotente
        finally:
            c.close()

    def test_7d_and_30d_coexist_as_distinct_snapshots_and_7d_is_short_trend(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30()), "TARGETING_7D": ("t7.csv", targeting_7())}, recalc=False)
        self.assoc("176948463003177", BOOK_A)
        per = self.q("SELECT period_id, row_count FROM imports WHERE book_id=? AND report_type='TARGETING' ORDER BY period_id", BOOK_A)
        self.assertEqual([p["period_id"] for p in per], ["2026-09-14__2026-10-13", "2026-10-07__2026-10-13"])
        self.assertEqual(len({r["snapshot_id"] for r in self.q("SELECT snapshot_id FROM imports")}), 2)
        from kdp_ads.snapshots import window_role
        rules = json.loads((self.root / "CONFIG" / "rules.json").read_text(encoding="utf-8"))["snapshots"]
        self.assertEqual([window_role("DATE_RANGE", d[0], d[1], rules) for d in (("2026-09-14", "2026-10-13"), ("2026-10-07", "2026-10-13"))], ["TACTICAL", "SHORT_TREND"])

    def test_different_file_same_period_is_a_revision_never_a_silent_overwrite(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30())}, recalc=False)
        other = targeting_30([tg(C_NEW, "zz", "PHRASE", "3999", 3, 0)])
        pv = GI.preview(self.ws, {"TARGETING_30D": ("t30b.csv", other)})["TARGETING_30D"]["preview"]
        self.assertEqual(pv["status"], "REVISIONE")
        self.assertFalse(pv["importable"])
        self.assertTrue(pv["revision_available"])
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30b.csv", other)}, recalc=False)
        self.assertFalse(r.ok)
        self.assertEqual(r.data["revisions_pending"], ["TARGETING_30D"])
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], 1)

    def test_same_keyword_and_search_term_in_different_campaigns_stay_separate(self):
        GI.run_import(self.ws, {"SEARCH_TERM_30D": ("s30.csv", search_30())}, recalc=False)
        self.assoc("176948463003177", BOOK_A)
        self.assoc("555000111222333", BOOK_B)
        rows = self.q("SELECT book_id, campaign_id, target, search_term, clicks FROM search_term_rows WHERE search_term='rare coin book' ORDER BY book_id")
        self.assertEqual([(r["book_id"], r["target"], r["clicks"]) for r in rows], [(BOOK_B, "rare coin", 20.0), (BOOK_A, "route 66", 5.0)])      # stesso termine in due campagne: due righe distinte

    def test_preview_reports_counts_without_writing(self):
        before = self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"]
        pv = GI.preview(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30()), "SEARCH_TERM_7D": ("s7.csv", search_7()), "TARGETING_7D": ("t30.csv", search_30())})
        p = pv["TARGETING_30D"]["preview"]
        self.assertEqual((p["report_label"], p["window_label"], p["rows"], p["campaigns"], p["portfolios"], p["assigned"], p["new"], p["status"]),
                         ("Targeting", "RECENT_30D", 8, 4, 2, 0, 4, "NUOVO"))
        self.assertEqual((p["date_from"], p["date_to"]), ("2026-09-14", "2026-10-13"))
        self.assertTrue(p["importable"])
        self.assertTrue(pv["TARGETING_7D"]["preview"]["problems"])                                    # un Search Term nello slot Targeting
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], before)
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)


class SearchTermSemanticsTests(WsBase):
    def setUp(self):
        super().setUp()
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30()), "SEARCH_TERM_30D": ("s30.csv", search_30())}, recalc=False)
        for cid, b in (("176948463003177", BOOK_A), ("206528816350500", BOOK_A), ("12345678901234567890", BOOK_A), ("555000111222333", BOOK_B)):
            self.assoc(cid, b)

    def test_search_terms_carry_their_parent_target_from_the_report_ids(self):
        r = self.q("SELECT target, match_type, amazon_target_id, amazon_ad_group_id, amazon_campaign_id FROM search_term_rows WHERE search_term='route 66 travel guide'")[0]
        self.assertEqual((r["target"], r["match_type"], r["amazon_target_id"], r["amazon_campaign_id"]), ("route 66", "A frase", "1001", "176948463003177"))
        self.assertTrue(r["amazon_ad_group_id"])

    def test_auto_identities_are_preserved_and_campaign_is_typed_other(self):
        t = {r["target"] for r in self.q("SELECT target FROM target_rows WHERE campaign_type='OTHER'")}
        self.assertEqual(t, {"close-match", "loose-match", "complements", "substitutes"})
        self.assertEqual({r["target"] for r in self.q("SELECT target FROM search_term_rows WHERE campaign_type='OTHER'")}, {"close-match", "loose-match", "complements", "substitutes"})
        from kdp_ads.metrics import target_type
        self.assertEqual({target_type(x, "OTHER") for x in t}, {"AUTO_TARGETING_GROUP"})             # restano gruppi Auto, mai keyword manuali

    def test_product_target_identity_is_kept(self):
        r = self.q("SELECT target, match_type FROM target_rows WHERE target LIKE 'asin=%'")[0]
        self.assertEqual(r["target"], 'asin="B0ABCDEFGH"')
        from kdp_ads.metrics import target_type
        self.assertEqual(target_type(r["target"], "COMPETITOR"), "PRODUCT_TARGET")

    def test_empty_search_term_is_parked_but_never_reaches_the_engine_tables(self):
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_rows WHERE empty_search_term=1")[0]["n"], 1)
        self.assertEqual(self.q("SELECT materialized FROM global_report_rows WHERE empty_search_term=1")[0]["materialized"], 2)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM search_term_rows WHERE search_term IS NULL OR TRIM(search_term)='' OR LOWER(search_term)='nan'")[0]["n"], 0)

    def test_product_target_and_empty_term_never_become_exact_or_negative(self):
        from kdp_ads.opportunities import compute_opportunities, load_inputs, make_ctx
        from kdp_ads.rules import load_rules
        from kdp_ads import registry
        c = connect(self.ws.db_path)
        try:
            book = registry.load_books(self.ws.books_path)[BOOK_B]
            view = registry.book_view(book, "US")
            ctx = make_ctx({**view, "royalty_per_copy": 11.0, "break_even_acos": 45.85}, load_rules(self.ws.rules_path))
            data = load_inputs(c, BOOK_B, "US", "US")
            res = compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], ctx)
        finally:
            c.close()
        types = {r["search_term"]: r["opportunity_type"] for r in res["records"]}
        self.assertNotIn("", types)
        self.assertNotEqual(types.get("b0abcdefgh"), "PROMOTE_TO_EXACT")                             # un ASIN non e' mai una keyword Exact
        self.assertNotIn("NEGATIVE_CANDIDATE", {t for k, t in types.items() if not k})

    def test_impossible_search_terms_still_flagged_by_the_existing_data_quality_logic(self):
        from kdp_ads.dataquality import is_impossible_search_term
        bad = st_row(C_PH, "ghost term", "route 66", "PHRASE", "1001", 0, 0, 5.0, 0.0)
        GI.run_import(self.ws, {"SEARCH_TERM_7D": ("s7.csv", csv_text(ST_HEAD, [{**bad, "Intervallo di date": RANGE7}]))}, recalc=False)
        row = self.q("SELECT clicks, spend, orders FROM search_term_rows WHERE search_term='ghost term'")[0]
        self.assertTrue(is_impossible_search_term(row))                                               # conservata e riconosciuta come dato incoerente


class DecisionIntegrationTests(WsBase):
    def _prepare(self, t7):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", targeting_30()), "SEARCH_TERM_30D": ("s30.csv", search_30()), "TARGETING_7D": ("t7.csv", t7)}, recalc=False)
        for cid in ("176948463003177", "206528816350500", "12345678901234567890"):
            self.assoc(cid, BOOK_A)
        res = GI.recalculate(self.ws, {(BOOK_A, "US")})
        self.assertTrue(res[0]["ok"], res)
        return {(d["campaign_id"], d["target"]): d for d in self.q("SELECT * FROM decisions WHERE run_id=(SELECT MAX(run_id) FROM decision_runs WHERE book_id=?)", BOOK_A)}

    def test_missing_7d_is_not_no_traffic_end_to_end_and_never_changes_the_decision(self):
        d = self._prepare(targeting_7(zero_target=True))
        zero = d[("AUTO_ROUTE66", "complements")]                                                    # riga 7D presente con 0 clic
        self.assertEqual((zero["trend_state"], zero["trend_clicks"]), ("NO_TRAFFIC_7D", 0))
        missing = d[("AUTO_ROUTE66", "loose-match")]                                                 # nessuna riga 7D per questo target
        self.assertEqual(missing["trend_state"], "MISSING_7D")
        self.assertIsNone(missing["trend_clicks"])
        self.assertNotEqual(missing["final_action"], "PAUSE_CANDIDATE")
        absent_camp = [x for x in d.values() if x["campaign_id"] == "ESATTA_ROUTE66" and x["target"] == "route 66 map"][0]
        self.assertIn(absent_camp["trend_state"], ("INSUFFICIENT_7D", "STABLE", "WATCH_RECENT"))      # presente nel 7D: stato reale
        phrase = d[("FRASE_ROUTE66", "route 66")]                                                    # 30D 32% (sotto l'obiettivo 35) + 7D forte e sfavorevole
        self.assertEqual(phrase["trend_state"], "WATCH_RECENT")                                      # 30D sano: il 7D da solo non dichiara il peggioramento
        self.assertFalse(phrase["final_action"].startswith("BID_DOWN"))

    def test_campaign_absent_from_the_7d_report_is_missing_not_zero(self):
        d = self._prepare(targeting_7(skip_au=True))
        auto = [x for x in d.values() if x["campaign_id"] == "AUTO_ROUTE66"]
        self.assertTrue(auto)
        self.assertEqual({x["trend_state"] for x in auto}, {"MISSING_7D"})

    def test_target_status_comes_from_the_report_when_there_is_no_lifetime(self):
        d = self._prepare(targeting_7())
        self.assertEqual({x["target_status"] for x in d.values()}, {"ENABLED"})                      # non UNKNOWN: "Stato del target" del report


class LegacyAndNonRegressionTests(WsBase):
    def test_new_tables_and_columns_are_additive_and_legacy_rows_stay_null(self):
        c = connect(self.ws.db_path)
        try:
            tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"global_report_imports", "global_report_rows", "campaign_book_mapping"} <= tabs)
            cols = {r[1] for r in c.execute("PRAGMA table_info(target_rows)")}
            self.assertTrue({"amazon_campaign_id", "amazon_ad_group_id", "amazon_target_id", "row_date_from", "row_date_to"} <= cols)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)       # DB nuovo: nasce direttamente alla versione corrente
        finally:
            c.close()

    def test_old_db_without_the_new_tables_migrates_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.db"
            c = connect(p)
            c.execute("INSERT INTO books (book_id,title) VALUES ('B1','Uno')")
            c.commit()
            for t in ("global_report_rows", "global_report_imports", "campaign_book_mapping"):
                c.execute(f"DROP TABLE {t}")
            c.commit()
            c.close()
            c = connect(p)
            self.assertTrue(GS.has_schema(c))
            self.assertEqual(c.execute("SELECT title FROM books WHERE book_id='B1'").fetchone()[0], "Uno")
            c.close()

    def test_global_modules_do_not_touch_the_ads_engine_files(self):
        for f in ("analysis.py", "rules.py", "opportunities.py", "growth.py", "importer.py"):
            src = (ROOT / "SCRIPTS" / "kdp_ads" / f).read_text(encoding="utf-8")
            self.assertNotIn("globalstore", src, f)
            self.assertNotIn("globalreport", src, f)
        for f in ("analyze.py", "decide.py", "import_csv.py"):
            src = (ROOT / "SCRIPTS" / f).read_text(encoding="utf-8")
            self.assertNotIn("global", src.lower().replace("global ", ""), f)

    def test_no_network_or_amazon_calls_in_the_new_modules(self):
        for f in (ROOT / "SCRIPTS" / "kdp_ads" / "globalreport.py", ROOT / "SCRIPTS" / "kdp_ads" / "globalstore.py", ROOT / "DASHBOARD" / "ops" / "global_import.py"):
            src = f.read_text(encoding="utf-8")
            for bad in ("requests", "urllib", "http.client", "socket", "selenium", "smtplib", "imaplib"):
                self.assertNotIn(bad, src, f"{f.name}: {bad}")

    def test_legacy_importer_still_works_and_new_columns_are_null(self):
        import test_importer as ti
        class T(ti.Base):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            t.add_book()
            r = t.one()
            self.assertEqual(r.status, "IMPORTED")
            self.assertEqual(t.conn.execute("SELECT COUNT(*) FROM target_rows WHERE amazon_campaign_id IS NULL").fetchone()[0], t.count("target_rows"))
        finally:
            t.tearDown()


DL = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
REAL = {"TARGETING_7D": "TEST_KDP_Targeting_7D.csv", "TARGETING_30D": "TEST_KDP_Targeting_30D.csv", "SEARCH_TERM_7D": "KDP_SearchTerm_7D.csv", "SEARCH_TERM_30D": "KDP_SearchTerm_30D.csv"}


@unittest.skipUnless(all((DL / n).is_file() for n in REAL.values()), "report globali reali assenti (Downloads)")
class RealFilesTests(unittest.TestCase):
    def test_four_real_files_parse_and_snapshots_are_slot_based(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes = {n: hashlib.sha256((DL / n).read_bytes()).hexdigest() for n in REAL.values()}
            out = {}
            for slot, n in REAL.items():
                p = G.parse_global_report(n, (DL / n).read_bytes())
                out[slot] = p
                self.assertEqual(p["report_type"], GS.SLOTS[slot][0])
                self.assertEqual(G.snapshot_dates(p["rows"], G.WINDOWS[GS.SLOTS[slot][1]])[1], "2026-10-13")
                self.assertTrue(all(isinstance(r["campaign_id"], str) and r["campaign_id"].isdigit() for r in p["rows"]))
            self.assertGreater(len({r["row_from"] for r in out["TARGETING_7D"]["rows"]}), 2)         # intervalli di riga diversi nello stesso file
            self.assertGreater(out["SEARCH_TERM_30D"]["empty_search_term_rows"], 0)
            self.assertEqual({n: hashlib.sha256((DL / n).read_bytes()).hexdigest() for n in REAL.values()}, hashes)    # file originali non modificati


if __name__ == "__main__":
    unittest.main()
