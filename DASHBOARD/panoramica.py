"""KDP Ads AI - Dashboard di Supporto alle Decisioni: PANORAMICA OPERATIVA ("cosa devo fare oggi su Amazon Ads?"). SOLA LETTURA.
Caricata da app.py tramite st.navigation (voce di menu "Panoramica"). Le sezioni e i KPI derivano dalle stesse collezioni
(data_access.overview.build_overview); le altre pagine restano disponibili per il dettaglio."""
import bootstrap  # noqa: F401  (percorsi)
import streamlit as st

import formatting as F
import ui_labels as U
from components import cards, growth_ui, overview_ui, sidebar
from data_access import overview as O
from data_access.repository import RepoError, parse_delivery_detail
from kdp_ads import growth as G
from kdp_ads.metrics import sym


def main():
    ctx = cards.page_setup(st, U.APP_TITLE, lambda: sidebar.select_context(st))
    st.caption(U.APP_SUBTITLE)
    b, run, an = ctx.book, ctx.run, ctx.analysis

    st.markdown(f"#### {b.get('title') or '(senza titolo)'} · `{b['book_id']}`")
    st.markdown(f"**Marketplace: {ctx.marketplace or F.ND} · {b.get('currency') or F.ND}**")
    st.caption(U.OV_INTRO + " Tutti i valori si riferiscono solo a questo marketplace.")
    c = st.columns(4)
    c[0].metric("ACOS obiettivo", F.pct(b.get("target_acos")))
    c[1].metric("ACOS di pareggio", F.pct(b.get("break_even_acos")))
    c[2].metric("Royalty per copia", F.money(b.get("royalty_per_copy")))
    c[3].metric("Prezzo libro", F.money(b.get("price")))
    c = st.columns(3)
    latest_run = ctx.runs[0]
    c[0].metric("Run di analisi (del run decisionale selezionato)", f"#{run.get('analysis_run_id')}" if an else F.ND,
                help=f"Run di analisi più recente del libro: #{ctx.latest_analysis['run_id']}" if ctx.latest_analysis else None)
    c[1].metric("Run decisionale", f"#{run['run_id']}" + (" (più recente)" if run["run_id"] == latest_run["run_id"] else " (storico)"))
    c[2].metric("Ultimo aggiornamento", U.label_datetime(run.get("generated_at")))
    if run["run_id"] != latest_run["run_id"]:
        st.warning(f"Stai guardando un run storico (#{run['run_id']}); l'ultimo è #{latest_run['run_id']}.")

    try:                                                   # opportunità Search Term: calcolate al volo, SOLA LETTURA
        opp = ctx.repo.search_term_opportunities(ctx.book, ctx.marketplace)
    except RepoError:
        opp = None
    warnings = list(ctx.warnings)
    for w in (opp or {}).get("data_warnings", []):             # righe Search Term impossibili rilevate al volo (se il run non le aveva segnalate)
        body = w.replace("DATA_WARNING ", "", 1)
        if not any(body[:60] in (x.get("text") or "") for x in warnings):
            warnings.append({"origin": "opportunità Search Term", "campaign": None, "text": w, "kind": "DATA_WARNING", "severity": "n/d"})
    ov = O.build_overview(ctx.decisions, b, ctx.summary, warnings, opp, parse_delivery_detail, sym())
    growth = G.build_growth(ctx.decisions, (opp or {}).get("records", []), b, G.config(ctx.repo.rules()), warnings)

    st.divider()
    st.header(U.OV_KPI_TITLE)
    overview_ui.kpis(st, ov)

    st.divider()
    overview_ui.today(st, ov, b)
    st.divider()
    overview_ui.exact_section(st, ov)
    st.divider()
    overview_ui.negative_section(st, ov)
    overview_ui.conflicts_section(st, ov)
    overview_ui.promoted_section(st, ov)
    overview_ui.products_section(st, ov)
    st.divider()
    overview_ui.delivery_section(st, ov, b)
    st.divider()
    overview_ui.paused_section(st, ov)
    st.divider()
    overview_ui.monitor_section(st, ov)
    st.divider()
    overview_ui.good_section(st, ov)
    st.divider()
    growth_ui.guarda_anche(st, G.summary_lines(growth), growth["total"])
    st.divider()
    overview_ui.warnings_section(st, ov)
    st.divider()
    overview_ui.others_section(st, ov)
    overview_ui.rules_section(st, ov)
    st.caption("Per approfondire: Tutte le decisioni · Target in pausa · Campagne · Erogazione · Confronto tra campagne · Opportunità Search Term (menu a sinistra).")


main()
