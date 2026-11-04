"""ESCALATION RECENTE: gerarchia delle finestre per libri maturi e correzione dei casi in cui il Lifetime frena troppo. Funzioni PURE (nessun DB).

GERARCHIA   7D = trend/conferma · 30D = finestra DECISIONALE primaria · 65D = contesto intermedio (persistente o temporaneo?) · Lifetime = memoria storica.
Il Lifetime NON puo' da solo annullare un deterioramento recente forte: se il 30D e' molto negativo, il 7D conferma e il campione e' sufficiente, l'azione parte comunque; il
Lifetime forte (molti acquisti, ACOS entro il pareggio) rende solo l'azione PIU' PRUDENTE (-5 / -10 invece della pausa). Il 65D da solo non reagisce mai.
Tutte le regole richiedono: evidenza 30D (tattica) + conferma 7D (la riga 7D esiste ed e' sfavorevole). Senza 7D disponibile (MISSING_7D) non si escala.

REGOLE (soglie RELATIVE a royalty / ACOS di pareggio, in rules.json -> recent_escalation):
  R1 ZERO ORDINI       30D con >= zero_order_min_clicks clic, 0 acquisti, spesa >= zero_order_min_spend_royalty_mult x royalty, 7D ancora a 0 acquisti: SCALA GRADUALE
                       10-14 clic => -5%; 15-19 => -10%;
  R1P PAUSA CANDIDATA  >= zero_order_pause_min_clicks clic (e spesa >= royalty) SOLO se il 65D conferma un problema persistente (il 65D puo' sbloccarla gia' da
                       zero_order_pause_unlock_min_clicks clic); senza 65D resta -10%. Storico forte => massimo -10%, mai pausa;
                       target storicamente forte => al massimo -10% (mai pausa immediata); 65D buono (calo temporaneo) => al massimo -5%.
  R3 ACOS ALTO         (1-2 acquisti = campione limitato: -5%, -10% solo con >= low_sample_step10_min_clicks clic, ACOS >= severe x pareggio e 7D a 0; pausa con il doppio dei clic)
                       30D CON acquisti ma ACOS > high_acos_break_even_mult x pareggio, >= high_acos_min_clicks clic e 7D che non recupera => BID_DOWN (mai «vincente»):
                       -5/-10 secondo le fasce ACOS del libro; PAUSE solo con evidenza forte (ACOS >= high_acos_pause_acos_break_even_mult x pareggio, >= high_acos_pause_min_clicks
                       clic, 7D senza acquisti con >= high_acos_pause_7d_min_clicks clic, storico non forte, 65D non buono).
  Lo storico forte (>= strong_history_min_orders acquisti e ACOS Lifetime entro il pareggio) limita il passo a strong_history_cap_step (-5), o a strong_history_cap_step_severe
  (-10) se il 30D e' MOLTO negativo (ACOS >= severe_acos_break_even_mult x pareggio con >= severe_min_clicks clic)."""

DEFAULTS = {
    "enabled": True,
    "zero_order_min_clicks": 10, "zero_order_min_spend_royalty_mult": 1.0, "zero_order_7d_min_clicks": 3,
    "zero_order_step": 5, "zero_order_step10_min_clicks": 15, "zero_order_pause_min_clicks": 20, "zero_order_pause_unlock_min_clicks": 15, "zero_order_pause_min_spend_royalty_mult": 1.0,
    "low_sample_orders_max": 2, "low_sample_step10_min_clicks": 50,
    "high_acos_break_even_mult": 1.2, "high_acos_min_clicks": 15, "high_acos_7d_min_clicks": 3, "high_acos_max_step": 10,
    "high_acos_pause_acos_break_even_mult": 2.0, "high_acos_pause_min_clicks": 50, "high_acos_pause_7d_min_clicks": 10,
    "strong_history_min_orders": 5, "strong_history_cap_step": 5, "strong_history_cap_step_severe": 10,
    "severe_acos_break_even_mult": 1.3, "severe_min_clicks": 20,
    "context_good_min_orders": 2,
}


def config(rules: dict | None = None) -> dict:
    return {**DEFAULTS, **((rules or {}).get("recent_escalation") or {})}


def strong_history(L, ctx, cfg) -> bool:
    """Lifetime FORTE: abbastanza acquisti e ACOS storico entro il pareggio (mai pausa immediata, passo prudente)."""
    return bool(L is not None and L.orders >= cfg["strong_history_min_orders"] and L.acos is not None and L.acos <= ctx.break_even_acos)


def context_state(C, ctx, cfg) -> str:
    """Il 65D (contesto intermedio) dice se il problema e' PERSISTENTE, un calo TEMPORANEO oppure UNKNOWN (65D assente o ambiguo). Non decide mai da solo."""
    if C is None:
        return "UNKNOWN"
    if C.orders >= cfg["context_good_min_orders"] and C.acos is not None and C.acos <= ctx.target_acos:
        return "TEMPORARY"
    if C.clicks > 0 and (C.orders == 0 or (C.acos is not None and C.acos > ctx.break_even_acos)):
        return "PERSISTENT"
    return "UNKNOWN"


def _fmt(v, suffix="%"):
    return "n/d" if v is None else f"{v:.1f}{suffix}"


def evaluate(T, L, C, m7, ctx, cfg) -> dict | None:
    """T = Metrics 30D (tattico), L = Metrics Lifetime | None, C = Metrics 65D (contesto) | None, m7 = Metrics 7D | None.
    Ritorna {step (5|10|15|100), rule, flag, logic, severity} oppure None (nessuna escalation)."""
    if not cfg.get("enabled", True) or T is None or m7 is None:
        return None
    R, be = ctx.royalty, ctx.break_even_acos
    hist_strong = strong_history(L, ctx, cfg)
    ctxs = context_state(C, ctx, cfg)
    ctx_txt = {"PERSISTENT": " Il 65D conferma un problema persistente.", "TEMPORARY": " Il 65D e' buono: probabile calo temporaneo, azione limitata.",
               "UNKNOWN": ""}[ctxs]
    # ---- R1 / R1P: 30D senza acquisti, 7D conferma
    if (T.orders == 0 and T.clicks >= cfg["zero_order_min_clicks"] and T.spend >= cfg["zero_order_min_spend_royalty_mult"] * R
            and m7.orders == 0 and m7.clicks >= cfg["zero_order_7d_min_clicks"]):
        # SCALA GRADUALE: 10-14 clic => -5%; 15-19 => -10%; >= 20 => pausa candidata SOLO se il 65D (reale) conferma un problema persistente. Senza 65D la pausa non e' sbloccata
        # (resta -10%); con il 65D che conferma, la pausa si sblocca gia' da zero_order_pause_unlock_min_clicks clic. Storico forte: massimo strong_history_cap_step_severe, mai pausa.
        step, note = cfg["zero_order_step"], ""
        if T.clicks >= cfg["zero_order_step10_min_clicks"]:
            step = 10
        if ctxs == "PERSISTENT" and T.clicks >= cfg["zero_order_pause_unlock_min_clicks"] and T.spend >= cfg["zero_order_pause_min_spend_royalty_mult"] * R:
            step = 100
        elif T.clicks >= cfg["zero_order_pause_min_clicks"]:
            note = " (pausa non sbloccata: manca la conferma del 65D, resta prudente)" if C is None else " (pausa non sbloccata: il 65D non conferma un problema persistente)"
        if ctxs == "TEMPORARY" and step > cfg["zero_order_step"]:
            step, note = cfg["zero_order_step"], " (limitato: 65D buono)"
        if hist_strong and step > cfg["strong_history_cap_step_severe"]:
            step, note = cfg["strong_history_cap_step_severe"], " (limitato: target storicamente forte, mai pausa immediata)"
        rule, flag = (("R1P_RECENT_ZERO_ORDER_PAUSE", "RECENT_ZERO_ORDER_PAUSE") if step == 100 else ("R1_RECENT_ZERO_ORDER_ESCALATION", "RECENT_ZERO_ORDER_ESCALATION"))
        what = "pausa candidata" if step == 100 else f"riduzione offerta -{step}%"
        return {"step": step, "rule": rule, "flag": flag, "severity": "ZERO_ORDER",
                "logic": (f"ESCALATION RECENTE: 30D con {T.clicks:g} clic, spesa {T.spend:.2f} (>= {cfg['zero_order_min_spend_royalty_mult']:g}x royalty) e nessun acquisto; "
                          f"il 7D conferma ({m7.clicks:g} clic, 0 acquisti): {what}{note}.{ctx_txt}")}
    # ---- R3: acquisti ma ACOS molto sopra il pareggio, 7D che non recupera
    if (T.orders >= 1 and T.acos is not None and T.acos > cfg["high_acos_break_even_mult"] * be and T.clicks >= cfg["high_acos_min_clicks"]
            and m7.clicks >= cfg["high_acos_7d_min_clicks"] and not (m7.orders >= 1 and m7.acos is not None and m7.acos <= be)):
        b = ctx.bands
        step = 5 if T.acos <= b["mild"] else 10 if T.acos <= b["unprofitable"] else 15
        step = min(step, cfg["high_acos_max_step"])
        rule, flag, note = "R3_HIGH_ACOS_WITH_ORDERS", "HIGH_ACOS_WITH_ORDERS", ""
        low = T.orders <= cfg["low_sample_orders_max"]                      # campione di acquisti ancora limitato (1-2): preferibilmente -5%
        if low:
            step = 5
            if T.clicks >= cfg["low_sample_step10_min_clicks"] and T.acos >= cfg["severe_acos_break_even_mult"] * be and m7.clicks >= cfg["high_acos_pause_7d_min_clicks"] and m7.orders == 0:
                step = 10                                                    # -10% solo con campione sufficiente, ACOS nettamente sopra il pareggio e 7D che conferma
            note = " (campione di acquisti limitato: prudente)" if step == 5 else ""
        if (T.acos >= cfg["high_acos_pause_acos_break_even_mult"] * be and T.clicks >= cfg["high_acos_pause_min_clicks"] * (2 if low else 1) and m7.orders == 0
                and m7.clicks >= cfg["high_acos_pause_7d_min_clicks"] and not hist_strong and ctxs != "TEMPORARY"):
            step, rule, flag = 100, "R3P_HIGH_ACOS_PAUSE", "HIGH_ACOS_PAUSE"
        if ctxs == "TEMPORARY" and step > 5:
            step, note = 5, " (limitato: 65D buono)"
        severe = T.acos >= cfg["severe_acos_break_even_mult"] * be and T.clicks >= cfg["severe_min_clicks"]
        cap = cfg["strong_history_cap_step_severe"] if severe else cfg["strong_history_cap_step"]
        if hist_strong and step > cap:
            step, note = cap, " (limitato: target storicamente forte)"
            if rule == "R3P_HIGH_ACOS_PAUSE":
                rule, flag = "R3_HIGH_ACOS_WITH_ORDERS", "HIGH_ACOS_WITH_ORDERS"
        what = "pausa candidata" if step == 100 else f"riduzione offerta -{step}%"
        return {"step": step, "rule": rule, "flag": flag, "severity": "HIGH_ACOS",
                "logic": (f"ESCALATION RECENTE: anche con {T.orders:g} acquisti, il 30D ha ACOS {_fmt(T.acos)} contro un pareggio del {_fmt(be)} ({T.clicks:g} clic) e il 7D non recupera "
                          f"({m7.clicks:g} clic, {m7.orders:g} acquisti, ACOS {_fmt(m7.acos)}): non e' un target vincente, {what}{note}.{ctx_txt}")}
    return None
