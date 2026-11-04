"""UN REPORT GLOBALE PUO' CONTENERE PIU' MARKETPLACE (es. USD + GBP nello stesso CSV dello stesso account Amazon Ads).
L'unita' non e' piu' «1 file = 1 marketplace» ma  FILE -> ACCOUNT -> GRUPPO MARKETPLACE/VALUTA -> CAMPAGNE -> RIGHE: ogni gruppo e' uno snapshot logico distinto
(account + marketplace + tipo + finestra + date) con lo stesso file_sha256 / import sorgente. Fixture sintetiche, workspace temporanei: mai il DB reale ne' books.json reale."""
import copy
import json
import os
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

import test_global_reports as tg
import test_global_robustness as rb
import test_multi_account as tma
from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads.db import connect
from ops import global_import as GI

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

ACC, NAME = tma.ACC_A, tma.NAME_A
US_ID, UK_ID = "111000111000111", "222000222000222"
TERM = "route 66 travel guide"


def gbp(c):
    return {**c, "Valuta budget": "GBP"}


def eur(c):
    return {**c, "Valuta budget": "EUR"}


CUS = tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US - 5/8/2026 08:11:33.222", "111", "ROUTE 66")
CUK = gbp(tma.acct_camp(ACC, NAME, UK_ID, "FRASE R66 UK - 5/8/2026 08:11:33.222", "222", "ROUTE 66 UK"))


def tgt(c, clicks, orders, rng=tg.RANGE30):
    return tg.tg(c, "route 66", "PHRASE", "1001", clicks, orders, 20.0 + clicks / 10, 22.99 * orders, rng)          # stesso ID target nei due marketplace


def sterm(c, clicks, orders, rng=tg.RANGE30):
    return tg.st_row(c, TERM, "route 66", "PHRASE", "1001", clicks, orders, 9.0 + clicks / 10, 22.99 * orders, rng)   # stesso termine di ricerca nei due marketplace


def targeting(*rows):
    return tg.csv_text(tg.TG_HEAD, list(rows))


def search(*rows):
    return tg.csv_text(tg.ST_HEAD, list(rows))


MIX_T30 = lambda: targeting(tgt(CUS, 30, 3), tgt(CUK, 18, 1))                # noqa: E731
MIX_S30 = lambda: search(sterm(CUS, 12, 2), sterm(CUK, 7, 1))                # noqa: E731

BOOKS_MM = copy.deepcopy(tg.BOOKS)
BOOKS_MM["books"][0]["marketplaces"].append({"marketplace": "UK", "currency": "GBP", "price": 17.99, "royalty_per_copy": 7.0, "target_acos": 35.0, "phase": "GROWTH", "active": True})


class MM(tma.MA):
    """Workspace con lo STESSO ASIN in due marketplace (US e UK) e un libro solo US."""
    def setUp(self):
        super().setUp()
        (self.root / "CONFIG" / "books.json").write_text(json.dumps(BOOKS_MM), encoding="utf-8")

    def full(self):
        self.imp("TARGETING_30D", MIX_T30(), "mix_t30.csv")
        self.imp("SEARCH_TERM_30D", MIX_S30(), "mix_s30.csv")

    def assoc_both(self, **kw):
        a = GI.associate(self.ws, US_ID, tg.BOOK_A, account_id=ACC, recalc=False, **kw)
        b = GI.associate(self.ws, UK_ID, tg.BOOK_A, account_id=ACC, recalc=False, **kw)
        self.assertTrue(a.ok, a.errors)
        self.assertTrue(b.ok, b.errors)


# ------------------------------------------------------------------ risoluzione marketplace (funzione separata, testabile)
class ResolverTests(unittest.TestCase):
    def test_unambiguous_currencies_resolve(self):
        self.assertEqual(G.resolve_marketplace("USD"), ("US", "CURRENCY"))
        self.assertEqual(G.resolve_marketplace("GBP"), ("UK", "CURRENCY"))
        self.assertEqual(G.resolve_marketplace("gbp"), ("UK", "CURRENCY"))

    def test_ambiguous_or_missing_currency_is_never_guessed(self):
        self.assertEqual(G.resolve_marketplace("EUR"), (None, "AMBIGUOUS"))
        self.assertEqual(G.resolve_marketplace(None), (None, "UNKNOWN"))
        self.assertEqual(G.resolve_marketplace(""), (None, "UNKNOWN"))

    def test_user_choice_and_report_metadata_have_priority(self):
        self.assertEqual(G.resolve_marketplace("EUR", override="de"), ("DE", "USER"))
        self.assertEqual(G.resolve_marketplace("EUR", hint="IT"), ("IT", "METADATA"))
        self.assertEqual(G.resolve_marketplace("USD", hint="CA"), ("CA", "METADATA"))
        self.assertEqual(G.resolve_marketplace("USD", hint="CA", override="UK"), ("UK", "USER"))
        self.assertEqual(G.resolve_marketplace("EUR", override="not a code!"), (None, "AMBIGUOUS"))

    def test_split_groups_by_currency_and_keeps_every_row_in_its_own_group(self):
        parsed = G.parse_global_report("m.csv", MIX_T30())
        groups = G.split_by_marketplace(parsed)
        self.assertEqual([(g["marketplace"], g["currency"], len(g["rows"]), g["campaigns"]) for g in groups], [("UK", "GBP", 1, 1), ("US", "USD", 1, 1)])
        self.assertEqual({r["campaign_id"] for r in groups[0]["rows"]}, {UK_ID})
        self.assertEqual({r["campaign_id"] for r in groups[1]["rows"]}, {US_ID})

    def test_user_choice_dict_fills_only_the_ambiguous_group_and_a_string_never_overrides_a_resolved_one(self):
        parsed = G.parse_global_report("m.csv", targeting(tgt(CUS, 30, 3), tgt(eur(tma.acct_camp(ACC, NAME, "333000333000333", "FRASE DE", "333", "DE")), 9, 1)))
        by = lambda m: {(g["marketplace"], g["currency"]) for g in G.split_by_marketplace(parsed, m)}       # noqa: E731
        self.assertEqual(by(None), {("US", "USD"), (None, "EUR")})
        self.assertEqual(by({"EUR": "DE"}), {("US", "USD"), ("DE", "EUR")})
        self.assertEqual(by("DE"), {("US", "USD"), ("DE", "EUR")})                  # stringa: riempie il gruppo non risolto, non tocca USD -> US
        single = G.parse_global_report("s.csv", targeting(tgt(CUS, 30, 3)))
        self.assertEqual([g["marketplace"] for g in G.split_by_marketplace(single, "UK")], ["UK"])           # file a gruppo unico: la scelta dell'utente prevale (compatibilita')


# ------------------------------------------------------------------ 1-6. anteprima e import di file monomarketplace e misti
class PreviewAndImportTests(MM):
    def test_1_single_usd_file_is_one_us_group(self):
        pv = GI.preview(self.ws, {"TARGETING_30D": ("us.csv", targeting(tgt(CUS, 30, 3)))})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["marketplace"], pv["currency"], pv["multi_marketplace"], pv["problems"], pv["importable"]), ("US", "USD", False, [], True))
        self.assertEqual([(g["marketplace"], g["rows"], g["campaigns"]) for g in pv["groups"]], [("US", 1, 1)])
        self.assertTrue(self.imp("TARGETING_30D", targeting(tgt(CUS, 30, 3)), "us.csv").ok)
        self.assertEqual({(r["marketplace"], r["currency"]) for r in self.q("SELECT marketplace, currency FROM global_report_imports")}, {("US", "USD")})
        self.assertEqual({(r["marketplace"], r["currency"]) for r in self.q("SELECT marketplace, currency FROM global_report_rows")}, {("US", "USD")})

    def test_2_single_gbp_file_is_one_uk_group(self):
        pv = GI.preview(self.ws, {"TARGETING_30D": ("uk.csv", targeting(tgt(CUK, 18, 1)))})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["marketplace"], pv["currency"], pv["problems"], pv["importable"]), ("UK", "GBP", [], True))
        self.assertTrue(self.imp("TARGETING_30D", targeting(tgt(CUK, 18, 1)), "uk.csv").ok)
        self.assertEqual({(r["marketplace"], r["currency"]) for r in self.q("SELECT marketplace, currency FROM global_report_imports")}, {("UK", "GBP")})
        self.assertEqual(self.q("SELECT marketplace FROM campaign_book_mapping")[0]["marketplace"], "UK")

    def test_3_mixed_usd_gbp_file_previews_two_groups_without_a_single_marketplace_choice(self):
        pv = GI.preview(self.ws, {"TARGETING_30D": ("mix.csv", MIX_T30())})["TARGETING_30D"]["preview"]
        self.assertTrue(pv["multi_marketplace"])
        self.assertEqual(pv["problems"], [])                                         # nessun «scegli il marketplace»
        self.assertTrue(pv["importable"])
        self.assertEqual({(g["marketplace"], g["currency"], g["rows"], g["campaigns"], g["portfolios"], g["new"], g["assigned"], g["status"]) for g in pv["groups"]},
                         {("US", "USD", 1, 1, 1, 1, 0, "NUOVO"), ("UK", "GBP", 1, 1, 1, 1, 0, "NUOVO")})
        self.assertEqual((pv["rows"], pv["campaigns"]), (2, 2))                      # totali = somma dei gruppi
        self.assertTrue(any("più marketplace" in w for w in pv["warnings"]))

    def test_4_rows_are_split_by_marketplace_and_never_mislabelled(self):
        self.full()
        rows = {(r["amazon_campaign_id"], r["marketplace"], r["currency"]) for r in self.q("SELECT amazon_campaign_id, marketplace, currency FROM global_report_rows")}
        self.assertEqual(rows, {(US_ID, "US", "USD"), (UK_ID, "UK", "GBP")})        # nessuna riga GBP come US, nessuna riga USD come UK
        m = {r["amazon_campaign_id"]: r["marketplace"] for r in self.q("SELECT * FROM campaign_book_mapping")}
        self.assertEqual(m, {US_ID: "US", UK_ID: "UK"})

    def test_5_one_file_makes_two_distinct_snapshots_sharing_file_hash_and_source_import(self):
        self.imp("TARGETING_30D", MIX_T30(), "mix_t30.csv")
        imps = self.q("SELECT * FROM global_report_imports ORDER BY marketplace")
        self.assertEqual([i["marketplace"] for i in imps], ["UK", "US"])
        self.assertEqual(len({i["file_sha256"] for i in imps}), 1)                                      # audit: stesso file
        self.assertEqual(len({i["source_gimport_id"] for i in imps}), 1)                                # stesso import sorgente
        self.assertIn(imps[0]["source_gimport_id"], {i["gimport_id"] for i in imps})
        self.assertEqual(len({i["gimport_id"] for i in imps}), 2)
        self.assertEqual({(i["report_type"], i["window_label"], i["date_from"], i["date_to"], i["account_id"]) for i in imps}, {("TARGETING", "RECENT_30D", "2026-09-14", "2026-10-13", ACC)})

    def test_6_no_double_counting_between_partitions(self):
        self.full()
        self.assoc_both()
        for table, col, total in (("target_rows", "clicks", 48.0), ("search_term_rows", "clicks", 19.0)):
            per = {r["marketplace"]: r["n"] for r in self.q(f"SELECT marketplace, SUM({col}) n FROM {table} GROUP BY marketplace")}
            self.assertEqual(per, {"US": total - (18.0 if table == "target_rows" else 7.0), "UK": 18.0 if table == "target_rows" else 7.0})
            self.assertEqual(sum(per.values()), total)
        self.assertEqual(self.q("SELECT SUM(row_count) n FROM global_report_imports")[0]["n"], 4)

    def test_18_reimporting_the_same_file_does_not_duplicate_anything(self):
        self.imp("TARGETING_30D", MIX_T30(), "mix_t30.csv")
        n = lambda: (self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], self.q("SELECT COUNT(*) n FROM global_report_rows")[0]["n"])        # noqa: E731
        before = n()
        pv = GI.preview(self.ws, {"TARGETING_30D": ("again.csv", MIX_T30())})["TARGETING_30D"]["preview"]
        self.assertEqual(pv["status"], "DUPLICATO")
        self.assertEqual({g["status"] for g in pv["groups"]}, {"DUPLICATO"})
        self.imp("TARGETING_30D", MIX_T30(), "again.csv")
        self.assertEqual(n(), before)

    def test_ambiguous_eur_group_blocks_the_import_until_its_marketplace_is_chosen(self):
        de = eur(tma.acct_camp(ACC, NAME, "333000333000333", "FRASE DE", "333", "DE"))
        data = targeting(tgt(CUS, 30, 3), tgt(de, 9, 1))
        pv = GI.preview(self.ws, {"TARGETING_30D": ("m.csv", data)})["TARGETING_30D"]["preview"]
        self.assertFalse(pv["importable"])
        self.assertTrue(any("EUR" in p and "da selezionare" in p for p in pv["problems"]))
        self.assertEqual({(g["currency"], g["marketplace"]) for g in pv["groups"]}, {("USD", "US"), ("EUR", None)})
        r = GI.run_import(self.ws, {"TARGETING_30D": ("m.csv", data)}, recalc=False)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], 0)             # nulla scritto: nemmeno il gruppo USD
        self.assertFalse(r.ok)
        pv2 = GI.preview(self.ws, {"TARGETING_30D": ("m.csv", data)}, {"EUR": "DE"})["TARGETING_30D"]["preview"]
        self.assertEqual(pv2["problems"], [])
        self.assertTrue(GI.run_import(self.ws, {"TARGETING_30D": ("m.csv", data)}, {"EUR": "DE"}, recalc=False).ok)
        self.assertEqual({r["marketplace"] for r in self.q("SELECT marketplace FROM global_report_imports")}, {"US", "DE"})

    def test_20_account_a_single_marketplace_flow_is_unchanged(self):
        self.imp("TARGETING_30D", tma.tg_file(tma.CA, 30, 3), "a_t30.csv")
        self.imp("SEARCH_TERM_30D", tma.st_file(tma.CA, 12, 2), "a_s30.csv")
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 2)
        self.assertTrue(GI.associate(self.ws, tma.CID, tg.BOOK_A, account_id=tma.ACC_A, recalc=True).ok)
        self.assertEqual({(r["marketplace"], r["amazon_account_id"]) for r in self.q("SELECT marketplace, amazon_account_id FROM decisions WHERE book_id=?", tg.BOOK_A)}, {("US", tma.ACC_A)})
        self.assertEqual(self.q("SELECT marketplace, status FROM campaign_book_mapping")[0], {"marketplace": "US", "status": "ASSIGNED"})


# ------------------------------------------------------------------ 7-9. libri con lo stesso ASIN in due marketplace
class SameAsinTests(MM):
    def test_7_same_asin_us_and_uk_associate_to_their_own_variant(self):
        self.full()
        self.assoc_both()
        self.assertEqual({(r["amazon_campaign_id"], r["marketplace"], r["book_id"]) for r in self.q("SELECT * FROM campaign_book_mapping WHERE status='ASSIGNED'")},
                         {(US_ID, "US", tg.BOOK_A), (UK_ID, "UK", tg.BOOK_A)})
        cur = {r["marketplace"]: r["currency"] for r in self.q("SELECT DISTINCT marketplace, currency FROM target_rows WHERE book_id=?", tg.BOOK_A)}
        self.assertEqual(cur, {"US": "USD", "UK": "GBP"})
        self.assertEqual(len({r["campaign_id"] for r in self.q("SELECT campaign_id FROM campaigns WHERE book_id=?", tg.BOOK_A)}), 2)

    def test_9_a_uk_campaign_cannot_be_associated_to_the_us_variant_or_to_a_book_without_uk(self):
        self.full()
        r = GI.associate(self.ws, UK_ID, tg.BOOK_A, marketplace="US", account_id=ACC, recalc=False)         # variante US richiesta esplicitamente
        self.assertFalse(r.ok)
        self.assertIn("CONFLICT", " ".join(r.errors))
        r = GI.associate(self.ws, UK_ID, tg.BOOK_B, account_id=ACC, recalc=False)                         # libro solo US
        self.assertFalse(r.ok)
        r = GI.associate(self.ws, US_ID, tg.BOOK_A, marketplace="UK", account_id=ACC, recalc=False)         # e viceversa
        self.assertFalse(r.ok)
        self.assertEqual({x["status"] for x in self.q("SELECT status FROM campaign_book_mapping")}, {"UNASSIGNED"})
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"], 0)

    def test_13_materialization_is_account_and_marketplace_safe(self):
        self.imp("TARGETING_30D", targeting(tgt(CUS, 30, 3)), "us_t30.csv")
        self.assertTrue(GI.associate(self.ws, US_ID, tg.BOOK_A, account_id=ACC, recalc=False).ok)
        base = self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"]
        # la STESSA campagna Amazon (stesso account, stesso ID) arriva in un altro report con valuta GBP: marketplace incompatibile
        uk_same_id = gbp(tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US - 5/8/2026 08:11:33.222", "111", "ROUTE 66"))
        pv = GI.preview(self.ws, {"TARGETING_7D": ("c7.csv", targeting(tgt(uk_same_id, 5, 1, tg.RANGE7)))})["TARGETING_7D"]["preview"]
        self.assertTrue(any("ACCOUNT/MARKETPLACE CONFLICT" in w for w in pv["warnings"]))
        r = self.imp("TARGETING_7D", targeting(tgt(uk_same_id, 5, 1, tg.RANGE7)), "c7.csv")
        self.assertTrue(any("ACCOUNT/MARKETPLACE CONFLICT" in w for w in r.data["conflicts"]))
        m = self.q("SELECT marketplace, marketplace_conflict FROM campaign_book_mapping WHERE amazon_campaign_id=?", US_ID)[0]
        self.assertEqual((m["marketplace"], m["marketplace_conflict"]), ("US", "UK"))                    # il marketplace registrato NON cambia in silenzio
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"], base)                     # nessuna materializzazione automatica dei dati GBP
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows WHERE marketplace='UK'")[0]["n"], 0)
        self.assertEqual(self.q("SELECT materialized FROM global_report_rows WHERE marketplace='UK'")[0]["materialized"], 0)

    def test_conflicting_unassigned_campaign_is_not_associable(self):
        self.imp("TARGETING_30D", targeting(tgt(CUS, 30, 3)), "us_t30.csv")
        uk_same_id = gbp(tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US - 5/8/2026 08:11:33.222", "111", "ROUTE 66"))
        self.imp("TARGETING_7D", targeting(tgt(uk_same_id, 5, 1, tg.RANGE7)), "c7.csv")
        r = GI.associate(self.ws, US_ID, tg.BOOK_A, account_id=ACC, recalc=False)
        self.assertFalse(r.ok)
        self.assertIn("CONFLICT", " ".join(r.errors))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM target_rows")[0]["n"], 0)

    def test_same_campaign_in_two_marketplaces_of_the_same_file_is_flagged_not_materialized(self):
        both = [tgt(CUS, 30, 3), tgt(gbp(tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US - 5/8/2026 08:11:33.222", "111", "ROUTE 66")), 9, 1)]
        r = self.imp("TARGETING_30D", targeting(*both), "weird.csv")
        self.assertTrue(any("più marketplace nello stesso file" in w for w in r.data["conflicts"]))
        self.assertTrue(self.q("SELECT marketplace_conflict FROM campaign_book_mapping")[0]["marketplace_conflict"])
        self.assertFalse(GI.associate(self.ws, US_ID, tg.BOOK_A, account_id=ACC, recalc=False).ok)


# ------------------------------------------------------------------ 14-17. decisioni e righe separate per marketplace
class SeparationTests(MM):
    def test_14_decisions_are_separate_per_marketplace(self):
        self.full()
        self.assoc_both()
        res = GI.recalculate(self.ws, {(tg.BOOK_A, "US"), (tg.BOOK_A, "UK")})
        self.assertTrue(all(x["ok"] for x in res), res)
        d = self.q("SELECT DISTINCT marketplace FROM decisions WHERE book_id=?", tg.BOOK_A)
        self.assertEqual({x["marketplace"] for x in d}, {"US", "UK"})
        runs = self.q("SELECT run_id, marketplace FROM decision_runs WHERE book_id=?", tg.BOOK_A)
        self.assertEqual({r["marketplace"] for r in runs}, {"US", "UK"})
        for r in runs:                                                                                 # ogni run contiene solo il proprio marketplace
            self.assertEqual({x["marketplace"] for x in self.q("SELECT marketplace FROM decisions WHERE run_id=?", r["run_id"])}, {r["marketplace"]})

    def test_15_and_17_search_terms_are_separate_and_the_same_term_does_not_collide(self):
        self.full()
        self.assoc_both()
        rows = {r["marketplace"]: r for r in self.q("SELECT * FROM search_term_rows WHERE search_term=?", TERM)}
        self.assertEqual(set(rows), {"US", "UK"})
        self.assertEqual((rows["US"]["clicks"], rows["UK"]["clicks"]), (12.0, 7.0))
        self.assertEqual((rows["US"]["amazon_campaign_id"], rows["UK"]["amazon_campaign_id"]), (US_ID, UK_ID))
        self.assertNotEqual(rows["US"]["campaign_id"], rows["UK"]["campaign_id"])
        self.assertEqual((rows["US"]["currency"], rows["UK"]["currency"]), ("USD", "GBP"))

    def test_16_targeting_rows_are_separate_per_marketplace(self):
        self.full()
        self.assoc_both()
        rows = {r["marketplace"]: r for r in self.q("SELECT * FROM target_rows WHERE target='route 66'")}
        self.assertEqual(set(rows), {"US", "UK"})
        self.assertEqual((rows["US"]["clicks"], rows["UK"]["clicks"]), (30.0, 18.0))
        self.assertEqual((rows["US"]["amazon_target_id"], rows["UK"]["amazon_target_id"]), ("1001", "1001"))       # lo stesso ID target in due marketplace non collide
        self.assertEqual(len({r["snapshot_id"] for r in self.q("SELECT snapshot_id FROM target_rows")}), 2)

    def test_12_revisions_are_independent_per_marketplace(self):
        self.full()
        self.assoc_both()
        uk_engine = self.q("SELECT COUNT(*) n, SUM(clicks) c FROM target_rows WHERE marketplace='UK'")[0]
        # nuova versione del solo gruppo USD (stessa finestra, contenuto diverso): sostituisce SOLO lo snapshot US
        us_v2 = targeting(tgt(CUS, 35, 4))
        pv = GI.preview(self.ws, {"TARGETING_30D": ("us_v2.csv", us_v2)})["TARGETING_30D"]["preview"]
        self.assertEqual((pv["status"], [g["marketplace"] for g in pv["groups"]]), ("REVISIONE", ["US"]))
        r = GI.run_import(self.ws, {"TARGETING_30D": ("us_v2.csv", us_v2)}, recalc=False)
        self.assertEqual(r.data["revisions_pending"], ["TARGETING_30D"])                                 # senza conferma nulla cambia
        r = GI.run_import(self.ws, {"TARGETING_30D": ("us_v2.csv", us_v2)}, replace={"TARGETING_30D"}, recalc=False)
        self.assertTrue(r.ok, r.errors)
        st = {(i["marketplace"], i["status"]) for i in self.q("SELECT marketplace, status FROM global_report_imports WHERE report_type='TARGETING'")}
        self.assertEqual(st, {("US", "SUPERSEDED"), ("US", "ACTIVE"), ("UK", "ACTIVE")})                  # UK resta ATTIVO
        self.assertEqual(self.q("SELECT COUNT(*) n, SUM(clicks) c FROM target_rows WHERE marketplace='UK'")[0], uk_engine)             # dati UK intatti
        self.assertEqual(self.q("SELECT SUM(clicks) c FROM target_rows WHERE marketplace='US'")[0]["c"], 35.0)
        # e viceversa: nuova versione del solo gruppo GBP
        uk_v2 = targeting(tgt(CUK, 21, 2))
        r = GI.run_import(self.ws, {"TARGETING_30D": ("uk_v2.csv", uk_v2)}, replace={"TARGETING_30D"}, recalc=False)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.q("SELECT SUM(clicks) c FROM target_rows WHERE marketplace='UK'")[0]["c"], 21.0)
        self.assertEqual(self.q("SELECT SUM(clicks) c FROM target_rows WHERE marketplace='US'")[0]["c"], 35.0)
        active = [(i["marketplace"]) for i in self.q("SELECT marketplace FROM global_report_imports WHERE report_type='TARGETING' AND status='ACTIVE'")]
        self.assertEqual(sorted(active), ["UK", "US"])


# ------------------------------------------------------------------ 10-11. backfill
class BackfillMarketplaceTests(MM):
    def uk_phrase(self):
        return rb.phrase_report(gbp(tma.acct_camp(ACC, NAME, UK_ID, "FRASE R66 UK", "222", "ROUTE 66 UK")))

    def us_phrase(self):
        return rb.phrase_report(tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US", "111", "ROUTE 66"))

    def test_10_cross_marketplace_backfill_is_refused_even_with_identical_targets_bids_and_name(self):
        rb.add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", rb.LEGACY_PH, mp="US")
        self.imp("TARGETING_30D", self.uk_phrase(), "uk.csv")
        self.assertEqual([c for c in GI.backfill_candidates(self.ws) if c["amazon_campaign_id"] == UK_ID], [])
        r = GI.associate(self.ws, UK_ID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC, recalc=False, confirm_legacy_account=True, confirm_legacy_marketplace=True)
        self.assertFalse(r.ok)
        self.assertEqual(self.q("SELECT status FROM campaign_book_mapping")[0]["status"], "UNASSIGNED")
        # controllo: la STESSA campagna con valuta USD (stesso marketplace della storica) e' invece un candidato forte
        self.imp("TARGETING_7D", rb.phrase_report(tma.acct_camp(ACC, NAME, US_ID, "FRASE R66 US", "111", "ROUTE 66"), rng=tg.RANGE7), "us7.csv")
        c = [x for x in GI.backfill_candidates(self.ws) if x["amazon_campaign_id"] == US_ID]
        self.assertEqual([(x["level"], x["marketplace"], x["legacy_marketplace"]) for x in c], [("ALTA", "US", "US")])

    def test_11_legacy_with_unknown_marketplace_needs_an_explicit_marketplace_confirmation(self):
        rb.add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", rb.LEGACY_PH, mp="")
        self.imp("TARGETING_30D", self.us_phrase(), "us.csv")
        c = GI.backfill_candidates(self.ws)
        self.assertEqual([(x["level"], x["needs_marketplace_confirmation"], x["needs_account_confirmation"], x["legacy_marketplace"]) for x in c], [("ALTA", True, True, None)])
        r = GI.associate(self.ws, US_ID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC, recalc=False, confirm_legacy_account=True)       # solo l'account
        self.assertFalse(r.ok)
        self.assertIn("marketplace", " ".join(r.errors))
        self.assertEqual(self.q("SELECT status FROM campaign_book_mapping")[0]["status"], "UNASSIGNED")
        r = GI.associate(self.ws, US_ID, tg.BOOK_A, backfill_of="FRASE", account_id=ACC, recalc=False, confirm_legacy_account=True, confirm_legacy_marketplace=True)
        self.assertTrue(r.ok, r.errors)
        m = self.q("SELECT status, backfill_of, legacy_account_confirmed, legacy_marketplace_confirmed FROM campaign_book_mapping")[0]
        self.assertEqual(m, {"status": "ASSIGNED", "backfill_of": "FRASE", "legacy_account_confirmed": 1, "legacy_marketplace_confirmed": 1})

    def test_known_matching_marketplace_needs_no_marketplace_confirmation(self):
        rb.add_legacy(self.ws, tg.BOOK_A, "FRASE", "PHRASE", rb.LEGACY_PH, mp="US")
        self.imp("TARGETING_30D", self.us_phrase(), "us.csv")
        c = GI.backfill_candidates(self.ws)
        self.assertEqual([(x["level"], x["needs_marketplace_confirmation"]) for x in c], [("ALTA", False)])


# ------------------------------------------------------------------ 19. riparazione di un vecchio import «tutto US»
class RepairTests(MM):
    def build_legacy_state(self):
        """Stato reale: i file con USD + GBP importati come un solo marketplace «US»: righe GBP dentro lo snapshot US, mapping UK registrato US, nessuna colonna valuta/marketplace."""
        self.full()
        self.assertTrue(GI.associate(self.ws, US_ID, tg.BOOK_A, account_id=ACC, recalc=False).ok)          # la campagna USD e' gia' associata e smistata (valida)
        c = connect(self.ws.db_path)
        try:
            for sha, in c.execute("SELECT DISTINCT file_sha256 FROM global_report_imports").fetchall():
                us = c.execute("SELECT gimport_id FROM global_report_imports WHERE file_sha256=? AND marketplace='US'", (sha,)).fetchone()[0]
                uk = c.execute("SELECT gimport_id FROM global_report_imports WHERE file_sha256=? AND marketplace='UK'", (sha,)).fetchone()[0]
                c.execute("UPDATE global_report_rows SET gimport_id=? WHERE gimport_id=?", (us, uk))
                c.execute("DELETE FROM global_report_imports WHERE gimport_id=?", (uk,))
                c.execute("UPDATE global_report_imports SET row_count=2, campaign_count=2, currency=NULL, source_gimport_id=NULL WHERE gimport_id=?", (us,))
            c.execute("UPDATE global_report_rows SET marketplace=NULL, currency=NULL")
            c.execute("UPDATE campaign_book_mapping SET marketplace='US' WHERE amazon_campaign_id=?", (UK_ID,))
            c.commit()
        finally:
            c.close()

    def snap(self):
        return {"engine": self.q("SELECT COUNT(*) n, SUM(clicks) c FROM target_rows")[0], "us_map": self.q("SELECT status, book_id, campaign_id FROM campaign_book_mapping WHERE amazon_campaign_id=?", US_ID)[0],
                "ids": [r["gimport_id"] for r in self.q("SELECT gimport_id FROM global_report_imports ORDER BY gimport_id")]}

    def test_19_repair_splits_the_wrong_all_us_import_without_losing_or_duplicating_anything(self):
        self.build_legacy_state()
        before = self.snap()
        raw_before = self.q("SELECT COUNT(*) n, SUM(clicks) c, SUM(spend) s FROM global_report_rows")[0]
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 2)
        self.assertEqual(len(GI.mixed_imports(self.ws)), 2)                                               # rilevati: i 2 file (Targeting + Search Term)
        # PIANO: calcolato su una copia, il DB non cambia
        plan = GI.repair_marketplace_plan(self.ws)
        self.assertFalse(plan["applied"])
        self.assertEqual((plan["created_imports"], plan["moved_rows"]), (2, 2))
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual({(u["amazon_campaign_id"], u["from"], u["to"]) for u in plan["mapping_updates"]}, {(UK_ID, "US", "UK")})
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 2)
        self.assertEqual(self.snap(), before)
        # APPLICA
        r = GI.repair_marketplace_apply(self.ws, recalc=False)
        self.assertTrue(r.ok, r.errors)
        self.assertTrue(r.data["backup"])
        imps = self.q("SELECT * FROM global_report_imports ORDER BY gimport_id")
        self.assertEqual(len(imps), 4)
        self.assertEqual(sorted((i["report_type"], i["marketplace"]) for i in imps), [("SEARCH_TERM", "UK"), ("SEARCH_TERM", "US"), ("TARGETING", "UK"), ("TARGETING", "US")])
        self.assertEqual(set(before["ids"]) - {i["gimport_id"] for i in imps}, set())                      # gli import originali esistono ancora (audit)
        for sha, in {(i["file_sha256"],) for i in imps}:
            pair = [i for i in imps if i["file_sha256"] == sha]
            self.assertEqual(len({i["source_gimport_id"] for i in pair}), 1)                               # stesso file e import sorgente
            self.assertEqual({i["marketplace"]: (i["row_count"], i["campaign_count"], i["currency"]) for i in pair}, {"US": (1, 1, "USD"), "UK": (1, 1, "GBP")})
        self.assertEqual({(x["amazon_campaign_id"], x["marketplace"], x["currency"]) for x in self.q("SELECT amazon_campaign_id, marketplace, currency FROM global_report_rows")},
                         {(US_ID, "US", "USD"), (UK_ID, "UK", "GBP")})
        self.assertEqual(self.q("SELECT COUNT(*) n, SUM(clicks) c, SUM(spend) s FROM global_report_rows")[0], raw_before)                   # nessuna riga persa o duplicata
        m = {x["amazon_campaign_id"]: (x["marketplace"], x["status"]) for x in self.q("SELECT * FROM campaign_book_mapping")}
        self.assertEqual(m, {US_ID: ("US", "ASSIGNED"), UK_ID: ("UK", "UNASSIGNED")})                       # mapping valido US intatto, UK ancora sospeso
        after = self.snap()
        self.assertEqual((after["engine"], after["us_map"]), (before["engine"], before["us_map"]))          # i dati gia' smistati sul libro non cambiano
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_repairs")[0]["n"], 1)                  # tracciato
        # IDEMPOTENTE
        self.assertEqual(GI.mixed_imports(self.ws), [])
        r2 = GI.repair_marketplace_apply(self.ws, recalc=False)
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 4)
        self.assertEqual(r2.data["created_imports"], 0)
        # DOPO la riparazione la campagna UK si associa alla variante UK e solo li'
        self.assertFalse(GI.associate(self.ws, UK_ID, tg.BOOK_A, marketplace="US", account_id=ACC, recalc=False).ok)
        self.assertTrue(GI.associate(self.ws, UK_ID, tg.BOOK_A, account_id=ACC, recalc=False).ok)
        self.assertEqual({(x["marketplace"], x["currency"]) for x in self.q("SELECT marketplace, currency FROM target_rows WHERE amazon_campaign_id=?", UK_ID)}, {("UK", "GBP")})

    def test_before_the_repair_a_mislabelled_campaign_is_not_associable_and_the_reimport_asks_for_the_repair(self):
        self.build_legacy_state()
        r = GI.associate(self.ws, UK_ID, tg.BOOK_A, account_id=ACC, recalc=False)                           # risulta US ma i report dicono GBP
        self.assertFalse(r.ok)
        self.assertIn("Riparazione marketplace", " ".join(r.errors))
        pv = GI.preview(self.ws, {"TARGETING_30D": ("mix_t30.csv", MIX_T30())})["TARGETING_30D"]["preview"]
        self.assertTrue(any("Riparazione marketplace" in p for p in pv["problems"]))
        self.assertFalse(pv["importable"])
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 2)

    def test_repair_never_moves_campaigns_already_associated_with_the_wrong_marketplace(self):
        self.build_legacy_state()
        c = connect(self.ws.db_path)
        c.execute("UPDATE campaign_book_mapping SET status='ASSIGNED', book_id=?, campaign_id='X_UK', campaign_type='PHRASE' WHERE amazon_campaign_id=?", (tg.BOOK_A, UK_ID))
        c.commit()
        c.close()
        plan = GI.repair_marketplace_plan(self.ws)
        self.assertEqual(plan["created_imports"], 0)
        self.assertTrue(any("già associate" in w for w in plan["conflicts"]))
        r = GI.repair_marketplace_apply(self.ws, recalc=False)
        self.assertEqual(len(self.q("SELECT * FROM global_report_imports")), 2)                             # nulla spostato

    def test_old_database_with_unique_file_hash_is_rebuilt_without_losing_rows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "old.db"
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE global_report_imports (gimport_id INTEGER PRIMARY KEY AUTOINCREMENT, report_type TEXT NOT NULL, window_label TEXT NOT NULL, window_days INTEGER NOT NULL,
                  date_from TEXT NOT NULL, date_to TEXT NOT NULL, period_id TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT, file_name TEXT NOT NULL, file_sha256 TEXT NOT NULL UNIQUE,
                  imported_at TEXT NOT NULL, row_count INTEGER, campaign_count INTEGER, empty_search_term_rows INTEGER, warnings_json TEXT, parser_version INTEGER, account_id TEXT,
                  status TEXT NOT NULL DEFAULT 'ACTIVE', revision INTEGER NOT NULL DEFAULT 1, supersedes_gimport_id INTEGER, superseded_by_gimport_id INTEGER, superseded_at TEXT);
                CREATE TABLE global_report_rows (id INTEGER PRIMARY KEY AUTOINCREMENT, gimport_id INTEGER NOT NULL REFERENCES global_report_imports(gimport_id), amazon_campaign_id TEXT NOT NULL,
                  clicks REAL, materialized INTEGER NOT NULL DEFAULT 0, empty_search_term INTEGER NOT NULL DEFAULT 0, raw_json TEXT);
                INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,campaign_count,account_id)
                  VALUES ('TARGETING','RECENT_30D',30,'2026-09-14','2026-10-13','p','US',NULL,'f.csv','abc','2026-10-19T10:00:00',2,2,'acc');
                INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,clicks,raw_json) VALUES (1,'1',5,'{"Valuta budget":"USD"}'),(1,'2',7,'{"Valuta budget":"GBP"}');
            """)
            old.commit()
            old.close()
            c = connect(path)
            try:
                uniq = [r for r in c.execute("PRAGMA index_list(global_report_imports)") if r[2] and r[3] == "u"]
                self.assertEqual([[x[2] for x in c.execute(f"PRAGMA index_info({u[1]})")] for u in uniq if len(list(c.execute(f"PRAGMA index_info({u[1]})"))) == 1], [])      # niente piu' UNIQUE(file_sha256) da solo
                self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 1)
                self.assertEqual(tuple(c.execute("SELECT COUNT(*), SUM(clicks) FROM global_report_rows WHERE gimport_id=1").fetchone()), (2, 12.0))
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(len(GS.find_mixed_imports(c)), 1)                                           # il vecchio import misto e' riconosciuto dal CSV originale
                rep = GS.repair_marketplace(c, apply=True)
                self.assertEqual((rep["created_imports"], rep["moved_rows"]), (1, 1))
                self.assertEqual([tuple(r) for r in c.execute("SELECT marketplace, COUNT(*) FROM global_report_imports GROUP BY 1 ORDER BY 1")], [("UK", 1), ("US", 1)])
                self.assertEqual(GS.repair_marketplace(c, apply=True)["created_imports"], 0)
            finally:
                c.close()


# ------------------------------------------------------------------ 8. UI: selettore libri filtrato per marketplace, raggruppamento, anteprima a gruppi
@unittest.skipIf(AppTest is None, "streamlit non installato")
class UiTests(MM):
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

    def page(self, name):
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / name), default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        return at

    @staticmethod
    def texts(at):
        return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [w.value for w in at.warning] + [e.value for e in at.error] + [i.value for i in at.info]
                         + [h.value for h in at.header] + [h.value for h in at.subheader])

    def test_8_book_selector_shows_marketplace_and_asin_and_filters_by_the_campaign_marketplace(self):
        self.full()
        at = self.page("14_Campagne_da_associare.py")
        uk = [str(o) for o in at.selectbox(key=f"ca_b_{ACC}_{UK_ID}").options]
        us = [str(o) for o in at.selectbox(key=f"ca_b_{ACC}_{US_ID}").options]
        self.assertEqual(uk, [f"Route 66 Day by Day · UK · {tg.BOOK_A}"])                                    # il libro solo US non e' offerto a una campagna UK
        self.assertEqual(us, [f"Route 66 Day by Day · US · {tg.BOOK_A}", f"Rare Coins Book for Beginners · US · {tg.BOOK_B}"])

    def test_association_page_groups_by_account_marketplace_portfolio_and_shows_marketplace_currency(self):
        self.full()
        t = self.texts(self.page("14_Campagne_da_associare.py"))
        for need in (f"ACCOUNT · {NAME} · {ACC}", "Marketplace UK", "Marketplace US", "MARKETPLACE: UK · Valuta: GBP", "MARKETPLACE: US · Valuta: USD", f"ID campagna `{UK_ID}`", f"ID campagna `{US_ID}`",
                     "Portfolio: ROUTE 66 UK"):
            self.assertIn(need, t, need)

    def test_association_button_attaches_the_uk_campaign_to_the_uk_variant_only(self):
        self.full()
        at = self.page("14_Campagne_da_associare.py")
        [b for b in at.button if b.key == f"ca_go_{ACC}_{UK_ID}"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual([(r["book_id"], r["marketplace"]) for r in self.q("SELECT book_id, marketplace FROM campaign_book_mapping WHERE amazon_campaign_id=?", UK_ID)], [(tg.BOOK_A, "UK")])

    def test_import_page_previews_one_group_per_marketplace_and_imports_without_a_global_choice(self):
        at = self.page("13_Report_globali_Amazon_Ads.py")
        up = at.get("file_uploader")
        up[1].upload("mix_t30.csv", MIX_T30(), "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        t = self.texts(at)
        self.assertIn("Gruppo US / USD", t)
        self.assertIn("Gruppo UK / GBP", t)
        self.assertNotIn("DA SELEZIONARE", t)
        self.assertEqual([s for s in at.selectbox if "Marketplace" in s.label], [])                         # nessun dropdown unico per tutto il file
        imp = [b for b in at.button if b.label == "IMPORTA"][0]
        self.assertFalse(imp.disabled)
        imp.click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual({r["marketplace"] for r in self.q("SELECT marketplace FROM global_report_imports")}, {"US", "UK"})

    def test_import_page_asks_the_marketplace_only_for_the_ambiguous_group(self):
        de = eur(tma.acct_camp(ACC, NAME, "333000333000333", "FRASE DE", "333", "DE"))
        at = self.page("13_Report_globali_Amazon_Ads.py")
        at.get("file_uploader")[1].upload("m.csv", targeting(tgt(CUS, 30, 3), tgt(de, 9, 1)), "text/csv")
        at.run()
        [b for b in at.button if b.label == "ANALIZZA FILE"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        boxes = [s for s in at.selectbox if "valuta EUR" in s.label]
        self.assertEqual(len(boxes), 1)
        self.assertTrue([b for b in at.button if b.label == "IMPORTA"][0].disabled)
        boxes[0].select("DE").run()
        self.assertFalse(at.exception, at.exception)
        self.assertFalse([b for b in at.button if b.label == "IMPORTA"][0].disabled)
        self.assertNotIn("DA SELEZIONARE", self.texts(at))

    def test_repair_section_appears_only_when_a_legacy_mixed_import_exists(self):
        self.full()
        self.assertNotIn("RIPARAZIONE MARKETPLACE", " ".join(e.label for e in self.page("13_Report_globali_Amazon_Ads.py").expander))
        RepairTests.build_legacy_state(self)
        at = self.page("13_Report_globali_Amazon_Ads.py")
        self.assertTrue(any("RIPARAZIONE MARKETPLACE" in e.label for e in at.expander))
        before = self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"]
        go = [b for b in at.button if b.key == "gr_repair_go"][0]
        self.assertTrue(go.disabled)                                                                         # serve la conferma esplicita
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports")[0]["n"], before)
        t = self.texts(self.page("14_Campagne_da_associare.py"))
        self.assertIn("marketplace da verificare", t.lower())
        self.assertTrue([b for b in self.page("14_Campagne_da_associare.py").button if b.key == f"ca_go_{ACC}_{UK_ID}"][0].disabled)


if __name__ == "__main__":
    unittest.main()
