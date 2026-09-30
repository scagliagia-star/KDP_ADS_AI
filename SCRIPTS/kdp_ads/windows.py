"""Finestre temporali, periodo tattico e DELIVERY SIGNAL. Funzioni pure sulle raccomandazioni analitiche (rec dict).
Regole: le finestre non si sommano mai; i confronti sono sempre normalizzati PER GIORNO e solo fra report dello STESSO tipo;
tutte le soglie stanno in rules.json (snapshots / delivery)."""
from .snapshots import days_inclusive, period_id as make_period_id, window_role

DELIVERY_SIGNALS = ("DELIVERY_INCREASING", "DELIVERY_STABLE", "DELIVERY_DECLINING", "DELIVERY_COLLAPSED", "UNKNOWN")
RT_ORDER = ("TARGETING", "SEARCH_TERM")


def rec_period(rec: dict) -> str:
    return rec.get("period_id") or make_period_id(rec["report_scope"], rec["date_from"], rec["date_to"])


def rec_days(rec: dict) -> int | None:
    return days_inclusive(rec["date_from"], rec["date_to"]) if rec["report_scope"] == "DATE_RANGE" else None


def agg_of(rec: dict) -> dict:
    return {"period_id": rec_period(rec), "days": rec_days(rec), "impressions": rec["impressions"] or 0, "clicks": rec["clicks"] or 0,
            "spend": rec["spend"] or 0, "orders": rec["orders"] or 0, "sales": rec["sales"] or 0,
            "date_from": rec["date_from"], "date_to": rec["date_to"]}


def per_day_metrics(agg: dict | None) -> dict:
    """impressions/clicks/spend/orders al giorno, solo se i giorni sono noti."""
    d = (agg or {}).get("days")
    return {f"{k}_per_day": (agg[k] / d if d else None) for k in ("impressions", "clicks", "spend", "orders")} if agg else \
        {f"{k}_per_day": None for k in ("impressions", "clicks", "spend", "orders")}


def campaign_context(recs: list[dict], snap_cfg: dict) -> dict:
    """Per campagna: snapshot presenti per tipo di report, periodi COMPARABILI (Target e Search Term con stesse date),
    ultimo periodo comparabile e periodo tattico (ultimo comparabile; altrimenti l'ultimo Target con ruolo TACTICAL)."""
    out = {}
    for r in recs:
        c = out.setdefault(r["campaign_id"], {"snaps": {rt: {} for rt in RT_ORDER}, "comparable": set(),
                                             "latest_comparable": None, "tactical_pid": None, "tactical_comparable": False})
        pid = rec_period(r)
        c["snaps"][r["report_type"]][pid] = {"date_from": r["date_from"], "date_to": r["date_to"], "scope": r["report_scope"],
                                             "days": rec_days(r),
                                             "role": window_role(r["report_scope"], r["date_from"], r["date_to"], snap_cfg)}

    def latest(pids, snaps):
        return max(pids, key=lambda p: (snaps[p]["date_to"] or "", -(snaps[p]["days"] or 10 ** 6)), default=None)
    for c in out.values():
        t = {p: i for p, i in c["snaps"]["TARGETING"].items() if i["scope"] == "DATE_RANGE"}
        s = {p: i for p, i in c["snaps"]["SEARCH_TERM"].items() if i["scope"] == "DATE_RANGE"}
        c["comparable"] = set(t) & set(s)
        c["latest_comparable"] = latest(c["comparable"], t)
        if c["latest_comparable"]:
            c["tactical_pid"], c["tactical_comparable"] = c["latest_comparable"], True
        else:
            c["tactical_pid"] = latest([p for p, i in t.items() if i["role"] == "TACTICAL"], t)
    return out


def campaign_aggregates(recs: list[dict]) -> dict:
    """Totali per (campagna, tipo report, snapshot): sono i totali dell'INTERO snapshot, mai fra snapshot diversi."""
    out = {}
    for r in recs:
        k = (r["campaign_id"], r["report_type"], rec_period(r))
        a = out.setdefault(k, {"period_id": k[2], "days": rec_days(r), "impressions": 0, "clicks": 0, "spend": 0, "orders": 0,
                               "sales": 0, "date_from": r["date_from"], "date_to": r["date_to"]})
        for f in ("impressions", "clicks", "spend", "orders", "sales"):
            a[f] += r[f] or 0
    return out


def pick_pair(snaps_rt: dict, tactical_pid: str | None):
    """(periodo recente, periodo di riferimento piu' lungo) fra gli snapshot DATE_RANGE dello stesso tipo di report."""
    ranges = {p: i for p, i in snaps_rt.items() if i["scope"] == "DATE_RANGE" and i["days"]}
    if not ranges:
        return None
    latest = tactical_pid if tactical_pid in ranges else max(
        (p for p, i in ranges.items() if i["role"] == "TACTICAL"), key=lambda p: (ranges[p]["date_to"], -ranges[p]["days"]), default=None)
    if latest is None:
        return None
    refs = [p for p, i in ranges.items() if p != latest and i["days"] > ranges[latest]["days"]]
    if not refs:
        return None
    return latest, max(refs, key=lambda p: (ranges[p]["days"], ranges[p]["date_to"]))


def classify_delivery(latest: dict | None, ref: dict | None, cfg: dict) -> tuple[str, float | None, str]:
    """ratio = metrica_al_giorno(recente) / metrica_al_giorno(riferimento). Soglie da rules.json -> delivery."""
    if latest is None or ref is None:
        return "UNKNOWN", None, "finestra recente o di riferimento assente"
    if not latest["days"] or not ref["days"]:
        return "UNKNOWN", None, "giorni non noti"
    if ref["days"] < cfg["min_reference_days"] or latest["days"] < cfg["min_latest_days"]:
        return "UNKNOWN", None, f"finestre troppo brevi (riferimento {ref['days']}g < {cfg['min_reference_days']}g o recente {latest['days']}g < {cfg['min_latest_days']}g)"
    if ref["clicks"] < cfg["min_reference_clicks"]:
        return "UNKNOWN", None, f"riferimento con soli {ref['clicks']:g} clic (< {cfg['min_reference_clicks']})"
    metric = cfg["primary_metric"].replace("_per_day", "")
    if not ref[metric]:
        metric = cfg["fallback_metric"].replace("_per_day", "")
    ref_pd = ref[metric] / ref["days"]
    if not ref_pd:
        return "UNKNOWN", None, "riferimento senza delivery misurabile"
    ratio = (latest[metric] / latest["days"]) / ref_pd
    if ratio >= cfg["ratio_increasing_min"]:
        sig = "DELIVERY_INCREASING"
    elif ratio >= cfg["ratio_declining_max"]:
        sig = "DELIVERY_STABLE"
    elif ratio >= cfg["ratio_collapsed_max"]:
        sig = "DELIVERY_DECLINING"
    else:
        sig = "DELIVERY_COLLAPSED"
    return sig, ratio, (f"{metric}/giorno: recente {latest[metric] / latest['days']:.2f} ({latest['period_id']}, {latest['days']}g) vs "
                        f"riferimento {ref_pd:.2f} ({ref['period_id']}, {ref['days']}g) = x{ratio:.2f}")


def delivery_from_rows(rows_by_rt: dict, snaps: dict, tactical_pid: str | None, cfg: dict) -> tuple[str, float | None, str | None, str, list]:
    """Delivery da righe (di una entita' o totali di campagna): {tipo_report: {period_id: agg}}.
    Confronta solo report dello stesso tipo; prima Target, poi Search Term. Ritorna (segnale, ratio, sorgente, dettaglio, flag)."""
    first_unknown = ("UNKNOWN", None, None, "nessuna coppia di finestre confrontabile", [])
    for rt in RT_ORDER:
        pair = pick_pair(snaps.get(rt, {}), tactical_pid)
        if not pair:
            continue
        latest_pid, ref_pid = pair
        rows = rows_by_rt.get(rt, {})
        latest, ref = rows.get(latest_pid), rows.get(ref_pid)
        flags = []
        if latest is None and ref is not None:
            if cfg["absent_row_counts_as_zero"]:
                d = snaps[rt][latest_pid]
                latest = {"period_id": latest_pid, "days": d["days"], "impressions": 0, "clicks": 0, "spend": 0, "orders": 0, "sales": 0}
            else:
                flags.append("ABSENT_IN_LATEST_SNAPSHOT")
        sig, ratio, detail = classify_delivery(latest, ref, cfg)
        if sig != "UNKNOWN":
            return sig, ratio, rt, detail, flags
        first_unknown = ("UNKNOWN", None, rt, detail, flags) if first_unknown[2] is None else first_unknown
    return first_unknown
