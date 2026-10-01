"""Suggerimenti di Amazon (es. Campaign Overview Beta) registrati A MANO. Nessun browser, nessuna rete.
Amazon e' evidenza di audit: NON guida la decisione, serve a misurare l'accordo con il nostro motore."""
from datetime import date, datetime
from pathlib import Path

from .metrics import normalize_term, product_asin
from . import registry

DIRECTIONS = ("REDUCE_BID", "INCREASE_BID", "PAUSE", "ADD_NEGATIVE", "REDUCE_PLACEMENT_MODIFIER", "NO_ACTION", "UNSPECIFIED")
AGREEMENTS = ("AGREE", "DISAGREE", "PARTIAL", "NOT_COMPARABLE")


class AmazonRecError(ValueError):
    pass


def target_identity(target: str | None) -> str:
    """Stessa identita' usata per gli action_key: keyword normalizzata, oppure ASIN minuscolo."""
    if not target:
        return ""
    a = product_asin(target)
    return a if a else normalize_term(target)


def _d(v, label):
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise AmazonRecError(f"{label}: data non valida (atteso YYYY-MM-DD): {v!r}")


def _num(v, label):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise AmazonRecError(f"{label}: numero non valido: {v!r}")


def record(conn, rec: dict, settings: dict) -> int:
    """Salva un suggerimento Amazon. Valida libro e campagna; rifiuta duplicati; non inventa percentuali."""
    r = dict(rec)
    book_id, campaign_id = str(r.get("book_id", "")).strip(), str(r.get("campaign_id", "")).strip()
    if book_id not in registry.load_books(settings["books_path"]):
        raise AmazonRecError(f"book_id {book_id!r} non presente in books.json")
    folder = registry.find_campaign_dir(settings["input_dir"], book_id, campaign_id)       # struttura legacy o per marketplace
    if folder is None or not (folder / "metadata.json").exists():
        raise AmazonRecError(f"campagna {book_id}/{campaign_id} non registrata (metadata.json mancante)")
    pf, pt = _d(r.get("period_from"), "period_from"), _d(r.get("period_to"), "period_to")
    if pf > pt:
        raise AmazonRecError("period_from successiva a period_to")
    for k in ("recommendation_type", "source"):
        if not str(r.get(k) or "").strip():
            raise AmazonRecError(f"campo obbligatorio mancante: {k}")
    direction = (r.get("suggested_direction") or "UNSPECIFIED").upper()
    if direction not in DIRECTIONS:
        raise AmazonRecError(f"suggested_direction deve essere una tra {DIRECTIONS}")
    target = (r.get("target") or "").strip() or None
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO amazon_recommendations (recorded_at,book_id,campaign_id,period_from,period_to,source,
                   recommendation_type,target,target_normalized,observed_spend,observed_orders,observation,
                   suggested_action,suggested_direction,suggested_pct,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(timespec="seconds"), book_id, campaign_id, pf, pt, r["source"].strip(),
                 r["recommendation_type"].strip(), target, target_identity(target), _num(r.get("observed_spend"), "observed_spend"),
                 _num(r.get("observed_orders"), "observed_orders"), r.get("observation"), r.get("suggested_action"),
                 direction, _num(r.get("suggested_pct"), "suggested_pct"), r.get("notes")))
    except Exception as e:      # UNIQUE
        if "UNIQUE" in str(e):
            raise AmazonRecError("suggerimento gia' registrato (stesso libro/campagna/periodo/tipo/target)") from e
        raise
    return cur.lastrowid


def load(conn, book_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM amazon_recommendations WHERE book_id=? ORDER BY id", (book_id,))]


def agreement(final_action: str, direction: str, growth: bool = False) -> str:
    """Accordo direzionale fra la decisione finale e il suggerimento Amazon (nessuna percentuale confrontata).
    REDUCE_BID: bid-down/pausa/negative = AGREE; HOLD = DISAGREE; WATCH/NO_ACTION/INSUFFICIENT = PARTIAL.
    INCREASE_BID: bid-down/pausa = DISAGREE; altro = PARTIAL. PAUSE: pausa = AGREE, bid-down = PARTIAL, HOLD = DISAGREE.
    ADD_NEGATIVE: negative = AGREE, pausa/bid-down = PARTIAL, HOLD = DISAGREE. Placement/no action/unspecified = NOT_COMPARABLE."""
    if final_action.startswith("REVIEW"):          # REVIEW_DELIVERY_BEFORE_BID_CHANGE: non e' ne' un bid-down ne' un HOLD
        return "PARTIAL" if direction in ("REDUCE_BID", "PAUSE", "ADD_NEGATIVE", "INCREASE_BID") else "NOT_COMPARABLE"
    down = final_action.startswith("BID_DOWN")
    strong = final_action in ("PAUSE_CANDIDATE", "NEGATIVE_CANDIDATE")
    if direction == "REDUCE_BID":
        return "AGREE" if (down or strong) else "DISAGREE" if final_action == "HOLD" else "PARTIAL"
    if direction == "INCREASE_BID":
        return "DISAGREE" if (down or strong) else "PARTIAL"
    if direction == "PAUSE":
        return "AGREE" if final_action == "PAUSE_CANDIDATE" else "PARTIAL" if (down or strong) or final_action != "HOLD" else "DISAGREE"
    if direction == "ADD_NEGATIVE":
        return "AGREE" if final_action == "NEGATIVE_CANDIDATE" else "PARTIAL" if (down or strong) or final_action != "HOLD" else "DISAGREE"
    return "NOT_COMPARABLE"
