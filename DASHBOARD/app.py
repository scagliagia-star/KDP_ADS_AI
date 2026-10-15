"""KDP Ads AI - Dashboard di Supporto alle Decisioni. SOLA LETTURA.
Avvio (invariato): python -m streamlit run DASHBOARD\\app.py

app.py e' solo l'indice di navigazione: definisce le voci del menu (titoli italiani in ui_labels.NAV_PAGES) con st.navigation.
La pagina principale e' panoramica.py ("Panoramica"); le altre sono in pages/."""
import bootstrap  # noqa: F401  (percorsi)
import streamlit as st

import ui_labels as U



def _unassigned() -> int:
    """Campagne nuove da associare (sola lettura, tollera DB o tabelle assenti): badge numerico nel menu, mai un errore."""
    try:
        import sqlite3
        db = bootstrap.db_path()
        if not db.is_file():
            return 0
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM campaign_book_mapping WHERE status='UNASSIGNED'").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


_n = _unassigned()
st.navigation([st.Page(path, title=(f"{title} ({_n})" if _n and path.endswith("14_Campagne_da_associare.py") else title), default=(i == 0))
               for i, (path, title) in enumerate(U.NAV_PAGES)]).run()
