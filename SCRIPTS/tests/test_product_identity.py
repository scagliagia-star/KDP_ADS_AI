"""Identita' dei product target: asin="X" e asin-expanded="X" sono target DIVERSI (stato, offerta, metriche, decisione separati).
Bug reale (Rare Coins B0GY42H3W4): le due righe finivano nella stessa entita' decisionale 'asin:<ASIN>' e la decisione ereditava stato/offerta
dell'uno e metriche dell'altro. Dati sintetici + COPIA del database reale (sola lettura)."""
import hashlib
import logging
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_decision as td
from kdp_ads import amazon
from kdp_ads.analysis import analyze_book
from kdp_ads.decision import action_key
from kdp_ads.metrics import product_asin, product_key, product_kind
from kdp_ads.report import save_to_db

A, X = 'asin="0794851630"', 'asin-expanded="0794851630"'
HDR = ("Stato,Categorie e prodotti,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,Costo totale (USD),CPC (USD),"
       "Acquisti,Vendite (USD),ACOS")


def trow(target, state, bid, impr, clicks, orders, spend, sales):
    st2 = "Attivo" if state == "ABILITATO" else "In pausa"
    q = target.replace('"', '""')
    return f'{state},"{q}",—,{st2},{bid},{impr},{clicks},"0,0200",{spend},0.70,{orders},{sales},"0,3000"'


class IdentityUnitTests(unittest.TestCase):
    def test_asin_and_expanded_are_different_identities_original_string_untouched(self):
        self.assertEqual((product_asin(A), product_asin(X)), ("0794851630", "0794851630"))
        self.assertEqual((product_kind(A), product_kind(X)), ("asin", "asin-expanded"))
        self.assertEqual((product_key(A), product_key(X)), ("asin:0794851630", "asin-expanded:0794851630"))
        self.assertNotEqual(amazon.target_identity(A), amazon.target_identity(X))
        self.assertNotEqual(action_key("B", "C", A), action_key("B", "C", X))
        self.assertEqual(action_key("B", "C", A), "B|C|asin:0794851630")                    # chiave storica invariata per asin=
        self.assertEqual(action_key("B", "C", X), "B|C|asin-expanded:0794851630")
        self.assertEqual(A, 'asin="0794851630"')
        self.assertIsNone(product_key("coin book"))
        self.assertEqual(amazon.target_identity("  Coin  Book "), "coin book")               # le keyword non cambiano


class SeparateDecisionsTests(td.DBase):
    def test_active_and_paused_targets_with_same_asin_keep_separate_everything(self):
        rows = "\n".join([HDR, trow(A, "ABILITATO", "0.70", 5000, 87, 7, "62.63", "167.93"), trow(X, "IN PAUSA", "0.69", 2313, 10, 0, "5.67", "0.00")])
        self.campaign(td.BOOK, "C_PROD", "COMPETITOR", {"t.csv": rows + "\n"})
        self.assertTrue(all(r.status == "IMPORTED" for r in self.run_import()))
        an = analyze_book(self.conn, self.settings, self.mapping, td.RULES_PATH, td.BOOK, self.logger)
        self.run_id = save_to_db(self.conn, an, "a", "b", "c", an.ctx.rules)
        ds = self.decisions()
        by = {d["target"]: d for d in ds.decisions}
        self.assertEqual(set(by), {A, X})
        a, x = by[A], by[X]
        self.assertNotEqual(a["action_key"], x["action_key"])
        self.assertEqual((a["target_status"], x["target_status"]), ("ENABLED", "PAUSED"))                   # stati separati
        self.assertEqual((a["current_bid"], x["current_bid"]), (0.7, 0.69))                                 # offerte separate
        self.assertEqual((a["lifetime_clicks"], a["lifetime_orders"], a["lifetime_spend"]), (87.0, 7.0, 62.63))   # metriche separate
        self.assertEqual((x["lifetime_clicks"], x["lifetime_orders"], x["lifetime_spend"]), (10.0, 0.0, 5.67))
        self.assertNotEqual((a["target_status"], a["lifetime_spend"]), (x["target_status"], x["lifetime_spend"]))
        self.assertFalse([w for w in ds.warnings if "snapshot lifetime" in w])                              # niente falso "2 snapshot lifetime"

    def test_other_product_targets_are_unaffected(self):
        b0 = 'asin="B0FTFJL6RB"'
        rows = "\n".join([HDR, trow(b0, "ABILITATO", "0.90", 4300, 86, 12, "61.51", "227.88")])
        self.campaign(td.BOOK, "C_COMP", "COMPETITOR", {"t.csv": rows + "\n"})
        self.assertTrue(all(r.status == "IMPORTED" for r in self.run_import()))
        an = analyze_book(self.conn, self.settings, self.mapping, td.RULES_PATH, td.BOOK, self.logger)
        self.run_id = save_to_db(self.conn, an, "a", "b", "c", an.ctx.rules)
        ds = self.decisions()
        self.assertEqual([d["action_key"] for d in ds.decisions], [f"{td.BOOK}|C_COMP|asin:B0FTFJL6RB"])


REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"


@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealCopyTests(unittest.TestCase):
    """Rare Coins Book (B0GY42H3W4, US) su una COPIA del DB reale: nessuna scrittura sul DB reale, books.json invariato."""

    @classmethod
    def setUpClass(cls):
        cls.real_before = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        cls.books_before = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        dst.close()
        src.close()
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row
        cls.ok = bool(cls.conn.execute("SELECT 1 FROM target_rows WHERE book_id='B0GY42H3W4' AND target LIKE 'asin-expanded%'").fetchone())

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def setUp(self):
        if not self.ok:
            self.skipTest("libro reale B0GY42H3W4 non presente")

    def test_real_book_decisions_are_separate(self):
        from kdp_ads.config import CONFIG_DIR, load_settings
        from kdp_ads.decision import build_decisions
        ds = build_decisions(self.conn, load_settings(), CONFIG_DIR / "rules.json", "B0GY42H3W4", None, logging.getLogger("t"), "US")
        by = {d["target"]: d for d in ds.decisions if d["campaign_id"] == "PRODOTTO"}
        a, x = by['asin="0794851630"'], by['asin-expanded="0794851630"']
        self.assertEqual((a["target_status"], x["target_status"]), ("ENABLED", "PAUSED"))
        self.assertEqual((a["lifetime_clicks"], a["lifetime_orders"], a["lifetime_spend"]), (87.0, 7.0, 62.63))
        self.assertEqual((x["lifetime_clicks"], x["lifetime_orders"], x["lifetime_spend"]), (10.0, 0.0, 5.67))
        self.assertEqual((a["current_bid"], x["current_bid"]), (0.7, 0.69))
        self.assertEqual(x["final_action"], "HOLD_PAUSED_TARGET")
        self.assertEqual((a["tactical_clicks"], a["tactical_orders"]), (43.0, 3.0))
        self.assertEqual((x["tactical_clicks"], x["tactical_orders"]), (0.0, 0.0))
        keys = {d["action_key"] for d in ds.decisions}
        self.assertIn("B0GY42H3W4|PRODOTTO|asin-expanded:B0FT2T32WY", keys)

    def test_other_books_decisions_identical_to_saved_runs(self):
        from kdp_ads.config import CONFIG_DIR, load_settings
        from kdp_ads.decision import build_decisions
        for book in ("1805565796", "1805566563"):
            run = self.conn.execute("SELECT run_id, marketplace, analysis_run_id FROM decision_runs WHERE book_id=? ORDER BY run_id DESC", (book,)).fetchone()
            if not run:
                continue
            old = {r["action_key"]: (r["final_action"], r["target_status"], r["lifetime_spend"])
                   for r in self.conn.execute("SELECT * FROM decisions WHERE run_id=?", (run["run_id"],))}
            ds = build_decisions(self.conn, load_settings(), CONFIG_DIR / "rules.json", book, run["analysis_run_id"], logging.getLogger("t"), run["marketplace"])
            new = {d["action_key"]: (d["final_action"], d["target_status"], d["lifetime_spend"]) for d in ds.decisions}
            self.assertEqual(old, new, book)

    def test_nothing_written(self):
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_before)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_before)


@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealOverviewTests(unittest.TestCase):
    """Panoramica sul libro reale con le decisioni rigenerate su una COPIA: asin= e asin-expanded= restano due schede separate."""

    def test_overview_shows_both_targets_separately(self):
        import os
        import re
        try:
            from streamlit.testing.v1 import AppTest
        except Exception:
            self.skipTest("streamlit non installato")
        import test_overview as tov
        from kdp_ads.config import CONFIG_DIR, load_settings
        from kdp_ads.pipeline import run_decisions
        before = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "k.db"
            dst = sqlite3.connect(db)
            src.backup(dst)
            src.close()
            dst.row_factory = sqlite3.Row
            if not dst.execute("SELECT 1 FROM target_rows WHERE book_id='B0GY42H3W4' AND target LIKE 'asin-expanded%'").fetchone():
                dst.close()
                self.skipTest("libro reale B0GY42H3W4 non presente")
            r = run_decisions(load_settings(), CONFIG_DIR / "rules.json", "B0GY42H3W4", Path(tmp) / "out", logging.getLogger("t"), None, save_db=True, conn=dst,
                              marketplace="US")
            dst.commit()
            dst.close()
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(db)
            try:
                at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
                tov.select(at, "Libro", "B0GY42H3W4")
                tov.select(at, "Run decisionale", f"#{r['run_id']}")
                text = "\n".join(re.sub(r"<[^>]+>", "", t) for t, in_exp, _ in tov.page_text(at) if not in_exp)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        self.assertFalse(at.exception)
        self.assertIn('PRODOTTO · asin="0794851630"', text.replace("&quot;", '"'))
        self.assertIn('PRODOTTO · asin-expanded="0794851630"', text.replace("&quot;", '"'))
        exp = text.split("asin-expanded=&quot;0794851630&quot;")[1].split("GIÀ IN PAUSA")[0].split("\n")[:3]
        self.assertTrue(any("Spesa storica $5.67" in x and "Clic 10" in x for x in exp), exp)                # dati propri dell'expanded in pausa
        self.assertNotIn("$62.63", "\n".join(exp))
        prod = text.split("TARGET PRODOTTO VINCENTE")[2].split("EROGAZIONE DA VERIFICARE")[0]
        self.assertIn("Ha generato 3 acquisti con ACOS 32,1% nei termini/prodotti recenti.", prod)
        self.assertIn("STATO OPERATIVO DEL TARGET:** MONITORA", prod)
        self.assertIn("Il target complessivo ha ACOS 42,1% negli ultimi 30 giorni. Per ora non modificare l'offerta.", prod)
        self.assertNotIn("NON TOCCARE", prod)
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), before)                              # DB reale intatto


if __name__ == "__main__":
    unittest.main()
