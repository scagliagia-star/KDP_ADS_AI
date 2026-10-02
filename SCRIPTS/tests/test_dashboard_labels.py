"""Test della localizzazione italiana della dashboard: etichette centralizzate, valori tecnici invariati, nessun testo inglese residuo,
database invariato. Streamlit non e' richiesto: le pagine girano con un registratore al posto di `st`."""
import hashlib
import os
import runpy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import formatting as F
import test_dashboard as td
import ui_labels as U
from components import cards
from data_access.context import load_context

# Testi inglesi dell'interfaccia precedente: non devono piu' comparire in nessuna pagina
ENGLISH_UI = ["ACTION CENTER", "Decision run", "Analysis run", "Last update", "Selection", "Book price", "Royalty / copy", "WARNINGS",
              "PAUSED TARGETS", "TOTAL DECISIONS", "ACTIONABLE NOW", "REVIEW REQUIRED", "READ-ONLY", "No Amazon Ads changes",
              "Manual Decision Support", "Current bid", "Bid change", "Confidence", "Delivery source", "WHY?", "Flags",
              "Cross campaign evidence", "Amazon observation", "Recommendation agreement", "Underlying candidate", "DATA WARNINGS",
              "Inherited from", "Severity", "All Decisions", "Paused Targets", "Campaign Overview", "Search target", "Row details",
              "decisions", "Clicks", "Orders", "Spend", "Sales", "Estimated profit", "Target ACOS", "Break-even ACOS", "(latest)",
              "(historical)", "not actionable", "ACTIONABLE", "SAFETY DELIVERY", "BETTER_IN_", "[WORSE", "conf HIGH", "conf LOW", "conf MEDIUM"]


class Rec:
    """Registra tutte le chiamate di UI (nome, argomenti) al posto di streamlit."""

    def __init__(self, log, name="st"):
        self._log, self._name = log, name

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Rec(self._log, f"{self._name}.{n}")

    def __call__(self, *a, **k):
        self._log.append((self._name, a, k))
        base = self._name.split(".")[-1]
        if base == "columns":
            n = a[0]
            return [Rec(self._log, "col") for _ in range(n if isinstance(n, int) else len(n))]
        if base == "selectbox":
            return list(a[1])[0]
        if base == "text_input":
            return ""
        if base == "stop":
            raise SystemExit
        return Rec(self._log, self._name + "()")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getitem__(self, i):
        return Rec(self._log, self._name + "[]")

    # st.session_state e' un Rec: le pagine possono scrivervi, testare l'appartenenza e iterare (senza __iter__/__contains__ `in` non termina mai)
    def __setitem__(self, k, v):
        pass

    def __contains__(self, k):
        return False

    def __iter__(self):
        return iter(())


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from strings(x)


def render_all(db_path):
    """Esegue home e pagine con il registratore; ritorna {pagina: [testi mostrati]} (st.json escluso: dati grezzi)."""
    out, old_env, old_mod = {}, os.environ.get("KDP_ADS_DB"), sys.modules.get("streamlit")
    os.environ["KDP_ADS_DB"] = str(db_path)
    try:
        for page in [ROOT / "DASHBOARD" / "app.py", ROOT / "DASHBOARD" / "panoramica.py"] + sorted(
                p for p in (ROOT / "DASHBOARD" / "pages").glob("*.py") if not p.name.startswith(("7_", "8_"))):   # V2: test_dashboard_v2.py
            log = []
            sys.modules["streamlit"] = Rec(log)
            for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
                del sys.modules[m]
            try:
                runpy.run_path(str(page), run_name="__main__")
            except SystemExit:
                pass
            out[page.name] = [s for name, a, k in log if not name.endswith("json") for s in strings([a, k])]
    finally:
        if old_env is None:
            os.environ.pop("KDP_ADS_DB", None)
        else:
            os.environ["KDP_ADS_DB"] = old_env
        if old_mod is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = old_mod
    return out


class MappingTests(unittest.TestCase):
    def test_action_labels_as_requested(self):
        exp = {"NO_ACTION": "Nessuna azione", "HOLD": "Mantieni", "WATCH": "Monitora", "HOLD_PAUSED_TARGET": "Target in pausa",
               "REVIEW_DELIVERY_BEFORE_BID_CHANGE": "Verifica erogazione prima di cambiare il bid",
               "BID_DOWN_5_CANDIDATE": "Valuta riduzione bid −5%", "BID_DOWN_10_CANDIDATE": "Valuta riduzione bid −10%",
               "BID_DOWN_15_CANDIDATE": "Valuta riduzione bid −15%", "BID_UP_5_CANDIDATE": "Valuta aumento bid +5%",
               "REVIEW_TARGET_STATUS": "Verifica stato target"}
        for k, v in exp.items():
            self.assertEqual(U.label_action(k), v, k)

    def test_status_confidence_delivery_labels(self):
        self.assertEqual([U.label_status(x) for x in ("ENABLED", "PAUSED", "UNKNOWN")], ["ATTIVO", "IN PAUSA", "STATO SCONOSCIUTO"])
        self.assertEqual([U.label_confidence(x) for x in ("HIGH", "MEDIUM", "LOW")], ["ALTA", "MEDIA", "BASSA"])
        self.assertEqual([U.label_delivery(x) for x in ("DELIVERY_COLLAPSED", "DELIVERY_DECLINING", "DELIVERY_STABLE",
                                                         "DELIVERY_INCREASING", "UNKNOWN")],
                         ["Erogazione crollata", "Erogazione in calo", "Erogazione stabile", "Erogazione in crescita", "Non disponibile"])
        self.assertEqual([U.label_delivery_source(x) for x in ("TARGET", "CAMPAIGN_FALLBACK", "UNKNOWN")],
                         ["Segnale del target", "Segnale ereditato dalla campagna", "Origine non disponibile"])

    def test_unknown_values_pass_through_and_none_is_nd(self):
        self.assertEqual(U.label_action("QUALCOSA_DI_NUOVO"), "QUALCOSA_DI_NUOVO")      # mai nascosto
        self.assertEqual((U.label_action(None), U.label_status(None), U.label_confidence("")), ("n/d", "n/d", "n/d"))
        self.assertEqual(U.label_flag("FLAG_FUTURA"), "FLAG_FUTURA")

    def test_every_flag_emitted_by_the_decision_layer_is_translated(self):
        import re
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "decision.py").read_text(encoding="utf-8")
        flags = set(re.findall(r'flags\.append\("([A-Z_]+)"\)', src))
        self.assertTrue(flags)
        self.assertEqual(sorted(f for f in flags if f not in U.FLAG_LABELS), [])

    def test_every_action_the_decision_layer_can_emit_is_translated(self):
        import re
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "decision.py").read_text(encoding="utf-8")
        actions = set(re.findall(r'"((?:BID_DOWN_\d+|BID_UP_\d+)_CANDIDATE|PAUSE_CANDIDATE|NEGATIVE_CANDIDATE|HOLD_PAUSED_TARGET|'
                                 r'REVIEW_[A-Z_]+|NO_ACTION|WATCH|HOLD|INSUFFICIENT_DATA)"', src))
        self.assertEqual(sorted(a for a in actions if a not in U.ACTION_LABELS and a not in U.FLAG_LABELS), [])   # REVIEW_DELIVERY e' un flag

    def test_window_run_origin_datetime_labels(self):
        self.assertEqual([U.label_window(x) for x in ("LIFETIME", "RECENT_65D", "RECENT_28D", "RECENT_14D", None)],
                         ["Storico completo", "Ultimi 65 giorni", "Ultimi 28 giorni", "Ultimi 14 giorni", "n/d"])
        self.assertEqual(U.label_run({"run_id": 6, "analysis_run_id": 3, "generated_at": "2026-09-30T21:25:30"}, True),
                         "Run decisionale #6 · Run di analisi #3 · 30/09/2026 21:25:30 · PIÙ RECENTE")
        self.assertEqual(U.label_run({"run_id": 5, "analysis_run_id": 3, "generated_at": "2026-09-30T21:04:53"}),
                         "Run decisionale #5 · Run di analisi #3 · 30/09/2026 21:04:53")
        self.assertEqual(U.label_origin("analysis_run 3 (non salvato nel decision run)"),
                         "Ereditato dal run di analisi #3 (non salvato nel run decisionale)")
        self.assertEqual(U.label_origin("analysis_run 3"), "Ereditato dal run di analisi #3")
        self.assertEqual(U.label_datetime("non-una-data"), "non-una-data")
        self.assertEqual(U.label_overlap_class("BETTER_IN_PHRASE"), "Meglio in PHRASE")
        self.assertEqual(U.label_error("Nessun decision run per questo libro."), "Nessun run decisionale per questo libro.")

    def test_reason_translation_changes_only_known_codes(self):
        raw = ("FINAL BID_DOWN_10_CANDIDATE (-10%) [rule C1_DOWN_CONFIRMED, conf HIGH, prio P1]. Lifetime: BID_DOWN_10_CANDIDATE [HIGH_VOLUME_INEFFICIENT] "
               "— 1761 clic, 213 ordini, spend $2040.74 | Delivery: DELIVERY_COLLAPSED (TARGET:SEARCH_TERM) | Cross: nessun overlap")
        out = U.label_reason(raw)
        self.assertIn("DECISIONE FINALE: Valuta riduzione bid −10% (-10%)", out)
        self.assertIn("affidabilità ALTA", out)
        self.assertIn("Storico completo: Valuta riduzione bid −10%", out)
        self.assertIn("Erogazione: Erogazione crollata (TARGET:SEARCH_TERM)", out)
        self.assertIn("HIGH_VOLUME_INEFFICIENT", out)                 # codici di classificazione: invariati
        self.assertIn("C1_DOWN_CONFIRMED", out)
        self.assertIn("1761 clic, 213 ordini, spesa $2040.74", out)   # numeri invariati
        self.assertEqual(U.label_reason(None), "n/d")

    def test_reason_translation_phrases_are_tight(self):
        raw = ("FINAL REVIEW_DELIVERY_BEFORE_BID_CHANGE [rule C2_LIFETIME_DOWN, conf HIGH, prio P1]. Lifetime: HOLD [ON_TARGET_HOLD] — spend $10.00, "
               "sales $50.00, ACOS N/A (NO_SALES), profit $-4.08, conf LOW | Delivery: DELIVERY_COLLAPSED (TARGET:SEARCH_TERM) impressions/giorno: recente 1 "
               "| Cross: kw [2026-07-28__2026-09-30]: BETTER_IN_PHRASE [WORSE, POSSIBILE INEFFICIENZA] | Logica: x SAFETY DELIVERY: y Flag: HIGH_VOLUME, REVIEW_DELIVERY.")
        out = U.label_reason(raw)
        for good in ("[regola C2_LIFETIME_DOWN, affidabilità ALTA, priorità P1]", "spesa $10.00", "vendite $50.00", "profitto $-4.08",
                     "ACOS n/d (nessuna vendita)", "impressioni/giorno", "Meglio in PHRASE [PEGGIORE, POSSIBILE INEFFICIENZA]",
                     "SICUREZZA EROGAZIONE", "Segnalazioni: Alto volume, Verifica erogazione."):
            self.assertIn(good, out)
        # nomi di keyword/ASIN con parole inglesi NON vengono alterati
        kw = "Cross: sales funnel spend plan [2026-07-28__2026-09-30]: SIMILAR_PERFORMANCE [OTHER] | asin B0FTFJL6RB"
        self.assertIn("sales funnel spend plan", U.label_reason(kw))
        self.assertIn("Performance simile [ALTRA]", U.label_reason(kw))
        self.assertIn("B0FTFJL6RB", U.label_reason(kw))
        rec = "reduce EXACT bid candidate (BID_DOWN_5_CANDIDATE sul target che cattura il termine: chair tai chi); keep PHRASE"
        out = U.label_reason(rec)
        self.assertIn("candidato alla riduzione del bid in EXACT", out)
        self.assertIn("; mantieni PHRASE", out)
        self.assertIn("(Valuta riduzione bid −5% sul target che cattura il termine: chair tai chi)", out)
        self.assertIn("affidabilità ALTA", U.label_reason("EXACT ACOS 53.1% (254 clic, spesa $241.81, conf HIGH) vs PHRASE"))

    def test_acos_without_sales_is_not_zero(self):
        self.assertEqual(F.acos(None), "n/d (nessuna vendita)")
        self.assertEqual(F.acos(0.0), "0.0%")                          # lo zero vero resta zero
        self.assertEqual(F.yesno(True), "SÌ")

    def test_central_texts_requested(self):
        self.assertEqual(U.APP_TITLE, "KDP Ads AI")
        self.assertEqual(U.APP_SUBTITLE, "Dashboard di Supporto alle Decisioni")
        self.assertEqual(U.READ_ONLY_TITLE, "MODALITÀ SOLA LETTURA")
        self.assertEqual(U.READ_ONLY_TEXT, "Nessuna modifica alle campagne Amazon Ads viene eseguita automaticamente.")
        self.assertEqual(U.ACTION_CENTER_DESCRIPTION, "Qui trovi solo le decisioni che richiedono la tua attenzione manuale. "
                                                      "La dashboard non modifica automaticamente le campagne Amazon Ads.")
        self.assertEqual((U.ACTION_CENTER_TITLE, U.WARNINGS_TITLE, U.DATA_WARNINGS_TITLE), ("CENTRO AZIONI", "AVVISI", "AVVISI SUI DATI"))


class RenderTests(td.DashBase):
    def test_technical_values_untouched_and_tables_localised(self):
        ctx = load_context(self.repo)
        before = [dict(d) for d in ctx.decisions]
        rows = cards.display_rows(ctx.decisions, ["priority", "campaign_id", "final_action", "target_status", "confidence",
                                                  "operationally_actionable", "delivery_source", "flags"])
        self.assertEqual(ctx.decisions, before)                                         # nessuna mutazione dei dati
        self.assertIn("Azione finale", rows[0])
        self.assertIn("Target in pausa", {r["Azione finale"] for r in rows})            # HOLD_PAUSED_TARGET
        self.assertTrue({r["Stato target"] for r in rows} <= {"ATTIVO", "IN PAUSA", "STATO SCONOSCIUTO", "n/d"})
        self.assertTrue({d["final_action"] for d in ctx.decisions} & {"HOLD_PAUSED_TARGET", "BID_DOWN_10_CANDIDATE"})
        self.assertEqual({d["target_status"] for d in ctx.decisions}, {"ENABLED", "PAUSED"})

    def test_all_pages_in_italian_and_database_untouched(self):
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        shown = render_all(self.db)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        allp = "\n".join("\n".join(v) for v in shown.values())
        for need in ("MODALITÀ SOLA LETTURA", "Nessuna modifica alle campagne Amazon Ads viene eseguita automaticamente.",
                     "Dashboard di Supporto alle Decisioni", "CENTRO AZIONI", "AVVISI", "DECISIONI TOTALI", "DA VALUTARE ORA",
                     "REVISIONE RICHIESTA", "TARGET IN PAUSA", "Qui trovi solo le decisioni che richiedono la tua attenzione manuale.",
                     "ACOS obiettivo", "ACOS di pareggio", "Royalty per copia", "Prezzo libro", "Ultimo aggiornamento",
                     "Run decisionale", "Run di analisi", "PERCHÉ?", "Affidabilità", "Bid attuale", "Variazione bid",
                     "AVVISI SUI DATI", "Tutte le decisioni", "Target in pausa", "Panoramica campagne", "Erogazione", "(Delivery)",
                     "Confronto tra campagne", "Ereditato dal run di analisi", "Gravità: n/d", "Libro", "Selezione"):
            self.assertIn(need, allp, need)
        for bad in ENGLISH_UI:
            self.assertNotIn(bad, allp, f"testo inglese residuo: {bad!r}")
        self.assertIn("Valuta riduzione bid", allp)

    def test_menu_home_is_panoramica_not_app(self):
        log, old = [], sys.modules.get("streamlit")
        sys.modules["streamlit"] = Rec(log)
        try:
            runpy.run_path(str(ROOT / "DASHBOARD" / "app.py"), run_name="__main__")
        finally:
            if old is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = old
        pages = [(a[0], k) for name, a, k in log if name == "st.Page"]
        self.assertEqual(pages[0], ("panoramica.py", {"title": "Panoramica", "default": True}))
        self.assertEqual([t["title"] for _, t in pages], ["Panoramica", "Tutte le decisioni", "Target in pausa", "Campagne",
                                                          "Erogazione", "Confronto tra campagne", "Opportunità Search Term", "Lancio nuovo libro", "Gestione libri", "Importa report"])
        self.assertEqual(sum(1 for _, t in pages if t["default"]), 1)
        for path, _ in pages:                                                        # ogni voce punta a un file esistente
            self.assertTrue((ROOT / "DASHBOARD" / path).is_file(), path)
        self.assertTrue(any(name.endswith("navigation") for name, _, _ in log))
        self.assertIn("streamlit run DASHBOARD", (ROOT / "DASHBOARD" / "app.py").read_text(encoding="utf-8"))   # avvio invariato

    def test_browser_title_home_is_not_duplicated(self):
        titles = {}
        old_env, old = os.environ.get("KDP_ADS_DB"), sys.modules.get("streamlit")
        os.environ["KDP_ADS_DB"] = str(self.db)
        try:
            for f in ("panoramica.py", "pages/2_Tutte_le_decisioni.py"):
                log = []
                sys.modules["streamlit"] = Rec(log)
                for m in [m for m in sys.modules if m == "components" or m.startswith("components.")]:
                    del sys.modules[m]
                try:
                    runpy.run_path(str(ROOT / "DASHBOARD" / f), run_name="__main__")
                except SystemExit:
                    pass
                titles[f] = [k["page_title"] for name, a, k in log if name.endswith("set_page_config")]
        finally:
            if old_env is None:
                os.environ.pop("KDP_ADS_DB", None)
            else:
                os.environ["KDP_ADS_DB"] = old_env
            if old is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = old
        self.assertEqual(titles["panoramica.py"], ["KDP Ads AI"])
        self.assertEqual(titles["pages/2_Tutte_le_decisioni.py"], ["KDP Ads AI · Tutte le decisioni"])

    def test_dashboard_sources_compile_without_syntax_warnings(self):
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            for p in (ROOT / "DASHBOARD").rglob("*.py"):
                compile(p.read_text(encoding="utf-8"), str(p), "exec")
        self.assertEqual([str(x.message) for x in w], [])

    def test_page_files_have_italian_names(self):
        names = sorted(p.name for p in (ROOT / "DASHBOARD" / "pages").glob("*.py"))
        self.assertEqual(names, ["10_Lancio_nuovo_libro.py", "2_Tutte_le_decisioni.py", "3_Target_in_pausa.py", "4_Campagne.py", "5_Erogazione.py",
                                 "6_Confronto_tra_campagne.py", "7_Gestione_libri.py", "8_Importa_report.py", "9_Opportunita_Search_Term.py"])


if __name__ == "__main__":
    unittest.main()
