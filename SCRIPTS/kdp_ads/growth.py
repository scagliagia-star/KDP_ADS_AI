"""SUGGERIMENTI DI CRESCITA: famiglia SEPARATA dalle decisioni difensive. Funzioni PURE: nascono solo da dati locali (decisioni, opportunita' Search
Term, target esistenti, ASIN osservati). Nessun LLM, nessuna keyword o ASIN inventati, nessuna scrittura, nessuna modifica Amazon Ads.

Categorie:
  SCALE            ALZA OFFERTA 5% (scaling MOLTO prudente, solo +5%). Candidato se TUTTO vero:
                   target ATTIVO in HOLD, >= scale_min_hist_orders acquisti storici, ACOS storico <= scale_hist_acos_max_pct_of_target x ACOS obiettivo,
                   periodo recente valido (30 giorni se ha >= scale_min_recent_orders acquisti, altrimenti 65 giorni) con ACOS <= obiettivo e
                   >= scale_min_recent_orders acquisti, nessun periodo recente valido sopra l'obiettivo, nessun periodo recente zero-order (clic senza
                   acquisti), nessun avviso dati che renda il recente inaffidabile, nessuno stato che richieda gia' monitoraggio o riduzione.
                   Le impressioni (stagionali) non entrano MAI nella decisione.
  AUTO_TO_PHRASE   AGGIUNGI IN FRASE: vincitori AUTO moderati (opportunita' SUGGEST_IN_PHRASE).
  TO_EXACT         METTI IN EXACT: vincitori FRASE e AUTO forti (opportunita' PROMOTE_TO_EXACT, stesse del KPI OPPORTUNITA' EXACT).
  DEVELOP          SVILUPPA CAMPAGNA: campagna (non AUTO) senza ordini che NON e' inefficiente ma ha traffico/coverage insufficiente
                   (< develop_max_clicks clic e spesa < develop_max_spend_mult x royalty). Concreta quando ci sono candidati reali dai dati.
  NEW_PRODUCT      VALUTA NUOVI TARGET PRODOTTO: ASIN osservati come query con buon rendimento che NON sono ancora un product target."""
from collections import defaultdict

DEFAULTS = {
    "scale_enabled": True,
    "scale_step_pct": 5,
    "scale_min_hist_orders": 5,
    "scale_hist_acos_max_pct_of_target": 0.75,
    "scale_min_recent_orders": 2,
    "develop_max_clicks": 30,
    "develop_max_spend_mult": 2.0,
}
CATEGORIES = ("SCALE", "AUTO_TO_PHRASE", "TO_EXACT", "DEVELOP", "NEW_PRODUCT")
# flag/avvisi che rendono il recente inaffidabile per lo scaling
UNRELIABLE_FLAGS = ("ABSENT_IN_LATEST_SNAPSHOT", "DATA_ANOMALY", "TARGET_STATUS_UNKNOWN")
IMPOSSIBLE = "IMPOSSIBLE_SEARCH_TERM_METRICS"


def config(rules: dict | None = None) -> dict:
    return {**DEFAULTS, **((rules or {}).get("growth") or {})}


def _flags(d) -> set:
    return {f for f in str(d.get("flags") or "").split(";") if f}


def _recent_for_scaling(d, cfg):
    """Periodo recente usato: 30 giorni se ha abbastanza acquisti, altrimenti 65 giorni. None se nessuno e' valido."""
    need = cfg["scale_min_recent_orders"]
    for prefix, days in (("tactical", 30), ("recent", 65)):
        orders, acos = d.get(f"{prefix}_orders"), d.get(f"{prefix}_acos")
        if (orders or 0) >= need and acos is not None:
            return {"days": days, "clicks": d.get(f"{prefix}_clicks"), "orders": orders, "acos": acos}
    return None


def scale_blockers(d, book, cfg, warnings=()) -> list:
    """Motivi (lista vuota = candidato allo scaling). Servono per i test e per spiegare perche' un target NON e' candidato."""
    why = []
    tgt = book.get("target_acos")
    if d.get("target_status") != "ENABLED":
        why.append("target non attivo")
    if d.get("final_action") != "HOLD":
        why.append("la decisione non e' HOLD (richiede gia' monitoraggio o riduzione)")
    if (d.get("lifetime_orders") or 0) < cfg["scale_min_hist_orders"]:
        why.append("acquisti storici insufficienti")
    life = d.get("lifetime_acos")
    if tgt is None or life is None or life > cfg["scale_hist_acos_max_pct_of_target"] * tgt:
        why.append("ACOS storico non abbastanza sotto l'obiettivo")
    rec = _recent_for_scaling(d, cfg)
    if rec is None:
        why.append("nessun periodo recente valido (acquisti recenti insufficienti)")
    elif tgt is not None and rec["acos"] > tgt:
        why.append("ACOS recente sopra l'obiettivo")
    for prefix in ("tactical", "recent"):                      # nessun deterioramento sopra l'obiettivo / nessun zero-order recente
        acos, clicks, orders = d.get(f"{prefix}_acos"), d.get(f"{prefix}_clicks") or 0, d.get(f"{prefix}_orders") or 0
        if tgt is not None and acos is not None and clicks >= 10 and acos > tgt:
            why.append(f"periodo recente ({prefix}) sopra l'obiettivo")
        if clicks >= 10 and orders == 0:
            why.append("zero-order recente")
    if _flags(d) & set(UNRELIABLE_FLAGS):
        why.append("avviso dati: il recente non e' affidabile")
    tname = str(d.get("target") or "")
    used = _recent_for_scaling(d, cfg)
    for w in warnings or ():
        text = w.get("text", "") if isinstance(w, dict) else str(w)
        if IMPOSSIBLE in text:
            # le righe impossibili sono ESCLUSE dai calcoli: il recente del report Target (30 giorni) non e' toccato; il 65 giorni (da Search Term) si'
            if used and used["days"] == 65 and str(d.get("campaign_id")) in text:
                why.append("avviso dati: il recente a 65 giorni deriva da Search Term con metriche impossibili")
                break
        elif tname and f"'{tname}'" in text or (tname and f'"{tname}"' in text):
            why.append("avviso dati che cita questo target")
            break
    return list(dict.fromkeys(why))


def suggested_bid(bid, pct):
    return None if bid is None else round(float(bid) * (1 + pct / 100.0), 2)


def scale_items(decisions, book, cfg, warnings=()):
    out = []
    if not cfg.get("scale_enabled", True):
        return out
    for d in decisions:
        if scale_blockers(d, book, cfg, warnings):
            continue
        rec = _recent_for_scaling(d, cfg)
        pct = cfg["scale_step_pct"]
        out.append({"category": "SCALE", "campaign": d.get("campaign_id"), "campaign_type": d.get("campaign_type"), "target": d.get("target"),
                    "target_type": d.get("target_type"), "decision_id": d.get("decision_id"), "bid": d.get("current_bid"),
                    "suggested_bid": suggested_bid(d.get("current_bid"), pct), "step_pct": pct,
                    "hist": {"clicks": d.get("lifetime_clicks"), "orders": d.get("lifetime_orders"), "spend": d.get("lifetime_spend"),
                             "sales": d.get("lifetime_sales"), "acos": d.get("lifetime_acos")},
                    "recent": rec, "confidence": "MEDIA" if (d.get("lifetime_orders") or 0) < 10 else "ALTA", "decision": d,
                    "reason": (f"Ha {d.get('lifetime_orders'):g} acquisti storici con ACOS {d.get('lifetime_acos'):.1f}% (molto sotto l'obiettivo "
                               f"{book.get('target_acos'):g}%) e anche negli ultimi {rec['days']} giorni resta in utile: ACOS {rec['acos']:.1f}% con "
                               f"{rec['orders']:g} acquisti. Si può provare ad alzare l'offerta del {pct}%.")})
    out.sort(key=lambda i: (-(i["hist"]["orders"] or 0), str(i["target"])))
    return out


def _unique(records, otype, rank):
    seen, out = set(), []
    for r in rank(records, otype):
        if r["search_term"] in seen:
            continue
        seen.add(r["search_term"])
        out.append(r)
    return out


def _term_item(category, r, text):
    return {"category": category, "term": r["search_term"], "campaign": r["campaign_id"], "campaign_type": r.get("campaign_type"),
            "origin": "Auto" if r.get("parent_target_type") == "AUTO_TARGETING_GROUP" else str(r.get("campaign_type") or "n/d"),
            "parent": r.get("parent_target"), "clicks": r.get("clicks"), "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"),
            "acos": r.get("acos"), "confidence": r.get("confidence"), "windows": r.get("windows_agreement"), "reason": text, "record": r}


def build_growth(decisions, opp_records, book, cfg=None, warnings=(), rank=None) -> dict:
    """Tutte le categorie + conteggi. `opp_records` = records di compute_opportunities (puo' essere vuoto/None)."""
    from .opportunities import PRODUCT_WINNER, PROMOTE, SUGGEST_PHRASE, rank as _rank
    rank = rank or _rank
    cfg = cfg or DEFAULTS
    recs = opp_records or []
    royalty = book.get("royalty_per_copy") or 0.0
    out = {c: [] for c in CATEGORIES}
    out["SCALE"] = scale_items(decisions, book, cfg, warnings)

    phrase_campaigns = sorted({c for r in recs for c in (r.get("suggested_campaigns") or [])})
    for r in _unique(recs, SUGGEST_PHRASE, rank):
        where = (f"nella campagna {', '.join(r.get('suggested_campaigns') or [])}" if r.get("suggested_campaigns")
                 else "in una campagna FRASE (non ce n'è ancora una: valuta di crearla)")
        out["AUTO_TO_PHRASE"].append(_term_item("AUTO_TO_PHRASE", r, f"Promettente ma con pochi dati: aggiungila come keyword a frase {where}. "
                                                                      "Non è ancora abbastanza forte per l'EXACT."))
    for r in _unique(recs, PROMOTE, rank):
        origin = "AUTO con segnale forte" if "AUTO_STRONG_WINNER" in (r.get("flags") or []) else "vincitore da FRASE"
        out["TO_EXACT"].append(_term_item("TO_EXACT", r, f"{origin}: valuta di metterla in EXACT."))
    for r in rank(recs, PRODUCT_WINNER):
        if r.get("asin_targeted") is False:
            out["NEW_PRODUCT"].append({"category": "NEW_PRODUCT", "asin": r.get("product_asin") or str(r["search_term"]).upper(), "campaign": r["campaign_id"],
                                       "clicks": r.get("clicks"), "orders": r.get("orders"), "spend": r.get("spend"), "sales": r.get("sales"),
                                       "acos": r.get("acos"), "confidence": r.get("confidence"), "record": r,
                                       "reason": "Questo prodotto compare nei termini di ricerca con buon rendimento ma non è ancora un target prodotto: "
                                                 "valuta di aggiungerlo."})

    # SVILUPPA CAMPAGNA: coverage insufficiente, MAI inefficienza
    by = defaultdict(list)
    for d in decisions:
        by[d.get("campaign_id")].append(d)
    for cid, ds in sorted(by.items(), key=lambda kv: str(kv[0])):
        ctype = str(ds[0].get("campaign_type") or "").upper()
        if ctype in ("OTHER", "AUTO"):
            continue
        clicks = sum(d.get("lifetime_clicks") or 0 for d in ds)
        orders = sum(d.get("lifetime_orders") or 0 for d in ds)
        spend = sum(d.get("lifetime_spend") or 0 for d in ds)
        active = sum(1 for d in ds if d.get("target_status") == "ENABLED")
        if orders > 0 or clicks >= cfg["develop_max_clicks"] or spend >= cfg["develop_max_spend_mult"] * royalty or not active:
            continue
        concrete = []
        if ctype == "PHRASE":
            concrete = [it["term"] for it in out["AUTO_TO_PHRASE"] if cid in (it["record"].get("suggested_campaigns") or [])]
            how = f"{len(concrete)} termine/i AUTO promettente/i disponibile/i: {', '.join(concrete)} → aggiungili in FRASE."
        elif ctype == "EXACT":
            concrete = [it["term"] for it in out["TO_EXACT"]]
            how = f"{len(concrete)} keyword pronta/e per l'EXACT: {', '.join(concrete)}."
        else:
            how = ""
        out["DEVELOP"].append({"category": "DEVELOP", "campaign": cid, "campaign_type": ctype, "clicks": clicks, "orders": orders, "spend": spend,
                               "sales": sum(d.get("lifetime_sales") or 0 for d in ds), "acos": None, "active_targets": active, "candidates": concrete, "decision": ds[0],
                               "secondary": not concrete, "confidence": "MEDIA" if concrete else "BASSA",
                               "reason": (f"La campagna non ha ancora generato ordini ma ha poco traffico ({clicks:g} clic, spesa ancora bassa): non è inefficiente, "
                                          "ha poca copertura. " + (how if concrete else "Copertura limitata: valuta ricerca di nuove keyword."))})
    counts = {c: len(out[c]) for c in CATEGORIES}
    out["counts"] = counts
    out["total"] = sum(counts.values())
    return out


GROWTH_WORDS = {"SCALE": ("candidato da scalare", "candidati da scalare"), "AUTO_TO_PHRASE": ("da testare in FRASE", "da testare in FRASE"),
                "TO_EXACT": ("keyword da mettere in EXACT", "keyword da mettere in EXACT"), "DEVELOP": ("campagna da sviluppare", "campagne da sviluppare"),
                "NEW_PRODUCT": ("nuovo target prodotto da valutare", "nuovi target prodotto da valutare")}


def summary_lines(growth) -> list:
    """Righe della card GUARDA ANCHE: solo categorie con conteggio > 0."""
    return [f"{growth['counts'][c]} {GROWTH_WORDS[c][0 if growth['counts'][c] == 1 else 1]}"
            for c in ("AUTO_TO_PHRASE", "SCALE", "DEVELOP", "TO_EXACT", "NEW_PRODUCT") if growth["counts"][c] > 0]
