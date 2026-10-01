"""Sezione 7 - confronto tra campagne: overlap dell'analysis run collegato."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import cards, sidebar  # noqa: E402

ctx = cards.page_setup(st, "Confronto tra campagne", lambda: sidebar.select_context(st))
groups, msg = ctx.repo.overlaps(ctx.analysis)
if msg:
    st.info(U.label_error(msg))
    st.stop()
alerts = [g for g in groups if g["possible_inefficiency"]]
st.metric("SOVRAPPOSIZIONI (termine × periodo)", len(groups))
if alerts:
    st.error(f"ALLARME · Possibile inefficienza: {len(alerts)}")
for g in groups:
    with st.container(border=True):
        head = f"`{g['search_term']}` · {g['period_id']}"
        if g["possible_inefficiency"]:
            st.markdown(f"### {head}")
            st.markdown(F.badge("ALLARME · Possibile inefficienza: SÌ", "red") + F.badge(U.label_overlap_class(g["classification"]), "orange"),
                        unsafe_allow_html=True)
        else:
            st.markdown(f"#### {head}")
            st.markdown(F.badge("Possibile inefficienza: NO", "grey") + F.badge(U.label_overlap_class(g["classification"]), "grey"),
                        unsafe_allow_html=True)
        st.markdown(f"Campagna migliore: **{g['better_campaign'] or F.ND}** · campagna peggiore: {g['worse_campaign'] or F.ND}")
        rows = []
        for m in g["members"]:
            acos = F.pct(m["acos_value"]) if m["acos_value"] is not None else ("n/d (nessuna vendita)" if m["acos"] == "N/A_NO_SALES" else (m["acos"] or F.ND))
            rows.append({U.label_column("campaign"): m["campaign_id"], "Clic": F.num(m["clicks"]), "Ordini": F.num(m["orders"]),
                         "Tasso di conversione": F.pct(m["conversion_rate"]), "CPC": F.money(m["cpc"]), "ACOS": acos,
                         "Spesa": F.money(m["spend"]), "Profitto stimato": F.money(m["estimated_direct_ad_profit"])})
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if g["detail"]:
            st.caption(U.label_reason(g["detail"]))
        if g["recommendation"]:
            st.caption(f"Nota analitica: {U.label_reason(g['recommendation'])}")
