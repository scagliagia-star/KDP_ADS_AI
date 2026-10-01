"""ANALIZZA LIBRO e GENERA DECISIONI: stessa logica di `analyze.py --book` e `decide.py --book --analysis-run`
(kdp_ads.pipeline). Prima di ogni scrittura nel DB si fa un backup. Solo dati locali: nessuna rete, nessuna Amazon."""
from kdp_ads import pipeline, registry
from kdp_ads.logs import setup_logger

from . import OpResult
from .backup import backup_db
from .dbread import mp_clause, query
from .importing import close_logger


def latest_analysis_run(ws, book_id, marketplace=None):
    sql, args = mp_clause(ws, book_id, marketplace)
    rows = query(ws, f"SELECT run_id, generated_at, row_count FROM analysis_runs WHERE book_id=?{sql} ORDER BY run_id DESC LIMIT 1",
                 (book_id, *args))
    return rows[0] if rows else None


def latest_decision_run(ws, book_id, marketplace=None):
    sql, args = mp_clause(ws, book_id, marketplace)
    rows = query(ws, f"SELECT run_id, analysis_run_id, generated_at, decision_count FROM decision_runs WHERE book_id=?{sql} "
                     "ORDER BY run_id DESC LIMIT 1", (book_id, *args))
    return rows[0] if rows else None


def run_book_analysis(ws, book_id, marketplace=None) -> OpResult:
    """Equivale a: python analyze.py --book <BOOK_ID> --marketplace <MP>. Analizza UN solo marketplace (mai piu' marketplace insieme).
    Ritorna analysis_run_id, righe, overlap, avvisi sui dati, timestamp."""
    bk = backup_db(ws, "pre_analisi")
    logger, _ = setup_logger(ws.logs_dir, f"analysis_{book_id}")
    logger.info("Analisi avviata dalla dashboard (solo dati locali) book=%s", book_id)
    try:
        r = pipeline.run_analysis(ws.settings, ws.mapping, ws.rules_path, book_id, ws.out_dir, logger, marketplace=marketplace)
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    finally:
        close_logger(logger)
    r["backup"] = str(bk) if bk else None
    return OpResult(True, message="Analisi completata", data=r)


def run_book_decisions(ws, book_id, analysis_run_id=None, marketplace=None) -> OpResult:
    """Equivale a: python decide.py --book <BOOK_ID> --marketplace <MP> --analysis-run <RUN_ID>."""
    bk = backup_db(ws, "pre_decisioni")
    logger, _ = setup_logger(ws.logs_dir, f"decision_{book_id}")
    logger.info("Decision layer avviato dalla dashboard (solo dati locali) book=%s", book_id)
    try:
        r = pipeline.run_decisions(ws.settings, ws.rules_path, book_id, ws.out_dir, logger, analysis_run_id, marketplace=marketplace)
    except registry.RegistryError as e:
        return OpResult(False, [str(e)])
    finally:
        close_logger(logger)
    r["backup"] = str(bk) if bk else None
    return OpResult(True, message="Decisioni generate", data=r)
