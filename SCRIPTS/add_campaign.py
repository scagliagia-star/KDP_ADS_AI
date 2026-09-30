"""Crea INPUT\\<BOOK_ID>\\<CAMPAIGN_ID>\\metadata.json (interattivo). Non sovrascrive senza conferma esplicita."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import load_settings
from kdp_ads import registry


def ask(label, default=None):
    raw = input(f"{label}" + (f" [{default}]" if default not in (None, "") else "") + ": ").strip()
    return raw if raw else (default if default is not None else "")


def ask_scope(prefix, default):
    scope = ask(f"{prefix} report scope {registry.SCOPES}", default).upper()
    d_from = d_to = None
    if scope == "DATE_RANGE":
        d_from = ask(f"{prefix} date_from (YYYY-MM-DD)") or None
        d_to = ask(f"{prefix} date_to (YYYY-MM-DD)") or None
    elif scope == "LIFETIME":
        d_to = ask(f"{prefix} date_to (YYYY-MM-DD, data export; vuoto = nessuna)") or None
    return scope, d_from, d_to


def main() -> int:
    settings = load_settings()
    books = registry.load_books(settings["books_path"])
    print("=== Nuova campagna ===")
    print("Libri registrati: " + ", ".join(f"{b} ({v['title']})" for b, v in books.items()))
    book_id = ask("book_id")
    book = books.get(book_id)
    if not book:
        print(f"ERRORE: book_id {book_id} non presente in books.json (usa prima add_book.py).")
        return 1
    campaign_id = ask("campaign_id (nome cartella, es. CHAIR_TAI_CHI_EXACT)")
    meta = {
        "book_id": book_id,
        "campaign_id": campaign_id,
        "campaign_name": ask("campaign_name", campaign_id),
        "campaign_type": ask("campaign_type (es. EXACT, PHRASE, COMPETITOR, AUTO, ...)").upper(),
        "marketplace": ask("marketplace", book["marketplace"]),
        "currency": ask("currency", book["currency"]),
    }
    for prefix, dflt in (("target", "LIFETIME"), ("search_term", "DATE_RANGE")):
        scope, d_from, d_to = ask_scope(prefix, dflt)
        meta[f"{prefix}_report_scope"] = scope
        meta[f"{prefix}_date_from"] = d_from
        meta[f"{prefix}_date_to"] = d_to
    meta["attribution_window"] = ask("attribution_window (es. '14 Day'; vuoto se non nota)") or None

    errs = registry.validate_metadata(meta) + registry.check_campaign_against_book(meta, book, book_id, campaign_id)
    if errs:
        print("ERRORE di validazione:\n  - " + "\n  - ".join(errs))
        return 1
    try:
        folder = registry.create_campaign(settings["input_dir"], settings["books_path"], meta)
    except registry.DuplicateError as e:
        print(f"ATTENZIONE: {e}")
        if input("Digita SOVRASCRIVI per sostituire il metadata.json esistente: ").strip() != "SOVRASCRIVI":
            print("Annullato, nessuna modifica.")
            return 1
        folder = registry.create_campaign(settings["input_dir"], settings["books_path"], meta, overwrite=True)
    except registry.RegistryError as e:
        print(f"ERRORE: {e}")
        return 1
    print(f"\nCampagna creata: {folder}\\metadata.json")
    print("Copia qui i CSV Amazon Ads, poi: python import_csv.py --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
