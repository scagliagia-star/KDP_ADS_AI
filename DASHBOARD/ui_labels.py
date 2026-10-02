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
    ("pages/9_Opportunita_Search_Term.py", "Opportunità Search Term"),
    ("pages/10_Lancio_nuovo_libro.py", "Lancio nuovo libro"),
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
TARGET_TYPE_LABELS = {
    "AUTO_TARGETING_GROUP": "Gruppo targeting automatico",
    "KEYWORD_TARGET": "Keyword",
    "PRODUCT_TARGET": "Target prodotto (ASIN/categoria)",
}
AUTO_GROUPS_NOTE = ("loose-match, close-match, complements e substitutes sono gruppi di targeting automatico di Amazon (campagna Auto), "
                    "non keyword manuali: i termini di ricerca che generano restano termini reali.")
PRIORITY_LABELS = {"P1":"P1 · evidenza forte", "P2": "P2 · attenzione", "P3": "P3 · da monitorare", "P4": "P4 · bassa priorità"}
AGREEMENT_LABELS = {"AGREE": "Concorda", "DISAGREE": "Non concorda", "PARTIAL": "Concorda in parte", "NOT_COMPARABLE": "Non confrontabile"}
WARNING_KIND_LABELS = {"DATA_WARNING": "AVVISO SUI DATI", "NOTA": "NOTA", "ALTRO": "ALTRO"}
REPORT_TYPE_LABELS = {"TARGETING": "Report Target", "SEARCH_TERM": "Report Termini di ricerca"}
# ------------------------------------------------------------------ Opportunita' Search Term (layer separato dalle decisioni di bid)
OPP_LABELS = {
    "PROMOTE_TO_EXACT": "Valuta aggiunta in EXACT",
    "NEGATIVE_CANDIDATE": "Valuta keyword negativa",
    "WATCH_SEARCH_TERM": "Monitora search term",
    "NO_ACTION": "Nessuna azione",
}
# ------------------------------------------------------------------ Lancio nuovo libro (bozza locale, nessuna rete)
LAUNCH_PAGE_TITLE = "Lancio nuovo libro"
LAUNCH_INTRO = ("Prepara la bozza di lancio di un nuovo libro: titolo, sottotitolo, descrizione, 7 keyword KDP, categorie, marketplace, prezzo, "
                "royalty e fino a 5 ASIN competitor scelti da te. Tutto resta nel file locale LAUNCH\\<bozza>.json: nessuna rete, nessuna modifica "
                "su Amazon, nessuna campagna creata.")
LAUNCH_COMPETITOR_SECTION = "COMPETITOR DI RIFERIMENTO"
LAUNCH_COMPETITOR_NOTE = ("Competitor fornito dall'utente (di solito scelto con Publisher Rocket), non scoperto automaticamente. "
                          "VALIDATO = controlli formali locali superati (formato ASIN, stesso marketplace, nessun duplicato): "
                          "l'esistenza su Amazon e l'adeguatezza come competitor non sono verificate.")
LAUNCH_SOURCE_LABELS = {"USER_COMPETITOR_ASIN": "Fornito dall'utente"}
LAUNCH_STRUCTURE_SECTION = "STRUTTURA CAMPAGNE CONSIGLIATA"
LAUNCH_KW_BUTTON = "GENERA PIANO KEYWORD"
LAUNCH_KW_INTRO = ("Piano keyword Ads iniziale ricavato SOLO dai testi della bozza (titolo, sottotitolo, descrizione, keyword KDP, categorie). "
                   "Non usa volumi di ricerca né dati esterni e non crea nulla su Amazon: sono proposte da valutare a mano. Il piano viene salvato nella bozza.")
LAUNCH_PROFILE_SECTION = "PROFILO DEL LIBRO"
LAUNCH_TITLE_QUERY_SECTION = "TITOLO PROPRIO / QUERY DEL TITOLO"
LAUNCH_TITLE_QUERY_NOTE = ("Query quasi identiche al titolo del nostro libro (BRANDED_SELF / TITLE_QUERY): non sono keyword commerciali generiche e non fanno "
                           "parte delle keyword di lancio principali. Valutale solo come campagna brand/difensiva.")
LAUNCH_SHOW_ALL = "Mostra tutte le keyword"
LAUNCH_SHOW_LIMIT_NOTE = "Mostro le prime {n} su {tot} per punteggio finale: attiva «Mostra tutte le keyword» per vederle tutte."
LAUNCH_SCORES_NOTE = ("Pertinenza = quanto la keyword copre i concetti commerciali del libro (sottotitolo, keyword KDP, categorie, descrizione); "
                      "Mercato = volume, competizione, vendite e conferme dai dati Publisher Rocket realmente presenti (i colori Green/Yellow/Red non contano); "
                      "Finale = la pertinenza viene prima e il mercato pesa fino al 30%, ma non può promuovere una keyword fuori tema.")
LAUNCH_EXACT_SECTION = "KEYWORD EXACT CONSIGLIATE"
LAUNCH_PHRASE_SECTION = "KEYWORD PHRASE CONSIGLIATE"
LAUNCH_DISCOVERY_SECTION = "DISCOVERY"
LAUNCH_REJECTED_SECTION = "SCARTATE / DA NON USARE"
LAUNCH_STALE_NOTE = "La bozza è cambiata dopo la generazione del piano keyword: rigeneralo per aggiornarlo."
LAUNCH_CONFIDENCE_LABELS = {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Bassa"}
LAUNCH_KW_SOURCE_LABELS = {"title": "titolo", "subtitle": "sottotitolo", "kdp_keyword": "keyword KDP", "category": "categoria",
                           "description": "descrizione", "competitor": "competitor", "publisher_rocket": "Publisher Rocket"}
LAUNCH_MATCH_LABELS = {"EXACT": "EXACT", "PHRASE": "PHRASE", "DISCOVERY": "DISCOVERY", "REJECT": "Scartata"}
LAUNCH_RESEARCH_SECTION = "RICERCA COMPETITOR / PUBLISHER ROCKET"
LAUNCH_RESEARCH_INTRO = ("Carica qui i file esportati da Publisher Rocket (CSV, XLSX o XLS). Il file resta sul tuo computer: viene letto in locale, nessuna rete, "
                         "nessuna modifica su Amazon, a books.json, al database Ads o alla cartella INPUT. Prima viene sempre fatto un dry run; solo dopo puoi "
                         "importarlo nella bozza (cartella LAUNCH/<bozza>/research/). Le colonne vengono riconosciute per nome: non serve un formato fisso.")
LAUNCH_RESEARCH_UPLOAD = "Carica file Publisher Rocket (anche più file insieme)"
LAUNCH_RESEARCH_DRY = "ANALIZZA TUTTI / DRY RUN"
LAUNCH_RESEARCH_IMPORT = "IMPORTA FILE VALIDI NELLA BOZZA"
LAUNCH_RESEARCH_BATCH_STATUS = {"DRY_RUN_OK": "VALIDO", "DUPLICATE_IN_BATCH": "DUPLICATO", "ALREADY_IMPORTED": "GIÀ IMPORTATO",
                                "UNRECOGNIZED": "NON RICONOSCIUTO", "ERROR": "ERRORE"}
LAUNCH_RESEARCH_BATCH_NOTE = ("Ogni file è analizzato separatamente: un file errato, duplicato o già importato resta escluso e non blocca gli altri. "
                              "Verranno importati solo i file con stato VALIDO.")
LAUNCH_RESEARCH_ENRICHED = "Competitor arricchito da Publisher Rocket"
LAUNCH_RESEARCH_CANDIDATE = "Competitor candidato"
LAUNCH_RESEARCH_CANDIDATE_NOTE = "ASIN presente nel file ma non tra i competitor della bozza: non viene aggiunto né validato automaticamente."
LAUNCH_RESEARCH_STATUS_LABELS = {"DRY_RUN_OK": "Dry run riuscito: il file può essere importato", "SKIPPED_DUPLICATE": "File già importato in questa bozza (stesso contenuto)",
                                 "UNRECOGNIZED": "File non riconosciuto: nessun import", "ERROR": "File non leggibile: nessun import"}
LAUNCH_RESEARCH_FIELD_LABELS = {
    "keyword": "Keyword", "asin": "ASIN", "title": "Titolo", "subtitle": "Sottotitolo", "author": "Autore", "price": "Prezzo", "avg_price": "Prezzo medio",
    "reviews": "Recensioni", "review_count": "Recensioni", "avg_reviews": "Recensioni medie", "rating": "Valutazione", "bsr": "BSR (classifica vendite)",
    "search_volume": "Ricerche stimate/mese", "search_volume_text": "Ricerche stimate/mese (testo)", "searches_color": "Colore ricerche",
    "competition": "Punteggio competitivo", "competition_color": "Colore competizione", "title_count": "Titoli concorrenti", "avg_pages": "Pagine medie",
    "pages": "Pagine", "avg_sales": "Vendite medie", "avg_monthly_earnings": "Guadagno mensile medio", "sales_month": "Vendite mensili stimate", "categories": "Categorie",
    "marketplace": "Marketplace"}
LAUNCH_COMPETITOR_CAMPAIGN = "Valuta campagna COMPETITOR"
LAUNCH_EVIDENCE_SECTION = "EVIDENZA DELLE KEYWORD"
LAUNCH_EVIDENCE_NOTE = ("Una keyword KDP ha più evidenza se compare in titolo, sottotitolo, altre keyword KDP e descrizione; se compare anche nei "
                        "competitor (dati pubblici) il livello sale di uno. Finché non è collegato un provider esterno non ci sono dati dei competitor: "
                        "nessun bonus e nessun dato inventato.")
LAUNCH_EVIDENCE_SOURCE_LABELS = {"title": "titolo", "subtitle": "sottotitolo", "kdp_keywords": "keyword KDP", "description": "descrizione"}
LAUNCH_LEVEL_LABELS = {"NONE": "Nessuna", "LOW": "Bassa", "MEDIUM": "Media", "HIGH": "Alta", "VERY_HIGH": "Molto alta"}
LAUNCH_PROVIDER_NOTE = "Arricchimento esterno (titolo, categorie, prezzo, recensioni dei competitor): non disponibile, nessun provider collegato."
OPP_PAGE_TITLE = "Opportunità Search Term"
OPP_INTRO = ("Suggerimenti manuali ricavati dai search term reali (query digitate dagli utenti) di PHRASE, Auto e altre campagne. "
             "Sono separati dalle decisioni di bid: nessuna campagna, keyword o negativa viene creata o modificata automaticamente.")
OPP_SECTION_PROMOTE = "A · DA PROMUOVERE IN EXACT"
OPP_SECTION_NEGATIVE = "B · CANDIDATI NEGATIVI"
OPP_SECTION_WATCH = "C · DA MONITORARE"
OPP_KPI_EXACT = "OPPORTUNITÀ EXACT"
OPP_KPI_NEGATIVE = "CANDIDATI NEGATIVI"
OPP_NEW_CAMPAIGN = "Valuta creazione campagna EXACT"
OPP_NEW_CAMPAIGN_TEXT = ("Ci sono almeno {n} search term unici da promuovere in EXACT provenienti da PHRASE/Auto e in questo marketplace non esiste "
                         "una campagna EXACT. Suggerimento strutturale, non operativo: nessuna campagna viene creata.")
OPP_AGREEMENT_LABELS = {"CONCORDI": "Finestre concordi", "DISCORDI": "Finestre discordanti", "SOLO_UNA_FINESTRA": "Una sola finestra",
                        "SOLO_65D": "Solo 65 giorni (30 giorni non importato)", "NON_CONFERMATO_30D": "Non confermato negli ultimi 30 giorni"}
OPP_FLAG_LABELS = {"ALREADY_IN_EXACT": "Già in EXACT", "EXACT_PAUSED": "Keyword EXACT in pausa", "DUPLICATE_ACROSS_CAMPAIGNS": "Presente in più campagne",
                   "MULTI_PARENT_TARGET": "Catturato da più target", "TERM_EQUALS_PARENT_TARGET": "Coincide con il target padre"}
OPP_NEGATIVE_TYPE_NOTE = "Il tipo di negativa (exact o phrase) non è deciso."
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
    "reason": "Perché", "flags": "Segnalazioni", "target_type": "Tipo di target",
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


def label_target_type(v):
    return _get(TARGET_TYPE_LABELS, v)


def label_launch_level(v):
    return _get(LAUNCH_LEVEL_LABELS, v)


def label_launch_confidence(v):
    return _get(LAUNCH_CONFIDENCE_LABELS, v)


def label_research_field(v):
    return LAUNCH_RESEARCH_FIELD_LABELS.get(v, v)


def label_match(v):
    return LAUNCH_MATCH_LABELS.get(v, v)


def label_kw_sources(sources):
    return ", ".join(LAUNCH_KW_SOURCE_LABELS.get(s, s) for s in sources) or "nessuna"


def label_launch_sources(sources):
    return ", ".join(LAUNCH_EVIDENCE_SOURCE_LABELS.get(s, s) for s in sources) or "nessuna"


def label_opportunity(v):
    return _get(OPP_LABELS, v)


def label_opp_agreement(v):
    return _get(OPP_AGREEMENT_LABELS, v)


def label_opp_flags(flags, sep="; "):
    return sep.join(OPP_FLAG_LABELS.get(f, f) for f in (flags or []))


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
    if column == "target_type":
        return label_target_type(v)
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
