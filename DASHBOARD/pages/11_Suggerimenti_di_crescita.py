"""Suggerimenti di crescita: scaling prudente (+5%), AUTO -> FRASE, FRASE -> EXACT, sviluppo campagne, nuovi target prodotto. Solo dati locali, SOLA LETTURA.
Separati dalle decisioni difensive: non entrano nei KPI della Panoramica."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import ui_labels as U  # noqa: E402
from components import cards, competitor_ui, growth_ui, sidebar  # noqa: E402
from data_access.repository import RepoError  # noqa: E402
from kdp_ads import growth as G  # noqa: E402

ctx = cards.page_setup(st, U.GROW_PAGE_TITLE, lambda: sidebar.select_context(st))
st.caption(U.GROW_INTRO)
try:
    opp = ctx.repo.search_term_opportunities(ctx.book, ctx.marketplace)
except RepoError as e:
    st.warning(U.label_error(str(e)))
    opp = None
growth = G.build_growth(ctx.decisions, opp["records"] if opp else [], ctx.book, G.config(ctx.repo.rules()), ctx.warnings)
if not growth["total"]:
    st.info(U.GROW_NONE)
else:
    st.markdown(f"**{growth['total']} suggerimenti:** " + " · ".join(G.summary_lines(growth)))
growth_ui.sections(st, growth)

# DA RICERCA COMPETITOR (Helium 10, fonte esterna): sezione SEPARATA, mai mischiata ai dati Amazon Ads reali e fuori dai KPI.
try:
    research = ctx.repo.competitor_research(ctx.book, ctx.marketplace, opp["records"] if opp else None)
except RepoError:
    research = {"projects": [], "total": 0}
if research["projects"]:
    competitor_ui.growth_section(st, research, "pages/12_Ricerca_competitor.py")
