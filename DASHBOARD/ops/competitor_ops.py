"""Ricerca competitor (Helium 10 Cerebro): servizi della dashboard. Scrivono SOLO le tabelle competitor_* del DB locale (con backup prima di ogni import).
Non toccano books.json, INPUT, ne' le tabelle dei report Amazon Ads. Nessuna rete, nessuna Amazon. Le letture usano una connessione di sola lettura."""
import sqlite3

from kdp_ads import cerebro, registry
from kdp_ads import competitor as C
from kdp_ads import competitor_store as S
from kdp_ads.db import connect

from . import OpResult
from .backup import backup_db


# ------------------------------------------------------------------ letture
def read_conn(ws):
    """Connessione in SOLA LETTURA (None se il DB non esiste). Tollera DB senza tabelle competitor_*."""
    if not ws.db_path.is_file():
        return None
    conn = sqlite3.connect(f"{ws.db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def book_choices(ws) -> list:
    """Libri di books.json (sola lettura): [{book_id, title, marketplaces:[codici]}]."""
    out = []
    for bid, b in registry.load_books(ws.books_path).items():
        try:
            mps = list(registry.marketplaces_of(b))
        except registry.RegistryError:
            mps = []
        out.append({"book_id": bid, "title": b.get("title") or bid, "marketplaces": mps})
    return sorted(out, key=lambda b: (str(b["title"]).lower(), b["book_id"]))


def list_projects(ws) -> list:
    conn = read_conn(ws)
    if conn is None:
        return []
    try:
        return S.list_projects(conn) if S.has_schema(conn) else []
    finally:
        conn.close()


def own_context(ws, book_id, marketplace):
    """Contesto dei dati reali del libro (target, Search Term) + negative candidate dal motore Amazon Ads. Sola lettura; se qualcosa manca: vuoto."""
    conn = read_conn(ws)
    if conn is None:
        return C.build_own_context()
    try:
        negatives = negative_candidates(ws, book_id, marketplace)
        return S.own_context(conn, book_id, marketplace, negatives)
    finally:
        conn.close()


def negative_candidates(ws, book_id, marketplace) -> list:
    """Le negative effettive di Amazon non sono importate: l'unica fonte e' il motore Opportunita' Search Term (NEGATIVE_CANDIDATE). Errori => nessuna."""
    try:
        from data_access.repository import Repo
        repo = Repo(ws.db_path, root=ws.root)
        book = repo.book(book_id)
        mp = next((m for m in repo.marketplaces(book_id) if str(m["marketplace"]).upper() == str(marketplace).upper()), None)
        if not book or not mp:
            return []
        return S.negatives_from_opportunities(repo.search_term_opportunities({**book, **mp}, mp["marketplace"])["records"])
    except Exception:                                                # nessun dato/economia incompleta: il gap resta valido, senza negative
        return []


def analyze(ws, research_id) -> dict | None:
    conn = read_conn(ws)
    if conn is None or not S.has_schema(conn):
        return None
    try:
        proj = S.get_project(conn, research_id)
        if not proj:
            return None
        own = own_context(ws, proj["book_id"], proj["marketplace"]) if proj["book_id"] else None
        try:
            from data_access.repository import Repo
            rules = Repo(ws.db_path, root=ws.root).rules()
        except Exception:
            rules = {}
        a = S.analyze(conn, research_id, rules, own)
        a["saved"] = {o["cluster_key"]: o for o in S.list_opportunities(conn, research_id)}
        a["raw"] = S.raw_observations(conn, research_id)
        return a
    finally:
        conn.close()


# ------------------------------------------------------------------ scritture
def plan_files(files: list, manual_asins: dict | None = None) -> list:
    """files = [(nome, bytes)]. Per ognuno: {name, parsed|None, error|None, asin, asin_source, needs_asin}. Nessuna scrittura."""
    out = []
    for name, data in files:
        manual = (manual_asins or {}).get(name)
        try:
            p = cerebro.parse_cerebro(name, data, asin=manual or None)
            out.append({"name": name, "parsed": p, "error": None, "asin": p["asin"], "asin_source": p["asin_source"], "needs_asin": p["asin"] is None})
        except cerebro.CerebroError as e:
            out.append({"name": name, "parsed": None, "error": str(e), "asin": None, "asin_source": None, "needs_asin": False})
    return out


def _refresh_opportunities(conn, research_id, rules=None):
    """Ricalcola e salva i cluster editoriali del progetto (stato DA_APPROFONDIRE; le scelte dell'utente non vengono sovrascritte)."""
    a = S.analyze(conn, research_id, rules)
    return S.sync_opportunities(conn, research_id, a["editorial"]["opportunities"])


def import_research(ws, book_id, marketplace, name, files: list, manual_asins=None, replace=False, research_id=None, book_title=None) -> OpResult:
    """Crea un progetto (o aggiunge file a `research_id`) e importa 1-5 file Cerebro. Un file non valido NON blocca gli altri; ogni file e' una transazione.
    Nessun overwrite silenzioso: stesso file = nessuna modifica; stesso ASIN = richiede replace=True."""
    plans = plan_files(files, manual_asins)
    ok = [p for p in plans if p["parsed"] is not None]
    errors = [f"{p['name']}: {p['error']}" for p in plans if p["error"]]
    if not ok:
        return OpResult(False, errors or ["Nessun file da importare"])
    if research_id is None and len(ok) > S.MAX_COMPETITORS:
        return OpResult(False, [f"Un progetto può avere al massimo {S.MAX_COMPETITORS} competitor"])
    backup_db(ws, "competitor_research")
    conn = connect(ws.db_path)
    try:
        rid = research_id or S.create_project(conn, book_id, name, marketplace, book_title=book_title)
        results = []
        for p in ok:
            try:
                r = S.import_source(conn, rid, p["parsed"], replace=replace)             # l'ASIN (nome file o manuale) e' gia' nel parsed
                results.append({"file": p["name"], **r})
            except S.StoreError as e:
                errors.append(f"{p['name']}: {e}")
        try:
            from data_access.repository import Repo
            rules = Repo(ws.db_path, root=ws.root).rules()
        except Exception:
            rules = {}
        done = [r for r in results if r["status"] in ("IMPORTED", "REPLACED")]
        if research_id is None and not done:                       # progetto appena creato e rimasto senza file: non lasciare un guscio vuoto
            S.delete_empty_project(conn, rid)
            return OpResult(False, errors or [r["message"] for r in results] or ["Nessun file importato"], data={"research_id": None, "imported": 0,
                                                                                                                "warnings": [r["message"] for r in results]})
        if S.list_sources(conn, rid, active_only=True):
            _refresh_opportunities(conn, rid, rules)
        warn = [r["message"] for r in results if r["status"] in ("DUPLICATE_FILE", "ASIN_EXISTS")]
        return OpResult(bool(done) or (bool(results) and not errors), errors, f"{len(done)} file importati", {"research_id": rid, "results": results, "warnings": warn, "imported": len(done)})
    finally:
        conn.close()


def update_project(ws, research_id, name=None, status=None, notes=None) -> OpResult:
    conn = connect(ws.db_path)
    try:
        S.update_project(conn, research_id, name, status, notes)
        return OpResult(True, message="Progetto aggiornato")
    except S.StoreError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def recompute(ws, research_id) -> OpResult:
    conn = connect(ws.db_path)
    try:
        return OpResult(True, message="Opportunità ricalcolate", data=_refresh_opportunities(conn, research_id))
    except S.StoreError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def save_as_project(ws, opportunity_id, title=None, notes=None) -> OpResult:
    conn = connect(ws.db_path)
    try:
        return OpResult(True, message="Salvata come progetto editoriale (stato REVIEW)", data=S.save_as_editorial_project(conn, int(opportunity_id), title, notes))
    except S.StoreError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def set_opportunity_status(ws, opportunity_id, status) -> OpResult:
    conn = connect(ws.db_path)
    try:
        S.set_opportunity_status(conn, int(opportunity_id), status)
        return OpResult(True, message="Stato aggiornato")
    except S.StoreError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()


def set_brand(ws, research_id, keyword_norms, value) -> OpResult:
    conn = connect(ws.db_path)
    try:
        for k in keyword_norms:
            S.set_brand_override(conn, research_id, k, value)
        _refresh_opportunities(conn, research_id)
        return OpResult(True, message="Flag BRAND/IP aggiornato")
    except S.StoreError as e:
        return OpResult(False, [str(e)])
    finally:
        conn.close()
