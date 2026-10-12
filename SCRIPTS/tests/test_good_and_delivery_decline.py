"""Panoramica: (1) target attivo profittevole con pochi acquisti in STA ANDANDO BENE / NON TOCCARE; (2) forte calo RELATIVO di erogazione, normalizzato
per giorno e solo con una base di confronto comparabile. Dati sintetici + COPIA del DB reale Calisthenics (B0GNZFLXCV). Nessuna nuova decisione."""
import hashlib
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

import test_overview as tov

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

BOOK = {"target_acos": 35.0, "break_even_acos": 40.0, "royalty_per_copy": 8.0, "currency": "USD"}
DET = "impressions/giorno: recente {r} (2026-09-06__2026-10-05, 30g) vs riferimento {ref} (2026-08-02__2026-10-05, 65g) = x{x}"


def build(ds, opps=None):
    return tov.O.build_overview(ds, BOOK, tov.SUMMARY, [], None if opps is None else {"records": opps, "new_exact_campaign": None}, tov.parse_delivery_detail)


def hold(i, **o):
    base = dict(final_action="HOLD", lifetime_clicks=20.0, lifetime_orders=2.0, lifetime_spend=11.16, lifetime_acos=27.9, tactical_clicks=12.0, tactical_orders=1.0,
                tactical_acos=34.8, target="complements", campaign_id="AUTO", lifetime_estimated_profit=4.8)
    base.update(o)
    return tov.dec(i, **base)


class ProfitableTargetsInGoodSectionTests(unittest.TestCase):
    def test_active_profitable_target_with_two_orders_is_do_not_touch(self):
        ov = build([hold(1)])
        self.assertEqual([c["badge"] for c in ov["good"]], ["NON TOCCARE"])
        c = ov["good"][0]
        self.assertEqual((c["lifetime_acos"], c["recent_acos"], c["orders"], c["reason"]), (27.9, 34.8, 2.0, "Sta funzionando bene."))
        self.assertEqual(ov["others"], [])

    def test_not_good_when_unprofitable_too_few_orders_paused_or_recent_weak(self):
        self.assertEqual(build([hold(1, lifetime_acos=36.0)])["good"], [])                       # sopra l'obiettivo
        self.assertEqual(build([hold(2, lifetime_orders=1.0)])["good"], [])                      # un solo acquisto
        self.assertEqual(build([hold(3, lifetime_acos=None)])["good"], [])                       # ACOS n/d
        self.assertEqual(build([hold(4, target_status="PAUSED", final_action="HOLD_PAUSED_TARGET")])["good"], [])
        weak = build([hold(5, tactical_acos=55.0)])                                              # recente peggiorato: DA MONITORARE, non NON TOCCARE
        self.assertEqual((weak["good"], [c["badge"] for c in weak["monitor"]]), ([], ["MONITORA"]))

    def test_decision_stays_the_one_of_the_decision_layer_and_kpis_do_not_change(self):
        d = hold(1)
        ov = build([d])
        self.assertEqual(d["final_action"], "HOLD")
        self.assertEqual((ov["kpi"]["actions"], len(ov["actions"])), (0, 0))                     # DA VALUTARE ORA invariato
        self.assertEqual(len(ov["all_ids"]), 1)                                                  # una sola scheda, nessuna decisione in piu'
        self.assertEqual(build([tov.dec(1)])["kpi"]["actions"], 0)

    def test_old_good_rule_still_works(self):
        ov2 = build([hold(2, lifetime_orders=5.0, lifetime_spend=30.0, lifetime_acos=30.0)])
        self.assertEqual(len(ov2["good"]), 1)


def declining(i, **o):
    base = dict(target="workout", campaign_id="FRASE", target_status="ENABLED", final_action="HOLD", lifetime_clicks=35.0, lifetime_orders=4.0,
                lifetime_acos=35.5, tactical_clicks=0.0, tactical_impressions_per_day=148 / 30, delivery_source="TARGET", delivery_signal="DELIVERY_DECLINING",
                delivery_ratio=0.15, reason=DET.format(r="4.93", ref="33.0", x="0.15"))
    base.update(o)
    return tov.dec(i, **base)


class RelativeDeliveryDeclineTests(unittest.TestCase):
    def item(self, d):
        return tov.O.delivery_item(d, tov.parse_delivery_detail)

    def test_strong_relative_decline_with_useful_history_is_listed(self):
        it = self.item(declining(1, reason=DET.format(r="4.93", ref="14.0", x="0.15")))             # riferimento 14/giorno (< 20): passa solo col storico utile
        self.assertEqual((it["klass"], it["badge"], it["impressions_30d"]), ("DECLINING", "EROGAZIONE IN CALO", 148))
        self.assertEqual(it["reason"], "Il target aveva traffico e vendite nello storico, ma negli ultimi 30 giorni l'erogazione è crollata. "
                                       "Verifica pertinenza, competitività dell'offerta e cambiamenti nella campagna.")

    def test_comparison_is_per_day_and_needs_a_comparable_baseline(self):
        # 148 impressioni contro uno storico enorme in valore assoluto NON basta: senza rapporto per giorno del TARGET nessun alert
        self.assertIsNone(self.item(declining(1, delivery_source="CAMPAIGN_FALLBACK", delivery_ratio=0.7)))
        self.assertIsNone(self.item(declining(2, delivery_source=None, delivery_signal="UNKNOWN", delivery_ratio=None, reason="FINAL HOLD")))
        self.assertIsNone(self.item(declining(3, delivery_ratio=0.9, delivery_signal="DELIVERY_STABLE")))     # stesso ritmo giornaliero: nessun crollo
        self.assertIsNone(self.item(declining(4, delivery_ratio=0.5)))                                       # calo non forte (> 0,30)

    def test_history_conditions_are_required(self):
        low = DET.format(r="4.93", ref="14.0", x="0.15")                                          # riferimento < 20/giorno: serve lo storico utile
        self.assertIsNone(self.item(declining(1, lifetime_clicks=12.0, reason=low)))             # storico non significativo
        self.assertIsNone(self.item(declining(2, lifetime_orders=0.0, reason=low)))              # nessun acquisto storico
        self.assertIsNone(self.item(declining(3, tactical_clicks=2.0, reason=low)))              # nel recente ci sono clic: non e' un crollo senza clic
        self.assertEqual(self.item(declining(4, tactical_impressions_per_day=0.0, reason=low))["klass"], "NO_DELIVERY")   # zero impressioni: altra classe

    def test_paused_targets_are_never_listed(self):
        self.assertIsNone(self.item(declining(1, target_status="PAUSED")))

    def test_one_card_per_target_priority_very_low_over_decline(self):
        d = declining(1, tactical_impressions_per_day=20 / 30, reason=DET.format(r="0.66", ref="14.0", x="0.05"), delivery_ratio=0.05)
        it = self.item(d)
        self.assertEqual(it["klass"], "VERY_LOW")                                                # 20 impressioni: molto bassa, non anche "calo"
        ov = build([d])
        self.assertEqual([x["klass"] for x in ov["delivery"]], ["VERY_LOW"])
        zero = declining(2, tactical_impressions_per_day=0.0)
        self.assertEqual(self.item(zero)["klass"], "NO_DELIVERY")                                # priorita': nessuna erogazione
        self.assertEqual(len(build([zero])["delivery"]), 1)

    def test_no_new_operational_decision_and_kpis_unchanged(self):
        d = declining(1, reason=DET.format(r="4.93", ref="14.0", x="0.15"))
        ov = build([d])
        self.assertEqual([x["klass"] for x in ov["delivery"]], ["DECLINING"])
        self.assertEqual((ov["kpi"]["actions"], ov["kpi"]["delivery"], len(ov["actions"])), (0, 1, 0))
        self.assertEqual(d["final_action"], "HOLD")                                              # nessun bid-down/pausa/alza
        self.assertEqual(len(ov["all_ids"]), 1)


REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealCalisthenicsTests(unittest.TestCase):
    """Calisthenics for Beginners (B0GNZFLXCV, US) su COPIA del DB: rianalisi + decisioni rigenerate; DB reale, books.json e INPUT invariati."""

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
        cls.ok = bool(dst.execute("SELECT 1 FROM decision_runs WHERE book_id='B0GNZFLXCV'").fetchone())
        cls.text, cls.stored = "", {}
        if cls.ok:
            from kdp_ads.config import CONFIG_DIR, load_mapping, load_settings
            from kdp_ads.pipeline import run_analysis, run_decisions
            lg, s = logging.getLogger("t"), load_settings()
            ra = run_analysis(s, load_mapping(), CONFIG_DIR / "rules.json", "B0GNZFLXCV", Path(cls.tmp.name) / "out", lg, save_db=True, conn=dst, marketplace="US")
            rd = run_decisions(s, CONFIG_DIR / "rules.json", "B0GNZFLXCV", Path(cls.tmp.name) / "out", lg, ra["run_id"], save_db=True, conn=dst, marketplace="US")
            dst.commit()
            cls.run_id = rd["run_id"]
            cls.stored = {(r["campaign_id"], r["target"]): r["final_action"] for r in dst.execute("SELECT * FROM decisions WHERE run_id=?", (cls.run_id,))}
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(cls.db)
            try:
                at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
                tov.select(at, "Libro", "B0GNZFLXCV")
                tov.select(at, "Run decisionale", f"#{cls.run_id}")
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
            self.skipTest("libro reale B0GNZFLXCV non presente")

    def block(self, a, b):
        return self.text.split(a)[1].split(b)[0]

    def test_auto_complements_is_in_do_not_touch(self):
        self.assertFalse(self.at.exception)
        good = self.block("STA ANDANDO BENE / NON TOCCARE\n", "AVVISI DATI")
        self.assertIn("NON TOCCARE **AUTO · Complementi**", good)
        self.assertIn("Sta funzionando bene.", good)
        self.assertIn("ACOS storico 27.9% · ACOS ultimi 30 giorni 34.8% · Acquisti 2", good)
        self.assertNotIn("Nessun target importante da lasciare com'è.", good)

    def test_workout_has_no_reliable_baseline_so_no_decline_alert(self):
        deliv = self.block("EROGAZIONE DA VERIFICARE\nTarget attivi", "TARGET IN PAUSA RILEVANTI")
        self.assertNotIn("workout**", deliv.replace("calisthenic workout", ""))
        self.assertNotIn("l'erogazione è crollata", self.text)                         # nessun target Calisthenics ha un periodo comparabile del target
        self.assertEqual(self.stored[("FRASE", "workout")], "HOLD")                      # decisione invariata: nessuna nuova azione

    def test_existing_delivery_and_monitor_cards_are_unchanged(self):
        deliv = self.block("EROGAZIONE DA VERIFICARE\nTarget attivi", "TARGET IN PAUSA RILEVANTI")
        self.assertIn("EROGAZIONE MOLTO BASSA **FRASE · calisthenics for beginners book**", deliv)
        self.assertIn("EROGAZIONE MOLTO BASSA **ESATTA · calisthenics for beginners book**", deliv)
        mon = self.block("DA MONITORARE\n", "STA ANDANDO BENE")
        self.assertIn("MONITORA **ESATTA · calisthenics books**", mon)
        self.assertIn("MONITORA **FRASE · calisthenic workout**", mon)

    def test_no_operational_action_and_kpis(self):
        k = {m.label: m.value for m in self.at.metric if m.label in ("DA VALUTARE ORA", "EROGAZIONE DA VERIFICARE")}
        self.assertEqual(k, {"DA VALUTARE ORA": "0", "EROGAZIONE DA VERIFICARE": "2"})
        self.assertFalse([x for x in self.stored.values() if x.startswith(("BID_", "PAUSE"))])

    def test_nothing_written(self):
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), self.real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)
        inp = ROOT / "INPUT"
        now = sorted((str(p.relative_to(inp)), p.stat().st_size, p.stat().st_mtime_ns) for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
        self.assertEqual(now, self.inp_state)


if __name__ == "__main__":
    unittest.main()
