"""Opportunita' Search Term: suggerimenti MANUALI da search term reali (PHRASE, Auto, altre campagne). Sola lettura, calcolo in memoria."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import cards, sidebar  # noqa: E402
from data_access.repository import RepoError  # noqa: E402
from kdp_ads.opportunities import NEGATIVE, NO_ACTION, PROMOTE, WATCH, rank, unique_terms  # noqa: E402

ctx = cards.page_setup(st, U.OPP_PAGE_TITLE, lambda: sidebar.select_context(st))
st.caption(U.OPP_INTRO)
try:
    res = ctx.repo.search_term_opportunities(ctx.book, ctx.marketplace)
except RepoError as e:
    st.error(U.label_error(str(e)))
    st.stop()
records = res["records"]
st.markdown(f"**Marketplace: {ctx.marketplace or F.ND}** · {len(records)} search term valutati · "
            f"{len([r for r in records if r['opportunity_type'] == NO_ACTION])} senza segnali")
if res["excluded"]:
    st.caption(f"Esclusi dalla valutazione: {res['excluded']}")

c = st.columns(3)
c[0].metric(U.OPP_KPI_EXACT, len(unique_terms(records, PROMOTE)))
c[1].metric(U.OPP_KPI_NEGATIVE, len(unique_terms(records, NEGATIVE)))
c[2].metric("DA MONITORARE", len(unique_terms(records, WATCH)))

sug = res.get("new_exact_campaign")
if sug:
    st.info(f"**{U.OPP_NEW_CAMPAIGN}** — " + U.OPP_NEW_CAMPAIGN_TEXT.format(n=sug["min_terms"]) + " Termini: " + ", ".join(sug["terms"]))

cfg = res["config"]
with st.expander("Regole usate (conservative, documentate)"):
    st.markdown(
        f"- **Valuta aggiunta in EXACT**: ≥ {cfg['promote_high_min_orders']} ordini con ACOS ≤ ACOS obiettivo (affidabilità ALTA), oppure "
        f"≥ {cfg['promote_medium_min_orders']} ordini con ACOS ≤ ACOS di pareggio (MEDIA). Mai se il termine è già una keyword EXACT, un ASIN o un gruppo Auto.\n"
        f"- **Valuta keyword negativa**: solo su search term reali, mai sul nome del target padre. 0 ordini con ≥ {cfg['negative_zero_min_clicks']} clic e spesa ≥ "
        f"{cfg['negative_zero_spend_mult']:g}× royalty, oppure {cfg['negative_acos_min_orders']}–9 ordini con ≥ {cfg['negative_acos_min_clicks']} clic e ACOS fortemente "
        f"sopra il pareggio. {U.OPP_NEGATIVE_TYPE_NOTE}\n"
        f"- **Monitora search term**: promettente con pochi dati, o debole ma con campione insufficiente per una negativa (≥ {cfg['watch_min_clicks']} clic).\n"
        "- **Finestre**: ultimi 30 giorni = evidenza principale, ultimi 65 giorni = conferma; mai sommate. Concordi con ordini in più: affidabilità +1; "
        "discordanti: −1; affidabilità BASSA → solo da monitorare.")


def window_text(w):
    if not w:
        return F.ND
    return f"{F.num(w['clicks'])} clic · {F.num(w['orders'])} ord. · {F.money(w['spend'])} · {F.acos(w['acos']) if w['sales'] else F.ND}"


def parent_text(r):
    ptype = U.label_target_type(r["parent_target_type"]) if r["parent_target_type"] == "AUTO_TARGETING_GROUP" else None
    return f"{r['parent_target']} ({ptype})" if ptype else r["parent_target"]


def table(rows):
    L = []
    for r in rows:
        L.append({
            "Search term": r["search_term"], "Campagna di provenienza": f"{r['campaign_id']} ({r['campaign_type'] or F.ND})",
            "Gruppo/target padre": parent_text(r), "Ultimi 30 giorni": window_text(r["window_30d"]), "Ultimi 65 giorni": window_text(r["window_65d"]),
            "Clic": F.num(r["clicks"]), "Ordini": F.num(r["orders"]), "Spesa": F.money(r["spend"]), "Vendite": F.money(r["sales"]),
            "ACOS": F.acos(r["acos"]), "CVR": F.pct(r["cvr"]), "Profitto stimato": F.money(r["estimated_profit"]),
            "Affidabilità": U.label_confidence(r["confidence"]), "Finestre": U.label_opp_agreement(r["windows_agreement"]),
            "Già in EXACT": "SÌ" if r["already_in_exact"] else "NO", "Segnalazioni": U.label_opp_flags(r["flags"]) or "-",
            "Motivo": r["reason"],
        })
    return L


for title, otype in ((U.OPP_SECTION_PROMOTE, PROMOTE), (U.OPP_SECTION_NEGATIVE, NEGATIVE), (U.OPP_SECTION_WATCH, WATCH)):
    rows = rank(records, otype)
    st.subheader(f"{title} ({len(rows)})")
    st.markdown(F.badge(U.label_opportunity(otype), {PROMOTE: "green", NEGATIVE: "red", WATCH: "orange"}[otype]), unsafe_allow_html=True)
    if otype == NEGATIVE:
        st.caption(U.OPP_NEGATIVE_TYPE_NOTE)
    if not rows:
        st.info("Nessun suggerimento in questa sezione con i dati e le soglie attuali.")
        continue
    st.dataframe(table(rows[:300]), use_container_width=True, hide_index=True)
    if len(rows) > 300:
        st.caption("Mostrate le prime 300 righe.")
