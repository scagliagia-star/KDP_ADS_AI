"""Importa i CSV Amazon Ads da INPUT\\<BOOK_ID>\\<CAMPAIGN_ID>\\ in SQLite. READ-ONLY: nessuna chiamata Amazon."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import load_settings, load_mapping
from kdp_ads.db import connect
from kdp_ads.importer import import_all
from kdp_ads.logs import setup_logger


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", help="importa solo questo book_id")
    ap.add_argument("--campaign", help="importa solo questa campaign_id")
    ap.add_argument("--marketplace", help="importa solo le campagne di questo marketplace")
    ap.add_argument("--dry-run", action="store_true", help="valida senza scrivere righe nel DB")
    ap.add_argument("--verbose", action="store_true", help="mostra il mapping colonne di ogni file")
    a = ap.parse_args()

    settings, mapping = load_settings(), load_mapping()
    if not a.marketplace:                              # mai scegliere un marketplace in silenzio
        from kdp_ads import registry
        amb = registry.multi_marketplace_books(registry.load_books(settings["books_path"]), a.book)
        if amb:
            for bid, mps in amb.items():
                print(f"ERRORE: il libro {bid} ha piu' marketplace ({', '.join(mps)}): specifica --marketplace.", file=sys.stderr)
            return 2
    logger, log_path = setup_logger(settings["logs_dir"], "import")
    logger.info("Import avviato (read-only, dry_run=%s, book=%s, campaign=%s)", a.dry_run, a.book, a.campaign)
    conn = connect(settings["db_path"])
    notes: list = []
    results = import_all(conn, settings, mapping, logger, only_book=a.book, only_campaign=a.campaign, only_marketplace=a.marketplace,
                         dry_run=a.dry_run, notes=notes)

    print("\n=== RIEPILOGO IMPORT ===")
    if not results:
        print("Nessun CSV trovato in INPUT\\<BOOK_ID>\\<CAMPAIGN_ID>\\.")
    for w in notes:
        print(f"ATTENZIONE (libro): {w}")
    for r in results:
        print(f"[{r.status}] {r.book_id}/{r.campaign_id} ({r.campaign_type}) | {r.filename} "
              f"type={r.report_type} scope={r.report_scope} rows={r.rows} dates={r.date_from}..{r.date_to}")
        for p in r.problems:
            print(f"    PROBLEMA: {p}")
        for w in r.warnings:
            print(f"    ATTENZIONE: {w}")
        if r.unmapped:
            print(f"    Colonne NON interpretate (WARNING, in raw_json): {r.unmapped}")
        if a.verbose and r.mapped:
            print("    Mapping: " + "; ".join(f"{k}<-{v!r}" for k, v in r.mapped.items()))
    print(f"Log: {log_path}")
    conn.close()
    return 1 if any(r.status == "REJECTED" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
