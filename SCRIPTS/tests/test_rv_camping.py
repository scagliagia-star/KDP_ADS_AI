"""RV Camping Across America (B0GZNKDLGM, US): (1) Search Term presente solo nel 65D ma forte => METTI IN EXACT (nessuna smentita nel 30D);
(2) target con storico sotto obiettivo e recente zero-order sotto soglia => MONITORA, mai "spende troppo rispetto all'obiettivo".
Dati sintetici + COPIA del DB reale (sola lettura)."""
import hashlib
import json
import logging
import os
import re
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

import test_opportunities as to
import test_overview as tov
from kdp_ads.opportunities import NEGATIVE, PROMOTE, PROMOTED, REVIEW_CONFLICT, WATCH, unique_terms

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

BOOK = {"target_acos": 35.0, "break_even_acos": 38.1, "royalty_per_copy": 8.0, "currency": "USD"}


def phrase_rows(term, parent="rv camping", clicks=4, orders=3, spend=3.55, only65=True):
    """Search Term FRASE presente SOLO nel 65D; il 30D della campagna e' importato (con un altro termine)."""
    rows = [to.st("FRASE", "PHRASE", parent, term, to.D65, clicks, orders, spend, 62.97),
            to.st("FRASE", "PHRASE", parent, "unrelated recent term", to.D30, 20, 0, 3.0)]
    if not only65:
        rows.append(to.st("FRASE", "PHRASE", parent, term, to.D30, clicks, orders, spend, 62.97))
    return rows


class ExactEligibilityTests(unittest.TestCase):
    def test_phrase_term_4_clicks_3_orders_great_acos_only_in_65d_is_exact(self):
        res = to.run(phrase_rows("rv camping bible"))
        r = to.only(res, "rv camping bible")
        self.assertEqual((r["opportunity_type"], r["windows_agreement"], r["confidence"]), (PROMOTE, "ASSENTE_30D", "MEDIUM"))
        self.assertAlmostEqual(r["acos"], 3.55 / 62.97 * 100, places=2)
        self.assertEqual(unique_terms(res["records"], PROMOTE), ["rv camping bible"])

    def test_term_related_to_the_phrase_parent_is_not_suppressed(self):
        # contiene il testo del target padre e non coincide con esso: eleggibile
        r = to.only(to.run(phrase_rows("rv camping bible", parent="rv camping")), "rv camping bible")
        self.assertEqual(r["opportunity_type"], PROMOTE)
        self.assertNotIn("TERM_EQUALS_PARENT_TARGET", r["flags"])
        self.assertFalse(r["already_in_exact"])

    def test_min_clicks_threshold_is_unchanged(self):
        self.assertEqual(to.only(to.run(phrase_rows("rv camping bible", clicks=2)), "rv camping bible")["opportunity_type"], WATCH)
        self.assertEqual(to.only(to.run(phrase_rows("rv camping bible", clicks=3)), "rv camping bible")["opportunity_type"], PROMOTE)

    def test_confirmed_in_30d_still_promotes(self):
        r = to.only(to.run(phrase_rows("rv camping bible", only65=False)), "rv camping bible", "FRASE")
        self.assertEqual(r["opportunity_type"], PROMOTE)

    def test_already_in_exact_is_not_duplicated(self):
        res = to.run(phrase_rows("rv camping bible"), [to.tgt("EXACT", "EXACT", "RV  Camping Bible")])
        r = to.only(res, "rv camping bible")
        self.assertEqual(r["opportunity_type"], "NO_ACTION")
        self.assertTrue(r["already_in_exact"])
        self.assertEqual(unique_terms(res["records"], PROMOTE), [])

    def test_already_negative_keeps_existing_behaviour(self):
        rows = to.with_added(phrase_rows("rv camping bible"), to.NEG_EXACT)
        self.assertEqual(to.only(to.run(rows), "rv camping bible")["opportunity_type"], REVIEW_CONFLICT)       # negativa e non in EXACT
        res = to.run(rows, [to.tgt("EXACT", "EXACT", "rv camping bible")])
        self.assertEqual(to.only(res, "rv camping bible")["opportunity_type"], PROMOTED)                      # promossa in EXACT
        self.assertEqual(unique_terms(res["records"], NEGATIVE), [])

    def test_similar_but_different_exact_keyword_does_not_block(self):
        res = to.run(phrase_rows("rv camping bible"), [to.tgt("EXACT", "EXACT", "rv camping bibles")])
        self.assertEqual(to.only(res, "rv camping bible")["opportunity_type"], PROMOTE)


class ZeroOrderMonitorTextTests(unittest.TestCase):
    def test_no_overspending_text_when_history_is_better_than_target(self):
        d = tov.dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, lifetime_acos=33.3, lifetime_orders=4.0, lifetime_clicks=36.0, lifetime_spend=27.96)
        s = tov.O.short_reason(d, BOOK)
        self.assertNotIn("spendendo troppo", s)
        self.assertNotIn("contro 35,0%): riduzione dell'offerta", s)
        self.assertIn("storico è in linea con l'obiettivo (ACOS 33,3% contro 35,0%)", s)

    def test_overspending_text_only_when_history_is_above_target(self):
        d = tov.dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, lifetime_acos=41.1, lifetime_orders=6.0)
        self.assertIn("Sta spendendo troppo rispetto al tuo ACOS obiettivo (storico 41,1% contro 35,0%)", tov.O.short_reason(d, BOOK))

    def test_recent_zero_orders_watch_text_and_monitor_section(self):
        d = tov.dec(1, final_action="WATCH", flags="RECENT_ZERO_ORDERS_WATCH", lifetime_clicks=36.0, lifetime_orders=4.0, lifetime_acos=33.3, lifetime_spend=27.96,
                    tactical_clicks=10.0, tactical_orders=0.0, tactical_spend=8.2, tactical_acos=None)
        ov = tov.O.build_overview([d], BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        self.assertEqual([c["badge"] for c in ov["monitor"]], ["MONITORA"])
        self.assertEqual((ov["kpi"]["actions"], ov["good"]), (0, []))                                   # non conta in DA VALUTARE ORA e non e' NON TOCCARE
        self.assertEqual(ov["monitor"][0]["reason"], "Storicamente è in linea con l'obiettivo, ma negli ultimi 30 giorni ha avuto 10 clic e $8,20 di spesa senza "
                                                     "acquisti. La spesa è ancora bassa per ridurre l'offerta: monitora.")


REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"
EXPECT_DEC = {("ESATTA", "rv camping"): ("BID_DOWN_5_CANDIDATE", -5.0), ("FRASE", "camping books"): ("BID_DOWN_5_CANDIDATE", -5.0),
              ("FRASE", "rv camping"): ("HOLD", None), ("AUTO", "loose-match"): ("HOLD", None), ("ESATTA", "rv camping across the usa"): ("HOLD", None),
              ("ESATTA", "rv camping across the usa 2026"): ("HOLD", None), ("FRASE", "rv for beginners"): ("HOLD", None),
              ("FRASE", "rv travel book"): ("WATCH", None)}


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealRvCampingTests(unittest.TestCase):
    """Rianalisi + decisioni rigenerate su una COPIA del DB reale; DB reale, books.json e INPUT invariati."""

    @classmethod
    def setUpClass(cls):
        cls.real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        cls.books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        inp = ROOT / "INPUT"
        cls.inp_state = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        src.close()
        dst.row_factory = sqlite3.Row
        cls.ok = bool(dst.execute("SELECT 1 FROM decision_runs WHERE book_id='B0GZNKDLGM'").fetchone())
        cls.text, cls.dec = "", {}
        if cls.ok:
            from kdp_ads.config import CONFIG_DIR, load_mapping, load_settings
            from kdp_ads.pipeline import run_analysis, run_decisions
            lg, s = logging.getLogger("t"), load_settings()
            ra = run_analysis(s, load_mapping(), CONFIG_DIR / "rules.json", "B0GZNKDLGM", Path(cls.tmp.name) / "out", lg, save_db=True, conn=dst, marketplace="US")
            rd = run_decisions(s, CONFIG_DIR / "rules.json", "B0GZNKDLGM", Path(cls.tmp.name) / "out", lg, ra["run_id"], save_db=True, conn=dst, marketplace="US")
            dst.commit()
            cls.dec = {(r["campaign_id"], r["target"]): dict(r) for r in dst.execute("SELECT * FROM decisions WHERE run_id=?", (rd["run_id"],))}
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(cls.db)
            try:
                at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
                tov.select(at, "Libro", "B0GZNKDLGM")
                tov.select(at, "Run decisionale", f"#{rd['run_id']}")
                cls.at = at
                cls.text = "\n".join(re.sub(r"<[^>]+>", "", t) for t, in_exp, _ in tov.page_text(at) if not in_exp)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        dst.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        if not self.ok:
            self.skipTest("libro reale B0GZNKDLGM non presente")

    def block(self, a, b):
        return self.text.split(a)[1].split(b)[0]

    def test_decisions(self):
        for key, (action, pct) in EXPECT_DEC.items():
            self.assertEqual((self.dec[key]["final_action"], self.dec[key]["bid_change_pct"]), (action, pct), key)
        t = self.dec[("FRASE", "rv travel book")]
        self.assertEqual((t["decision_rule"], t["operationally_actionable"]), ("C0W_RECENT_ZERO_ORDERS_WATCH", 0))
        self.assertIn("RECENT_ZERO_ORDERS_WATCH", t["flags"])

    def test_kpis_and_sections(self):
        self.assertFalse(self.at.exception)
        k = {m.label: m.value for m in self.at.metric if m.label in ("DA VALUTARE ORA", "OPPORTUNITÀ EXACT", "CANDIDATI NEGATIVI")}
        self.assertEqual(k, {"DA VALUTARE ORA": "2", "OPPORTUNITÀ EXACT": "1", "CANDIDATI NEGATIVI": "0"})
        acts = self.block("COSA FARE OGGI", "KEYWORD DA METTERE IN EXACT")
        self.assertIn("**ESATTA · rv camping**", acts)
        self.assertIn("**FRASE · camping books**", acts)
        self.assertEqual(len(re.findall(r"## ABBASSA OFFERTA DEL 5%", acts)), 2)
        self.assertIn("METTI IN EXACT **rv camping bible**", self.text)
        mon = self.block("DA MONITORARE\n", "STA ANDANDO BENE")
        for t in ("FRASE · rv camping**", "FRASE · rv travel book**", "AUTO · Corrispondenza generica**"):
            self.assertIn(t, mon)
        good = self.block("STA ANDANDO BENE / NON TOCCARE\n", "AVVISI DATI")
        for t in ("ESATTA · rv camping across the usa**", "ESATTA · rv camping across the usa 2026**", "FRASE · rv for beginners**"):
            self.assertIn(t, good)

    def test_rv_travel_book_text_is_logical(self):
        self.assertNotIn("storico 33,3% contro 35,0%", self.text)
        mon = self.block("DA MONITORARE\n", "STA ANDANDO BENE")
        self.assertIn("negli ultimi 30 giorni ha avuto 10 clic e $8,20 di spesa senza acquisti", mon)

    def test_nothing_written(self):
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        inp = ROOT / "INPUT"
        now = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        self.assertEqual(now, self.inp_state)


if __name__ == "__main__":
    unittest.main()
