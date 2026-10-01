"""Sezione 4 - target in pausa."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import cards, sidebar  # noqa: E402

ctx = cards.page_setup(st, "Target in pausa", lambda: sidebar.select_context(st))
if ctx.summary["paused"] is None:
    st.info("Questo run è precedente al campo target_status (stato del target): sezione non disponibile.")
    st.stop()
paused = [d for d in ctx.decisions if d.get("target_status") == "PAUSED"]
with_action = [d for d in paused if d.get("underlying_candidate_action")]
without = [d for d in paused if not d.get("underlying_candidate_action")]
st.metric("TARGET IN PAUSA", len(paused))
st.caption("Un target in pausa non può avere un'azione di bid operativa: l'analisi economica è conservata come azione potenziale.")


def acos_of(d, prefix):
    return F.acos(d.get(f"{prefix}_acos")) if d.get(f"{prefix}_clicks") is not None else F.ND


L = U.label_column
st.subheader(f"B · In pausa con azione potenziale ({len(with_action)})")
rows = [{L("campaign"): d["campaign_id"], L("target"): d["target"], L("priority"): d.get("priority"),
         L("underlying_candidate_action"): U.label_action(d["underlying_candidate_action"]),
         L("final_action"): U.label_action(d.get("final_action")),
         L("lifetime_acos"): acos_of(d, "lifetime"), L("recent_acos"): acos_of(d, "recent"),
         L("flags"): U.label_flags(d.get("flags")), L("reason"): U.label_reason(d.get("reason"))} for d in with_action]
st.dataframe(rows, use_container_width=True, hide_index=True)
for d in with_action:
    if "RECENT_SPEND_ON_PAUSED_TARGET" in (d.get("flags") or ""):
        st.caption(f"{U.label_flag('RECENT_SPEND_ON_PAUSED_TARGET')} · {d['campaign_id']} · {d['target']}: "
                   "spesa recente su un target in pausa, causa non dedotta.")

st.subheader(f"A · In pausa senza azione potenziale ({len(without)})")
rows = [{L("campaign"): d["campaign_id"], L("target"): d["target"], L("priority"): d.get("priority"),
         L("final_action"): U.label_action(d.get("final_action")),
         L("lifetime_clicks"): d.get("lifetime_clicks"), L("lifetime_acos"): acos_of(d, "lifetime"),
         L("flags"): U.label_flags(d.get("flags"))} for d in without]
st.dataframe(rows, use_container_width=True, hide_index=True)
