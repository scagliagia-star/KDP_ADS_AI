"""Parsing valori numerici/date. Assume formato USA (marketplace .com): '.' decimale, ',' migliaia.
Valori che non rispettano questa assunzione sollevano ParseError invece di essere indovinati."""
import re
from datetime import date, datetime

EMPTY = {"", "-", "--", "—", "n/a", "na", "null", "none"}


class ParseError(ValueError):
    pass


def parse_number(v: str | None) -> float | None:
    if v is None:
        return None
    s = v.strip()
    if s.lower() in EMPTY:
        return None
    s = re.sub(r"[$€£%\s]", "", s)
    if "," in s:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
            s = s.replace(",", "")
        else:
            raise ParseError(f"formato numerico ambiguo (locale non-USA?): {v!r}")
    try:
        return float(s)
    except ValueError:
        raise ParseError(f"numero non interpretabile: {v!r}")


def parse_ratio_pct(v: str | None) -> float | None:
    """CTR/ACOS/conversione. Nei report IT reali sono FRAZIONI con virgola decimale ("0,0586" = 5.86%).
    Con '%' esplicito il valore e' gia' in punti percentuali. Ritorna sempre punti percentuali."""
    if v is None:
        return None
    s = v.strip()
    if s.lower() in EMPTY:
        return None
    has_pct = "%" in s
    s = re.sub(r"[%\s]", "", s)
    if re.fullmatch(r"-?\d+,\d+", s):
        s = s.replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        raise ParseError(f"rapporto non interpretabile: {v!r}")
    return x if has_pct else x * 100


_MONTHS =("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def parse_date(v: str | None) -> date | None:
    if v is None or v.strip().lower() in EMPTY:
        return None
    s = v.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.strptime(s, "%Y-%m-%d").date()
    for fmt in _MONTHS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, y = map(int, m.groups())
        if a > 12 >= b:
            return date(y, b, a)
        if b > 12 >= a:
            return date(y, a, b)
        raise ParseError(f"data ambigua (gg/mm vs mm/gg): {v!r}")
    raise ParseError(f"data non interpretabile: {v!r}")
