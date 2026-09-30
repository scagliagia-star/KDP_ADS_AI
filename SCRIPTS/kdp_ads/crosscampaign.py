"""Segnale recente-vs-storico e analisi cross-campaign. Solo confronti verificabili, nessuna causalita' assunta."""
from collections import defaultdict

from .metrics import Metrics, compute_metrics, normalize_term, product_asin, term_matches_target
from .snapshots import days_inclusive
from .rules import CONF_ORDER, BookCtx, calculate_confidence


# ------------------------------------------------------------------ recent_vs_historical_signal
def _signal(recent: Metrics, life: Metrics, cfg: dict) -> tuple[str, str]:
    d = (f"recente {recent.orders:g} ord/{recent.clicks:g} clic/ACOS "
         f"{'N/A' if recent.acos is None else format(recent.acos, '.1f') + '%'} vs lifetime {life.orders:g} ord/"
         f"{life.clicks:g} clic/ACOS {'N/A' if life.acos is None else format(life.acos, '.1f') + '%'}")
    if recent.orders == 0:
        if recent.clicks >= cfg["min_recent_clicks_zero_orders"] and life.orders >= cfg["min_lifetime_orders_for_decline_zero"]:
            return "DECLINING_SIGNAL", d + " (recente: 0 ordini con clic sufficienti, lifetime convertiva)"
        return "UNKNOWN", d + " (campione recente insufficiente)"
    if recent.orders < cfg["min_recent_orders"] or recent.acos is None or life.acos is None:
        return "UNKNOWN", d + " (ordini recenti insufficienti)"
    delta = recent.acos - life.acos
    sig = "IMPROVING_SIGNAL" if delta <= -cfg["acos_delta_pp"] else "DECLINING_SIGNAL" if delta >= cfg["acos_delta_pp"] else "STABLE_SIGNAL"
    return sig, d + f", delta ACOS {delta:+.1f} pp"


def build_signals(target_rows: list[dict], st_rows: list[dict], ctx: BookCtx) -> dict:
    """Ritorna {('T', id_riga): (segnale, dettaglio), ('S', id_riga): ...}.
    Confronto SOLO fra: stesso book, stessa campagna, una riga RECENTE (Search Term con termine == target, oppure Target di
    una finestra DATE_RANGE) e l'UNICA riga Target LIFETIME con quel target. Il lifetime include il periodo recente: e' un
    segnale direzionale, non un confronto fra periodi indipendenti. La riga lifetime riceve il segnale della finestra
    recente piu' aggiornata (date_to piu' recente; a parita', la piu' breve)."""
    cfg = ctx.rules["signal"]
    life = defaultdict(list)
    for t in target_rows:
        if t["report_scope"] == "LIFETIME":
            life[(t["campaign_id"], normalize_term(t["target"]))].append(t)
    recents = [("S", s) for s in st_rows if term_matches_target(s["search_term"], s["target"])]
    recents += [("T", t) for t in target_rows if t["report_scope"] == "DATE_RANGE"]
    res, latest_for_life = {}, {}
    for kind, r in recents:
        key = (r["campaign_id"], normalize_term(r["target"]))
        cands = life.get(key, [])
        if len(cands) != 1:
            res[(kind, r["id"])] = ("UNKNOWN", "nessuna associazione univoca verificabile con una riga Target lifetime")
            continue
        lt = cands[0]
        if r["report_scope"] != "DATE_RANGE":
            res[(kind, r["id"])] = ("UNKNOWN", "scope dei report non noto")
            continue
        sig = _signal(r["_m"], lt["_m"], cfg)
        res[(kind, r["id"])] = sig
        days = days_inclusive(r.get("date_from"), r.get("date_to")) or 10 ** 6
        rank = (r.get("date_to") or "", -days)
        if key not in latest_for_life or rank >= latest_for_life[key][0]:
            latest_for_life[key] = (rank, lt["id"], sig)
    for _, lid, sig in latest_for_life.values():
        res[("T", lid)] = sig
    for s in st_rows:                      # riga ST il cui termine non e' il target: nessun confronto
        if ("S", s["id"]) not in res:
            res[("S", s["id"])] = ("UNKNOWN", "il termine differisce dal target: nessun confronto Target-vs-Search Term")
    return res


# ------------------------------------------------------------------ cross-campaign
def _label(camp: dict, group: list[dict]) -> str:
    same = [c for c in group if c["campaign_type"] == camp["campaign_type"]]
    return camp["campaign_type"] if len(same) == 1 else camp["campaign_id"]


def analyze_overlaps(st_rows: list[dict], ctx: BookCtx) -> list[dict]:
    """Termini di ricerca identici (normalizzati) in >=2 campagne dello stesso libro.
    Classe base CROSS_CAMPAIGN_OVERLAP; 'possible_inefficiency' solo se stesso termine + entrambe spendono
    in modo significativo + una materialmente meno efficiente + campione sufficiente. Nessuna causalita'."""
    cfg, R = ctx.rules["overlap"], ctx.royalty
    # aggrega per (campagna, termine): il termine puo' comparire con piu' target nella stessa campagna
    agg: dict = {}
    for s in st_rows:
        k = (s["campaign_id"], normalize_term(s["search_term"]))
        a = agg.setdefault(k, {"campaign_id": s["campaign_id"], "campaign_type": s["campaign_type"],
                               "term": k[1], "impressions": 0, "clicks": 0, "spend": 0, "orders": 0, "sales": 0,
                               "targets": set(), "target_type": s["target_type"]})
        for f in ("impressions", "clicks", "spend", "orders", "sales"):
            a[f] += s[f] or 0
        a["targets"].add(s["target"])
    by_term = defaultdict(list)
    for a in agg.values():
        a["m"] = compute_metrics(a["impressions"], a["clicks"], a["spend"], a["orders"], a["sales"], R)
        by_term[a["term"]].append(a)

    out = []
    for term, group in sorted(by_term.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda c: c["campaign_id"])
        suff = [c for c in group if c["m"].clicks >= cfg["min_clicks_each"]]
        rec = {"search_term": term, "book_id": ctx.book_id, "campaigns": group, "classification": "INSUFFICIENT_DATA",
               "possible_inefficiency": False, "better": None, "worse": None, "recommendation": "", "detail": ""}
        rec["is_asin"] = bool(product_asin(term) or (len(term) == 10 and term.startswith("b0")))
        if len(suff) >= 2:
            def eff(c):  # ACOS piu' basso = migliore; 0 ordini (campione sufficiente) = peggiore
                return (c["m"].acos if c["m"].orders > 0 and c["m"].acos is not None else float("inf"), -c["m"].orders)
            ranked = sorted(suff, key=eff)
            best, worst = ranked[0], ranked[-1]
            ba, wa = eff(best)[0], eff(worst)[0]
            similar = (ba == wa) or (ba != float("inf") and wa != float("inf") and (
                abs(ba - wa) <= cfg["similar_acos_pp"] or abs(ba - wa) <= cfg["similar_acos_rel"] * max(ba, wa)))
            if similar:
                rec["classification"] = "SIMILAR_PERFORMANCE"
                rec["detail"] = "ACOS comparabile tra le campagne con campione sufficiente."
            else:
                rec["classification"] = "BETTER_IN_" + _label(best, group).upper()
                rec["better"], rec["worse"] = best, worst
                gap_ok = wa == float("inf") or (wa - ba) >= cfg["material_gap_pp"]
                spend_ok = all(c["m"].spend >= cfg["min_spend_mult_each"] * R for c in (best, worst))
                over_target = wa == float("inf") or wa > ctx.target_acos
                confs = {c["campaign_id"]: calculate_confidence(c["m"].orders, c["m"].clicks, c["m"].spend, R, ctx.rules)
                         for c in (best, worst)}
                conf_ok = all(CONF_ORDER[v] >= CONF_ORDER[cfg["min_confidence"]] for v in confs.values())
                rec["confidence"] = confs
                if gap_ok and spend_ok and over_target and conf_ok:
                    rec["possible_inefficiency"] = True
                    tg = ", ".join(sorted(t for t in worst["targets"] if t))
                    rec["recommendation"] = (
                        f"reduce {_label(worst, group)} bid candidate (BID_DOWN_5_CANDIDATE sul target che cattura il termine: {tg}); "
                        f"keep {_label(best, group)}")
                    rec["detail"] = ("Possibile inefficienza (nessuna causalita' assunta): "
                                     f"{_label(worst, group)} ACOS {'N/A (0 ordini)' if wa == float('inf') else format(wa, '.1f') + '%'} "
                                     f"({worst['m'].clicks:g} clic, {worst['m'].orders:g} ordini, spesa ${worst['m'].spend:.2f}, conf {confs[worst['campaign_id']]}) "
                                     f"vs {_label(best, group)} ACOS {ba:.1f}% ({best['m'].clicks:g} clic, {best['m'].orders:g} ordini, "
                                     f"spesa ${best['m'].spend:.2f}, conf {confs[best['campaign_id']]}). "
                                     "Se il termine non coincide col target, il bid change colpirebbe anche altri termini.")
                else:
                    fails = [n for n, ok in (("gap ACOS non materiale", gap_ok), ("spesa non significativa in entrambe", spend_ok),
                                             ("ACOS della peggiore non sopra target", over_target),
                                             (f"confidence < {cfg['min_confidence']} ({confs})", conf_ok)) if not ok]
                    rec["detail"] = ("Una campagna e' piu' efficiente ma NON si segnala inefficienza: " + "; ".join(fails) + ".")
        else:
            rec["detail"] = f"meno di 2 campagne con >= {cfg['min_clicks_each']} clic."
        out.append(rec)
    return out


def analyze_overlaps_by_period(st_rows: list[dict], ctx: BookCtx) -> list[dict]:
    """Overlap calcolati SEPARATAMENTE per ogni snapshot Search Term (stesso period_id): finestre diverse non si sommano
    ne' si confrontano fra loro. Ogni overlap riporta il proprio period_id."""
    groups = defaultdict(list)
    for s in st_rows:
        groups[s.get("period_id")].append(s)
    out = []
    for pid, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        for o in analyze_overlaps(rows, ctx):
            o["period_id"] = pid
            out.append(o)
    return out
