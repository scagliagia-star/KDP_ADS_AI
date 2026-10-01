"""Intestazione delle pagine V2 (gestione e importazione). A differenza della V1 queste pagine SCRIVONO file locali:
il banner lo dice e ribadisce che non c'e' nessuna modifica su Amazon Ads."""
import ui_labels as U
from components import style


def setup(st, title):
    st.set_page_config(page_title=f"{U.APP_TITLE} · {title}", layout="wide")
    st.markdown(style.CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="ro-banner"><b>{U.OPS_NO_AMAZON}</b> &nbsp;·&nbsp; {U.OPS_BANNER_TEXT}</div>', unsafe_allow_html=True)
    st.title(title)
