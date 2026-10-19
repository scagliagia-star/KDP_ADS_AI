"""(1) Filtro di INTENTO per le promozioni Search Term: performance buona != pertinenza sufficiente («route 66 shirt» non si promuove, ma non e' per questo una negativa);
(2) identificazione della CAMPAGNA (nome + ID Amazon) nelle schede; (3) riconoscimento del tipo campagna (strutturato > tipo di corrispondenza > nome).
Dati sintetici + COPIA del DB reale (sola lettura). Nessuna rete, nessuna Amazon."""
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

import test_opportunities as to
import test_overview as tov
from data_access import overview as O
from kdp_ads import globalreport as G
from kdp_ads import growth as Gr
from kdp_ads import intent as I
from kdp_ads.opportunities import NEGATIVE, OUT_OF_INTENT, PROMOTE, SUGGEST_PHRASE, WATCH, compute_opportunities, make_ctx, unique_terms
from kdp_ads.rules import load_rules

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

RULES = load_rules(ROOT / "CONFIG" / "rules.json")


def ctx_with(**intent):
    book = {"book_id": "B1", "title": "T", "marketplace": "US", "currency": "USD", "price": 22.99, "royalty_per_copy": 9.0, "target_acos": 35.0, "break_even_acos": 39.1}
    rules = {**RULES, "intent": {**RULES.get("intent", {}), **intent}}
    return make_ctx(book, rules)


def run(rows, targets=(), ctx=None):
    return compute_opportunities(list(rows), list(targets), to.CAMPS, ctx or ctx_with(), None)


def auto_term(term, clicks=7, orders=2, spend=5.89):
    return to.both("AUTO", "OTHER", "loose-match", term, clicks, orders, spend)


def phrase_term(term, clicks=20, orders=3, spend=15.0):
    return to.both("FRASE", "PHRASE", "route 66", term, clicks, orders, spend)


# ------------------------------------------------------------------ 1. filtro di intento
class IntentFilterUnitTests(unittest.TestCase):
    def blocked(self, term, **kw):
        return I.check(term, **kw)

    def test_apparel_merch_and_objects_are_recognised_with_token_matching(self):
        for term, matched in (("route 66 shirt", "shirt"), ("route 66 t-shirt", "shirt"), ("route 66 t shirt", "shirt"), ("route 66 tee shirts", "shirt"), ("route 66 hoodie", "hoodie"),
                              ("route 66 decal", "decal"), ("route 66 stickers", "sticker"), ("route 66 wall art", "wall art"), ("route 66 phone case", "phone case"),
                              ("route 66 patches", "patch"), ("route 66 mug", "mug"), ("route 66 toy", "toy"), ("route 66 hat", "hat"), ("route 66 keychain", "keychain")):
            hit = self.blocked(term)
            self.assertIsNotNone(hit, term)
            self.assertEqual(hit["matched"], matched, term)
            self.assertEqual(hit["reason"], "Ha convertito, ma la query indica un intento di acquisto diverso dal contenuto del libro.")

    def test_no_naive_substring_false_positives(self):
        for term in ("route 66 shirtwaist history", "pinterest route 66", "route 66 pine trees guide", "capital route 66 trip", "teeth route 66", "route 66 hatchet", "route 66 mugshot gallery", "mapping route 66"):
            self.assertIsNone(self.blocked(term), term)

    def test_book_oriented_queries_stay_eligible(self):
        for term in ("route 66 travel guide", "route 66 map book", "route 66 history book", "route 66 road trip", "ez66 guide for travelers", "route 66 journal", "route 66 history"):
            self.assertIsNone(self.blocked(term), term)

    def test_book_guide_journal_words_do_not_rescue_a_blocked_signal(self):
        for term in ("route 66 sticker book", "route 66 shirt book", "route 66 decal guide", "route 66 poster journal", "route 66 mug planner", "t shirt guide"):
            hit = self.blocked(term)
            self.assertIsNotNone(hit, term)
            self.assertEqual(hit["reason"], I.REASON)
        self.assertEqual(I.config({}, "B1")["book_intent_wins"], False)                          # default: nessun salvataggio automatico
        self.assertIsNone(self.blocked("route 66 sticker book", book_intent_wins=True))           # interruttore opzionale (spento di default)

    def test_only_explicit_exceptions_unblock_a_book_like_blocked_query(self):
        self.assertIsNone(self.blocked("route 66 sticker book", allow=["sticker"]))              # allow globale
        cfg = I.config({"intent": {"book_allow": {"B1": ["sticker"]}}}, "B1")
        self.assertIsNone(self.blocked("route 66 sticker book", allow=cfg["allow"]))              # allow per libro
        cfg_other = I.config({"intent": {"book_allow": {"B1": ["sticker"]}}}, "B2")
        self.assertIsNotNone(self.blocked("route 66 sticker book", allow=cfg_other["allow"]))

    def test_allowlist_unblocks_a_signal_globally_and_per_book(self):
        self.assertIsNone(self.blocked("t shirt design", allow=["shirt"]))
        self.assertIsNotNone(self.blocked("route 66 sticker", allow=["shirt"]))                 # l'allowlist sblocca solo i segnali indicati
        cfg = I.config({"intent": {"allow": ["decal"], "book_allow": {"B9": ["shirt"]}}}, "B9")
        self.assertEqual(sorted(cfg["allow"]), ["decal", "shirt"])
        self.assertEqual(I.config({"intent": {"book_allow": {"B9": ["shirt"]}}}, "B1")["allow"], [])
        self.assertIsNone(self.blocked("shirt design ideas", allow=cfg["allow"]))

    def test_extra_blocked_and_disabled(self):
        self.assertIsNotNone(self.blocked("route 66 lunchbox", extra_blocked=["lunchbox"]))
        self.assertIsNone(self.blocked("route 66 shirt", enabled=False))
        self.assertIsNone(self.blocked("route 66 shirt book", book_intent_wins=True))
        self.assertIsNotNone(self.blocked("route 66 shirt book", book_intent_wins=False))
        self.assertIsNone(self.blocked(""))
        self.assertIsNone(self.blocked(None))


class IntentInOpportunitiesTests(unittest.TestCase):
    def test_route_66_shirt_is_not_promoted_to_exact_even_with_great_metrics(self):
        res = run(auto_term("route 66 shirt", 7, 2, 5.89))
        r = to.only(res, "route 66 shirt")
        self.assertEqual(r["opportunity_type"], OUT_OF_INTENT)
        self.assertIn("OUT_OF_INTENT", r["flags"])
        self.assertEqual(r["intent"]["category"], "APPAREL")
        self.assertFalse(r["operationally_actionable"])
        self.assertAlmostEqual(r["acos"], 5.89 / (2 * to.SALE) * 100, places=2)
        self.assertIn("Ha convertito, ma la query indica un intento di acquisto diverso dal contenuto del libro.", r["reason"])
        self.assertEqual((unique_terms(res["records"], PROMOTE), unique_terms(res["records"], SUGGEST_PHRASE)), ([], []))

    def test_t_shirt_and_decal_are_not_promoted_in_any_path(self):
        for term in ("route 66 t-shirt", "route 66 decal"):
            for rows in (auto_term(term, 7, 2, 5.89), phrase_term(term), auto_term(term, 4, 1, 1.7)):        # AUTO->EXACT, FRASE->EXACT, AUTO->FRASE
                r = to.only(run(rows), term)
                self.assertEqual(r["opportunity_type"], OUT_OF_INTENT, (term, rows[0]["campaign_id"]))

    def test_sticker_book_is_out_of_intent_unless_explicitly_allowed(self):
        r = to.only(run(phrase_term("route 66 sticker book")), "route 66 sticker book")
        self.assertEqual((r["opportunity_type"], r["intent"]["matched"]), (OUT_OF_INTENT, "sticker"))
        self.assertNotIn(r["opportunity_type"], (NEGATIVE, PROMOTE))
        self.assertEqual(to.only(run(auto_term("route 66 sticker book", 4, 1, 1.7)), "route 66 sticker book")["opportunity_type"], OUT_OF_INTENT)
        self.assertEqual(to.only(run(phrase_term("route 66 history book")), "route 66 history book")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(phrase_term("route 66 sticker book"), ctx=ctx_with(allow=["sticker"])), "route 66 sticker book")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(phrase_term("route 66 sticker book"), ctx=ctx_with(book_allow={"B1": ["sticker"]})), "route 66 sticker book")["opportunity_type"], PROMOTE)

    def test_book_intent_terms_remain_eligible(self):
        self.assertEqual(to.only(run(phrase_term("route 66 travel guide")), "route 66 travel guide")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(phrase_term("route 66 map book")), "route 66 map book")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(auto_term("route 66 map book", 4, 1, 1.7)), "route 66 map book")["opportunity_type"], SUGGEST_PHRASE)

    def test_out_of_intent_is_not_automatically_a_negative_candidate(self):
        res = run(auto_term("route 66 shirt", 7, 2, 5.89))
        self.assertEqual(unique_terms(res["records"], NEGATIVE), [])
        self.assertNotEqual(to.only(res, "route 66 shirt")["opportunity_type"], NEGATIVE)
        # un termine fuori intento che SPRECA resta valutato dalle regole sui risultati: l'intento non decide ne' crea la negativa
        waste = to.only(run(to.both("FRASE", "PHRASE", "route 66", "route 66 shirt", 30, 0, 30.0)), "route 66 shirt")
        self.assertEqual(waste["opportunity_type"], NEGATIVE)

    def test_allowlist_can_unblock_a_term(self):
        ctx = ctx_with(allow=["shirt"])
        self.assertEqual(to.only(run(phrase_term("t shirt design"), ctx=ctx), "t shirt design")["opportunity_type"], PROMOTE)
        per_book = ctx_with(book_allow={"B1": ["shirt"]})
        self.assertEqual(to.only(run(phrase_term("route 66 shirt"), ctx=per_book), "route 66 shirt")["opportunity_type"], PROMOTE)
        other_book = ctx_with(book_allow={"B7": ["shirt"]})
        self.assertEqual(to.only(run(phrase_term("route 66 shirt"), ctx=other_book), "route 66 shirt")["opportunity_type"], OUT_OF_INTENT)
        book = {"book_id": "B1", "title": "T", "marketplace": "US", "currency": "USD", "price": 22.99, "royalty_per_copy": 9.0, "target_acos": 35.0, "break_even_acos": 39.1,
                "rule_overrides": {"intent": {"allow": ["shirt"]}}}
        ov = make_ctx(book, RULES)                                                                  # rule_overrides del libro in books.json
        self.assertEqual(to.only(run(phrase_term("route 66 shirt"), ctx=ov), "route 66 shirt")["opportunity_type"], PROMOTE)

    def test_out_of_intent_never_feeds_growth_new_campaign_or_kpis(self):
        rows = []
        for term in ("route 66 shirt", "route 66 decal", "route 66 poster"):
            rows += phrase_term(term)
        res = run(rows, [], ctx_with())
        self.assertIsNone(res["new_exact_campaign"])                                              # 3 termini buoni ma fuori intento: nessuna "crea campagna EXACT"
        g = Gr.build_growth([], res["records"], {"royalty_per_copy": 9.0, "target_acos": 35.0}, Gr.DEFAULTS)
        self.assertEqual((g["TO_EXACT"], g["AUTO_TO_PHRASE"]), ([], []))
        ov = tov.O.build_overview([tov.dec(1)], tov.BOOK, tov.SUMMARY, [], res, tov.parse_delivery_detail)
        self.assertEqual((ov["kpi"]["exact"], ov["kpi"]["negatives"]), (0, 0))                    # nessun conteggio KPI

    def test_no_regression_on_valid_promotions(self):
        res = run(phrase_term("rv camping bible", 4, 3, 3.55))
        self.assertEqual(to.only(res, "rv camping bible")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(auto_term("dry mix recipe book", 100, 7, 31.56)), "dry mix recipe book")["opportunity_type"], PROMOTE)
        self.assertEqual(to.only(run(auto_term("glute book", 4, 1, 1.7)), "glute book")["opportunity_type"], SUGGEST_PHRASE)
        self.assertEqual(to.only(run(auto_term("rare coin book", 5, 3, 3.10)), "rare coin book")["opportunity_type"], PROMOTE)

    def test_rules_json_has_the_intent_section_with_empty_exceptions(self):
        self.assertEqual({k: RULES["intent"][k] for k in ("enabled", "allow", "book_allow", "extra_blocked", "book_intent_wins")},
                         {"enabled": True, "allow": [], "book_allow": {}, "extra_blocked": [], "book_intent_wins": False})


# ------------------------------------------------------------------ 3. tipo campagna
class CampaignTypeDetectionTests(unittest.TestCase):
    def kind(self, matches, targets=(), names=()):
        return G.classify_campaign(matches, targets, names)

    def test_auto_from_automatic_targeting_and_from_close_match(self):
        self.assertEqual(self.kind({"TARGETING_EXPRESSION_PREDEFINED": 4}, ["close-match", "loose-match"], ["AUTO"]), ("OTHER", "AUTO", "STRUCTURED"))
        self.assertEqual(self.kind({}, ["close-match"], ["campagna qualunque"]), ("OTHER", "AUTO", "STRUCTURED"))              # parent target close-match
        for g in ("loose-match", "complements", "substitutes"):
            self.assertEqual(self.kind({}, [g], [])[1], "AUTO")

    def test_phrase_and_exact_from_match_type(self):
        for names in (["FRASE - 8/28/2026"], ["PHRASE - x"], ["qualunque"]):
            self.assertEqual(self.kind({"PHRASE": 8}, [], names)[:2], ("PHRASE", "PHRASE"))
        for names in (["ESATTA - 9/29"], ["EXACT"], ["qualunque"]):
            self.assertEqual(self.kind({"EXACT": 5}, [], names)[:2], ("EXACT", "EXACT"))
        self.assertEqual(self.kind({"PHRASE": 5}, [], [])[2], "MATCH_TYPE")

    def test_product_targeting_is_competitor(self):
        self.assertEqual(self.kind({"TARGETING_EXPRESSION": 3}, ['asin="B0ABCDEFGH"'], ["R66_PRODUCT_TARGET"]), ("COMPETITOR", "COMPETITOR", "STRUCTURED"))
        self.assertEqual(self.kind({}, ['asin-expanded="0794851630"'], [])[:2], ("COMPETITOR", "COMPETITOR"))

    def test_priority_structured_over_match_type_over_name(self):
        self.assertEqual(self.kind({"TARGETING_EXPRESSION_PREDEFINED": 4}, [], ["FRASE ESATTA"])[:2], ("OTHER", "AUTO"))        # strutturato batte il nome
        self.assertEqual(self.kind({"PHRASE": 3}, [], ["AUTO campaign"])[:3], ("PHRASE", "PHRASE", "MATCH_TYPE"))              # match type batte il nome
        self.assertEqual(self.kind({"EXACT": 3}, [], ["FRASE old name"])[1], "EXACT")

    def test_name_is_only_the_last_resort_and_never_guesses(self):
        self.assertEqual(self.kind({}, [], ["CM_EXACT_RVCamping_Core"])[:3], ("EXACT", "EXACT", "NAME"))
        self.assertEqual(self.kind({"PHRASE": 1, "EXACT": 1}, [], ["FRASE core"])[:3], ("PHRASE", "PHRASE", "NAME"))             # parita' di match type: decide il nome
        self.assertEqual(self.kind({}, [], ["PRODOTTI - 7/9/2026"])[:2], ("COMPETITOR", "COMPETITOR"))

    def test_ambiguous_case_is_other_and_not_invented(self):
        self.assertEqual(self.kind({}, [], ["campagna X"]), ("OTHER", "AMBIGUOUS", "NONE"))
        self.assertEqual(self.kind({"PHRASE": 1, "EXACT": 1}, [], ["nome neutro"])[:2], ("OTHER", "AMBIGUOUS"))
        self.assertEqual(self.kind({}, [], ["R66_AUTO", "FRASE"])[:2], ("OTHER", "AMBIGUOUS"))                                  # nome con due tipi diversi
        self.assertEqual(G.detect_campaign_type({}, [], ["campagna X"]), "OTHER")

    def test_campaign_summary_exposes_kind_and_source(self):
        rows = [{"campaign_id": "1", "campaign_name": "ESATTA NUOVA", "portfolio_id": None, "portfolio_name": None, "ad_group_name": "ESATTA NUOVA- 10/5", "targeting": "a b",
                 "match_type_raw": "EXACT", "row_from": None, "row_to": None, "impressions": 1, "clicks": 1, "spend": 1, "orders": 0, "sales": 0, "account_id": "A", "account_name": "n"}]
        s = G.campaign_summary(rows)["1"]
        self.assertEqual((s["campaign_type"], s["campaign_kind"], s["type_source"]), ("EXACT", "EXACT", "MATCH_TYPE"))

    def test_real_global_files_keep_their_previous_types(self):
        dl = Path(os.environ.get("USERPROFILE", "")) / "Downloads" / "TEST_KDP_Targeting_30D.csv"
        if not dl.is_file():
            self.skipTest("report globale reale assente")
        s = G.campaign_summary(G.parse_global_report(dl.name, dl.read_bytes())["rows"])
        old = {cid: G.detect_campaign_type(c["matches"], c["targets"]) for cid, c in s.items()}
        self.assertEqual({cid: c["campaign_type"] for cid, c in s.items()}, old)
        self.assertEqual(len(s), 40)
        kinds = {c["campaign_kind"] for c in s.values()}
        self.assertEqual(kinds, {"AUTO", "PHRASE", "EXACT", "COMPETITOR"})                      # nessuna campagna reale resta ambigua
        self.assertEqual(sum(1 for c in s.values() if c["campaign_kind"] == "AUTO"), sum(1 for c in s.values() if c["campaign_type"] == "OTHER"))


# ------------------------------------------------------------------ 2. identita' della campagna nelle schede
def cdec(i, campaign, name, amz, ctype="OTHER", **o):
    return tov.dec(i, campaign_id=campaign, campaign_name=name, campaign_type=ctype, amazon_campaign_id=amz, target="close-match", target_type="AUTO_TARGETING_GROUP",
                   final_action="WATCH", lifetime_clicks=40.0, lifetime_spend=20.0, **o)


class CampaignIdentityTests(unittest.TestCase):
    def test_campaign_text_compact_with_name_and_id(self):
        self.assertEqual(O.campaign_text("AUTO- 7/9/2026 09:30:04.968", "206528816350500"), "Campagna: AUTO- 7/9/2026 09:30:04.968 · ID 206528816350500")
        long = O.campaign_text("AUTO- 7/9/2026 09:30:04.968 con un nome davvero molto lungo", "206528816350500")
        self.assertTrue(long.startswith("Campagna: AUTO- 7/9/2026") and "…" in long and long.endswith("· ID 206528816350500"))
        self.assertIsNone(O.campaign_text("AUTO", None))                                          # storica senza ID e nessuna ambiguita': nessun rumore
        self.assertEqual(O.campaign_text("AUTO", None, internal_id="AUTO", show_legacy=True), "Campagna: AUTO · storica, senza ID Amazon")

    def test_two_autos_of_the_same_book_are_distinguishable_in_every_section(self):
        a = cdec(1, "AUTO", "AUTO- 7/9/2026 09:30:04.968", "206528816350500", amazon_account_id="accX", amazon_account_name="Autore")
        b = cdec(2, "AUTO_2", "AUTO - 10/2/2026 14:39:07.551", "218356313176144", amazon_account_id="accX", amazon_account_name="Autore")
        a["lifetime_clicks"] = b["lifetime_clicks"] = 40.0
        ov = O.build_overview([a, b], tov.BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        cards = [c for k in ("actions", "monitor", "good", "paused", "delivery") for c in ov[k]]
        self.assertEqual(len(cards), 2)
        lines = {c["campaign_line"] for c in cards}
        self.assertEqual(lines, {"Campagna: AUTO- 7/9/2026 09:30:04.968 · ID 206528816350500", "Campagna: AUTO - 10/2/2026 14:39:07.551 · ID 218356313176144"})
        self.assertTrue(all(c["account"] == "Account: Autore · accX" for c in cards))              # account + campagna + target: gerarchia completa

    def test_legacy_auto_is_shown_with_its_name_when_the_book_has_two_autos(self):
        legacy = cdec(1, "AUTO", "AUTO", None)
        glob = cdec(2, "AUTO_2", "AUTO - 10/2/2026 14:39:07.551", "218356313176144")
        ov = O.build_overview([legacy, glob], tov.BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        lines = {c["title"]: c["campaign_line"] for k in ("monitor", "good", "delivery", "actions", "paused") for c in ov[k]}
        self.assertIn("Campagna: AUTO · storica, senza ID Amazon", lines.values())
        single = O.build_overview([cdec(1, "AUTO", "AUTO", None)], tov.BOOK, tov.SUMMARY, [], None, tov.parse_delivery_detail)
        self.assertEqual([c["campaign_line"] for k in ("monitor", "good", "delivery", "actions", "paused") for c in single[k]], [None])

    def test_opportunity_items_carry_the_campaign(self):
        rec = tov.opp("rv camping bible", "PROMOTE_TO_EXACT", campaign_id="FRASE", campaign_name="FRASE - 8/28/2026 10:01:39.723", amazon_campaign_id="281284384323178",
                      amazon_account_id="accX")
        ov = O.build_overview([tov.dec(1)], tov.BOOK, tov.SUMMARY, [], {"records": [rec], "new_exact_campaign": None}, tov.parse_delivery_detail, account_names={"accX": "Autore"})
        self.assertEqual(ov["exact"][0]["campaign_line"], "Campagna: FRASE - 8/28/2026 10:01:39.723 · ID 281284384323178")
        self.assertEqual(ov["exact"][0]["account"], "Account: Autore · accX")

    def test_ui_renders_account_then_campaign(self):
        class Rec:
            def __init__(self):
                self.captions = []

            def caption(self, text, *a, **k):
                self.captions.append(text)
        from components import overview_ui
        st = Rec()
        overview_ui._acct(st, {"account": "Account: Autore · accX", "campaign_line": "Campagna: AUTO · ID 1"})
        self.assertEqual(st.captions, ["Account: Autore · accX", "Campagna: AUTO · ID 1"])
        st = Rec()
        overview_ui._acct(st, {"account": None, "campaign_line": None})
        self.assertEqual(st.captions, [])


# ------------------------------------------------------------------ dati reali (COPIA del DB)
import realdata  # noqa: E402  (snapshot deterministico dei dati storici: vedi realdata.py)
REAL_DB = realdata.snapshot()
R66 = "B0GZNG6N1D"


LIVE_DB = ROOT / "DATABASE" / "kdp_ads.db"


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(os.environ.get("KDP_REAL_SMOKE") == "1" and LIVE_DB.is_file(),
                     "smoke sul DB operativo (COPIA in sola lettura): opt-in con KDP_REAL_SMOKE=1; dipende dallo stato corrente dei report globali, quindi NON fa parte della suite standard")
class RealRoute66SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        REAL_DB = LIVE_DB
        cls.real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        cls.books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        src.close()
        dst.row_factory = sqlite3.Row
        cls.ok = bool(dst.execute("SELECT 1 FROM sqlite_master WHERE name='campaign_book_mapping'").fetchone()) and bool(
            dst.execute("SELECT 1 FROM decision_runs WHERE book_id=?", (R66,)).fetchone())
        if cls.ok:
            from kdp_ads.config import CONFIG_DIR, load_mapping, load_settings
            from kdp_ads.pipeline import run_analysis, run_decisions
            lg, s = logging.getLogger("t"), realdata.settings()
            ra = run_analysis(s, load_mapping(), CONFIG_DIR / "rules.json", R66, Path(cls.tmp.name) / "out", lg, save_db=True, conn=dst, marketplace="US")
            rd = run_decisions(s, CONFIG_DIR / "rules.json", R66, Path(cls.tmp.name) / "out", lg, ra["run_id"], save_db=True, conn=dst, marketplace="US")
            dst.commit()
            cls.run_id = rd["run_id"]
            from data_access.repository import Repo
            cls.repo = Repo(str(cls.db))
            b = cls.repo.book(R66)
            cls.book = {**b, **[m for m in cls.repo.marketplaces(R66) if m["marketplace"] == "US"][0]}
            cls.opp = cls.repo.search_term_opportunities(cls.book, "US")
            cls.dec = cls.repo.decisions(cls.run_id)
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(cls.db)
            try:
                at = AppTest.from_file(str(tov.PAGE), default_timeout=120).run()
                tov.select(at, "Libro", R66)
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
            self.skipTest("Route 66 con report globali non presente nel DB")

    def test_route_66_shirt_is_out_of_intent_and_not_in_exact(self):
        by = {(r["campaign_id"], r["search_term"]): r for r in self.opp["records"]}
        shirt = [r for k, r in by.items() if k[1] == "route 66 shirt"]
        self.assertTrue(shirt)
        self.assertEqual({r["opportunity_type"] for r in shirt}, {OUT_OF_INTENT})
        self.assertNotIn("route 66 shirt", unique_terms(self.opp["records"], PROMOTE))
        self.assertNotIn("route 66 shirt", unique_terms(self.opp["records"], SUGGEST_PHRASE))
        self.assertNotIn("route 66 shirt", unique_terms(self.opp["records"], NEGATIVE))
        exact_block = self.text.split("KEYWORD DA METTERE IN EXACT")[1].split("KEYWORD DA VALUTARE COME NEGATIVE")[0]
        self.assertNotIn("route 66 shirt", exact_block)

    def test_no_blocked_term_is_promoted_anywhere_in_the_book(self):
        for r in self.opp["records"]:
            if r["opportunity_type"] in (PROMOTE, SUGGEST_PHRASE):
                self.assertIsNone(I.check(r["search_term"]), r["search_term"])

    def test_valid_targets_are_not_filtered_and_frase_route_66_keeps_bid_down(self):
        d = {(x["campaign_id"], x["target"]): x for x in self.dec}
        self.assertEqual(d[("FRASE", "route 66")]["final_action"], "BID_DOWN_5_CANDIDATE")
        for tgt in ("route 66 travel guide", "route 66 map"):
            self.assertIsNone(I.check(tgt), tgt)                                                   # i target validi non sono toccati dal filtro
            self.assertTrue([x for x in self.dec if x["target"] == tgt])

    def test_autos_show_campaign_name_and_id(self):
        autos = [d for d in self.dec if d["campaign_type"] == "OTHER" and d.get("amazon_campaign_id")]
        self.assertTrue(autos)
        for d in autos:
            self.assertTrue(d["campaign_name"])
        ov = O.build_overview(self.dec, self.book, {"actionable": 1}, [], self.opp, tov.parse_delivery_detail, "$")
        lines = {c["campaign_line"] for k in ("actions", "monitor", "good", "delivery", "paused") for c in ov[k] if c["decision"]["campaign_type"] == "OTHER"}
        self.assertEqual(lines, {"Campagna: AUTO · storica, senza ID Amazon"})                       # la AUTO storica e' distinta dalla AUTO globale (stesso nome «AUTO»)
        new_auto = [d for d in autos if d["campaign_id"] == "AUTO_2"]
        self.assertTrue(new_auto)
        self.assertEqual(O.campaign_text(new_auto[0]["campaign_name"], new_auto[0]["amazon_campaign_id"]), "Campagna: AUTO · ID 218356313176144")
        self.assertIn("Campagna: AUTO · storica, senza ID Amazon", self.text)                        # visibile nella pagina
        self.assertIn("Campagna: FRASE · ID 281284384323178", self.text)
        self.assertIn("Campagna: ESATTA · ID 175742940768847", self.text)

    def test_account_is_still_shown_with_the_campaign(self):
        self.assertTrue(any(l.startswith("Account:") for l in self.text.splitlines()))

    def test_nothing_written(self):
        self.assertEqual(hashlib.sha256(LIVE_DB.read_bytes()).hexdigest(), self.real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), self.books_hash)


import test_global_reports as tg
import test_multi_account as tma
from kdp_ads import globalstore as GS
from ops import global_import as GI


class PipelineIdentityAndTypeTests(tma.MA):
    def test_import_stores_the_detected_kind_and_decisions_keep_the_amazon_campaign_id(self):
        auto = tma.acct_camp(tma.ACC_A, tma.NAME_A, "206528816350500", "AUTO- 7/9/2026 09:30:04.968", "111", "ROUTE 66")
        data = tg.csv_text(tg.TG_HEAD, [tg.tg(auto, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 30, 2, 20.0, 60.0),
                                        tg.tg(tma.CA, "route 66", "PHRASE", "1001", 80, 7, 90.0, 280.0)])
        self.imp("TARGETING_30D", data, "t30.csv")
        m = {r["amazon_campaign_id"]: r for r in self.q("SELECT * FROM campaign_book_mapping")}
        self.assertEqual((m["206528816350500"]["detected_type"], m["206528816350500"]["detected_kind"]), ("OTHER", "AUTO"))
        self.assertEqual((m[tma.CID]["detected_type"], m[tma.CID]["detected_kind"]), ("PHRASE", "PHRASE"))
        for cid in m:
            self.assertTrue(GI.associate(self.ws, cid, tg.BOOK_A, account_id=tma.ACC_A, recalc=False).ok)
        self.assertTrue(all(x["ok"] for x in GI.recalculate(self.ws, {(tg.BOOK_A, "US")})))
        d = {r["campaign_name"]: r["amazon_campaign_id"] for r in self.q("SELECT DISTINCT campaign_name, amazon_campaign_id FROM decisions WHERE book_id=?", tg.BOOK_A)}
        self.assertEqual(d, {"AUTO- 7/9/2026 09:30:04.968": "206528816350500", "FRASE ROUTE66 - 5/8/2026 08:11:33.222": tma.CID})

    def test_already_mapped_campaigns_keep_their_type_and_old_tables_get_the_new_column(self):
        self.imp("TARGETING_30D", tma.tg_file(tma.CA), "a.csv")
        self.assertTrue(GI.associate(self.ws, tma.CID, tg.BOOK_A, account_id=tma.ACC_A, campaign_type="EXACT", recalc=False).ok)       # scelta manuale dell'utente
        self.imp("TARGETING_7D", tma.tg_file(tma.CA, 5, 1, tg.RANGE7), "a7.csv")                                                         # nuovo import: il tipo scelto non cambia
        r = self.q("SELECT campaign_type, detected_type, detected_kind, status FROM campaign_book_mapping")[0]
        self.assertEqual((r["campaign_type"], r["status"], r["detected_kind"]), ("EXACT", "ASSIGNED", "PHRASE"))
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE campaign_book_mapping (mapping_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL DEFAULT '', amazon_campaign_id TEXT NOT NULL, "
                  "status TEXT NOT NULL DEFAULT 'UNASSIGNED', book_id TEXT, detected_type TEXT, UNIQUE (account_id, amazon_campaign_id))")
        c.execute("INSERT INTO campaign_book_mapping (account_id, amazon_campaign_id, detected_type) VALUES ('a','1','PHRASE')")
        GS.ensure_schema(c)
        self.assertIn("detected_kind", {x[1] for x in c.execute("PRAGMA table_info(campaign_book_mapping)")})
        self.assertEqual(c.execute("SELECT detected_type, detected_kind FROM campaign_book_mapping").fetchone(), ("PHRASE", None))        # riga esistente intatta
        c.close()


class SyntheticRoute66EndToEndTests(tma.MA):
    """Route 66 ricostruito con report globali SINTETICI (deterministico, nessun DB operativo): import -> associazione -> run decisionale -> opportunita'."""
    SHIRT, STICKER, GUIDE, MAP = "route 66 shirt", "route 66 sticker book", "route 66 travel guide", "map of route 66"

    def setUp(self):
        super().setUp()
        auto = tma.acct_camp(tma.ACC_A, tma.NAME_A, "206528816350500", "AUTO- 7/9/2026 09:30:04.968", "111", "ROUTE 66")
        t = tg.csv_text(tg.TG_HEAD, [tg.tg(tma.CA, "route 66", "PHRASE", "1001", 80, 7, 90.0, 280.0), tg.tg(auto, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 40, 6, 30.0, 140.0)])
        s = tg.csv_text(tg.ST_HEAD, [tg.st_row(auto, x, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 12, 3, 6.0, 69.0) for x in (self.SHIRT, self.STICKER, self.GUIDE, self.MAP)])
        self.imp("TARGETING_30D", t, "t30.csv")
        self.imp("SEARCH_TERM_30D", s, "s30.csv")
        for cid in ("206528816350500", tma.CID):
            self.assertTrue(GI.associate(self.ws, cid, tg.BOOK_A, account_id=tma.ACC_A, recalc=False).ok)
        self.assertTrue(all(x["ok"] for x in GI.recalculate(self.ws, {(tg.BOOK_A, "US")})))
        from data_access.repository import Repo
        repo = Repo(str(self.ws.db_path))
        b = repo.book(tg.BOOK_A)
        self.book = {**b, **[m for m in repo.marketplaces(tg.BOOK_A) if m["marketplace"] == "US"][0]}
        self.opp = repo.search_term_opportunities(self.book, "US")

    def types(self, term):
        return {r["opportunity_type"] for r in self.opp["records"] if r["search_term"] == term}

    def test_shirt_and_sticker_book_are_out_of_intent_and_never_promoted_or_negated(self):
        for term in (self.SHIRT, self.STICKER):
            self.assertEqual(self.types(term), {OUT_OF_INTENT}, term)
            for kind in (PROMOTE, SUGGEST_PHRASE, NEGATIVE):
                self.assertNotIn(term, unique_terms(self.opp["records"], kind), (term, kind))

    def test_travel_guide_and_map_are_eligible(self):
        for term in (self.GUIDE, self.MAP):
            self.assertNotIn(OUT_OF_INTENT, self.types(term), term)
        self.assertIn(self.GUIDE, unique_terms(self.opp["records"], PROMOTE) + unique_terms(self.opp["records"], SUGGEST_PHRASE))

    def test_nothing_out_of_intent_reaches_exact_auto_to_phrase_growth_or_new_exact(self):
        promoted = set(unique_terms(self.opp["records"], PROMOTE)) | set(unique_terms(self.opp["records"], SUGGEST_PHRASE))
        for term in (self.SHIRT, self.STICKER):
            self.assertNotIn(term, promoted)
        for r in self.opp["records"]:
            if r["opportunity_type"] in (PROMOTE, SUGGEST_PHRASE):
                self.assertIsNone(I.check(r["search_term"]), r["search_term"])
        grow = self.q("SELECT * FROM decisions WHERE book_id=?", tg.BOOK_A)
        self.assertTrue(grow)
        self.assertTrue(all(d["target"] not in (self.SHIRT, self.STICKER) for d in grow))

    def test_phrase_route_66_keeps_its_decision_and_the_campaign_identity(self):
        d = [r for r in self.q("SELECT * FROM decisions WHERE book_id=? ORDER BY run_id DESC", tg.BOOK_A) if r["target"] == "route 66"]
        self.assertTrue(d)
        self.assertEqual(d[0]["amazon_campaign_id"], tma.CID)
        auto = [r for r in self.q("SELECT * FROM decisions WHERE book_id=?", tg.BOOK_A) if r["campaign_type"] == "OTHER"]
        self.assertTrue(auto and all(r["amazon_campaign_id"] == "206528816350500" for r in auto))
        self.assertEqual(O.campaign_text(auto[0]["campaign_name"], auto[0]["amazon_campaign_id"]), "Campagna: AUTO- 7/9/2026 09:30:04.968 · ID 206528816350500")


@unittest.skipIf(AppTest is None, "streamlit non installato")
class CampaignTypeUiTests(tma.MA):
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

    def test_auto_is_preselected_ambiguous_is_distinct_and_the_stored_code_stays_other(self):
        auto = tma.acct_camp(tma.ACC_A, tma.NAME_A, "206528816350500", "AUTO- 7/9/2026 09:30:04.968", "111", "ROUTE 66")
        amb = tma.acct_camp(tma.ACC_A, tma.NAME_A, "777000111222333", "campagna senza indizi", "111", "ROUTE 66")
        phr = tma.acct_camp(tma.ACC_A, tma.NAME_A, "555000111222333", "FRASE R66", "111", "ROUTE 66")
        data = tg.csv_text(tg.TG_HEAD, [tg.tg(auto, "close-match", "TARGETING_EXPRESSION_PREDEFINED", "1002", 30, 2, 20.0, 60.0), tg.tg(amb, "x y", "", "1003", 5, 0, 3.0, 0.0),
                                        tg.tg(phr, "route 66", "PHRASE", "1001", 80, 7, 90.0, 280.0)])
        self.imp("TARGETING_30D", data, "t30.csv")
        kinds = {r["amazon_campaign_id"]: (r["detected_type"], r["detected_kind"]) for r in self.q("SELECT * FROM campaign_book_mapping")}
        self.assertEqual(kinds, {"206528816350500": ("OTHER", "AUTO"), "777000111222333": ("OTHER", "AMBIGUOUS"), "555000111222333": ("PHRASE", "PHRASE")})
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / "14_Campagne_da_associare.py"), default_timeout=120).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.selectbox(key=f"ca_t_{tma.ACC_A}_206528816350500").value, "AUTO")                          # AUTO preselezionata, non «OTHER»
        self.assertEqual(at.selectbox(key=f"ca_t_{tma.ACC_A}_777000111222333").value, "OTHER")                          # ambiguo: distinto da AUTO
        self.assertEqual(at.selectbox(key=f"ca_t_{tma.ACC_A}_555000111222333").value, "PHRASE")
        opts = [str(o) for o in at.selectbox(key=f"ca_t_{tma.ACC_A}_206528816350500").options]
        self.assertIn("AUTO (targeting automatico)", opts)
        self.assertIn("ALTRO / non determinato", opts)
        text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
        self.assertIn("Tipo non determinabile dai dati o dal nome", text)
        [b for b in at.button if b.key == f"ca_go_{tma.ACC_A}_206528816350500"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        m = self.q("SELECT campaign_type FROM campaign_book_mapping WHERE amazon_campaign_id='206528816350500'")[0]
        self.assertEqual(m["campaign_type"], "OTHER")                                                                     # codice storico invariato: nessuna migrazione


if __name__ == "__main__":
    unittest.main()
