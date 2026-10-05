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
  STA ANDANDO BENE: HOLD con >= GOOD_MIN_ORDERS acquisti storici o spesa >= GOOD_MIN_SPEND (le minori finiscono in ALTRE DECISIONI)."""
import re

from kdp_ads.opportunities import NEGATIVE, NO_ACTION, PROMOTE, REVIEW_CONFLICT, rank
from kdp_ads.snapshots import days_inclusive

LOW_IMPRESSIONS_30D = 30
DECLINE_RATIO = 0.30
DECLINE_MIN_REF_PER_DAY = 20.0
PAUSED_MIN_SPEND = 10.0
PAUSED_MIN_CLICKS = 10
WATCH_MIN_CLICKS = 10
WATCH_MIN_SPEND = 10.0
GOOD_MIN_ORDERS = 5
GOOD_MIN_SPEND = 30.0
MAX_ITEMS = 10                                    # elementi mostrati per EXACT / negative

RULES_TEXT = {
    "erogazione": (f"Target ATTIVO con 0 impressioni negli ultimi 30 giorni: nessuna erogazione; da 1 a {LOW_IMPRESSIONS_30D}: erogazione molto bassa; "
                   f"segnale del target in calo con rapporto ≤ {DECLINE_RATIO:g} rispetto al riferimento (almeno {DECLINE_MIN_REF_PER_DAY:g} impressioni/giorno di "
                   "riferimento): erogazione in calo. Non si suggerisce mai di alzare l'offerta solo perché non eroga."),
    "pausa": (f"Target in pausa con azione potenziale, oppure spesa ≥ {PAUSED_MIN_SPEND:g} o ≥ {PAUSED_MIN_CLICKS} clic."),
    "monitora": (f"Decisioni di monitoraggio con ≥ {WATCH_MIN_CLICKS} clic o spesa ≥ {WATCH_MIN_SPEND:g}, e target in mantenimento con rendimento recente peggiorato."),
    "bene": f"Target in mantenimento con ≥ {GOOD_MIN_ORDERS} acquisti storici o spesa ≥ {GOOD_MIN_SPEND:g}.",
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


def short_reason(d: dict, book: dict) -> str:
    """Motivo breve in italiano semplice, costruito dai numeri della decisione (mai codici tecnici)."""
    fa, tgt = d.get("final_action"), book.get("target_acos")
    life, rec, tac = d.get("lifetime_acos"), d.get("recent_acos"), d.get("tactical_acos")
    rd, td = window_days(d.get("recent_window"), 65), window_days(d.get("tactical_window"), 30)
    orders = d.get("lifetime_orders")
    sig = d.get("recent_vs_historical_signal")
    if fa in ("BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE", "BID_DOWN_15_CANDIDATE"):
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
        return "Era inefficiente, ma è già fermo. Nessuna azione necessaria."
    if fa == "WATCH":
        if sig == "DECLINING_SIGNAL" or (life is not None and tgt is not None and life <= tgt and tac is not None and tac > tgt):
            return "Storicamente forte, ma il rendimento recente è peggiorato."
        if (life is not None and tgt is not None and life > tgt) or (tac is not None and tgt is not None and tac > tgt):
            return f"Rendimento sotto l'obiettivo ma con pochi dati ({plural(orders or 0, 'acquisto', 'acquisti')} nello storico): tienilo d'occhio."
        return "Dati ancora insufficienti per agire: tienilo d'occhio."
    if fa == "HOLD":
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
    if cls is None and d.get("delivery_source") == "TARGET" and d.get("delivery_signal") in ("DELIVERY_COLLAPSED", "DELIVERY_DECLINING"):
        r = ref["ratio"]
        if r is not None and r <= DECLINE_RATIO and (ref["per_day"] or 0) >= DECLINE_MIN_REF_PER_DAY:
            cls = "DECLINING"
    if cls is None:
        return None
    if cls == "NO_DELIVERY":
        reason = "Il target è attivo ma non sta ricevendo traffico. Verifica offerta e pertinenza."
    elif cls == "VERY_LOW":
        reason = f"Il target è attivo ma riceve pochissime impressioni ({it_int(imp)} negli ultimi {days} giorni). Verifica offerta e pertinenza."
    else:
        reason = (f"Le impressioni sono calate molto rispetto al riferimento (×{str(round(ref['ratio'], 2)).replace('.', ',')}). "
                  "Verifica offerta, concorrenza e stato della campagna.")
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
        return base + " e nessun acquisto."
    return base + f", {plural(r['orders'], 'acquisto', 'acquisti')} con ACOS {it_pct(r.get('acos'))}: rendimento molto sotto il pareggio."


def conflict_reason(r: dict) -> str:
    kind = "negativa esatta" if r.get("negative_match") == "EXACT" else "negativa a frase"
    camp = r.get("campaign_id")
    return (f"{plural(r.get('orders') or 0, 'acquisto', 'acquisti')} e ACOS {it_pct(r.get('acos'))}, ma il termine risulta già impostato come "
            f"{kind} nella campagna {camp}.")


CONFLICT_TEXT = ("Questo termine sta vendendo, ma risulta già impostato come negativa esatta. "
                 "Verifica la configurazione prima di promuoverlo.")
CONFLICT_TEXT_PHRASE = ("Questo termine sta vendendo, ma risulta già impostato come negativa a frase. "
                        "Verifica la configurazione prima di promuoverlo.")
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
                    "text": CONFLICT_TEXT if r.get("negative_match") == "EXACT" else CONFLICT_TEXT_PHRASE, "reason": conflict_reason(r),
                    "hint": CONFLICT_HINT, "record": r})
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
        out.append({"kind": "EXACT" if otype == PROMOTE else "NEGATIVE", "badge": "METTI IN EXACT" if otype == PROMOTE else "VALUTA COME NEGATIVA",
                    "term": r["search_term"], "campaign": r["campaign_id"], "also_in": also, "parent": target_name(r.get("parent_target")),
                    "parents": [target_name(p) for p in r.get("parent_targets") or []], "origin": _origin(r), "clicks": r.get("clicks"),
                    "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"), "acos": r.get("acos"), "confidence": r.get("confidence"),
                    "already_in_exact": bool(r.get("already_in_exact")), "reason": exact_reason(r) if otype == PROMOTE else negative_reason(r, symbol),
                    "record": r})
    return out


# ------------------------------------------------------------------ composizione della Panoramica
def _spend(d):
    return d.get("lifetime_spend") or 0.0


def _paused_relevant(d: dict) -> bool:
    return bool(d.get("underlying_candidate_action")) or _spend(d) >= PAUSED_MIN_SPEND or (d.get("lifetime_clicks") or 0) >= PAUSED_MIN_CLICKS


def _weak_recent(d: dict, book: dict) -> bool:
    tgt = book.get("target_acos")
    if d.get("recent_vs_historical_signal") == "DECLINING_SIGNAL":
        return True
    tac = d.get("tactical_acos")
    return tac is not None and tgt is not None and tac > tgt and (d.get("tactical_clicks") or 0) >= 3


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


def build_overview(decisions: list, book: dict, summary: dict, warnings: list, opp: dict | None, parse_detail=None, symbol="$") -> dict:
    """Tutte le sezioni e i KPI. `opp` = risultato di Repo.search_term_opportunities (None se non calcolabile). I KPI sono len() delle liste."""
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
        elif fa == "HOLD" and ((d.get("lifetime_orders") or 0) >= GOOD_MIN_ORDERS or _spend(d) >= GOOD_MIN_SPEND):
            good.append(d)
        else:
            other.append(d)
    monitor.sort(key=lambda d: (-_spend(d), str(d.get("target"))))
    good.sort(key=lambda d: (-(d.get("lifetime_orders") or 0), str(d.get("target"))))
    exact_items = opportunity_items(opp["records"], PROMOTE, symbol) if opp else None
    neg_items = opportunity_items(opp["records"], NEGATIVE, symbol) if opp else None
    conflicts = conflict_items(opp["records"], symbol) if opp else None
    already_neg = already_negative_items(opp["records"]) if opp else None
    cards = {"actions": [decision_card(d, book) for d in actionable], "paused": [paused_card(d, book) for d in paused_rel],
             "delivery": delivery, "monitor": [monitor_card(d, book) for d in monitor], "good": [good_card(d, book) for d in good]}
    others = [other_row(d) for d in paused_other + other]
    kpi = {"actions": len(cards["actions"]) if has_flag else None, "exact": None if exact_items is None else len(exact_items),
           "negatives": None if neg_items is None else len(neg_items), "paused": len(cards["paused"]), "warnings": len(warnings),
           "delivery": len(delivery)}
    if conflicts:
        kpi["conflicts"] = len(conflicts)                                    # KPI separato: solo se esistono conflitti
    new_campaign = bool(opp and opp.get("new_exact_campaign"))
    by_word = {}
    for c in cards["actions"]:
        by_word[c["badge"]] = by_word.get(c["badge"], 0) + 1
    return {"has_actionable_field": has_flag, "kpi": kpi, **cards, "exact": exact_items, "exact_shown": (exact_items or [])[:MAX_ITEMS],
            "exact_new_campaign": new_campaign, "exact_suggestion": (opp or {}).get("new_exact_campaign"), "negatives": neg_items,
            "conflicts": conflicts, "already_negative": already_neg,
            "negatives_shown": (neg_items or [])[:MAX_ITEMS], "others": others, "warnings": warnings, "actions_by_word": by_word, "rules": RULES_TEXT,
            "all_ids": [c["id"] for k in ("actions", "paused", "delivery", "monitor", "good") for c in cards[k]] + [r["id"] for r in others]}
