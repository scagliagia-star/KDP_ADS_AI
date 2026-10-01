"""Pipeline analisi -> decisioni riusabile da CLI (analyze.py, decide.py) e dalla dashboard V2.
E' esattamente la sequenza che prima stava dentro gli script: nessuna logica nuova. Solo dati locali: nessuna rete, nessuna Amazon."""
from datetime import datetime
from pathlib import Path

from .analysis import analyze_book
from .db import connect
from .decision import build_decisions
from .decisions_report import save_decisions_to_db, write_combined_markdown, write_decisions_csv
from .report import save_to_db, write_markdown, write_overlaps_csv, write_recommendations_csv


def run_analysis(settings, mapping, rules_path, book_id, out_root, logger, save_db=True, conn=None, marketplace=None) -> dict:
    """Come `analyze.py --book <BOOK_ID>`. Ritorna run_id, percorsi dei file, righe, overlap, warning, timestamp."""
    own = conn is None
    conn = conn or connect(settings["db_path"])
    try:
        an = analyze_book(conn, settings, mapping, rules_path, book_id, logger, marketplace)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(out_root)
        csv_p = write_recommendations_csv(an, out / "recommendations", ts)
        ov_p = write_overlaps_csv(an, out / "recommendations", ts)
        md_p = write_markdown(an, out / "daily_reports", ts)
        run_id = save_to_db(conn, an, csv_p, md_p, ov_p, an.ctx.rules) if save_db else None
        logger.info("Output: %s | %s | %s (run_id=%s)", csv_p, ov_p, md_p, run_id)
        for w in an.warnings:
            logger.warning(w)
        return {"run_id": run_id, "marketplace": an.ctx.marketplace, "csv": csv_p, "overlaps_csv": ov_p, "md": md_p, "rows": len(an.recs),
                "overlaps": len(an.overlaps), "warnings": list(an.warnings), "generated_at": an.generated_at}
    finally:
        if own:
            conn.close()


def run_decisions(settings, rules_path, book_id, out_root, logger, analysis_run_id=None, save_db=True, conn=None, marketplace=None) -> dict:
    """Come `decide.py --book <BOOK_ID> [--analysis-run <RUN_ID>]`."""
    own = conn is None
    conn = conn or connect(settings["db_path"])
    try:
        ds = build_decisions(conn, settings, rules_path, book_id, analysis_run_id, logger, marketplace)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        row = conn.execute("SELECT md_path FROM analysis_runs WHERE run_id=?", (ds.analysis_run_id,)).fetchone()
        out = Path(out_root)
        csv_p = write_decisions_csv(ds, out / "decisions", ts)
        md_p = write_combined_markdown(ds, out / "daily_reports", ts, row["md_path"] if row else None)
        run_id = save_decisions_to_db(conn, ds, csv_p, md_p) if save_db else None
        for w in ds.all_warnings():
            logger.warning(w)
        logger.info("Output: %s | %s (decision run %s)", csv_p, md_p, run_id)
        return {"run_id": run_id, "marketplace": ds.ctx.marketplace, "analysis_run_id": ds.analysis_run_id, "csv": csv_p, "md": md_p,
                "decisions": len(ds.decisions),
                "actionable": sum(1 for d in ds.decisions if d.get("operationally_actionable")),
                "paused": sum(1 for d in ds.decisions if d.get("target_status") == "PAUSED"),
                "analysis_warnings": len(ds.analysis_warnings), "own_warnings": len(ds.warnings),
                "analytic_recommendations": ds.accounting["analytic_recommendations"], "generated_at": ds.generated_at}
    finally:
        if own:
            conn.close()
