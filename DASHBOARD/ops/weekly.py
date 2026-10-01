"""Routine settimanale: per ogni campagna i 4 report attesi e quali mancano. Lettura dei soli snapshot gia' importati.
Un report e' PRESENTE se esiste uno snapshot del tipo/finestra giusti con data di fine non piu' vecchia di FRESH_DAYS
rispetto alla data di riferimento; e' OBSOLETO se esiste ma e' piu' vecchio; MANCANTE altrimenti."""
from datetime import date, timedelta

from kdp_ads.snapshots import days_inclusive

from .catalog import list_campaigns, list_marketplaces
from .dbread import query

FRESH_DAYS = 7
EXPECTED = [("TARGETING", "LIFETIME", "Target Lifetime"),
            ("SEARCH_TERM", "D65", "Search Term 65D"),
            ("TARGETING", "D30", "Target 30D"),
            ("SEARCH_TERM", "D30", "Search Term 30D")]
STATUS_LABELS = {"PRESENTE": "PRESENTE", "OBSOLETO": "OBSOLETO (più vecchio di 7 giorni)", "MANCANTE": "MANCANTE"}


def _kind(row):
    if row["report_scope"] == "LIFETIME":
        return "LIFETIME"
    n = days_inclusive(row["date_from"], row["date_to"]) if row["report_scope"] == "DATE_RANGE" else None
    return {65: "D65", 30: "D30", 28: "D28"}.get(n)


def checklist(ws, book_id, reference: date, fresh_days: int = FRESH_DAYS, marketplace=None) -> list[dict]:
    """Checklist per campagna. Con `marketplace` solo le campagne e gli snapshot di quel marketplace (indipendenti dagli altri)."""
    snaps = query(ws, "SELECT campaign_id, marketplace, report_type, report_scope, date_from, date_to, period_id, row_count, source_filename "
                      "FROM imports WHERE book_id=?", (book_id,))
    out = []
    for c in list_campaigns(ws, book_id, marketplace):
        items = []
        for rt, kind, label in EXPECTED:
            cands = [s for s in snaps if s["campaign_id"] == c["campaign_id"] and s["report_type"] == rt and _kind(s) == kind
                     and str(s.get("marketplace") or "").upper() in ("", str(c["marketplace"] or "").upper())]
            best = max(cands, key=lambda s: s["date_to"] or "", default=None)
            if best is None:
                status = "MANCANTE"
            else:
                limit = (reference - timedelta(days=fresh_days)).isoformat()
                status = "PRESENTE" if (best["date_to"] or "") >= limit else "OBSOLETO"
            items.append({"report_type": rt, "kind": kind, "label": label, "status": status,
                          "date_to": best["date_to"] if best else None, "period_id": best["period_id"] if best else None})
        out.append({"campaign_id": c["campaign_id"], "campaign_type": c["campaign_type"], "marketplace": c["marketplace"], "items": items,
                    "complete": all(i["status"] == "PRESENTE" for i in items),
                    "missing": [i for i in items if i["status"] != "PRESENTE"]})
    return out


def book_complete(rows) -> bool:
    return bool(rows) and all(r["complete"] for r in rows)


def checklist_by_marketplace(ws, book_id, reference: date, fresh_days: int = FRESH_DAYS) -> dict:
    """{marketplace: righe checklist}: una sezione per ogni marketplace configurato (anche senza campagne). Mai un 'completo' di libro:
    il set e' completo per ciascun marketplace."""
    return {m["marketplace"]: checklist(ws, book_id, reference, fresh_days, m["marketplace"]) for m in list_marketplaces(ws, book_id)}


def marketplace_complete(rows) -> bool:
    """Completo per un marketplace: ha campagne e ogni campagna ha i 4 report presenti."""
    return book_complete(rows)
