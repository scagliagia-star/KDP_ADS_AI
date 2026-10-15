"""REPORT GLOBALI · ROBUSTEZZA: migrazione di schema (SCHEMA_VERSION), report rigenerati nello stesso periodo (revisioni), identita' = Campaign ID (mai fusioni),
backfill verificato, persistenza dell'associazione al cambio nome, creazione libro validata con scrittura atomica, MISSING_7D vs NO_TRAFFIC_7D."""
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_reports as tg
from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads import registry
from kdp_ads.db import SCHEMA_VERSION, connect
from ops import global_import as GI

NEW_TABLES = {"global_report_imports", "global_report_rows", "campaign_book_mapping", "competitor_research_projects", "competitor_opportunities"}
NEW_COLS = {"amazon_campaign_id", "amazon_ad_group_id", "amazon_target_id", "row_date_from", "row_date_to"}


# ------------------------------------------------------------------ 1. SCHEMA_VERSION e migrazione
class SchemaMigrationTests(unittest.TestCase):
    def make_v5(self, d):
        """DB come era alla v5: senza le tabelle/colonne additive, con dati reali di prova."""
        p = Path(d) / "k.db"
        c = connect(p)
        c.execute("INSERT INTO books (book_id,title,marketplace) VALUES ('B1','Uno','US')")
        c.execute("INSERT INTO imports (book_id,campaign_id,report_type,import_timestamp,source_filename,file_path,file_sha256,marketplace,period_id,snapshot_id) "
                  "VALUES ('B1','C1','TARGETING','t','f.csv','p','h','US','P1','B1|C1|TARGETING|P1')")
        iid = c.execute("SELECT import_id FROM imports").fetchone()[0]
        c.execute("INSERT INTO target_rows (import_id,book_id,campaign_id,report_type,import_timestamp,source_filename,marketplace,period_id,target,clicks) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (iid, "B1", "C1", "TARGETING", "t", "f.csv", "US", "P1", "kw uno", 7))
        c.commit()
        for t in ("global_report_rows", "global_report_imports", "campaign_book_mapping", "competitor_opportunity_keywords", "competitor_opportunities", "competitor_keyword_observations",
                  "competitor_research_keywords", "competitor_research_sources", "competitor_research_projects"):
            c.execute(f"DROP TABLE {t}")                                                         # solo nel TEST: ricostruisce lo stato pre-migrazione
        for t in ("target_rows", "search_term_rows"):
            for col in NEW_COLS:
                c.execute(f"ALTER TABLE {t} DROP COLUMN {col}")
        c.execute("PRAGMA user_version=5")
        c.commit()
        c.close()
        return p

    def test_schema_version_is_6_and_reflects_the_additive_change(self):
        self.assertEqual(SCHEMA_VERSION, 6)
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "db.py").read_text(encoding="utf-8")
        self.assertIn("v5 -> v6", src)

    def test_old_v5_db_migrates_without_losing_data_and_with_one_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make_v5(d)
            c = sqlite3.connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertFalse(NEW_TABLES & {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            c.close()
            c = connect(p)
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(NEW_TABLES <= names)
            for t in ("target_rows", "search_term_rows"):
                self.assertTrue(NEW_COLS <= {r[1] for r in c.execute(f"PRAGMA table_info({t})")})
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(c.execute("SELECT title FROM books WHERE book_id='B1'").fetchone()[0], "Uno")             # nessuna perdita
            row = c.execute("SELECT target, clicks, amazon_campaign_id FROM target_rows").fetchone()
            self.assertEqual((row[0], row[1], row[2]), ("kw uno", 7, None))                                            # righe storiche: colonne nuove NULL
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            c.close()
            backups = list((Path(d) / "backups").glob("kdp_ads_pre_schema_v6_*.db"))
            self.assertEqual(len(backups), 1)                                                                          # un backup prima della migrazione
            b = sqlite3.connect(backups[0])
            self.assertEqual(b.execute("PRAGMA user_version").fetchone()[0], 5)                                       # la copia e' PRE-migrazione
            self.assertEqual(b.execute("SELECT clicks FROM target_rows").fetchone()[0], 7)
            b.close()

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make_v5(d)
            connect(p).close()
            def snap():
                cx = sqlite3.connect(p)
                try:
                    return cx.execute("SELECT COUNT(*) FROM target_rows").fetchone()[0]
                finally:
                    cx.close()
            n = snap()
            for _ in range(3):
                connect(p).close()
            self.assertEqual(snap(), n)
            self.assertEqual(len(list((Path(d) / "backups").glob("*.db"))), 1)                                         # nessun backup ripetuto
            c = connect(p)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 6)
            c.close()

    def test_new_db_is_born_with_the_new_schema_without_backup(self):
        with tempfile.TemporaryDirectory() as d:
            c = connect(Path(d) / "new.db")
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertTrue(NEW_TABLES <= {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertTrue(NEW_COLS <= {r[1] for r in c.execute("PRAGMA table_info(search_term_rows)")})
            c.close()
            self.assertFalse((Path(d) / "backups").exists())

    def test_no_destructive_statements_in_the_new_modules(self):
        for f in ("globalstore.py", "globalreport.py", "competitor_store.py"):
            src = (ROOT / "SCRIPTS" / "kdp_ads" / f).read_text(encoding="utf-8")
            self.assertNotRegex(src, r"DROP\s+(TABLE|COLUMN|INDEX)", f)
            self.assertNotIn("PRAGMA writable_schema", src)

    @unittest.skipUnless((ROOT / "DATABASE" / "kdp_ads.db").is_file(), "DB reale assente")
    def test_copy_of_the_real_db_migrates_keeping_every_row(self):
        real = ROOT / "DATABASE" / "kdp_ads.db"
        h = hashlib.sha256(real.read_bytes()).hexdigest()
        src = sqlite3.connect(f"{real.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.db"
            dst = sqlite3.connect(p)
            src.backup(dst)
            dst.close()
            src.close()
            c = sqlite3.connect(p)
            c.execute("PRAGMA user_version=5")
            c.commit()
            tabs = ("imports", "target_rows", "search_term_rows", "recommendations", "decisions", "decision_runs", "analysis_runs", "campaigns", "books")
            before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}
            c.close()
            c = connect(p)
            self.assertEqual({t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tabs}, before)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 6)
            c.close()
        self.assertEqual(hashlib.sha256(real.read_bytes()).hexdigest(), h)


# ------------------------------------------------------------------ 2. report rigenerato nello stesso periodo
def targeting_30_revised(clicks=85, orders=8, drop_auto=False):
    rows = [tg.tg(tg.C_PH, "route 66", "PHRASE", "1001", clicks, orders, 95.0, 300.0), tg.tg(tg.C_EX, "route 66 map", "EXACT", "1006", 15, 3, 12.0, 70.0),
            tg.tg(tg.C_RC, "rare coin", "PHRASE", "2001", 40, 4, 40.0, 96.0)]
    if not drop_auto:
        rows += [tg.tg(tg.C_AU, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 30, 2, 20.0, 60.0)]
    return tg.csv_text(tg.TG_HEAD, rows)


class RevisionTests(tg.WsBase):
    def setUp(self):
        super().setUp()
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        for cid, b in (("176948463003177", tg.BOOK_A), ("206528816350500", tg.BOOK_A), ("555000111222333", tg.BOOK_B)):
            self.assoc(cid, b)

    def counts(self):
        return {t: self.q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("global_report_imports", "global_report_rows", "imports", "target_rows", "campaign_book_mapping")}

    def clicks_of(self, aid):
        return self.q("SELECT SUM(clicks) c FROM target_rows WHERE amazon_campaign_id=?", aid)[0]["c"]

    def test_a_same_hash_is_a_duplicate_and_skipped(self):
        before = self.counts()
        pv = GI.preview(self.ws, {"TARGETING_30D": ("again.csv", tg.targeting_30())})["TARGETING_30D"]["preview"]
        self.assertEqual(pv["status"], "DUPLICATO")
        r = GI.run_import(self.ws, {"TARGETING_30D": ("again.csv", tg.targeting_30())}, recalc=False, replace={"TARGETING_30D"})
        self.assertFalse(r.ok)
        self.assertEqual(self.counts(), before)                                                      # nemmeno con replace: stesso hash = nessuna modifica

    def test_b_same_logical_snapshot_different_hash_is_a_revision_with_a_clear_message(self):
        before = self.counts()
        pv = GI.preview(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised())})["TARGETING_30D"]["preview"]
        self.assertEqual(pv["status"], "REVISIONE")
        self.assertTrue(pv["revision_available"])
        self.assertFalse(pv["importable"])
        self.assertTrue(any("Esiste già un report per questa finestra, ma il file contiene dati diversi" in w for w in pv["warnings"]))
        self.assertEqual((pv["revision"]["old"]["file"], pv["revision"]["old"]["revision"]), ("t30.csv", 1))
        self.assertNotEqual(pv["revision"]["old"]["rows"], pv["revision"]["new"]["rows"])
        cn = connect(self.ws.db_path)
        try:
            st = GS.stage_file(cn, G.parse_global_report("v2.csv", targeting_30_revised()), "RECENT_30D", "US", {})
        finally:
            cn.close()
        self.assertEqual(st["status"], "REVISION_AVAILABLE")                                          # senza conferma: niente scritto
        self.assertEqual(self.counts(), before)

    def test_c_confirmed_replacement_supersedes_keeps_audit_and_never_double_counts(self):
        self.assertEqual(self.clicks_of("176948463003177"), 80)
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised(85, 8))}, recalc=False, replace={"TARGETING_30D"})
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["replaced"], ["TARGETING_30D"])
        imps = self.q("SELECT gimport_id, status, revision, supersedes_gimport_id, superseded_by_gimport_id, file_name FROM global_report_imports ORDER BY gimport_id")
        self.assertEqual([(i["status"], i["revision"]) for i in imps], [("SUPERSEDED", 1), ("ACTIVE", 2)])
        self.assertEqual((imps[1]["supersedes_gimport_id"], imps[0]["superseded_by_gimport_id"]), (imps[0]["gimport_id"], imps[1]["gimport_id"]))
        self.assertEqual(sum(1 for i in imps if i["status"] == "ACTIVE"), 1)                          # una sola versione attiva
        self.assertEqual(self.clicks_of("176948463003177"), 85)                                        # NON 80 + 85: nessun doppio conteggio
        self.assertEqual(self.q("SELECT COUNT(*) n FROM imports WHERE report_type='TARGETING' AND campaign_id=? AND period_id='2026-09-14__2026-10-13'",
                                self.q("SELECT campaign_id FROM campaign_book_mapping WHERE amazon_campaign_id='176948463003177'")[0]["campaign_id"])[0]["n"], 1)
        old_rows = self.q("SELECT COUNT(*) n FROM global_report_rows WHERE gimport_id=?", imps[0]["gimport_id"])[0]["n"]
        self.assertGreater(old_rows, 0)                                                                # audit: i dati della versione sostituita restano
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_rows WHERE gimport_id=? AND amazon_campaign_id='176948463003177'", imps[0]["gimport_id"])[0]["n"], 1)
        # mappa e associazioni non cambiano
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE status='ASSIGNED'")[0]["n"], 3)

    def test_c2_replacement_removes_derived_data_of_campaigns_missing_from_the_new_version(self):
        self.assertGreater(self.q("SELECT COUNT(*) n FROM target_rows WHERE amazon_campaign_id='206528816350500'")[0]["n"], 0)
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised(drop_auto=True))}, recalc=False, replace={"TARGETING_30D"})
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE amazon_campaign_id='206528816350500'")[0]["n"], 0)         # la versione attiva non la contiene
        self.assertIn((tg.BOOK_A, "US"), r.data["books"])                                              # il libro va ricalcolato

    def test_d_cancelled_replacement_changes_nothing(self):
        before = self.counts()
        c1 = self.clicks_of("176948463003177")
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised())}, recalc=False)                 # nessuna conferma = ANNULLA
        self.assertFalse(r.ok)
        self.assertEqual(r.data["revisions_pending"], ["TARGETING_30D"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.clicks_of("176948463003177"), c1)
        self.assertEqual([i["status"] for i in self.q("SELECT status FROM global_report_imports")], ["ACTIVE"])

    def test_e_replacement_recalculates_analysis_and_decisions_of_the_affected_books(self):
        r = GI.run_import(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised(120, 9))}, recalc=True, replace={"TARGETING_30D"})
        self.assertTrue(r.ok, r.errors)
        ok = {(x["book_id"], x["marketplace"]): x for x in r.data["recalc"]}
        self.assertTrue(ok[(tg.BOOK_A, "US")]["ok"], ok)
        dec = self.q("SELECT tactical_clicks FROM decisions WHERE book_id=? AND target='route 66' ORDER BY run_id DESC LIMIT 1", tg.BOOK_A)
        self.assertEqual(dec[0]["tactical_clicks"], 120)                                               # le decisioni usano la versione attiva

    def test_f_old_superseded_file_is_recognised_and_not_resurrected(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30_v2.csv", targeting_30_revised())}, recalc=False, replace={"TARGETING_30D"})
        pv = GI.preview(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())})["TARGETING_30D"]["preview"]
        self.assertEqual(pv["status"], "DUPLICATO")
        self.assertTrue(any("già sostituita" in w for w in pv["warnings"]))

    def test_g_only_one_active_version_is_enforced_by_the_database(self):
        c = sqlite3.connect(self.ws.db_path)
        row = c.execute("SELECT report_type, window_label, window_days, date_from, date_to, period_id, marketplace, account_id FROM global_report_imports").fetchone()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,account_id,file_name,file_sha256,imported_at,status) "
                          "VALUES (?,?,?,?,?,?,?,?,'x.csv','deadbeef','t','ACTIVE')", row)
            c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,account_id,file_name,file_sha256,imported_at,status) "
                      "VALUES (?,?,?,?,?,?,?,?,'x.csv','deadbeef','t','SUPERSEDED')", row)                  # una versione SUPERSEDED, invece, e' ammessa
        finally:
            c.close()


# ------------------------------------------------------------------ 3. Campaign ID = identita'; storico e backfill
def add_legacy(ws, book, cid, ctype, targets, mp="US"):
    """Campagna STORICA (senza ID Amazon): campaigns + imports + target_rows. targets = [(testo, 'Esatta'|'A frase', bid)]."""
    c = connect(ws.db_path)
    try:
        c.execute("INSERT INTO campaigns (book_id,campaign_id,campaign_name,campaign_type,marketplace,currency) VALUES (?,?,?,?,?,?)", (book, cid, cid, ctype, mp, "USD"))
        c.execute("INSERT INTO imports (book_id,campaign_id,campaign_name,campaign_type,marketplace,currency,report_type,report_scope,date_to,import_timestamp,source_filename,file_path,"
                  "file_sha256,period_id,snapshot_id) VALUES (?,?,?,?,?,?,'TARGETING','LIFETIME','2026-10-05','t','legacy.csv','p',?,'LIFETIME__2026-10-05',?)",
                  (book, cid, cid, ctype, mp, "USD", f"legacy-{book}-{cid}", f"{book}|{cid}|TARGETING|LIFETIME__2026-10-05"))
        iid = c.execute("SELECT import_id FROM imports WHERE book_id=? AND campaign_id=?", (book, cid)).fetchone()[0]
        pid, snap = "LIFETIME__2026-10-05", f"{book}|{cid}|TARGETING|LIFETIME__2026-10-05"
        cols = ("import_id,book_id,campaign_id,campaign_name,campaign_type,marketplace,currency,report_type,report_scope,date_to,import_timestamp,source_filename,period_id,snapshot_id,"
                "target,match_type,bid,impressions,clicks,spend,sales,orders")
        for t, m, bid in targets:
            c.execute(f"INSERT INTO target_rows ({cols}) VALUES ({','.join('?' * 22)})",
                      (iid, book, cid, cid, ctype, mp, "USD", "TARGETING", "LIFETIME", "2026-10-05", "t", "legacy.csv", pid, snap, t, m, bid, 500, 50, 40, 200, 10))
        c.commit()
    finally:
        c.close()


PH_TARGETS = [("route 66 road trip", "PHRASE", "2001", 12), ("route 66 map", "PHRASE", "2002", 9), ("route 66 guide", "PHRASE", "2003", 7), ("historic route 66", "PHRASE", "2004", 5)]


def phrase_report(camp_row, targets=PH_TARGETS, bids=None, rng=tg.RANGE30):
    return tg.csv_text(tg.TG_HEAD, [tg.tg(camp_row, t, m, tid, clicks, 1, 5.0, 22.99, rng, bid=(bids or {}).get(t, "0.50")) for t, m, tid, clicks in targets])


LEGACY_PH = [(t, "A frase", 0.5) for t, _m, _i, _c in PH_TARGETS] + [("route 66 books", "A frase", 0.6), ("route 66 travel", "A frase", 0.6)]


class CampaignIdentityTests(tg.WsBase):
    def test_two_different_campaign_ids_of_the_same_book_are_never_merged(self):
        c2 = tg.camp("281284384323178", "R66_PHRASE_SECONDARY")
        data = tg.csv_text(tg.TG_HEAD, [tg.tg(tg.C_PH, "route 66", "PHRASE", "1001", 30, 2), tg.tg(c2, "route 66", "PHRASE", "1101", 12, 1)])
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", data)}, recalc=False)
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", [("route 66", "A frase", 0.5)])
        self.assoc("176948463003177", tg.BOOK_A)
        self.assoc("281284384323178", tg.BOOK_A)
        maps = {m["amazon_campaign_id"]: m["campaign_id"] for m in self.q("SELECT * FROM campaign_book_mapping WHERE status='ASSIGNED'")}
        self.assertEqual(len(set(maps.values())), 2)                                                  # due Campaign ID = due campagne interne
        self.assertNotIn("FRASE", maps.values())                                                      # nessuna fusione con la campagna storica FRASE
        for aid in maps:
            rows = self.q("SELECT DISTINCT campaign_id FROM target_rows WHERE amazon_campaign_id=?", aid)
            self.assertEqual([r["campaign_id"] for r in rows], [maps[aid]])                           # le righe di una non finiscono nell'altra
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE campaign_id='FRASE' AND amazon_campaign_id IS NOT NULL")[0]["n"], 0)

    def test_new_campaign_has_no_lifetime_and_no_borrowed_history(self):
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", [(t, "A frase", 0.5) for t, *_ in PH_TARGETS])
        r = self.assoc("176948463003177", tg.BOOK_A)
        self.assertNotEqual(r.data["campaign_id"], "FRASE")
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE campaign_id=? AND report_scope='LIFETIME'", r.data["campaign_id"])[0]["n"], 0)       # Lifetime non disponibile
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE campaign_id=? AND report_scope='DATE_RANGE'", r.data["campaign_id"])[0]["n"], 4)       # 30D normale
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE campaign_id='FRASE'")[0]["n"], 4)                                                       # la storica e' intatta

    def test_internal_id_never_collides_with_a_historic_campaign_even_with_the_same_name(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE_ROUTE66", "PHRASE", [("x", "A frase", 0.5)])
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)           # nome Amazon "FRASE ROUTE66 - data" -> slug FRASE_ROUTE66
        r = self.assoc("176948463003177", tg.BOOK_A)
        self.assertEqual(r.data["campaign_id"], "FRASE_ROUTE66_2")

    def test_backfill_is_only_a_suggestion_with_strong_evidence(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", LEGACY_PH)
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)
        cand = GI.backfill_candidates(self.ws)
        self.assertEqual(len(cand), 1)
        x = cand[0]
        self.assertEqual((x["level"], x["legacy_campaign_id"], x["book_id"], x["shared"], x["bids_equal"], x["bids_compared"]), ("ALTA", "FRASE", tg.BOOK_A, 4, 4, 4))
        self.assertEqual(self.q("SELECT status FROM campaign_book_mapping")[0]["status"], "UNASSIGNED")             # nessuna associazione automatica
        self.assertEqual(GI.counts(self.ws)["ASSIGNED"], 0)

    def test_confirmed_backfill_links_the_real_campaign_and_inherits_lifetime(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", LEGACY_PH)
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)
        r = GI.associate(self.ws, "176948463003177", tg.BOOK_A, backfill_of="FRASE", recalc=False)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.data["campaign_id"], "FRASE")
        kinds = {x["report_scope"] for x in self.q("SELECT report_scope FROM target_rows WHERE campaign_id='FRASE' AND book_id=?", tg.BOOK_A)}
        self.assertEqual(kinds, {"LIFETIME", "DATE_RANGE"})                                            # lo storico (Lifetime) + il 30D della STESSA campagna
        self.assertEqual(self.q("SELECT campaign_name FROM campaigns WHERE book_id=? AND campaign_id='FRASE'", tg.BOOK_A)[0]["campaign_name"], "FRASE")   # riga storica non riscritta
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaigns WHERE book_id=? AND campaign_id='FRASE'", tg.BOOK_A)[0]["n"], 1)

    def test_type_or_name_alone_never_proves_identity(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", [("totally different one", "A frase", 0.5), ("totally different two", "A frase", 0.5), ("another unrelated", "A frase", 0.5)])
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)       # stesso libro, stesso tipo, nessun target in comune
        self.assertEqual(GI.backfill_candidates(self.ws), [])
        r = GI.associate(self.ws, "176948463003177", tg.BOOK_A, backfill_of="FRASE", recalc=False)
        self.assertFalse(r.ok)                                                                          # backfill forzato a mano: rifiutato
        self.assertIn("NON dimostrata", r.errors[0])
        self.assertEqual(GI.counts(self.ws)["ASSIGNED"], 0)

    def test_ambiguous_or_weak_evidence_is_not_high_confidence(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", LEGACY_PH)
        c2 = tg.camp("281284384323178", "SECONDA FRASE")
        data = tg.csv_text(tg.TG_HEAD, [tg.tg(tg.C_PH, t, m, tid, clicks, 1, 5.0, 22.99) for t, m, tid, clicks in PH_TARGETS] +
                           [tg.tg(c2, t, m, "9" + tid, clicks, 1, 5.0, 22.99) for t, m, tid, clicks in PH_TARGETS])           # due campagne Amazon con gli stessi target
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", data)}, recalc=False)
        cand = GI.backfill_candidates(self.ws)
        self.assertEqual({x["level"] for x in cand}, {"DA_VERIFICARE"})                                # ambiguo: nessuna ALTA
        self.assertTrue(all(any("ambiguo" in r for r in x["reasons"]) for x in cand))

    def test_bids_that_differ_are_not_enough_evidence(self):
        add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", LEGACY_PH)
        bids = {t: "0.99" for t, *_ in PH_TARGETS}
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH, bids=bids))}, recalc=False)
        cand = GI.backfill_candidates(self.ws)
        self.assertEqual([x["level"] for x in cand], ["DA_VERIFICARE"])
        self.assertTrue(any("offerte uguali" in r for r in cand[0]["reasons"]))

    def test_auto_campaigns_are_never_provable_by_target_set(self):
        add_legacy(self.ws, tg.BOOK_A, "AUTO", "OTHER", [(g, "", 0.5) for g in ("close-match", "loose-match", "complements", "substitutes")])
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.targeting_30())}, recalc=False)
        self.assertEqual([x for x in GI.backfill_candidates(self.ws) if x["amazon_campaign_id"] == "206528816350500"], [])

    def test_book_must_match_the_portfolio_suggestion(self):
        add_legacy(self.ws, tg.BOOK_B, "FRASE", "PHRASE", LEGACY_PH)                                   # campagna storica di un ALTRO libro
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", phrase_report(tg.C_PH))}, recalc=False)   # portfolio ROUTE 66 -> libro A
        cand = GI.backfill_candidates(self.ws)
        self.assertEqual([x["level"] for x in cand], ["DA_VERIFICARE"])
        self.assertTrue(any("libro suggerito" in r for r in cand[0]["reasons"]))


# ------------------------------------------------------------------ 4. associazione indipendente dal nome
class AssociationPersistenceTests(tg.WsBase):
    def test_association_survives_a_campaign_rename_and_a_different_suggestion(self):
        c1 = tg.camp("123", "AUTO", "111", "ROUTE 66")
        GI.run_import(self.ws, {"TARGETING_30D": ("t30.csv", tg.csv_text(tg.TG_HEAD, [tg.tg(c1, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1", 20, 1)]))}, recalc=False)
        self.assoc("123", tg.BOOK_A)
        m = self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id='123'")[0]
        self.assertEqual((m["status"], m["book_id"], m["campaign_name_last_seen"]), ("ASSIGNED", tg.BOOK_A, "AUTO"))
        renamed = tg.camp("123", "AUTO NEW NAME", "222", "rare coin")                                    # nome E portfolio cambiano (suggerirebbero Rare Coins)
        r = GI.run_import(self.ws, {"TARGETING_7D": ("t7.csv", tg.csv_text(tg.TG_HEAD, [tg.tg(renamed, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1", 8, 1, rng=tg.RANGE7)]))},
                          recalc=False)
        self.assertTrue(r.ok, r.errors)
        m = self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id='123'")[0]
        self.assertEqual((m["status"], m["book_id"], m["campaign_id"]), ("ASSIGNED", tg.BOOK_A, m["campaign_id"]))        # la relazione col libro NON cambia
        self.assertEqual(m["campaign_name_last_seen"], "AUTO NEW NAME")                                                    # il nome last_seen si' (e anche il portfolio visto)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping")[0]["n"], 1)
        self.assertEqual({x["book_id"] for x in self.q("SELECT book_id FROM target_rows WHERE amazon_campaign_id='123'")}, {tg.BOOK_A})
        self.assertEqual(self.q("SELECT COUNT(DISTINCT campaign_id) n FROM target_rows WHERE amazon_campaign_id='123'")[0]["n"], 1)


# ------------------------------------------------------------------ 5. libro nuovo e books.json
class NewBookTests(tg.WsBase):
    GOOD = {"title": "Libro Nuovo", "book_id": "b0newbook1", "marketplace": "US", "price": "22.99", "royalty": "9", "target_acos": "35"}

    def books_bytes(self):
        return (self.root / "CONFIG" / "books.json").read_bytes()

    def test_validation_rules(self):
        cases = [({"book_id": "123"}, "ASINnon"), ({"book_id": "B0 BAD!!!!"}, "ASIN"), ({"marketplace": ""}, "Marketplace"), ({"marketplace": "USA1"}, "Marketplace"),
                 ({"price": "0"}, "prezzo"), ({"price": "-5"}, "prezzo"), ({"royalty": "30"}, "royalty"), ({"royalty": "0"}, "royalty"), ({"target_acos": "0"}, "ACOS"),
                 ({"target_acos": "150"}, "ACOS"), ({"title": " "}, "Titolo"), ({"price": "abc"}, "Prezzo")]
        for over, word in cases:
            clean, errs = GI.validate_new_book(self.ws, {**self.GOOD, **over})
            self.assertTrue(errs, over)
            self.assertEqual(clean, {}, over)
            if word != "ASINnon":
                self.assertTrue(any(word.lower() in e.lower() for e in errs), (over, errs))
        clean, errs = GI.validate_new_book(self.ws, self.GOOD)
        self.assertEqual(errs, [])
        self.assertEqual(clean["book_id"], "B0NEWBOOK1")
        self.assertAlmostEqual(clean["break_even_acos"], 39.15, places=2)                              # derivato: 9 / 22.99
        self.assertAlmostEqual(clean["break_even_acos"], round(9 / 22.99 * 100, 2), places=2)

    def test_duplicate_asin_and_marketplace_is_refused_and_points_to_the_existing_book(self):
        before = self.books_bytes()
        r = GI.create_book_and_associate(self.ws, {**self.GOOD, "book_id": tg.BOOK_A}, [], recalc=False)
        self.assertFalse(r.ok)
        self.assertIn("esiste già", r.errors[0])
        self.assertIn("associa le campagne al libro esistente", r.errors[0])
        r = GI.create_book_and_associate(self.ws, {**self.GOOD, "book_id": tg.BOOK_A, "marketplace": "UK"}, [], recalc=False)           # stesso ASIN, altro marketplace
        self.assertFalse(r.ok)
        self.assertIn("aggiungi il marketplace UK", r.errors[0])
        self.assertEqual(self.books_bytes(), before)

    def test_invalid_form_never_touches_books_json(self):
        before = self.books_bytes()
        for over in ({"price": "0"}, {"royalty": "99"}, {"book_id": "x"}):
            self.assertFalse(GI.create_book_and_associate(self.ws, {**self.GOOD, **over}, [], recalc=False).ok)
        self.assertEqual(self.books_bytes(), before)

    def test_write_is_atomic_a_failure_leaves_the_original_intact_and_no_temp_files(self):
        before = self.books_bytes()
        with mock.patch("os.replace", side_effect=OSError("disco pieno")):
            r = GI.create_book_and_associate(self.ws, self.GOOD, [], recalc=False)
        self.assertFalse(r.ok)
        self.assertIn("non è stato modificato", r.errors[0])
        self.assertEqual(self.books_bytes(), before)
        json.loads(before.decode("utf-8"))                                                              # ancora JSON valido
        self.assertEqual(list((self.root / "CONFIG").glob("*.tmp")), [])
        ok = GI.create_book_and_associate(self.ws, self.GOOD, [], recalc=False)                         # e poi funziona
        self.assertTrue(ok.ok, ok.errors)
        self.assertIn("B0NEWBOOK1", registry.load_books(self.ws.books_path))
        json.loads(self.books_bytes().decode("utf-8"))
        self.assertEqual(list((self.root / "CONFIG").glob("*.tmp")), [])

    def test_real_books_json_is_not_touched_by_these_tests(self):
        real = ROOT / "CONFIG" / "books.json"
        self.assertNotEqual(self.ws.books_path.resolve(), real.resolve())


# ------------------------------------------------------------------ 7. MISSING_7D vs NO_TRAFFIC_7D e conteggi della home
class SevenDayUxTests(unittest.TestCase):
    def test_labels_are_distinct_and_explicit(self):
        import ui_labels as U
        self.assertEqual(U.label_trend("NO_TRAFFIC_7D"), "NESSUN TRAFFICO 7D")
        self.assertEqual(U.label_trend("MISSING_7D"), "DATI 7D NON DISPONIBILI")
        self.assertNotEqual(U.label_trend("NO_TRAFFIC_7D"), U.label_trend("MISSING_7D"))

    def render(self, d):
        import test_dashboard_labels as tl
        from components import trend_ui
        log = []
        trend_ui.line(tl.Rec(log), d)
        return chr(10).join(x for _n, a, k in log for x in tl.strings([a, k]))

    def test_cards_show_explicit_zero_clicks_versus_missing(self):
        base = {"lifetime_acos": 28.0, "tactical_acos": 30.0, "tactical_clicks": 40}
        zero = self.render({**base, "trend_state": "NO_TRAFFIC_7D", "trend_clicks": 0, "trend_orders": 0, "trend_evidence": "NO_TRAFFIC", "trend_note": "n"})
        miss = self.render({**base, "trend_state": "MISSING_7D", "trend_clicks": None, "trend_orders": None, "trend_evidence": None, "trend_note": "n"})
        self.assertIn("NESSUN TRAFFICO 7D", zero)
        self.assertIn("0 clic · 0 acquisti", zero)
        self.assertNotIn("DATI 7D NON DISPONIBILI", zero)
        self.assertIn("DATI 7D NON DISPONIBILI", miss)
        self.assertNotIn("NESSUN TRAFFICO 7D", miss)
        self.assertNotIn("0 clic", miss)                                                                # il dato mancante non diventa "0 clic"

    def test_home_counts_do_not_depend_on_7d_states(self):
        import test_overview as tov
        from data_access import overview as O
        from data_access.repository import parse_delivery_detail
        def build(state):
            ds = [tov.dec(i, trend_state=state, trend_clicks=(0 if state == "NO_TRAFFIC_7D" else None), final_action=a, operationally_actionable=int(a != "HOLD"))
                  for i, a in enumerate(("BID_DOWN_5_CANDIDATE", "HOLD", "WATCH"), 1)]
            ov = O.build_overview(ds, tov.BOOK, tov.SUMMARY, [], None, parse_delivery_detail)
            return json.dumps({"kpis": ov.get("kpis"), "today": [c["title"] for c in ov.get("today", [])]}, sort_keys=True, default=str)
        self.assertEqual(build("NO_TRAFFIC_7D"), build("MISSING_7D"))
        self.assertEqual(build("MISSING_7D"), build(None))                                              # nemmeno contano nei KPI/azioni della home


if __name__ == "__main__":
    unittest.main()
