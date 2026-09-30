"""Rule engine V1: analizza i dati importati e produce CSV + report Markdown di RACCOMANDAZIONI.
READ-ONLY: nessuna chiamata Amazon, nessuna modifica di campagne/bid/budget."""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.analysis import analyze_book
from kdp_ads.config import CONFIG_DIR, ROOT, load_mapping, load_settings
from kdp_ads.db import connect
from kdp_ads.logs import setup_logger
from kdp_ads.report import (NON_ACTIONS, save_to_db, write_markdown, write_overlaps_csv, write_recommendations_csv)
from kdp_ads.rules import load_rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, help="book_id da analizzare")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--no-db", action="store_true", help="non salvare le raccomandazioni nel DB")
    a = ap.parse_args()

    settings, mapping = load_settings(), load_mapping()
    logger, log_path = setup_logger(settings["logs_dir"], f"analysis_{a.book}")
    logger.info("Analisi avviata (read-only) book=%s", a.book)
    conn = connect(settings["db_path"])
    rules_path = CONFIG_DIR / "rules.json"
    an = analyze_book(conn, settings, mapping, rules_path, a.book, logger)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "OUTPUT"
    csv_p = write_recommendations_csv(an, out / "recommendations", ts)
    ov_p = write_overlaps_csv(an, out / "recommendations", ts)
    md_p = write_markdown(an, out / "daily_reports", ts)
    run_id = None if a.no_db else save_to_db(conn, an, csv_p, md_p, ov_p, an.ctx.rules)
    logger.info("Output: %s | %s | %s (run_id=%s)", csv_p, ov_p, md_p, run_id)
    for w in an.warnings:
        logger.warning(w)
    print(f"CSV:        {csv_p}\nOverlaps:   {ov_p}\nMarkdown:   {md_p}\nLog:        {log_path}\nrun_id:     {run_id}")
    print(f"Righe: {len(an.recs)} | overlap: {len(an.overlaps)} | data warnings: {len(an.warnings)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
