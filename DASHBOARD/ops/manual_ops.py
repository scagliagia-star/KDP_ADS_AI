"""REGISTRO AZIONI MANUALI: servizi della dashboard («HO FATTO QUESTA MODIFICA»). Scrivono SOLO nella tabella locale `manual_actions` del DB (append-only, una riga per modifica):
nessuna rete, nessuna Amazon, nessuna modifica ad Amazon Ads. La prima apertura di un DB pre-v8 con `connect` esegue la migrazione a v8 con il backup versionato di sempre."""
from datetime import date, datetime

from kdp_ads import manual_actions as MA
from kdp_ads.db import connect

from . import OpResult


def record_action(ws, payload: dict, executed_on: date | None = None, value_after=None, note: str | None = None) -> OpResult:
    """`payload` = dati della card (data_access.overview.decision_register / item_register). `value_after` (offerta realmente impostata) sostituisce quello suggerito.
    Idempotente: la stessa identica registrazione non si duplica."""
    data = {k: v for k, v in (payload or {}).items() if v is not None or k in ("value_before", "value_after")}
    if value_after is not None:
        data["value_after"] = float(value_after)
    when = executed_on or date.today()
    stamp = datetime.combine(when, datetime.now().time()).isoformat(timespec="seconds") if isinstance(when, date) else str(when)
    conn = connect(ws.db_path)
    try:
        aid = MA.record(conn, executed_at=stamp, note=(note or None), **data)
        return OpResult(True, [], "Modifica registrata (solo nel database locale: Amazon Ads non è stato toccato)", {"action_id": aid})
    except MA.ManualActionError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def undo_action(ws, action_id: int) -> OpResult:
    """Annulla una registrazione (status UNDONE: la riga resta come audit). L'azione torna a essere proponibile."""
    conn = connect(ws.db_path)
    try:
        MA.set_status(conn, action_id, "UNDONE")
        return OpResult(True, [], "Registrazione annullata", {"action_id": action_id})
    except MA.ManualActionError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()
