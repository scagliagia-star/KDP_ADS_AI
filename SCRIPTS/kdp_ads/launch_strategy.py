"""LANCIO NUOVO LIBRO: preset di strategia PHRASE_FIRST_AUTO. MANUAL DECISION SUPPORT, funzioni PURE: nessuna rete, nessuna scrittura, nessuna
campagna Amazon creata, nessun bid applicato (i bid sono solo SUGGERITI).

IL METODO (validato a mano):
  * campagna iniziale PHRASE con una lista MOLTO selezionata (indicativamente 15-20 keyword, mai riempita artificialmente);
  * campagna AUTO in parallelo come discovery iniziale (nessuna keyword);
  * NESSUNA campagna EXACT iniziale: EXACT si alimenta DOPO il lancio dai Search Term reali che vendono (pagina "Opportunità Search Term",
    azione "Valuta promozione in EXACT").
Ogni keyword conserva due informazioni distinte: analytical_classification (EXACT/PHRASE/DISCOVERY dal classificatore) e recommended_launch_match
(sempre PHRASE nel preset): una keyword che il classificatore considera EXACT entra comunque nella campagna PHRASE di lancio.

REGOLE
 1. Candidate: EXACT e PHRASE del piano keyword, piu' le DISCOVERY "forti" (pertinenza e mercato alti). Mai REJECT, mai query del titolo del libro.
 2. LAUNCH_NATURAL: la keyword deve sembrare una ricerca naturale, ANCHE se viene da Publisher Rocket e ha buoni numeri ("short stories or seniors",
    "seniors short story", "short stories for adults women", "short stories seniors", "short story senior", "mystery large print" sono escluse dal
    lancio, restano nei dati). Le keyword KDP scritte dall'utente non vengono escluse da questa euristica.
 3. FAMIGLIE: le varianti quasi identiche (stessi concetti, ordine/singolare/plurale/preposizione diversi) si riducono a UNA: vince la forma piu'
    naturale, poi il punteggio di lancio (volume, competizione, ...). Le altre restano come "varianti note", fuori dalla lista iniziale.
 4. PRIORITA' (launch_priority_score 0-100): pertinenza commerciale 40%, naturalezza 15%, copertura dei concetti centrali 15%, evidenza di mercato
    Publisher Rocket 30% (presenza e piu' file, volume, competitive score, conferma da competitor): la pertinenza pesa piu' del volume.
 5. Ammesse al lancio: >= 3 parole (salvo keyword KDP dell'utente), pertinenza >= 80, priorita' >= 70. Massimo 20; sotto soglia si mostrano meno keyword.
 6. BID SUGGERITO a fasce (valuta del marketplace, mai applicato): TOP_TIER 0.90 (pertinenza >= 90 e mercato >= 65),
    CORE 0.85 (buona pertinenza), BROAD 0.80 (larga, mercato forte) / 0.75 (larga); AUTO 0.60 (piu' prudente).
 7. Famiglie: nella famiglia vince la keyword scritta dall'autore, poi la forma piu' naturale ("short stories for seniors" batte "very short stories for
    seniors" e "senior short story"), poi la priorita'."""
import math
import re
from datetime import datetime

PRESET = "PHRASE_FIRST_AUTO"
CFG = {
    "target_min": 15, "target_max": 20, "min_words": 3, "min_relevance": 80, "min_launch_score": 70,
    "discovery_min_relevance": 90, "discovery_min_market": 70, "discovery_min_concepts": 3,
    "weights": {"relevance": 0.40, "natural": 0.15, "coverage": 0.15, "evidence": 0.30},
    "evidence_weights": {"presence": 0.10, "volume": 0.10, "competition": 0.05, "competitor": 0.05},
    "no_evidence_default": 0.55, "user_keyword_form_bonus": 0.10, "user_keyword_priority_bonus": 5,
    "tiers": {"TOP_TIER": {"min_relevance": 90, "min_market": 65, "bid": 0.90}, "CORE": {"min_relevance": 85, "bid": 0.85},
              "BROAD_STRONG_MARKET": {"min_market": 70, "bid": 0.80}, "BROAD": {"bid": 0.75}},
    "auto_bid": 0.60,
}
TIER_LABELS = {"TOP_TIER": "Top tier", "CORE": "Core", "BROAD_STRONG_MARKET": "Ampia (mercato forte)", "BROAD": "Ampia / discovery", "AUTO": "Auto"}

_HEAD_PL = frozenset("stories mysteries books novels tales".split())
_HEAD_SG = frozenset("story mystery book novel tale fiction".split())
_AUD_PL = frozenset("seniors adults women men readers elders".split())
_AUD_SG_END = frozenset("senior adult elderly".split())
_LEAD_AUD = frozenset("seniors adults".split())
_GENRE_MOD = frozenset("cozy cosy mystery mysteries".split())
_BARE_GENRE = frozenset("mystery mysteries fiction".split())


def _words(k: str) -> list:
    return re.sub(r"[^a-z0-9' ]+", " ", str(k or "").lower()).split()


def launch_natural(keyword: str, user_keyword: bool = False) -> tuple:
    """(ok, motivo). Euristica LAUNCH_NATURAL sulla formulazione: una query reale ha un ordine di parole e una grammatica plausibili.
    Le keyword KDP dell'utente sono sempre ammesse."""
    if user_keyword:
        return True, None
    w = _words(keyword)
    if not w:
        return False, "vuota"
    if "or" in w:
        return False, "contiene 'or' (congiunzione innaturale in una ricerca)"
    if w[-1] in _AUD_SG_END:
        return False, f"finisce con '{w[-1]}' al singolare (forma innaturale)"
    if len(w) >= 2 and w[0] in _LEAD_AUD:
        return False, f"inizia con il pubblico al plurale '{w[0]}' (es. 'seniors short story')"
    for a, b in zip(w, w[1:]):
        if (a in _HEAD_PL or a in _HEAD_SG) and b in _AUD_PL:
            return False, f"'{a} {b}': pubblico attaccato al nome senza preposizione (es. 'stories seniors')"
        if a in _AUD_PL and (b in _AUD_PL or b in _AUD_SG_END):
            return False, f"'{a} {b}': due indicazioni di pubblico affiancate"
        if a in _AUD_PL and (b in _HEAD_PL or b in _HEAD_SG):
            return False, f"'{a} {b}': pubblico al plurale davanti al nome (es. 'adults mysteries')"
        if a in _HEAD_PL and b in _HEAD_PL:
            return False, f"'{a} {b}': due nomi al plurale affiancati"
        if (a in _HEAD_PL or a in ("story", "book")) and b in _GENRE_MOD:
            return False, f"'{a} {b}': il genere deve precedere il nome (es. 'cozy mystery short stories')"
    if re.search(r"\b(mystery|mysteries|fiction) large print$", " ".join(w)):
        return False, "'large print' dopo un solo nome di genere (es. 'mystery large print')"
    return True, None


def _form_score(keyword: str, user_keyword: bool = False) -> float:
    """Naturalezza/qualita' della forma (0-1): preferisce 'for <pubblico plurale>' e i plurali, penalizza 'with', 'very' e il nome al singolare in coda."""
    w = _words(keyword)
    s = 0.85
    if "for" in w and w[-1] in _AUD_PL:
        s += 0.10
    if any(x in ("stories", "mysteries", "novels", "tales") for x in w):
        s += 0.05
    if "with" in w:
        s -= 0.05
    if "very" in w or "extra" in w:
        s -= 0.10
    if w and w[-1] in ("story", "book", "novel"):
        s -= 0.10
    if " ".join(w).endswith("large print") or re.search(r"stories large print", " ".join(w)):
        s -= 0.15                                                       # "large print" in coda e' meno naturale che davanti al nome
    if user_keyword:
        s += CFG["user_keyword_form_bonus"]                             # frase scelta dall'autore: forma gia' validata
    return max(0.0, min(1.0, s))


def _volume(e: dict):
    """(numero|None, testo|None) dal miglior record Publisher Rocket."""
    recs = (e.get("research") or {}).get("records") or []
    nums = [r["search_volume"] for r in recs if isinstance(r.get("search_volume"), (int, float))]
    if nums:
        return max(nums), None
    txt = next((r["search_volume_text"] for r in recs if r.get("search_volume_text")), None)
    return None, txt


def _competition(e: dict):
    recs = (e.get("research") or {}).get("records") or []
    v = [r["competition"] for r in recs if isinstance(r.get("competition"), (int, float))]
    return min(v) if v else None


def _evidence(e: dict) -> tuple:
    """(0-1 | None, dettaglio): evidenza Publisher Rocket / competitor, solo dai dati presenti."""
    C = CFG["evidence_weights"]
    recs = (e.get("research") or {}).get("records") or []
    parts, wsum, acc = {}, 0.0, 0.0
    if recs:
        files = len({r.get("source_file") for r in recs})
        s = 0.6 + (0.4 if files >= 2 else 0.0)
        parts["presence"] = s
        acc, wsum = acc + C["presence"] * s, wsum + C["presence"]
        num, txt = _volume(e)
        if num is not None or txt:
            s = min(1.0, max(0.0, (math.log10(num) - 2) / 2)) if num else 0.05
            parts["volume"] = round(s, 2)
            acc, wsum = acc + C["volume"] * s, wsum + C["volume"]
        comp = _competition(e)
        if comp is not None:
            s = 1 - min(max(comp, 0), 100) / 100
            parts["competition"] = round(s, 2)
            acc, wsum = acc + C["competition"] * s, wsum + C["competition"]
    if e.get("competitor_hits"):
        parts["competitor"] = 1.0
        acc, wsum = acc + C["competitor"], wsum + C["competitor"]
    return (acc / wsum if wsum else None), parts


def launch_priority(e: dict, user_keyword: bool = False) -> tuple:
    """(punteggio 0-100, dettaglio auditabile). La pertinenza (40%) + naturalezza + copertura valgono 70%: il volume da solo non porta in lancio."""
    W = CFG["weights"]
    pert = (e.get("relevance_score") if e.get("relevance_score") is not None else e.get("evidence_score", 0)) / 100
    nat = _form_score(e["keyword"], user_keyword)
    strong = (e.get("core_topic") or {}).get("strong_concepts")
    cover = min(1.0, strong / 5) if strong is not None else pert
    ev, parts = _evidence(e)
    evid = CFG["no_evidence_default"] if ev is None else ev
    score = 100 * (W["relevance"] * pert + W["natural"] * nat + W["coverage"] * cover + W["evidence"] * evid)
    if user_keyword:
        score += CFG["user_keyword_priority_bonus"]                      # frase scelta dall'autore (keyword KDP): a parita' di dati passa avanti
    return min(100, round(score)), {"relevance": round(pert, 2), "natural": round(nat, 2), "coverage": round(cover, 2), "evidence": None if ev is None else round(ev, 2),
                          "evidence_parts": parts}


_FILLERS = frozenset("very extra book books the a an".split())


def _family(keyword: str) -> frozenset:
    """Stessi concetti (radici di singolare/plurale), ordine, preposizioni e riempitivi ignorati: "senior short stories" = "short story for seniors".
    Parole che cambiano il prodotto ("series", "collection", "box set") restano nella chiave: "cozy mystery short story series" e' un'altra famiglia."""
    from . import launch_keywords as K
    out = set()
    for w in _words(keyword):
        if w in K.STOPWORDS or w in _FILLERS or K._is_number(w):
            continue
        out.add(K._stem(w))
    return frozenset(out)


def _tier(e: dict, market, analytical: str) -> str:
    T = CFG["tiers"]
    rel = e.get("relevance_score") or 0
    if rel >= T["TOP_TIER"]["min_relevance"] and (market or 0) >= T["TOP_TIER"]["min_market"] and analytical in ("EXACT", "PHRASE"):
        return "TOP_TIER"
    if analytical in ("EXACT", "PHRASE") and rel >= T["CORE"]["min_relevance"]:
        return "CORE"
    return "BROAD_STRONG_MARKET" if (market or 0) >= T["BROAD_STRONG_MARKET"]["min_market"] else "BROAD"


def _user_keywords(plan: dict) -> set:
    from . import launch_keywords as K
    return {K.kw_norm(k) for k in (plan.get("book") or {}).get("kdp_keywords") or []}


def build_strategy(kp: dict, plan: dict, now: str | None = None) -> dict:
    """Strategia PHRASE_FIRST_AUTO dal piano keyword `kp` (output di build_keyword_plan) e dalla bozza `plan`. Funzione pura."""
    own = _user_keywords(plan)
    cur = (plan.get("book") or {}).get("currency")
    pool = [(e, e["recommended_match_type"]) for lst in ("exact", "phrase", "discovery") for e in kp.get(lst, [])]
    excluded, ranked = [], []
    for e, analytical in pool:
        k = e["keyword"]
        user = k in own
        if analytical == "DISCOVERY":
            ok_d = ((e.get("relevance_score") or 0) >= CFG["discovery_min_relevance"] and (e.get("market_evidence_score") or 0) >= CFG["discovery_min_market"]
                    and ((e.get("core_topic") or {}).get("strong_concepts") or 0) >= CFG["discovery_min_concepts"])
            if not ok_d:
                continue                                                  # le DISCOVERY non forti non sono nemmeno candidate: non si elencano (rumore)
        ok, why = launch_natural(k, user)
        if not ok:
            excluded.append({"keyword": k, "reason": f"Query innaturale: {why}", "kind": "UNNATURAL", "analytical_classification": analytical})
            continue
        if len(_words(k)) < CFG["min_words"] and not user:
            excluded.append({"keyword": k, "reason": f"Meno di {CFG['min_words']} parole: troppo ampia per il lancio", "kind": "TOO_SHORT",
                             "analytical_classification": analytical})
            continue
        score, parts = launch_priority(e, user)
        if (e.get("relevance_score") or 0) < CFG["min_relevance"] or score < CFG["min_launch_score"]:
            excluded.append({"keyword": k, "reason": f"Sotto soglia (pertinenza {e.get('relevance_score')}, priorita' {score})", "kind": "BELOW_THRESHOLD",
                             "analytical_classification": analytical})
            continue
        ranked.append((score, parts, e, analytical))
    # famiglie di varianti: vince la forma migliore (priorita', poi volume)
    fam: dict = {}
    # nella famiglia vince: keyword dell'autore, poi forma piu' naturale, poi la piu' corta, poi priorita' (che include volume e competizione)
    for item in sorted(ranked, key=lambda x: (x[2]["keyword"] not in own, -x[1]["natural"], len(_words(x[2]["keyword"])), -x[0], -(_volume(x[2])[0] or 0), x[2]["keyword"])):
        fam.setdefault(_family(item[2]["keyword"]), []).append(item)
    winners = []
    for members in fam.values():
        best = members[0]
        winners.append((best, [m[2]["keyword"] for m in members[1:]]))
        for m in members[1:]:
            excluded.append({"keyword": m[2]["keyword"], "reason": f"Variante di '{best[2]['keyword']}' (stessi concetti): tenuta solo la forma migliore",
                             "kind": "VARIANT", "variant_of": best[2]["keyword"], "analytical_classification": m[3]})
    winners.sort(key=lambda w: (-w[0][0], w[0][2]["keyword"]))
    over = winners[CFG["target_max"]:]
    for (score, parts, e, analytical), variants in over:
        excluded.append({"keyword": e["keyword"], "reason": f"Oltre il limite di {CFG['target_max']} keyword di lancio (priorita' {score})", "kind": "OVER_LIMIT",
                         "analytical_classification": analytical})
    launch = []
    for (score, parts, e, analytical), variants in winners[:CFG["target_max"]]:
        market = e.get("market_evidence_score")
        tier = _tier(e, market, analytical)
        num, txt = _volume(e)
        why = [f"pertinenza {e.get('relevance_score')}", "mercato " + ("n/d" if market is None else str(market))]
        if analytical == "EXACT":
            why.append("classificata EXACT dall'analisi: nel preset parte comunque in PHRASE")
        if e.get("competitor_hits"):
            why.append("supportata da competitor: " + ", ".join(e["competitor_hits"]))
        if variants:
            why.append(f"varianti note escluse: {', '.join(variants)}")
        launch.append({"keyword": e["keyword"], "analytical_classification": analytical, "recommended_launch_match": "PHRASE",
                       "relevance_score": e.get("relevance_score"), "market_evidence_score": market, "final_launch_score": e.get("final_launch_score"),
                       "launch_priority_score": score, "volume": num if num is not None else txt, "competition": _competition(e),
                       "competitor_support": list(e.get("competitor_hits") or []), "tier": tier, "tier_label": TIER_LABELS[tier],
                       "suggested_bid": CFG["tiers"][tier]["bid"], "bid_currency": cur, "variants": variants, "natural_ok": True,
                       "priority_parts": parts, "reason": "; ".join(why)})
    n = len(launch)
    note = (f"{n} keyword di lancio (obiettivo indicativo {CFG['target_min']}-{CFG['target_max']}): " +
            ("la lista non viene riempita artificialmente, ce ne sono meno di quelle valide." if n < CFG["target_min"] else "entro l'obiettivo."))
    return {
        "preset": PRESET, "generated_at": now or datetime.now().isoformat(timespec="seconds"),
        "campaigns_at_launch": ["PHRASE", "AUTO"],
        "phrase": {"keywords": [x["keyword"] for x in launch], "count": n, "target_range": [CFG["target_min"], CFG["target_max"]], "note": note,
                   "copy_text": "\n".join(x["keyword"] for x in launch)},
        "auto": {"recommended": True, "keywords_required": False, "suggested_bid": CFG["auto_bid"], "bid_currency": cur,
                 "reason": "Discovery iniziale: raccoglie search term reali senza scegliere keyword. Bid piu' prudente di PHRASE."},
        "exact": {"needed_at_launch": False, "status": "Da creare/promuovere dai Search Term vincitori",
                  "how": "Dopo il lancio, importa i report e apri 'Opportunità Search Term': le query reali con ordini sufficienti e ACOS buono, non gia' in EXACT, "
                         "compaiono come 'Valuta promozione in EXACT'."},
        "phrase_launch": launch, "excluded": excluded, "bid_tiers": {t: v["bid"] for t, v in CFG["tiers"].items()},
        "rules": {k: v for k, v in CFG.items()},
        "notes": ["Bid solo SUGGERITI (valuta del marketplace): non vengono mai applicati e nessuna campagna viene creata su Amazon.",
                  "analytical_classification e recommended_launch_match sono separate: una keyword EXACT per l'analisi entra comunque nella campagna PHRASE di lancio.",
                  "Le keyword escluse restano nel piano keyword e nei dati: qui si escludono solo dalla campagna di lancio."],
    }
