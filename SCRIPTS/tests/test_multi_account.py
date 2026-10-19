"""MULTI-ACCOUNT: due account Amazon Ads (A e B) nello stesso DB senza collisioni. L'identita' operativa e' (account, ID campagna): mapping, snapshot, revisioni, ignore, backfill,
materializzazione e decisioni sono per account. Fixture sintetiche con Account B DIVERSO ma con ID campagna / gruppo annunci / target e termini di ricerca UGUALI a quelli dell'Account A.
Workspace temporanei: mai il DB reale ne' books.json reale; nessuna rete, nessuna Amazon."""
import hashlib
import shutil
import os
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import test_global_reports as tg
import test_global_robustness as rb
from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads.db import connect
from ops import global_import as GI

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

ACC_A, NAME_A = "amzn1.ads-account.g.x", "Sponsored ads - Author"
ACC_B, NAME_B = "amzn1.ads-account.g.B2", "Sponsored ads - Tai Chi"
CID = "176948463003177"                                                  # lo STESSO ID campagna in entrambi gli account
TERM = "route 66 travel guide"                                           # lo STESSO termine di ricerca in entrambi gli account
TID = "1001"                                                             # lo STESSO ID target
RANGE30 = tg.RANGE30


def acct_camp(acct, acct_name, cid=CID, name="FRASE ROUTE66 - 5/8/2026 08:11:33.222", pf_id="111", pf="ROUTE 66"):
    c = tg.camp(cid, name, pf_id, pf)
    c["ID account inserzionista"], c["Nome account inserzionista"] = tg.xl(acct), acct_name
    return c


def tg_file(c, clicks=30, orders=3, rng=RANGE30, target="route 66"):
    return tg.csv_text(tg.TG_HEAD, [tg.tg(c, target, "PHRASE", TID, clicks, orders, 20.0 + clicks / 10, 22.99 * orders, rng)])


def st_file(c, clicks=12, orders=2, rng=RANGE30):
    return tg.csv_text(tg.ST_HEAD, [tg.st_row(c, TERM, "route 66", "PHRASE", TID, clicks, orders, 9.0 + clicks / 10, 22.99 * orders, rng)])


CA, CB = acct_camp(ACC_A, NAME_A), acct_camp(ACC_B, NAME_B)


class MA(tg.WsBase):
    def q(self, sql, *a):
        c = self.db()
        try:
            return [dict(r) for r in c.execute(sql, a)]
        finally:
            c.close()

    def imp(self, slot, data, name="f.csv", **kw):
        return GI.run_import(self.ws, {slot: (name, data)}, kw.pop("marketplace", None), recalc=False, **kw)

    def both_imported(self):
        self.imp("TARGETING_30D", tg_file(CA, 30, 3), "a_t30.csv")
        self.imp("TARGETING_30D", tg_file(CB, 18, 1), "b_t30.csv")
        self.imp("SEARCH_TERM_30D", st_file(CA, 12, 2), "a_s30.csv")
        self.imp("SEARCH_TERM_30D", st_file(CB, 7, 1), "b_s30.csv")

    def assoc_both(self, book_a=tg.BOOK_A, book_b=tg.BOOK_B, **kw):
        ra = GI.associate(self.ws, CID, book_a, account_id=ACC_A, recalc=False, **kw)
        rb_ = GI.associate(self.ws, CID, book_b, account_id=ACC_B, recalc=False, **kw)
        self.assertTrue(ra.ok, ra.errors)
        self.assertTrue(rb_.ok, rb_.errors)
        return ra, rb_


# ------------------------------------------------------------------ 1-5. stessi ID in due account: nessuna collisione
class SameIdsTwoAccountsTests(MA):
    def test_same_campaign_id_in_two_accounts_coexist_and_never_overwrite(self):
        self.both_imported()
        rows = self.q("SELECT account_id, amazon_campaign_id, status, account_name_last_seen FROM campaign_book_mapping ORDER BY account_id")
        self.assertEqual([(r["account_id"], r["amazon_campaign_id"], r["status"]) for r in rows], [(ACC_B, CID, "UNASSIGNED"), (ACC_A, CID, "UNASSIGNED")] if ACC_B < ACC_A else
                         [(ACC_A, CID, "UNASSIGNED"), (ACC_B, CID, "UNASSIGNED")])
        self.assertEqual({r["account_name_last_seen"] for r in rows}, {NAME_A, NAME_B})
        self.assoc_both()
        m = {r["account_id"]: r for r in self.q("SELECT * FROM campaign_book_mapping")}
        self.assertEqual((m[ACC_A]["book_id"], m[ACC_B]["book_id"]), (tg.BOOK_A, tg.BOOK_B))                 # Campaign ID 12345 -> Book A / Book B
        self.assertNotEqual((m[ACC_A]["book_id"], m[ACC_A]["campaign_id"]), (m[ACC_B]["book_id"], m[ACC_B]["campaign_id"]))

    def test_unique_key_is_account_plus_campaign(self):
        c = self.db()
        try:
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='campaign_book_mapping'").fetchone()[0]
            self.assertIn("UNIQUE (account_id, amazon_campaign_id)", sql)
            self.assertNotIn("amazon_campaign_id TEXT PRIMARY KEY", sql)
            c.execute("INSERT INTO campaign_book_mapping (account_id, amazon_campaign_id) VALUES ('acc1','9')")
            c.execute("INSERT INTO campaign_book_mapping (account_id, amazon_campaign_id) VALUES ('acc2','9')")       # stesso ID, altro account: ammesso
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO campaign_book_mapping (account_id, amazon_campaign_id) VALUES ('acc1','9')")   # stesso account e ID: vietato
        finally:
            c.close()

    def test_same_ad_group_target_and_search_term_in_two_accounts_have_zero_collisions(self):
        self.both_imported()
        self.assoc_both()
        a = self.q("SELECT amazon_account_id, amazon_ad_group_id, amazon_target_id, clicks FROM target_rows WHERE book_id=?", tg.BOOK_A)
        b = self.q("SELECT amazon_account_id, amazon_ad_group_id, amazon_target_id, clicks FROM target_rows WHERE book_id=?", tg.BOOK_B)
        self.assertEqual((len(a), len(b)), (1, 1))
        self.assertEqual((a[0]["amazon_ad_group_id"], a[0]["amazon_target_id"]), (b[0]["amazon_ad_group_id"], b[0]["amazon_target_id"]))   # ID uguali...
        self.assertEqual((a[0]["amazon_account_id"], b[0]["amazon_account_id"]), (ACC_A, ACC_B))                                       # ...account diversi
        self.assertEqual((a[0]["clicks"], b[0]["clicks"]), (30.0, 18.0))                                                               # nessuna fusione/somma
        sa = self.q("SELECT amazon_account_id, search_term, clicks FROM search_term_rows WHERE book_id=?", tg.BOOK_A)
        sb = self.q("SELECT amazon_account_id, search_term, clicks FROM search_term_rows WHERE book_id=?", tg.BOOK_B)
        self.assertEqual(([(r["search_term"], r["clicks"], r["amazon_account_id"]) for r in sa], [(r["search_term"], r["clicks"], r["amazon_account_id"]) for r in sb]),
                         ([(TERM, 12.0, ACC_A)], [(TERM, 7.0, ACC_B)]))
        raw = self.q("SELECT account_id, COUNT(*) n, SUM(clicks) c FROM global_report_rows WHERE target_id=? AND search_term IS NULL GROUP BY account_id", TID)
        self.assertEqual({(r["account_id"], r["c"]) for r in raw}, {(ACC_A, 30.0), (ACC_B, 18.0)})

    def test_same_campaign_id_same_name_different_account_are_two_campaigns(self):
        self.both_imported()
        self.assertEqual(CA["Nome della campagna"], CB["Nome della campagna"])
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE amazon_campaign_id=?", CID)[0]["n"], 2)


# ------------------------------------------------------------------ 5-6. snapshot e revisioni
class SnapshotsAndRevisionsTests(MA):
    def test_same_type_window_dates_in_two_accounts_are_two_valid_snapshots_not_duplicate_or_revision(self):
        self.imp("TARGETING_30D", tg_file(CA, 30, 3), "a.csv")
        pv = GI.preview(self.ws, {"TARGETING_30D": ("b.csv", tg_file(CB, 18, 1))})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["status"], pv["importable"], pv["revision_available"], pv["account_id"], pv["account_name"]), ("NUOVO", True, False, ACC_B, NAME_B))
        r = self.imp("TARGETING_30D", tg_file(CB, 18, 1), "b.csv")
        self.assertEqual([x["status"] for x in r.data["results"]], ["IMPORTED"])                                   # niente DUPLICATE / REVISION / SNAPSHOT_CONFLICT
        imps = self.q("SELECT account_id, account_name, status, window_label, date_from, date_to, marketplace FROM global_report_imports")
        self.assertEqual({(i["account_id"], i["status"]) for i in imps}, {(ACC_A, "ACTIVE"), (ACC_B, "ACTIVE")})
        self.assertEqual(len({(i["window_label"], i["date_from"], i["date_to"], i["marketplace"]) for i in imps}), 1)
        self.assertEqual({i["account_name"] for i in imps}, {NAME_A, NAME_B})

    def test_unique_active_index_includes_the_account(self):
        c = self.db()
        try:
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='ux_gri_active'").fetchone()[0]
            self.assertIn("COALESCE(account_id, '')", sql)
        finally:
            c.close()

    def test_a_revision_of_account_b_never_supersedes_account_a(self):
        self.both_imported()
        self.assoc_both()
        a_rows_before = self.q("SELECT COUNT(*) n, SUM(clicks) c FROM target_rows WHERE book_id=?", tg.BOOK_A)[0]
        v2 = tg_file(CB, 25, 2)                                                                                    # stesso snapshot logico dell'Account B, dati diversi
        pv = GI.preview(self.ws, {"TARGETING_30D": ("b2.csv", v2)})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["status"], pv["account_id"]), ("REVISIONE", ACC_B))
        self.assertEqual(pv["revision"]["old"]["file"], "b_t30.csv")                                              # la versione attiva confrontata e' quella di B
        pending = self.imp("TARGETING_30D", v2, "b2.csv")
        self.assertEqual(pending.data["revisions_pending"], ["TARGETING_30D"])                                     # senza conferma non si sovrascrive
        done = self.imp("TARGETING_30D", v2, "b2.csv", replace={"TARGETING_30D"})
        self.assertEqual(done.data["replaced"], ["TARGETING_30D"])
        st = {(r["account_id"], r["file_name"]): r["status"] for r in self.q("SELECT account_id, file_name, status FROM global_report_imports WHERE report_type='TARGETING'")}
        self.assertEqual(st[(ACC_A, "a_t30.csv")], "ACTIVE")                                                       # l'Account A non e' toccato
        self.assertEqual((st[(ACC_B, "b_t30.csv")], st[(ACC_B, "b2.csv")]), ("SUPERSEDED", "ACTIVE"))
        self.assertEqual(self.q("SELECT COUNT(*) n, SUM(clicks) c FROM target_rows WHERE book_id=?", tg.BOOK_A)[0], a_rows_before)       # dati derivati di A intatti
        self.assertEqual(self.q("SELECT SUM(clicks) c FROM target_rows WHERE book_id=?", tg.BOOK_B)[0]["c"], 25.0)                       # B ora ha la nuova versione

    def test_a_revision_of_account_a_never_supersedes_account_b(self):
        self.both_imported()
        self.imp("TARGETING_30D", tg_file(CA, 31, 4), "a2.csv", replace={"TARGETING_30D"})
        st = {(r["account_id"], r["file_name"]): r["status"] for r in self.q("SELECT account_id, file_name, status FROM global_report_imports WHERE report_type='TARGETING'")}
        self.assertEqual((st[(ACC_A, "a_t30.csv")], st[(ACC_A, "a2.csv")], st[(ACC_B, "b_t30.csv")]), ("SUPERSEDED", "ACTIVE", "ACTIVE"))


# ------------------------------------------------------------------ 7-8. mapping e ignore indipendenti
class MappingAndIgnoreTests(MA):
    def test_mapping_is_independent_per_account(self):
        self.both_imported()
        r = GI.associate(self.ws, CID, tg.BOOK_A, account_id=ACC_A, recalc=False)
        self.assertTrue(r.ok)
        st = {x["account_id"]: x["status"] for x in self.q("SELECT account_id, status FROM campaign_book_mapping")}
        self.assertEqual((st[ACC_A], st[ACC_B]), ("ASSIGNED", "UNASSIGNED"))                                       # B resta da associare
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE book_id=?", tg.BOOK_B)[0]["n"], 0)       # e nessun suo dato e' stato smistato
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE amazon_account_id=?", ACC_B)[0]["n"], 0)

    def test_an_id_present_in_two_accounts_cannot_be_associated_without_naming_the_account(self):
        self.both_imported()
        r = GI.associate(self.ws, CID, tg.BOOK_A, recalc=False)
        self.assertFalse(r.ok)
        self.assertIn("più account", " ".join(r.errors))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE status='ASSIGNED'")[0]["n"], 0)       # nessuna scelta a caso fra account

    def test_ignore_is_account_scoped(self):
        self.both_imported()
        self.assertFalse(GI.ignore(self.ws, CID).ok)                                                                # ambiguo: serve l'account
        self.assertTrue(GI.ignore(self.ws, CID, account_id=ACC_A).ok)
        st = {x["account_id"]: x["status"] for x in self.q("SELECT account_id, status FROM campaign_book_mapping")}
        self.assertEqual((st[ACC_A], st[ACC_B]), ("IGNORED", "UNASSIGNED"))                                       # Account A / 123 IGNORED non significa Account B / 123 IGNORED
        self.assertTrue(GI.unignore(self.ws, CID, account_id=ACC_A).ok)
        self.assertEqual({x["status"] for x in self.q("SELECT status FROM campaign_book_mapping")}, {"UNASSIGNED"})
        self.assertEqual(GI.counts(self.ws), {"UNASSIGNED": 2, "ASSIGNED": 0, "IGNORED": 0})

    def test_a_new_report_of_account_b_does_not_reopen_an_ignored_campaign_of_account_a(self):
        self.imp("TARGETING_30D", tg_file(CA), "a.csv")
        GI.ignore(self.ws, CID, account_id=ACC_A)
        self.imp("TARGETING_7D", tg_file(CB, 5, 1, tg.RANGE7), "b7.csv")
        st = {x["account_id"]: x["status"] for x in self.q("SELECT account_id, status FROM campaign_book_mapping")}
        self.assertEqual((st[ACC_A], st[ACC_B]), ("IGNORED", "UNASSIGNED"))


# ------------------------------------------------------------------ 11-13. rinomina e stesso ID
class RenameTests(MA):
    def test_rename_in_the_same_account_updates_the_name_not_the_identity(self):
        self.imp("TARGETING_30D", tg_file(CA), "a.csv")
        self.imp("TARGETING_7D", tg_file(acct_camp(ACC_A, NAME_A, CID, "FRASE ROUTE66 RINOMINATA"), 5, 1, tg.RANGE7), "a7.csv")
        rows = self.q("SELECT campaign_name_last_seen FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", ACC_A, CID)
        self.assertEqual([r["campaign_name_last_seen"] for r in rows], ["FRASE ROUTE66 RINOMINATA"])               # stesso ID, nome diverso, stesso account: UNA campagna

    def test_rename_in_account_b_leaves_account_a_untouched(self):
        self.both_imported()
        self.imp("TARGETING_7D", tg_file(acct_camp(ACC_B, NAME_B, CID, "TAI CHI RINOMINATA"), 5, 1, tg.RANGE7), "b7.csv")
        names = {r["account_id"]: r["campaign_name_last_seen"] for r in self.q("SELECT account_id, campaign_name_last_seen FROM campaign_book_mapping")}
        self.assertEqual(names[ACC_A], "FRASE ROUTE66 - 5/8/2026 08:11:33.222")
        self.assertEqual(names[ACC_B], "TAI CHI RINOMINATA")


# ------------------------------------------------------------------ 9-10. backfill e libro con due account
class BackfillAndBookTests(MA):
    def legacy(self, account=None):
        rb.add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", rb.LEGACY_PH)
        if account:
            c = connect(self.ws.db_path)
            c.execute("UPDATE campaigns SET amazon_account_id=? WHERE book_id=? AND campaign_id='FRASE'", (account, tg.BOOK_A))
            c.commit()
            c.close()

    def phrase(self, acct, name):
        return rb.phrase_report(acct_camp(acct, name, CID, "FRASE ROUTE66"))

    def test_backfill_is_never_proposed_across_accounts(self):
        self.legacy(ACC_A)                                                                  # la campagna storica e' dell'Account A
        self.imp("TARGETING_30D", self.phrase(ACC_B, NAME_B), "b.csv")
        self.assertEqual(GI.backfill_candidates(self.ws), [])                                 # una campagna di B non e' MAI il backfill di una campagna di A
        r = GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC_B, recalc=False, confirm_legacy_account=True)
        self.assertFalse(r.ok)                                                                # nemmeno forzando la chiamata con la conferma
        self.assertEqual(self.q("SELECT campaign_id FROM campaign_book_mapping")[0]["campaign_id"], None)

    def test_backfill_same_account_is_proposed_without_extra_confirmation(self):
        self.legacy(ACC_A)
        self.imp("TARGETING_30D", self.phrase(ACC_A, NAME_A), "a.csv")
        c = GI.backfill_candidates(self.ws)
        self.assertEqual([(x["account_id"], x["legacy_account_id"], x["needs_account_confirmation"], x["level"]) for x in c], [(ACC_A, ACC_A, False, "ALTA")])
        r = GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC_A, recalc=False)
        self.assertTrue(r.ok, r.errors)

    def test_legacy_without_account_is_never_auto_linked_and_needs_explicit_confirmation(self):
        self.legacy(None)                                                                   # legacy_account_unknown
        self.imp("TARGETING_30D", self.phrase(ACC_A, NAME_A), "a.csv")
        self.assertEqual({x["status"] for x in self.q("SELECT status FROM campaign_book_mapping")}, {"UNASSIGNED"})      # nessun collegamento automatico a nessun account
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaigns WHERE book_id=? AND amazon_account_id IS NOT NULL", tg.BOOK_A)[0]["n"], 0)
        cands = GI.backfill_candidates(self.ws)
        self.assertEqual([(x["account_id"], x["needs_account_confirmation"], x["level"]) for x in cands], [(ACC_A, True, "ALTA")])
        r = GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC_A, recalc=False)                 # senza conferma esplicita
        self.assertFalse(r.ok)
        self.assertIn("legacy_account_unknown", " ".join(r.errors))
        self.assertEqual(self.q("SELECT status FROM campaign_book_mapping")[0]["status"], "UNASSIGNED")
        r = GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC_A, recalc=False, confirm_legacy_account=True)
        self.assertTrue(r.ok, r.errors)
        m = self.q("SELECT * FROM campaign_book_mapping")[0]
        self.assertEqual((m["status"], m["backfill_of"], m["legacy_account_confirmed"]), ("ASSIGNED", "FRASE", 1))                       # tracciato
        self.assertEqual(self.q("SELECT amazon_account_id FROM campaigns WHERE book_id=? AND campaign_id='FRASE'", tg.BOOK_A)[0]["amazon_account_id"], ACC_A)
        # la campagna storica ora e' dell'Account A: una campagna identica dell'Account B non puo' piu' collegarsi
        self.imp("TARGETING_30D", self.phrase(ACC_B, NAME_B), "b.csv")
        self.assertEqual([x for x in GI.backfill_candidates(self.ws) if x["account_id"] == ACC_B], [])
        self.assertFalse(GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC_B, recalc=False, confirm_legacy_account=True).ok)

    def test_two_identical_amazon_campaigns_of_different_accounts_make_the_legacy_backfill_ambiguous(self):
        self.legacy(None)
        self.imp("TARGETING_30D", self.phrase(ACC_A, NAME_A), "a.csv")
        self.imp("TARGETING_30D", self.phrase(ACC_B, NAME_B), "b.csv")
        for acc in (ACC_A, ACC_B):                                                                                        # mai scelto a caso: nessuno dei due e' ALTA
            self.assertFalse(GI.associate(self.ws, CID, tg.BOOK_A, backfill_of="FRASE", account_id=acc, recalc=False, confirm_legacy_account=True).ok)
        self.assertEqual({x["status"] for x in self.q("SELECT status FROM campaign_book_mapping")}, {"UNASSIGNED"})

    def test_a_book_can_have_campaigns_in_two_accounts_listed_separately(self):
        self.both_imported()
        self.assoc_both(tg.BOOK_A, tg.BOOK_A)                                                 # stesso libro, due account, stesso ID campagna
        lst = GI.book_campaigns(self.ws, tg.BOOK_A)
        self.assertEqual(sorted(x["account_id"] for x in lst), sorted([ACC_A, ACC_B]))
        self.assertEqual(len({x["campaign_id"] for x in lst}), 2)                              # due campagne interne distinte
        self.assertEqual({x["account_name_last_seen"] for x in lst}, {NAME_A, NAME_B})
        self.assertEqual(len({r["account_id"] for r in self.q("SELECT amazon_account_id AS account_id FROM campaigns WHERE book_id=?", tg.BOOK_A)}), 2)
        self.assertEqual({r["amazon_account_id"] for r in self.q("SELECT DISTINCT amazon_account_id FROM target_rows WHERE book_id=?", tg.BOOK_A)}, {ACC_A, ACC_B})

    def test_internal_campaign_owned_by_another_account_is_never_fed_with_new_data(self):
        self.imp("TARGETING_30D", tg_file(CA, 30, 3), "a.csv")
        self.assertTrue(GI.associate(self.ws, CID, tg.BOOK_A, account_id=ACC_A, recalc=False).ok)
        c = connect(self.ws.db_path)
        c.execute("UPDATE campaigns SET amazon_account_id=? WHERE book_id=?", (ACC_B, tg.BOOK_A))        # simula una campagna interna appartenente a un ALTRO account
        c.commit()
        c.close()
        r = self.imp("TARGETING_7D", tg_file(CA, 5, 1, tg.RANGE7), "a7.csv")
        self.assertTrue(any("ACCOUNT_CONFLICT" in w for w in r.data["conflicts"]))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE book_id=? AND period_id LIKE '2026-10-07%'", tg.BOOK_A)[0]["n"], 0)


# ------------------------------------------------------------------ 14-16. file e account
class FileAccountTests(MA):
    def test_single_account_file_is_valid_and_shows_the_account(self):
        pv = GI.preview(self.ws, {"TARGETING_30D": ("a.csv", tg_file(CA))})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["account_id"], pv["account_name"], pv["problems"], pv["importable"]), (ACC_A, NAME_A, [], True))
        p = G.parse_global_report("a.csv", tg_file(CA))
        self.assertEqual((p["account_ids"], p["account_id"], p["account_name"]), ([ACC_A], ACC_A, NAME_A))

    def test_file_with_more_than_one_account_is_rejected(self):
        mixed = tg.csv_text(tg.TG_HEAD, [tg.tg(CA, "route 66", "PHRASE", TID, 30, 3, 20.0, 69.0), tg.tg(CB, "route 66", "PHRASE", TID, 18, 1, 20.0, 23.0)])
        pv = GI.preview(self.ws, {"TARGETING_30D": ("m.csv", mixed)})["TARGETING_30D"]["preview"]
        self.assertFalse(pv["importable"])
        self.assertIn("2 account Amazon Ads diversi", " ".join(pv["problems"]))
        r = GI.run_import(self.ws, {"TARGETING_30D": ("m.csv", mixed)}, recalc=False)
        self.assertEqual(r.data["imported"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], 0)                       # nessun import parziale
        c = connect(self.ws.db_path)
        with self.assertRaises(GS.GlobalImportError):
            GS.stage_file(c, G.parse_global_report("m.csv", mixed), "RECENT_30D", "US", {})
        c.close()

    def test_file_without_account_id_is_not_importable(self):
        c = dict(CA)
        c["ID account inserzionista"] = ""
        data = tg.csv_text(tg.TG_HEAD, [tg.tg(c, "route 66", "PHRASE", TID, 30, 3)])
        pv = GI.preview(self.ws, {"TARGETING_30D": ("n.csv", data)})["TARGETING_30D"]["preview"]
        self.assertFalse(pv["importable"])
        self.assertIn("non contiene l'ID account", " ".join(pv["problems"]))

    def test_identical_file_is_still_a_duplicate_only_within_its_own_account(self):
        self.imp("TARGETING_30D", tg_file(CA), "a.csv")
        r = self.imp("TARGETING_30D", tg_file(CA), "a_again.csv")
        self.assertEqual(r.data["imported"], 0)                                                                    # lo STESSO file dell'Account A resta un duplicato


# ------------------------------------------------------------------ 16-17. materializzazione e decisioni
class MaterializationAndDecisionsTests(MA):
    def test_materialization_keeps_the_account_on_every_derived_row_and_campaign(self):
        self.both_imported()
        self.assoc_both()
        for table in ("target_rows", "search_term_rows"):
            self.assertEqual({(r["book_id"], r["amazon_account_id"]) for r in self.q(f"SELECT DISTINCT book_id, amazon_account_id FROM {table}")},
                             {(tg.BOOK_A, ACC_A), (tg.BOOK_B, ACC_B)})
        self.assertEqual({(r["book_id"], r["amazon_account_id"]) for r in self.q("SELECT book_id, amazon_account_id FROM campaigns")}, {(tg.BOOK_A, ACC_A), (tg.BOOK_B, ACC_B)})
        raw = self.q("SELECT DISTINCT account_id, account_name FROM global_report_rows")
        self.assertEqual({(r["account_id"], r["account_name"]) for r in raw}, {(ACC_A, NAME_A), (ACC_B, NAME_B)})

    def test_decisions_keep_the_account_identity_and_ui_cards_show_it(self):
        self.both_imported()
        GI.associate(self.ws, CID, tg.BOOK_A, account_id=ACC_A, recalc=True)
        GI.associate(self.ws, CID, tg.BOOK_B, account_id=ACC_B, recalc=True)
        da = self.q("SELECT DISTINCT amazon_account_id, amazon_account_name FROM decisions WHERE book_id=?", tg.BOOK_A)
        db_ = self.q("SELECT DISTINCT amazon_account_id, amazon_account_name FROM decisions WHERE book_id=?", tg.BOOK_B)
        self.assertEqual(([(r["amazon_account_id"], r["amazon_account_name"]) for r in da], [(r["amazon_account_id"], r["amazon_account_name"]) for r in db_]),
                         ([(ACC_A, NAME_A)], [(ACC_B, NAME_B)]))
        from data_access import overview as O
        d = self.q("SELECT * FROM decisions WHERE book_id=? ORDER BY run_id DESC", tg.BOOK_B)[0]
        d = {**d, "final_action": "BID_DOWN_5_CANDIDATE", "operationally_actionable": 1, "bid_change_pct": -5.0, "current_bid": 1.0}
        card = O.decision_card(d, {"target_acos": 35.0, "break_even_acos": 41.0})
        ov = O.build_overview([d], {"target_acos": 35.0, "break_even_acos": 41.0, "royalty_per_copy": 11.0}, {"actionable": 1}, [], None)
        shown = [c for k in ("actions", "paused", "delivery", "monitor", "good") for c in ov[k]]
        self.assertTrue(shown and all(c["account"] == f"Account: {NAME_B} · {ACC_B}" for c in shown))
        self.assertIsInstance(card, dict)

    def test_legacy_decisions_have_no_account_and_no_invented_label(self):
        from data_access import overview as O
        self.assertIsNone(O.account_text(None))
        self.assertIsNone(O.account_text(""))
        self.assertEqual(O.account_text("acc1"), "Account: acc1")
        self.assertEqual(O.account_text("acc1", {"acc1": "Autore"}), "Account: Autore · acc1")

    def test_opportunity_records_carry_their_account(self):
        from kdp_ads.opportunities import compute_opportunities, make_ctx
        from kdp_ads.rules import load_rules
        ctx = make_ctx({"book_id": "B", "title": "T", "marketplace": "US", "currency": "USD", "price": 20.0, "royalty_per_copy": 8.0, "target_acos": 35.0, "break_even_acos": 40.0},
                       load_rules(ROOT / "CONFIG" / "rules.json"))
        import test_opportunities as to
        rows = [dict(r, amazon_account_id=ACC_A) for r in to.both("FRASE", "PHRASE", "p", "good term", 20, 3, 15.0)]
        res = compute_opportunities(rows, [], [{"campaign_id": "FRASE", "campaign_type": "PHRASE", "amazon_account_id": ACC_A}], ctx)
        self.assertEqual({r["amazon_account_id"] for r in res["records"]}, {ACC_A})


# ------------------------------------------------------------------ 17. schema/migrazione
class GrowthAccountTests(unittest.TestCase):
    """I suggerimenti di crescita risalgono al proprio account (decisione o record Search Term di origine)."""

    def test_growth_items_carry_their_account_and_the_card_shows_it(self):
        from kdp_ads import growth as Gr
        import test_overview as tov
        book = {"target_acos": 35.0, "break_even_acos": 41.0, "royalty_per_copy": 9.0}
        dec = tov.dec(1, final_action="HOLD", target="route 66 map", campaign_id="ESATTA", campaign_type="EXACT", target_status="ENABLED", current_bid=1.11, lifetime_orders=13.0,
                      lifetime_acos=14.0, tactical_clicks=13.0, tactical_orders=3.0, tactical_acos=20.4, amazon_account_id=ACC_B, amazon_account_name=NAME_B)
        rec = {**tov.opp("glute book", "SUGGEST_IN_PHRASE", campaign_id="AUTO", suggested_campaigns=["FRASE"]), "amazon_account_id": ACC_A}
        g = Gr.build_growth([dec], [rec], book, Gr.DEFAULTS)
        self.assertEqual(g["SCALE"][0]["decision"]["amazon_account_id"], ACC_B)
        self.assertEqual(g["AUTO_TO_PHRASE"][0]["record"]["amazon_account_id"], ACC_A)
        from components import growth_ui
        growth_ui.NAMES = {ACC_A: NAME_A, ACC_B: NAME_B}

        class Rec:
            def __init__(self):
                self.captions = []

            def container(self, **k):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def caption(self, text, *a, **k):
                self.captions.append(text)

            def __getattr__(self, name):
                return lambda *a, **k: None
        for item, who in ((g["SCALE"][0], f"Account: {NAME_B} · {ACC_B}"), (g["AUTO_TO_PHRASE"][0], f"Account: {NAME_A} · {ACC_A}")):
            st = Rec()
            growth_ui.card(st, item)
            self.assertIn(who, st.captions)
        growth_ui.NAMES = {}


class SchemaTests(unittest.TestCase):
    def test_old_single_key_mapping_is_migrated_without_losing_rows(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.db"
            c = sqlite3.connect(p)
            c.executescript("""CREATE TABLE campaign_book_mapping (amazon_campaign_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'UNASSIGNED', book_id TEXT, marketplace TEXT,
                campaign_id TEXT, campaign_type TEXT, suggested_book_id TEXT, suggestion_confidence TEXT, suggestion_reason TEXT, first_seen TEXT, last_seen TEXT,
                campaign_name_last_seen TEXT, portfolio_id_last_seen TEXT, portfolio_last_seen TEXT, ad_group_last_seen TEXT, detected_type TEXT, account_id TEXT, assigned_at TEXT, updated_at TEXT);
                CREATE INDEX ix_cbm_book ON campaign_book_mapping(book_id, status);
                INSERT INTO campaign_book_mapping (amazon_campaign_id,status,book_id,campaign_id,account_id,campaign_name_last_seen) VALUES ('1','ASSIGNED','B1','FRASE','accA','n1');
                INSERT INTO campaign_book_mapping (amazon_campaign_id,status,account_id) VALUES ('2','UNASSIGNED',NULL);""")
            GS.ensure_schema(c)
            rows = [tuple(r) for r in c.execute("SELECT account_id, amazon_campaign_id, status, book_id, campaign_id, campaign_name_last_seen FROM campaign_book_mapping ORDER BY amazon_campaign_id")]
            self.assertEqual(rows, [("accA", "1", "ASSIGNED", "B1", "FRASE", "n1"), ("", "2", "UNASSIGNED", None, None, None)])      # account sconosciuto = '' (mai inventato)
            self.assertIn("UNIQUE (account_id, amazon_campaign_id)", c.execute("SELECT sql FROM sqlite_master WHERE name='campaign_book_mapping'").fetchone()[0])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM campaign_book_mapping_v6_old").fetchone()[0], 2)                         # la vecchia tabella resta come audit
            GS.ensure_schema(c)                                                                                                        # idempotente
            self.assertEqual(c.execute("SELECT COUNT(*) FROM campaign_book_mapping").fetchone()[0], 2)
            c.execute("INSERT INTO campaign_book_mapping_v6_old (amazon_campaign_id, status, account_id) VALUES ('3','ASSIGNED','accZ')")   # la copia conservativa non e' mai letta dal runtime
            c.commit()
            self.assertEqual(GS.counts(c), {"UNASSIGNED": 1, "ASSIGNED": 1, "IGNORED": 0})
            self.assertEqual([a["account_id"] for a in GS.accounts(c)], ["", "accA"])
            self.assertEqual(GS.book_campaigns(c, "B1")[0]["amazon_campaign_id"], "1")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM campaign_book_mapping WHERE amazon_campaign_id='3'").fetchone()[0], 0)
            c.close()

    def test_migration_is_idempotent_and_additive_on_a_copy_of_the_controlled_snapshot(self):
        import realdata
        snap = realdata.snapshot()
        if not snap.is_file():
            self.skipTest("DB reale assente")
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            copy = Path(tmp) / "k.db"
            shutil.copyfile(snap, copy)
            tabs = ("imports", "target_rows", "search_term_rows", "decisions", "campaigns")
            c = sqlite3.connect(copy)
            n_before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}
            c.close()
            connect(copy).close()
            connect(copy).close()                                                                                                       # seconda apertura: nessun effetto
            c = sqlite3.connect(copy)
            self.assertEqual({t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in n_before}, n_before)                    # nessuna riga persa
            self.assertEqual(c.execute("SELECT COUNT(*) FROM campaign_book_mapping").fetchone()[0], 0)                                 # lo snapshot non ha dati globali
            c.close()
        before, after = realdata.real_hashes()
        self.assertEqual(before, after)                                                                                                 # DB reale mai scritto


# ------------------------------------------------------------------ 18. UI
@unittest.skipIf(AppTest is None, "streamlit non installato")
class UiTests(MA):
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
                         + [e.value for e in at.error] + [h.value for h in at.header] + [h.value for h in at.subheader])

    def test_import_preview_shows_the_amazon_account(self):
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / "13_Report_globali_Amazon_Ads.py"), default_timeout=120).run()
        up = at.get("file_uploader")
        up[1].upload("a.csv", tg_file(CA), "text/csv")
        up[2].upload("b.csv", st_file(CB), "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertIn("ACCOUNT AMAZON", t)
        self.assertIn(NAME_A, t)
        self.assertIn(ACC_A, t)
        self.assertIn(NAME_B, t)
        self.assertIn(ACC_B, t)
        self.assertIn("2 account Amazon Ads diversi", t)                                                          # avviso: batch con due account

    def test_association_page_groups_account_portfolio_campaign_and_shows_the_account_on_each_campaign(self):
        self.both_imported()
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / "14_Campagne_da_associare.py"), default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        for need in (f"ACCOUNT · {NAME_A} · {ACC_A}", f"ACCOUNT · {NAME_B} · {ACC_B}", f"ACCOUNT: {NAME_A} · {ACC_A}", f"ACCOUNT: {NAME_B} · {ACC_B}", f"ID campagna `{CID}`",
                     "stato: da associare"):
            self.assertIn(need, t, need)
        keys = [b.key for b in at.button if b.key and b.key.startswith("ca_go_")]
        self.assertEqual(len(keys), 2)                                                                              # un pulsante per (account, campagna): nessuna chiave duplicata
        [b for b in at.button if b.key == f"ca_go_{ACC_B}_{CID}"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        st = {r["account_id"]: r["status"] for r in self.q("SELECT account_id, status FROM campaign_book_mapping")}
        self.assertEqual((st[ACC_A], st[ACC_B]), ("UNASSIGNED", "ASSIGNED"))                                         # associare B non tocca A

    def test_books_page_lists_the_two_accounts_of_the_same_book_separately(self):
        self.both_imported()
        self.assoc_both(tg.BOOK_A, tg.BOOK_A)
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / "7_Gestione_libri.py"), default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        box = [s for s in at.selectbox if s.label == "Libro"][0]
        box.select(tg.BOOK_A).run()
        t = self.texts(at)
        self.assertIn("in 2 account Amazon Ads diversi", t)
        frames = [df.value for df in at.dataframe if "Account" in df.value.columns and "ID campagna Amazon" in df.value.columns]
        self.assertTrue(frames)
        accs = {str(a) for a in frames[0]["Account"].tolist()}
        self.assertEqual(accs, {f"{NAME_A} · {ACC_A}", f"{NAME_B} · {ACC_B}"})

    def test_home_has_an_account_filter_when_a_book_has_two_accounts(self):
        import test_overview as tov
        self.both_imported()
        self.assoc_both(tg.BOOK_A, tg.BOOK_A)
        res = GI.recalculate(self.ws, {(tg.BOOK_A, "US")})
        self.assertTrue(all(x["ok"] for x in res), res)
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "panoramica.py"), default_timeout=180).run()
        tov.select(at, "Libro", tg.BOOK_A)
        self.assertFalse(at.exception, at.exception)
        box = [s for s in at.selectbox if s.label == "Account Amazon Ads"]
        self.assertEqual(len(box), 1)
        labels = [str(o) for o in box[0].options]
        self.assertTrue(any(NAME_A in o for o in labels) and any(NAME_B in o for o in labels), labels)
        self.assertEqual(box[0].value, "ALL")
        box[0].select(ACC_B).run()
        self.assertFalse(at.exception, at.exception)

    def test_home_has_no_account_filter_with_a_single_account(self):
        import test_overview as tov
        self.imp("TARGETING_30D", tg_file(CA), "a.csv")
        GI.associate(self.ws, CID, tg.BOOK_A, account_id=ACC_A, recalc=False)
        GI.recalculate(self.ws, {(tg.BOOK_A, "US")})
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "panoramica.py"), default_timeout=180).run()
        tov.select(at, "Libro", tg.BOOK_A)
        self.assertFalse(at.exception, at.exception)
        self.assertEqual([s for s in at.selectbox if s.label == "Account Amazon Ads"], [])


if __name__ == "__main__":
    unittest.main()
