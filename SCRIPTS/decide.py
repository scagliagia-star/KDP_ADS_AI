"""Decision Layer: consolida le raccomandazioni analitiche in decisioni operative PROPOSED (approvazione manuale).
READ-ONLY: nessuna chiamata Amazon, nessuna esecuzione, nessuna modifica delle recommendations analitiche."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import CONFIG_DIR, ROOT, load_settings
from kdp_ads.logs import setup_logger
from kdp_ads.registry import RegistryError
from kdp_ads.pipeline import run_decisions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--analysis-run", type=int, help="run_id analitico (default: ultimo del libro e marketplace)")
    ap.add_argument("--marketplace", help="marketplace (obbligatorio se il libro ne ha piu' di uno e manca --analysis-run)")
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()

    settings = load_settings()
    logger, log_path = setup_logger(settings["logs_dir"], f"decision_{a.book}")
    logger.info("Decision layer avviato (read-only) book=%s", a.book)
    try:
        r = run_decisions(settings, CONFIG_DIR / "rules.json", a.book, ROOT / "OUTPUT", logger, a.analysis_run, save_db=not a.no_db,
                          marketplace=a.marketplace)
    except RegistryError as e:                         # es. libro con piu' marketplace senza --marketplace
        print(f"ERRORE: {e}", file=sys.stderr)
        return 2
    print(f"CSV decisioni: {r['csv']}\nMarkdown:      {r['md']}\nLog:           {log_path}\ndecision run:  {r['run_id']}")
    print(f"Marketplace: {r['marketplace']}")
    print(f"Recs analitiche: {r['analytic_recommendations']} -> decisioni: {r['decisions']} | warning: {r['analysis_warnings']} ereditati + {r['own_warnings']} propri")
    return 0


if __name__ == "__main__":
    sys.exit(main())
