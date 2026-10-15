"""PERIOD SNAPSHOT: identita' di un report per intervallo temporale. Snapshot con intervalli diversi coesistono,
non si sovrascrivono e non si fondono. Finestre sovrapposte non vengono MAI sommate."""
from datetime import date


SAME_CONTENT_DIFFERENT_PERIOD = "SAME_CONTENT_DIFFERENT_PERIOD"
SAME_CONTENT_MESSAGE = ("Il report ha contenuto identico a un altro snapshot della stessa campagna ma con periodo diverso. "
                        "Verificare che il periodo selezionato su Amazon sia corretto.")


def days_inclusive(d_from: str | None, d_to: str | None) -> int | None:
    """Giorni del periodo (estremi inclusi); None se le date non sono entrambe note (es. LIFETIME senza date_from)."""
    if not d_from or not d_to:
        return None
    return (date.fromisoformat(d_to) - date.fromisoformat(d_from)).days + 1


def period_id(scope: str | None, d_from: str | None, d_to: str | None) -> str:
    """'2026-08-30__2026-09-26' per un intervallo, 'LIFETIME__2026-09-30' per il lifetime, 'UNKNOWN__NA' se non noto."""
    if scope == "LIFETIME":
        return f"LIFETIME__{d_to or 'NA'}"
    if scope == "DATE_RANGE" and d_from and d_to:
        return f"{d_from}__{d_to}"
    return "UNKNOWN__NA"


def snapshot_id(book_id, campaign_id, report_type, scope, d_from, d_to) -> str:
    return f"{book_id}|{campaign_id}|{report_type}|{period_id(scope, d_from, d_to)}"


def window_label(scope, d_from, d_to) -> str:
    """LIFETIME / RECENT_<n>D (es. RECENT_65D, RECENT_28D) / UNKNOWN. Qualunque intervallo e' supportato."""
    if scope == "LIFETIME":
        return "LIFETIME"
    n = days_inclusive(d_from, d_to) if scope == "DATE_RANGE" else None
    return f"RECENT_{n}D" if n else "UNKNOWN"


SHORT_TREND_MAX_DAYS = 10


def window_role(scope, d_from, d_to, cfg: dict) -> str:
    """Ruolo della finestra (soglie in rules.json -> snapshots):
    SHORT_TREND (<= short_trend_max_days, il 7D: solo overlay di trend), TACTICAL (<= tactical_max_days), RECENT_CONTEXT (<= recent_context_max_days), HISTORICAL_CONTEXT (lifetime o piu' lunga)."""
    if scope == "LIFETIME":
        return "HISTORICAL_CONTEXT"
    n = days_inclusive(d_from, d_to) if scope == "DATE_RANGE" else None
    if not n:
        return "UNKNOWN"
    if n <= cfg.get("short_trend_max_days", SHORT_TREND_MAX_DAYS):
        return "SHORT_TREND"                       # 7D: overlay di trend, MAI finestra tattica/di contesto
    if n <= cfg["tactical_max_days"]:
        return "TACTICAL"
    return "RECENT_CONTEXT" if n <= cfg["recent_context_max_days"] else "HISTORICAL_CONTEXT"


def per_day(value, days) -> float | None:
    """Normalizzazione per giorno: solo se i giorni sono noti."""
    return None if (value is None or not days) else value / days
