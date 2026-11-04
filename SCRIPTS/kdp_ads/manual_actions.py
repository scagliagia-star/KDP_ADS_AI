"""REGISTRO AZIONI MANUALI (schema v8): memoria LOCALE delle modifiche che l'utente ha gia' fatto a mano su Amazon Ads.

Il sistema non modifica mai Amazon Ads: pero' ogni settimana rivede gli stessi dati e riproporrebbe le stesse azioni gia' eseguite (negative, pause, bid, EXACT).
Qui l'utente registra «HO FATTO QUESTA MODIFICA» (data, valore prima/dopo) e la Panoramica smette di riproporla. Solo SQLite locale: nessuna rete, nessuna Amazon.

IDENTITA' di un'azione (mai solo il nome della campagna, altrimenti due campagne omonime collidono):
    account + marketplace + libro + campagna (ID campagna Amazon, oppure ID interno per le campagne storiche) + soggetto (target oppure termine di ricerca) + tipo azione.

CLASSIFICAZIONE di una raccomandazione rispetto al registro (`classify`):
    TODO       nessuna azione registrata per quel soggetto (o registrata ma non coerente): resta in «DA FARE OGGI»;
    DONE       la stessa identica azione e' gia' stata eseguita (stesso tipo e, per le offerte, stesso valore finale): «GIÀ ESEGUITA», non si ripropone;
    NEW_AFTER  esiste una modifica precedente ma i nuovi dati giustificano un'azione DIVERSA (es. offerta 0,81 -> 0,77 gia' fatta, ora 0,77 -> 0,73): «NUOVA AZIONE DOPO
               MODIFICA PRECEDENTE». Il target non viene mai nascosto per sempre.
Famiglie: offerta (BID_DOWN / BID_UP: conta l'ULTIMA modifica), stato (PAUSE / REACTIVATE: conta l'ultima), negative (una frase copre anche l'esatta), EXACT."""
import sqlite3
from datetime import datetime

from .metrics import normalize_term

ACTION_TYPES = ("BID_DOWN", "BID_UP", "PAUSE", "REACTIVATE", "ADD_NEGATIVE_EXACT", "ADD_NEGATIVE_PHRASE", "PROMOTE_TO_EXACT")
STATUSES = ("EXECUTED", "UNDONE", "INVALIDATED")
BID_FAMILY = ("BID_DOWN", "BID_UP")
STATE_FAMILY = ("PAUSE", "REACTIVATE")
NEG_FAMILY = ("ADD_NEGATIVE_EXACT", "ADD_NEGATIVE_PHRASE")
TARGET_ACTIONS = BID_FAMILY + STATE_FAMILY                   # soggetto = target; le altre hanno come soggetto il termine di ricerca
TODO, DONE, NEW_AFTER = "TODO", "DONE", "NEW_AFTER"
BID_TOL = 0.005                                               # due offerte a 2 decimali sono la stessa offerta

# azione del decision layer -> tipo di azione manuale
DECISION_ACTION = {"BID_DOWN_5_CANDIDATE": "BID_DOWN", "BID_DOWN_10_CANDIDATE": "BID_DOWN", "BID_DOWN_15_CANDIDATE": "BID_DOWN", "BID_UP_5_CANDIDATE": "BID_UP",
                   "BID_UP_10_CANDIDATE": "BID_UP", "PAUSE_CANDIDATE": "PAUSE", "REACTIVATE_CANDIDATE": "REACTIVATE", "NEGATIVE_CANDIDATE": "ADD_NEGATIVE_EXACT"}
ACTION_WORDS = {"BID_DOWN": "OFFERTA ABBASSATA", "BID_UP": "OFFERTA ALZATA", "PAUSE": "MESSO IN PAUSA", "REACTIVATE": "RIATTIVATO",
                "ADD_NEGATIVE_EXACT": "NEGATIVA ESATTA AGGIUNTA", "ADD_NEGATIVE_PHRASE": "NEGATIVA A FRASE AGGIUNTA", "PROMOTE_TO_EXACT": "PROMOSSA IN EXACT"}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS manual_actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL, executed_at TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '', marketplace TEXT NOT NULL, book_id TEXT NOT NULL,
    campaign_id TEXT, amazon_campaign_id TEXT, amazon_ad_group_id TEXT, amazon_target_id TEXT,
    search_term TEXT, target_identity TEXT,
    action_type TEXT NOT NULL CHECK (action_type IN ({",".join(f"'{a}'" for a in ACTION_TYPES)})),
    value_before REAL, value_after REAL,
    status TEXT NOT NULL DEFAULT 'EXECUTED' CHECK (status IN ({",".join(f"'{s}'" for s in STATUSES)})),
    source_decision_id TEXT, source_run_id INTEGER, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_ma_book ON manual_actions(book_id, marketplace, status);
CREATE INDEX IF NOT EXISTS ix_ma_amz ON manual_actions(account_id, amazon_campaign_id);
"""


class ManualActionError(ValueError):
    """Registrazione non valida (messaggio italiano). Nessuna scrittura."""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def has_schema(conn) -> bool:
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_actions'").fetchone())
    except sqlite3.Error:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def acct_key(account_id) -> str:
    return str(account_id or "").strip()


def subject_key(action_type: str, target=None, term=None) -> str:
    """Soggetto normalizzato: target (identita' keyword/ASIN) per bid/stato, termine di ricerca normalizzato per negative/EXACT."""
    if action_type in TARGET_ACTIONS:
        from .amazon import target_identity
        return target_identity(target) if target else ""
    return normalize_term(term)


# ------------------------------------------------------------------ scrittura
def record(conn, *, book_id, marketplace, action_type, account_id=None, campaign_id=None, amazon_campaign_id=None, amazon_ad_group_id=None, amazon_target_id=None,
           target=None, search_term=None, value_before=None, value_after=None, executed_at=None, source_decision_id=None, source_run_id=None, note=None,
           now=None) -> int:
    """Registra UNA modifica gia' eseguita a mano. Non tocca Amazon Ads. Ritorna action_id. Una registrazione identica (stessa azione sullo stesso soggetto e con lo stesso
    valore finale gia' registrata come EXECUTED) NON viene duplicata: ritorna l'id esistente."""
    if action_type not in ACTION_TYPES:
        raise ManualActionError(f"Tipo di azione non valido: {action_type}")
    mp = str(marketplace or "").strip().upper()
    if not mp or not str(book_id or "").strip():
        raise ManualActionError("Servono libro e marketplace: due marketplace diversi non devono mai collidere")
    if not (campaign_id or amazon_campaign_id):
        raise ManualActionError("Serve la campagna (ID Amazon o ID interno)")
    subj = subject_key(action_type, target, search_term)
    if not subj:
        raise ManualActionError("Serve il target" if action_type in TARGET_ACTIONS else "Serve il termine di ricerca")
    if action_type in BID_FAMILY and value_after is None:
        raise ManualActionError("Per una modifica di offerta indica l'offerta dopo la modifica")
    ensure_schema(conn)
    ex = classify(list_actions(conn, book_id=book_id, marketplace=mp), account_id=account_id, marketplace=mp, book_id=book_id, campaign_id=campaign_id,
                  amazon_campaign_id=amazon_campaign_id, target=target, search_term=search_term, action_type=action_type, value_after=value_after)
    if ex["state"] == DONE and ex["match"] is not None and ex["match"]["action_type"] == action_type:
        return ex["match"]["action_id"]
    stamp = now or _now()
    with conn:
        cur = conn.execute(
            "INSERT INTO manual_actions (created_at, executed_at, account_id, marketplace, book_id, campaign_id, amazon_campaign_id, amazon_ad_group_id, amazon_target_id, "
            "search_term, target_identity, action_type, value_before, value_after, status, source_decision_id, source_run_id, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'EXECUTED',?,?,?)",
            (stamp, str(executed_at or stamp), acct_key(account_id), mp, str(book_id), campaign_id, amazon_campaign_id, amazon_ad_group_id, amazon_target_id,
             None if action_type in TARGET_ACTIONS else search_term, subj if action_type in TARGET_ACTIONS else None, action_type,
             None if value_before is None else float(value_before), None if value_after is None else float(value_after), source_decision_id, source_run_id, note))
    return cur.lastrowid


def set_status(conn, action_id: int, status: str) -> None:
    """UNDONE / INVALIDATED: l'azione non conta piu' (la riga resta come audit). Mai una cancellazione."""
    if status not in STATUSES:
        raise ManualActionError(f"Stato non valido: {status}")
    with conn:
        n = conn.execute("UPDATE manual_actions SET status=? WHERE action_id=?", (status, int(action_id))).rowcount
    if not n:
        raise ManualActionError(f"Azione {action_id} non trovata")


# ------------------------------------------------------------------ lettura
def list_actions(conn, book_id=None, marketplace=None, status="EXECUTED") -> list:
    """Azioni registrate (default solo EXECUTED), dalla piu' vecchia alla piu' recente. [] se la tabella non esiste (DB non ancora migrato a v8)."""
    if not has_schema(conn):
        return []
    sql, args = "SELECT * FROM manual_actions WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if book_id is not None:
        sql += " AND book_id=?"
        args.append(str(book_id))
    if marketplace:
        sql += " AND UPPER(marketplace)=?"
        args.append(str(marketplace).upper())
    prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql + " ORDER BY executed_at, action_id", args)]
    finally:
        conn.row_factory = prev


# ------------------------------------------------------------------ classificazione
def _same_campaign(a: dict, amazon_campaign_id, campaign_id) -> bool:
    if a.get("amazon_campaign_id") and amazon_campaign_id:
        return str(a["amazon_campaign_id"]) == str(amazon_campaign_id)
    return bool(a.get("campaign_id") and campaign_id and str(a["campaign_id"]) == str(campaign_id))


def _bid_eq(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < BID_TOL


def matching(actions: list, *, account_id, marketplace, book_id, campaign_id=None, amazon_campaign_id=None, target=None, search_term=None, family=None) -> list:
    """Azioni EXECUTED che riguardano lo stesso soggetto: stesso account, marketplace, libro, campagna e target/termine. `family`: tipi ammessi."""
    ak, mp = acct_key(account_id), str(marketplace or "").upper()
    out = []
    for a in actions:
        if a.get("status", "EXECUTED") != "EXECUTED" or acct_key(a.get("account_id")) != ak or str(a.get("marketplace") or "").upper() != mp:
            continue
        if str(a.get("book_id")) != str(book_id) or not _same_campaign(a, amazon_campaign_id, campaign_id):
            continue
        if family and a["action_type"] not in family:
            continue
        if a["action_type"] in TARGET_ACTIONS:
            if not target or subject_key(a["action_type"], target=target) != (a.get("target_identity") or ""):
                continue
        elif normalize_term(search_term) != normalize_term(a.get("search_term")) or not normalize_term(search_term):
            continue
        out.append(a)
    return sorted(out, key=lambda a: (str(a.get("executed_at") or ""), a["action_id"]))


def classify(actions: list, *, account_id, marketplace, book_id, action_type, campaign_id=None, amazon_campaign_id=None, target=None, search_term=None, value_after=None) -> dict:
    """{state: TODO | DONE | NEW_AFTER, last: ultima azione rilevante | None, match: azione identica | None} per UNA raccomandazione."""
    ident = dict(account_id=account_id, marketplace=marketplace, book_id=book_id, campaign_id=campaign_id, amazon_campaign_id=amazon_campaign_id, target=target,
                 search_term=search_term)
    res = {"state": TODO, "last": None, "match": None}
    if action_type in BID_FAMILY:
        bids = matching(actions, **ident, family=BID_FAMILY)
        paused = matching(actions, **ident, family=STATE_FAMILY)
        if paused and paused[-1]["action_type"] == "PAUSE" and (not bids or str(bids[-1].get("executed_at")) <= str(paused[-1].get("executed_at"))):
            return {"state": DONE, "last": paused[-1], "match": paused[-1]}          # gia' messo in pausa dopo l'ultima modifica di offerta: l'offerta non e' piu' il problema
        if not bids:
            return res
        last = bids[-1]
        res["last"] = last
        if last["action_type"] == action_type and (_bid_eq(last.get("value_after"), value_after)):
            return {"state": DONE, "last": last, "match": last}
        return {"state": NEW_AFTER, "last": last, "match": None}
    if action_type in STATE_FAMILY:
        st = matching(actions, **ident, family=STATE_FAMILY)
        if not st:
            return res
        last = st[-1]
        return {"state": DONE if last["action_type"] == action_type else NEW_AFTER, "last": last, "match": last if last["action_type"] == action_type else None}
    if action_type in NEG_FAMILY:
        negs = matching(actions, **ident, family=NEG_FAMILY)
        covers = [n for n in negs if n["action_type"] == "ADD_NEGATIVE_PHRASE"] if action_type == "ADD_NEGATIVE_PHRASE" else negs     # la frase copre anche l'esatta, non viceversa
        return {"state": DONE, "last": covers[-1], "match": covers[-1]} if covers else res
    if action_type == "PROMOTE_TO_EXACT":
        pr = matching(actions, **ident, family=("PROMOTE_TO_EXACT",))
        return {"state": DONE, "last": pr[-1], "match": pr[-1]} if pr else res
    return res


def describe(a: dict, symbol="$") -> str:
    """Testo breve dell'azione registrata: «04/11/2026 · OFFERTA ABBASSATA · $0.81 → $0.77»."""
    when = str(a.get("executed_at") or "")[:10]
    try:
        y, m, d = when.split("-")
        when = f"{d}/{m}/{y}"
    except ValueError:
        pass
    txt = f"{when} · {ACTION_WORDS.get(a['action_type'], a['action_type'])}"
    if a.get("value_after") is not None:
        before = "n/d" if a.get("value_before") is None else f"{symbol}{float(a['value_before']):.2f}"
        txt += f" · {before} → {symbol}{float(a['value_after']):.2f}"
    return txt
