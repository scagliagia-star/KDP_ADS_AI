"""PANORAMICA OPERATIVA: costruisce, dalle decisioni salvate e dalle opportunita' Search Term, TUTTE le collezioni mostrate nella pagina e i KPI.
Funzioni PURE (nessuna dipendenza da Streamlit, nessun accesso al DB, nessuna scrittura): i KPI sono derivati dalle STESSE liste che la pagina
renderizza, quindi non possono divergere. La dashboard non applica mai nulla su Amazon Ads: offerte e azioni sono solo suggerimenti.

Ogni decisione finisce in UNA sola sezione, in quest'ordine di priorita':
  COSA FARE OGGI (operationally_actionable) > TARGET IN PAUSA RILEVANTI > EROGAZIONE DA VERIFICARE > DA MONITORARE > STA ANDANDO BENE > ALTRE DECISIONI.

Soglie (documentate qui e mostrate nella pagina, sezione "Regole usate"):
  EROGAZIONE: target ATTIVO con 0 impressioni negli ultimi 30 giorni = NESSUNA EROGAZIONE; da 1 a LOW_IMPRESSIONS_30D = EROGAZIONE MOLTO BASSA;
              calo forte = segnale del TARGET crollato/in calo con rapporto recente/riferimento <= DECLINE_RATIO e riferimento >= DECLINE_MIN_REF_PER_DAY
              impressioni al giorno = EROGAZIONE IN CALO. Mai un suggerimento di alzare l'offerta solo perche' non eroga.
  PAUSA RILEVANTE: target in pausa con azione potenziale conservata, oppure spesa storica >= PAUSED_MIN_SPEND, oppure clic >= PAUSED_MIN_CLICKS.
  DA MONITORARE: WATCH importanti (>= WATCH_MIN_CLICKS clic o spesa >= WATCH_MIN_SPEND) e HOLD con rendimento recente peggiorato.
  STA ANDANDO BENE: HOLD con >= GOOD_MIN_ORDERS acquisti storici o spesa >= GOOD_MIN_SPEND, oppure target attivo PROFITTEVOLE (ACOS storico <= obiettivo)
              con >= GOOD_MIN_ORDERS_PROFITABLE acquisti e rendimento recente non peggiorato (le minori finiscono in ALTRE DECISIONI).
  CALO RELATIVO DI EROGAZIONE (target attivo con storico utile): rapporto per-giorno recente/riferimento del TARGET <= DECLINE_RATIO (periodi con date note,
              confronto normalizzato per durata, mai lifetime contro 30 giorni in valore assoluto) + storico >= DECLINE_HIST_MIN_CLICKS clic e
              >= DECLINE_HIST_MIN_ORDERS acquisti + impressioni recenti > 0 ma 0 clic. Senza una base di confronto comparabile del target non si classifica
              nessun calo (meglio perdere un alert che inventare un trend). Priorita' una sola card: nessuna erogazione > molto bassa > forte calo."""
import re

from kdp_ads.opportunities import NEGATIVE, NO_ACTION, PRODUCT_WINNER, PROMOTE, PROMOTED, REVIEW_CONFLICT, rank
from kdp_ads.snapshots import days_inclusive

LOW_IMPRESSIONS_30D = 30
DECLINE_RATIO = 0.30
DECLINE_MIN_REF_PER_DAY = 20.0
PAUSED_MIN_SPEND = 10.0
PAUSED_MIN_CLICKS = 10
WATCH_MIN_CLICKS = 10
WATCH_MIN_SPEND = 10.0
GOOD_MIN_ORDERS = 5
GOOD_MIN_ORDERS_PROFITABLE = 2                    # target profittevole (ACOS storico <= obiettivo) con almeno questi acquisti
DECLINE_HIST_MIN_CLICKS = 20                      # storico significativo per il calo relativo
DECLINE_HIST_MIN_ORDERS = 1
GOOD_MIN_SPEND = 30.0
RECENT_VALID_MIN_CLICKS = 10                      # periodo recente "valido": >= clic (come zero_orders.sample_clicks_min) e >= acquisti; sotto = campione minuscolo
RECENT_VALID_MIN_ORDERS = 1
MAX_DELIVERY_HOME = 10                            # righe di EROGAZIONE mostrate in Home (le altre stanno nella pagina Erogazione); il KPI resta il totale
MAX_ITEMS = 10                                    # elementi mostrati per EXACT / negative

RULES_TEXT = {
    "erogazione": (f"Target ATTIVO con 0 impressioni negli ultimi 30 giorni: nessuna erogazione; da 1 a {LOW_IMPRESSIONS_30D}: erogazione molto bassa; "
                   f"segnale del target in calo con rapporto ≤ {DECLINE_RATIO:g} rispetto al riferimento (almeno {DECLINE_MIN_REF_PER_DAY:g} impressioni/giorno di "
                   "riferimento): erogazione in calo. Anche con meno impressioni di riferimento se lo storico è utile "
                   f"(≥ {DECLINE_HIST_MIN_CLICKS} clic e ≥ {DECLINE_HIST_MIN_ORDERS} acquisto) e nel recente ci sono impressioni ma nessun clic. "
                   "Il calo si calcola per giorno su periodi con date note; senza un confronto comparabile non si segnala. "
                   "Non si suggerisce mai di alzare l'offerta solo perché non eroga."),
    "pausa": (f"Target in pausa con azione potenziale, oppure spesa ≥ {PAUSED_MIN_SPEND:g} o ≥ {PAUSED_MIN_CLICKS} clic."),
    "monitora": (f"Decisioni di monitoraggio con ≥ {WATCH_MIN_CLICKS} clic o spesa ≥ {WATCH_MIN_SPEND:g}, e target in mantenimento con rendimento recente peggiorato."),
    "bene": (f"Target in mantenimento con ≥ {GOOD_MIN_ORDERS} acquisti storici o spesa ≥ {GOOD_MIN_SPEND:g}, oppure attivo e profittevole "
             f"(ACOS storico entro l'obiettivo) con ≥ {GOOD_MIN_ORDERS_PROFITABLE} acquisti e rendimento recente non peggiorato."),
}

# ------------------------------------------------------------------ terminologia (solo visualizzazione)
ACTION_WORDS = {
    "BID_DOWN_5_CANDIDATE": "ABBASSA OFFERTA", "BID_DOWN_10_CANDIDATE": "ABBASSA OFFERTA", "BID_DOWN_15_CANDIDATE": "ABBASSA OFFERTA",
    "BID_UP_5_CANDIDATE": "ALZA OFFERTA", "BID_UP_10_CANDIDATE": "ALZA OFFERTA", "PAUSE_CANDIDATE": "METTI IN PAUSA",
    "REACTIVATE_CANDIDATE": "RIATTIVA", "REVIEW_DELIVERY_BEFORE_BID_CHANGE": "VERIFICA EROGAZIONE", "REVIEW_TARGET_STATUS": "VERIFICA STATO TARGET",
    "NEGATIVE_CANDIDATE": "VALUTA COME NEGATIVA", "PROMOTE_TO_EXACT": "METTI IN EXACT", "WATCH": "MONITORA", "HOLD": "NON TOCCARE",
    "HOLD_PAUSED_TARGET": "GIÀ IN PAUSA", "NO_ACTION": "NESSUNA AZIONE",
}
BID_ACTIONS = {"BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE", "BID_UP_5_CANDIDATE", "BID_UP_10_CANDIDATE"}
AUTO_GROUP_NAMES = {"loose-match": "Corrispondenza generica", "close-match": "Corrispondenza stretta", "substitutes": "Sostituti", "complements": "Complementi"}
DELIVERY_CLASS = {"NO_DELIVERY": "NESSUNA EROGAZIONE", "VERY_LOW": "EROGAZIONE MOLTO BASSA", "DECLINING": "EROGAZIONE IN CALO"}
_PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
_ORIGIN = {"PHRASE": "Frase", "EXACT": "Exact", "BROAD": "Generica"}


def target_name(d_or_name, target_type=None) -> str:
    """Nome del target come nel pannello Amazon Ads in italiano: i gruppi di targeting automatico sono tradotti, il resto resta com'e'."""
    name = d_or_name.get("target") if isinstance(d_or_name, dict) else d_or_name
    return AUTO_GROUP_NAMES.get(str(name), name) if name is not None else "n/d"


def card_title(d: dict) -> str:
    return f"{d.get('campaign_id')} · {target_name(d)}"


# ------------------------------------------------------------------ formattazione italiana nelle frasi
def it_pct(v, digits=1) -> str:
    return "n/d" if v is None else f"{float(v):.{digits}f}".replace(".", ",") + "%"


def it_money(v, symbol="$") -> str:
    if v is None:
        return "n/d"
    s = f"{abs(float(v)):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"-{symbol}{s}" if float(v) < 0 else f"{symbol}{s}"


def it_int(v) -> str:
    return "n/d" if v is None else f"{float(v):,.0f}".replace(",", ".")


def plural(n, one, many) -> str:
    return f"{it_int(n)} {one if float(n) == 1 else many}"


def strip_codes(text) -> str:
    """Testo per l'utente: via i blocchi tra parentesi quadre del motore ([regola C1_..., affidabilita'...], [HIGH_VOLUME_INEFFICIENT]) e i codici
    MAIUSCOLI_CON_TRATTINO. Il testo originale resta intatto in DETTAGLIO TECNICO."""
    s = re.sub(r"\s*\[[^\]]*\]", "", str(text or ""))
    s = re.sub(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def plain_text(text) -> str:
    """strip_codes + terminologia Amazon Ads in italiano (ordini -> acquisti, bid -> offerta, search term -> termini di ricerca)."""
    s = re.sub(r"\(\s*[A-Z][A-Z_:]*\s*\)", "", strip_codes(text))
    for rx, rep in ((r"\bordini\b", "acquisti"), (r"\bordine\b", "acquisto"), (r"\bbid-down\b", "riduzione dell'offerta"),
                    (r"\bbid-up\b", "aumento dell'offerta"), (r"\bbid\b", "offerta"), (r"\bsearch terms?\b", "termini di ricerca")):
        s = re.sub(rx, rep, s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip()


def window_days(window_or_period, default=None):
    """'RECENT_65D' -> 65; '2026-09-06__2026-10-05' o '2026-08-02→2026-10-05' -> giorni inclusi."""
    s = str(window_or_period or "")
    m = re.fullmatch(r"RECENT_(\d+)D", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:__|→|->)(\d{4}-\d{2}-\d{2})", s)
    return days_inclusive(m.group(1), m.group(2)) if m else default


# ------------------------------------------------------------------ azioni e offerta suggerita
def simple_action(d: dict) -> dict:
    """{word, pct, headline}: etichetta semplice lato utente. Il codice tecnico non esce da qui."""
    fa = d.get("final_action") or ""
    word = ACTION_WORDS.get(fa, fa or "n/d")
    pct = d.get("bid_change_pct")
    if fa in BID_ACTIONS and pct is not None:
        return {"word": word, "pct": float(pct), "headline": f"{word} DEL {abs(float(pct)):g}%"}
    return {"word": word, "pct": None, "headline": word}


def suggested_bid(current, pct):
    """Offerta suggerita = attuale x (1 + variazione%), 2 decimali. Solo un suggerimento: non viene mai applicata."""
    if current is None or pct is None:
        return None
    return round(float(current) * (1 + float(pct) / 100), 2)


def bid_info(d: dict) -> dict | None:
    pct = d.get("bid_change_pct")
    if pct is None or d.get("current_bid") is None:
        return None
    return {"current": float(d["current_bid"]), "suggested": suggested_bid(d["current_bid"], pct), "change_pct": float(pct)}


def acos_summary(d: dict, book: dict) -> dict:
    """ACOS mostrati sulla card: n/d (None) se il periodo non esiste o non ha vendite."""
    rw = window_days(d.get("recent_window"), 65)
    tw = window_days(d.get("tactical_window"), 30)
    return {"target": book.get("target_acos"), "break_even": book.get("break_even_acos"), "lifetime": d.get("lifetime_acos"),
            "recent": d.get("recent_acos") if d.get("recent_clicks") is not None else None, "recent_days": rw,
            "tactical": d.get("tactical_acos") if d.get("tactical_clicks") is not None else None, "tactical_days": tw}


PAUSED_WASTE_ROYALTY_MULT = 2.0      # spreco chiaro: 0 acquisti con spesa storica >= 2 x royalty


def provably_inefficient(d: dict, book: dict) -> bool:
    """True solo con dati chiari: ACOS storico sopra il pareggio, oppure nessun acquisto con spesa storica >= PAUSED_WASTE_ROYALTY_MULT x royalty.
    Con ACOS n/d, nessuna vendita o dati scarsi l'inefficienza NON e' dimostrabile."""
    acos, be, roy = d.get("lifetime_acos"), book.get("break_even_acos"), book.get("royalty_per_copy")
    if acos is not None and be is not None and acos > be:
        return True
    return bool(not (d.get("lifetime_orders") or 0) and roy and _spend(d) >= PAUSED_WASTE_ROYALTY_MULT * float(roy))


def short_reason(d: dict, book: dict) -> str:
    """Motivo breve in italiano semplice, costruito dai numeri della decisione (mai codici tecnici)."""
    fa, tgt = d.get("final_action"), book.get("target_acos")
    life, rec, tac = d.get("lifetime_acos"), d.get("recent_acos"), d.get("tactical_acos")
    rd, td = window_days(d.get("recent_window"), 65), window_days(d.get("tactical_window"), 30)
    orders = d.get("lifetime_orders")
    sig = d.get("recent_vs_historical_signal")
    zero_orders = (d.get("lifetime_orders") or 0) == 0 and life is None           # 0 acquisti: ACOS n/d, mai confrontato con obiettivo o pareggio
    roy = book.get("royalty_per_copy")
    if fa in ("BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE") and zero_orders:
        ratio = (f" La spesa è {str(round(_spend(d) / float(roy), 1)).replace('.', ',')} volte la royalty:" if roy else " La spesa ha superato la soglia:")
        return (f"Ha accumulato {plural(d.get('lifetime_clicks') or 0, 'clic', 'clic')} e {it_money(d.get('lifetime_spend'))} di spesa senza acquisti." + ratio
                + " riduzione prudente dell'offerta.")
    if fa in ("BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE"):
        if life is not None and tgt is not None and life <= tgt:        # storico NON sopra l'obiettivo: mai "spende troppo rispetto all'obiettivo"
            return (f"Lo storico è in linea con l'obiettivo (ACOS {it_pct(life)} contro {it_pct(tgt)}), ma il rendimento recente è peggiorato: "
                    "riduzione prudente dell'offerta.")
        if "STRONG_CONFIRMED_CUT" in str(d.get("flags") or ""):
            return (f"Storico ({it_pct(life)}) e ultimi {td} giorni ({it_pct(tac)}) sono molto sopra il tuo ACOS di pareggio, con molti clic: "
                    "riduzione più decisa dell'offerta.")
        s = f"Sta spendendo troppo rispetto al tuo ACOS obiettivo (storico {it_pct(life)} contro {it_pct(tgt)})"
        if rec is not None and tgt is not None and rec <= tgt:
            return s + f", ma gli ultimi {rd} giorni sono migliori ({it_pct(rec)}): riduzione prudente."
        if tac is not None and tgt is not None and tac <= tgt:
            return s + f", ma gli ultimi {td} giorni sono migliori ({it_pct(tac)}): riduzione prudente."
        return s + ": riduzione dell'offerta."
    if fa in ("BID_UP_5_CANDIDATE", "BID_UP_10_CANDIDATE"):
        return f"Rende bene (ACOS {it_pct(life)}, sotto l'obiettivo {it_pct(tgt)}) e c'è margine di crescita: aumento prudente."
    if fa == "PAUSE_CANDIDATE":
        return f"Ha speso {it_money(d.get('lifetime_spend'))} senza un ritorno sufficiente (ACOS {it_pct(life)}): valuta di metterlo in pausa."
    if fa == "REVIEW_DELIVERY_BEFORE_BID_CHANGE":
        return "L'erogazione è calata: verifica l'offerta e la pertinenza prima di cambiare l'offerta."
    if fa == "HOLD_PAUSED_TARGET":
        return "Era inefficiente ed è già in pausa." if provably_inefficient(d, book) else "È già in pausa. Nessuna azione necessaria."
    if fa == "WATCH":
        if "NO_RECENT_TRAFFIC" in str(d.get("flags") or ""):
            return "Storicamente era da ridurre, ma ora non sta ricevendo traffico e l'offerta è già bassa: verifica l'erogazione prima di toccarla."
        if "RECENT_ZERO_ORDERS_WATCH" in str(d.get("flags") or ""):
            return (f"Storicamente è in linea con l'obiettivo, ma negli ultimi {td} giorni ha avuto {plural(d.get('tactical_clicks') or 0, 'clic', 'clic')} "
                    f"e {it_money(d.get('tactical_spend'))} di spesa senza acquisti. La spesa è ancora bassa per ridurre l'offerta: monitora.")
        if "LIFETIME_MILD_ZERO_ORDER_WATCH" in str(d.get("flags") or ""):
            return (f"Lo storico è solo poco sopra l'obiettivo e negli ultimi {td} giorni ha avuto {plural(d.get('tactical_clicks') or 0, 'clic', 'clic')} e "
                    f"{it_money(d.get('tactical_spend'))} di spesa senza acquisti, ancora sotto le soglie per ridurre l'offerta: monitora.")
        if "RECENT_GOOD_ZERO_ORDER_WATCH" in str(d.get("flags") or ""):
            return (f"Storicamente è poco sopra l'obiettivo, ma il periodo recente è profittevole e negli ultimi {td} giorni ha avuto "
                    f"{plural(d.get('tactical_clicks') or 0, 'clic', 'clic')} e {it_money(d.get('tactical_spend'))} di spesa senza acquisti, ancora sotto le soglie "
                    "per ridurre l'offerta: monitora.")
        if "MILD_DETERIORATION_MONITOR" in str(d.get("flags") or ""):
            return "Storicamente è ancora profittevole, ma il rendimento recente è peggiorato. Il bid è già basso: monitora prima di ridurlo."
        if (d.get("lifetime_orders") or 0) == 0 and life is None:
            return "Non ha ancora generato acquisti, ma la spesa è ancora troppo bassa per giustificare una riduzione dell'offerta."
        if sig == "DECLINING_SIGNAL" or (life is not None and tgt is not None and life <= tgt and tac is not None and tac > tgt):
            return "Storicamente forte, ma il rendimento recente è peggiorato."
        if (life is not None and tgt is not None and life > tgt) or (tac is not None and tgt is not None and tac > tgt):
            return f"Rendimento sotto l'obiettivo ma con pochi dati ({plural(orders or 0, 'acquisto', 'acquisti')} nello storico): tienilo d'occhio."
        return "Dati ancora insufficienti per agire: tienilo d'occhio."
    if fa == "HOLD":
        if life is not None and tgt is not None and life <= tgt:        # storico entro l'obiettivo: giudica solo il recente valido contro l'obiettivo
            w = recent_window_used(d)
            if w is not None and w["acos"] > tgt:
                return (f"Storicamente in linea con l'obiettivo (ACOS {it_pct(life)}), ma negli ultimi {w['days']} giorni l'ACOS è {it_pct(w['acos'])}, "
                        f"sopra l'obiettivo {it_pct(tgt)}: tienilo d'occhio.")
            return "Sta funzionando bene." if (d.get("lifetime_estimated_profit") or 0) > 0 else "Rendimento in linea con l'obiettivo: non serve intervenire."
        if sig == "DECLINING_SIGNAL":
            return "Storicamente forte, ma il rendimento recente è peggiorato."
        if tac is not None and tgt is not None and tac > tgt and (d.get("tactical_orders") or 0) < 3:
            return "Recente debole, ma campione ancora limitato."
        if tac is not None and tgt is not None and tac > tgt:
            return "Recente debole rispetto all'obiettivo: tienilo d'occhio."
        if (d.get("lifetime_estimated_profit") or 0) > 0:
            return "Sta funzionando bene."
        return "Rendimento in linea con l'obiettivo: non serve intervenire."
    if fa == "NO_ACTION":
        return "Dati insufficienti per decidere: nessuna azione."
    return strip_codes(d.get("reason")) or "n/d"


# ------------------------------------------------------------------ card
def decision_card(d: dict, book: dict) -> dict:
    sa = simple_action(d)
    b = bid_info(d)
    return {"id": d.get("decision_id") or d.get("pk"), "kind": "DECISION", "badge": sa["word"], "headline": sa["headline"], "priority": d.get("priority"),
            "campaign": d.get("campaign_id"), "target": target_name(d), "title": card_title(d), "bid": b, "reason": short_reason(d, book),
            "acos": acos_summary(d, book), "confidence": d.get("confidence"), "underlying": d.get("underlying_candidate_action"),
            "status": d.get("target_status"), "decision": d}


def _imp30(d: dict):
    """(impressioni negli ultimi 30 giorni del TARGET, giorni) dal periodo tattico; None se il periodo non c'e'."""
    ipd = d.get("tactical_impressions_per_day")
    days = window_days(d.get("tactical_period")) or window_days(d.get("tactical_window"))
    if ipd is None or not days or d.get("tactical_window") is None:
        return None, days
    return round(float(ipd) * days), days


def _delivery_ref(d: dict, parse_detail) -> dict:
    det = parse_detail(d.get("reason")) if parse_detail else None
    scope = "target" if d.get("delivery_source") == "TARGET" else "campagna"
    return {"per_day": det["reference"] if det else None, "days": det["reference_days"] if det else None, "scope": scope,
            "ratio": d.get("delivery_ratio") if d.get("delivery_ratio") is not None else (det["ratio"] if det else None)}


def delivery_item(d: dict, parse_detail=None) -> dict | None:
    """Voce di EROGAZIONE DA VERIFICARE per un target ATTIVO, oppure None."""
    if d.get("target_status") != "ENABLED":
        return None
    imp, days = _imp30(d)
    ref = _delivery_ref(d, parse_detail)
    cls = None
    if imp is not None:
        if imp == 0:
            cls = "NO_DELIVERY"
        elif imp <= LOW_IMPRESSIONS_30D:
            cls = "VERY_LOW"
    history = False
    if cls is None and d.get("delivery_source") == "TARGET" and d.get("delivery_signal") in ("DELIVERY_COLLAPSED", "DELIVERY_DECLINING"):
        r = ref["ratio"]                                  # rapporto PER GIORNO fra due periodi con date note del TARGET: normalizzato per durata
        if r is not None and r <= DECLINE_RATIO and (ref["per_day"] or 0) >= DECLINE_MIN_REF_PER_DAY:
            cls = "DECLINING"
        elif (r is not None and r <= DECLINE_RATIO and (ref["per_day"] or 0) > 0 and imp and not (d.get("tactical_clicks") or 0)
              and (d.get("lifetime_clicks") or 0) >= DECLINE_HIST_MIN_CLICKS and (d.get("lifetime_orders") or 0) >= DECLINE_HIST_MIN_ORDERS):
            cls, history = "DECLINING", True              # storico utile + calo forte relativo, anche con riferimento giornaliero basso
    if cls is None:
        return None
    if cls == "NO_DELIVERY":
        reason = "Il target è attivo ma non sta ricevendo traffico. Verifica offerta e pertinenza."
    elif cls == "VERY_LOW":
        reason = f"Il target è attivo ma riceve pochissime impressioni ({it_int(imp)} negli ultimi {days} giorni). Verifica offerta e pertinenza."
    elif history:
        reason = ("Il target aveva traffico e vendite nello storico, ma negli ultimi 30 giorni l'erogazione è crollata. "
                  "Verifica pertinenza, competitività dell'offerta e cambiamenti nella campagna.")
    else:
        reason = "Le impressioni sono calate molto rispetto al periodo precedente. Verifica offerta, concorrenza e stato della campagna."
    return {"id": d.get("decision_id") or d.get("pk"), "kind": "DELIVERY", "klass": cls, "badge": DELIVERY_CLASS[cls], "campaign": d.get("campaign_id"),
            "target": target_name(d), "title": card_title(d), "bid": d.get("current_bid"), "impressions_30d": imp, "days": days, "reference": ref,
            "clicks_30d": d.get("tactical_clicks"), "status": d.get("target_status"), "reason": reason, "decision": d}


# ------------------------------------------------------------------ opportunita' Search Term
def _origin(r: dict) -> str:
    if r.get("parent_target_type") == "AUTO_TARGETING_GROUP":
        return "Auto"
    return _ORIGIN.get(str(r.get("campaign_type")), str(r.get("campaign_type") or "n/d"))


def exact_reason(r: dict) -> str:
    acos = it_pct(r.get("acos"))
    strength = "rendimento forte" if r.get("confidence") == "HIGH" else "buon rendimento (dati ancora limitati)"
    tail = ("non è ancora presente in EXACT" if not r.get("already_in_exact") else "è già presente in EXACT")
    s = f"{plural(r.get('orders') or 0, 'acquisto', 'acquisti')}, ACOS {acos}, {strength} e {tail}."
    if r.get("already_targeted_elsewhere") and r.get("targeted_in"):
        s += f" È già un target in {', '.join(r['targeted_in'])}: la promozione resta possibile."
    return s


def negative_reason(r: dict, symbol="$") -> str:
    base = f"{plural(r.get('clicks') or 0, 'clic', 'clic')}, {it_money(r.get('spend'), symbol)} di spesa"
    if not r.get("orders"):
        s = base + " e nessun acquisto."
    else:
        s = base + f", {plural(r['orders'], 'acquisto', 'acquisti')} con ACOS {it_pct(r.get('acos'))}: rendimento molto sotto il pareggio."
    if r.get("negative_type") == "EXACT":
        s += " È la stessa query del target: aggiungila come negativa esatta, il target resta attivo."
    return s


def conflict_reason(r: dict) -> str:
    kind = "negativa esatta" if r.get("negative_match") == "EXACT" else "negativa a frase"
    camp = r.get("campaign_id")
    return (f"{plural(r.get('orders') or 0, 'acquisto', 'acquisti')} e ACOS {it_pct(r.get('acos'))}, ma il termine risulta già impostato come "
            f"{kind} nella campagna {camp}.")


def conflict_text(r: dict) -> str:
    kind = "negativa esatta" if r.get("negative_match") == "EXACT" else "negativa a frase"
    return (f"Il termine è {kind} nella campagna {r.get('campaign_id')}, ma non risulta presente in una campagna EXACT. "
            "Verifica se la promozione è stata completata.")


PROMOTED_TEXT = "Questa keyword è già stata promossa in EXACT ed esclusa dalla FRASE con negativa esatta. Configurazione coerente."
PROMOTED_PAUSED_TEXT = "Questa keyword è stata promossa in EXACT ed esclusa dalla FRASE, ma il target EXACT è attualmente in pausa."
CONFLICT_HINT = "Verifica la negativa prima di qualsiasi promozione in EXACT."


def conflict_items(records: list, symbol="$") -> list:
    """CONFLITTI DA VERIFICARE: termini che vendono ma sono gia' negativi (stessa campagna). Informativi: mai azionabili, mai nei KPI EXACT/negative.
    Un record per (campagna, termine): lo stesso termine in due campagne diverse e' due conflitti distinti."""
    out = []
    for r in rank(records, REVIEW_CONFLICT):
        out.append({"kind": "CONFLICT", "badge": "CONFLITTO DA VERIFICARE", "term": r["search_term"], "campaign": r["campaign_id"], "also_in": [],
                    "parent": target_name(r.get("parent_target")), "origin": _origin(r), "clicks": r.get("clicks"), "orders": r.get("orders"),
                    "spend": r.get("spend"), "sales": r.get("sales"), "acos": r.get("acos"), "confidence": r.get("confidence"),
                    "negative_match": r.get("negative_match"), "already_in_exact": bool(r.get("already_in_exact")),
                    "text": conflict_text(r), "reason": conflict_reason(r),
                    "hint": CONFLICT_HINT, "record": r})
    return out


def promoted_items(records: list) -> list:
    """KEYWORD GIA' PROMOSSE IN EXACT: negativa nella campagna di scoperta + keyword gia' in una campagna EXACT (flusso voluto). Solo informazione:
    nessuna azione, nessun KPI. Termini unici (la voce con piu' acquisti)."""
    out, seen = [], set()
    for r in rank(records, PROMOTED):
        if r["search_term"] in seen:
            continue
        seen.add(r["search_term"])
        paused = bool(r.get("promoted_exact_paused"))
        out.append({"kind": "PROMOTED", "badge": "PROMOSSA IN EXACT · ATTUALMENTE IN PAUSA" if paused else "PROMOSSA IN EXACT", "term": r["search_term"],
                    "campaign": r["campaign_id"], "exact_campaigns": list(r.get("promoted_in") or []), "paused": paused, "clicks": r.get("clicks"),
                    "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"), "acos": r.get("acos"),
                    "text": PROMOTED_PAUSED_TEXT if paused else PROMOTED_TEXT, "record": r})
    return out


def _product_decision(decisions, r) -> dict | None:
    """Decisione operativa del product target che ha catturato l'ASIN (stessa campagna, stessa stringa target). None se non esiste."""
    tgt = str(r.get("parent_target") or "").strip().lower()
    for d in decisions or []:
        if d.get("campaign_id") == r.get("campaign_id") and not d.get("search_term") and str(d.get("target") or "").strip().lower() == tgt:
            return d
    return None


def product_state(d: dict, state_of: dict | None = None) -> dict:
    """STATO OPERATIVO del target dalla decisione del decision layer (mai una decisione propria): stessa etichetta con cui la Panoramica mostra
    quel target (COSA FARE OGGI / IN PAUSA / MONITORA / NON TOCCARE), altrimenti la parola dell'azione."""
    sa = simple_action(d)
    word = (state_of or {}).get(d.get("decision_id") or d.get("pk")) or sa["word"]
    days = window_days(d.get("tactical_window"), 30)
    tac = d.get("tactical_acos") if d.get("tactical_clicks") is not None else None
    if word == "GIÀ IN PAUSA":
        text = "Il target è già in pausa. Nessuna azione necessaria."
    else:
        text = (f"Il target complessivo ha ACOS {it_pct(tac)} negli ultimi {days} giorni." if tac is not None
                else f"Il target complessivo ha ACOS storico {it_pct(d.get('lifetime_acos'))}.")
        text += " Per ora non modificare l'offerta." if word in ("MONITORA", "NON TOCCARE") else f" Decisione sul target: {sa['headline'].lower()}."
    return {"word": word, "text": text, "target_acos": tac, "days": days}


def product_winner_items(records: list, decisions=None, state_of=None) -> list:
    """TARGET PRODOTTO VINCENTE: classificazione INFORMATIVA derivata dai Search Term (un ASIN non e' una keyword: mai 'METTI IN EXACT').
    Non genera nessuna decisione: lo stato operativo, se c'e', e' quello del decision layer per quel product target; senza decisione
    si mostrano solo i dati Search Term. Nessun KPI."""
    out = []
    for r in rank(records, PRODUCT_WINNER):
        d = _product_decision(decisions, r)
        out.append({"kind": "PRODUCT", "badge": "TARGET PRODOTTO VINCENTE", "asin": r.get("product_asin") or str(r["search_term"]).upper(),
                    "campaign": r["campaign_id"], "status": (d or {}).get("target_status") or r.get("target_status"), "clicks": r.get("clicks"),
                    "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"), "acos": r.get("acos"),
                    "reason": f"Ha generato {plural(r.get('orders') or 0, 'acquisto', 'acquisti')} con ACOS {it_pct(r.get('acos'))} nei termini/prodotti recenti.",
                    "operational": product_state(d, state_of) if d else None, "record": r})
    return out


def already_negative_items(records: list) -> list:
    """GIA' NEGATIVA: solo informazione (nessuna azione). Termini unici gia' negativi in almeno una campagna e non in conflitto."""
    seen, out = set(), []
    for r in sorted(records, key=lambda r: (-(r.get("spend") or 0), r["search_term"])):
        if r.get("opportunity_type") != NO_ACTION or not r.get("negative_match") or r["search_term"] in seen:
            continue
        seen.add(r["search_term"])
        out.append({"term": r["search_term"], "campaign": r["campaign_id"], "badge": "GIÀ NEGATIVA", "negative_match": r["negative_match"]})
    return out


def opportunity_items(records: list, otype: str, symbol="$") -> list:
    """Termini UNICI (il migliore per affidabilita'/acquisti/spesa) con i dati per la card. Il conteggio dei KPI e' la lunghezza di questa lista."""
    out, seen = [], set()
    for r in rank(records, otype):
        if r["search_term"] in seen:
            continue
        seen.add(r["search_term"])
        also = sorted({x["campaign_id"] for x in records if x["search_term"] == r["search_term"] and x["opportunity_type"] == otype and x is not r})
        neg_exact = otype == NEGATIVE and r.get("negative_type") == "EXACT"
        out.append({"kind": "EXACT" if otype == PROMOTE else "NEGATIVE",
                    "badge": "METTI IN EXACT" if otype == PROMOTE else "VALUTA COME NEGATIVA ESATTA" if neg_exact else "VALUTA COME NEGATIVA",
                    "term": r["search_term"], "campaign": r["campaign_id"], "also_in": also, "parent": target_name(r.get("parent_target")),
                    "parents": [target_name(p) for p in r.get("parent_targets") or []], "origin": _origin(r), "clicks": r.get("clicks"),
                    "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"), "acos": r.get("acos"), "confidence": r.get("confidence"),
                    "already_in_exact": bool(r.get("already_in_exact")), "reason": exact_reason(r) if otype == PROMOTE else negative_reason(r, symbol),
                    "negative_type": r.get("negative_type") if otype == NEGATIVE else None, "record": r})
    return out


# ------------------------------------------------------------------ composizione della Panoramica
def _spend(d):
    return d.get("lifetime_spend") or 0.0


def _paused_relevant(d: dict) -> bool:
    return bool(d.get("underlying_candidate_action")) or _spend(d) >= PAUSED_MIN_SPEND or (d.get("lifetime_clicks") or 0) >= PAUSED_MIN_CLICKS


def _profitable_small(d: dict, book: dict) -> bool:
    """Target ATTIVO in mantenimento, profittevole (ACOS storico <= obiettivo) con almeno GOOD_MIN_ORDERS_PROFITABLE acquisti. Il recente peggiorato e'
    gia' stato escluso prima (DA MONITORARE). La decisione resta quella del decision layer: qui si decide solo DOVE mostrarla."""
    life, tgt = d.get("lifetime_acos"), book.get("target_acos")
    return bool(d.get("target_status") == "ENABLED" and life is not None and tgt is not None and life <= tgt
                and (d.get("lifetime_orders") or 0) >= GOOD_MIN_ORDERS_PROFITABLE)


def recent_window_used(d: dict) -> dict | None:
    """Periodo recente piu' informativo UTILIZZABILE: il 30 giorni (tattico) se ha almeno RECENT_VALID_MIN_CLICKS clic e RECENT_VALID_MIN_ORDERS acquisti
    (quindi un ACOS), altrimenti il 65 giorni; None se nessuno dei due e' un campione sufficiente (niente reazioni a campioni minuscoli)."""
    for prefix, default_days, label in (("tactical", 30, "tactical_window"), ("recent", 65, "recent_window")):
        clicks, orders, acos = d.get(f"{prefix}_clicks"), d.get(f"{prefix}_orders"), d.get(f"{prefix}_acos")
        if acos is not None and (clicks or 0) >= RECENT_VALID_MIN_CLICKS and (orders or 0) >= RECENT_VALID_MIN_ORDERS:
            return {"acos": acos, "clicks": clicks, "orders": orders, "days": window_days(d.get(label), default_days)}
    return None


def recent_state(d: dict, book: dict) -> str | None:
    """'WEAK' (da monitorare), 'OK' (recente valido entro l'obiettivo) o None (nessun giudizio).
    Storico entro l'obiettivo: conta SOLO il recente valido contro l'OBIETTIVO; un ACOS recente numericamente piu' alto dello storico ma ancora entro
    l'obiettivo NON e' un peggioramento (il deterioramento relativo da solo non basta). Storico sopra l'obiettivo (o n/d): regola prudente precedente
    (segnale in calo, oppure ACOS 30 giorni sopra l'obiettivo con almeno 3 clic)."""
    tgt, life = book.get("target_acos"), d.get("lifetime_acos")
    if tgt is not None and life is not None and life <= tgt:
        w = recent_window_used(d)
        return None if w is None else ("WEAK" if w["acos"] > tgt else "OK")
    if d.get("recent_vs_historical_signal") == "DECLINING_SIGNAL":
        return "WEAK"
    tac = d.get("tactical_acos")
    return "WEAK" if tac is not None and tgt is not None and tac > tgt and (d.get("tactical_clicks") or 0) >= 3 else None


def _weak_recent(d: dict, book: dict) -> bool:
    return recent_state(d, book) == "WEAK"


def paused_card(d: dict, book: dict) -> dict:
    note = None
    if d.get("underlying_candidate_action"):
        und = simple_action({"final_action": d["underlying_candidate_action"], "bid_change_pct": None})
        note = f"Azione potenziale conservata: {und['word'].lower()} (il target è già fermo, nessuna azione necessaria)."
    return {"id": d.get("decision_id") or d.get("pk"), "kind": "PAUSED", "badge": "GIÀ IN PAUSA", "title": card_title(d), "campaign": d.get("campaign_id"),
            "target": target_name(d), "reason": short_reason({**d, "final_action": "HOLD_PAUSED_TARGET"}, book), "note": note,
            "acos": acos_summary(d, book), "spend": d.get("lifetime_spend"), "clicks": d.get("lifetime_clicks"), "decision": d}


def monitor_card(d: dict, book: dict) -> dict:
    return {"id": d.get("decision_id") or d.get("pk"), "kind": "MONITOR", "badge": "MONITORA", "title": card_title(d), "campaign": d.get("campaign_id"),
            "target": target_name(d), "reason": short_reason({**d, "final_action": "WATCH" if d.get("final_action") == "WATCH" else "HOLD"}, book),
            "lifetime_acos": d.get("lifetime_acos"), "tactical_acos": d.get("tactical_acos") if d.get("tactical_clicks") is not None else None,
            "tactical_orders": d.get("tactical_orders"), "tactical_days": window_days(d.get("tactical_window"), 30), "decision": d}


def good_card(d: dict, book: dict) -> dict:
    return {"id": d.get("decision_id") or d.get("pk"), "kind": "GOOD", "badge": "NON TOCCARE", "title": card_title(d), "campaign": d.get("campaign_id"),
            "target": target_name(d), "reason": short_reason({**d, "final_action": "HOLD"}, book), "lifetime_acos": d.get("lifetime_acos"),
            "recent_acos": d.get("recent_acos") if d.get("recent_clicks") is not None else d.get("tactical_acos"),
            "recent_days": window_days(d.get("recent_window"), 65) if d.get("recent_clicks") is not None else window_days(d.get("tactical_window"), 30),
            "orders": d.get("lifetime_orders"), "profit": d.get("lifetime_estimated_profit"), "decision": d}


def other_row(d: dict) -> dict:
    sa = simple_action(d)
    return {"id": d.get("decision_id") or d.get("pk"), "campaign": d.get("campaign_id"), "target": target_name(d), "action": sa["word"],
            "status": d.get("target_status"), "priority": d.get("priority"), "decision": d}


def account_text(account_id, names: dict | None = None) -> str | None:
    """«Account: nome · ID» di una decisione/opportunita' (None = campagna legacy senza account: nessuna etichetta inventata)."""
    if not account_id:
        return None
    name = (names or {}).get(account_id)
    return f"Account: {name} · {account_id}" if name else f"Account: {account_id}"


def campaign_text(name, amazon_campaign_id, max_len: int = 44, internal_id=None, show_legacy: bool = False) -> str | None:
    """«Campagna: nome… · ID 1234» (testo secondario) per distinguere campagne dello stesso tipo (piu' AUTO, piu' FRASE...). Una campagna storica senza ID Amazon mostra il nome
    interno («storica, senza ID Amazon») solo se `show_legacy` (cioe' se il libro ha piu' campagne dello stesso tipo): niente rumore quando non serve."""
    nm = str(name or "").strip() or str(internal_id or "").strip()
    nm = nm if len(nm) <= max_len else nm[:max_len - 1].rstrip() + "…"
    if amazon_campaign_id:
        return f"Campagna: {nm} · ID {amazon_campaign_id}" if nm else f"Campagna ID {amazon_campaign_id}"
    if show_legacy and nm:
        return f"Campagna: {nm} · storica, senza ID Amazon"
    return None


def ambiguous_campaign_types(decisions) -> set:
    """Tipi di campagna con PIU' di una campagna nel libro (es. due AUTO): lì l'identificazione della campagna serve sempre, anche per le storiche."""
    seen = {}
    for d in decisions:
        seen.setdefault(str(d.get("campaign_type") or ""), set()).add(d.get("campaign_id"))
    return {t for t, ids in seen.items() if len(ids) > 1}


def build_overview(decisions: list, book: dict, summary: dict, warnings: list, opp: dict | None, parse_detail=None, symbol="$", account_names: dict | None = None) -> dict:
    """Tutte le sezioni e i KPI. `opp` = risultato di Repo.search_term_opportunities (None se non calcolabile). I KPI sono len() delle liste.
    Ogni scheda riporta il proprio ACCOUNT Amazon Ads (campo `account`) quando noto: con piu' account una decisione non e' mai anonima."""
    has_flag = summary.get("actionable") is not None
    actionable = sorted((d for d in decisions if d.get("operationally_actionable")),
                        key=lambda d: (_PRIORITY_ORDER.get(d.get("priority"), 9), -_spend(d), str(d.get("target"))))
    taken = {id(d) for d in actionable}
    paused_rel, paused_other = [], []
    for d in decisions:
        if id(d) in taken or d.get("target_status") != "PAUSED":
            continue
        (paused_rel if _paused_relevant(d) else paused_other).append(d)
    taken |= {id(d) for d in paused_rel + paused_other}
    paused_rel.sort(key=lambda d: (bool(d.get("underlying_candidate_action")) is False, -_spend(d), str(d.get("target"))))
    delivery = []
    for d in decisions:
        if id(d) in taken:
            continue
        it = delivery_item(d, parse_detail)
        if it:
            delivery.append(it)
            taken.add(id(d))
    delivery.sort(key=lambda x: (["NO_DELIVERY", "DECLINING", "VERY_LOW"].index(x["klass"]), -(x["decision"].get("lifetime_clicks") or 0), x["target"]))
    monitor, good, other = [], [], []
    for d in decisions:
        if id(d) in taken:
            continue
        fa = d.get("final_action")
        if fa == "WATCH" and ((d.get("lifetime_clicks") or 0) >= WATCH_MIN_CLICKS or _spend(d) >= WATCH_MIN_SPEND):
            monitor.append(d)
        elif fa == "HOLD" and _weak_recent(d, book):
            monitor.append(d)
        elif fa == "HOLD" and ((d.get("lifetime_orders") or 0) >= GOOD_MIN_ORDERS or _spend(d) >= GOOD_MIN_SPEND or _profitable_small(d, book)):
            good.append(d)
        else:
            other.append(d)
    monitor.sort(key=lambda d: (-_spend(d), str(d.get("target"))))
    good.sort(key=lambda d: (-(d.get("lifetime_orders") or 0), str(d.get("target"))))
    exact_items = opportunity_items(opp["records"], PROMOTE, symbol) if opp else None
    neg_items = opportunity_items(opp["records"], NEGATIVE, symbol) if opp else None
    conflicts = conflict_items(opp["records"], symbol) if opp else None
    already_neg = already_negative_items(opp["records"]) if opp else None
    promoted = promoted_items(opp["records"]) if opp else None
    cards = {"actions": [decision_card(d, book) for d in actionable], "paused": [paused_card(d, book) for d in paused_rel],
             "delivery": delivery, "monitor": [monitor_card(d, book) for d in monitor], "good": [good_card(d, book) for d in good]}
    others = [other_row(d) for d in paused_other + other]
    state_of = {c["id"]: c["badge"] for k in ("actions", "paused", "monitor", "good") for c in cards[k]}          # etichetta con cui la pagina mostra il target
    products = product_winner_items(opp["records"], decisions, state_of) if opp else None
    kpi = {"actions": len(cards["actions"]) if has_flag else None, "exact": None if exact_items is None else len(exact_items),
           "negatives": None if neg_items is None else len(neg_items), "paused": len(cards["paused"]), "warnings": len(warnings),
           "delivery": len(delivery)}
    if conflicts:
        kpi["conflicts"] = len(conflicts)                                    # KPI separato: solo se esistono conflitti
    new_campaign = bool(opp and opp.get("new_exact_campaign"))
    by_word = {}
    for c in cards["actions"]:
        by_word[c["badge"]] = by_word.get(c["badge"], 0) + 1
    names = {d["amazon_account_id"]: d.get("amazon_account_name") for d in decisions if d.get("amazon_account_id") and d.get("amazon_account_name")}
    names.update(account_names or {})
    multi = ambiguous_campaign_types(decisions)
    for lst in cards.values():
        for c in lst:
            dd = c.get("decision") or {}
            c["account"] = account_text(dd.get("amazon_account_id"), names)
            c["campaign_line"] = campaign_text(dd.get("campaign_name"), dd.get("amazon_campaign_id"), internal_id=dd.get("campaign_id"),
                                               show_legacy=str(dd.get("campaign_type") or "") in multi)
    for lst in (exact_items, neg_items, conflicts, promoted, products):
        for it in lst or []:
            rr = it.get("record") or {}
            it["account"] = account_text(rr.get("amazon_account_id"), names)
            it["campaign_line"] = campaign_text(rr.get("campaign_name"), rr.get("amazon_campaign_id"), internal_id=rr.get("campaign_id"),
                                                show_legacy=str(rr.get("campaign_type") or "") in multi)
    return {"has_actionable_field": has_flag, "kpi": kpi, **cards, "delivery_shown": delivery[:MAX_DELIVERY_HOME], "exact": exact_items, "exact_shown": (exact_items or [])[:MAX_ITEMS],
            "exact_new_campaign": new_campaign, "exact_suggestion": (opp or {}).get("new_exact_campaign"), "negatives": neg_items,
            "conflicts": conflicts, "already_negative": already_neg, "promoted": promoted, "products": products,
            "negatives_shown": (neg_items or [])[:MAX_ITEMS], "others": others, "warnings": warnings, "actions_by_word": by_word, "rules": RULES_TEXT,
            "all_ids": [c["id"] for k in ("actions", "paused", "delivery", "monitor", "good") for c in cards[k]] + [r["id"] for r in others]}
