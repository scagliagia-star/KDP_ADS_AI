"""REGISTRO AZIONI MANUALI (schema v8): memoria locale delle modifiche gia' fatte a mano su Amazon Ads. Stessa azione gia' eseguita => non torna in «COSA FARE OGGI»; nuova azione
dopo una modifica precedente => «NUOVA AZIONE DOPO MODIFICA PRECEDENTE»; account / marketplace diversi non collidono. Fixture sintetiche, DB temporanei: mai il DB reale, mai
books.json reale, nessuna rete, nessuna Amazon."""
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

from data_access import overview as O
from data_access.repository import Repo
from kdp_ads import manual_actions as MA
from kdp_ads.db import connect
from ops import manual_ops, workspace

BOOK = {"book_id": "B0RARECOIN", "marketplace": "US", "target_acos": 35.0, "break_even_acos": 45.85, "royalty_per_copy": 11.0, "price": 23.99, "currency": "USD"}
ACC_A, ACC_B = "amzn1.ads-account.g.A", "amzn1.ads-account.g.B"
CAMP = "FRASE_RARE_COINS"
AMZ = "555000111222333"
KW = "coin books for collectors"


def db():
    d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    return d, connect(Path(d.name) / "t.db")


def dec(final="BID_DOWN_5_CANDIDATE", pct=-5.0, bid=0.81, target=KW, account=ACC_A, mp="US", amz=AMZ, camp=CAMP, status="ENABLED", did="D1", search_term=None, **kw):
    return {"decision_id": did, "run_id": 7, "book_id": BOOK["book_id"], "marketplace": mp, "campaign_id": camp, "amazon_campaign_id": amz, "amazon_account_id": account,
            "campaign_name": "FRASE RARE COINS", "campaign_type": "PHRASE", "target": target, "search_term": search_term, "final_action": final, "bid_change_pct": pct,
            "current_bid": bid, "operationally_actionable": 1, "priority": "P2", "target_status": status, "confidence": "MEDIUM", "lifetime_acos": 40.0, "lifetime_orders": 4,
            "lifetime_spend": 40.0, "lifetime_clicks": 60, **kw}


def rec(**kw):
    base = dict(action_type="BID_DOWN", book_id=BOOK["book_id"], marketplace="US", account_id=ACC_A, campaign_id=CAMP, amazon_campaign_id=AMZ, target=KW, value_before=0.81,
                value_after=0.77, executed_at="2026-10-28T10:00:00")
    base.update(kw)
    return base


def overview(decisions, executed, opp=None):
    return O.build_overview(decisions, BOOK, {"actionable": len(decisions)}, [], opp, None, "$", None, executed)


def opp_record(term, otype, **kw):
    r = {"search_term": term, "opportunity_type": otype, "campaign_id": CAMP, "amazon_campaign_id": AMZ, "amazon_account_id": ACC_A, "campaign_name": "FRASE RARE COINS",
         "campaign_type": "PHRASE", "parent_target": "coin books", "parent_targets": ["coin books"], "clicks": 20, "orders": 3, "spend": 20.0, "sales": 90.0, "acos": 22.0,
         "confidence": "HIGH", "already_in_exact": False, "negative_type": "EXACT"}
    r.update(kw)
    return r


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.c = db()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_create_and_read_with_every_field(self):
        aid = MA.record(self.c, **rec(source_decision_id="D1", source_run_id=7, note="fatto dal pannello", amazon_ad_group_id="9", amazon_target_id="2101"))
        rows = MA.list_actions(self.c)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["action_id"], aid)
        self.assertEqual((r["account_id"], r["marketplace"], r["book_id"], r["amazon_campaign_id"], r["amazon_ad_group_id"], r["amazon_target_id"]),
                         (ACC_A, "US", BOOK["book_id"], AMZ, "9", "2101"))
        self.assertEqual((r["action_type"], r["value_before"], r["value_after"], r["status"], r["target_identity"]), ("BID_DOWN", 0.81, 0.77, "EXECUTED", KW))
        self.assertEqual((r["source_decision_id"], r["source_run_id"], r["note"]), ("D1", 7, "fatto dal pannello"))
        self.assertTrue(r["created_at"] and r["executed_at"])

    def test_every_required_action_type_and_status_exists(self):
        sql = self.c.execute("SELECT sql FROM sqlite_master WHERE name='manual_actions'").fetchone()[0]
        for t in ("BID_DOWN", "BID_UP", "PAUSE", "REACTIVATE", "ADD_NEGATIVE_EXACT", "ADD_NEGATIVE_PHRASE", "PROMOTE_TO_EXACT", "EXECUTED", "UNDONE", "INVALIDATED"):
            self.assertIn(t, sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("INSERT INTO manual_actions (created_at, executed_at, marketplace, book_id, action_type) VALUES ('a','a','US','B','DELETE_ACCOUNT')")

    def test_invalid_registrations_are_refused_and_write_nothing(self):
        for bad in (dict(action_type="ZAP"), dict(marketplace=""), dict(campaign_id=None, amazon_campaign_id=None), dict(target=None), dict(value_after=None)):
            with self.assertRaises(MA.ManualActionError):
                MA.record(self.c, **rec(**bad))
        self.assertEqual(MA.list_actions(self.c), [])

    def test_identical_registration_is_idempotent(self):
        a = MA.record(self.c, **rec())
        b = MA.record(self.c, **rec())
        self.assertEqual(a, b)
        self.assertEqual(len(MA.list_actions(self.c)), 1)

    def test_undo_keeps_the_row_as_audit_and_the_action_becomes_proposable_again(self):
        aid = MA.record(self.c, **rec())
        MA.set_status(self.c, aid, "UNDONE")
        self.assertEqual(MA.list_actions(self.c), [])
        self.assertEqual(len(MA.list_actions(self.c, status=None)), 1)
        ov = overview([dec(pct=-5.0, bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(len(ov["actions"]), 1)
        self.assertEqual(ov["done"], [])


class DuplicateSuppressionTests(unittest.TestCase):
    """1-2. BID_DOWN registrato la settimana scorsa."""

    def setUp(self):
        self.tmp, self.c = db()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_same_bid_down_recommended_again_is_not_in_today_but_in_done(self):
        MA.record(self.c, **rec(value_before=0.81, value_after=0.77))
        # i dati non sono ancora aggiornati: l'offerta nel report e' ancora 0,81 e la raccomandazione e' ancora -5% (0,77)
        ov = overview([dec(pct=-5.0, bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(ov["actions"], [])
        self.assertEqual(ov["kpi"]["actions"], 0)
        self.assertEqual(len(ov["done"]), 1)
        d = ov["done"][0]
        self.assertEqual((d["badge"], d["source"], d["action_type"]), ("GIÀ ESEGUITA", "DECISION", "BID_DOWN"))
        self.assertIn("28/10/2026", d["executed_text"])
        self.assertIn("$0.81 → $0.77", d["executed_text"])
        self.assertIn("D1", ov["all_ids"])                      # la decisione resta consultabile: non sparisce

    def test_new_bid_down_after_a_previous_one_is_a_new_action_with_previous_details(self):
        MA.record(self.c, **rec(value_before=0.81, value_after=0.77))
        ov = overview([dec(pct=-5.0, bid=0.77)], MA.list_actions(self.c))                 # nuovo report: offerta 0,77, serve 0,73
        self.assertEqual(len(ov["actions"]), 1)
        c = ov["actions"][0]
        self.assertTrue(c["new_after"])
        self.assertEqual(c["manual"]["state"], "NEW_AFTER")
        self.assertIn("$0.81 → $0.77", c["manual"]["last_text"])
        self.assertEqual(c["bid"]["suggested"], 0.73)
        self.assertEqual(ov["actions_new_after"], [c])
        self.assertEqual(ov["actions_todo"], [])
        self.assertEqual(ov["done"], [])
        self.assertEqual(ov["kpi"]["actions"], 1)                                          # resta un'azione da fare: il target non sparisce per sempre

    def test_unrelated_target_is_untouched(self):
        MA.record(self.c, **rec())
        ov = overview([dec(pct=-5.0, bid=0.81), dec(target="other keyword", did="D2", bid=0.5)], MA.list_actions(self.c))
        self.assertEqual([c["id"] for c in ov["actions"]], ["D2"])
        self.assertEqual(ov["actions_todo"][0]["manual"]["state"], "TODO")

    def test_bid_up_after_a_bid_down_is_not_the_same_action(self):
        MA.record(self.c, **rec(value_before=0.81, value_after=0.77))
        res = MA.classify(MA.list_actions(self.c), account_id=ACC_A, marketplace="US", book_id=BOOK["book_id"], action_type="BID_UP", campaign_id=CAMP, amazon_campaign_id=AMZ,
                          target=KW, value_after=0.77)
        self.assertEqual(res["state"], "NEW_AFTER")


class PauseNegativeExactTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.c = db()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_pause_already_executed_is_not_proposed_again(self):
        MA.record(self.c, **rec(action_type="PAUSE", value_before=None, value_after=None))
        ov = overview([dec(final="PAUSE_CANDIDATE", pct=None, bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(ov["actions"], [])
        self.assertEqual((ov["done"][0]["action_type"], ov["done"][0]["badge"]), ("PAUSE", "GIÀ ESEGUITA"))

    def test_pause_then_reactivate_then_pause_again_is_a_new_action(self):
        MA.record(self.c, **rec(action_type="PAUSE", value_before=None, value_after=None, executed_at="2026-10-01T10:00:00"))
        MA.record(self.c, **rec(action_type="REACTIVATE", value_before=None, value_after=None, executed_at="2026-10-10T10:00:00"))
        ov = overview([dec(final="PAUSE_CANDIDATE", pct=None)], MA.list_actions(self.c))
        self.assertEqual(len(ov["actions"]), 1)
        self.assertTrue(ov["actions"][0]["new_after"])

    def test_negative_already_executed_is_not_proposed_again(self):
        MA.record(self.c, **rec(action_type="ADD_NEGATIVE_EXACT", target=None, search_term="free coin pictures", value_before=None, value_after=None))
        opp = {"records": [opp_record("free coin pictures", "NEGATIVE_CANDIDATE", negative_type="EXACT", orders=0, acos=None),
                           opp_record("coin clip art", "NEGATIVE_CANDIDATE", negative_type="EXACT", orders=0, acos=None)], "new_exact_campaign": None}
        ov = overview([], MA.list_actions(self.c), opp)
        self.assertEqual([i["term"] for i in ov["negatives"]], ["coin clip art"])
        self.assertEqual(ov["kpi"]["negatives"], 1)
        self.assertEqual([(d["source"], d["title"]) for d in ov["done"]], [("NEGATIVE", "free coin pictures")])

    def test_phrase_negative_covers_the_exact_one_but_not_vice_versa(self):
        MA.record(self.c, **rec(action_type="ADD_NEGATIVE_PHRASE", target=None, search_term="free coin", value_before=None, value_after=None))
        MA.record(self.c, **rec(action_type="ADD_NEGATIVE_EXACT", target=None, search_term="coin clip art", value_before=None, value_after=None))
        ex = MA.list_actions(self.c)
        kw = dict(account_id=ACC_A, marketplace="US", book_id=BOOK["book_id"], campaign_id=CAMP, amazon_campaign_id=AMZ)
        self.assertEqual(MA.classify(ex, action_type="ADD_NEGATIVE_EXACT", search_term="free coin", **kw)["state"], "DONE")
        self.assertEqual(MA.classify(ex, action_type="ADD_NEGATIVE_PHRASE", search_term="coin clip art", **kw)["state"], "TODO")

    def test_exact_promotion_already_executed_is_promoted_not_proposed(self):
        MA.record(self.c, **rec(action_type="PROMOTE_TO_EXACT", target=None, search_term="rare coin values", value_before=None, value_after=None))
        opp = {"records": [opp_record("rare coin values", "PROMOTE_TO_EXACT"), opp_record("old coin book", "PROMOTE_TO_EXACT")], "new_exact_campaign": None}
        ov = overview([], MA.list_actions(self.c), opp)
        self.assertEqual([i["term"] for i in ov["exact"]], ["old coin book"])
        self.assertEqual(ov["kpi"]["exact"], 1)
        self.assertEqual([(d["source"], d["title"], d["badge"]) for d in ov["done"]], [("EXACT", "rare coin values", "GIÀ ESEGUITA")])

    def test_already_paused_target_from_the_report_is_already_in_pause_not_a_pause_action(self):
        d = dec(final="HOLD_PAUSED_TARGET", pct=None, status="PAUSED", operationally_actionable=0, underlying_candidate_action="PAUSE_CANDIDATE")
        ov = overview([d], [])
        self.assertEqual(ov["actions"], [])
        self.assertEqual([(c["badge"], c["kind"]) for c in ov["paused"]], [("GIÀ IN PAUSA", "PAUSED")])


class IsolationTests(unittest.TestCase):
    """6. Account / marketplace diversi (e campagne omonime) non collidono."""

    def setUp(self):
        self.tmp, self.c = db()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_other_account_with_the_same_ids_is_not_suppressed(self):
        MA.record(self.c, **rec(account_id=ACC_A))
        ov = overview([dec(account=ACC_B, bid=0.81)], MA.list_actions(self.c))                 # stesso ID campagna, stesso target, ALTRO account
        self.assertEqual(len(ov["actions"]), 1)
        self.assertFalse(ov["actions"][0]["new_after"])
        self.assertEqual(ov["done"], [])

    def test_other_marketplace_with_the_same_ids_is_not_suppressed(self):
        MA.record(self.c, **rec(marketplace="US"))
        ov = overview([dec(mp="UK", bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(len(ov["actions"]), 1)
        self.assertEqual(ov["done"], [])
        res = MA.classify(MA.list_actions(self.c), account_id=ACC_A, marketplace="UK", book_id=BOOK["book_id"], action_type="BID_DOWN", campaign_id=CAMP, amazon_campaign_id=AMZ,
                          target=KW, value_after=0.77)
        self.assertEqual(res["state"], "TODO")

    def test_same_campaign_name_but_different_campaign_id_is_a_different_campaign(self):
        MA.record(self.c, **rec())
        ov = overview([dec(amz="999000999000999", camp="FRASE_RARE_COINS_2", bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(len(ov["actions"]), 1)

    def test_legacy_campaign_without_amazon_id_matches_by_internal_id_and_account_stays_separate(self):
        MA.record(self.c, **rec(account_id=None, amazon_campaign_id=None))                   # campagna storica: nessun ID Amazon, nessun account
        ov = overview([dec(account=None, amz=None, bid=0.81)], MA.list_actions(self.c))
        self.assertEqual(len(ov["done"]), 1)
        ov2 = overview([dec(account=ACC_A, amz=None, bid=0.81)], MA.list_actions(self.c))     # un account noto NON eredita le azioni di una campagna legacy_account_unknown
        self.assertEqual(len(ov2["actions"]), 1)


class GrowthAndStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.c = db()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_executed_bid_up_and_exact_are_removed_from_growth_suggestions(self):
        MA.record(self.c, **rec(action_type="BID_UP", value_before=0.5, value_after=0.53))
        growth = {"SCALE": [{"category": "SCALE", "campaign": CAMP, "target": KW, "suggested_bid": 0.53, "decision": dec(final="HOLD", pct=None)},
                            {"category": "SCALE", "campaign": CAMP, "target": "other", "suggested_bid": 0.4, "decision": dec(final="HOLD", pct=None, target="other")}],
                  "TO_EXACT": [], "counts": {"SCALE": 2, "TO_EXACT": 0, "AUTO_TO_PHRASE": 1}, "total": 3}
        out = O.filter_growth(growth, BOOK, MA.list_actions(self.c))
        self.assertEqual([i["target"] for i in out["SCALE"]], ["other"])
        self.assertEqual((out["counts"]["SCALE"], out["total"]), (1, 2))

    def test_card_carries_the_payload_to_register_locally(self):
        ov = overview([dec(pct=-5.0, bid=0.81)], [])
        reg = ov["actions"][0]["register"]
        self.assertEqual((reg["action_type"], reg["value_before"], reg["value_after"], reg["account_id"], reg["marketplace"], reg["source_decision_id"], reg["source_run_id"]),
                         ("BID_DOWN", 0.81, 0.77, ACC_A, "US", "D1", 7))

    def test_repo_reads_the_registry_read_only_and_old_db_without_the_table_gives_empty(self):
        MA.record(self.c, **rec())
        p = Path(self.tmp.name) / "t.db"
        self.c.commit()
        self.assertEqual(len(Repo(p).manual_actions(BOOK["book_id"], "US")), 1)
        self.assertEqual(Repo(p).manual_actions(BOOK["book_id"], "UK"), [])
        old = Path(self.tmp.name) / "old.db"
        sqlite3.connect(old).close()
        self.assertEqual(Repo(old).manual_actions(BOOK["book_id"], "US"), [])

    def test_list_actions_is_empty_without_the_table(self):
        c = sqlite3.connect(":memory:")
        self.assertEqual(MA.list_actions(c), [])


class DashboardOpsTests(unittest.TestCase):
    def test_record_through_the_dashboard_service_writes_only_the_local_registry(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            root = Path(d)
            for sub in ("CONFIG", "DATABASE", "LOGS", "INPUT", "OUTPUT"):
                (root / sub).mkdir()
            for f in ("rules.json", "settings.json", "column_mapping.json"):
                (root / "CONFIG" / f).write_bytes((ROOT / "CONFIG" / f).read_bytes())
            (root / "CONFIG" / "books.json").write_text('{"books": []}', encoding="utf-8")
            ws = workspace.from_root(root)
            connect(ws.db_path).close()
            from datetime import date
            r = manual_ops.record_action(ws, {k: v for k, v in rec().items() if k != "executed_at"}, date(2026, 11, 4), 0.77, "ok")
            self.assertTrue(r.ok, r.errors)
            rows = Repo(ws.db_path).manual_actions(BOOK["book_id"], "US")
            self.assertEqual((len(rows), rows[0]["executed_at"][:10], rows[0]["value_after"], rows[0]["note"]), (1, "2026-11-04", 0.77, "ok"))
            bad = manual_ops.record_action(ws, {"action_type": "BID_DOWN", "book_id": "B", "marketplace": "US"}, date(2026, 11, 4))
            self.assertFalse(bad.ok)
            u = manual_ops.undo_action(ws, r.data["action_id"])
            self.assertTrue(u.ok)
            self.assertEqual(Repo(ws.db_path).manual_actions(BOOK["book_id"], "US"), [])


if __name__ == "__main__":
    unittest.main()
