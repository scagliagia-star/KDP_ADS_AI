"""KDP Ads AI - Dashboard di Supporto alle Decisioni: pagina PANORAMICA (home). SOLA LETTURA.
Caricata da app.py tramite st.navigation (voce di menu "Panoramica")."""
import bootstrap  # noqa: F401  (percorsi)
import streamlit as st

import formatting as F
import ui_labels as U
from components import cards, sidebar


def main():
    ctx = cards.page_setup(st, U.APP_TITLE, lambda: sidebar.select_context(st))
    st.caption(U.APP_SUBTITLE)
    b, run, an = ctx.book, ctx.run, ctx.analysis

    st.markdown(f"#### {b.get('title') or '(senza titolo)'} · `{b['book_id']}`")
    st.markdown(f"**Marketplace: {ctx.marketplace or F.ND} · {b.get('currency') or F.ND}**")
    st.caption("Tutti i valori di questa pagina si riferiscono solo a questo marketplace.")
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

    st.divider()
    cards.kpi_row(st, ctx.summary)
    cards.opportunity_kpis(st, ctx.repo, ctx.book, ctx.marketplace)

    st.divider()
    st.header(f"1 · {U.ACTION_CENTER_TITLE}")
    st.caption(U.ACTION_CENTER_DESCRIPTION)
    actionable = [d for d in ctx.decisions if d.get("operationally_actionable")]
    if ctx.summary["actionable"] is None:
        st.info("Questo run è precedente al campo operationally_actionable (azionabile ora): il Centro azioni non è disponibile per questo run.")
    elif not actionable:
        st.success("Nessuna decisione da valutare ora in questo run.")
    for d in actionable:
        cards.decision_card(st, d)

    st.divider()
    st.header(f"2 · {U.WARNINGS_TITLE}")
    cards.warnings_section(st, ctx.warnings)
    st.caption("Altre sezioni: Tutte le decisioni · Target in pausa · Campagne · Erogazione · Confronto tra campagne (menu a sinistra).")


main()
