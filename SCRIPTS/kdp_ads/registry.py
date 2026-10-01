"""Registro libri (CONFIG/books.json) e metadata campagna (INPUT/<BOOK_ID>/<CAMPAIGN_ID>/metadata.json).
Nessun parametro economico e' hardcodato nel codice: tutto arriva da qui."""
import json
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple

PHASES = ("LAUNCH", "GROWTH", "PROFIT_STABLE")
SCOPES = ("LIFETIME", "DATE_RANGE", "UNKNOWN")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")   # sicuro come nome cartella
MP_RE = re.compile(r"^[A-Z]{2,3}$")                      # codice marketplace: US, UK, DE, IT, FR, ES, CA, AU ...
CUR_RE = re.compile(r"^[A-Z]{3}$")                       # valuta: USD, GBP, EUR ...
MARKETPLACE_FIELDS = ("marketplace", "currency", "price", "royalty_per_copy", "break_even_acos", "target_acos", "phase",
                      "active", "rule_overrides")


class RegistryError(ValueError):
    pass


class DuplicateError(RegistryError):
    pass


def break_even_calc(price: float, royalty: float) -> float:
    return round(royalty / price * 100, 2)


# ---------------------------------------------------------------- marketplace
# MODELLO. Un libro (book_id, title) puo' avere piu' marketplace; ognuno ha valuta, prezzo, royalty, target ACOS, break-even e fase.
#   * forma LEGACY (libro pilota): i campi economici sono nel libro stesso => UN marketplace implicito (quello dichiarato).
#   * forma NUOVA: "marketplaces": [ {marketplace, currency, price, royalty_per_copy, target_acos, break_even_acos, phase}, ... ].
#   Un libro legacy puo' ricevere marketplace aggiuntivi nella lista "marketplaces" senza toccare i campi legacy.
# Tutto il codice a valle lavora su una VISTA piatta (libro + un marketplace): stessa forma del vecchio libro.
def legacy_marketplace(book: dict) -> str | None:
    m = str(book.get("marketplace") or "").strip().upper()
    return m or None


def is_legacy_shape(book: dict) -> bool:
    return legacy_marketplace(book) is not None


def marketplaces_of(book: dict) -> dict[str, dict]:
    """{codice: vista piatta del libro per quel marketplace}. I campi del libro (book_id, title) sono condivisi."""
    base = {k: book[k] for k in ("book_id", "title") if k in book}
    book_active = bool(book.get("active", True))
    out: dict[str, dict] = {}
    lm = legacy_marketplace(book)
    if lm:
        out[lm] = {**base, **{k: book[k] for k in MARKETPLACE_FIELDS if k in book}, "marketplace": lm, "active": book_active}
    for m in book.get("marketplaces") or []:
        code = str(m.get("marketplace") or "").strip().upper()
        if not code:
            raise RegistryError(f"libro {book.get('book_id')}: marketplace senza codice")
        if code in out:
            raise RegistryError(f"libro {book.get('book_id')}: marketplace duplicato ({code})")
        v = {**base, **{k: m[k] for k in MARKETPLACE_FIELDS if k in m}, "marketplace": code,
             "active": bool(m.get("active", True)) and book_active}
        ro = {**(book.get("rule_overrides") or {}), **(m.get("rule_overrides") or {})}
        if ro:
            v["rule_overrides"] = ro
        out[code] = v
    return out


def book_view(book: dict, marketplace: str | None = None) -> dict:
    """Vista piatta (libro + marketplace). Senza marketplace e' valida solo se il libro ne ha UNO; altrimenti errore esplicito:
    mai scegliere in silenzio fra marketplace diversi."""
    mps = marketplaces_of(book)
    bid = book.get("book_id")
    if marketplace in (None, ""):
        if len(mps) == 1:
            return next(iter(mps.values()))
        if not mps:
            raise RegistryError(f"il libro {bid} non ha marketplace configurati: aggiungine uno")
        raise RegistryError(f"il libro {bid} ha piu' marketplace ({', '.join(mps)}): specifica quale")
    code = str(marketplace).strip().upper()
    if code not in mps:
        raise RegistryError(f"marketplace {code} non configurato per il libro {bid} (configurati: {', '.join(mps) or 'nessuno'})")
    return mps[code]


def multi_marketplace_books(books: dict, only_book: str | None = None) -> dict[str, list[str]]:
    """{book_id: [marketplace, ...]} per i libri selezionati che hanno PIU' DI UN marketplace (per cui la CLI richiede --marketplace)."""
    out = {}
    for bid, b in books.items():
        if only_book and bid != only_book:
            continue
        try:
            mps = sorted(marketplaces_of(b))
        except RegistryError:
            continue
        if len(mps) > 1:
            out[bid] = mps
    return out


def primary_marketplace(book: dict) -> str | None:
    """Marketplace 'principale': quello legacy, altrimenti il primo configurato."""
    mps = marketplaces_of(book)
    return legacy_marketplace(book) or (next(iter(mps)) if mps else None)


def run_marketplace(book: dict, stored: str | None) -> str | None:
    """Marketplace di un run salvato. I run storici (prima del multi-marketplace) non lo registrano (NULL): appartengono al
    marketplace legacy del libro (o all'unico configurato)."""
    if stored:
        return str(stored).strip().upper()
    mps = marketplaces_of(book)
    return legacy_marketplace(book) or (next(iter(mps)) if len(mps) == 1 else None)


def file_tag(book: dict, marketplace: str) -> str:
    """Etichetta per i nomi dei file di output: <BOOK_ID> se il libro ha un solo marketplace, altrimenti <BOOK_ID>_<MP>."""
    return str(book["book_id"]) if len(marketplaces_of(book)) <= 1 else f"{book['book_id']}_{str(marketplace).upper()}"


def validate_marketplace_entry(m: dict) -> list[str]:
    errs = validate_book({"book_id": "x", "title": "x", **m})
    code, cur = str(m.get("marketplace") or "").strip().upper(), str(m.get("currency") or "").strip().upper()
    if code and not MP_RE.match(code):
        errs.append("marketplace: codice non valido (2-3 lettere, es. US, UK, DE)")
    if cur and not CUR_RE.match(cur):
        errs.append("currency: codice non valido (3 lettere, es. USD, GBP, EUR)")
    return errs


def _save_books(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_book_shell(books_path, input_dir, book_id: str, title: str) -> dict:
    """Nuovo libro SENZA marketplace (si aggiungono dopo). Crea INPUT/<book_id>/. Non sovrascrive mai."""
    book_id, title = str(book_id or "").strip(), str(title or "").strip()
    if not book_id or not ID_RE.match(book_id):
        raise RegistryError("book_id mancante o con caratteri non validi")
    if not title:
        raise RegistryError("title mancante")
    if book_id in load_books(books_path):
        raise DuplicateError(f"book_id gia' presente: {book_id}")
    p = Path(books_path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"books": []}
    book = {"book_id": book_id, "title": title, "active": True, "marketplaces": []}
    data.setdefault("books", []).append(book)
    _save_books(p, data)
    (Path(input_dir) / book_id).mkdir(parents=True, exist_ok=True)
    return book


def add_marketplace(books_path, input_dir, book_id: str, mp: dict) -> dict:
    """Aggiunge un marketplace a un libro esistente (anche legacy: i suoi campi legacy restano intatti). Nessuna conversione di valuta.
    Crea INPUT/<book_id>/<MP>/ quando le campagne di quel marketplace usano la struttura nidificata."""
    mp = dict(mp)
    mp["marketplace"] = str(mp.get("marketplace") or "").strip().upper()
    mp["currency"] = str(mp.get("currency") or "").strip().upper()
    errs = validate_marketplace_entry(mp)
    if errs:
        raise RegistryError("; ".join(errs))
    for k in ("price", "royalty_per_copy", "target_acos"):
        mp[k] = float(mp[k])
    mp["break_even_acos"] = break_even_calc(mp["price"], mp["royalty_per_copy"]) if mp.get("break_even_acos") is None else float(mp["break_even_acos"])
    mp.setdefault("active", True)
    p = Path(books_path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"books": []}
    entry = next((b for b in data.get("books", []) if str(b.get("book_id")) == str(book_id)), None)
    if entry is None:
        raise RegistryError(f"book_id non presente in books.json: {book_id}")
    if mp["marketplace"] in marketplaces_of(entry):
        raise DuplicateError(f"il libro {book_id} ha gia' il marketplace {mp['marketplace']}")
    entry.setdefault("marketplaces", []).append(mp)
    _save_books(p, data)
    if nested_layout(entry, mp["marketplace"]):
        (Path(input_dir) / str(book_id) / mp["marketplace"]).mkdir(parents=True, exist_ok=True)
    return mp


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


def check_campaign_against_book(meta: dict, book: dict | None, folder_book: str, folder_campaign: str,
                                folder_marketplace: str | None = None) -> list[str]:
    """Validazioni incrociate: nessun file viene mai assegnato in automatico a un altro libro/campagna/marketplace.
    Il marketplace del metadata deve essere uno di quelli configurati per il libro (con la stessa valuta) e, se la campagna sta in
    una cartella marketplace, coincidere con essa."""
    errs = []
    if book is None:
        return [f"book_id '{folder_book}' (cartella) non presente in books.json"]
    if str(meta.get("book_id")) != folder_book:
        errs.append(f"book_id del metadata ({meta.get('book_id')}) diverso dalla cartella libro ({folder_book})")
    if str(meta.get("campaign_id")) != folder_campaign:
        errs.append(f"campaign_id del metadata ({meta.get('campaign_id')}) diverso dalla cartella campagna ({folder_campaign})")
    mp = str(meta.get("marketplace") or "").strip().upper()
    try:
        view = book_view(book, mp)
    except RegistryError as e:
        errs.append(f"marketplace del metadata ({meta.get('marketplace')}) non valido per il libro: {e}")
        view = None
    if view is not None and meta.get("currency") != view.get("currency"):
        errs.append(f"currency del metadata ({meta.get('currency')}) diversa da quella del marketplace {mp} ({view.get('currency')})")
    if folder_marketplace and mp != str(folder_marketplace).upper():
        errs.append(f"marketplace del metadata ({mp}) diverso dalla cartella marketplace ({str(folder_marketplace).upper()})")
    return errs


class CampaignLoc(NamedTuple):
    path: Path
    campaign_id: str
    marketplace: str | None      # codice della cartella marketplace; None = struttura legacy INPUT/<BOOK>/<CAMPAIGN>/


def nested_layout(book: dict, marketplace: str) -> bool:
    """Struttura cartelle: INPUT/<BOOK>/<MP>/<CAMPAIGN>/ per i nuovi dati. Solo il marketplace LEGACY di un libro legacy continua
    a usare INPUT/<BOOK>/<CAMPAIGN>/ (nessun file esistente viene spostato)."""
    return not (is_legacy_shape(book) and str(marketplace).strip().upper() == legacy_marketplace(book))


def campaign_dir_for(input_dir, book: dict, marketplace: str, campaign_id: str) -> Path:
    base = Path(input_dir) / str(book["book_id"])
    return base / str(marketplace).strip().upper() / str(campaign_id) if nested_layout(book, marketplace) else base / str(campaign_id)


def iter_campaigns(input_dir, book_id, book: dict | None = None) -> list[CampaignLoc]:
    """Tutte le cartelle campagna del libro, in entrambe le strutture. Una sottocartella con metadata.json e' una campagna legacy;
    una sottocartella con nome di marketplace (senza metadata.json) contiene campagne nidificate."""
    base = Path(input_dir) / str(book_id)
    out: list[CampaignLoc] = []
    if not base.is_dir():
        return out
    try:
        known = set(marketplaces_of(book)) if book else set()
    except RegistryError:
        known = set()
    for child in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if (child / "metadata.json").is_file():
            out.append(CampaignLoc(child, child.name, None))
        elif child.name.upper() in known or re.fullmatch(r"[A-Za-z]{2,3}", child.name):
            for sub in sorted(p for p in child.iterdir() if p.is_dir() and not p.name.startswith("_")):
                out.append(CampaignLoc(sub, sub.name, child.name.upper()))
        else:
            out.append(CampaignLoc(child, child.name, None))        # senza metadata.json: l'import la rifiuta con messaggio chiaro
    return out


def find_campaign_dir(input_dir, book_id, campaign_id) -> Path | None:
    """Cartella della campagna in una qualunque struttura (gli ID campagna sono unici nel libro, fra tutti i marketplace)."""
    for loc in iter_campaigns(input_dir, book_id):
        if loc.campaign_id == str(campaign_id):
            return loc.path
    return None


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
    mp = str(meta["marketplace"]).strip().upper()
    existing = find_campaign_dir(input_dir, meta["book_id"], meta["campaign_id"])
    if existing is not None:
        if not overwrite:
            raise DuplicateError(f"campagna gia' esistente: {existing}")
        old, _ = load_metadata(existing)
        if old and str(old.get("marketplace") or "").strip().upper() not in ("", mp):
            raise RegistryError("la campagna esiste gia' in un altro marketplace: gli ID campagna sono unici nel libro")
        folder = existing
    else:
        folder = campaign_dir_for(input_dir, book, mp, meta["campaign_id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder


def add_snapshot(campaign_dir, entry: dict, overwrite: bool = False) -> dict:
    """Dichiara lo SNAPSHOT di un CSV gia' presente nella cartella campagna (metadata.json -> snapshots).
    Non sposta/modifica i CSV. Un file gia' dichiarato richiede overwrite=True. Ritorna il nuovo metadata."""
    folder = Path(campaign_dir)
    meta, errs = load_metadata(folder)
    if meta is None or errs:
        raise RegistryError("; ".join(errs))
    if not (folder / str(entry.get("file", ""))).is_file():
        raise RegistryError(f"CSV non presente nella cartella campagna: {entry.get('file')}")
    old = meta.get("snapshots") or []
    snaps = [s for s in old if s["file"] != entry["file"]]
    if len(snaps) != len(old) and not overwrite:
        raise DuplicateError(f"snapshot gia' dichiarato per il file {entry['file']}")
    new = {**meta, "snapshots": snaps + [entry]}
    errs = validate_metadata(new)
    if errs:
        raise RegistryError("; ".join(errs))
    (folder / "metadata.json").write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return new
