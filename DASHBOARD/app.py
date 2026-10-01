"""KDP Ads AI - Dashboard di Supporto alle Decisioni. SOLA LETTURA.
Avvio (invariato): python -m streamlit run DASHBOARD\\app.py

app.py e' solo l'indice di navigazione: definisce le voci del menu (titoli italiani in ui_labels.NAV_PAGES) con st.navigation.
La pagina principale e' panoramica.py ("Panoramica"); le altre sono in pages/."""
import bootstrap  # noqa: F401  (percorsi)
import streamlit as st

import ui_labels as U

st.navigation([st.Page(path, title=title, default=(i == 0)) for i, (path, title) in enumerate(U.NAV_PAGES)]).run()
