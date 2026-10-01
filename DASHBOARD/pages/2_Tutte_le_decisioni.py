"""Sezione 3 - tutte le decisioni, filtrabili."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import ui_labels as U  # noqa: E402
from components import cards, sidebar  # noqa: E402
from data_access.repository import filter_decisions  # noqa: E402

COLUMNS = ["priority", "campaign_id", "target", "target_status", "final_action", "underlying_candidate_action",
           "operationally_actionable", "current_bid", "bid_change_pct", "confidence", "delivery_signal", "delivery_source",
           "lifetime_acos", "recent_acos", "reason"]


def opts(decisions, col):
    return ["ALL"] + sorted({str(d[col]) for d in decisions if d.get(col) is not None})


def fmt(col):
    """Mostra l'etichetta italiana nel filtro; il valore usato per filtrare resta quello tecnico."""
    return lambda v: "Tutti" if v == "ALL" else U.display_value(col, v)


ctx = cards.page_setup(st, "Tutte le decisioni", lambda: sidebar.select_context(st))
D = ctx.decisions
f = st.columns(4)
campaign = f[0].selectbox("Campagna", opts(D, "campaign_id"), format_func=fmt("campaign_id"))
priority = f[1].selectbox("Priorità", opts(D, "priority"), format_func=fmt("priority"))
status = f[2].selectbox("Stato target", opts(D, "target_status"), format_func=fmt("target_status"))
action = f[3].selectbox("Azione finale", opts(D, "final_action"), format_func=fmt("final_action"))
f = st.columns(4)
conf = f[0].selectbox("Affidabilità", opts(D, "confidence"), format_func=fmt("confidence"))
act = f[1].selectbox("Azionabile ora", ["ALL", "true", "false"], format_func=lambda v: {"ALL": "Tutti", "true": "Sì", "false": "No"}[v])
dsig = f[2].selectbox("Segnale di erogazione", opts(D, "delivery_signal"), format_func=fmt("delivery_signal"))
dsrc = f[3].selectbox("Origine del segnale", opts(D, "delivery_source"), format_func=fmt("delivery_source"))
search = st.text_input("Cerca target", placeholder="Scrivi una parte del nome del target")

rows = filter_decisions(D, campaign, priority, status, action, conf, None if act == "ALL" else act == "true", dsig, dsrc, search)
st.caption(f"{len(rows)} di {len(D)} decisioni")
if not rows:
    st.info("Nessuna decisione corrisponde ai filtri selezionati.")
st.dataframe(cards.display_rows(rows, COLUMNS), use_container_width=True, hide_index=True)

st.subheader("Dettaglio righe")
for d in rows[:200]:
    with st.expander(f"{d.get('priority')} · {d.get('campaign_id')} · {d.get('target')} · {U.label_action(d.get('final_action'))}"):
        st.markdown(cards.decision_badges(d), unsafe_allow_html=True)
        st.json({k: v for k, v in d.items() if v is not None}, expanded=False)
if len(rows) > 200:
    st.caption("Dettagli limitati alle prime 200 righe: restringi i filtri per vedere le altre.")
