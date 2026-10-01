"""Libri, marketplace e campagne: elenco, aggiunta. Riusa kdp_ads.registry (la stessa logica di add_book.py / add_campaign.py):
qui ci sono solo il parsing dei campi del form e i controlli di input; validazione, scrittura di books.json e creazione delle
cartelle sono fatte da registry.

Modello: LIBRO -> MARKETPLACE -> CAMPAGNE. Ogni marketplace ha la propria valuta, prezzo, royalty, ACOS: nessuna conversione di valuta.
Cartelle: INPUT/<BOOK>/<MP>/<CAMPAIGN>/ per i nuovi dati; il libro legacy continua a usare INPUT/<BOOK>/<CAMPAIGN>/ (nulla viene spostato)."""
import math

from kdp_ads import registry

from . import OpResult, ValidationError

CAMPAIGN_TYPES = ("EXACT", "PHRASE", "COMPETITOR", "OTHER")      # il registro accetta qualunque tipo descrittivo
MARKETPLACE_CHOICES = ("US", "UK", "DE", "IT", "FR", "ES", "CA", "AU")   # suggerimenti; il codice puo' essere un altro (2-3 lettere)
CURRENCY_BY_MARKETPLACE = {"US": "USD", "UK": "GBP", "DE": "EUR", "IT": "EUR", "FR": "EUR", "ES": "EUR", "CA": "CAD", "AU": "AUD"}


def parse_decimal(text, label) -> float:
    s = str(text if text is not None else "").strip().replace(",", ".")
    if not s:
        raise ValidationError(f"{label}: campo obbligatorio")
    try:
        v = float(s)
    except ValueError:
        raise ValidationError(f"{label}: valore numerico non valido ({text!r})")
    if math.isnan(v) or math.isinf(v):
        raise ValidationError(f"{label}: valore numerico non valido ({text!r})")
    return v


# ------------------------------------------------------------------ lettura
def _book(ws, book_id):
    return registry.load_books(ws.books_path).get(str(book_id))


def list_campaigns(ws, book_id, marketplace=None) -> list[dict]:
    """Campagne del libro in entrambe le strutture, con il marketplace di ciascuna (dal metadata; altrimenti dalla cartella)."""
    book = _book(ws, book_id)
    want = str(marketplace).strip().upper() if marketplace else None
    out = []
    for loc in registry.iter_campaigns(ws.input_dir, book_id, book):
        if not (loc.path / "metadata.json").is_file():
            continue
        meta, errs = registry.load_metadata(loc.path)
        meta = meta or {}
        mp = str(meta.get("marketplace") or loc.marketplace or "").strip().upper() or None
        if want and mp != want:
            continue
        out.append({"campaign_id": loc.campaign_id, "campaign_name": meta.get("campaign_name"), "campaign_type": meta.get("campaign_type"),
                    "marketplace": mp, "snapshots": len(meta.get("snapshots") or []), "csv_files": len(list(loc.path.glob("*.csv"))),
                    "valid": not errs, "errors": errs, "path": str(loc.path)})
    return sorted(out, key=lambda c: (c["marketplace"] or "", c["campaign_id"]))


def list_books(ws) -> list[dict]:
    """Un dict per libro, con la lista dei suoi marketplace (economia propria). Le chiavi 'price', 'royalty_per_copy', ...
    riflettono il marketplace principale (compatibilita' con il formato precedente)."""
    out = []
    tol = ws.settings.get("break_even_mismatch_tolerance_pp", 0.5)
    for bid, b in registry.load_books(ws.books_path).items():
        try:
            views = registry.marketplaces_of(b)
        except registry.RegistryError:
            views = {}
        camps = list_campaigns(ws, bid)
        mps = []
        for code, v in views.items():
            try:
                be = registry.book_economics(v, tol)[0]["break_even_acos"]
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                be = None
            mps.append({"marketplace": code, "currency": v.get("currency"), "price": v.get("price"),
                        "royalty_per_copy": v.get("royalty_per_copy"), "target_acos": v.get("target_acos"), "break_even_acos": be,
                        "phase": v.get("phase"), "active": v.get("active", True),
                        "campaigns": sum(1 for c in camps if c["marketplace"] == code)})
        main = next((m for m in mps if m["marketplace"] == registry.primary_marketplace(b)), mps[0] if mps else {})
        out.append({"book_id": bid, "title": b.get("title"), "active": b.get("active", True), "marketplaces": mps,
                    "price": main.get("price"), "royalty_per_copy": main.get("royalty_per_copy"), "target_acos": main.get("target_acos"),
                    "break_even_acos": main.get("break_even_acos"), "marketplace": main.get("marketplace"),
                    "currency": main.get("currency"), "phase": main.get("phase"), "campaigns": len(camps)})
    return out


def list_marketplaces(ws, book_id) -> list[dict]:
    b = next((x for x in list_books(ws) if x["book_id"] == str(book_id)), None)
    return b["marketplaces"] if b else []


# ------------------------------------------------------------------ aggiunta libro (formato precedente: libro + 1 marketplace)
def _econ_errors(f, errors):
    def num(key, label):
        try:
            return parse_decimal(f.get(key), label)
        except ValidationError as e:
            errors.append(str(e))
            return None
    price, royalty, target = num("price", "Prezzo"), num("royalty", "Royalty"), num("target_acos", "ACOS obiettivo")
    if price is not None and price <= 0:
        errors.append("Il prezzo deve essere maggiore di zero")
    if royalty is not None and royalty <= 0:
        errors.append("La royalty deve essere maggiore di zero")
    if price is not None and royalty is not None and price > 0 and royalty > price:
        errors.append("La royalty non può superare il prezzo")
    if target is not None and not (0 < target <= 100):
        errors.append("ACOS obiettivo non valido: deve essere compreso tra 0 e 100 (%)")
    marketplace, currency = str(f.get("marketplace") or "").strip().upper(), str(f.get("currency") or "").strip().upper()
    if not marketplace:
        errors.append("Marketplace: campo obbligatorio")
    if not currency:
        errors.append("Valuta: campo obbligatorio")
    if f.get("phase") not in registry.PHASES:
        errors.append("Fase libro non valida")
    return price, royalty, target, marketplace, currency


def add_book(ws, f: dict) -> OpResult:
    """Libro completo in un passo (un solo marketplace, formato precedente). La dashboard usa add_book_shell + add_marketplace."""
    errors = []
    title, book_id = str(f.get("title") or "").strip(), str(f.get("book_id") or "").strip()
    if not title:
        errors.append("Titolo: campo obbligatorio")
    if not book_id:
        errors.append("Book ID / ASIN: campo obbligatorio")
    price, royalty, target, marketplace, currency = _econ_errors(f, errors)
    if errors:
        return OpResult(False, errors)
    book = {"book_id": book_id, "title": title, "marketplace": marketplace, "currency": currency, "price": price,
            "royalty_per_copy": royalty, "target_acos": target, "phase": f["phase"], "active": True}
    errs = registry.validate_book(book)
    if errs:
        return OpResult(False, errs)
    try:
        saved = registry.add_book(ws.books_path, ws.input_dir, book)
    except registry.DuplicateError:
        return OpResult(False, [f"Esiste già un libro con Book ID {book_id}: non lo sovrascrivo"], data={"duplicate": True})
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Libro aggiunto correttamente", data=saved)


# ------------------------------------------------------------------ nuovo flusso: libro, poi marketplace
def add_book_shell(ws, f: dict) -> OpResult:
    """Aggiunge il LIBRO (titolo + Book ID/ASIN), senza marketplace. Il titolo e il book_id sono proprieta' del libro."""
    errors = []
    title, book_id = str(f.get("title") or "").strip(), str(f.get("book_id") or "").strip()
    if not title:
        errors.append("Titolo: campo obbligatorio")
    if not book_id:
        errors.append("Book ID / ASIN: campo obbligatorio")
    if errors:
        return OpResult(False, errors)
    try:
        saved = registry.add_book_shell(ws.books_path, ws.input_dir, book_id, title)
    except registry.DuplicateError:
        return OpResult(False, [f"Esiste già un libro con Book ID {book_id}: non lo sovrascrivo"], data={"duplicate": True})
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Libro aggiunto correttamente", data=saved)


def add_marketplace(ws, f: dict) -> OpResult:
    """Aggiunge un MARKETPLACE a un libro esistente: valuta, prezzo, royalty, ACOS obiettivo, fase. Nessuna conversione di valuta."""
    errors = []
    book_id = str(f.get("book_id") or "").strip()
    if _book(ws, book_id) is None:
        errors.append("Libro non trovato: aggiungilo prima")
    price, royalty, target, marketplace, currency = _econ_errors(f, errors)
    if marketplace and not registry.MP_RE.match(marketplace):
        errors.append("Marketplace: codice non valido (2-3 lettere, es. US, UK, DE)")
    if currency and not registry.CUR_RE.match(currency):
        errors.append("Valuta: codice non valido (3 lettere, es. USD, GBP, EUR)")
    if errors:
        return OpResult(False, errors)
    mp = {"marketplace": marketplace, "currency": currency, "price": price, "royalty_per_copy": royalty,
          "target_acos": target, "phase": f["phase"], "active": True}
    try:
        saved = registry.add_marketplace(ws.books_path, ws.input_dir, book_id, mp)
    except registry.DuplicateError:
        return OpResult(False, [f"Il libro {book_id} ha già il marketplace {marketplace}: non lo sovrascrivo"], data={"duplicate": True})
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Marketplace aggiunto correttamente", data=saved)


# ------------------------------------------------------------------ aggiunta campagna
def add_campaign(ws, f: dict, overwrite: bool = False) -> OpResult:
    """f: book_id, marketplace, campaign_id, campaign_name, campaign_type. Il marketplace e' obbligatorio (puo' mancare solo se il
    libro ne ha uno). Gli ID campagna sono unici nel libro. Con overwrite=True aggiorna nome/tipo mantenendo gli snapshot."""
    book_id, cid = str(f.get("book_id") or "").strip(), str(f.get("campaign_id") or "").strip()
    errors = []
    book = _book(ws, book_id)
    view = None
    if book is None:
        errors.append("Libro non trovato: aggiungilo prima in 'Aggiungi libro'")
    else:
        try:
            view = registry.book_view(book, f.get("marketplace") or None)
        except registry.RegistryError as e:
            errors.append(f"Marketplace: {e}" if f.get("marketplace") else
                          "Marketplace: campo obbligatorio (aggiungi prima un marketplace al libro e sceglilo qui)")
    if not cid:
        errors.append("ID campagna: campo obbligatorio")
    ctype = str(f.get("campaign_type") or "").strip().upper()
    if not ctype:
        errors.append("Tipo campagna: campo obbligatorio")
    if errors:
        return OpResult(False, errors)
    mp = view["marketplace"]
    meta = {"book_id": book_id, "campaign_id": cid, "campaign_name": str(f.get("campaign_name") or "").strip() or cid,
            "campaign_type": ctype, "marketplace": mp, "currency": view["currency"],
            # i periodi veri sono dichiarati per ogni CSV (snapshot): questi sono solo i valori di default della campagna
            "target_report_scope": "LIFETIME", "target_date_from": None, "target_date_to": None,
            "search_term_report_scope": "UNKNOWN", "search_term_date_from": None, "search_term_date_to": None,
            "attribution_window": None}
    existing = registry.find_campaign_dir(ws.input_dir, book_id, cid)
    if overwrite and existing is not None:
        old, _ = registry.load_metadata(existing)
        if old:
            meta = {**old, "campaign_name": meta["campaign_name"], "campaign_type": ctype, "marketplace": mp, "currency": view["currency"]}
    try:
        path = registry.create_campaign(ws.input_dir, ws.books_path, meta, overwrite=overwrite)
    except registry.DuplicateError:
        return OpResult(False, [f"La campagna {cid} esiste già in questo libro (gli ID campagna sono unici fra tutti i marketplace)"],
                        data={"duplicate": True})
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Campagna aggiunta correttamente", data=str(path))
