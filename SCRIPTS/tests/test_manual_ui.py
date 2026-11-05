"""PANORAMICA + REGISTRO AZIONI MANUALI (AppTest su workspace temporaneo): DA FARE OGGI / GIÀ ESEGUITO / NUOVA AZIONE DOPO MODIFICA PRECEDENTE, bottone «HO FATTO QUESTA MODIFICA» (solo
registrazione locale), badge del target prodotto non vincente, nota di frequenza dei report. Mai il DB reale, mai books.json reale, nessuna rete, nessuna Amazon."""
import hashlib
import json
import os
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_reports as tg
import test_recent_escalation as tre
from data_access import overview as O
from ops import global_import as GI
from ops import manual_ops

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

PAN = str(ROOT / "DASHBOARD" / "panoramica.py")
P13 = str(ROOT / "DASHBOARD" / "pages" / "13_Report_globali_Amazon_Ads.py")


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PanoramicaRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tg.WsBase()
        cls.base.setUp()
        cls.ws, cls.root = cls.base.ws, cls.base.root
        books = {"books": [b for b in tre.BOOKS["books"] if b["book_id"] == tre.BOOK_RC]}
        (cls.root / "CONFIG" / "books.json").write_text(json.dumps(books), encoding="utf-8")
        cls.env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"], os.environ["KDP_ADS_DB"] = str(cls.root), str(cls.ws.db_path)
        r = GI.run_import(cls.ws, tre.files(False, True), None, recalc=False)
        assert r.ok, r.errors
        for cid in ("555000111222333", "555000111222555", "555000111222444"):
            assert GI.associate(cls.ws, cid, tre.BOOK_RC, recalc=False).ok
        assert all(x["ok"] for x in GI.recalculate(cls.ws, {(tre.BOOK_RC, "US")}))
        cls.asin_dec = cls.row("SELECT * FROM decisions WHERE target=? ORDER BY run_id DESC LIMIT 1", tre.T_ASIN)
        cls.coll_dec = cls.row("SELECT * FROM decisions WHERE target=? ORDER BY run_id DESC LIMIT 1", tre.T_COLLECT)

    @classmethod
    def row(cls, sql, *a):
        return cls.base.q(sql, *a)[0]

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.base.tearDown()

    def setUp(self):
        c = self.base.db()
        try:
            c.execute("DELETE FROM manual_actions")
            c.commit()
        finally:
            c.close()

    def run_page(self):
        at = AppTest.from_file(PAN, default_timeout=180).run()
        self.assertFalse(at.exception, at.exception)
        return at

    def texts(self, at):
        return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [h.value for h in at.header] + [h.value for h in at.subheader]
                         + [i.value for i in at.info] + [s.value for s in at.success])

    def register(self, d, **over):
        reg = O.decision_register(d)
        reg.update(over)
        aid = None
        r = manual_ops.record_action(self.ws, reg, date(2026, 11, 4), reg.get("value_after"))
        self.assertTrue(r.ok, r.errors)
        return r.data["action_id"]

    def test_card_has_the_register_button_and_a_clear_statement_that_amazon_is_untouched(self):
        at = self.run_page()
        labels = [e.label for e in at.expander]
        self.assertIn("HO FATTO QUESTA MODIFICA", labels)
        self.assertIn("non cambia nulla su Amazon Ads", self.texts(at))
        self.assertNotIn("GIÀ ESEGUITO", " ".join(labels))                   # nessuna azione registrata: nessuna sezione «già eseguito»
        self.assertEqual(at.session_state["ov_account"] if "ov_account" in at.session_state else None, None)

    def test_registering_through_the_form_writes_only_the_local_registry(self):
        before_books = hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest()
        at = self.run_page()
        btns = [b for b in at.button if b.label == "Registra"]
        self.assertTrue(btns)
        k_before = {k.label: k.value for k in at.metric}["DA VALUTARE ORA"]
        btns[0].click().run()                                                                 # click REALE sul pulsante del form
        self.assertFalse(at.exception, at.exception)
        rows = self.base.q("SELECT action_type, status, account_id, marketplace, book_id, executed_at, value_after FROM manual_actions")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["status"], r["marketplace"], r["book_id"], r["account_id"]), ("EXECUTED", "US", tre.BOOK_RC, "amzn1.ads-account.g.x"))
        self.assertEqual(r["executed_at"][:10], date.today().isoformat())
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), before_books)        # books.json mai toccato
        self.assertEqual(self.base.q("SELECT COUNT(*) n FROM amazon_recommendations")[0]["n"], 0)
        self.assertLessEqual(int({k.label: k.value for k in self.run_page().metric}["DA VALUTARE ORA"]), int(k_before))        # l'azione registrata esce da «DA FARE»

    def test_executed_action_leaves_today_and_appears_in_done_with_its_date(self):
        at0 = self.run_page()
        k0 = {k.label: k.value for k in at0.metric}
        self.register(self.coll_dec, value_after=None)                                         # PAUSE candidata registrata come eseguita
        at = self.run_page()
        done = [e for e in at.expander if e.label.startswith("GIÀ ESEGUITO")]
        self.assertEqual(len(done), 1)
        body = "\n".join(m.value for m in done[0].markdown)
        self.assertIn("ULTIMA AZIONE MANUALE: PAUSA registrata il 04/", body)
        self.assertIn("IN ATTESA DI CONFERMA DAL PROSSIMO REPORT", body)
        self.assertNotIn(tre.T_COLLECT, "\n".join(m.value for m in at.markdown if "COSA FARE" in m.value))
        self.assertLess(int({k.label: k.value for k in at.metric}["DA VALUTARE ORA"]), int(k0["DA VALUTARE ORA"]))

    def test_new_bid_down_after_a_previous_one_is_marked_new_action_with_the_last_record(self):
        d = self.asin_dec
        self.assertTrue(d["final_action"].startswith("BID_DOWN"), d["final_action"])
        self.register(d, value_after=0.31)                                                     # modifica precedente diversa da quella ora suggerita
        at = self.run_page()
        sub = [s.value for s in at.subheader]
        self.assertTrue(any("NUOVA AZIONE DOPO MODIFICA PRECEDENTE" in s for s in sub), sub)
        txt = self.texts(at)
        self.assertIn("Ultima azione registrata:", txt)
        self.assertIn("Nuova raccomandazione:", txt)
        self.assertIn("04/11/2026", txt)

    def test_same_bid_down_is_not_repeated(self):
        d = self.asin_dec
        b = O.bid_info(d)
        self.register(d, value_after=b["suggested"])                                           # esattamente quanto suggerito
        at = self.run_page()
        sub = [s.value for s in at.subheader]
        self.assertFalse(any("NUOVA AZIONE" in s for s in sub))
        self.assertEqual(len([e for e in at.expander if e.label.startswith("GIÀ ESEGUITO")]), 1)
        # annullare la registrazione rimette l'azione in «DA FARE»
        aid = self.base.q("SELECT action_id FROM manual_actions")[0]["action_id"]
        self.assertTrue(manual_ops.undo_action(self.ws, aid).ok)
        at2 = self.run_page()
        self.assertEqual([e for e in at2.expander if e.label.startswith("GIÀ ESEGUITO")], [])

    def test_product_target_with_bad_parent_is_not_labelled_winner_on_the_page(self):
        at = self.run_page()
        txt = self.texts(at)
        self.assertIn("TERMINE/PRODOTTO INTERESSANTE, MA TARGET COMPLESSIVO INEFFICIENTE", txt)
        self.assertNotIn("TARGET PRODOTTO VINCENTE</span>", txt)


@unittest.skipIf(AppTest is None, "streamlit non installato")
class ReportsPageNoteTests(tg.WsBase):
    def test_frequency_note_and_six_slots_and_import_does_not_require_support_windows(self):
        env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"], os.environ["KDP_ADS_DB"] = str(self.root), str(self.ws.db_path)
        try:
            at = AppTest.from_file(P13, default_timeout=120).run()
            self.assertFalse(at.exception, at.exception)
            txt = "\n".join([i.value for i in at.info] + [m.value for m in at.markdown] + [c.value for c in at.caption])
            for s in ("Ogni settimana", "Targeting 7D", "Search Term 65D", "Ogni mese", "Targeting Lifetime", "finestre di SUPPORTO", "non si blocca"):
                self.assertIn(s, txt)
            self.assertEqual(len(at.get("file_uploader")), 6)
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
