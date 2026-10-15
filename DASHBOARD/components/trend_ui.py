"""Lettura temporale Lifetime + 30D + 7D nelle card operative (solo presentazione). Il 7D e' un overlay di trend: si mostra sempre con la dimensione del campione
(clic / acquisti), perche' un ACOS senza campione non si deve interpretare."""
import formatting as F
import ui_labels as U


def has_trend(d) -> bool:
    return d.get("trend_state") is not None or d.get("trend_clicks") is not None


def badge(d) -> str:
    state = d.get("trend_state")
    if not state:
        return ""
    return F.badge(U.label_trend(state, d.get("trend_evidence")), U.TREND_TONES.get(state, "grey"))


def line(st, d):
    """Riga ACOS Lifetime / 30D / 7D + stato leggibile + campione 7D. Niente se non c'e' nessuno snapshot 7D per questa campagna."""
    if not has_trend(d):
        return
    tac = d.get("tactical_acos") if d.get("tactical_clicks") is not None else None
    acos7 = d.get("trend_acos") if d.get("trend_clicks") else None
    clicks, orders = d.get("trend_clicks"), d.get("trend_orders")
    sample = f"{F.num(clicks)} clic · {F.num(orders)} acquisti" if clicks is not None else F.ND
    c = st.columns(4)
    c[0].metric("ACOS storico completo", F.pct(d.get("lifetime_acos")) if d.get("lifetime_acos") is not None else F.ND)
    c[1].metric("ACOS ultimi 30 giorni", F.pct(tac) if tac is not None else F.ND)
    c[2].metric("ACOS ultimi 7 giorni", F.pct(acos7) if acos7 is not None else F.ND)
    c[3].metric("Campione 7 giorni", sample)
    st.markdown(f"Tendenza: {badge(d)}", unsafe_allow_html=True)
    if d.get("trend_note"):
        st.caption(f"{d['trend_note']} (evidenza 7D: {U.TREND_EVIDENCE.get(d.get('trend_evidence'), F.ND)}). Il 30D resta la finestra decisionale: il 7D è solo un segnale di tendenza.")
