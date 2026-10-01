"""Rule engine V1: analizza i dati importati e produce CSV + report Markdown di RACCOMANDAZIONI.
READ-ONLY: nessuna chiamata Amazon, nessuna modifica di campagne/bid/budget."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import CONFIG_DIR, ROOT, load_mapping, load_settings
from kdp_ads.logs import setup_logger
from kdp_ads.registry import RegistryError
from kdp_ads.pipeline import run_analysis


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, help="book_id da analizzare")
    ap.add_argument("--marketplace", help="marketplace da analizzare (obbligatorio se il libro ne ha piu' di uno)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--no-db", action="store_true", help="non salvare le raccomandazioni nel DB")
    a = ap.parse_args()

    settings, mapping = load_settings(), load_mapping()
    logger, log_path = setup_logger(settings["logs_dir"], f"analysis_{a.book}")
    logger.info("Analisi avviata (read-only) book=%s", a.book)
    try:
        r = run_analysis(settings, mapping, CONFIG_DIR / "rules.json", a.book, ROOT / "OUTPUT", logger, save_db=not a.no_db,
                         marketplace=a.marketplace)
    except RegistryError as e:                         # es. libro con piu' marketplace senza --marketplace
        print(f"ERRORE: {e}", file=sys.stderr)
        return 2
    print(f"CSV:        {r['csv']}\nOverlaps:   {r['overlaps_csv']}\nMarkdown:   {r['md']}\nLog:        {log_path}\nrun_id:     {r['run_id']}")
    print(f"Marketplace: {r['marketplace']}")
    print(f"Righe: {r['rows']} | overlap: {r['overlaps']} | data warnings: {len(r['warnings'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
