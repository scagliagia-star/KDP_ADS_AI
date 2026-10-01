"""Sezione 5 - panoramica campagne. Ogni finestra viene dal proprio snapshot: le finestre sovrapposte non si sommano."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import cards, sidebar, style  # noqa: E402
from kdp_ads.metrics import AUTO_CAMPAIGN_TYPES  # noqa: E402

ctx = cards.page_setup(st, "Panoramica campagne", lambda: sidebar.select_context(st))
camps = ctx.repo.campaign_overview(ctx.book["book_id"], ctx.book.get("royalty_per_copy"), ctx.marketplace)
if not camps:
    st.info("Nessun dato di campagna importato.")
    st.stop()
st.caption("Storico completo, ultimi 65 giorni e ultimi 30 giorni sono snapshot distinti e sovrapposti: sono mostrati affiancati, mai sommati. "
           "Profitto = stima diretta (ordini × royalty − spesa).")
for c in camps:
    with st.container(border=True):
        st.markdown(f"### {c['campaign_id']} · {c.get('campaign_type') or F.ND}")
        if str(c.get("campaign_type") or "").upper() in AUTO_CAMPAIGN_TYPES:
            st.info(f"Campagna con targeting automatico. {U.AUTO_GROUPS_NOTE}")
        if not c["windows"]:
            st.write("Nessuna finestra importata.")
            continue
        rows = [{"Finestra": U.label_window(w["window"]), "Report di origine": U.label_report_type(w["report_type"]),
                 "Periodo": w["period_id"], "Giorni": w["days"] or "-",
                 "Clic": F.num(w["clicks"]), "Spesa": F.money(w["spend"]), "Ordini": F.num(w["orders"]), "Vendite": F.money(w["sales"]),
                 "ACOS": F.acos(w["acos"]), "Profitto diretto stimato": F.money(w["estimated_profit"])} for w in c["windows"]]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        recent = [w for w in c["windows"] if w["days"]]
        if len(recent) >= 2:
            st.markdown("**Andamento al giorno** (ogni finestra è normalizzata sulla propria durata)")
            for label, key, fmt in (("Spesa / giorno", "spend_per_day", F.money), ("Clic / giorno", "clicks_per_day", lambda v: F.num(v, 2)),
                                    ("Ordini / giorno", "orders_per_day", lambda v: F.num(v, 2))):
                mx = max((w[key] or 0) for w in recent)
                cols = st.columns(len(recent))
                for col, w in zip(cols, recent):
                    col.markdown(f"{label} · {U.label_window(w['window'])}<br><b>{fmt(w[key])}</b>" + style.bar(w[key], mx), unsafe_allow_html=True)
            base = recent[0]
            for w in recent[1:]:
                r = F.safe_div(w["spend_per_day"], base["spend_per_day"])
                st.caption(f"Spesa/giorno {U.label_window(w['window'])} rispetto a {U.label_window(base['window'])}: " + (F.ND if r is None else f"×{r:.2f}"))
