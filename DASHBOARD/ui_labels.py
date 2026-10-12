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
    ("pages/11_Suggerimenti_di_crescita.py", "Suggerimenti di crescita"),
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
# ------------------------------------------------------------------ Panoramica operativa ("cosa devo fare oggi su Amazon Ads?")
OV_INTRO = "Cosa devo fare oggi su Amazon Ads? Tutto ciò che serve per decidere è qui sotto. La dashboard non modifica nulla su Amazon Ads: le azioni restano manuali."
OV_KPI_TITLE = "1 · KPI PRINCIPALI"
OV_TODAY = "COSA FARE OGGI"
OV_EXACT = "KEYWORD DA METTERE IN EXACT"
OV_EXACT_NEW = "CREA CAMPAGNA EXACT CON QUESTE KEYWORD"
OV_NEGATIVE = "KEYWORD DA VALUTARE COME NEGATIVE"
OV_CONFLICTS = "CONFLITTI DA VERIFICARE"
GROW_LOOK_ALSO = "GUARDA ANCHE"
GROW_NONE = "Al momento non ci sono suggerimenti di crescita affidabili."
GROW_SEE = "Vedi suggerimenti"
GROW_PAGE_TITLE = "Suggerimenti di crescita"
GROW_INTRO = ("Opportunità di crescita ricavate solo dai dati locali (termini di ricerca, target, campagne, ASIN osservati). Sono separate dalle azioni difensive "
              "(non contano in «Da valutare ora») e restano suggerimenti manuali: la dashboard non modifica nulla su Amazon Ads.")
GROW_SCALE = "1 · SCALA TARGET VINCENTI"
GROW_AUTO_PHRASE = "2 · AUTO → FRASE"
GROW_TO_EXACT = "3 · FRASE → EXACT"
GROW_DEVELOP = "4 · SVILUPPA CAMPAGNE"
GROW_NEW_PRODUCT = "5 · NUOVI TARGET PRODOTTO"
GROW_EMPTY = {"SCALE": "Nessun target candidato allo scaling con i gate prudenti attuali.",
              "AUTO_TO_PHRASE": "Nessun termine AUTO promettente da validare in FRASE.",
              "TO_EXACT": "Nessuna keyword da mettere in EXACT.",
              "DEVELOP": "Nessuna campagna con copertura insufficiente.",
              "NEW_PRODUCT": "Nessun nuovo target prodotto supportato dai dati."}
OV_PROMOTED = "KEYWORD GIÀ PROMOSSE IN EXACT"
OV_PRODUCTS = "TARGET PRODOTTO VINCENTE"
OV_DELIVERY = "EROGAZIONE DA VERIFICARE"
OV_PAUSED = "TARGET IN PAUSA RILEVANTI"
OV_MONITOR = "DA MONITORARE"
OV_GOOD = "STA ANDANDO BENE / NON TOCCARE"
OV_WARNINGS = "AVVISI DATI"
OV_OTHERS = "ALTRE DECISIONI · HOLD / WATCH"
OV_WHY = "PERCHÉ?"
OV_TECH = "DETTAGLIO TECNICO"
OV_SEE_ALL_OPP = "Mostra tutte in Opportunità Search Term"
OV_KPI = {"actions": "DA VALUTARE ORA", "exact": "OPPORTUNITÀ EXACT", "negatives": "CANDIDATI NEGATIVI", "paused": "TARGET IN PAUSA",
          "warnings": "AVVISI", "delivery": "EROGAZIONE DA VERIFICARE", "conflicts": "CONFLITTI DA VERIFICARE"}
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
    "REVIEW_DELIVERY_BEFORE_BID_CHANGE": "Verifica erogazione prima di cambiare l'offerta",
    "BID_DOWN_5_CANDIDATE": "Valuta riduzione offerta −5%",
    "BID_DOWN_10_CANDIDATE": "Valuta riduzione offerta −10%",
    "BID_DOWN_15_CANDIDATE": "Valuta riduzione offerta −15%",
    "BID_UP_5_CANDIDATE": "Valuta aumento offerta +5%",
    "REVIEW_TARGET_STATUS": "Verifica stato target",
    # valori che il motore puo' produrre ma non elencati nella richiesta (tradotti per coerenza)
    "PAUSE_CANDIDATE": "Valuta pausa del target",
    "NEGATIVE_CANDIDATE": "Valuta come negativa",
    "INSUFFICIENT_DATA": "Dati insufficienti",
    "BID_UP_5_OPPORTUNITY": "Opportunità di crescita (+5%)",
    "BID_UP_10_CANDIDATE": "Valuta aumento offerta +10%",
}
STATUS_LABELS = {"ENABLED": "Attivo", "PAUSED": "In pausa", "UNKNOWN": "Stato non disponibile"}
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
    "AUTO_TARGETING_GROUP": "Gruppo di targeting automatico",
    "KEYWORD_TARGET": "Parola chiave",
    "PRODUCT_TARGET": "Target prodotto",
}
# nomi dei gruppi di targeting automatico come nel pannello Amazon Ads in italiano (i valori tecnici restano nel DB)
AUTO_GROUP_NAMES = {"loose-match": "Corrispondenza generica", "close-match": "Corrispondenza stretta", "substitutes": "Sostituti", "complements": "Complementi"}
MATCH_TYPE_LABELS = {"EXACT": "Exact", "PHRASE": "A frase", "BROAD": "Generica", "A frase": "A frase", "Exact": "Exact", "Generica": "Generica"}
AUTO_GROUPS_NOTE = ("Corrispondenza generica, Corrispondenza stretta, Sostituti e Complementi sono gruppi di targeting automatico di Amazon (campagna Auto), "
                    "non parole chiave manuali: i termini di ricerca che generano restano termini reali.")
PRIORITY_LABELS = {"P1":"P1 · evidenza forte", "P2": "P2 · attenzione", "P3": "P3 · da monitorare", "P4": "P4 · bassa priorità"}
AGREEMENT_LABELS = {"AGREE": "Concorda", "DISAGREE": "Non concorda", "PARTIAL": "Concorda in parte", "NOT_COMPARABLE": "Non confrontabile"}
WARNING_KIND_LABELS = {"DATA_WARNING": "AVVISO SUI DATI", "NOTA": "NOTA", "ALTRO": "ALTRO"}
REPORT_TYPE_LABELS = {"TARGETING": "Report Target", "SEARCH_TERM": "Report Termini di ricerca"}
# ------------------------------------------------------------------ Opportunita' Search Term (layer separato dalle decisioni sulle offerte)
OPP_LABELS = {
    "PROMOTE_TO_EXACT": "Valuta promozione in EXACT",
    "NEGATIVE_CANDIDATE": "Valuta come negativa",
    "WATCH_SEARCH_TERM": "Monitora termine di ricerca",
    "NO_ACTION": "Nessuna azione",
    "REVIEW_NEGATIVE_CONFLICT": "Conflitto da verificare",
    "PROMOTED_TO_EXACT": "Promossa in EXACT",
    "WINNING_PRODUCT_TARGET": "Target prodotto vincente",
    "SUGGEST_IN_PHRASE": "Aggiungi in FRASE",
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
LAUNCH_STRATEGY_SECTION = "STRATEGIA DI LANCIO"
LAUNCH_STRATEGY_INTRO = ("Preset PHRASE_FIRST_AUTO: una lista PHRASE molto selezionata più una campagna AUTO in parallelo. Nessuna campagna EXACT iniziale: "
                         "EXACT si alimenta dopo il lancio dai Search Term reali che vendono. Bid e keyword sono solo SUGGERITI: nulla viene creato né applicato su Amazon.")
LAUNCH_STRATEGY_PHRASE = "A · PHRASE DA LANCIARE"
LAUNCH_STRATEGY_AUTO = "B · AUTO"
LAUNCH_STRATEGY_EXACT = "C · EXACT"
LAUNCH_STRATEGY_COPY = "Copia keyword PHRASE"
LAUNCH_STRATEGY_EXACT_TEXT = "Da alimentare dai vincitori reali dopo il lancio"
LAUNCH_STRATEGY_TIERS = {"TOP_TIER": "Top tier", "CORE": "Core", "BROAD_STRONG_MARKET": "Ampia (mercato forte)", "BROAD": "Ampia / discovery"}
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
OPP_INTRO = ("Suggerimenti manuali ricavati dai termini di ricerca reali (query digitate dagli utenti) di PHRASE, Auto e altre campagne. "
             "Sono separati dalle decisioni sulle offerte: nessuna campagna, keyword o negativa viene creata o modificata automaticamente.")
OPP_SECTION_PROMOTE = "A · DA PROMUOVERE IN EXACT"
OPP_SECTION_NEGATIVE = "B · CANDIDATI NEGATIVI"
OPP_SECTION_WATCH = "C · DA MONITORARE"
OPP_SECTION_CONFLICT = "D · CONFLITTI DA VERIFICARE"
OPP_SECTION_PROMOTED = "E · GIÀ PROMOSSE IN EXACT"
OPP_SECTION_PRODUCT = "F · TARGET PRODOTTO VINCENTE"
OPP_SECTION_PHRASE = "G · DA AGGIUNGERE IN FRASE"
OPP_KPI_EXACT = "OPPORTUNITÀ EXACT"
OPP_KPI_NEGATIVE = "CANDIDATI NEGATIVI"
OPP_NEW_CAMPAIGN = "Valuta creazione campagna EXACT"
OPP_NEW_CAMPAIGN_TEXT = ("Ci sono almeno {n} termini di ricerca unici da promuovere in EXACT provenienti da PHRASE/Auto e in questo marketplace non esiste "
                         "una campagna EXACT. Suggerimento strutturale, non operativo: nessuna campagna viene creata.")
OPP_AGREEMENT_LABELS = {"CONCORDI": "Finestre concordi", "DISCORDI": "Finestre discordanti", "SOLO_UNA_FINESTRA": "Una sola finestra",
                        "SOLO_65D": "Solo 65 giorni (30 giorni non importato)", "NON_CONFERMATO_30D": "Non confermato negli ultimi 30 giorni",
                        "ASSENTE_30D": "Nessuna attività negli ultimi 30 giorni"}
OPP_FLAG_LABELS = {"AUTO_STRONG_WINNER": "Vincitore AUTO forte", "AUTO_MODERATE_WINNER": "Vincitore AUTO moderato",
                   "ALREADY_IN_EXACT": "Già in EXACT", "EXACT_PAUSED": "Keyword EXACT in pausa", "DUPLICATE_ACROSS_CAMPAIGNS": "Presente in più campagne",
                   "MULTI_PARENT_TARGET": "Catturato da più target", "TERM_EQUALS_PARENT_TARGET": "Coincide con il target padre",
                   "NEGATIVE_EXACT": "Già negativa esatta", "NEGATIVE_PHRASE": "Già negativa a frase", "ALREADY_NEGATIVE": "Già negativa",
                   "NEGATIVE_CONFLICT": "Conflitto con negativa", "NEGATIVE_EXACT_ONLY": "Solo negativa esatta (stessa query del target)", "PROMOTED_TO_EXACT": "Promossa in EXACT",
                   "PROMOTED_EXACT_PAUSED": "Promossa in EXACT (target in pausa)", "PRODUCT_TARGET": "Target prodotto"}
OPP_NEGATIVE_TYPE_NOTE = "Il tipo di negativa (exact o phrase) non è deciso."
FLAG_LABELS = {
    "ZERO_ORDER_WASTE": "Spesa senza acquisti",
    "HIGH_VOLUME": "Alto volume",
    "HIGH_VOLUME_CAP": "Limite di sicurezza alto volume",
    "TARGET_STATUS_UNKNOWN": "Stato target sconosciuto",
    "TARGET_PAUSED": "Target in pausa",
    "TACTICAL_RECOVERED": "Periodo recente in recupero",
    "REVIEW_DELIVERY": "Verifica erogazione",
    "RECENT_DETERIORATION": "Peggioramento recente",
    "MILD_DETERIORATION_MONITOR": "Peggioramento lieve: da monitorare",
    "NO_RECENT_TRAFFIC": "Nessun traffico recente: verifica l'erogazione",
    "RECENT_ZERO_ORDERS_WATCH": "Recente senza acquisti: da monitorare",
    "LIFETIME_MILD_ZERO_ORDER_WATCH": "Eccesso storico lieve, recente senza acquisti: da monitorare",
    "RECENT_GOOD_ZERO_ORDER_WATCH": "Recente profittevole, ultimo periodo senza acquisti: da monitorare",
    "STRONG_CONFIRMED_CUT": "Problema forte e confermato: riduzione più decisa",
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
    "operationally_actionable": "Azionabile ora", "current_bid": "Offerta attuale", "bid_change_pct": "Variazione offerta",
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


def display_target(v):
    """Nome del target per la UI principale: i gruppi di targeting automatico sono tradotti, il resto resta com'e'."""
    return ND if v in (None, "") else AUTO_GROUP_NAMES.get(str(v), str(v))


def label_match_type(v):
    return ND if v in (None, "") else MATCH_TYPE_LABELS.get(str(v), str(v))


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
    (re.compile(r"\breduce (EXACT|PHRASE|COMPETITOR|[A-Z][A-Z0-9_]+) bid candidate"), r"candidato alla riduzione dell'offerta in \1"),
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


ACOS_COLUMNS = ("lifetime_acos", "recent_acos", "tactical_acos")           # valori gia' in punti percentuali (35.7 = 35,7%)


def display_value(column, v):
    """Valore di una colonna decisione per le tabelle: codici tecnici -> etichette italiane; NULL -> n/d."""
    if v is None:
        return ND
    if column in ("final_action", "underlying_candidate_action", "latest_period_action", "growth_opportunity"):
        return label_action(v)
    if column == "bid_change_pct":
        try:
            return f"{float(v):+.0f}%"                                       # testo, mai numeri misti a "n/d" nella stessa colonna
        except (TypeError, ValueError):
            return str(v)
    if column in ACOS_COLUMNS:
        try:
            return f"{float(v):.1f}%"                                        # testo "35.7%": mai numeri misti a "n/d" nella stessa colonna
        except (TypeError, ValueError):
            return str(v)
    if column == "target":
        return display_target(v)
    if column == "match_type":
        return label_match_type(v)
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

