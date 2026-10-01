"""Etichette ITALIANE dell'interfaccia (unica fonte). Solo presentazione: nel database e nella business logic restano i valori
tecnici originali (ENABLED, PAUSED, BID_DOWN_10_CANDIDATE, DELIVERY_COLLAPSED, ...). Nessuna dipendenza da Streamlit.

Regola: un valore sconosciuto viene mostrato cosi' com'e' (mai nascosto); None -> "n/d"."""
import re
from datetime import datetime

ND = "n/d"

# ------------------------------------------------------------------ testi generali
APP_TITLE = "KDP Ads AI"                                        # invariato
APP_SUBTITLE = "Dashboard di Supporto alle Decisioni"
READ_ONLY_TITLE = "MODALITÀ SOLA LETTURA"
READ_ONLY_TEXT = "Nessuna modifica alle campagne Amazon Ads viene eseguita automaticamente."
# voci del menu di navigazione (percorso relativo ad app.py, titolo mostrato); la prima e' la pagina principale
NAV_PAGES = [
    ("panoramica.py", "Panoramica"),
    ("pages/2_Tutte_le_decisioni.py", "Tutte le decisioni"),
    ("pages/3_Target_in_pausa.py", "Target in pausa"),
    ("pages/4_Campagne.py", "Campagne"),
    ("pages/5_Erogazione.py", "Erogazione"),
    ("pages/6_Confronto_tra_campagne.py", "Confronto tra campagne"),
    ("pages/7_Gestione_libri.py", "Gestione libri"),
    ("pages/8_Importa_report.py", "Importa report"),
]
SIDEBAR_SELECTION = "Selezione"
SIDEBAR_BOOK = "Libro"
SIDEBAR_MARKETPLACE = "Marketplace"
SIDEBAR_RUN = "Run decisionale"
SIDEBAR_READ_ONLY = "SOLA LETTURA · il database viene aperto in sola lettura."
LATEST_TAG = "PIÙ RECENTE"
ACTION_CENTER_TITLE = "CENTRO AZIONI"
ACTION_CENTER_DESCRIPTION = ("Qui trovi solo le decisioni che richiedono la tua attenzione manuale. "
                             "La dashboard non modifica automaticamente le campagne Amazon Ads.")
WARNINGS_TITLE = "AVVISI"
DATA_WARNINGS_TITLE = "AVVISI SUI DATI"
SEVERITY = "Gravità"
NO_DATA = "Nessun dato da mostrare."

# ------------------------------------------------------------------ dashboard V2 (gestione e importazione): scrivono SOLO file locali
OPS_NO_AMAZON = "Nessuna modifica viene eseguita su Amazon Ads."
OPS_BANNER_TEXT = ("Queste pagine scrivono solo nei file locali del progetto (libri, cartelle INPUT, database locale, OUTPUT, LOGS). "
                   "Non si collegano ad Amazon.")
OPS_BOOK_ADDED = "Libro aggiunto correttamente"
OPS_CAMPAIGN_ADDED = "Campagna aggiunta correttamente"
OPS_WEEKLY_COMPLETE = "Set report settimanale completo"
DRY_RUN_LABELS = {"DRY_RUN_OK": "DRY_RUN_OK · il report può essere importato", "SKIPPED_DUPLICATE": "SKIPPED_DUPLICATE · report già importato",
                  "SNAPSHOT_CONFLICT": "SNAPSHOT_CONFLICT · esiste già uno snapshot diverso per questo periodo", "ERROR": "ERROR · report non importabile"}
WEEKLY_STATUS_LABELS = {"PRESENTE": "presente", "OBSOLETO": "obsoleto (più vecchio di 7 giorni)", "MANCANTE": "mancante"}

# ------------------------------------------------------------------ azioni (final_action, underlying_candidate_action, ...)
ACTION_LABELS = {
    "NO_ACTION": "Nessuna azione",
    "HOLD": "Mantieni",
    "WATCH": "Monitora",
    "HOLD_PAUSED_TARGET": "Target in pausa",
    "REVIEW_DELIVERY_BEFORE_BID_CHANGE": "Verifica erogazione prima di cambiare il bid",
    "BID_DOWN_5_CANDIDATE": "Valuta riduzione bid −5%",
    "BID_DOWN_10_CANDIDATE": "Valuta riduzione bid −10%",
    "BID_DOWN_15_CANDIDATE": "Valuta riduzione bid −15%",
    "BID_UP_5_CANDIDATE": "Valuta aumento bid +5%",
    "REVIEW_TARGET_STATUS": "Verifica stato target",
    # valori che il motore puo' produrre ma non elencati nella richiesta (tradotti per coerenza)
    "PAUSE_CANDIDATE": "Valuta pausa del target",
    "NEGATIVE_CANDIDATE": "Valuta keyword negativa",
    "INSUFFICIENT_DATA": "Dati insufficienti",
    "BID_UP_5_OPPORTUNITY": "Opportunità di crescita (+5%)",
    "BID_UP_10_CANDIDATE": "Valuta aumento bid +10%",
}
STATUS_LABELS = {"ENABLED": "ATTIVO", "PAUSED": "IN PAUSA", "UNKNOWN": "STATO SCONOSCIUTO"}
CONFIDENCE_LABELS = {"HIGH": "ALTA", "MEDIUM": "MEDIA", "LOW": "BASSA"}
DELIVERY_LABELS = {
    "DELIVERY_COLLAPSED": "Erogazione crollata",
    "DELIVERY_DECLINING": "Erogazione in calo",
    "DELIVERY_STABLE": "Erogazione stabile",
    "DELIVERY_INCREASING": "Erogazione in crescita",
    "UNKNOWN": "Non disponibile",
}
DELIVERY_SOURCE_LABELS = {
    "TARGET": "Segnale del target",
    "CAMPAIGN_FALLBACK": "Segnale ereditato dalla campagna",
    "UNKNOWN": "Origine non disponibile",
}
PRIORITY_LABELS = {"P1": "P1 · evidenza forte", "P2": "P2 · attenzione", "P3": "P3 · da monitorare", "P4": "P4 · bassa priorità"}
AGREEMENT_LABELS = {"AGREE": "Concorda", "DISAGREE": "Non concorda", "PARTIAL": "Concorda in parte", "NOT_COMPARABLE": "Non confrontabile"}
WARNING_KIND_LABELS = {"DATA_WARNING": "AVVISO SUI DATI", "NOTA": "NOTA", "ALTRO": "ALTRO"}
REPORT_TYPE_LABELS = {"TARGETING": "Report Target", "SEARCH_TERM": "Report Termini di ricerca"}
FLAG_LABELS = {
    "ZERO_ORDER_WASTE": "Spesa senza ordini",
    "HIGH_VOLUME": "Alto volume",
    "HIGH_VOLUME_CAP": "Limite di sicurezza alto volume",
    "TARGET_STATUS_UNKNOWN": "Stato target sconosciuto",
    "TARGET_PAUSED": "Target in pausa",
    "TACTICAL_RECOVERED": "Periodo recente in recupero",
    "REVIEW_DELIVERY": "Verifica erogazione",
    "RECENT_DETERIORATION": "Peggioramento recente",
    "LOW_SAMPLE": "Campione ridotto",
    "LOW_SAMPLE_LOSS": "Perdita su campione ridotto",
    "LOSS_UNDER_LOW_SAMPLE": "Perdita con campione ridotto",
    "HIGH_CPC": "CPC elevato",
    "DELIVERY_INHERITED_COLLAPSED_NOT_ENFORCED": "Erogazione crollata ereditata (non applicata)",
    "DELIVERY_DECLINING_CAP": "Limite per erogazione in calo",
    "DATA_ANOMALY": "Anomalia nei dati",
    "ABSENT_IN_LATEST_SNAPSHOT": "Assente nell'ultimo periodo",
    "RECENT_SPEND_ON_PAUSED_TARGET": "Spesa recente su target in pausa",
}
COLUMN_LABELS = {
    "priority": "Priorità", "campaign_id": "Campagna", "campaign": "Campagna", "target": "Target", "target_status": "Stato target",
    "final_action": "Azione finale", "underlying_candidate_action": "Azione potenziale",
    "operationally_actionable": "Azionabile ora", "current_bid": "Bid attuale", "bid_change_pct": "Variazione bid",
    "confidence": "Affidabilità", "delivery_signal": "Erogazione", "delivery_source": "Origine del segnale",
    "lifetime_acos": "ACOS storico completo", "recent_acos": "ACOS recente", "lifetime_clicks": "Clic storico completo",
    "reason": "Perché", "flags": "Segnalazioni",
}


# ------------------------------------------------------------------ helper
def _get(mapping, value):
    if value is None or value == "":
        return ND
    return mapping.get(value, str(value))


def label_action(v):
    return _get(ACTION_LABELS, v)


def label_status(v):
    return _get(STATUS_LABELS, v)


def label_confidence(v):
    return _get(CONFIDENCE_LABELS, v)


def label_delivery(v):
    return _get(DELIVERY_LABELS, v)


def label_delivery_source(v):
    return _get(DELIVERY_SOURCE_LABELS, v)


def label_priority(v):
    return _get(PRIORITY_LABELS, v)


def label_agreement(v):
    return _get(AGREEMENT_LABELS, v)


def label_warning_kind(v):
    return _get(WARNING_KIND_LABELS, v)


def label_report_type(v):
    return _get(REPORT_TYPE_LABELS, v)


def label_flag(v):
    return _get(FLAG_LABELS, v)


def label_flags(flags, sep="; "):
    """Stringa 'A;B' salvata nel DB -> etichette italiane (stringa vuota se non ci sono flag)."""
    return sep.join(label_flag(f) for f in str(flags or "").split(";") if f)


def label_column(c):
    return COLUMN_LABELS.get(c, c)


def label_yesno(v):
    return ND if v is None else "SÌ" if v else "NO"


def label_window(v):
    """LIFETIME -> 'Storico completo'; RECENT_65D -> 'Ultimi 65 giorni' (qualunque numero di giorni)."""
    if v in (None, ""):
        return ND
    if v == "LIFETIME":
        return "Storico completo"
    m = re.fullmatch(r"RECENT_(\d+)D", str(v))
    if m:
        return f"Ultimi {m.group(1)} giorni"
    return "Non disponibile" if v == "UNKNOWN" else str(v)


def label_overlap_class(v):
    """BETTER_IN_<CAMPAGNA/TIPO> -> 'Meglio in <CAMPAGNA/TIPO>' (il nome resta com'e')."""
    if v in (None, ""):
        return ND
    if v == "SIMILAR_PERFORMANCE":
        return "Performance simile"
    if v == "INSUFFICIENT_DATA":
        return "Dati insufficienti"
    m = re.fullmatch(r"BETTER_IN_(.+)", str(v))
    return f"Meglio in {m.group(1)}" if m else str(v)


def label_origin(origin):
    """Provenienza di un avviso (stringa generata dal data access) -> testo italiano."""
    o = str(origin or "")
    m = re.fullmatch(r"analysis_run (\d+) \(non salvato nel decision run\)", o)
    if m:
        return f"Ereditato dal run di analisi #{m.group(1)} (non salvato nel run decisionale)"
    m = re.fullmatch(r"analysis_run (\d+)", o)
    if m:
        return f"Ereditato dal run di analisi #{m.group(1)}"
    if o == "decision":
        return "Run decisionale"
    if o.startswith("decision (legacy"):
        return "Run decisionale (storico, senza prefisso)"
    return o or ND


def label_datetime(v):
    """'2026-09-30T21:25:30' -> '30/09/2026 21:25:30'. Valori non interpretabili restano invariati."""
    if v in (None, ""):
        return ND
    try:
        return datetime.fromisoformat(str(v)).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return str(v)


def label_run(r, latest=False):
    """'Run decisionale #6 · Run di analisi #3 · data/ora · PIÙ RECENTE'."""
    aid = r.get("analysis_run_id")
    txt = f"Run decisionale #{r['run_id']} · Run di analisi #{aid if aid is not None else ND} · {label_datetime(r.get('generated_at'))}"
    return txt + (f" · {LATEST_TAG}" if latest else "")


def label_error(msg):
    """Messaggi di errore del data access: i nomi tecnici dei run sono mostrati in italiano."""
    s = str(msg or "")
    return re.sub(r"decision run", "run decisionale", re.sub(r"analysis run", "run di analisi", s, flags=re.I), flags=re.I)


# ---- testo 'reason' salvato dal decision layer: solo i CODICI tecnici noti vengono sostituiti nella visualizzazione
_TOKEN_MAP = {**ACTION_LABELS, **{k: v for k, v in DELIVERY_LABELS.items() if k != "UNKNOWN"}, **CONFIDENCE_LABELS}
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, _TOKEN_MAP), key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
_PART_PREFIX = {"Lifetime": "Storico completo", "Tactical": "Periodo tattico", "Recent": "Recente", "Delivery": "Erogazione",
                "Cross": "Tra campagne"}
_PART_RE = re.compile(r"(^|\| |\. )(Lifetime|Tactical|Recent|Delivery|Cross)(?=[ :])")


# frasi generate dal motore in formato fisso: sostituzioni STRETTE (richiedono il contesto) per non toccare nomi di keyword/ASIN
_PHRASES = [
    (re.compile(r"\bspend (?=(?:\$|£|€|CA\$|A\$)-?\d)"), "spesa "), (re.compile(r"\bsales (?=(?:\$|£|€|CA\$|A\$)-?\d)"), "vendite "), (re.compile(r"\bprofit (?=(?:\$|£|€|CA\$|A\$)-?\d)"), "profitto "),
    (re.compile(r"\bconf (?=(?:ALTA|MEDIA|BASSA)\b)"), "affidabilità "), (re.compile(r"\[rule "), "[regola "),
    (re.compile(r", prio (?=P\d)"), ", priorità "), (re.compile(r"ACOS N/A \(NO_SALES\)"), "ACOS n/d (nessuna vendita)"),
    (re.compile(r"\bSAFETY DELIVERY\b"), "SICUREZZA EROGAZIONE"), (re.compile(r"\bimpressions/giorno\b"), "impressioni/giorno"),
    (re.compile(r"\bclicks/giorno\b"), "clic/giorno"), (re.compile(r"\bBETTER_IN_([A-Za-z0-9_]+)"), r"Meglio in \1"),
    (re.compile(r"\bSIMILAR_PERFORMANCE\b"), "Performance simile"),
    (re.compile(r"\breduce (EXACT|PHRASE|COMPETITOR|[A-Z][A-Z0-9_]+) bid candidate"), r"candidato alla riduzione del bid in \1"),
    (re.compile(r"; keep ([A-Z][A-Z0-9_]+)"), r"; mantieni \1"),
    (re.compile(r"(?<=\[)WORSE(?=[\],])"), "PEGGIORE"), (re.compile(r"(?<=\[)BETTER(?=[\],])"), "MIGLIORE"),
    (re.compile(r"(?<=\[)OTHER(?=[\],])"), "ALTRA"),
]
_FLAGS_RE = re.compile(r"Flag: ([A-Z_]+(?:, [A-Z_]+)*)\.")


def label_reason(text):
    """Solo visualizzazione: il testo nel DB non cambia. Sostituisce codici azione/erogazione/affidabilita' con le etichette
    italiane e traduce i prefissi di sezione generati dal motore. Numeri e nomi (campagne, target, ASIN) restano invariati."""
    if text in (None, ""):
        return ND
    s = str(text)
    s = re.sub(r"^FINAL ", "DECISIONE FINALE: ", s)
    s = _PART_RE.sub(lambda m: m.group(1) + _PART_PREFIX[m.group(2)], s)
    s = _TOKEN_RE.sub(lambda m: _TOKEN_MAP[m.group(1)], s)
    for rx, repl in _PHRASES:
        s = rx.sub(repl, s)
    return _FLAGS_RE.sub(lambda m: "Segnalazioni: " + ", ".join(label_flag(f) for f in m.group(1).split(", ")) + ".", s)


def display_value(column, v):
    """Valore di una colonna decisione per le tabelle: codici tecnici -> etichette italiane; NULL -> n/d."""
    if v is None:
        return ND
    if column in ("final_action", "underlying_candidate_action", "latest_period_action", "growth_opportunity"):
        return label_action(v)
    if column == "target_status":
        return label_status(v)
    if column == "confidence":
        return label_confidence(v)
    if column == "delivery_signal":
        return label_delivery(v)
    if column == "delivery_source":
        return label_delivery_source(v)
    if column == "operationally_actionable":
        return label_yesno(v)
    if column == "flags":
        return label_flags(v) or ND
    if column == "reason":
        return label_reason(v)
    if column == "recommendation_agreement":
        return label_agreement(v)
    return v
