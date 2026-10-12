"""Sezione 6 - erogazione (delivery): segnali del TARGET e ereditati dalla CAMPAGNA, mai mischiati."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import cards, sidebar  # noqa: E402
from data_access.repository import parse_delivery_detail  # noqa: E402

ctx = cards.page_setup(st, "Erogazione", lambda: sidebar.select_context(st), small="(Delivery)")
D = ctx.decisions
if not any(d.get("delivery_source") for d in D):
    st.info("Questo run non ha il campo delivery_source (origine del segnale): sezione non disponibile.")
    st.stop()
tgt = [d for d in D if d.get("delivery_source") == "TARGET"]
fb = [d for d in D if d.get("delivery_source") == "CAMPAIGN_FALLBACK"]
unk = [d for d in D if d.get("delivery_source") == "UNKNOWN"]
L = U.label_column

st.markdown(F.badge(U.label_delivery_source("TARGET"), "blue") + f" {len(tgt)} &nbsp; "
            + F.badge(U.label_delivery_source("CAMPAIGN_FALLBACK"), "purple") + f" {len(fb)} &nbsp; "
            + F.badge(U.label_delivery_source("UNKNOWN"), "grey") + f" {len(unk)}", unsafe_allow_html=True)

from data_access import overview as O  # noqa: E402

full = [i for i in (O.delivery_item(d, parse_delivery_detail) for d in D) if i]
st.subheader(f"Target attivi da verificare ({len(full)})")
st.caption("Elenco COMPLETO (nella Panoramica compaiono solo i più rilevanti): nessuna erogazione, pochissime impressioni o forte calo delle impressioni.")
if full:
    st.dataframe([{"Campagna": i["campaign"], "Target": i["target"], "Verifica": i["badge"], "Offerta": F.money(i["bid"]),
                   f"Impressioni ultimi {i['days'] or 30} giorni": F.num(i["impressions_30d"]), "Clic": F.num(i["clicks_30d"]), "Motivo": i["reason"]} for i in full],
                 use_container_width=True, hide_index=True)
else:
    st.success("Nessun target attivo con problemi di erogazione.")

st.subheader(f"Segnali di erogazione del target ({len(tgt)})")
st.caption("Segnale calcolato sul singolo target: evidenza diretta.")
rows = []
for d in tgt:
    det = parse_delivery_detail(d.get("reason"))
    ratio = d.get("delivery_ratio") if d.get("delivery_ratio") is not None else (det["ratio"] if det else None)
    rows.append({L("campaign"): d["campaign_id"], L("target"): U.display_target(d["target"]), "Origine": U.label_delivery_source("TARGET"),
                 "Segnale": U.label_delivery(d.get("delivery_signal")),
                 "Impressioni/giorno storiche": F.num(det["reference"], 2) if det else F.ND,
                 "Impressioni/giorno recenti": F.num(det["recent"], 2) if det else F.ND,
                 "Rapporto": F.num(ratio, 2),
                 "Finestra storica": f"{det['reference_period']} ({det['reference_days']} g)" if det else F.ND,
                 "Finestra recente": f"{det['recent_period']} ({det['recent_days']} g)" if det else F.ND,
                 L("final_action"): U.label_action(d.get("final_action")), L("priority"): d.get("priority")})
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader(f"Segnali ereditati dalla campagna ({len(fb)})")
st.info("Segnale di erogazione ereditato dalla campagna: non è una prova sul singolo target e non forza da solo una revisione.")
by_camp = {}
for d in fb:
    by_camp.setdefault((d["campaign_id"], d.get("delivery_signal")), []).append(d)
st.dataframe([{L("campaign"): c, "Segnale": U.label_delivery(s), "Origine": U.label_delivery_source("CAMPAIGN_FALLBACK"),
               "Target": len(v), "Rapporto": F.num(v[0].get("delivery_ratio"), 2)} for (c, s), v in sorted(by_camp.items())],
             use_container_width=True, hide_index=True)
with st.expander(f"Target con segnale ereditato dalla campagna ({len(fb)})"):
    st.dataframe(cards.display_rows(fb, ["priority", "campaign_id", "target", "delivery_signal", "delivery_source", "final_action"]),
                 use_container_width=True, hide_index=True)
if unk:
    st.subheader(f"Origine non disponibile ({len(unk)})")
    st.dataframe(cards.display_rows(unk, ["priority", "campaign_id", "target", "final_action"]), use_container_width=True, hide_index=True)
