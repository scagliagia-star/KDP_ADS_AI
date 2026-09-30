"""Decision Layer: consolida le raccomandazioni analitiche in decisioni operative PROPOSED (approvazione manuale).
READ-ONLY: nessuna chiamata Amazon, nessuna esecuzione, nessuna modifica delle recommendations analitiche."""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import CONFIG_DIR, ROOT, load_settings
from kdp_ads.db import connect
from kdp_ads.decision import build_decisions
from kdp_ads.decisions_report import save_decisions_to_db, write_combined_markdown, write_decisions_csv
from kdp_ads.logs import setup_logger


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--analysis-run", type=int, help="run_id analitico (default: ultimo del libro)")
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()

    settings = load_settings()
    logger, log_path = setup_logger(settings["logs_dir"], f"decision_{a.book}")
    logger.info("Decision layer avviato (read-only) book=%s", a.book)
    conn = connect(settings["db_path"])
    ds = build_decisions(conn, settings, CONFIG_DIR / "rules.json", a.book, a.analysis_run, logger)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    row = conn.execute("SELECT md_path FROM analysis_runs WHERE run_id=?", (ds.analysis_run_id,)).fetchone()
    csv_p = write_decisions_csv(ds, ROOT / "OUTPUT" / "decisions", ts)
    md_p = write_combined_markdown(ds, ROOT / "OUTPUT" / "daily_reports", ts, row["md_path"] if row else None)
    run_id = None if a.no_db else save_decisions_to_db(conn, ds, csv_p, md_p)
    for w in ds.all_warnings():
        logger.warning(w)
    logger.info("Output: %s | %s (decision run %s)", csv_p, md_p, run_id)
    print(f"CSV decisioni: {csv_p}\nMarkdown:      {md_p}\nLog:           {log_path}\ndecision run:  {run_id}")
    print(f"Recs analitiche: {ds.accounting['analytic_recommendations']} -> decisioni: {len(ds.decisions)} | warning: {len(ds.analysis_warnings)} ereditati + {len(ds.warnings)} propri")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
