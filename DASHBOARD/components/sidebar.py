"""Selezione libro e decision run (sidebar). Di default: ultimo decision run del libro."""
import bootstrap
import ui_labels as U
from data_access.context import load_context, run_label
from data_access.repository import Repo, RepoError


def select_context(st):
    repo = Repo(bootstrap.db_path(), root=bootstrap.ROOT)
    st.sidebar.markdown(f"### {U.SIDEBAR_SELECTION}")
    try:
        books = repo.list_books()
    except RepoError as e:
        return load_context(repo)          # ctx.error popolato
    book_id = None
    if books:
        labels = {f"{b.get('title') or '(senza titolo)'} · {b['book_id']}": b["book_id"] for b in books}
        choice = st.sidebar.selectbox(U.SIDEBAR_BOOK, list(labels), key="kb_book")
        book_id = labels[choice]
    mp = None
    try:
        mps = repo.marketplaces(book_id) if book_id else []
    except RepoError:
        mps = []
    if mps:
        mlabels = {f"{m['marketplace']} · {m.get('currency') or U.ND}": m["marketplace"] for m in mps}
        mp = mlabels[st.sidebar.selectbox(U.SIDEBAR_MARKETPLACE, list(mlabels), key=f"kb_mp_{book_id}")]
    run_id = None
    runs = []
    try:
        runs = repo.decision_runs(book_id, mp) if book_id else []
    except RepoError:
        pass
    if runs:
        labels = {run_label(r, i == 0): r["run_id"] for i, r in enumerate(runs)}
        run_id = labels[st.sidebar.selectbox(U.SIDEBAR_RUN, list(labels), key=f"kb_run_{book_id}_{mp}")]
    st.sidebar.caption(U.SIDEBAR_READ_ONLY)
    return load_context(repo, book_id, run_id, mp)
