"""Registra un nuovo libro in CONFIG\\books.json e crea INPUT\\<BOOK_ID>\\ (interattivo). Non sovrascrive libri esistenti."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import load_settings
from kdp_ads import registry


def ask(label, default=None, cast=str):
    while True:
        raw = input(f"{label}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if not raw and default is not None:
            return default
        if raw:
            try:
                return cast(raw)
            except ValueError:
                print("  valore non valido, riprova")


def main() -> int:
    settings = load_settings()
    books = registry.load_books(settings["books_path"])
    print("=== Nuovo libro ===")
    book_id = ask("book_id / ASIN")
    if book_id in books:
        print(f"ERRORE: book_id {book_id} esiste gia' in books.json (non sovrascrivo).")
        return 1
    book = {
        "book_id": book_id,
        "title": ask("Titolo"),
        "marketplace": ask("Marketplace", "US").upper(),
        "currency": ask("Valuta", "USD").upper(),
        "price": ask("Prezzo", cast=float),
        "royalty_per_copy": ask("Royalty per copia", cast=float),
        "target_acos": ask("Target ACOS (%)", cast=float),
        "phase": ask(f"Fase {registry.PHASES}", "PROFIT_STABLE").upper(),
        "active": True,
    }
    errs = registry.validate_book(book)
    if errs:
        print("ERRORE di validazione:\n  - " + "\n  - ".join(errs))
        return 1
    try:
        saved = registry.add_book(settings["books_path"], settings["input_dir"], book)
    except registry.RegistryError as e:
        print(f"ERRORE: {e}")
        return 1
    print("\n=== Libro registrato ===")
    for k, v in saved.items():
        print(f"  {k}: {v}")
    print(f"  break-even ACOS calcolato (royalty/prezzo): {registry.break_even_calc(saved['price'], saved['royalty_per_copy'])}%")
    print(f"  cartella input: {Path(settings['input_dir']) / book_id}")
    print("Prossimo passo: python add_campaign.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
