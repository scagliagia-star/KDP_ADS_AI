"""Registro libri (CONFIG/books.json) e metadata campagna (INPUT/<BOOK_ID>/<CAMPAIGN_ID>/metadata.json).
Nessun parametro economico e' hardcodato nel codice: tutto arriva da qui."""
import json
import re
from datetime import date
from pathlib import Path

PHASES = ("LAUNCH", "GROWTH", "PROFIT_STABLE")
SCOPES = ("LIFETIME", "DATE_RANGE", "UNKNOWN")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")   # sicuro come nome cartella


class RegistryError(ValueError):
    pass


class DuplicateError(RegistryError):
    pass


def break_even_calc(price: float, royalty: float) -> float:
    return round(royalty / price * 100, 2)


# ---------------------------------------------------------------- libri
def load_books(path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for b in data.get("books", []):
        bid = str(b.get("book_id", "")).strip()
        if bid in out:
            raise RegistryError(f"book_id duplicato in books.json: {bid}")
        out[bid] = b
    return out


def validate_book(b: dict) -> list[str]:
    errs = []
    bid = str(b.get("book_id", "")).strip()
    if not bid or not ID_RE.match(bid):
        errs.append("book_id mancante o con caratteri non validi")
    if not str(b.get("title", "")).strip():
        errs.append("title mancante")
    if not str(b.get("marketplace", "")).strip():
        errs.append("marketplace mancante")
    if not str(b.get("currency", "")).strip():
        errs.append("currency mancante")
    try:
        if not float(b.get("price")) > 0:
            errs.append("price deve essere > 0")
    except (TypeError, ValueError):
        errs.append("price non numerico")
    try:
        if float(b.get("royalty_per_copy")) < 0:
            errs.append("royalty_per_copy deve essere >= 0")
    except (TypeError, ValueError):
        errs.append("royalty_per_copy non numerico")
    try:
        if not float(b.get("target_acos")) > 0:
            errs.append("target_acos deve essere > 0")
    except (TypeError, ValueError):
        errs.append("target_acos non numerico")
    if b.get("phase") not in PHASES:
        errs.append(f"phase deve essere una tra {PHASES}")
    if b.get("break_even_acos") is not None:
        try:
            float(b["break_even_acos"])
        except (TypeError, ValueError):
            errs.append("break_even_acos non numerico")
    return errs


def book_economics(b: dict, tolerance_pp: float = 0.5) -> tuple[dict, list[str]]:
    """Ritorna (valori economici con break-even calcolato/configurato/effettivo, warning)."""
    calc = break_even_calc(float(b["price"]), float(b["royalty_per_copy"]))
    conf = float(b["break_even_acos"]) if b.get("break_even_acos") is not None else None
    warns = []
    if conf is not None and abs(conf - calc) > tolerance_pp:
        warns.append(f"libro {b['book_id']}: break_even_acos configurato ({conf}) differisce dal calcolato "
                     f"royalty/price ({calc}) di oltre {tolerance_pp} pp; uso il configurato")
    return {"break_even_acos_calculated": calc, "break_even_acos_configured": conf,
            "break_even_acos": conf if conf is not None else calc}, warns


def add_book(books_path, input_dir, book: dict) -> dict:
    """Aggiunge un libro a books.json e crea INPUT/<book_id>/. Non sovrascrive mai libri esistenti."""
    book = dict(book)
    book["book_id"] = str(book.get("book_id", "")).strip()
    errs = validate_book(book)
    if errs:
        raise RegistryError("; ".join(errs))
    for k in ("price", "royalty_per_copy", "target_acos"):
        book[k] = float(book[k])
    book["break_even_acos"] = break_even_calc(book["price"], book["royalty_per_copy"]) \
        if book.get("break_even_acos") is None else float(book["break_even_acos"])
    book.setdefault("active", True)
    books = load_books(books_path)
    if book["book_id"] in books:
        raise DuplicateError(f"book_id gia' presente: {book['book_id']}")
    p = Path(books_path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"books": []}
    data.setdefault("books", []).append(book)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (Path(input_dir) / book["book_id"]).mkdir(parents=True, exist_ok=True)
    return book


# ------------------------------------------------------------- campagne
def _iso(v, label, errs):
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        errs.append(f"{label}: data non valida (atteso YYYY-MM-DD): {v!r}")
        return None


def validate_metadata(meta: dict) -> list[str]:
    errs = []
    for k in ("book_id", "campaign_id", "campaign_name", "campaign_type", "marketplace", "currency"):
        if not str(meta.get(k) or "").strip():
            errs.append(f"campo obbligatorio mancante: {k}")
    if meta.get("campaign_id") and not ID_RE.match(str(meta["campaign_id"])):
        errs.append("campaign_id con caratteri non validi per un nome cartella")
    for prefix in ("target", "search_term"):
        _check_scope(prefix, meta.get(f"{prefix}_report_scope"), meta.get(f"{prefix}_date_from"),
                     meta.get(f"{prefix}_date_to"), errs)
    snaps = meta.get("snapshots")
    if snaps is not None:
        if not isinstance(snaps, list):
            errs.append("snapshots deve essere una lista")
        else:
            seen = set()
            for i, s in enumerate(snaps):
                lab = f"snapshots[{i}]"
                if not isinstance(s, dict) or not str(s.get("file") or "").strip():
                    errs.append(f"{lab}: campo 'file' mancante")
                    continue
                if s["file"] in seen:
                    errs.append(f"{lab}: file duplicato ({s['file']})")
                seen.add(s["file"])
                if s.get("report_type") not in (None, "TARGETING", "SEARCH_TERM"):
                    errs.append(f"{lab}: report_type deve essere TARGETING o SEARCH_TERM")
                _check_scope(lab, s.get("report_scope"), s.get("date_from"), s.get("date_to"), errs)
    return errs


def _check_scope(label, scope, d_from_raw, d_to_raw, errs):
    if scope not in SCOPES:
        errs.append(f"{label}_report_scope deve essere uno tra {SCOPES}" if "[" not in label else f"{label}: report_scope deve essere uno tra {SCOPES}")
        return
    d_from, d_to = _iso(d_from_raw, f"{label}_date_from", errs), _iso(d_to_raw, f"{label}_date_to", errs)
    if scope == "DATE_RANGE" and not (d_from and d_to):
        errs.append(f"{label}: DATE_RANGE richiede date_from e date_to")
    if scope == "LIFETIME" and d_from:
        errs.append(f"{label}: LIFETIME non deve avere date_from")
    if scope == "UNKNOWN" and (d_from or d_to):
        errs.append(f"{label}: UNKNOWN non deve avere date")
    if d_from and d_to and d_from > d_to:
        errs.append(f"{label}: date_from successiva a date_to")


def load_metadata(campaign_dir: Path) -> tuple[dict | None, list[str]]:
    f = Path(campaign_dir) / "metadata.json"
    if not f.exists():
        return None, [f"metadata.json mancante in {campaign_dir}"]
    try:
        meta = json.loads(f.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return None, [f"metadata.json non e' JSON valido: {e}"]
    if not isinstance(meta, dict):
        return None, ["metadata.json deve essere un oggetto JSON"]
    return meta, validate_metadata(meta)


def check_campaign_against_book(meta: dict, book: dict | None, folder_book: str, folder_campaign: str) -> list[str]:
    """Validazioni incrociate: nessun file viene mai assegnato in automatico a un altro libro/campagna."""
    errs = []
    if book is None:
        return [f"book_id '{folder_book}' (cartella) non presente in books.json"]
    if str(meta.get("book_id")) != folder_book:
        errs.append(f"book_id del metadata ({meta.get('book_id')}) diverso dalla cartella libro ({folder_book})")
    if str(meta.get("campaign_id")) != folder_campaign:
        errs.append(f"campaign_id del metadata ({meta.get('campaign_id')}) diverso dalla cartella campagna ({folder_campaign})")
    if meta.get("marketplace") != book.get("marketplace"):
        errs.append(f"marketplace del metadata ({meta.get('marketplace')}) diverso da quello del libro ({book.get('marketplace')})")
    if meta.get("currency") != book.get("currency"):
        errs.append(f"currency del metadata ({meta.get('currency')}) diversa da quella del libro ({book.get('currency')})")
    return errs


def create_campaign(input_dir, books_path, meta: dict, overwrite: bool = False) -> Path:
    meta = dict(meta)
    errs = validate_metadata(meta)
    if errs:
        raise RegistryError("; ".join(errs))
    books = load_books(books_path)
    book = books.get(str(meta["book_id"]))
    errs = check_campaign_against_book(meta, book, str(meta["book_id"]), str(meta["campaign_id"]))
    if errs:
        raise RegistryError("; ".join(errs))
    folder = Path(input_dir) / str(meta["book_id"]) / str(meta["campaign_id"])
    f = folder / "metadata.json"
    if f.exists() and not overwrite:
        raise DuplicateError(f"campagna gia' esistente: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder
