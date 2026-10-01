"""Contesto di una vista: libro + decision run (+ analysis run collegato), decisioni, warning, riepilogo. Nessuna dipendenza da Streamlit."""
from dataclasses import dataclass, field

import ui_labels
from .repository import Repo, RepoError, summarize


@dataclass
class Context:
    repo: Repo
    error: str | None = None
    books: list = field(default_factory=list)
    book: dict | None = None             # riga del libro con l'economia del marketplace selezionato
    marketplaces: list = field(default_factory=list)
    marketplace: str | None = None
    runs: list = field(default_factory=list)
    run: dict | None = None
    analysis: dict | None = None
    latest_analysis: dict | None = None
    decisions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.run is not None


def run_label(r, latest=False) -> str:
    """Etichetta italiana del run (solo presentazione): 'Run decisionale #6 · Run di analisi #3 · data/ora · PIÙ RECENTE'."""
    return ui_labels.label_run(r, latest)


_ECON = ("currency", "price", "royalty_per_copy", "break_even_acos", "target_acos", "phase")


def load_context(repo: Repo, book_id=None, decision_run_id=None, marketplace=None) -> Context:
    """book_id None = primo libro; marketplace None = primo marketplace con run decisionali; decision_run_id None = ultimo run
    del libro e marketplace. Tutti i dati e i KPI si riferiscono a UN solo marketplace."""
    ctx = Context(repo=repo)
    try:
        ctx.books = repo.list_books()
        if not ctx.books:
            ctx.error = "Nessun libro nel database."
            return ctx
        ctx.book = next((b for b in ctx.books if b["book_id"] == book_id), ctx.books[0])
        bid = ctx.book["book_id"]
        ctx.marketplaces = repo.marketplaces(bid)
        codes = [m["marketplace"] for m in ctx.marketplaces]
        mp = str(marketplace).upper() if marketplace else None
        if mp not in codes:
            mp = next((c for c in codes if repo.decision_runs(bid, c)), codes[0] if codes else None)
        ctx.marketplace = mp
        mrow = next((m for m in ctx.marketplaces if m["marketplace"] == mp), None)
        if mrow:                                          # economia del marketplace selezionato (mai quella di un altro)
            ctx.book = {**ctx.book, "marketplace": mp, **{k: mrow.get(k) for k in _ECON if k in mrow}}
        ctx.runs = repo.decision_runs(bid, mp)
        ctx.latest_analysis = repo.latest_analysis_run(bid, mp)
        if not ctx.runs:
            ctx.error = "Nessun decision run per questo libro" + (f" nel marketplace {mp}." if mp else ".")
            return ctx
        ctx.run = next((r for r in ctx.runs if r["run_id"] == decision_run_id), ctx.runs[0])
        ctx.analysis = repo.analysis_run_for_decision_run(ctx.run)
        if ctx.analysis is None:
            ctx.error = None        # il run e' visibile anche senza analysis run (segnalato dalla UI)
        ctx.decisions = repo.decisions(ctx.run["run_id"])
        ctx.warnings = repo.warnings(ctx.run, ctx.analysis)
        ctx.summary = summarize(ctx.decisions, ctx.warnings)
    except RepoError as e:
        ctx.error = str(e)
    return ctx
