"""«HO FATTO UN'ALTRA MODIFICA» + gerarchia semantica del registro (PAUSA supera BID_DOWN) + stato da report vs azione manuale + testo del motivo con le metriche reali + sezione TARGET
PRODOTTO DA VALUTARE + isolamento per libro/marketplace/account. Caso reale: Rare Coins 'coin books for collectors' (raccomandazione BID_DOWN_10, l'utente ha invece messo il target
in PAUSA su Amazon). Workspace temporaneo: mai il DB reale, mai books.json reale, nessuna rete, nessuna Amazon."""
import hashlib
import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_recent_escalation as tre
from data_access import overview as O
from kdp_ads import manual_actions as MA
from kdp_ads import registry
from kdp_ads.db import connect
from ops import manual_ops

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

PAN = str(ROOT / "DASHBOARD" / "panoramica.py")
ACCT = "amzn1.ads-account.g.x"


class Base(tre.Scenario):
    """Scenario del caso reale (Rare Coins / Route 66 / Twenty Women) senza 65D ne' Lifetime."""
    LIFE, C65 = False, False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"], os.environ["KDP_ADS_DB"] = str(cls.root), str(cls.ws.db_path)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDownClass()

    def setUp(self):
        c = self.base.db()
        try:
            c.execute("DELETE FROM manual_actions")
            c.commit()
        finally:
            c.close()

    # ---- helpers
    def book(self, book_id):
        books = registry.load_books(self.root / "CONFIG" / "books.json")
        view = registry.book_view(books[book_id], "US")
        eco, _ = registry.book_economics(view, 0.5)
        return {**view, "book_id": book_id, "marketplace": "US", "break_even_acos": eco["break_even_acos"]}

    def decs(self, book_id):
        rid = self.q("SELECT MAX(run_id) r FROM decision_runs WHERE book_id=?", book_id)[0]["r"]
        return [dict(r) for r in self.q("SELECT * FROM decisions WHERE run_id=?", rid)]

    def overview(self, book_id, executed=None, decisions=None, opp=None):
        return O.build_overview(decisions if decisions is not None else self.decs(book_id), self.book(book_id), {"actionable": 1}, [], opp, None, "$", None, executed or [])

    def conn(self):
        return connect(self.ws.db_path)

    def register_other(self, d, action_type, value_after=None, when="2026-11-05T10:00:00"):
        payload = O.other_payload(O.other_identity(d), action_type, value_after)
        c = self.conn()
        try:
            return MA.record(c, executed_at=when, note="TEST", **payload)
        finally:
            c.close()

    def executed(self, book_id=tre.BOOK_RC):
        c = self.conn()
        try:
            return MA.list_actions(c, book_id, "US")
        finally:
            c.close()

    def coin(self):
        return self.decision(tre.BOOK_RC, tre.T_COLLECT)

    @staticmethod
    def targets(cards):
        return [c["decision"]["target"] for c in cards]


# ------------------------------------------------------------------ 1-3: raccomandazione BID_DOWN + PAUSA manuale
class PauseSupersedesBidDownTests(Base):
    def test_recommendation_is_bid_down_and_the_other_modification_registers_the_real_pause(self):
        d = self.coin()
        self.assertEqual(d["final_action"], "BID_DOWN_10_CANDIDATE")
        before = self.overview(tre.BOOK_RC)
        card = next(c for c in before["actions"] if c["decision"]["target"] == tre.T_COLLECT)
        self.assertEqual(card["register"]["action_type"], "BID_DOWN")                       # il bottone rapido registrerebbe la raccomandazione
        self.assertIn("PAUSE", MA.OTHER_ACTIONS[card["other"]["kind"]])                      # l'altra modifica consente di registrare quella VERA
        aid = self.register_other(d, "PAUSE")
        rows = self.q("SELECT * FROM manual_actions")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["action_id"], r["action_type"], r["status"], r["marketplace"], r["book_id"]), (aid, "PAUSE", "EXECUTED", "US", tre.BOOK_RC))
        self.assertEqual(r["amazon_campaign_id"], d["amazon_campaign_id"])
        self.assertTrue(r["target_identity"])
        self.assertIsNone(r["value_after"])                                                  # una pausa non ha un'offerta

    def test_a_manual_pause_removes_the_current_bid_down_from_today(self):
        d = self.coin()
        self.register_other(d, "PAUSE")
        ov = self.overview(tre.BOOK_RC, self.executed())
        self.assertNotIn(tre.T_COLLECT, self.targets(ov["actions"]))
        self.assertEqual([x["title"] for x in ov["done"] if tre.T_COLLECT in x["title"]] != [], True)
        self.assertEqual(ov["done"][0]["action_type"], "PAUSE")

    def test_the_semantic_hierarchy_pause_beats_bids_and_reactivate_beats_pause(self):
        self.assertIn("BID_DOWN", MA.SUPERSEDES["PAUSE"])
        self.assertIn("BID_UP", MA.SUPERSEDES["PAUSE"])
        self.assertIn("PAUSE", MA.SUPERSEDES["REACTIVATE"])
        d = self.coin()
        self.register_other(d, "PAUSE", when="2026-11-05T10:00:00")
        self.assertEqual(MA.classify(self.executed(), account_id=d["amazon_account_id"], marketplace="US", book_id=tre.BOOK_RC, action_type="BID_UP",
                                     campaign_id=d["campaign_id"], amazon_campaign_id=d["amazon_campaign_id"], target=d["target"], value_after=0.6)["state"], MA.DONE)
        self.register_other(d, "REACTIVATE", when="2026-11-06T10:00:00")                     # riattivato: la pausa non conta piu', il bid-down torna proponibile
        ov = self.overview(tre.BOOK_RC, self.executed())
        self.assertIn(tre.T_COLLECT, self.targets(ov["actions"]))

    def test_report_enabled_plus_manual_pause_is_waiting_for_confirmation_and_not_a_fake_paused(self):
        d = self.coin()
        self.assertEqual(d["target_status"], "ENABLED")
        rdate = O.report_date(d)
        self.assertTrue(rdate)
        pause_day = (datetime.fromisoformat(rdate) + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")
        self.register_other(d, "PAUSE", when=pause_day)
        entry = next(x for x in self.overview(tre.BOOK_RC, self.executed())["done"] if tre.T_COLLECT in x["title"])
        tm = entry["target_manual"]
        self.assertEqual(tm["state"], "PENDING")
        self.assertEqual(tm["report_status"], "ATTIVO")                                      # lo stato da report resta ATTIVO: non finge che Amazon abbia confermato PAUSED
        self.assertIn("ULTIMA AZIONE MANUALE: PAUSA registrata il", tm["manual_text"])
        self.assertIn("STATO DA REPORT: ATTIVO", tm["report_text"])
        self.assertEqual(tm["confirmation_text"], "IN ATTESA DI CONFERMA DAL PROSSIMO REPORT")

    def test_a_later_report_that_shows_paused_confirms_the_manual_action(self):
        d = self.coin()
        self.register_other(d, "PAUSE", when="2026-11-05T10:00:00")
        later = {**d, "target_status": "PAUSED", "final_action": "HOLD_PAUSED_TARGET", "operationally_actionable": 0, "tactical_period": "2026-10-12__2026-11-10",
                 "trend_period": "2026-11-04__2026-11-10"}
        decs = [later if x["decision_id"] == d["decision_id"] else x for x in self.decs(tre.BOOK_RC)]
        ov = self.overview(tre.BOOK_RC, self.executed(), decisions=decs)
        card = next(c for c in ov["paused"] + ov["monitor"] + ov["good"] if c["decision"]["decision_id"] == d["decision_id"]) if any(
            c["decision"]["decision_id"] == d["decision_id"] for c in ov["paused"] + ov["monitor"] + ov["good"]) else None
        tm = O.target_manual_status(later, self.executed())
        self.assertEqual(tm["state"], "CONFIRMED")
        self.assertEqual(tm["confirmation_text"], "CONFERMATO DAL REPORT")
        if card is not None:
            self.assertEqual(card["target_manual"]["state"], "CONFIRMED")

    def test_a_newer_report_still_showing_enabled_contradicts_the_pause_and_the_bid_down_returns(self):
        d = self.coin()
        old_day = (datetime.fromisoformat(O.report_date(d)) - timedelta(days=5)).strftime("%Y-%m-%dT10:00:00")
        self.register_other(d, "PAUSE", when=old_day)                                        # il report e' PIU' RECENTE della pausa ma dice ancora ATTIVO
        tm = O.target_manual_status(d, self.executed())
        self.assertEqual(tm["state"], "CONTRADICTED")
        ov = self.overview(tre.BOOK_RC, self.executed())
        self.assertIn(tre.T_COLLECT, self.targets(ov["actions"]))                           # la dichiarazione non vale: l'azione resta da fare
        self.assertEqual(ov["done"], [])

    def test_promote_to_exact_suppresses_the_same_future_promotion_and_negatives_keep_their_semantics(self):
        d = self.coin()
        ident = O.other_identity(d)
        c = self.conn()
        try:
            MA.record(c, executed_at="2026-11-05T10:00:00", **O.other_payload(ident, "PROMOTE_TO_EXACT"))
            MA.record(c, executed_at="2026-11-05T10:00:00", **O.other_payload(ident, "ADD_NEGATIVE_PHRASE"))
        finally:
            c.close()
        kw = dict(account_id=d["amazon_account_id"], marketplace="US", book_id=tre.BOOK_RC, campaign_id=d["campaign_id"], amazon_campaign_id=d["amazon_campaign_id"], search_term=d["target"])
        ex = self.executed()
        self.assertEqual(MA.classify(ex, action_type="PROMOTE_TO_EXACT", **kw)["state"], MA.DONE)
        self.assertEqual(MA.classify(ex, action_type="ADD_NEGATIVE_EXACT", **kw)["state"], MA.DONE)        # la frase copre anche l'esatta
        self.assertEqual(MA.classify(ex[:1], action_type="ADD_NEGATIVE_PHRASE", **kw)["state"], MA.TODO)   # la sola promozione non e' una negativa


# ------------------------------------------------------------------ 5: offerta realmente impostata
class RealBidTests(Base):
    def test_manual_bid_records_the_value_really_set_not_the_suggestion(self):
        d = self.coin()
        b = O.bid_info(d)
        real = round(b["suggested"] - 0.05, 2)
        self.assertLess(real, b["suggested"])
        self.register_other(d, "BID_DOWN", real)
        r = self.q("SELECT * FROM manual_actions")[0]
        self.assertEqual(r["action_type"], "BID_DOWN")
        self.assertAlmostEqual(r["value_before"], b["current"], places=2)                    # offerta del report
        self.assertAlmostEqual(r["value_after"], real, places=2)                             # NON il suggerimento
        self.assertNotAlmostEqual(r["value_after"], b["suggested"], places=2)

    def test_a_real_value_different_from_the_suggestion_is_a_new_action_not_a_duplicate(self):
        d = self.coin()
        b = O.bid_info(d)
        self.register_other(d, "BID_DOWN", round(b["suggested"] - 0.05, 2))
        ov = self.overview(tre.BOOK_RC, self.executed())
        card = next(c for c in ov["actions"] if c["decision"]["target"] == tre.T_COLLECT)
        self.assertTrue(card["new_after"])                                                   # il valore reale e' diverso da quello suggerito: non si nasconde

    def test_anti_repetition_still_holds_for_the_exact_recommendation(self):
        d = self.coin()
        payload = O.decision_register(d)                                                     # bottone rapido: esattamente la raccomandazione
        self.assertTrue(manual_ops.record_action(self.ws, payload, date(2026, 11, 5), payload["value_after"]).ok)
        self.assertTrue(manual_ops.record_action(self.ws, payload, date(2026, 11, 5), payload["value_after"]).ok)       # idempotente
        self.assertEqual(self.q("SELECT COUNT(*) n FROM manual_actions")[0]["n"], 1)
        ov = self.overview(tre.BOOK_RC, self.executed())
        self.assertNotIn(tre.T_COLLECT, self.targets(ov["actions"]))
        self.assertEqual(len([x for x in ov["done"] if tre.T_COLLECT in x["title"]]), 1)
        self.assertTrue(manual_ops.undo_action(self.ws, self.q("SELECT action_id a FROM manual_actions")[0]["a"]).ok)
        self.assertIn(tre.T_COLLECT, self.targets(self.overview(tre.BOOK_RC, self.executed())["actions"]))

    def test_only_compatible_actions_are_offered_and_free_action_types_are_refused(self):
        d = self.coin()
        asin = self.decision(tre.BOOK_RC, tre.T_ASIN)
        self.assertEqual(O.other_identity(d)["kind"], "TARGET")
        self.assertEqual(O.other_identity(asin)["kind"], "ASIN")
        self.assertNotIn("ADD_NEGATIVE_EXACT", MA.OTHER_ACTIONS["ASIN"])
        self.assertNotIn("PROMOTE_TO_EXACT", MA.OTHER_ACTIONS["ASIN"])
        self.assertEqual(set(MA.OTHER_ACTIONS["TERM"]), {"ADD_NEGATIVE_EXACT", "ADD_NEGATIVE_PHRASE", "PROMOTE_TO_EXACT"})
        with self.assertRaises(MA.ManualActionError):
            O.other_payload(O.other_identity(asin), "ADD_NEGATIVE_EXACT")
        with self.assertRaises(MA.ManualActionError):
            O.other_payload(O.other_identity(d), "DELETE_CAMPAIGN")
        self.assertEqual(set(MA.ACTION_CHOICES), set(MA.ACTION_TYPES))                       # etichette per ogni tipo, nessun campo libero

    def test_the_direction_of_the_bid_is_checked(self):
        d = self.coin()
        b = O.bid_info(d)
        bad = O.other_payload(O.other_identity(d), "BID_DOWN", b["current"] + 0.10)
        r = manual_ops.record_action(self.ws, bad, date(2026, 11, 5), None)
        self.assertFalse(r.ok)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM manual_actions")[0]["n"], 0)


# ------------------------------------------------------------------ 6: testo del motivo con le metriche reali
class ReasonTextTests(Base):
    def test_escalation_reason_uses_the_clicks_and_spend_of_the_window_that_triggered_it(self):
        d = self.coin()
        txt = O.short_reason(d, self.book(tre.BOOK_RC))
        self.assertIn("18 clic", txt)
        self.assertIn("$17,59", txt)
        self.assertIn("senza acquisti", txt)
        self.assertIn("9 clic", txt)                                                         # conferma 7D
        self.assertNotRegex(txt, r"(?<![0-9])0 clic")
        self.assertNotIn("n/d di spesa", txt)
        self.assertNotIn("0,0 volte", txt)

    def test_asin_reason_shows_30d_and_7d_not_zeros(self):
        d = self.decision(tre.BOOK_RC, tre.T_ASIN)
        txt = O.short_reason(d, self.book(tre.BOOK_RC))
        self.assertIn("130 clic", txt)
        self.assertIn("5 acquisti", txt)
        self.assertIn("ACOS 71,2%", txt)
        self.assertIn("14 clic", txt)                                                        # 7D
        self.assertNotRegex(txt, r"(?<![0-9])0 clic")
        self.assertNotIn("n/d di spesa", txt)
        self.assertNotIn("senza acquisti", txt)

    def test_pause_reason_uses_the_window_too_and_old_text_is_kept_when_lifetime_exists(self):
        b = self.book(tre.BOOK_RC)
        d = {"final_action": "PAUSE_CANDIDATE", "decision_rule": "R1P_RECENT_ZERO_ORDER_PAUSE", "lifetime_clicks": None, "lifetime_orders": None, "lifetime_spend": None,
             "tactical_clicks": 21, "tactical_orders": 0, "tactical_spend": 18.1, "tactical_window": "RECENT_30D", "trend_clicks": 6, "trend_orders": 0}
        txt = O.short_reason(d, b)
        self.assertIn("21 clic", txt)
        self.assertIn("$18,10", txt)
        self.assertIn("pausa", txt)
        legacy = {"final_action": "BID_DOWN_10_CANDIDATE", "decision_rule": "C3_X", "lifetime_clicks": 80, "lifetime_orders": 0, "lifetime_acos": None, "lifetime_spend": 40.0,
                  "tactical_clicks": 5, "tactical_orders": 0, "tactical_spend": 2.0}
        self.assertIn("80 clic", O.short_reason(legacy, b))                                  # senza escalation e con Lifetime: invariato


# ------------------------------------------------------------------ 7: sezione target prodotto
class ProductSectionTests(Base):
    def test_a_product_with_inefficient_parent_is_never_a_winner(self):
        from kdp_ads.opportunities import compute_opportunities, load_inputs, make_ctx
        from kdp_ads.decision import map_target_status
        from kdp_ads.rules import load_rules
        book = self.book(tre.BOOK_RC)
        c = self.conn()
        try:
            data = load_inputs(c, tre.BOOK_RC, "US", "US")
            opp = compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], make_ctx(book, load_rules(self.root / "CONFIG" / "rules.json")), map_target_status)
        finally:
            c.close()
        ov = self.overview(tre.BOOK_RC, opp=opp)
        prod = [p for p in ov["products"] if "0794851630" in p["asin"]]
        self.assertTrue(prod)
        self.assertEqual(prod[0]["state"], "INEFFICIENT")
        self.assertNotEqual(prod[0]["badge"], "TARGET PRODOTTO VINCENTE")
        self.assertFalse(prod[0]["winner"])
        self.assertIn("TARGET COMPLESSIVO INEFFICIENTE", prod[0]["badge"])
        self.assertEqual(set(O.PRODUCT_STATES), {"WINNER", "INEFFICIENT", "MONITOR"})

    @unittest.skipIf(AppTest is None, "streamlit non installato")
    def test_the_page_groups_products_under_a_neutral_title(self):
        at = AppTest.from_file(PAN, default_timeout=180).run()
        self.assertFalse(at.exception, at.exception)
        heads = [h.value for h in at.header]
        subs = [h.value for h in at.subheader]
        self.assertIn("TARGET PRODOTTO DA VALUTARE", heads)
        self.assertNotIn("TARGET PRODOTTO VINCENTE", heads)                                   # il titolo non e' piu' «vincente»
        self.assertTrue(any("TARGET COMPLESSIVO INEFFICIENTE" in s for s in subs), subs)
        self.assertNotIn("VINCENTI", subs)                                                    # nessun target prodotto vincente in questo caso


# ------------------------------------------------------------------ 9: isolamento
class IsolationTests(Base):
    def test_manual_action_never_leaks_across_account_marketplace_or_book(self):
        d = self.coin()
        self.register_other(d, "PAUSE")
        ex = self.executed()
        kw = dict(marketplace="US", book_id=tre.BOOK_RC, action_type="PAUSE", campaign_id=d["campaign_id"], amazon_campaign_id=d["amazon_campaign_id"], target=d["target"])
        self.assertEqual(MA.classify(ex, account_id=d["amazon_account_id"], **kw)["state"], MA.DONE)
        self.assertEqual(MA.classify(ex, account_id="amzn1.ads-account.g.OTHER", **{**kw, "amazon_campaign_id": d["amazon_campaign_id"]})["state"], MA.TODO)    # altro account
        self.assertEqual(MA.classify(ex, account_id=d["amazon_account_id"], **{**kw, "marketplace": "UK"})["state"], MA.TODO)                                 # altro marketplace
        self.assertEqual(MA.classify(ex, account_id=d["amazon_account_id"], **{**kw, "book_id": tre.BOOK_R66})["state"], MA.TODO)                             # altro libro
        self.assertEqual(MA.classify(ex, account_id=d["amazon_account_id"], **{**kw, "amazon_campaign_id": "999"})["state"], MA.TODO)                         # altra campagna
        other_acct = {**d, "amazon_account_id": "amzn1.ads-account.g.OTHER"}
        self.assertIsNone(O.target_manual_status(other_acct, ex))
        self.assertIsNone(O.target_manual_status({**d, "marketplace": "UK"}, ex))

    def test_the_overview_of_one_book_never_contains_cards_of_another(self):
        rc = self.overview(tre.BOOK_RC)
        r66 = self.overview(tre.BOOK_R66)
        self.assertTrue(rc["all_ids"] and r66["all_ids"])                                      # (gli id decisione sono per run: l'isolamento e' per libro, non per id)
        for ov, book in ((rc, tre.BOOK_RC), (r66, tre.BOOK_R66)):
            for k in ("actions", "paused", "monitor", "good", "delivery"):
                for c in ov[k]:
                    self.assertEqual(c["decision"]["book_id"], book)
        self.assertNotIn(tre.T_MAP, self.targets(rc["actions"]))
        self.assertNotIn(tre.T_COLLECT, self.targets(r66["actions"]))

    @unittest.skipIf(AppTest is None, "streamlit non installato")
    def test_switching_book_keeps_no_card_of_the_previous_book(self):
        at = AppTest.from_file(PAN, default_timeout=180).run()
        self.assertFalse(at.exception, at.exception)
        sel = at.sidebar.selectbox(key="kb_book")
        opts = list(sel.options)
        rc_label = next(o for o in opts if tre.BOOK_RC in o)
        r66_label = next(o for o in opts if tre.BOOK_R66 in o)
        txt = lambda a: "\n".join([m.value for m in a.markdown] + [c.value for c in a.caption] + [h.value for h in a.header] + [h.value for h in a.subheader])
        a1 = at.sidebar.selectbox(key="kb_book").select(rc_label).run()
        self.assertIn(tre.T_COLLECT, txt(a1))
        a2 = a1.sidebar.selectbox(key="kb_book").select(r66_label).run()
        self.assertFalse(a2.exception, a2.exception)
        t2 = txt(a2)
        self.assertIn(tre.T_MAP, t2)
        for leftover in (tre.T_COLLECT, tre.T_PAUSED, "0794851630", "FRASE_RARE_COINS", "PRODOTTI_RARE_COINS"):
            self.assertNotIn(leftover, t2, f"residuo del libro precedente: {leftover}")
        a3 = a2.sidebar.selectbox(key="kb_book").select(rc_label).run()
        self.assertNotIn(tre.T_MAP, txt(a3))                                                  # e viceversa


# ------------------------------------------------------------------ UI: flusso «HO FATTO UN'ALTRA MODIFICA» + persistenza
@unittest.skipIf(AppTest is None, "streamlit non installato")
class OtherModificationUiTests(Base):
    def run_page(self):
        at = AppTest.from_file(PAN, default_timeout=180).run()
        self.assertFalse(at.exception, at.exception)
        return at

    def key_for(self, at, prefix, decision_id):
        return next(w.key for w in list(at.selectbox) + list(at.button) + list(at.number_input) if str(w.key).startswith(prefix) and decision_id in str(w.key))

    def test_quick_button_and_other_modification_coexist_and_pause_replaces_the_bid_down(self):
        d = self.coin()
        before_books = hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest()
        at = self.run_page()
        labels = [e.label for e in at.expander]
        self.assertIn("HO FATTO QUESTA MODIFICA", labels)
        self.assertIn("HO FATTO UN'ALTRA MODIFICA", labels)
        k = self.key_for(at, "ma_oa_", d["decision_id"])
        sel = at.selectbox(key=k)
        self.assertEqual(len(sel.options), 8)                                                                                   # «— scegli —» + 7 azioni compatibili
        self.assertEqual(self.q("SELECT COUNT(*) n FROM manual_actions")[0]["n"], 0)                                            # scegliere non salva nulla
        at = sel.select_index(1 + MA.OTHER_ACTIONS["TARGET"].index("PAUSE")).run()
        info = " ".join(i.value for i in at.info)
        self.assertIn("Stai per registrare", info)
        self.assertIn("PAUSA", info)
        self.assertIn("coin books for collectors", info)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM manual_actions")[0]["n"], 0)                                            # prima della conferma: ancora nulla
        btn = at.button(key=self.key_for(at, "ma_oc_", d["decision_id"]))
        at = btn.click().run()
        self.assertFalse(at.exception, at.exception)
        rows = self.q("SELECT * FROM manual_actions")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["action_type"], rows[0]["status"], rows[0]["marketplace"], rows[0]["book_id"]), ("PAUSE", "EXECUTED", "US", tre.BOOK_RC))
        self.assertEqual(hashlib.sha256((self.root / "CONFIG" / "books.json").read_bytes()).hexdigest(), before_books)
        # la card BID_DOWN non e' piu' in COSA FARE OGGI; compare l'ultima azione manuale in attesa di conferma
        at2 = self.run_page()
        today = [m.value for m in at2.markdown if "coin books for collectors" in m.value and "GIÀ ESEGUITA" not in m.value and m.value.startswith("**")]
        self.assertEqual(today, [])                                                                                            # nessuna card del target fuori da «GIÀ ESEGUITO»
        self.assertNotIn("## ABBASSA OFFERTA DEL 10%", [m.value for m in at2.markdown])
        done = [e for e in at2.expander if e.label.startswith("GIÀ ESEGUITO")]
        self.assertEqual(len(done), 1)
        body = "\n".join(m.value for m in done[0].markdown)
        self.assertIn("ULTIMA AZIONE MANUALE: PAUSA registrata il", body)
        self.assertIn("STATO DA REPORT: ATTIVO", body)
        self.assertIn("IN ATTESA DI CONFERMA DAL PROSSIMO REPORT", body)
        at3 = self.run_page()                                                                                                  # «riavvio»: nuova sessione, stessa memoria
        self.assertEqual(len([e for e in at3.expander if e.label.startswith("GIÀ ESEGUITO")]), 1)

    def test_bid_flow_asks_for_the_real_value_and_saves_it(self):
        d = self.coin()
        b = O.bid_info(d)
        at = self.run_page()
        k = self.key_for(at, "ma_oa_", d["decision_id"])
        at = at.selectbox(key=k).select_index(1 + MA.OTHER_ACTIONS["TARGET"].index("BID_DOWN")).run()
        nk = self.key_for(at, "ma_ov_", d["decision_id"])
        real = round(b["suggested"] - 0.05, 2)
        at = at.number_input(key=nk).set_value(real).run()
        info = " ".join(i.value for i in at.info)
        self.assertIn("ABBASSA OFFERTA", info)
        self.assertIn(f"impostata ${real:.2f}", info)
        self.assertIn("il suggerimento era", info)
        at = at.button(key=self.key_for(at, "ma_oc_", d["decision_id"])).click().run()
        r = self.q("SELECT * FROM manual_actions")[0]
        self.assertEqual(r["action_type"], "BID_DOWN")
        self.assertAlmostEqual(r["value_before"], b["current"], places=2)
        self.assertAlmostEqual(r["value_after"], real, places=2)


if __name__ == "__main__":
    unittest.main()
