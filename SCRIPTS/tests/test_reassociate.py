"""RIASSOCIAZIONE DI UNA CAMPAGNA GIA' ASSOCIATA («CAMBIA ASSOCIAZIONE»): correzione del libro sbagliato senza reimportare i report e senza cancellare dati. Identita' =
(account, marketplace, ID campagna Amazon); avviso di coerenza NON bloccante; audit in global_report_repairs; nessuna modifica a Amazon Ads / books.json / motore decisionale.
Fixture sintetiche (workspace temporanei): mai il DB reale ne' books.json reale."""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_reports as tg
import test_multi_account as tma
import test_multi_marketplace_reports as mm
from kdp_ads import globalstore as GS
from ops import global_import as GI

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

PAGE14 = str(ROOT / "DASHBOARD" / "pages" / "14_Campagne_da_associare.py")
ACC, US_ID, UK_ID = mm.ACC, mm.US_ID, mm.UK_ID
WRONG, RIGHT = tg.BOOK_B, tg.BOOK_A                    # libro sbagliato (solo US) -> libro corretto (US + UK)
ENGINE = ("imports", "target_rows", "search_term_rows", "campaigns")


class Base(mm.MM):
    """Report misto US + UK importato; la campagna US associata per errore al libro SBAGLIATO, quella UK al libro corretto (variante UK)."""

    def setUp(self):
        super().setUp()
        self.full()
        self.assertTrue(GI.associate(self.ws, US_ID, WRONG, account_id=ACC, recalc=False).ok)
        self.assertTrue(GI.associate(self.ws, UK_ID, RIGHT, account_id=ACC, recalc=False).ok)

    def counts(self, *tables):
        return {t: self.q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in tables}

    def digest(self, sql):
        return hashlib.sha256(json.dumps([list(r.values()) for r in self.q(sql)], default=str).encode()).hexdigest()

    def mapping(self, cid=US_ID, acct=ACC):
        return self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id=? AND account_id=?", cid, acct)[0]


class ReassociationTests(Base):
    def test_old_book_to_new_book_preserving_identity_and_history(self):
        before = self.mapping()
        self.assertEqual(before["book_id"], WRONG)
        r = GI.reassociate(self.ws, US_ID, RIGHT, ACC, marketplace="US")
        self.assertTrue(r.ok, r.errors)
        self.assertIn("Esegui un nuovo ricalcolo", r.message)
        after = self.mapping()
        self.assertEqual(after["book_id"], RIGHT)
        for k in ("account_id", "marketplace", "amazon_campaign_id", "campaign_name_last_seen", "campaign_type", "detected_kind", "detected_type", "portfolio_last_seen", "first_seen",
                  "status", "backfill_of"):
            self.assertEqual(after[k], before[k], k)                                           # identita' e tipo rilevato preservati
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE amazon_campaign_id=? AND account_id=?", US_ID, ACC)[0]["n"], 1)      # nessun mapping duplicato
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE status='UNASSIGNED'")[0]["n"], 0)
        self.assertEqual(r.data["old_book_id"], WRONG)
        self.assertEqual(r.data["new_book_id"], RIGHT)

    def test_audit_is_written_in_the_existing_table_without_a_new_one(self):
        GI.reassociate(self.ws, US_ID, RIGHT, ACC)
        a = GI.reassignments(self.ws)
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["account_id"], a[0]["marketplace"], a[0]["amazon_campaign_id"], a[0]["old_book_id"], a[0]["new_book_id"]), (ACC, "US", US_ID, WRONG, RIGHT))
        self.assertTrue(a[0]["timestamp"])
        names = {r["name"] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("campaign_reassignments", names)
        self.assertEqual(self.q("SELECT kind FROM global_report_repairs")[-1]["kind"], "REASSIGN_CAMPAIGN")

    def test_no_data_lost_global_rows_imports_and_history_unchanged(self):
        self.assertTrue(all(x["ok"] for x in GI.recalculate(self.ws, {(WRONG, "US"), (RIGHT, "UK")})))
        n0 = self.counts("global_report_rows", "global_report_imports", "imports", "target_rows", "search_term_rows", "campaigns", "campaign_book_mapping", "decisions", "decision_runs",
                         "analysis_runs", "recommendations")
        g0 = self.digest("SELECT * FROM global_report_rows ORDER BY id")
        gi0 = self.digest("SELECT * FROM global_report_imports ORDER BY gimport_id")
        d0 = self.digest("SELECT * FROM decisions ORDER BY pk")
        r = GI.reassociate(self.ws, US_ID, RIGHT, ACC)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.counts(*n0), n0)                                                  # nessuna riga cancellata o duplicata
        self.assertEqual(self.digest("SELECT * FROM global_report_rows ORDER BY id"), g0)       # report globali invariati
        self.assertEqual(self.digest("SELECT * FROM global_report_imports ORDER BY gimport_id"), gi0)
        self.assertEqual(self.digest("SELECT * FROM decisions ORDER BY pk"), d0)                # decisioni storiche invariate (restano riferite al vecchio libro)
        self.assertGreater(r.data["moved"]["imports"], 0)
        by = {t: {x["book_id"]: x["n"] for x in self.q(f"SELECT book_id, COUNT(*) n FROM {t} GROUP BY book_id")} for t in ("imports", "target_rows", "search_term_rows")}
        for t in by:
            self.assertEqual(by[t].get(WRONG, 0), 0, t)                                         # il vecchio libro non ha piu' le copie di quella campagna
            self.assertGreater(by[t].get(RIGHT, 0), 0, t)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM imports WHERE snapshot_id LIKE ?", f"{WRONG}|%")[0]["n"], 0)

    def test_after_recalc_the_old_book_no_longer_shows_the_campaign_and_the_new_book_does(self):
        self.assertTrue(all(x["ok"] for x in GI.recalculate(self.ws, {(WRONG, "US")})))
        self.assertTrue(self.q("SELECT 1 FROM decisions WHERE book_id=? AND amazon_campaign_id=? LIMIT 1", WRONG, US_ID))
        GI.reassociate(self.ws, US_ID, RIGHT, ACC)
        self.assertTrue(all(x["ok"] for x in GI.recalculate(self.ws, {(WRONG, "US"), (RIGHT, "US")})))
        latest = lambda b: self.q("SELECT MAX(run_id) r FROM decision_runs WHERE book_id=? AND UPPER(marketplace)='US'", b)[0]["r"]      # noqa: E731
        old = self.q("SELECT campaign_id, amazon_campaign_id FROM decisions WHERE run_id=?", latest(WRONG))
        new = self.q("SELECT campaign_id, amazon_campaign_id FROM decisions WHERE run_id=?", latest(RIGHT))
        self.assertNotIn(US_ID, {d["amazon_campaign_id"] for d in old})
        self.assertIn(US_ID, {d["amazon_campaign_id"] for d in new})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaign_book_mapping WHERE status='UNASSIGNED'")[0]["n"], 0)

    def test_the_new_book_row_is_created_only_if_missing_and_campaigns_move_with_their_id(self):
        r = GI.reassociate(self.ws, US_ID, RIGHT, ACC)
        self.assertEqual(r.data["new_campaign_id"], self.mapping()["campaign_id"])
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaigns WHERE book_id=? AND campaign_id=?", RIGHT, r.data["new_campaign_id"])[0]["n"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM campaigns WHERE book_id=? AND campaign_id=?", WRONG, r.data["old_campaign_id"])[0]["n"], 0)


class IsolationTests(Base):
    def test_marketplace_isolation_us_to_uk_and_uk_to_us_are_refused(self):
        uk = GI.reassociate(self.ws, UK_ID, WRONG, ACC)                                         # il libro scelto non ha la variante UK
        self.assertFalse(uk.ok)
        self.assertEqual(self.mapping(UK_ID)["book_id"], RIGHT)
        us_to_uk = GI.reassociate(self.ws, US_ID, RIGHT, ACC, marketplace="UK")
        self.assertFalse(us_to_uk.ok)
        self.assertTrue(any("MARKETPLACE" in e for e in us_to_uk.errors), us_to_uk.errors)
        self.assertEqual(self.mapping()["book_id"], WRONG)                                      # nessuna modifica
        self.assertEqual(self.mapping()["marketplace"], "US")

    def test_reassociating_the_us_campaign_does_not_touch_the_uk_one(self):
        before = self.mapping(UK_ID)
        rows = self.q("SELECT COUNT(*) n FROM target_rows WHERE book_id=? AND UPPER(marketplace)='UK'", RIGHT)[0]["n"]
        self.assertTrue(GI.reassociate(self.ws, US_ID, RIGHT, ACC).ok)
        self.assertEqual({k: self.mapping(UK_ID)[k] for k in ("book_id", "campaign_id", "marketplace", "status")}, {k: before[k] for k in ("book_id", "campaign_id", "marketplace", "status")})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE book_id=? AND UPPER(marketplace)='UK'", RIGHT)[0]["n"], rows)

    def test_refused_cases_leave_everything_as_it_was(self):
        snap = (self.digest("SELECT * FROM campaign_book_mapping ORDER BY mapping_id"), self.counts(*ENGINE))
        for args in ((US_ID, WRONG, ACC), ("999", RIGHT, ACC), (US_ID, "B0NESSUNO", ACC)):       # stesso libro / campagna sconosciuta / libro inesistente
            self.assertFalse(GI.reassociate(self.ws, *args).ok, args)
        self.assertEqual((self.digest("SELECT * FROM campaign_book_mapping ORDER BY mapping_id"), self.counts(*ENGINE)), snap)

    def test_existing_workflow_unchanged_an_assigned_campaign_is_not_reassigned_silently(self):
        r = GI.associate(self.ws, US_ID, RIGHT, account_id=ACC, recalc=False)
        self.assertFalse(r.ok)
        self.assertIn("non la riassegno in silenzio", " ".join(r.errors))
        self.assertEqual(self.mapping()["book_id"], WRONG)


class TwoAccountsTests(tma.MA):
    def test_same_account_isolation_the_same_id_in_another_account_is_not_touched(self):
        self.both_imported()
        self.assoc_both(tg.BOOK_A, tg.BOOK_B)                                                  # stesso Campaign ID in due account, libri diversi
        other = self.q("SELECT * FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", tma.ACC_B, tma.CID)[0]
        rows_b = self.digest_rows(tg.BOOK_B)
        amb = GI.reassociate(self.ws, tma.CID, tg.BOOK_B, None)                                # senza account l'ID e' ambiguo: mai una scelta a caso
        self.assertFalse(amb.ok)
        self.assertIn("più account", " ".join(amb.errors))
        r = GI.reassociate(self.ws, tma.CID, tg.BOOK_B, tma.ACC_A)
        self.assertTrue(r.ok, r.errors)
        a = self.q("SELECT * FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", tma.ACC_A, tma.CID)[0]
        b = self.q("SELECT * FROM campaign_book_mapping WHERE account_id=? AND amazon_campaign_id=?", tma.ACC_B, tma.CID)[0]
        self.assertEqual(a["book_id"], tg.BOOK_B)
        self.assertEqual({k: b[k] for k in ("book_id", "campaign_id", "marketplace", "status", "updated_at")}, {k: other[k] for k in ("book_id", "campaign_id", "marketplace", "status", "updated_at")})
        self.assertEqual(self.digest_rows(tg.BOOK_B, tma.ACC_B), rows_b)                         # le righe dell'altro account non sono state toccate
        self.assertNotEqual(a["campaign_id"], b["campaign_id"])                                   # id interno diverso: nessuna fusione nello stesso libro
        accts = {x["amazon_account_id"] for x in self.q("SELECT amazon_account_id FROM target_rows WHERE book_id=?", tg.BOOK_B)}
        self.assertEqual(accts, {tma.ACC_A, tma.ACC_B})

    def digest_rows(self, book, acct=None):
        sql = "SELECT campaign_id, clicks, orders, snapshot_id FROM target_rows WHERE book_id=?" + (" AND amazon_account_id=?" if acct else "") + " ORDER BY id"
        return hashlib.sha256(json.dumps([list(r.values()) for r in self.q(sql, *( [book, acct] if acct else [book]))]).encode()).hexdigest()


class WarningTests(unittest.TestCase):
    TWELVE = {"campaign_name_last_seen": "AUTO TWELVE NATALE", "portfolio_last_seen": "TWELVE_CHRISTMAS", "campaign_id": "AUTO_TWELVE_NATALE", "ad_group_last_seen": "US_TWELVE_CHRISTMAS_PHRASE"}

    def test_incoherent_name_warns_with_the_significant_tokens(self):
        w = GS.coherence_warning(self.TWELVE, "SHORT STORIES FOR SENIORS")
        self.assertIn("ATTENZIONE", w)
        self.assertIn("'TWELVE'", w)
        self.assertIn("'NATALE'", w)
        self.assertIn("Verifica prima di confermare", w)

    def test_coherent_book_has_no_warning_and_generic_tokens_are_ignored(self):
        self.assertIsNone(GS.coherence_warning(self.TWELVE, "Twelve Women, Twelve Christmas Mysteries"))
        generic = {"campaign_name_last_seen": "AUTO FRASE EXACT PHRASE ESATTA SP CAMPAIGN ADS BOOK - 11/5/2026 09:08:19.687", "portfolio_last_seen": None, "campaign_id": "FRASE"}
        self.assertIsNone(GS.coherence_warning(generic, "SHORT STORIES FOR SENIORS"))          # solo parole generiche/numeri/date: nessun segnale, nessun avviso
        only_generic_and_book = {**generic, "campaign_name_last_seen": "AUTO ESATTA short stories"}
        self.assertIsNone(GS.coherence_warning(only_generic_and_book, "SHORT STORIES FOR SENIORS"))
        self.assertNotIn("'AUTO'", GS.coherence_warning({**generic, "campaign_name_last_seen": "AUTO FRASE twelve"}, "SHORT STORIES FOR SENIORS") or "")

    def test_warning_never_blocks_the_save(self):
        # la funzione di salvataggio non la consulta: anche con avviso la riassociazione riesce (vedi test UI e ops)
        import inspect
        self.assertNotIn("coherence", inspect.getsource(GS.reassociate))


@unittest.skipIf(AppTest is None, "streamlit non installato")
class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.env = {k: os.environ.get(k) for k in ("KDP_ADS_ROOT", "KDP_ADS_DB")}
        os.environ["KDP_ADS_ROOT"], os.environ["KDP_ADS_DB"] = str(self.root), str(self.ws.db_path)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def page(self):
        at = AppTest.from_file(PAGE14, default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        return at

    @staticmethod
    def table(at):
        found = [df.value for df in at.dataframe if "Campaign ID" in df.value.columns and "Libro attuale" in df.value.columns]
        return found[0] if found else []                                                       # nessuna riga: Streamlit non mostra nemmeno le colonne

    def texts(self, at):
        return " | ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [w.value for w in at.warning] + [i.value for i in at.info] + [s.value for s in at.success])

    def test_associated_section_lists_the_required_columns_and_filters_by_id_name_book_account_marketplace(self):
        at = self.page()
        t = self.table(at)
        for col in ("Account", "Marketplace", "Campagna", "Campaign ID", "Portfolio", "Tipo rilevato", "Libro attuale", "book_id attuale"):
            self.assertIn(col, t.columns)
        self.assertEqual(len(t), 2)
        at.text_input(key="ca_as_q").set_value(US_ID).run()
        self.assertEqual(list(self.table(at)["Campaign ID"]), [US_ID])                            # per Campaign ID
        at.text_input(key="ca_as_q").set_value("zzz-nessuna").run()
        self.assertEqual(len(self.table(at)), 0)
        at.text_input(key="ca_as_q").set_value("").run()
        at.selectbox(key="ca_as_mp").select("UK").run()                                           # per marketplace
        self.assertEqual(list(self.table(at)["Campaign ID"]), [UK_ID])
        at.selectbox(key="ca_as_mp").select("Tutti").run()
        at.selectbox(key="ca_as_bk").select(WRONG).run()                                          # per libro
        self.assertEqual(list(self.table(at)["Campaign ID"]), [US_ID])
        at.selectbox(key="ca_as_bk").select("Tutti").run()
        at.selectbox(key="ca_as_acc").select(ACC).run()                                           # per account
        self.assertEqual(len(self.table(at)), 2)
        at.selectbox(key="ca_as_acc").select("Tutti").run()
        at.text_input(key="ca_as_q").set_value("r66").run()                                      # per nome campagna
        self.assertEqual(len(self.table(at)), 2)

    def test_da_associare_and_gia_associate_stay_separate_tabs(self):
        at = self.page()
        labels = [t.label for t in at.tabs]
        self.assertEqual(labels[0], "DA ASSOCIARE (0)")
        self.assertEqual(labels[2], "ASSOCIATE (2)")
        self.assertEqual([b.label for b in at.button if b.label == "CAMBIA ASSOCIAZIONE"], ["CAMBIA ASSOCIAZIONE"] * 2)

    def test_change_flow_same_marketplace_options_summary_warning_and_explicit_confirmation(self):
        at = self.page()
        key = f"ca_re_{ACC}_{US_ID}"
        [b for b in at.button if b.key == key][0].click().run()
        sel = at.selectbox(key=f"ca_re_b_{ACC}_{US_ID}")
        opts = list(sel.options)
        self.assertTrue(opts and all("US" in o for o in opts), opts)                              # solo libri con la variante US
        self.assertFalse(any(WRONG in o for o in opts))                                           # mai il libro attuale
        t = self.texts(at)
        for need in ("CAMPAGNA:", "Campaign ID:", "ASSOCIAZIONE ATTUALE:", "NUOVA ASSOCIAZIONE:", "Questa operazione non modifica Amazon Ads e non reimporta i report."):
            self.assertIn(need, t, need)
        self.assertEqual(self.mapping()["book_id"], WRONG)                                        # prima della conferma nulla cambia
        [b for b in at.button if b.key == f"ca_re_ok_{ACC}_{US_ID}"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertNotEqual(self.mapping()["book_id"], WRONG)
        t2 = self.texts(at)
        self.assertIn("Associazione aggiornata. Esegui un nuovo ricalcolo per attribuire le decisioni future al libro corretto.", t2)
        self.assertEqual(len(GI.reassignments(self.ws)), 1)

    def test_uk_campaign_never_offers_a_book_without_the_uk_variant(self):
        at = self.page()
        [b for b in at.button if b.key == f"ca_re_{ACC}_{UK_ID}"][0].click().run()
        self.assertEqual(len(list(at.selectbox(key=f"ca_re_b_{ACC}_{UK_ID}").options)) if [s for s in at.selectbox if s.key == f"ca_re_b_{ACC}_{UK_ID}"] else 0, 0)
        self.assertIn("Nessun altro libro con la variante UK", self.texts(at))

    def test_incoherent_choice_shows_a_non_blocking_warning(self):
        # la campagna del test e' «FRASE R66 US»: associarla a un libro senza 'route/66' mostra l'avviso ma il salvataggio resta possibile
        c = GI.coherence_warning(self.ws, self.q("SELECT * FROM campaign_book_mapping WHERE amazon_campaign_id=?", US_ID)[0], WRONG)
        self.assertIsNotNone(c)
        self.assertIn("ATTENZIONE", c)
        self.assertTrue(GI.reassociate(self.ws, US_ID, RIGHT, ACC).ok)                             # il warning non impedisce di salvare


if __name__ == "__main__":
    unittest.main()
