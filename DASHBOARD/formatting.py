"""Formattazione pura (nessuna dipendenza da Streamlit): valori NULL, ACOS non disponibile, divisioni per zero, etichette e colori semantici.
I testi mostrati all'utente sono in ui_labels.py (italiano); qui restano solo le funzioni di formato."""
import html

from kdp_ads.metrics import set_currency, sym
from ui_labels import ND, PRIORITY_LABELS


def num(v, digits=0):
    if v is None:
        return ND
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def money(v, digits=2):
    if v is None:
        return ND
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"-{sym()}{abs(x):,.{digits}f}" if x < 0 else f"{sym()}{x:,.{digits}f}"


def pct(v, digits=1):
    if v is None:
        return ND
    try:
        return f"{float(v):.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def acos(v):
    """ACOS e' None quando non ci sono vendite: mai mostrato come 0%."""
    return "n/d (nessuna vendita)" if v is None else pct(v)


def bid_change(v):
    if v is None:
        return "-"
    return f"{float(v):+.0f}%"


def yesno(v):
    if v is None:
        return ND
    return "SÌ" if v else "NO"


def safe_div(a, b):
    try:
        return None if a is None or not b else float(a) / float(b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def esc(v):
    return html.escape("" if v is None else str(v))


# ---- etichette semantiche: il colore non e' mai l'unica informazione (c'e' sempre il testo)
PRIORITY_LABEL = PRIORITY_LABELS          # etichette italiane (ui_labels)
PRIORITY_TONE = {"P1": "red", "P2": "orange", "P3": "yellow", "P4": "grey"}


def action_tone(action):
    a = action or ""
    if a.startswith("BID_DOWN") or a == "PAUSE_CANDIDATE":
        return "red"
    if a.startswith("REVIEW") or a == "WATCH" or a == "NEGATIVE_CANDIDATE":
        return "orange"
    if a == "HOLD":
        return "green"
    if a.startswith("BID_UP"):
        return "green"
    return "grey"          # HOLD_PAUSED_TARGET, NO_ACTION, sconosciuto


def status_tone(status):
    return {"ENABLED": "green", "PAUSED": "grey", "UNKNOWN": "orange"}.get(status or "", "grey")


def source_tone(source):
    return {"TARGET": "blue", "CAMPAIGN_FALLBACK": "purple"}.get(source or "", "grey")


def badge(text, tone="grey"):
    return f'<span class="kb kb-{tone}">{esc(text)}</span>'
