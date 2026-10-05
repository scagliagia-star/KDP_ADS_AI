"""SEARCH TERM OPPORTUNITIES: layer SEPARATO dalle decisioni di bid. MANUAL DECISION SUPPORT: non modifica Amazon, non crea campagne,
non aggiunge keyword ne' negative, non scrive nel database (solo calcolo in memoria su dati gia' importati).

Principio: un search term e' una query REALE dell'utente. Non va confuso con la keyword target che l'ha catturato ne' con un gruppo di
targeting automatico (loose-match, close-match, complements, substitutes), che NON sono mai suggeriti come EXACT o negative.

Opportunity type: PROMOTE_TO_EXACT | NEGATIVE_CANDIDATE | WATCH_SEARCH_TERM | NO_ACTION. Ogni record ha operationally_actionable = False e
manual_review_required = True.

REGOLE EFFETTIVE (tutte in DEFAULTS, sovrascrivibili con la chiave opzionale rules["opportunities"]; rules.json NON e' modificato):
  Finestre: 30D tattico (ruolo TACTICAL, <= snapshots.tactical_max_days) = evidenza principale; 65D (RECENT_CONTEXT) = conferma; il lifetime
  non esiste per i Search Term (il Target report e' per target, non per termine). Le finestre sono nidificate/sovrapposte: MAI sommate;
  ognuna e' valutata da sola. Dentro la STESSA campagna e finestra, le righe dello stesso termine con target padre diversi sono invece
  disgiunte e si sommano. Fra campagne diverse i record restano separati (campo also_seen_in).
  Classe per finestra:
    PROMO_HIGH   >= 3 ordini e ACOS <= target ACOS                      (confidence HIGH)
    PROMO_MEDIUM >= 2 ordini e ACOS <= break-even ACOS                  (confidence MEDIUM)
    NEG_ZERO     0 ordini, >= 10 clic (zero_orders.sample_clicks_min) e spesa >= 2x royalty (zero_orders.negative_spend_mult)
    NEG_ACOS     2..9 ordini, >= 15 clic e ACOS > banda 'unprofitable' (>60%: fortemente sopra il break-even); >= 10 ordini mai negative
    WATCH_GOOD   >= 1 ordine con ACOS <= break-even ma sotto le soglie PROMO (promettente, pochi dati)
    WATCH_BAD    0 ordini con >= 3 clic, oppure ordini con ACOS > break-even, ma sotto le soglie NEG (debole, campione insufficiente)
    NONE         tutto il resto (es. 1-2 clic senza ordini)
  Confidence PROMO: dalla classe. Confidence NEG: calculate_confidence() gia' usata dal motore (0 ordini: HIGH >= 25 clic, MEDIUM >= 10).
  Combinazione 30D/65D: se le finestre concordano E la conferma porta ordini in piu' (indipendenti dal 30D) la confidence sale di un
  livello; se discordano scende di un livello. Se la confidence finale e' LOW il suggerimento diventa WATCH. Se il 30D non e' stato
  importato per la campagna, si usa il 65D con confidence ridotta di un livello; se il 30D e' importato ma il termine non vi compare
  (nessuna conferma recente) il suggerimento e' al massimo WATCH.
  PROMOTE_TO_EXACT: solo termini reali (non ASIN, non gruppi Auto), non gia' keyword EXACT nello stesso libro + marketplace
  (se gia' EXACT: NO_ACTION con flag ALREADY_IN_EXACT). Gia' in PHRASE/altre campagne: puo' comunque essere promosso (already_targeted_elsewhere).
  NEGATIVE_CANDIDATE: solo su search term reali, mai sul nome del target padre (termine == target padre => nessuna negative); tipo
  (exact/phrase) NON deciso.
  Campagna EXACT nuova (non operativa): se >= 3 termini unici PROMOTE_TO_EXACT da PHRASE/AUTO/BROAD e nel marketplace non c'e' nessuna
  campagna EXACT: 'Valuta creazione campagna EXACT'. N = 3 = la soglia di ordini della confidence HIGH: un gruppo minimo di tre termini
  forti giustifica una campagna dedicata; con 1-2 termini basta aggiungerli a una campagna esistente."""
import re
from collections import defaultdict

from .metrics import AUTO_TARGET_GROUPS, compute_metrics, normalize_term, product_asin, target_type, term_matches_target
from .rules import BookCtx, CONF_ORDER, calculate_confidence, deep_merge
from .snapshots import days_inclusive, period_id as make_period_id, window_label, window_role

PROMOTE, NEGATIVE, WATCH, NO_ACTION = "PROMOTE_TO_EXACT", "NEGATIVE_CANDIDATE", "WATCH_SEARCH_TERM", "NO_ACTION"
REVIEW_CONFLICT = "REVIEW_NEGATIVE_CONFLICT"                     # termine che vende ma gia' negativo: solo verifica manuale
OPPORTUNITY_TYPES = (PROMOTE, NEGATIVE, WATCH, NO_ACTION, REVIEW_CONFLICT)
SUGGESTION_NEW_EXACT = "Valuta creazione campagna EXACT"
DISCOVERY_TYPES = frozenset({"PHRASE", "BROAD", "AUTO", "OTHER"})
_CONF = ["LOW", "MEDIUM", "HIGH"]
_ASIN_LIKE = re.compile(r"^b0[a-z0-9]{8}$")

DEFAULTS = {
    "promote_high_min_orders": 3,            # + ACOS <= target ACOS
    "promote_medium_min_orders": 2,          # + ACOS <= break-even ACOS
    "negative_zero_min_clicks": None,        # None = zero_orders.sample_clicks_min (10)
    "negative_zero_spend_mult": None,        # None = zero_orders.negative_spend_mult (2.0) x royalty
    "negative_acos_min_orders": 2,
    "negative_acos_min_clicks": 15,
    "watch_min_clicks": 3,                   # 0 ordini sotto questa soglia = NO_ACTION; negative mai con 2-3 clic
    "new_exact_campaign_min_terms": 3,
}


def _cfg(ctx):
    c = deep_merge(DEFAULTS, ctx.rules.get("opportunities") or {})
    z = ctx.rules["zero_orders"]
    if c["negative_zero_min_clicks"] is None:
        c["negative_zero_min_clicks"] = z["sample_clicks_min"]
    if c["negative_zero_spend_mult"] is None:
        c["negative_zero_spend_mult"] = z["negative_spend_mult"]
    return c


def make_ctx(book: dict, rules: dict) -> BookCtx:
    """BookCtx minimo dall'economia del marketplace (royalty, target ACOS, break-even): nessun valore hardcodato."""
    return BookCtx(str(book["book_id"]), book.get("title") or "", str(book.get("marketplace") or ""), book.get("currency") or "USD",
                   float(book.get("price") or 0), float(book["royalty_per_copy"]), float(book["target_acos"]),
                   float(book["break_even_acos"]), book.get("phase") or "", rules)


def normalize_added_as(raw):
    """Normalizza il campo Amazon "Aggiunto come" (valore originale MAI modificato). Valori: "Parola chiave: a frase",
    "Parola chiave: esatta", "Parola chiave neg.: esatta neg.", "Parola chiave neg.: frase neg.", combinazioni con virgola
    ("a frase, esatta neg."). Ritorna {keyword_exact, keyword_phrase, negative_exact, negative_phrase} (bool)."""
    out = {"keyword_exact": False, "keyword_phrase": False, "negative_exact": False, "negative_phrase": False}
    if not raw:
        return out
    for tok in str(raw).lower().split(","):
        tok = tok.strip()
        neg = "neg" in tok
        body = tok.split(":", 1)[1] if ":" in tok else tok
        exact = "esatt" in body or "exact" in body
        phrase = "frase" in body or "phrase" in body
        if exact:
            out["negative_exact" if neg else "keyword_exact"] = True
        if phrase:
            out["negative_phrase" if neg else "keyword_phrase"] = True
    return out


def negative_state(raws):
    """Stato negativa di un (campagna, termine) dai valori "Aggiunto come" di tutte le sue righe: 'EXACT', 'PHRASE' o None."""
    flags = [normalize_added_as(x) for x in raws]
    if any(f["negative_exact"] for f in flags):
        return "EXACT"
    if any(f["negative_phrase"] for f in flags):
        return "PHRASE"
    return None


def is_real_query(term):
    """True per una query digitata dall'utente: non vuota, non un ASIN, non il nome di un gruppo di targeting automatico."""
    t = normalize_term(term)
    return bool(t) and not product_asin(t) and not _ASIN_LIKE.match(t) and t not in AUTO_TARGET_GROUPS and not t.startswith(("asin=", "category="))


# ------------------------------------------------------------------ valutazione di una finestra
def _eval_window(rows, ctx, cfg):
    """Metriche + classe di UNA finestra (righe dello stesso termine/campagna/periodo: disgiunte, quindi sommabili)."""
    m = compute_metrics(sum(r["impressions"] or 0 for r in rows), sum(r["clicks"] or 0 for r in rows), sum(r["spend"] or 0 for r in rows),
                        sum(r["orders"] or 0 for r in rows), sum(r["sales"] or 0 for r in rows), ctx.royalty)
    r0 = rows[0]
    R, rules, b = ctx.royalty, ctx.rules, ctx.bands
    cls, conf = "NONE", "LOW"
    if m.orders >= cfg["promote_high_min_orders"] and m.acos is not None and m.acos <= ctx.target_acos:
        cls, conf = "PROMO_HIGH", "HIGH"
    elif m.orders >= cfg["promote_medium_min_orders"] and m.acos is not None and m.acos <= ctx.break_even_acos:
        cls, conf = "PROMO_MEDIUM", "MEDIUM"
    elif (m.orders == 0 and m.clicks >= cfg["negative_zero_min_clicks"] and m.spend >= cfg["negative_zero_spend_mult"] * R):
        cls, conf = "NEG_ZERO", calculate_confidence(m.orders, m.clicks, m.spend, R, rules)
    elif (cfg["negative_acos_min_orders"] <= m.orders < rules["with_orders"]["high_volume_orders"] and m.clicks >= cfg["negative_acos_min_clicks"]
          and m.acos is not None and m.acos > b["unprofitable"]):
        cls, conf = "NEG_ACOS", calculate_confidence(m.orders, m.clicks, m.spend, R, rules)
    elif m.orders >= 1 and m.acos is not None and m.acos <= ctx.break_even_acos:
        cls, conf = "WATCH_GOOD", calculate_confidence(m.orders, m.clicks, m.spend, R, rules)
    elif (m.orders == 0 and m.clicks >= cfg["watch_min_clicks"]) or (m.orders >= 1 and (m.acos is None or m.acos > ctx.break_even_acos)):
        cls, conf = "WATCH_BAD", calculate_confidence(m.orders, m.clicks, m.spend, R, rules)
    days = days_inclusive(r0["date_from"], r0["date_to"]) if r0["report_scope"] == "DATE_RANGE" else None
    return {"period_id": r0.get("period_id") or make_period_id(r0["report_scope"], r0["date_from"], r0["date_to"]),
            "window": window_label(r0["report_scope"], r0["date_from"], r0["date_to"]), "days": days,
            "impressions": m.impressions, "clicks": m.clicks, "orders": m.orders, "spend": m.spend, "sales": m.sales,
            "acos": m.acos, "cvr": m.cvr, "cpc": m.cpc, "estimated_profit": m.est_profit, "cls": cls, "confidence": conf}


def _dir(w):
    if w is None:
        return "NONE"
    return "POS" if w["cls"] in ("PROMO_HIGH", "PROMO_MEDIUM", "WATCH_GOOD") else "NEG" if w["cls"] in ("NEG_ZERO", "NEG_ACOS", "WATCH_BAD") else "NONE"


def _up(c):
    return _CONF[min(2, CONF_ORDER[c] + 1)]


def _down(c):
    return _CONF[max(0, CONF_ORDER[c] - 1)]


# ------------------------------------------------------------------ finestre di una campagna
def campaign_windows(st_rows, snap_cfg):
    """{campaign_id: {'tactical': period_id|None, 'confirm': period_id|None}} dalle finestre Search Term importate (mai sommate)."""
    seen = defaultdict(dict)
    for r in st_rows:
        pid = r.get("period_id") or make_period_id(r["report_scope"], r["date_from"], r["date_to"])
        seen[r["campaign_id"]][pid] = (window_role(r["report_scope"], r["date_from"], r["date_to"], snap_cfg),
                                       days_inclusive(r["date_from"], r["date_to"]) if r["report_scope"] == "DATE_RANGE" else None, r["date_to"] or "")
    out = {}
    for cid, per in seen.items():
        tac = sorted((p for p, (role, _, _) in per.items() if role == "TACTICAL"), key=lambda p: (per[p][2], -(per[p][1] or 0)), reverse=True)
        con = sorted((p for p, (role, _, _) in per.items() if role == "RECENT_CONTEXT"), key=lambda p: (per[p][1] or 0, per[p][2]), reverse=True)
        out[cid] = {"tactical": tac[0] if tac else None, "confirm": con[0] if con else None}
    return out


# ------------------------------------------------------------------ motore
def compute_opportunities(st_rows, target_rows, campaigns, ctx: BookCtx, status_of=None) -> dict:
    """st_rows/target_rows: righe DB di UN libro e UN marketplace (gia' filtrate). campaigns: [{campaign_id, campaign_type}].
    status_of(raw_json) -> 'ENABLED'/'PAUSED'/'UNKNOWN' (opzionale, per segnalare keyword EXACT in pausa).
    Ritorna {'records': [...], 'excluded': {...}, 'new_exact_campaign': {...}|None, 'config': {...}}."""
    cfg = _cfg(ctx)
    ctype = {c["campaign_id"]: str(c.get("campaign_type") or "").upper() for c in campaigns}
    for r in list(st_rows) + list(target_rows):
        ctype.setdefault(r["campaign_id"], str(r.get("campaign_type") or "").upper())
    wins = campaign_windows(st_rows, ctx.rules["snapshots"])

    # keyword target esistenti (mai ASIN/categorie/gruppi Auto)
    exact_kw, any_kw = defaultdict(list), defaultdict(list)
    for t in target_rows:
        ct = ctype.get(t["campaign_id"], "")
        if target_type(t["target"], ct) != "KEYWORD_TARGET":
            continue
        key = normalize_term(t["target"])
        st_ = status_of(t.get("raw_json")) if status_of else "UNKNOWN"
        entry = (t["campaign_id"], st_)
        if entry not in any_kw[key]:
            any_kw[key].append(entry)
        if ct == "EXACT" or normalize_term(t.get("match_type")) in ("exact", "esatta"):
            if entry not in exact_kw[key]:
                exact_kw[key].append(entry)

    # raggruppa per (campagna, termine) e periodo
    excluded = defaultdict(int)
    groups = defaultdict(lambda: defaultdict(list))             # (campaign, term) -> period -> rows
    added, ad_groups = defaultdict(set), defaultdict(set)       # "Aggiunto come" e gruppi annunci per (campagna, termine)
    for r in st_rows:
        term = normalize_term(r["search_term"])
        if not is_real_query(r["search_term"]):
            excluded["non_query" if term else "vuoto"] += 1
            continue
        pid = r.get("period_id") or make_period_id(r["report_scope"], r["date_from"], r["date_to"])
        groups[(r["campaign_id"], term)][pid].append({**r, "period_id": pid})
        key = (r["campaign_id"], term)
        if r.get("added_as"):
            added[key].add(str(r["added_as"]).strip())
        if r.get("ad_group"):
            ad_groups[key].add(str(r["ad_group"]).strip())

    records = []
    for (cid, term), per in sorted(groups.items()):
        w = wins.get(cid, {"tactical": None, "confirm": None})
        T = _eval_window(per[w["tactical"]], ctx, cfg) if w["tactical"] in per else None
        C = _eval_window(per[w["confirm"]], ctx, cfg) if w["confirm"] in per else None
        if T is None and C is None:                              # finestre fuori dai ruoli tattico/conferma: nessuna valutazione
            excluded["finestra_non_valutabile"] += 1
            continue
        parents = defaultdict(float)
        for r in (per[w["tactical"]] if w["tactical"] in per else per[w["confirm"]]):
            parents[r["target"]] += r["clicks"] or 0
        parent = max(parents, key=lambda k: (parents[k], str(k)))
        rec = _decide(cid, ctype.get(cid, ""), term, parent, sorted(parents, key=str), T, C, w, ctx, cfg, exact_kw, any_kw,
                      added_as=sorted(added[(cid, term)]), ad_groups=sorted(ad_groups[(cid, term)]))
        records.append(rec)

    seen_in = defaultdict(set)
    for r in records:
        seen_in[r["search_term"]].add(r["campaign_id"])
    for r in records:
        others = sorted(seen_in[r["search_term"]] - {r["campaign_id"]})
        r["also_seen_in"] = others
        if others:
            r["flags"].append("DUPLICATE_ACROSS_CAMPAIGNS")
    return {"records": records, "excluded": dict(excluded), "config": cfg,
            "new_exact_campaign": new_exact_campaign_suggestion(records, campaigns, ctype, cfg)}


def _decide(cid, ctype_, term, parent, parents, T, C, w, ctx, cfg, exact_kw, any_kw, added_as=(), ad_groups=()):
    flags, notes = [], []
    negative = negative_state(added_as)                         # negativa gia' impostata, valutata solo in QUESTA campagna
    primary = T or C
    in_exact = exact_kw.get(term, [])
    elsewhere = [e for e in any_kw.get(term, []) if e[0] != cid]
    already_exact = bool(in_exact)
    if already_exact:
        flags.append("ALREADY_IN_EXACT")
        if all(st == "PAUSED" for _, st in in_exact):
            flags.append("EXACT_PAUSED")
    if len(parents) > 1:
        flags.append("MULTI_PARENT_TARGET")
    term_is_parent = term_matches_target(term, parent, ctype_) or normalize_term(parent) == term
    has_tactical_window = w["tactical"] is not None
    otype, conf, agree, base = NO_ACTION, primary["confidence"], "SOLO_UNA_FINESTRA", primary
    why = []

    def combine(main, other, same_dir, field):
        """concordanza fra la finestra principale e l'altra: ritorna (confidence, etichetta). La confidence sale solo se la
        conferma porta evidenza in piu' (ordini per le promozioni, clic per le negative): le finestre sono nidificate."""
        if other is None:
            return main["confidence"], "SOLO_UNA_FINESTRA"
        extra = other[field] > main[field]
        if same_dir(other):
            return (_up(main["confidence"]) if extra else main["confidence"]), "CONCORDI"
        return _down(main["confidence"]), "DISCORDI"

    promo = lambda x: x is not None and x["cls"] in ("PROMO_HIGH", "PROMO_MEDIUM")
    neg = lambda x: x is not None and x["cls"] in ("NEG_ZERO", "NEG_ACOS")
    if promo(T) or promo(C):
        if promo(T):
            base = T
            conf, agree = combine(T, C, lambda o: _dir(o) == "POS", "orders")
            why.append(f"30D: {T['orders']:g} ordini, ACOS {_acos(T)} ({'<= ACOS obiettivo' if T['cls'] == 'PROMO_HIGH' else '<= pareggio'})")
        else:                                                       # promo solo nel 65D
            base = C
            if not has_tactical_window:
                conf, agree = _down(C["confidence"]), "SOLO_65D"
                why.append("finestra 30D non importata: valutato solo sul 65D, confidence ridotta")
            elif T is None or _dir(T) == "NONE":
                conf, agree = "LOW", "NON_CONFERMATO_30D"
                why.append("ordini nel 65D ma nessuna conferma nel 30D (termine assente o senza attivita' recente)")
            elif _dir(T) == "POS":
                conf, agree = _down(C["confidence"]), "CONCORDI"
                why.append("65D forte; il 30D e' nella stessa direzione ma con pochi dati")
            else:
                conf, agree = "LOW", "DISCORDI"
                why.append("65D forte ma il 30D e' negativo: finestre discordanti")
        if conf == "LOW":
            otype = WATCH
            why.append("confidence finale insufficiente per promuovere")
        elif already_exact:
            otype = NO_ACTION
            why.append("gia' presente come keyword EXACT" + (" (in pausa)" if "EXACT_PAUSED" in flags else "") + ": nessuna promozione")
        else:
            otype = PROMOTE
            if elsewhere:
                why.append("gia' target altrove (" + ", ".join(sorted({e[0] for e in elsewhere})) + "): la promozione resta possibile")
    elif neg(T) or neg(C):
        if term_is_parent:
            otype, why = WATCH, ["il termine coincide con il target padre: nessuna negative sul nome del target"]
        else:
            if neg(T):
                base = T
                conf, agree = combine(T, C, lambda o: _dir(o) == "NEG", "clicks")
                why.append(f"30D: {'0 ordini' if T['cls'] == 'NEG_ZERO' else str(round(T['orders'])) + ' ordini con ACOS ' + _acos(T)}, {T['clicks']:g} clic, spesa {T['spend']:.2f}")
            else:
                base = C
                if not has_tactical_window:
                    conf, agree = _down(C["confidence"]), "SOLO_65D"
                    why.append("finestra 30D non importata: valutato solo sul 65D, confidence ridotta")
                elif T is None or _dir(T) == "NONE":
                    conf, agree = "LOW", "NON_CONFERMATO_30D"
                    why.append("spreco nel 65D ma nessuna attivita' recente: nessuna urgenza")
                elif _dir(T) == "NEG":
                    conf, agree = C["confidence"], "CONCORDI"
                    why.append("65D debole e il 30D conferma la direzione")
                else:
                    conf, agree = "LOW", "DISCORDI"
                    why.append("65D debole ma il 30D e' positivo: finestre discordanti")
            if conf == "LOW":
                otype = WATCH
                why.append("confidence finale insufficiente per una negative")
            else:
                otype = NEGATIVE
                why.append("tipo di negative (exact/phrase) non deciso")
    elif any(x is not None and x["cls"] in ("WATCH_GOOD", "WATCH_BAD") for x in (T, C)):
        otype = WATCH
        base = next(x for x in (T, C) if x is not None and x["cls"] in ("WATCH_GOOD", "WATCH_BAD"))
        agree = "SOLO_UNA_FINESTRA" if (T is None or C is None) else ("CONCORDI" if _dir(T) == _dir(C) else "DISCORDI")
        conf = base["confidence"]
        why.append("promettente ma pochi dati" if base["cls"] == "WATCH_GOOD" else "debole ma campione insufficiente per una negative")
    else:
        why.append("nessun segnale (pochi clic, nessun ordine)")
    if term_is_parent:
        flags.append("TERM_EQUALS_PARENT_TARGET")
    if negative:
        kind = "esatta" if negative == "EXACT" else "a frase"
        flags.append("NEGATIVE_EXACT" if negative == "EXACT" else "NEGATIVE_PHRASE")
        if promo(T) or promo(C):                                # vende ma e' gia' negativo: mai promozione, solo verifica
            otype = REVIEW_CONFLICT
            flags.append("NEGATIVE_CONFLICT")
            why.append(f"il termine vende ma risulta gia' impostato come negativa {kind} in questa campagna: verificare prima di promuovere")
        else:
            if otype == NEGATIVE:
                why.append(f"gia' negativa {kind} in questa campagna: nessuna nuova negativa")
            otype = NO_ACTION
            flags.append("ALREADY_NEGATIVE")
    return {
        "book_id": ctx.book_id, "marketplace": ctx.marketplace, "campaign_id": cid, "campaign_type": ctype_,
        "parent_target": parent, "parent_targets": parents, "parent_target_type": target_type(parent, ctype_),
        "search_term": term, "period_id": base["period_id"], "primary_window": base["window"],
        "clicks": base["clicks"], "spend": base["spend"], "orders": base["orders"], "sales": base["sales"], "acos": base["acos"],
        "cvr": base["cvr"], "cpc": base["cpc"], "estimated_profit": base["estimated_profit"],
        "window_30d": T, "window_65d": C, "windows_agreement": agree,
        "opportunity_type": otype, "confidence": conf, "reason": "; ".join(why) + ".", "flags": flags,
        "already_in_exact": already_exact, "already_targeted_elsewhere": bool(elsewhere),
        "targeted_in": sorted({e[0] for e in elsewhere}), "also_seen_in": [],
        "added_as_raw": list(added_as), "added_as_normalized": [k for k, v in _merge_added(added_as).items() if v],
        "negative_match": negative, "ad_groups": list(ad_groups),
        "operationally_actionable": False, "manual_review_required": True,
    }


def _merge_added(raws):
    out = normalize_added_as(None)
    for x in raws:
        for k, v in normalize_added_as(x).items():
            out[k] = out[k] or v
    return out


def _acos(w):
    return "n/d" if w["acos"] is None else f"{w['acos']:.1f}%"


def new_exact_campaign_suggestion(records, campaigns, ctype, cfg):
    """Suggerimento strutturale NON operativo. Termini UNICI PROMOTE_TO_EXACT da campagne di discovery; nessuna campagna EXACT nel
    marketplace (i dati sono gia' filtrati per marketplace)."""
    terms = sorted({r["search_term"] for r in records if r["opportunity_type"] == PROMOTE and r["campaign_type"] in DISCOVERY_TYPES})
    has_exact = any(str(c.get("campaign_type") or "").upper() == "EXACT" for c in campaigns) or "EXACT" in ctype.values()
    n = cfg["new_exact_campaign_min_terms"]
    if len(terms) >= n and not has_exact:
        return {"suggestion": SUGGESTION_NEW_EXACT, "terms": terms, "min_terms": n, "operational": False}
    return None


def unique_terms(records, otype):
    """Termini UNICI per tipo (lo stesso termine in PHRASE e AUTO conta una volta)."""
    return sorted({r["search_term"] for r in records if r["opportunity_type"] == otype})


def rank(records, otype):
    """Ordinamento di presentazione: confidence, poi ordini e vendite (promozioni) / spesa (negative)."""
    rows = [r for r in records if r["opportunity_type"] == otype]
    if otype == NEGATIVE:
        return sorted(rows, key=lambda r: (-CONF_ORDER[r["confidence"]], -r["spend"], r["search_term"]))
    return sorted(rows, key=lambda r: (-CONF_ORDER[r["confidence"]], -r["orders"], -r["sales"], r["search_term"]))


# ------------------------------------------------------------------ lettura dal database (SOLA LETTURA)
def load_inputs(conn, book_id, marketplace, legacy_marketplace=None):
    """Righe Search Term / Target e campagne di UN libro e UN marketplace da una connessione sqlite (aperta in sola lettura dal chiamante).
    Le righe storiche senza marketplace appartengono al marketplace legacy del libro. Nessuna scrittura."""
    def q(sql, args):
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    mp = str(marketplace).upper() if marketplace else None
    legacy = str(legacy_marketplace).upper() if legacy_marketplace else None

    def clause(table):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not mp or "marketplace" not in have:
            return "", ()
        return " AND (UPPER(marketplace)=? OR (marketplace IS NULL AND ?=?))", (mp, mp, legacy)
    out = {}
    for key, table, cols in (("st_rows", "search_term_rows", "campaign_id, campaign_type, target, match_type, search_term, report_scope, date_from, date_to, period_id, impressions, clicks, spend, orders, sales{extra}"),
                             ("target_rows", "target_rows", "campaign_id, campaign_type, target, match_type, report_scope, date_from, date_to, period_id, raw_json")):
        sql, args = clause(table)
        if key == "st_rows":
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            cols = cols.replace("{extra}", "".join(f", {c}" for c in ("ad_group", "added_as") if c in have))
        out[key] = q(f"SELECT {cols} FROM {table} WHERE book_id=?{sql}", (book_id, *args))
    sql, args = clause("campaigns")
    out["campaigns"] = q(f"SELECT campaign_id, campaign_type FROM campaigns WHERE book_id=?{sql}", (book_id, *args))
    return out
