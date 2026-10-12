"""Panoramica operativa ("cosa devo fare oggi su Amazon Ads?"): sezioni, KPI coerenti, terminologia Amazon Ads in italiano, codici tecnici solo in
PERCHÉ? > DETTAGLIO TECNICO, libro reale in sola lettura (copia del DB). Nessuna scrittura, nessuna rete, books.json / INPUT / DB invariati."""
import hashlib
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

import ui_labels as U  # noqa: E402
from data_access import overview as O  # noqa: E402
from data_access.repository import parse_delivery_detail  # noqa: E402

REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"
BOOK = {"target_acos": 35.0, "break_even_acos": 45.02, "currency": "USD"}
CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
TECH_WORDS = ("loose-match", "close-match", "substitutes", "complements", "ENABLED", "PAUSED", "KEYWORD_TARGET", "AUTO_TARGETING_GROUP", "UNKNOWN")
SUMMARY = {"actionable": 1}


def dec(i, **o):
    d = {"pk": i, "decision_id": f"D-{i:04d}", "campaign_id": "FRASE", "target": f"kw {i}", "target_type": "KEYWORD_TARGET", "target_status": "ENABLED",
         "final_action": "NO_ACTION", "priority": "P4", "operationally_actionable": 0, "current_bid": 1.0, "bid_change_pct": None, "confidence": "HIGH",
         "lifetime_clicks": 0.0, "lifetime_orders": 0.0, "lifetime_spend": 0.0, "lifetime_sales": 0.0, "lifetime_acos": None, "lifetime_estimated_profit": 0.0,
         "recent_window": None, "recent_clicks": None, "recent_orders": None, "recent_acos": None, "recent_period": None,
         "tactical_window": "RECENT_30D", "tactical_period": "2026-09-06__2026-10-05", "tactical_clicks": 5.0, "tactical_orders": 1.0, "tactical_acos": 30.0,
         "tactical_impressions_per_day": 100.0, "delivery_signal": "DELIVERY_STABLE", "delivery_source": "TARGET", "delivery_ratio": 1.0,
         "recent_vs_historical_signal": "STABLE_SIGNAL", "decision_rule": "C0_NO_DOWN", "flags": "", "reason": "FINAL NO_ACTION [rule C0_NO_DOWN]",
         "underlying_candidate_action": None}
    d.update(o)
    return d


def opp(term, otype="PROMOTE_TO_EXACT", **o):
    r = {"search_term": term, "campaign_id": "FRASE", "campaign_type": "PHRASE", "parent_target": "kw parent", "parent_targets": ["kw parent"],
         "parent_target_type": "KEYWORD_TARGET", "clicks": 20.0, "orders": 6.0, "spend": 30.0, "sales": 120.0, "acos": 25.0, "confidence": "HIGH",
         "already_in_exact": False, "already_targeted_elsewhere": False, "targeted_in": [], "opportunity_type": otype, "reason": "x"}
    r.update(o)
    return r


def build(decisions, opps=None, new_campaign=False, warnings=()):
    res = None if opps is None else {"records": opps, "new_exact_campaign": {"min_terms": 3} if new_campaign else None}
    return O.build_overview(decisions, BOOK, SUMMARY, list(warnings), res, parse_delivery_detail)


class BuilderTests(unittest.TestCase):
    def test_every_actionable_decision_is_in_what_to_do_today_sorted_by_priority(self):
        ds = [dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, operationally_actionable=1, priority="P3"),
              dec(2, final_action="BID_UP_5_CANDIDATE", bid_change_pct=5.0, operationally_actionable=1, priority="P1"),
              dec(3, final_action="PAUSE_CANDIDATE", operationally_actionable=1, priority="P2"), dec(4, operationally_actionable=0)]
        ov = build(ds)
        self.assertEqual([c["id"] for c in ov["actions"]], ["D-0002", "D-0003", "D-0001"])             # P1 -> P2 -> P3
        self.assertEqual({c["id"] for c in ov["actions"]}, {d["decision_id"] for d in ds if d["operationally_actionable"]})
        self.assertEqual(ov["kpi"]["actions"], 3)
        self.assertEqual([c["badge"] for c in ov["actions"]], ["ALZA OFFERTA", "METTI IN PAUSA", "ABBASSA OFFERTA"])

    def test_bid_down_shows_current_and_suggested_offer(self):
        c = build([dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, current_bid=1.00, operationally_actionable=1)])["actions"][0]
        self.assertEqual(c["headline"], "ABBASSA OFFERTA DEL 5%")
        self.assertEqual(c["bid"], {"current": 1.0, "suggested": 0.95, "change_pct": -5.0})

    def test_bid_up_shows_current_and_suggested_offer(self):
        c = build([dec(1, final_action="BID_UP_10_CANDIDATE", bid_change_pct=10.0, current_bid=0.70, operationally_actionable=1)])["actions"][0]
        self.assertEqual(c["headline"], "ALZA OFFERTA DEL 10%")
        self.assertEqual(c["bid"], {"current": 0.70, "suggested": 0.77, "change_pct": 10.0})

    def test_suggested_bid_rounding_and_missing_values(self):
        self.assertEqual(O.suggested_bid(1.19, -5), 1.13)
        self.assertEqual(O.suggested_bid(0.64, -15), 0.54)
        self.assertEqual(O.suggested_bid(0.333, 10), 0.37)
        self.assertIsNone(O.suggested_bid(None, 5))
        self.assertIsNone(O.suggested_bid(1.0, None))
        self.assertIsNone(O.bid_info(dec(1, current_bid=None, bid_change_pct=-5.0)))

    def test_user_labels_cover_the_requested_vocabulary(self):
        w = O.ACTION_WORDS
        self.assertEqual((w["BID_DOWN_10_CANDIDATE"], w["BID_UP_5_CANDIDATE"], w["PAUSE_CANDIDATE"], w["REACTIVATE_CANDIDATE"], w["PROMOTE_TO_EXACT"],
                          w["NEGATIVE_CANDIDATE"], w["REVIEW_DELIVERY_BEFORE_BID_CHANGE"], w["WATCH"], w["HOLD"], w["HOLD_PAUSED_TARGET"]),
                         ("ABBASSA OFFERTA", "ALZA OFFERTA", "METTI IN PAUSA", "RIATTIVA", "METTI IN EXACT", "VALUTA COME NEGATIVA", "VERIFICA EROGAZIONE",
                          "MONITORA", "NON TOCCARE", "GIÀ IN PAUSA"))

    def test_hold_becomes_do_not_touch_and_watch_becomes_monitor(self):
        ds = [dec(1, final_action="HOLD", lifetime_orders=20.0, lifetime_spend=100.0, lifetime_acos=30.0, lifetime_estimated_profit=40.0, tactical_acos=25.0),
              dec(2, final_action="WATCH", lifetime_clicks=40.0, lifetime_spend=40.0, lifetime_orders=3.0, lifetime_acos=50.0, tactical_acos=60.0)]
        ov = build(ds)
        self.assertEqual([c["badge"] for c in ov["good"]], ["NON TOCCARE"])
        self.assertEqual([c["badge"] for c in ov["monitor"]], ["MONITORA"])
        self.assertEqual(ov["good"][0]["reason"], "Sta funzionando bene.")

    def test_hold_with_recent_deterioration_is_monitored(self):
        weak = dec(1, final_action="HOLD", lifetime_orders=103.0, lifetime_spend=586.0, lifetime_acos=28.5, recent_vs_historical_signal="DECLINING_SIGNAL",
                   tactical_clicks=20.0, tactical_orders=3.0, tactical_acos=60.0)
        limited = dec(2, final_action="HOLD", lifetime_orders=5.0, lifetime_spend=28.0, lifetime_acos=27.8, tactical_acos=75.1, tactical_clicks=23.0, tactical_orders=1.0)
        ov = build([weak, limited])
        self.assertEqual([c["id"] for c in ov["monitor"]], ["D-0001", "D-0002"])                 # storico buono + recente valido SOPRA l'obiettivo
        self.assertEqual(ov["monitor"][0]["reason"], "Storicamente in linea con l'obiettivo (ACOS 28,5%), ma negli ultimi 30 giorni l'ACOS è 60,0%, "
                                                     "sopra l'obiettivo 35,0%: tienilo d'occhio.")
        self.assertEqual(ov["monitor"][1]["reason"], "Storicamente in linea con l'obiettivo (ACOS 27,8%), ma negli ultimi 30 giorni l'ACOS è 75,1%, "
                                                     "sopra l'obiettivo 35,0%: tienilo d'occhio.")
        self.assertFalse(ov["good"])

    def test_relevant_paused_target_is_shown_as_already_paused_with_underlying_note(self):
        p = dec(1, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED", lifetime_spend=108.0, lifetime_clicks=181.0, lifetime_acos=60.0,
                underlying_candidate_action="BID_DOWN_5_CANDIDATE")
        quiet = dec(2, final_action="HOLD_PAUSED_TARGET", target_status="PAUSED")
        ov = build([p, quiet])
        self.assertEqual([c["badge"] for c in ov["paused"]], ["GIÀ IN PAUSA"])
        self.assertIn("abbassa offerta", ov["paused"][0]["note"])
        self.assertEqual(ov["paused"][0]["reason"], "Era inefficiente ed è già in pausa.")                  # ACOS 60% > pareggio: dimostrabile
        self.assertEqual([r["id"] for r in ov["others"]], ["D-0002"])                                  # senza dati rilevanti: nell'elenco in fondo
        self.assertEqual(ov["kpi"]["paused"], 1)

    def test_conflicts_are_a_separate_section_and_kpi_only_when_present(self):
        recs = [opp("a"), opp("us national parks book", "REVIEW_NEGATIVE_CONFLICT", orders=2.0, acos=6.8, negative_match="EXACT"),
                opp("passport", "NO_ACTION", negative_match="EXACT")]
        ov = build([dec(1)], recs)
        self.assertEqual([i["term"] for i in ov["exact"]], ["a"])
        self.assertEqual(ov["kpi"]["exact"], 1)
        self.assertEqual(ov["kpi"]["negatives"], 0)                                                       # le gia' negative non sono candidati
        self.assertEqual(ov["kpi"]["conflicts"], len(ov["conflicts"]), 1)
        c = ov["conflicts"][0]
        self.assertEqual(c["badge"], "CONFLITTO DA VERIFICARE")
        self.assertEqual(c["reason"], "2 acquisti e ACOS 6,8%, ma il termine risulta già impostato come negativa esatta nella campagna FRASE.")
        self.assertEqual(c["hint"], "Verifica la negativa prima di qualsiasi promozione in EXACT.")
        self.assertEqual([i["term"] for i in ov["already_negative"]], ["passport"])
        self.assertNotIn("conflicts", build([dec(1)], [opp("a")])["kpi"])                               # nessun KPI se non ci sono conflitti

    def test_exact_and_negative_opportunities_are_listed_directly_with_simple_reasons(self):
        recs = [opp("national parks", orders=6.0, acos=25.7), opp("passport book", "NEGATIVE_CANDIDATE", clicks=18.0, orders=0.0, spend=14.5, sales=0.0, acos=None)]
        ov = build([dec(1)], recs, new_campaign=True)
        self.assertEqual([i["term"] for i in ov["exact"]], ["national parks"])
        self.assertEqual(ov["exact"][0]["badge"], "METTI IN EXACT")
        self.assertEqual(ov["exact"][0]["reason"], "6 acquisti, ACOS 25,7%, rendimento forte e non è ancora presente in EXACT.")
        self.assertEqual(ov["exact"][0]["origin"], "Frase")
        self.assertEqual([i["term"] for i in ov["negatives"]], ["passport book"])
        self.assertEqual(ov["negatives"][0]["badge"], "VALUTA COME NEGATIVA")
        self.assertEqual(ov["negatives"][0]["reason"], "18 clic, $14,50 di spesa e nessun acquisto.")
        self.assertTrue(ov["exact_new_campaign"])

    def test_exact_origin_auto_and_unique_terms(self):
        recs = [opp("t1", campaign_id="AUTO", campaign_type="OTHER", parent_target="loose-match", parent_targets=["loose-match"],
                    parent_target_type="AUTO_TARGETING_GROUP", confidence="MEDIUM"), opp("t1"), opp("t2")]
        ov = build([dec(1)], recs)
        self.assertEqual(sorted(i["term"] for i in ov["exact"]), ["t1", "t2"])                          # termini UNICI
        t1 = [i for i in ov["exact"] if i["term"] == "t1"][0]
        self.assertEqual(t1["origin"], "Frase")                                                          # il record migliore (affidabilita' ALTA, FRASE)
        auto = O.opportunity_items([opp("x", campaign_id="AUTO", campaign_type="OTHER", parent_target="close-match", parent_targets=["close-match"],
                                         parent_target_type="AUTO_TARGETING_GROUP")], "PROMOTE_TO_EXACT")[0]
        self.assertEqual((auto["origin"], auto["parent"]), ("Auto", "Corrispondenza stretta"))

    def test_more_than_ten_exact_shows_ten_and_the_kpi_counts_all_of_them(self):
        recs = [opp(f"term {i:02d}", orders=float(20 - i)) for i in range(14)]
        ov = build([dec(1)], recs)
        self.assertEqual(len(ov["exact_shown"]), 10)
        self.assertEqual(ov["kpi"]["exact"], 14)
        self.assertEqual(len(ov["exact"]), 14)                                                           # il KPI e' len() della stessa lista

    def test_opportunities_unavailable_gives_nd_not_zero(self):
        ov = build([dec(1)], None)
        self.assertIsNone(ov["kpi"]["exact"])
        self.assertIsNone(ov["kpi"]["negatives"])
        self.assertIsNone(ov["exact"])

    def test_delivery_classes_and_no_bid_up_suggestion(self):
        ds = [dec(1, tactical_impressions_per_day=0.0), dec(2, tactical_impressions_per_day=0.5), dec(3, tactical_impressions_per_day=100.0),
              dec(4, tactical_impressions_per_day=0.0, target_status="PAUSED", final_action="HOLD_PAUSED_TARGET"),
              dec(5, tactical_impressions_per_day=None, tactical_window=None)]
        ov = build(ds)
        self.assertEqual([(i["id"], i["badge"], i["impressions_30d"]) for i in ov["delivery"]],
                         [("D-0001", "NESSUNA EROGAZIONE", 0), ("D-0002", "EROGAZIONE MOLTO BASSA", 15)])
        self.assertEqual(ov["kpi"]["delivery"], 2)
        for i in ov["delivery"]:
            self.assertNotIn("alza", i["reason"].lower())                                                # mai "ALZA OFFERTA" solo perche' non eroga
        self.assertEqual(ov["delivery"][0]["reason"], "Il target è attivo ma non sta ricevendo traffico. Verifica offerta e pertinenza.")

    def test_strong_decline_is_flagged_only_with_enough_reference_and_target_source(self):
        ok = dec(1, delivery_signal="DELIVERY_COLLAPSED", delivery_ratio=0.2, reason="x impressions/giorno: recente 5 (a, 30g) vs riferimento 100 (b, 65g) = x0.2")
        weak_ref = dec(2, delivery_signal="DELIVERY_COLLAPSED", delivery_ratio=0.2, reason="x impressions/giorno: recente 1 (a, 30g) vs riferimento 5 (b, 65g) = x0.2")
        inherited = dec(3, delivery_signal="DELIVERY_COLLAPSED", delivery_ratio=0.2, delivery_source="CAMPAIGN_FALLBACK")
        ov = build([ok, weak_ref, inherited])
        self.assertEqual([(i["id"], i["badge"]) for i in ov["delivery"]], [("D-0001", "EROGAZIONE IN CALO")])

    def test_each_decision_appears_in_exactly_one_section(self):
        ds = [dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, operationally_actionable=1),
              dec(2, target_status="PAUSED", final_action="HOLD_PAUSED_TARGET", lifetime_spend=50.0), dec(3, tactical_impressions_per_day=0.0),
              dec(4, final_action="WATCH", lifetime_clicks=30.0), dec(5, final_action="HOLD", lifetime_orders=9.0), dec(6, final_action="HOLD")]
        ov = build(ds)
        ids = ov["all_ids"]
        self.assertEqual(sorted(ids), sorted(d["decision_id"] for d in ds))
        self.assertEqual(len(ids), len(set(ids)))

    def test_kpis_are_derived_from_the_rendered_collections(self):
        ds = [dec(1, final_action="BID_DOWN_5_CANDIDATE", bid_change_pct=-5.0, operationally_actionable=1), dec(2, target_status="PAUSED", lifetime_spend=40.0),
              dec(3, tactical_impressions_per_day=0.0), dec(4, tactical_impressions_per_day=0.0)]
        ov = build(ds, [opp("a"), opp("b"), opp("n", "NEGATIVE_CANDIDATE")], warnings=[{"text": "w"}])
        k = ov["kpi"]
        self.assertEqual((k["actions"], k["exact"], k["negatives"], k["paused"], k["warnings"], k["delivery"]),
                         (len(ov["actions"]), len(ov["exact"]), len(ov["negatives"]), len(ov["paused"]), len(ov["warnings"]), len(ov["delivery"])))
        self.assertEqual((k["actions"], k["exact"], k["negatives"], k["paused"], k["warnings"], k["delivery"]), (1, 2, 1, 1, 1, 2))

    def test_old_run_without_actionable_field_is_flagged(self):
        ov = O.build_overview([dec(1)], BOOK, {"actionable": None}, [], None, parse_delivery_detail)
        self.assertFalse(ov["has_actionable_field"])
        self.assertIsNone(ov["kpi"]["actions"])


class TerminologyTests(unittest.TestCase):
    def test_auto_group_names_are_italian_and_other_targets_unchanged(self):
        self.assertEqual([O.target_name(x) for x in ("loose-match", "close-match", "substitutes", "complements")],
                         ["Corrispondenza generica", "Corrispondenza stretta", "Sostituti", "Complementi"])
        self.assertEqual(O.target_name("national parks"), "national parks")
        self.assertEqual([U.display_target(x) for x in ("loose-match", "close-match", "substitutes", "complements")],
                         ["Corrispondenza generica", "Corrispondenza stretta", "Sostituti", "Complementi"])
        self.assertEqual(U.display_value("target", "complements"), "Complementi")

    def test_status_and_type_labels_are_amazon_ads_italian(self):
        self.assertEqual([U.label_status(x) for x in ("ENABLED", "PAUSED", "UNKNOWN")], ["Attivo", "In pausa", "Stato non disponibile"])
        self.assertEqual([U.label_target_type(x) for x in ("AUTO_TARGETING_GROUP", "KEYWORD_TARGET", "PRODUCT_TARGET")],
                         ["Gruppo di targeting automatico", "Parola chiave", "Target prodotto"])
        self.assertEqual(U.label_match_type("PHRASE"), "A frase")

    def test_technical_values_are_untouched_in_the_data(self):
        d = dec(1, target="loose-match", target_status="ENABLED", final_action="HOLD")
        before = dict(d)
        build([d])
        self.assertEqual(d, before)                                                                      # la presentazione non modifica i dati

    def test_strip_codes_and_plain_text(self):
        raw = "FINAL BID_DOWN_5_CANDIDATE (-5%) [rule C1_DOWN_CONFIRMED, conf HIGH]. Lifetime: x [HIGH_VOLUME_INEFFICIENT] — 35 ordini (TARGET:) bid-down"
        self.assertNotRegex(O.strip_codes(raw), CODE_RE)
        p = O.plain_text(raw)
        self.assertIn("35 acquisti", p)
        self.assertIn("riduzione dell'offerta", p)
        self.assertNotRegex(p, CODE_RE)


# ------------------------------------------------------------------ albero degli elementi della pagina (AppTest)
try:
    from streamlit.testing.v1 import AppTest
except Exception:                                                                              # pragma: no cover
    AppTest = None

PAGE = ROOT / "DASHBOARD" / "panoramica.py"


def walk(block, out, in_exp=False, tech=False):
    """Appiattisce l'albero: [(testo, in_expander, dopo_DETTAGLIO_TECNICO)]. Un expander e' un blocco figlio: i suoi elementi hanno in_expander=True."""
    state = {"tech": tech}
    for ch in block.children.values():
        t = type(ch).__name__
        if hasattr(ch, "children") and ch.children:
            sub_tech = False if t == "Expander" else state["tech"]
            if t == "Expander":
                out.append((f"[expander] {getattr(ch, 'label', '')}", in_exp, False))
            walk(ch, out, in_exp or t == "Expander", sub_tech)
            continue
        v = getattr(ch, "value", None)
        if t == "Metric":
            out.append((f"{ch.label}: {ch.value}", in_exp, state["tech"]))
        elif t == "Dataframe":
            df = ch.value
            out.append((" | ".join(map(str, df.columns)) + " || " + " | ".join(" ".join(map(str, r)) for r in df.values.tolist()), in_exp, state["tech"]))
        elif v is not None and t in ("Markdown", "Caption", "Header", "Subheader", "Title", "Success", "Info", "Warning", "Error", "Code", "Text"):
            if str(v).strip() == "**DETTAGLIO TECNICO**":
                state["tech"] = True
            out.append((str(v), in_exp, state["tech"]))


def page_text(at):
    out = []
    walk(at.main, out)
    return out


def select(at, label, contains):
    box = [s for s in at.sidebar.selectbox if s.label == label][0]
    opt = [o for o in box.options if contains in str(o)][0]
    box.set_value(opt)
    return at.run()


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealBookTests(unittest.TestCase):
    """Libro reale NATIONAL PARKS ROAD TRIP FOR BEGINNERS (B0H7P3C4D7, US, decision run #11): SOLA LETTURA su una COPIA del database."""

    @classmethod
    def setUpClass(cls):
        src = sqlite3.connect(f"file:{REAL_DB.resolve().as_uri().split('file:///')[1]}?mode=ro", uri=True) if False else sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        dst.close()
        src.close()
        has = sqlite3.connect(cls.db)
        cls.available = bool(has.execute("SELECT 1 FROM decision_runs WHERE run_id=11 AND book_id='B0H7P3C4D7'").fetchone())
        has.close()
        if not cls.available:
            return
        cls.old = os.environ.get("KDP_ADS_DB")
        os.environ["KDP_ADS_DB"] = str(cls.db)
        cls.hash_before = hashlib.sha256(cls.db.read_bytes()).hexdigest()
        at = AppTest.from_file(str(PAGE), default_timeout=120).run()
        select(at, "Libro", "B0H7P3C4D7")
        select(at, "Run decisionale", "#11")
        cls.at = at
        cls.items = page_text(at)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "old", None) is None:
            os.environ.pop("KDP_ADS_DB", None)
        else:
            os.environ["KDP_ADS_DB"] = cls.old
        cls.tmp.cleanup()

    def setUp(self):
        if not self.available:
            self.skipTest("libro reale B0H7P3C4D7 / run #11 non presente")

    def main_text(self):
        return "\n".join(t for t, in_exp, _ in self.items if not in_exp)

    def test_page_renders_without_errors_and_kpis_match_the_sections(self):
        self.assertFalse(self.at.exception)
        k = {m.label: m.value for m in self.at.metric if m.label in U.OV_KPI.values()}
        self.assertEqual(k, {"DA VALUTARE ORA": "1", "OPPORTUNITÀ EXACT": "3", "CANDIDATI NEGATIVI": "0", "TARGET IN PAUSA": "3", "AVVISI": "2",
                             "EROGAZIONE DA VERIFICARE": "8", "CONFLITTI DA VERIFICARE": "2"})
        text = self.main_text()
        heads = [h.value for h in self.at.header]
        for need in ("1 · KPI PRINCIPALI", "COSA FARE OGGI", "CREA CAMPAGNA EXACT CON QUESTE KEYWORD", "KEYWORD DA VALUTARE COME NEGATIVE", "EROGAZIONE DA VERIFICARE",
                     "TARGET IN PAUSA RILEVANTI", "DA MONITORARE", "STA ANDANDO BENE / NON TOCCARE", "CONFLITTI DA VERIFICARE"):
            self.assertIn(need, heads, need)
        self.assertTrue(any(h.startswith("AVVISI DATI") for h in heads))
        order = [heads.index(x) for x in ("1 · KPI PRINCIPALI", "COSA FARE OGGI", "CREA CAMPAGNA EXACT CON QUESTE KEYWORD", "KEYWORD DA VALUTARE COME NEGATIVE",
                                          "EROGAZIONE DA VERIFICARE", "TARGET IN PAUSA RILEVANTI", "DA MONITORARE", "STA ANDANDO BENE / NON TOCCARE")]
        self.assertEqual(order, sorted(order))                                                           # ordine richiesto
        self.assertEqual(len(re.findall(r"METTI IN EXACT", text)), 3)                                    # tanti elementi quanti il KPI
        self.assertEqual(len(re.findall(r"VALUTA COME NEGATIVA", text)), 0)
        self.assertEqual(len(re.findall(r"CONFLITTO DA VERIFICARE", text)), 2)
        self.assertEqual(len(re.findall(r"NESSUNA EROGAZIONE|EROGAZIONE MOLTO BASSA|EROGAZIONE IN CALO", text)), 8)
        self.assertEqual(len(re.findall(r"GIÀ IN PAUSA", text)), 3)

    def test_what_to_do_today_national_parks(self):
        text = self.main_text()
        self.assertIn("## ABBASSA OFFERTA DEL 5%", text)
        self.assertIn("**FRASE · national parks**", text)
        m = {m.label: m.value for m in self.at.metric}
        self.assertEqual((m["Offerta attuale"], m["Offerta suggerita"], m["Variazione"]), ("$1.00", "$0.95", "-5%"))
        self.assertIn("$1.00 → $0.95 (-5%)", text)
        self.assertIn("Sta spendendo troppo rispetto al tuo ACOS obiettivo", text)
        for lab, val in (("ACOS obiettivo", "35.0%"), ("ACOS di pareggio", "45.0%"), ("ACOS storico completo", "48.2%"), ("ACOS ultimi 65 giorni", "25.7%"),
                         ("ACOS ultimi 30 giorni", "48.2%"), ("Affidabilità", "ALTA")):
            self.assertIn(f"{lab}: {val}", text, lab)

    def test_exact_negative_delivery_paused_monitor_good_sections(self):
        text = self.main_text()
        for term in ("national parks", "national park map", "national park guide"):
            self.assertIn(f"**{term}**", text, term)
        exact_block = text.split("CREA CAMPAGNA EXACT CON QUESTE KEYWORD")[1].split("KEYWORD DA VALUTARE COME NEGATIVE")[0]
        self.assertNotIn("us national parks book", exact_block)                                          # in conflitto: mai in EXACT
        neg_block = text.split("KEYWORD DA VALUTARE COME NEGATIVE")[1].split("CONFLITTI DA VERIFICARE")[0]
        self.assertNotIn("national park passport book", neg_block)                                       # gia' negativa esatta
        self.assertIn("**us national parks book**", text)
        self.assertIn("2 acquisti e ACOS 6,8%, ma il termine risulta già impostato come negativa esatta nella campagna FRASE.", text)
        self.assertIn("Verifica la negativa prima di qualsiasi promozione in EXACT.", text)
        self.assertIn("GIÀ NEGATIVA (solo informazione, nessuna azione)", text)
        self.assertIn("6 acquisti, ACOS 25,7%, rendimento forte e non è ancora presente in EXACT.", text)
        for t in ("national parks drivers guide", "national park road trips", "national parks road trips for beginners", "national park road trip map"):
            self.assertIn(f"FRASE · {t}", text, t)
        self.assertIn("Il target è attivo ma non sta ricevendo traffico. Verifica offerta e pertinenza.", text)
        self.assertIn("AUTO · Complementi", text)
        self.assertIn("Era inefficiente ed è già in pausa.", text)                                      # passport book: ACOS 144,6%
        self.assertIn("È già in pausa. Nessuna azione necessaria.", text)                               # Complementi: 38,8% < pareggio, non dimostrabile
        self.assertNotIn("Era inefficiente, ma", text)
        self.assertIn("AUTO · Corrispondenza generica", text)
        self.assertIn("Storicamente in linea con l'obiettivo (ACOS 28,5%), ma negli ultimi 30 giorni l'ACOS è 42,7%, sopra l'obiettivo 35,0%: tienilo d'occhio.", text)
        self.assertIn("AUTO · Corrispondenza stretta", text)
        self.assertIn("Storicamente in linea con l'obiettivo (ACOS 27,8%), ma negli ultimi 30 giorni l'ACOS è 75,1%, sopra l'obiettivo 35,0%: tienilo d'occhio.", text)
        self.assertIn("FRASE · national parks book", text)
        self.assertIn("Sta funzionando bene.", text)

    def test_technical_codes_and_english_values_are_absent_from_the_main_view(self):
        main = [(t, e) for t, e, _ in self.items if not e]
        for t, _ in main:
            self.assertNotRegex(re.sub(r"</?span[^>]*>", "", t), CODE_RE, t[:80])
            for w in TECH_WORDS:
                self.assertNotIn(w, t, f"{w!r} in: {t[:80]}")
        before_tech = [t for t, e, tech in self.items if e and not tech]
        for t in before_tech:
            if t.startswith("[expander]"):
                continue
            self.assertNotRegex(t, CODE_RE, t[:100])                                                     # nemmeno in PERCHÉ?, prima del dettaglio tecnico
            for w in ("loose-match", "close-match", "substitutes", "complements"):
                self.assertNotIn(w, t)

    def test_technical_codes_are_in_the_technical_detail(self):
        tech = "\n".join(t for t, e, tech in self.items if tech)
        for need in ("C1_DOWN_CONFIRMED", "BID_DOWN_5_CANDIDATE", "KEYWORD_TARGET", "ENABLED", "HIGH_VOLUME_INEFFICIENT", "id decisione: D2026"):
            self.assertIn(need, tech, need)
        self.assertTrue(any(t.startswith("[expander] PERCHÉ?") for t, _, _ in self.items))

    def test_other_decisions_expander_is_closed_by_default_and_translated(self):
        exp = [e for e in self.at.expander if str(e.label).startswith("ALTRE DECISIONI")]
        self.assertEqual(len(exp), 1)
        self.assertFalse(exp[0].proto.expanded)
        self.assertFalse([e for e in self.at.expander if e.label == "PERCHÉ?" and e.proto.expanded])

    def test_nothing_was_written_and_no_network(self):
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("rete usata")), mock.patch("socket.create_connection", side_effect=AssertionError("rete usata")):
            at = AppTest.from_file(str(PAGE), default_timeout=120).run()
            select(at, "Libro", "B0H7P3C4D7")
            select(at, "Run decisionale", "#11")
        self.assertFalse(at.exception)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.hash_before)           # la lettura non scrive nel DB


@unittest.skipIf(AppTest is None, "streamlit non installato")
class ProjectFilesUntouchedTests(unittest.TestCase):
    def test_books_json_input_and_real_db_unchanged_by_rendering(self):
        files = [ROOT / "CONFIG" / "books.json", REAL_DB] + sorted((ROOT / "INPUT").rglob("*")) if (ROOT / "INPUT").exists() else [ROOT / "CONFIG" / "books.json", REAL_DB]
        snap = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}  # noqa: E731
        before = snap()
        if REAL_DB.is_file():
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(REAL_DB)
            try:
                at = AppTest.from_file(str(PAGE), default_timeout=120).run()
                self.assertFalse(at.exception)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        self.assertEqual(snap(), before)


# ------------------------------------------------------------------ pipeline di prova (nessun dato reale)
@unittest.skipIf(AppTest is None, "streamlit non installato")
class PipelineDbTests(unittest.TestCase):
    def test_page_on_a_pipeline_built_database(self):
        import test_dashboard as td

        class T(td.DashBase):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            t.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(t.db)
            try:
                before = hashlib.sha256(t.db.read_bytes()).hexdigest()
                at = AppTest.from_file(str(PAGE), default_timeout=120).run()
                self.assertFalse(at.exception)
                items = page_text(at)
                main = "\n".join(x for x, e, _ in items if not e)
                self.assertIn("COSA FARE OGGI", main)
                self.assertIn("ABBASSA OFFERTA", main)                                                     # C_EXACT con riduzione dell'offerta
                self.assertIn("Offerta suggerita", main)
                self.assertIn("GIÀ IN PAUSA", main)                                                        # C_PAUSED
                k = {m.label: m.value for m in at.metric if m.label in U.OV_KPI.values()}
                self.assertEqual(k["DA VALUTARE ORA"], str(len([1 for x in main.split("\n") if x.startswith("## ABBASSA") or x.startswith("## ALZA")])))
                for x, e, _ in items:
                    if not e:                                                                             # (C_EXACT / C_PAUSED sono nomi di campagna, non codici)
                        self.assertNotRegex(re.sub(r"C_EXACT|C_PAUSED", "", re.sub(r"</?span[^>]*>", "", x)), CODE_RE, x[:80])
                self.assertEqual(hashlib.sha256(t.db.read_bytes()).hexdigest(), before)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        finally:
            t.tearDown()


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class OtherPagesTerminologyTests(unittest.TestCase):
    """Terminologia Amazon Ads in italiano anche in Tutte le decisioni, Target in pausa, Campagne, Erogazione, Opportunità Search Term (libro reale, copia)."""
    PAGES = ("2_Tutte_le_decisioni", "3_Target_in_pausa", "4_Campagne", "5_Erogazione", "9_Opportunita_Search_Term")
    BAD = ("loose-match", "close-match", "substitutes", "complements", "ENABLED", "PAUSED", "KEYWORD_TARGET", "AUTO_TARGETING_GROUP", "Ordini", "Bid ", "Search term")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "k.db"
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        dst = sqlite3.connect(cls.db)
        src.backup(dst)
        ok = bool(dst.execute("SELECT 1 FROM decision_runs WHERE run_id=11 AND book_id='B0H7P3C4D7'").fetchone())
        dst.close()
        src.close()
        cls.available = ok
        cls.old = os.environ.get("KDP_ADS_DB")
        os.environ["KDP_ADS_DB"] = str(cls.db)

    @classmethod
    def tearDownClass(cls):
        if cls.old is None:
            os.environ.pop("KDP_ADS_DB", None)
        else:
            os.environ["KDP_ADS_DB"] = cls.old
        cls.tmp.cleanup()

    def render(self, page):
        if not self.available:
            self.skipTest("libro reale non presente")
        at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / f"{page}.py"), default_timeout=120).run()
        select(at, "Libro", "B0H7P3C4D7")
        select(at, "Run decisionale", "#11")
        self.assertFalse(at.exception, page)
        return "\n".join(t for t, _, _ in page_text(at))

    def test_no_english_technical_values_and_italian_terms_on_the_main_pages(self):
        for page in self.PAGES:
            txt = self.render(page)
            for w in self.BAD:
                self.assertNotIn(w, txt, f"{w!r} in {page}")

    def test_all_decisions_and_paused_pages_use_italian_names(self):
        allp = self.render("2_Tutte_le_decisioni")
        self.assertIn("Corrispondenza generica", allp)
        self.assertIn("Offerta attuale", allp)
        self.assertIn("Attivo", allp)
        self.assertIn("In pausa", allp)
        self.assertIn("Gruppo di targeting automatico", allp)
        self.assertIn("Parola chiave", allp)
        paused = self.render("3_Target_in_pausa")
        self.assertIn("Complementi", paused)
        deliv = self.render("5_Erogazione")
        self.assertIn("Corrispondenza stretta", deliv)

    def test_opportunity_page_uses_search_term_italian_vocabulary(self):
        txt = self.render("9_Opportunita_Search_Term")
        self.assertIn("Termine di ricerca", txt)
        self.assertIn("Acquisti", txt)
        self.assertIn("Valuta promozione in EXACT", txt)
        self.assertIn("Corrispondenza generica", txt)                                                    # target padre dei termini AUTO
        self.assertIn("Valuta come negativa", txt)


if __name__ == "__main__":
    unittest.main()
