"""EROGAZIONE MOLTO BASSA non vuol dire «alza l'offerta»: tre esiti distinti (MONITORA / NON ALZARE / TEST +5%), solo presentazione e sempre manuale.
La logica decisionale (final_action, regole) non cambia. Dati sintetici."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_overview as tov  # noqa: E402

BOOK = {"target_acos": 35.0, "break_even_acos": 39.1}


def very_low(i, **o):
    base = dict(target="route 66 map", campaign_id="FRASE", target_status="ENABLED", final_action="HOLD", tactical_impressions_per_day=0.5, tactical_clicks=0.0,
                lifetime_clicks=40.0, lifetime_orders=5.0, lifetime_acos=28.0, delivery_signal="DELIVERY_STABLE", delivery_source="TARGET", delivery_ratio=1.0)
    base.update(o)
    return tov.dec(i, **base)


class DeliveryAdviceTests(unittest.TestCase):
    def item(self, d):
        return tov.O.delivery_item(d, tov.parse_delivery_detail, BOOK)

    def test_strong_history_relevant_target_very_low_delivery_suggests_a_manual_test(self):
        it = self.item(very_low(1))
        self.assertEqual((it["klass"], it["advice"]["code"], it["advice"]["label"]), ("VERY_LOW", "TEST_PLUS_5", "TEST +5%"))
        self.assertIn("solo manuale", it["advice"]["text"])

    def test_inefficient_history_says_do_not_raise(self):
        for o in ({"lifetime_orders": 0.0, "lifetime_acos": None}, {"lifetime_acos": 55.0}):                 # clic senza acquisti / ACOS storico oltre il pareggio
            adv = self.item(very_low(2, **o))["advice"]
            self.assertEqual((adv["code"], adv["label"]), ("DO_NOT_RAISE", "NON ALZARE"), o)

    def test_irrelevant_target_says_do_not_raise_even_with_a_strong_history(self):
        adv = self.item(very_low(3, target="route 66 shirt"))["advice"]
        self.assertEqual(adv["code"], "DO_NOT_RAISE")
        self.assertIn("pertinente", adv["text"])

    def test_product_targets_are_not_judged_by_the_text_filter(self):
        self.assertEqual(self.item(very_low(4, target="asin=B0ABCDEFGH", target_type="PRODUCT_TARGET"))["advice"]["code"], "TEST_PLUS_5")

    def test_insufficient_data_says_monitor(self):
        for o in ({"lifetime_clicks": 6.0, "lifetime_orders": 0.0, "lifetime_acos": None}, {"lifetime_orders": 2.0}):       # pochi clic / pochi acquisti
            adv = self.item(very_low(5, **o))["advice"]
            self.assertEqual((adv["code"], adv["label"]), ("MONITOR", "MONITORA"), o)
            self.assertIn("Non alzare l'offerta", adv["text"])

    def test_other_delivery_classes_get_no_advice(self):
        no_delivery = self.item(very_low(6, tactical_impressions_per_day=0.0))
        self.assertEqual((no_delivery["klass"], no_delivery["advice"]), ("NO_DELIVERY", None))

    def test_the_decision_itself_is_untouched_and_never_a_bid_up(self):
        d = very_low(7)
        before = dict(d)
        it = self.item(d)
        self.assertEqual(d, before)                                           # nessuna modifica alla decisione
        self.assertEqual(d["final_action"], "HOLD")
        self.assertNotIn("ALZA", it["advice"]["text"].upper())
        ov = tov.build([d]) if hasattr(tov, "build") else None
        if ov:
            self.assertEqual(ov["kpi"]["actions"], 0)                         # nessuna nuova azione in DA VALUTARE ORA


if __name__ == "__main__":
    unittest.main()
