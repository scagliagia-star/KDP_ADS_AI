"""MULTI-MARKETPLACE: LIBRO -> MARKETPLACE -> CAMPAGNE. Stesso libro con US (USD) e UK (GBP): economia, cartelle, import, checklist,
analisi, decisioni e confronti cross-campaign sempre separati; compatibilita' con il libro legacy 1805566563 (US).
Workspace e DB temporanei; il progetto reale e' solo LETTO (copia del DB)."""
import hashlib
import json
import os
import shutil
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

import test_dashboard_v2 as td2  # noqa: E402
import test_rules as tr  # noqa: E402
from data_access.context import load_context  # noqa: E402
from data_access.repository import Repo  # noqa: E402
from kdp_ads import registry  # noqa: E402
from kdp_ads.analysis import analyze_book  # noqa: E402
from kdp_ads.db import SCHEMA_VERSION, connect  # noqa: E402
from kdp_ads.decision import build_decisions  # noqa: E402
from kdp_ads.importer import import_all  # noqa: E402
from kdp_ads.logs import setup_logger  # noqa: E402
from ops import catalog, importing, pipeline_ops, weekly  # noqa: E402

BID = "B0MULTI001"
US = dict(marketplace="US", currency="USD", price="19,99", royalty="8.55", target_acos="35", phase="PROFIT_STABLE")
UK = dict(marketplace="UK", currency="GBP", price="12.99", royalty="4.10", target_acos="25", phase="LAUNCH")
END = date(2026, 9, 30)


def gbp(b: bytes) -> bytes:
    return b.replace(b"(USD)", b"(GBP)")


def tgt(seed=0, uk=False):
    d = td2.tcsv(seed)
    return gbp(d) if uk else d


def st(seed=0, uk=False):
    d = td2.scsv(seed)
    return gbp(d) if uk else d


class MBase(td2.WsBase):
    """Libro con US (US_EXACT, US_PHRASE) e UK (UK_EXACT)."""

    def setUp(self):
        super().setUp()
        self._closers = []
        self.assertTrue(catalog.add_book_shell(self.ws, {"title": "Libro multi", "book_id": BID}).ok)
        for mp in (US, UK):
            r = catalog.add_marketplace(self.ws, {"book_id": BID, **mp})
            self.assertTrue(r.ok, r.errors)
        for cid, mp, ctype in (("US_EXACT", "US", "EXACT"), ("US_PHRASE", "US", "PHRASE"), ("UK_EXACT", "UK", "EXACT")):
            r = catalog.add_campaign(self.ws, {"book_id": BID, "marketplace": mp, "campaign_id": cid, "campaign_type": ctype})
            self.assertTrue(r.ok, r.errors)

    def rep(self, cid, rtype, kind, data, name, **kw):
        return self.report(cid, rtype, kind, data, name, book=BID, **kw)

    def fill_us_uk_data(self):
        """Stessi numeri in US e UK (cosi' si vede che solo la VALUTA/economia del marketplace cambia l'esito)."""
        self.rep("US_EXACT", "TARGETING", "LIFETIME", tgt(), "ut.csv")
        self.rep("US_EXACT", "SEARCH_TERM", "D65", st(), "us.csv")
        self.rep("US_PHRASE", "TARGETING", "LIFETIME", td2.tr.it_rows_target([("kw frase", 200, 30, 100, 800)]).encode(), "pt.csv")
        self.rep("US_PHRASE", "SEARCH_TERM", "D65", td2.tr.it_rows_st([("kw uno", "kw frase", 100, 20, 60, 400)]).encode(), "ps.csv")
        self.rep("UK_EXACT", "TARGETING", "LIFETIME", tgt(uk=True), "kt.csv")
        self.rep("UK_EXACT", "SEARCH_TERM", "D65", st(uk=True), "ks.csv")

    def analysis(self, mp):
        return analyze_book(self.db_conn(), self.ws.settings, self.ws.mapping, self.ws.rules_path, BID, self._lg(), mp)

    def db_conn(self):
        c = connect(self.ws.db_path)
        self._closers.append(c.close)
        return c

    def _lg(self):
        lg, _ = setup_logger(self.ws.logs_dir, "mm")
        self._closers.append(lambda: importing.close_logger(lg))
        return lg

    def tearDown(self):
        for f in getattr(self, "_closers", []):          # chiudo connessioni e log PRIMA di cancellare la cartella temporanea
            f()
        super().tearDown()


# ------------------------------------------------------------------ modello dati e economia
class ModelTests(MBase):
    def test_same_book_two_marketplaces_independent_economics(self):
        b = next(x for x in catalog.list_books(self.ws) if x["book_id"] == BID)
        mps = {m["marketplace"]: m for m in b["marketplaces"]}
        self.assertEqual(set(mps), {"US", "UK"})
        self.assertEqual((mps["US"]["currency"], mps["US"]["price"], mps["US"]["royalty_per_copy"], mps["US"]["target_acos"]), ("USD", 19.99, 8.55, 35.0))
        self.assertEqual((mps["UK"]["currency"], mps["UK"]["price"], mps["UK"]["royalty_per_copy"], mps["UK"]["target_acos"]), ("GBP", 12.99, 4.10, 25.0))
        self.assertEqual((mps["US"]["break_even_acos"], mps["UK"]["break_even_acos"]), (42.77, 31.56))   # royalty/prezzo di CIASCUN marketplace
        self.assertEqual((mps["US"]["campaigns"], mps["UK"]["campaigns"]), (2, 1))
        self.assertEqual(len(registry.load_books(self.ws.books_path)), 1)                              # un solo libro, non due
        raw = registry.load_books(self.ws.books_path)[BID]
        self.assertEqual((raw["title"], raw["book_id"]), ("Libro multi", BID))

    def test_break_even_uses_the_right_marketplace(self):
        raw = registry.load_books(self.ws.books_path)[BID]
        for mp, be in (("US", 42.77), ("UK", 31.56)):
            v = registry.book_view(raw, mp)
            self.assertEqual(registry.book_economics(v)[0]["break_even_acos"], be)
            self.assertEqual(registry.break_even_calc(float(v["price"]), float(v["royalty_per_copy"])), be)

    def test_duplicate_and_invalid_marketplace(self):
        r = catalog.add_marketplace(self.ws, {"book_id": BID, **US})
        self.assertFalse(r.ok)
        self.assertTrue(r.data["duplicate"])
        for over, needle in (({"marketplace": "USA1"}, "codice non valido"), ({"currency": "US"}, "codice non valido"),
                             ({"price": "0"}, "maggiore di zero"), ({"royalty": "99"}, "non può superare"), ({"target_acos": "200"}, "ACOS")):
            r = catalog.add_marketplace(self.ws, {"book_id": BID, **{**UK, "marketplace": "DE", "currency": "EUR"}, **over})
            self.assertFalse(r.ok, over)
            self.assertTrue(any(needle in e for e in r.errors), (over, r.errors))
        self.assertFalse(catalog.add_marketplace(self.ws, {"book_id": "NOPE", **UK}).ok)
        self.assertEqual({m["marketplace"] for m in catalog.list_marketplaces(self.ws, BID)}, {"US", "UK"})

    def test_ambiguous_marketplace_is_never_guessed(self):
        raw = registry.load_books(self.ws.books_path)[BID]
        with self.assertRaises(registry.RegistryError):
            registry.book_view(raw)                                         # 2 marketplace: serve sceglierne uno
        with self.assertRaises(registry.RegistryError):
            registry.book_view(raw, "DE")                                   # non configurato
        with self.assertRaises(registry.RegistryError):
            analyze_book(self.db_conn(), self.ws.settings, self.ws.mapping, self.ws.rules_path, BID, self._lg())
        with self.assertRaises(registry.RegistryError):
            build_decisions(self.db_conn(), self.ws.settings, self.ws.rules_path, BID)


# ------------------------------------------------------------------ campagne e cartelle
class CampaignAndFolderTests(MBase):
    def test_new_structure_has_separate_marketplace_folders(self):
        base = self.ws.input_dir / BID
        for mp, cid in (("US", "US_EXACT"), ("US", "US_PHRASE"), ("UK", "UK_EXACT")):
            self.assertTrue((base / mp / cid / "metadata.json").is_file(), (mp, cid))
            meta = json.loads((base / mp / cid / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["marketplace"], meta["currency"]), (mp, "USD" if mp == "US" else "GBP"))
        self.assertFalse((base / "US_EXACT").exists())                       # niente struttura piatta per i libri nuovi
        self.assertEqual([c["campaign_id"] for c in catalog.list_campaigns(self.ws, BID, "US")], ["US_EXACT", "US_PHRASE"])
        self.assertEqual([c["campaign_id"] for c in catalog.list_campaigns(self.ws, BID, "UK")], ["UK_EXACT"])

    def test_campaign_requires_a_valid_marketplace_and_unique_id(self):
        base = {"book_id": BID, "campaign_id": "X1", "campaign_type": "EXACT"}
        r = catalog.add_campaign(self.ws, base)                              # libro con 2 marketplace: manca il marketplace
        self.assertFalse(r.ok)
        self.assertTrue(any("Marketplace" in e for e in r.errors))
        self.assertFalse(catalog.add_campaign(self.ws, {**base, "marketplace": "DE"}).ok)          # non configurato
        self.assertFalse(catalog.add_campaign(self.ws, {**base, "marketplace": ""}).ok)
        r = catalog.add_campaign(self.ws, {**base, "campaign_id": "US_EXACT", "marketplace": "UK"})  # ID gia' usato in US
        self.assertFalse(r.ok)
        self.assertTrue(r.data["duplicate"])
        self.assertTrue(catalog.add_campaign(self.ws, {**base, "marketplace": "UK"}).ok)
        self.assertTrue((self.ws.input_dir / BID / "UK" / "X1" / "metadata.json").is_file())

    def test_metadata_marketplace_must_match_folder_and_book(self):
        folder = self.ws.input_dir / BID / "UK" / "UK_EXACT"
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        (folder / "metadata.json").write_text(json.dumps({**meta, "marketplace": "US", "currency": "USD"}), encoding="utf-8")   # US dentro la cartella UK
        (folder / "t.csv").write_bytes(tgt(uk=True))
        res = import_all(self.db_conn(), self.ws.settings, self.ws.mapping, self._lg(), only_book=BID, dry_run=True)
        bad = [r for r in res if r.campaign_id == "UK_EXACT"]
        self.assertEqual([r.status for r in bad], ["REJECTED"])
        self.assertTrue(any("cartella marketplace" in p for p in bad[0].problems))

    def test_same_campaign_id_in_two_marketplace_folders_is_rejected(self):
        d = self.ws.input_dir / BID / "UK" / "US_EXACT"                       # copia manuale con ID gia' usato in US
        d.mkdir(parents=True)
        meta = json.loads((self.ws.input_dir / BID / "UK" / "UK_EXACT" / "metadata.json").read_text(encoding="utf-8"))
        (d / "metadata.json").write_text(json.dumps({**meta, "campaign_id": "US_EXACT"}), encoding="utf-8")
        (d / "x.csv").write_bytes(tgt(5, uk=True))
        res = import_all(self.db_conn(), self.ws.settings, self.ws.mapping, self._lg(), only_book=BID, dry_run=True)
        self.assertTrue(any(r.status == "REJECTED" and any("due marketplace" in p for p in r.problems) for r in res))


# ------------------------------------------------------------------ import
class ImportTests(MBase):
    def test_us_import_does_not_land_in_uk_and_vice_versa(self):
        self.fill_us_uk_data()
        c = self.db()
        rows = {r[0]: r[1:] for r in c.execute("SELECT marketplace, COUNT(*), COUNT(DISTINCT campaign_id) FROM target_rows GROUP BY marketplace")}
        c.close()
        self.assertEqual(set(rows), {"US", "UK"})
        self.assertEqual((rows["US"][1], rows["UK"][1]), (2, 1))                              # campagne per marketplace
        c = self.db()
        self.assertEqual({r[0] for r in c.execute("SELECT DISTINCT campaign_id FROM target_rows WHERE marketplace='UK'")}, {"UK_EXACT"})
        self.assertEqual({r[0] for r in c.execute("SELECT DISTINCT campaign_id FROM search_term_rows WHERE marketplace='US'")}, {"US_EXACT", "US_PHRASE"})
        self.assertEqual({tuple(r) for r in c.execute("SELECT marketplace, currency FROM imports GROUP BY 1,2")}, {("US", "USD"), ("UK", "GBP")})
        bm = {r["marketplace"]: dict(r) for r in c.execute("SELECT * FROM book_marketplaces WHERE book_id=?", (BID,))}
        c.close()
        self.assertEqual((bm["US"]["royalty_per_copy"], bm["UK"]["royalty_per_copy"], bm["UK"]["break_even_acos"]), (8.55, 4.10, 31.56))

    def test_wrong_currency_for_the_marketplace_is_an_error(self):
        _, _, dry, _ = self.rep("UK_EXACT", "TARGETING", "LIFETIME", tgt(1), "usd_in_uk.csv", commit=False)          # header USD in campagna UK
        self.assertEqual((dry.status, dry.ok), ("ERROR", False))
        self.assertTrue(any("valuta" in p.lower() for p in dry.problems), dry.problems)
        _, _, dry, _ = self.rep("US_EXACT", "TARGETING", "LIFETIME", tgt(2, uk=True), "gbp_in_us.csv", commit=False)  # header GBP in campagna US
        self.assertFalse(dry.ok)
        self.assertEqual(self.count("imports"), 0)

    def test_dry_run_and_preview_show_the_marketplace(self):
        data = tgt()
        entry = importing.build_entry("a.csv", "TARGETING", importing.resolve_period("LIFETIME", None, END))
        self.assertEqual(importing.campaign_marketplace(self.ws, BID, "UK_EXACT"), "UK")
        self.assertEqual(importing.preview(BID, "UK_EXACT", "a.csv", entry, marketplace="UK")["Marketplace"], "UK")
        _, _, dry, _ = self.rep("US_EXACT", "TARGETING", "LIFETIME", data, "a.csv", commit=False)
        self.assertEqual((dry.status, dry.marketplace), ("DRY_RUN_OK", "US"))

    def test_marketplace_filter_in_import_all(self):
        d = self.ws.input_dir / BID / "UK" / "UK_EXACT"
        (d / "k.csv").write_bytes(tgt(8, uk=True))
        (self.ws.input_dir / BID / "US" / "US_EXACT" / "u.csv").write_bytes(tgt(9))
        res = import_all(self.db_conn(), self.ws.settings, self.ws.mapping, self._lg(), only_book=BID, dry_run=True, only_marketplace="UK")
        self.assertEqual({r.campaign_id for r in res}, {"UK_EXACT"})


# ------------------------------------------------------------------ checklist settimanale
class WeeklyTests(MBase):
    REF = date(2026, 10, 1)

    def four(self, cid, uk, base):
        self.rep(cid, "TARGETING", "LIFETIME", tgt(base, uk), f"{cid}a.csv")
        self.rep(cid, "SEARCH_TERM", "D65", st(base, uk), f"{cid}b.csv")
        self.rep(cid, "TARGETING", "D30", tgt(base + 1, uk), f"{cid}c.csv")
        self.rep(cid, "SEARCH_TERM", "D30", st(base + 1, uk), f"{cid}d.csv")

    def test_checklist_is_independent_per_marketplace(self):
        by = weekly.checklist_by_marketplace(self.ws, BID, self.REF)
        self.assertEqual(set(by), {"US", "UK"})
        self.assertEqual([(c["campaign_id"], c["marketplace"]) for c in by["US"]], [("US_EXACT", "US"), ("US_PHRASE", "US")])
        self.assertEqual([c["campaign_id"] for c in by["UK"]], ["UK_EXACT"])
        self.assertFalse(weekly.marketplace_complete(by["US"]) or weekly.marketplace_complete(by["UK"]))
        self.four("US_EXACT", False, 10)
        by = weekly.checklist_by_marketplace(self.ws, BID, self.REF)
        self.assertTrue(by["US"][0]["complete"] and not by["US"][1]["complete"])             # US_PHRASE manca ancora
        self.assertFalse(weekly.marketplace_complete(by["US"]))
        self.four("US_PHRASE", False, 20)
        by = weekly.checklist_by_marketplace(self.ws, BID, self.REF)
        self.assertTrue(weekly.marketplace_complete(by["US"]))                                # US completo...
        self.assertFalse(weekly.marketplace_complete(by["UK"]))                               # ...UK NO: i dati US non contano
        self.assertEqual(len(by["UK"][0]["missing"]), 4)
        self.four("UK_EXACT", True, 30)
        by = weekly.checklist_by_marketplace(self.ws, BID, self.REF)
        self.assertTrue(weekly.marketplace_complete(by["US"]) and weekly.marketplace_complete(by["UK"]))

    def test_snapshot_of_another_marketplace_never_counts(self):
        self.four("UK_EXACT", True, 40)
        by = weekly.checklist_by_marketplace(self.ws, BID, self.REF)
        self.assertEqual({i["status"] for c in by["US"] for i in c["items"]}, {"MANCANTE"})
        self.assertTrue(by["UK"][0]["complete"])

    def test_weekly_page_shows_per_marketplace_messages(self):
        self.four("US_EXACT", False, 10)
        self.four("US_PHRASE", False, 20)
        p = td2.Pages(self.root)
        p.state["imp_mode"] = "Importazione settimanale"
        t, _ = p.run("8_Importa_report.py", inputs={"Data di riferimento": date(2026, 10, 1)})
        self.assertIn("Set report completo per US", t)
        self.assertNotIn("Set report completo per UK", t)
        self.assertTrue(any("Marketplace UK · GBP" in x for x in t) and any("Marketplace US · USD" in x for x in t))


# ------------------------------------------------------------------ analisi, decisioni, cross-campaign
class AnalysisTests(MBase):
    def setUp(self):
        super().setUp()
        self.fill_us_uk_data()

    def test_analysis_per_marketplace_with_its_own_economics(self):
        us, uk = self.analysis("US"), self.analysis("UK")
        self.assertEqual({r["campaign_id"] for r in us.recs}, {"US_EXACT", "US_PHRASE"})
        self.assertEqual({r["campaign_id"] for r in uk.recs}, {"UK_EXACT"})
        self.assertEqual({r["marketplace"] for r in us.recs}, {"US"})
        self.assertEqual({r["marketplace"] for r in uk.recs}, {"UK"})
        self.assertEqual({(r["royalty_per_copy"], r["target_acos"], r["break_even_acos"], r["price"]) for r in us.recs}, {(8.55, 35.0, 42.77, 19.99)})
        self.assertEqual({(r["royalty_per_copy"], r["target_acos"], r["break_even_acos"], r["price"]) for r in uk.recs}, {(4.10, 25.0, 31.56, 12.99)})
        for an, n in ((us, 6), (uk, 3)):                                                       # 2+2+2 righe US, 1+1 UK (+ ST)
            self.assertEqual(len(an.recs), sum(t["rows"] for t in an.audit["totals"]))
        self.assertTrue({t["campaign_id"] for t in uk.audit["totals"]} <= {"UK_EXACT"})        # totali audit solo UK
        self.assertEqual(us.warnings, [])

    def test_same_numbers_different_economics_different_judgement(self):
        us = {(r["report_type"], r["campaign_id"], r["target"]): r for r in self.analysis("US").recs}
        uk = {(r["report_type"], r["campaign_id"], r["target"]): r for r in self.analysis("UK").recs}
        u, k = us[("TARGETING", "US_EXACT", "kw uno")], uk[("TARGETING", "UK_EXACT", "kw uno")]
        self.assertEqual((u["clicks"], u["orders"], u["spend"], u["sales"]), (k["clicks"], k["orders"], k["spend"], k["sales"]))
        self.assertAlmostEqual(u["estimated_direct_ad_profit"], 10 * 8.55 - 100)                # royalty US
        self.assertAlmostEqual(k["estimated_direct_ad_profit"], 10 * 4.10 - 100)                # royalty UK, non US
        self.assertNotEqual(u["estimated_direct_ad_profit"], k["estimated_direct_ad_profit"])

    def test_cross_campaign_never_compares_marketplaces(self):
        us, uk = self.analysis("US"), self.analysis("UK")
        us_ids = {c["campaign_id"] for o in us.overlaps for c in o["campaigns"]}
        self.assertEqual(us_ids, {"US_EXACT", "US_PHRASE"})                                      # 'kw uno' e' anche in UK_EXACT: ignorato
        self.assertEqual([o["search_term"] for o in us.overlaps], ["kw uno"])
        self.assertEqual(uk.overlaps, [])                                                        # in UK una sola campagna: nessun overlap con US
        self.assertTrue(all("UK_EXACT" not in o["campaigns"][i]["campaign_id"] for o in us.overlaps for i in range(len(o["campaigns"]))))

    def test_currency_symbol_follows_the_marketplace(self):
        us, uk = self.analysis("US"), self.analysis("UK")
        us_txt = " ".join(r["reason"] for r in us.recs)
        uk_txt = " ".join(r["reason"] for r in uk.recs)
        self.assertIn("spend $", us_txt)
        self.assertNotIn("£", us_txt)
        self.assertIn("spend £", uk_txt)
        self.assertNotIn("$", uk_txt)


class DecisionTests(MBase):
    def setUp(self):
        super().setUp()
        self.fill_us_uk_data()
        self.a_us = pipeline_ops.run_book_analysis(self.ws, BID, "US")
        self.a_uk = pipeline_ops.run_book_analysis(self.ws, BID, "UK")
        self.assertTrue(self.a_us.ok and self.a_uk.ok, (self.a_us.errors, self.a_uk.errors))

    def test_each_marketplace_has_its_own_analysis_run(self):
        self.assertEqual((self.a_us.data["marketplace"], self.a_uk.data["marketplace"]), ("US", "UK"))
        c = self.db()
        runs = {r["run_id"]: r["marketplace"] for r in c.execute("SELECT run_id, marketplace FROM analysis_runs")}
        c.close()
        self.assertEqual(runs, {self.a_us.data["run_id"]: "US", self.a_uk.data["run_id"]: "UK"})
        # i file di output distinguono il marketplace
        self.assertIn(f"{BID}_US_", Path(self.a_us.data["csv"]).name)
        self.assertIn(f"{BID}_UK_", Path(self.a_uk.data["csv"]).name)
        self.assertEqual(pipeline_ops.latest_analysis_run(self.ws, BID, "US")["run_id"], self.a_us.data["run_id"])
        self.assertEqual(pipeline_ops.latest_analysis_run(self.ws, BID, "UK")["run_id"], self.a_uk.data["run_id"])

    def test_decide_us_excludes_uk_and_uses_the_us_analysis_run(self):
        d = pipeline_ops.run_book_decisions(self.ws, BID, None, "US")          # nessun run indicato: ultimo run di analisi US (non l'ultimo in assoluto)
        self.assertTrue(d.ok, d.errors)
        self.assertEqual((d.data["marketplace"], d.data["analysis_run_id"]), ("US", self.a_us.data["run_id"]))
        c = self.db()
        mps = {r[0] for r in c.execute("SELECT DISTINCT marketplace FROM decisions WHERE run_id=?", (d.data["run_id"],))}
        camps = {r[0] for r in c.execute("SELECT DISTINCT campaign_id FROM decisions WHERE run_id=?", (d.data["run_id"],))}
        n = c.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (d.data["run_id"],)).fetchone()[0]
        run_mp = c.execute("SELECT marketplace FROM decision_runs WHERE run_id=?", (d.data["run_id"],)).fetchone()[0]
        c.close()
        self.assertEqual((mps, camps, run_mp), ({"US"}, {"US_EXACT", "US_PHRASE"}, "US"))
        self.assertEqual(n, d.data["decisions"])
        u = pipeline_ops.run_book_decisions(self.ws, BID, self.a_uk.data["run_id"])    # marketplace dedotto dal run
        self.assertEqual((u.data["marketplace"], u.data["decisions"]), ("UK", 2))

    def test_decide_rejects_an_analysis_run_of_another_marketplace(self):
        r = pipeline_ops.run_book_decisions(self.ws, BID, self.a_uk.data["run_id"], "US")
        self.assertFalse(r.ok)
        self.assertTrue(any("marketplace" in e for e in r.errors))
        self.assertFalse(pipeline_ops.run_book_decisions(self.ws, BID, None, None).ok)           # ambiguo: due marketplace

    def test_decision_economics_and_currency_are_the_marketplace_ones(self):
        d_us = pipeline_ops.run_book_decisions(self.ws, BID, None, "US").data
        d_uk = pipeline_ops.run_book_decisions(self.ws, BID, None, "UK").data
        c = self.db()
        us_reason = " ".join(r[0] for r in c.execute("SELECT reason FROM decisions WHERE run_id=?", (d_us["run_id"],)))
        uk_reason = " ".join(r[0] for r in c.execute("SELECT reason FROM decisions WHERE run_id=?", (d_uk["run_id"],)))
        c.close()
        self.assertIn("$", us_reason)
        self.assertNotIn("$", uk_reason)
        self.assertIn("£", uk_reason)


# ------------------------------------------------------------------ dashboard V1 per marketplace
class DashboardTests(MBase):
    def setUp(self):
        super().setUp()
        self.fill_us_uk_data()
        for mp in ("US", "UK"):
            a = pipeline_ops.run_book_analysis(self.ws, BID, mp)
            self.assertTrue(pipeline_ops.run_book_decisions(self.ws, BID, a.data["run_id"]).ok)
        self.repo = Repo(self.ws.db_path, root=self.root)

    def test_context_is_scoped_to_one_marketplace(self):
        us, uk = load_context(self.repo, BID, None, "US"), load_context(self.repo, BID, None, "UK")
        self.assertEqual((us.marketplace, uk.marketplace), ("US", "UK"))
        self.assertEqual([m["marketplace"] for m in us.marketplaces], ["US", "UK"])
        self.assertEqual((us.book["target_acos"], us.book["royalty_per_copy"], us.book["currency"]), (35.0, 8.55, "USD"))
        self.assertEqual((uk.book["target_acos"], uk.book["royalty_per_copy"], uk.book["currency"], uk.book["break_even_acos"]), (25.0, 4.10, "GBP", 31.56))
        self.assertEqual({d["marketplace"] for d in us.decisions}, {"US"})
        self.assertEqual({d["marketplace"] for d in uk.decisions}, {"UK"})
        c = self.db()
        n_us = c.execute("SELECT COUNT(DISTINCT campaign_id || '|' || LOWER(target)) FROM target_rows WHERE marketplace='US'").fetchone()[0]
        n_uk = c.execute("SELECT COUNT(DISTINCT campaign_id || '|' || LOWER(target)) FROM target_rows WHERE marketplace='UK'").fetchone()[0]
        c.close()
        self.assertEqual((us.summary["total"], uk.summary["total"]), (n_us, n_uk))
        self.assertEqual((n_us, n_uk), (3, 2))
        self.assertEqual(len(us.runs), 1)
        self.assertEqual({r["marketplace"] for r in uk.runs}, {"UK"})
        self.assertEqual(load_context(self.repo, BID).marketplace, "US")                       # senza scelta: marketplace principale

    def test_campaign_overview_is_scoped(self):
        us = {c["campaign_id"] for c in self.repo.campaign_overview(BID, 8.55, "US")}
        uk = {c["campaign_id"] for c in self.repo.campaign_overview(BID, 4.10, "UK")}
        self.assertEqual((us, uk), ({"US_EXACT", "US_PHRASE"}, {"UK_EXACT"}))

    def test_panoramica_shows_marketplace_and_currency(self):
        def render(mp_label):
            old, env = sys.modules.get("streamlit"), os.environ.get("KDP_ADS_DB")
            ctl = {"log": [], "state": {}, "press": set(), "inputs": {"Marketplace": mp_label}, "upload": None}
            os.environ["KDP_ADS_DB"] = str(self.ws.db_path)
            sys.modules["streamlit"] = td2.Rec(ctl)
            for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
                del sys.modules[m]
            try:
                import runpy
                runpy.run_path(str(ROOT / "DASHBOARD" / "panoramica.py"), run_name="__main__")
            finally:
                if env is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = env
                sys.modules["streamlit"] = old if old else sys.modules["streamlit"]
            return list(td2.flat([a for _, a, k in ctl["log"]]))
        uk = render("UK · GBP")
        self.assertIn("**Marketplace: UK · GBP**", uk)
        self.assertIn("25.0%", uk)
        self.assertIn("£4.10", uk)
        self.assertNotIn("$8.55", uk)
        us = render("US · USD")
        self.assertIn("**Marketplace: US · USD**", us)
        self.assertIn("$8.55", us)
        self.assertIn("35.0%", us)


# ------------------------------------------------------------------ compatibilita' e libro legacy
class LegacyTests(td2.WsBase):
    def setUp(self):
        super().setUp()
        self.new_book()                                        # formato precedente: campi economici nel libro (US implicito)
        self.new_campaign("OLD_EXACT")                         # struttura piatta INPUT/<BOOK>/<CAMPAIGN>/

    def test_legacy_book_is_the_us_marketplace_and_keeps_flat_folders(self):
        raw = registry.load_books(self.ws.books_path)["B0TEST0001"]
        self.assertEqual(list(registry.marketplaces_of(raw)), ["US"])
        self.assertEqual(registry.book_view(raw)["royalty_per_copy"], 8.55)
        self.assertFalse(registry.nested_layout(raw, "US"))
        self.assertTrue((self.ws.input_dir / "B0TEST0001" / "OLD_EXACT" / "metadata.json").is_file())
        self.assertEqual([c["marketplace"] for c in catalog.list_campaigns(self.ws, "B0TEST0001")], ["US"])
        self.assertEqual(registry.run_marketplace(raw, None), "US")

    def test_adding_uk_to_a_legacy_book_keeps_legacy_fields_and_data(self):
        self.report("OLD_EXACT", "TARGETING", "LIFETIME", td2.tcsv(), "old.csv")
        before_files = td2.tree(self.ws.input_dir)
        raw0 = dict(registry.load_books(self.ws.books_path)["B0TEST0001"])
        r = catalog.add_marketplace(self.ws, {"book_id": "B0TEST0001", **UK})
        self.assertTrue(r.ok, r.errors)
        raw = registry.load_books(self.ws.books_path)["B0TEST0001"]
        self.assertEqual({k: raw[k] for k in raw0}, raw0)                                  # campi legacy invariati
        self.assertEqual([m["marketplace"] for m in raw["marketplaces"]], ["UK"])
        self.assertEqual(list(registry.marketplaces_of(raw)), ["US", "UK"])
        # UK annidato; le campagne e i file US restano dove sono
        self.assertTrue(catalog.add_campaign(self.ws, {"book_id": "B0TEST0001", "marketplace": "UK", "campaign_id": "NEW_UK", "campaign_type": "EXACT"}).ok)
        self.assertTrue((self.ws.input_dir / "B0TEST0001" / "UK" / "NEW_UK" / "metadata.json").is_file())
        after = td2.tree(self.ws.input_dir)
        self.assertTrue(all(after.get(k) == v for k, v in before_files.items()))           # nessun file esistente spostato o modificato
        self.assertTrue((self.ws.input_dir / "B0TEST0001" / "OLD_EXACT" / "old.csv").is_file())
        self.assertEqual({(c["campaign_id"], c["marketplace"]) for c in catalog.list_campaigns(self.ws, "B0TEST0001")},
                         {("OLD_EXACT", "US"), ("NEW_UK", "UK")})
        # il libro legacy continua a essere analizzabile come US
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001", "US")
        self.assertTrue(a.ok, a.errors)
        self.assertEqual(a.data["marketplace"], "US")
        self.assertIn("B0TEST0001_US_", Path(a.data["csv"]).name)                             # ora ha 2 marketplace: l'etichetta file lo dice

    def test_single_marketplace_book_needs_no_marketplace_argument(self):
        self.report("OLD_EXACT", "TARGETING", "LIFETIME", td2.tcsv(), "old.csv")
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001")                              # nessun marketplace: ce n'e' uno solo
        self.assertTrue(a.ok, a.errors)
        self.assertEqual(a.data["marketplace"], "US")
        self.assertEqual(Path(a.data["csv"]).name.split("_")[0:2], ["recommendations", "B0TEST0001"])    # nome file invariato
        self.assertTrue(pipeline_ops.run_book_decisions(self.ws, "B0TEST0001").ok)

    def test_historical_runs_without_marketplace_belong_to_the_legacy_marketplace(self):
        self.report("OLD_EXACT", "TARGETING", "LIFETIME", td2.tcsv(), "old.csv")
        a = pipeline_ops.run_book_analysis(self.ws, "B0TEST0001")
        d = pipeline_ops.run_book_decisions(self.ws, "B0TEST0001", a.data["run_id"])
        self.assertTrue(catalog.add_marketplace(self.ws, {"book_id": "B0TEST0001", **UK}).ok)
        c = self.db()
        for tbl in ("analysis_runs", "recommendations", "decision_runs", "decisions"):          # simula i run storici: marketplace NULL
            c.execute(f"UPDATE {tbl} SET marketplace=NULL")
        c.commit()
        c.close()
        repo = Repo(self.ws.db_path, root=self.root)
        self.assertEqual([r["run_id"] for r in repo.decision_runs("B0TEST0001", "US")], [d.data["run_id"]])
        self.assertEqual(repo.decision_runs("B0TEST0001", "UK"), [])
        ctx = load_context(repo, "B0TEST0001")
        self.assertEqual((ctx.marketplace, ctx.run["run_id"]), ("US", d.data["run_id"]))
        self.assertEqual(ctx.summary["total"], d.data["decisions"])
        # e le decisioni si possono rigenerare dal vecchio run di analisi: marketplace dedotto = US
        again = pipeline_ops.run_book_decisions(self.ws, "B0TEST0001", a.data["run_id"])
        self.assertTrue(again.ok, again.errors)
        self.assertEqual(again.data["marketplace"], "US")
        self.assertEqual(pipeline_ops.latest_analysis_run(self.ws, "B0TEST0001", "US")["run_id"], a.data["run_id"])
        self.assertIsNone(pipeline_ops.latest_analysis_run(self.ws, "B0TEST0001", "UK"))


# ------------------------------------------------------------------ libro pilota reale 1805566563 (solo lettura)
import realdata  # noqa: E402  (snapshot deterministico dei dati storici: vedi realdata.py)
REAL_DB = realdata.snapshot()


class CliMarketplaceTests(MBase):
    """Le CLI non scelgono mai in silenzio un marketplace quando il libro ne ha piu' di uno."""

    def run_cli(self, mod, *argv):
        import contextlib
        import importlib
        import io
        from unittest import mock
        m = importlib.import_module(mod)
        settings = dict(self.ws.settings)
        err, out = io.StringIO(), io.StringIO()
        def logger_tracked(*args, **kw):                       # i file di log vanno chiusi prima di cancellare la cartella
            lg, path = setup_logger(*args, **kw)
            self._closers.append(lambda: importing.close_logger(lg))
            return lg, path
        patches = (mock.patch.object(m, "load_settings", return_value=settings), mock.patch.object(m, "setup_logger", logger_tracked),
                   mock.patch.object(sys, "argv", [mod, *argv]), contextlib.redirect_stderr(err), contextlib.redirect_stdout(out))
        with contextlib.ExitStack() as stack:
            for pt in patches:
                stack.enter_context(pt)
            if hasattr(m, "load_mapping"):
                with mock.patch.object(m, "load_mapping", return_value=self.ws.mapping):
                    code = m.main()
            else:
                code = m.main()
        return code, out.getvalue(), err.getvalue()

    def test_import_requires_marketplace_for_multi_marketplace_book(self):
        code, _, err = self.run_cli("import_csv", "--book", BID, "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("--marketplace", err)
        self.assertIn("UK, US", err)

    def test_import_with_marketplace_runs(self):
        code, out, _ = self.run_cli("import_csv", "--book", BID, "--marketplace", "UK", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("RIEPILOGO", out)

    def test_analyze_and_decide_error_without_marketplace(self):
        code, _, err = self.run_cli("analyze", "--book", BID)
        self.assertEqual(code, 2)
        self.assertIn("marketplace", err)
        code, _, err = self.run_cli("decide", "--book", BID)
        self.assertEqual(code, 2)
        self.assertIn("marketplace", err)


class SingleMarketplaceCliTests(td2.WsBase):
    def test_single_marketplace_book_needs_no_flag(self):
        self.assertTrue(catalog.add_book_shell(self.ws, {"title": "Solo", "book_id": BID}).ok)
        self.assertTrue(catalog.add_marketplace(self.ws, {"book_id": BID, **US}).ok)
        self.assertEqual(registry.multi_marketplace_books(registry.load_books(self.ws.settings["books_path"]), BID), {})


class MigrationBackupTests(unittest.TestCase):
    def _v3(self, d):
        p = Path(d) / "k.db"
        connect(p).close()
        c = sqlite3.connect(p)
        c.execute("PRAGMA user_version=3")
        c.commit()
        c.close()
        return p

    @staticmethod
    def _ver(path):
        c = sqlite3.connect(path)
        try:
            return c.execute("PRAGMA user_version").fetchone()[0]
        finally:
            c.close()

    def test_backup_once_per_migration_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._v3(d)
            self.assertEqual(list((Path(d) / "backups").glob("*")), [])                  # DB nuovo: nessun backup
            connect(p).close()
            files = sorted((Path(d) / "backups").glob("*"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, rf"^kdp_ads_pre_schema_v{SCHEMA_VERSION}_\d{{8}}_\d{{6}}\.db$")
            self.assertEqual(self._ver(files[0]), 3)                                    # copia PRE migrazione
            connect(p).close()                                                           # gia' alla versione corrente: nessun nuovo backup
            self.assertEqual(len(list((Path(d) / "backups").glob("*"))), 1)

    def test_same_second_never_overwrites(self):
        from unittest import mock
        from kdp_ads import db as dbm
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.db"
            connect(p).close()
            fixed = mock.Mock()
            fixed.now.return_value = __import__("datetime").datetime(2026, 1, 2, 3, 4, 5)
            with mock.patch.object(dbm, "datetime", fixed):
                c = sqlite3.connect(p)
                a = dbm.backup_before_migration(c, 3)
                b = dbm.backup_before_migration(c, 3)
                c.close()
            self.assertNotEqual(a, b)
            self.assertTrue(a.is_file() and b.is_file())
            self.assertEqual(a.name, f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_20260102_030405.db")

    def test_memory_db_has_no_backup(self):
        connect(":memory:").close()


class PilotBookTests(unittest.TestCase):
    def test_pilot_book_is_the_us_marketplace(self):
        b = registry.load_books(ROOT / "CONFIG" / "books.json")["1805566563"]
        self.assertEqual(list(registry.marketplaces_of(b))[0], "US")                           # il pilota resta US; puo' avere altri marketplace (es. UK)
        v = registry.book_view(b, "US")
        self.assertEqual((v["currency"], v["price"], v["royalty_per_copy"], v["target_acos"], v["phase"]), ("USD", 19.99, 8.55, 35.0, "PROFIT_STABLE"))
        self.assertEqual(registry.book_economics(v)[0]["break_even_acos"], 42.77)
        self.assertFalse(registry.nested_layout(b, "US"))
        locs = registry.iter_campaigns(ROOT / "INPUT", "1805566563", b)                        # struttura piatta esistente leggibile
        legacy = [l for l in locs if l.marketplace is None]                                    # cartelle piatte storiche (altri marketplace possono avere cartelle annidate)
        self.assertEqual(sorted(l.campaign_id for l in legacy), ["CHAIR_TAI_CHI_COMPETITOR", "CHAIR_TAI_CHI_EXACT", "CHAIR_TAI_CHI_PHRASE"])
        self.assertTrue(all((l.path / "metadata.json").is_file() for l in locs))

    @unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
    def test_real_data_survives_migration_and_run_6_is_readable(self):
        """Migra una COPIA del DB reale: nessun dato perso, run 6 leggibile come US."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "k.db"
            src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
            dst = sqlite3.connect(copy)
            src.backup(dst)
            dst.close()
            src.close()

            def snapshot(path):
                c = sqlite3.connect(path)
                out = {}
                for t in ("imports", "target_rows", "search_term_rows", "recommendations", "decisions", "decision_runs", "analysis_runs", "campaigns", "books"):
                    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] not in ("marketplace",) or t in ("imports", "target_rows", "search_term_rows", "campaigns", "books")]
                    cols = [x for x in cols if x not in ("period_id", "snapshot_id")] if t in ("imports", "target_rows", "search_term_rows") else cols
                    cols = [x for x in cols if not (t == "imports" and x == "warnings_json")]          # colonna nuova (avvisi strutturati), NULL sui vecchi
                    cols = [x for x in cols if not (t in ("target_rows", "search_term_rows") and (x.startswith("amazon_") or x in ("row_date_from", "row_date_to")))]   # tracciabilita' report globali (nullable)
                    cols = [x for x in cols if not (t == "decisions" and x.startswith("trend_"))]      # colonne nuove del trend 7D (nullable, NULL sulle decisioni storiche)
                    cols = [x for x in cols if not (t in ("decisions", "campaigns") and x.startswith("amazon_"))]   # colonne account Ads (nullable, NULL sullo storico)
                    rows = c.execute(f"SELECT {','.join(cols)} FROM {t} ORDER BY 1,2").fetchall() if cols else []
                    out[t] = (len(rows), hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest())
                c.close()
                return out
            c = sqlite3.connect(copy)
            if c.execute("PRAGMA user_version").fetchone()[0] >= 4:                            # DB reale gia' migrato dalla dashboard: la copia
                c.execute("PRAGMA user_version=3")                                             # torna a v3 per riprovare la migrazione
                c.commit()
            c.close()
            before = snapshot(copy)
            connect(copy).close()                                                              # migrazione schema v5 sulla copia
            backups = list((Path(tmp) / "backups").glob(f"kdp_ads_pre_schema_v{SCHEMA_VERSION}_*.db"))
            self.assertEqual(len(backups), 1)                                                  # backup automatico, una sola volta
            self.assertEqual(snapshot(backups[0]), before)                                     # il backup e' la copia pre-migrazione
            print("BACKUP:", backups[0].name, "| righe prima/dopo:", {k: v[0] for k, v in before.items()})
            self.assertEqual(MigrationBackupTests._ver(copy), SCHEMA_VERSION)
            c = sqlite3.connect(copy)
            try:
                self.assertIn("UNIQUE (book_id, campaign_id, report_type, period_id, file_sha256)",
                              c.execute("SELECT sql FROM sqlite_master WHERE name='imports'").fetchone()[0])
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                c.close()
            after = snapshot(copy)
            self.assertEqual(after, before)                                                    # nessuna riga persa o modificata
            repo = Repo(copy, root=ROOT)
            self.assertEqual([m["marketplace"] for m in repo.marketplaces("1805566563")][0], "US")          # US sempre presente e per primo
            ctx = load_context(repo, "1805566563", 6, "US")
            self.assertEqual((ctx.error, ctx.run["run_id"], ctx.marketplace), (None, 6, "US"))
            self.assertEqual((ctx.summary["total"], ctx.summary["actionable"], ctx.summary["paused"], ctx.summary["warnings"]), (136, 2, 113, 3))
            self.assertEqual((ctx.book["target_acos"], ctx.book["royalty_per_copy"], ctx.book["currency"]), (35.0, 8.55, "USD"))
            self.assertIn(6, [r["run_id"] for r in repo.decision_runs("1805566563", "US")])
            self.assertNotIn(6, [r["run_id"] for r in repo.decision_runs("1805566563", "UK")])      # il run 6 e' US, mai UK


if __name__ == "__main__":
    unittest.main()
