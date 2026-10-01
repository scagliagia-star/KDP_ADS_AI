"""Dichiara lo SNAPSHOT (tipo di report + intervallo) di un CSV gia' copiato nella cartella campagna, aggiungendolo a
metadata.json -> snapshots. Non sposta ne' modifica i CSV; non importa nulla (poi: python import_csv.py --dry-run).
La logica e' in kdp_ads.registry.add_snapshot (usata anche dalla dashboard)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads import registry
from kdp_ads.config import load_settings
from kdp_ads.snapshots import period_id, window_label


def ask(label, default=None):
    raw = input(f"{label}" + (f" [{default}]" if default not in (None, "") else "") + ": ").strip()
    return raw if raw else (default if default is not None else "")


def main() -> int:
    settings = load_settings()
    book_id, cid = ask("book_id"), ask("campaign_id")
    folder = Path(settings["input_dir"]) / book_id / cid
    meta, errs = registry.load_metadata(folder)
    if meta is None or errs:
        print("ERRORE metadata:", "; ".join(errs))
        return 1
    csvs = sorted(p.name for p in folder.glob("*.csv"))
    print("CSV nella cartella:", ", ".join(csvs) or "(nessuno)")
    fname = ask("nome file CSV")
    if fname not in csvs:
        print(f"ERRORE: {fname} non presente in {folder}")
        return 1
    rt = ask("report_type (TARGETING / SEARCH_TERM; vuoto = rilevato dal file)").upper() or None
    scope = ask(f"report scope {registry.SCOPES}", "DATE_RANGE").upper()
    d_from = ask("date_from (YYYY-MM-DD; vuoto per LIFETIME)") or None
    d_to = ask("date_to (YYYY-MM-DD)") or None
    entry = {"file": fname, "report_type": rt, "report_scope": scope, "date_from": d_from, "date_to": d_to}
    win = ask("attribution_window (vuoto se non nota)") or None
    if win:
        entry["attribution_window"] = win
    try:
        try:
            registry.add_snapshot(folder, entry)
        except registry.DuplicateError:
            if input("Snapshot gia' dichiarato per questo file. Digita SOVRASCRIVI per sostituirlo: ").strip() != "SOVRASCRIVI":
                print("Annullato.")
                return 1
            registry.add_snapshot(folder, entry, overwrite=True)
    except registry.RegistryError as e:
        print(f"ERRORE di validazione:\n  - {e}")
        return 1
    print(f"Snapshot dichiarato: {fname} -> {period_id(scope, d_from, d_to)} ({window_label(scope, d_from, d_to)})")
    print("Prossimo passo: python import_csv.py --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
