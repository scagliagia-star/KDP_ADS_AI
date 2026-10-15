"""REPORT GLOBALI AMAZON ADS: pagine di import e di associazione (AppTest su workspace temporaneo) e badge nel menu."""
import os
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import pyarrow as pa

import test_global_reports as tg
import test_global_robustness as rb
from kdp_ads.db import connect

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

P13 = str(ROOT / "DASHBOARD" / "pages" / "13_Report_globali_Amazon_Ads.py")
P14 = str(ROOT / "DASHBOARD" / "pages" / "14_Campagne_da_associare.py")
P8 = str(ROOT / "DASHBOARD" / "pages" / "8_Importa_report.py")


@unittest.skipIf(AppTest is None, "streamlit non installato")
class GlobalPagesTests(tg.WsBase):
    def setUp(self):
        super().setUp()
        self.env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"] = str(self.root)
        os.environ["KDP_ADS_DB"] = str(self.ws.db_path)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def texts(self, at):
        return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info] + [w.value for w in at.warning] + [s.value for s in at.success]
                         + [e.value for e in at.error] + [h.value for h in at.header] + [h.value for h in at.subheader] + [h.value for h in at.title])

    def test_import_page_has_four_slots_preview_then_import(self):
        at = AppTest.from_file(P13, default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual([u.label for u in at.get("file_uploader")], ["Targeting 7D", "Targeting 30D", "Search Term 7D", "Search Term 30D"])
        t = self.texts(at)
        self.assertIn("Nuovo flusso consigliato", t)
        self.assertIn("nessuna azione viene eseguita su Amazon Ads", t)
        before = self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"]
        up = at.get("file_uploader")
        up[1].upload("TG30.csv", tg.targeting_30(), "text/csv")
        up[2].upload("ST7.csv", tg.search_7(), "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        for need in ("Anteprima", "Targeting 30D", "Search Term 7D", "NUOVO"):
            self.assertIn(need, t, need)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], before)             # anteprima: nessuna scrittura
        self.assertTrue(any(m.label == "Righe valide" for m in at.metric))
        [b for b in at.button if b.label == "IMPORTA"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertGreater(self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"], before)
        t = self.texts(at)
        self.assertIn("Esito dell'import", t)
        self.assertIn("campagne nuove non ancora associate", t)
        self.assertEqual(GI_counts(self.ws)["UNASSIGNED"], 4)
        for df in at.dataframe:
            pa.Table.from_pandas(df.value)

    def test_invalid_file_is_a_clear_error_not_a_crash(self):
        at = AppTest.from_file(P13, default_timeout=120).run()
        at.get("file_uploader")[0].upload("bad.csv", b"a,b\n1,2\n", "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("Tipo di report non riconosciuto", self.texts(at))
        self.assertTrue([b for b in at.button if b.label == "IMPORTA"][0].disabled)

    def test_associate_page_flow_suggestion_confirm_ignore_and_persistence(self):
        from ops import global_import as GI
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        at = AppTest.from_file(P14, default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertIn("Campagne da associare", t)
        self.assertIn("campagne nuove non ancora associate", t)
        self.assertIn("È solo una proposta", t)
        self.assertIn("Suggerimento: **Route 66 Day by Day", t)
        self.assertEqual(GI.counts(self.ws)["ASSIGNED"], 0)                                                 # la pagina da sola non associa nulla
        cid = "555000111222333"
        [b for b in at.button if b.key == f"ca_go_{cid}"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(GI.counts(self.ws), {"UNASSIGNED": 3, "ASSIGNED": 1, "IGNORED": 0})
        m = self.q("SELECT status, book_id FROM campaign_book_mapping WHERE amazon_campaign_id=?", cid)[0]
        self.assertEqual((m["status"], m["book_id"]), ("ASSIGNED", tg.BOOK_B))                              # il libro suggerito era quello giusto
        at = AppTest.from_file(P14, default_timeout=120).run()
        [b for b in at.button if b.key == "ca_ig_176948463003177"][0].click().run()
        self.assertEqual(GI.counts(self.ws), {"UNASSIGNED": 2, "ASSIGNED": 1, "IGNORED": 1})
        at = AppTest.from_file(P14, default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("RIMUOVI IGNORA", [b.label for b in at.button])
        for df in at.dataframe:
            pa.Table.from_pandas(df.value)

    def test_bulk_portfolio_association_needs_explicit_confirmation(self):
        from ops import global_import as GI
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        at = AppTest.from_file(P14, default_timeout=120).run()
        go = [b for b in at.button if b.key == "ca_pfgo_ROUTE 66"][0]
        self.assertTrue(go.disabled)                                                                         # senza la conferma il pulsante e' spento
        at.checkbox(key="ca_pfok_ROUTE 66").check().run()
        [b for b in at.button if b.key == "ca_pfgo_ROUTE 66"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(GI.counts(self.ws)["ASSIGNED"], 3)                                                  # le 3 campagne del portfolio ROUTE 66, non quella di Rare Coins
        self.assertEqual(GI.counts(self.ws)["UNASSIGNED"], 1)

    def test_legacy_import_page_still_works_and_points_to_the_new_flow(self):
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "app.py"), default_timeout=120).run()
        at = at.switch_page("pages/8_Importa_report.py").run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertIn("NUOVO FLUSSO CONSIGLIATO", t)
        self.assertIn("IMPORT LEGACY", t)

    def test_menu_has_the_new_pages_and_numeric_badge(self):
        import ui_labels as U
        titles = [t for _p, t in U.NAV_PAGES]
        self.assertLess(titles.index("Report globali Amazon Ads"), titles.index("Importa report"))            # il nuovo flusso viene prima del legacy
        self.assertIn("Campagne da associare", titles)
        from ops import global_import as GI
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "app.py"), default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        src = (ROOT / "DASHBOARD" / "app.py").read_text(encoding="utf-8")
        self.assertIn("campaign_book_mapping WHERE status='UNASSIGNED'", src)

    def test_revision_page_flow_confirm_or_cancel_never_overwrites_silently(self):
        from ops import global_import as GI
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        v2 = rb.targeting_30_revised()
        at = AppTest.from_file(P13, default_timeout=120).run()
        at.get("file_uploader")[1].upload("t30_v2.csv", v2, "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertIn("Esiste già un report per questa finestra, ma il file contiene dati diversi", t)
        labels = [b.label for b in at.button]
        self.assertIn("USA VERSIONE PIÙ RECENTE", labels)
        self.assertIn("ANNULLA", labels)
        self.assertTrue([b for b in at.button if b.label == "IMPORTA"][0].disabled)                       # la revisione non e' importabile "per sbaglio"
        before = self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"]
        [b for b in at.button if b.label == "ANNULLA"][0].click().run()
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], before)           # ANNULLA: nulla cambia
        at = AppTest.from_file(P13, default_timeout=120).run()
        at.get("file_uploader")[1].upload("t30_v2.csv", v2, "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        [b for b in at.button if b.label == "USA VERSIONE PIÙ RECENTE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual([r["status"] for r in self.q("SELECT status FROM global_report_imports ORDER BY gimport_id")], ["SUPERSEDED", "ACTIVE"])

    def test_backfill_section_is_a_suggestion_and_needs_confirmation(self):
        from ops import global_import as GI
        rb.add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", rb.LEGACY_PH)
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", rb.phrase_report(tg.C_PH))}, recalc=False)
        at = AppTest.from_file(P14, default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertTrue(any("BACKFILL SUGGERITO" in e.label for e in at.expander))
        self.assertIn("Mai per solo tipo o nome", t)
        self.assertNotIn("storico esistente", t)                                                              # nessuna opzione di fusione nel flusso normale
        self.assertEqual(GI.counts(self.ws)["ASSIGNED"], 0)
        [b for b in at.button if b.key == "ca_bf_176948463003177"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(self.q("SELECT campaign_id FROM campaign_book_mapping WHERE amazon_campaign_id='176948463003177'")[0]["campaign_id"], "FRASE")


def GI_counts(ws):
    from ops import global_import as GI
    return GI.counts(ws)


if __name__ == "__main__":
    unittest.main()
