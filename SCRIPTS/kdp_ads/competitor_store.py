"""RICERCA COMPETITOR: persistenza locale (SQLite) dei progetti di ricerca Helium 10 Cerebro. Tabelle NUOVE e ADDITIVE (CREATE TABLE IF NOT EXISTS): nessuna
tabella esistente viene toccata, quindi compatibile col DB attuale (SCHEMA_VERSION 6 = v5 + tabelle/colonne additive, vedi db._upgrade). Nessuna rete. Non scrive mai books.json, INPUT o le tabelle
dei report Amazon Ads (target_rows, search_term_rows, decisions, ...): le legge soltanto, per il confronto (gap).

Tabelle:
  competitor_research_projects       progetto di ricerca (mode EXISTING_BOOK | NEW_OPPORTUNITY, book_id nullable -> books, marketplace, status)
  competitor_research_sources        un file Cerebro importato (= un competitor): ASIN, file, sha256, data import, stato ACTIVE | SUPERSEDED
  competitor_research_keywords       keyword normalizzate del progetto (dedup per progetto) + flag manuale brand/IP
  competitor_keyword_observations    relazione keyword <-> competitor (<-> file) con le metriche normalizzate e il valore originale (raw_json)
  competitor_opportunities           cluster editoriali derivati (stato, punteggio, salvataggio come progetto editoriale)
  competitor_opportunity_keywords    keyword che hanno generato ogni cluster

Regole: import RIPETIBILE (stesso file = nessuna modifica), STORICO conservato (un file sostituito resta SUPERSEDED, mai cancellato), nessun overwrite
silenzioso (un secondo file per lo stesso ASIN richiede `replace=True` esplicito), una transazione per file."""
import json
import re
import sqlite3
from datetime import datetime

from . import cerebro
from . import competitor as C

MODES = ("EXISTING_BOOK", "NEW_OPPORTUNITY")
STATUSES = ("RESEARCH", "REVIEW", "APPROVED", "REJECTED", "CONVERTED_TO_BOOK")
OPP_STATUSES = ("DA_APPROFONDIRE", "REVIEW", "APPROVED", "REJECTED", "CONVERTED_TO_BOOK")
MAX_COMPETITORS = 5
METRIC_COLS = ("search_volume", "search_volume_trend", "keyword_sales", "cerebro_iq", "competing_products", "title_density", "organic_rank", "sponsored_rank",
               "amazon_recommended_rank", "ppc_bid", "ppc_bid_min", "ppc_bid_max", "competitor_rank_avg", "ranking_competitors", "relative_rank", "cpr",
               "sponsored_asins")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS competitor_research_projects (
    research_id TEXT PRIMARY KEY, name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ({",".join(f"'{m}'" for m in MODES)})),
    book_id TEXT REFERENCES books(book_id),
    marketplace TEXT NOT NULL DEFAULT 'US',
    status TEXT NOT NULL DEFAULT 'RESEARCH' CHECK (status IN ({",".join(f"'{s}'" for s in STATUSES)})),
    notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK (mode = 'NEW_OPPORTUNITY' OR book_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_cr_projects_book ON competitor_research_projects(book_id);
CREATE TABLE IF NOT EXISTS competitor_research_sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL REFERENCES competitor_research_projects(research_id),
    asin TEXT NOT NULL, asin_source TEXT, marketplace TEXT,
    file_name TEXT NOT NULL, file_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL,
    row_count INTEGER, status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')),
    columns_json TEXT, warnings_json TEXT, parser_version INTEGER, source TEXT NOT NULL DEFAULT 'HELIUM10_CEREBRO',
    UNIQUE (research_id, file_sha256)
);
CREATE INDEX IF NOT EXISTS ix_cr_sources_research ON competitor_research_sources(research_id, status);
CREATE TABLE IF NOT EXISTS competitor_research_keywords (
    keyword_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL REFERENCES competitor_research_projects(research_id),
    keyword_norm TEXT NOT NULL, keyword_text TEXT NOT NULL, brand_override INTEGER CHECK (brand_override IN (0,1)),
    UNIQUE (research_id, keyword_norm)
);
CREATE TABLE IF NOT EXISTS competitor_keyword_observations (
    obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES competitor_research_sources(source_id),
    keyword_id INTEGER NOT NULL REFERENCES competitor_research_keywords(keyword_id),
    research_id TEXT NOT NULL, asin TEXT NOT NULL, keyword_original TEXT NOT NULL,
    {", ".join(f"{c} REAL" for c in METRIC_COLS)},
    raw_json TEXT,
    UNIQUE (source_id, keyword_id)
);
CREATE INDEX IF NOT EXISTS ix_cr_obs_research ON competitor_keyword_observations(research_id, keyword_id);
CREATE TABLE IF NOT EXISTS competitor_opportunities (
    opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id TEXT NOT NULL REFERENCES competitor_research_projects(research_id),
    cluster_key TEXT NOT NULL, label TEXT NOT NULL,
    origin_book_id TEXT REFERENCES books(book_id),
    status TEXT NOT NULL DEFAULT 'DA_APPROFONDIRE' CHECK (status IN ({",".join(f"'{s}'" for s in OPP_STATUSES)})),
    score REAL, score_label TEXT, competitor_count INTEGER, competitor_total INTEGER, keyword_count INTEGER, volume_sum REAL,
    payload_json TEXT, saved_as_project INTEGER NOT NULL DEFAULT 0, project_title TEXT, notes TEXT, research_source TEXT,
    linked_book_id TEXT REFERENCES books(book_id), linked_asin TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (research_id, cluster_key)
);
CREATE TABLE IF NOT EXISTS competitor_opportunity_keywords (
    opportunity_id INTEGER NOT NULL REFERENCES competitor_opportunities(opportunity_id),
    keyword_id INTEGER NOT NULL REFERENCES competitor_research_keywords(keyword_id),
    PRIMARY KEY (opportunity_id, keyword_id)
);
"""


class StoreError(ValueError):
    """Operazione non valida (messaggio in italiano). Nessuna modifica al DB."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def has_schema(conn) -> bool:
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='competitor_research_projects'").fetchone())
    except sqlite3.Error:
        return False


def _rows(conn, sql, params=()):
    prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.row_factory = prev


# ------------------------------------------------------------------ progetti
def default_name(mode: str, book_title: str | None, today: str) -> str:
    return f"Competitor Research — {book_title} — {today}" if mode == "EXISTING_BOOK" else f"Nuova ricerca {today}"


def new_research_id(conn, today: str) -> str:
    base = "CR-" + today.replace("-", "")
    n = (conn.execute("SELECT COUNT(*) FROM competitor_research_projects WHERE research_id LIKE ?", (base + "-%",)).fetchone()[0] or 0) + 1
    while conn.execute("SELECT 1 FROM competitor_research_projects WHERE research_id=?", (f"{base}-{n:03d}",)).fetchone():
        n += 1
    return f"{base}-{n:03d}"


def create_project(conn, book_id: str | None = None, name: str | None = None, marketplace: str = "US", book_title: str | None = None,
                   notes: str | None = None, now: str | None = None) -> str:
    """Nuovo progetto. book_id None => NEW_OPPORTUNITY, altrimenti EXISTING_BOOK. Se il libro non e' ancora nella tabella `books` del DB (c'e' solo in
    books.json) si inserisce la stessa riga minima che crea l'importer (book_id, titolo): serve alla chiave esterna, nessun dato Ads."""
    ensure_schema(conn)
    now = now or now_iso()
    today = now[:10]
    mode = "EXISTING_BOOK" if book_id else "NEW_OPPORTUNITY"
    mp = str(marketplace or "US").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,3}", mp):
        raise StoreError(f"Marketplace non valido: {marketplace!r}")
    if book_id:
        book_id = str(book_id).strip()
        row = conn.execute("SELECT title FROM books WHERE book_id=?", (book_id,)).fetchone()
        if row is None:
            conn.execute("INSERT OR IGNORE INTO books (book_id, title, synced_at) VALUES (?,?,?)", (book_id, book_title, now))
        elif book_title is None:
            book_title = row[0]
    final = (name or "").strip() or default_name(mode, book_title or book_id, today)
    rid = new_research_id(conn, today)
    with conn:
        conn.execute("INSERT INTO competitor_research_projects (research_id,name,mode,book_id,marketplace,status,notes,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,'RESEARCH',?,?,?)", (rid, final, mode, book_id, mp, notes, now, now))
    return rid


def delete_empty_project(conn, research_id) -> bool:
    """Elimina un progetto appena creato che NON ha nessun file (guscio vuoto). Rifiuta se ha sorgenti, osservazioni od opportunita': nessuna perdita di dati."""
    for t in ("competitor_research_sources", "competitor_keyword_observations", "competitor_opportunities"):
        if conn.execute(f"SELECT 1 FROM {t} WHERE research_id=? LIMIT 1", (research_id,)).fetchone():
            return False
    with conn:
        conn.execute("DELETE FROM competitor_research_keywords WHERE research_id=?", (research_id,))
        return conn.execute("DELETE FROM competitor_research_projects WHERE research_id=?", (research_id,)).rowcount > 0


def get_project(conn, research_id) -> dict | None:
    r = _rows(conn, "SELECT p.*, b.title AS book_title FROM competitor_research_projects p LEFT JOIN books b ON b.book_id=p.book_id WHERE p.research_id=?",
              (research_id,))
    return r[0] if r else None


def list_projects(conn, book_id=None) -> list:
    sql = "SELECT p.*, b.title AS book_title FROM competitor_research_projects p LEFT JOIN books b ON b.book_id=p.book_id"
    params = ()
    if book_id:
        sql, params = sql + " WHERE p.book_id=?", (str(book_id),)
    rows = _rows(conn, sql + " ORDER BY p.created_at DESC, p.research_id DESC", params)
    for r in rows:
        r["competitors"] = (_rows(conn, "SELECT COUNT(*) AS n FROM competitor_research_sources WHERE research_id=? AND status='ACTIVE'", (r["research_id"],)) or [{"n": 0}])[0]["n"]
    return rows


def update_project(conn, research_id, name=None, status=None, notes=None, now=None) -> None:
    if status is not None and status not in STATUSES:
        raise StoreError(f"Stato non valido: {status!r}")
    if name is not None and not name.strip():
        raise StoreError("Il nome del progetto non può essere vuoto")
    if not get_project(conn, research_id):
        raise StoreError("Progetto di ricerca non trovato")
    sets, params = [], []
    for col, v in (("name", name.strip() if name is not None else None), ("status", status), ("notes", notes)):
        if v is not None:
            sets.append(f"{col}=?")
            params.append(v)
    if sets:
        with conn:
            conn.execute(f"UPDATE competitor_research_projects SET {', '.join(sets)}, updated_at=? WHERE research_id=?", (*params, now or now_iso(), research_id))


# ------------------------------------------------------------------ import
def import_source(conn, research_id: str, parsed: dict, asin: str | None = None, replace: bool = False, now: str | None = None) -> dict:
    """Importa UN file Cerebro gia' letto da cerebro.parse_cerebro. Ritorna {status, source_id, asin, rows, message}:
      IMPORTED        file nuovo
      REPLACED        stesso ASIN gia' presente + replace=True: il vecchio file resta nello storico come SUPERSEDED
      DUPLICATE_FILE  stesso contenuto (sha256) gia' importato nel progetto: nessuna modifica (import ripetibile)
      ASIN_EXISTS     stesso ASIN con file diverso e replace=False: NIENTE scritto, serve una scelta esplicita
    StoreError se manca l'ASIN, il progetto non esiste o si superano 5 competitor."""
    ensure_schema(conn)
    proj = get_project(conn, research_id)
    if not proj:
        raise StoreError("Progetto di ricerca non trovato")
    a = cerebro.normalize_asin(asin) if asin else parsed.get("asin")
    if asin and not a:
        raise StoreError(f"ASIN non valido: {asin!r}")
    if not a:
        raise StoreError(f"ASIN competitor mancante per «{parsed['file_name']}»: indicalo a mano (non è nel nome del file)")
    now = now or now_iso()
    dup = conn.execute("SELECT source_id, status FROM competitor_research_sources WHERE research_id=? AND file_sha256=?", (research_id, parsed["sha256"])).fetchone()
    if dup:
        return {"status": "DUPLICATE_FILE", "source_id": dup[0], "asin": a, "rows": 0,
                "message": f"«{parsed['file_name']}» è già stato importato in questo progetto: nessuna modifica."}
    active = _rows(conn, "SELECT source_id, file_name, imported_at FROM competitor_research_sources WHERE research_id=? AND status='ACTIVE' AND asin=?", (research_id, a))
    if active and not replace:
        return {"status": "ASIN_EXISTS", "source_id": None, "asin": a, "rows": 0,
                "message": f"L'ASIN {a} è già presente nel progetto (file «{active[0]['file_name']}» del {active[0]['imported_at'][:10]}): "
                           "per sostituirlo conferma esplicitamente; il vecchio file resta nello storico."}
    n_active = conn.execute("SELECT COUNT(*) FROM competitor_research_sources WHERE research_id=? AND status='ACTIVE'", (research_id,)).fetchone()[0]
    if not active and n_active >= MAX_COMPETITORS:
        raise StoreError(f"Un progetto può avere al massimo {MAX_COMPETITORS} competitor")
    rows = cerebro.merge_duplicate_rows(parsed["rows"])
    with conn:                                                             # una transazione per file: o tutto o niente
        for o in active:
            conn.execute("UPDATE competitor_research_sources SET status='SUPERSEDED' WHERE source_id=?", (o["source_id"],))
        cur = conn.execute(
            "INSERT INTO competitor_research_sources (research_id,asin,asin_source,marketplace,file_name,file_sha256,imported_at,row_count,status,columns_json,"
            "warnings_json,parser_version) VALUES (?,?,?,?,?,?,?,?,'ACTIVE',?,?,?)",
            (research_id, a, "MANUAL" if asin else parsed.get("asin_source"), proj["marketplace"], parsed["file_name"], parsed["sha256"], now, len(rows),
             json.dumps([{"header": c["header"], "canonical": c["canonical"]} for c in parsed["columns"]], ensure_ascii=False),
             json.dumps(parsed["warnings"], ensure_ascii=False), parsed.get("parser_version")))
        sid = cur.lastrowid
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO competitor_research_keywords (research_id,keyword_norm,keyword_text) VALUES (?,?,?)",
                         (research_id, r["keyword_norm"], r["keyword"]))
            kid = conn.execute("SELECT keyword_id FROM competitor_research_keywords WHERE research_id=? AND keyword_norm=?", (research_id, r["keyword_norm"])).fetchone()[0]
            conn.execute(f"INSERT INTO competitor_keyword_observations (source_id,keyword_id,research_id,asin,keyword_original,{','.join(METRIC_COLS)},raw_json) "
                         f"VALUES (?,?,?,?,?,{','.join('?' * len(METRIC_COLS))},?)",
                         (sid, kid, research_id, a, r["keyword"], *[r.get(c) for c in METRIC_COLS], json.dumps(r["raw"], ensure_ascii=False)))
        conn.execute("UPDATE competitor_research_projects SET updated_at=? WHERE research_id=?", (now, research_id))
    return {"status": "REPLACED" if active else "IMPORTED", "source_id": sid, "asin": a, "rows": len(rows),
            "message": f"{a}: {len(rows)} keyword importate" + (" (file precedente conservato nello storico)" if active else "")}


def list_sources(conn, research_id, active_only=False) -> list:
    sql = "SELECT * FROM competitor_research_sources WHERE research_id=?" + (" AND status='ACTIVE'" if active_only else "") + " ORDER BY imported_at, source_id"
    rows = _rows(conn, sql, (research_id,))
    for r in rows:
        r["warnings"] = json.loads(r.pop("warnings_json") or "[]")
        r["columns"] = json.loads(r.pop("columns_json") or "[]")
    return rows


def load_agg_sources(conn, research_id) -> list:
    """[{asin, source_id, rows:[...]}] dei soli file ATTIVI, ricostruiti dalle osservazioni (stesso formato del parser)."""
    out = []
    for s in list_sources(conn, research_id, active_only=True):
        obs = _rows(conn, "SELECT o.*, k.keyword_norm FROM competitor_keyword_observations o JOIN competitor_research_keywords k ON k.keyword_id=o.keyword_id "
                          "WHERE o.source_id=? ORDER BY o.obs_id", (s["source_id"],))
        rows = []
        for o in obs:
            row = {"keyword": o["keyword_original"], "keyword_norm": o["keyword_norm"], "raw": json.loads(o["raw_json"] or "{}")}
            row.update({c: o[c] for c in METRIC_COLS})
            rows.append(row)
        out.append({"asin": s["asin"], "source_id": s["source_id"], "file_name": s["file_name"], "rows": rows})
    return out


def raw_observations(conn, research_id, limit=5000) -> list:
    """DATI GREZZI: ogni osservazione (anche dei file sostituiti) con valore originale (raw_json)."""
    return _rows(conn, "SELECT s.asin, s.file_name, s.status AS source_status, k.keyword_norm, o.keyword_original, "
                       + ", ".join(f"o.{c}" for c in METRIC_COLS) + ", o.raw_json FROM competitor_keyword_observations o "
                       "JOIN competitor_research_sources s ON s.source_id=o.source_id JOIN competitor_research_keywords k ON k.keyword_id=o.keyword_id "
                       "WHERE o.research_id=? ORDER BY s.source_id, o.obs_id LIMIT ?", (research_id, limit))


# ------------------------------------------------------------------ brand / IP manuale
def set_brand_override(conn, research_id, keyword_norm, value) -> None:
    """True = segna BRAND_OR_IP; False = NON brand (annulla il rilevamento automatico); None = torna al rilevamento automatico."""
    if not conn.execute("SELECT 1 FROM competitor_research_keywords WHERE research_id=? AND keyword_norm=?", (research_id, keyword_norm)).fetchone():
        raise StoreError("Keyword non trovata nel progetto")
    with conn:
        conn.execute("UPDATE competitor_research_keywords SET brand_override=? WHERE research_id=? AND keyword_norm=?",
                     (None if value is None else int(bool(value)), research_id, keyword_norm))
        conn.execute("UPDATE competitor_research_projects SET updated_at=? WHERE research_id=?", (now_iso(), research_id))


def brand_overrides(conn, research_id) -> dict:
    return {r["keyword_norm"]: bool(r["brand_override"]) for r in
            _rows(conn, "SELECT keyword_norm, brand_override FROM competitor_research_keywords WHERE research_id=? AND brand_override IS NOT NULL", (research_id,))}


# ------------------------------------------------------------------ contesto dei dati reali (SOLA LETTURA)
def own_context(conn, book_id, marketplace=None, negatives=()) -> dict:
    """Keyword target, search term osservati e negative del libro (+ marketplace) per il gap. Legge target_rows / search_term_rows senza scrivere.
    I Search Term sono presi come MASSIMO per finestra (le finestre 30D/65D/lifetime sono nidificate: mai sommate). Tabelle assenti = contesto vuoto."""
    mp = str(marketplace).upper() if marketplace else None
    cond = " AND (UPPER(marketplace)=? OR marketplace IS NULL)" if mp else ""
    params = (str(book_id), mp) if mp else (str(book_id),)
    targets = _rows(conn, "SELECT target, match_type, campaign_type, campaign_name FROM target_rows WHERE book_id=?" + cond + " GROUP BY target, match_type, campaign_type, campaign_name", params)
    per = _rows(conn, "SELECT search_term, period_id, SUM(clicks) AS clicks, SUM(orders) AS orders, SUM(spend) AS spend FROM search_term_rows WHERE book_id=?" + cond
                + " GROUP BY search_term, period_id", params)
    best = {}
    for r in per:
        b = best.setdefault(r["search_term"], {"search_term": r["search_term"], "clicks": 0, "orders": 0, "spend": 0})
        for f in ("clicks", "orders", "spend"):
            b[f] = max(b[f], r[f] or 0)
    return C.build_own_context(targets, best.values(), negatives)


def negatives_from_opportunities(records) -> list:
    """Search term che il motore Amazon Ads propone come NEGATIVE_CANDIDATE: l'unica fonte di 'negative' nel DB (le negative effettive non sono importate)."""
    return sorted({r["search_term"] for r in records or [] if r.get("opportunity_type") == "NEGATIVE_CANDIDATE" and r.get("search_term")})


# ------------------------------------------------------------------ analisi completa di un progetto
def _others_for_overlap(conn, research_id, origin_book_id) -> list:
    others = []
    for b in _rows(conn, "SELECT book_id, title FROM books WHERE title IS NOT NULL"):
        if b["book_id"] != origin_book_id:
            others.append({"kind": "LIBRO", "name": b["title"], "tokens": set(C.content_tokens(b["title"])), "ref": b["book_id"]})
    for o in _rows(conn, "SELECT o.research_id, o.label, o.payload_json, o.saved_as_project, o.project_title, p.name AS research_name FROM competitor_opportunities o "
                         "JOIN competitor_research_projects p ON p.research_id=o.research_id WHERE o.research_id<>? AND o.status<>'REJECTED'", (research_id,)):
        try:
            core = set(json.loads(o["payload_json"] or "{}").get("topic", [])) | set(json.loads(o["payload_json"] or "{}").get("audience", []))
        except ValueError:
            continue
        if core:
            others.append({"kind": "PROGETTO" if o["saved_as_project"] else "RICERCA", "name": f"{o['project_title'] or o['label']} ({o['research_name']})",
                           "tokens": core, "ref": o["research_id"]})
    return others


def analyze(conn, research_id, rules=None, own=None) -> dict:
    """Analisi completa e DETERMINISTICA di un progetto (nessuna scrittura): aggregato, keyword comuni, gap (solo EXISTING_BOOK), suggerimenti Ads,
    opportunita' editoriali (sempre), panoramica. `own` = C.build_own_context(...) o store.own_context(...); se None, il gap e' calcolato su un contesto vuoto."""
    proj = get_project(conn, research_id)
    if not proj:
        raise StoreError("Progetto di ricerca non trovato")
    cfg = C.config(rules)
    srcs = load_agg_sources(conn, research_id)
    agg = C.aggregate(srcs, brand_overrides=brand_overrides(conn, research_id))
    existing = proj["mode"] == "EXISTING_BOOK"
    gap = ads = None
    if existing:
        gap = C.gap_table(agg, own if own is not None else C.build_own_context(), cfg, proj.get("book_title"))
        ads = C.ads_suggestions(gap, cfg, agg["total"])
    opps = C.build_opportunities(agg, cfg, proj.get("book_title") if existing else None, proj["book_id"] if existing else None,
                                 _others_for_overlap(conn, research_id, proj["book_id"]))
    launch = C.launch_keywords(agg, cfg, gap, proj.get("book_title") if existing else None)
    return {"project": proj, "cfg": cfg, "agg": agg, "launch": launch, "sources": list_sources(conn, research_id), "gap": gap, "ads": ads, "editorial": opps,
            "overview": C.overview(agg, ads, opps, proj["mode"]), "own_asins": sorted(own["product_asins"]) if own else []}


# ------------------------------------------------------------------ opportunita' editoriali persistite
def sync_opportunities(conn, research_id, opportunities: list, now=None) -> dict:
    """Salva/aggiorna i cluster derivati. Stato iniziale DA_APPROFONDIRE. Metriche, keyword e punteggio si aggiornano; stato, titolo del progetto e note
    scelti dall'utente NON vengono mai sovrascritti. I cluster non piu' prodotti spariscono solo se non toccati dall'utente (stato iniziale, non salvati)."""
    now = now or now_iso()
    kid = {r["keyword_norm"]: r["keyword_id"] for r in _rows(conn, "SELECT keyword_id, keyword_norm FROM competitor_research_keywords WHERE research_id=?", (research_id,))}
    name = (get_project(conn, research_id) or {}).get("name")
    keys, added, updated = set(), 0, 0
    with conn:
        for o in opportunities:
            keys.add(o["cluster_key"])
            payload = json.dumps({k: v for k, v in o.items()}, ensure_ascii=False, default=str)
            row = conn.execute("SELECT opportunity_id FROM competitor_opportunities WHERE research_id=? AND cluster_key=?", (research_id, o["cluster_key"])).fetchone()
            vals = (o["label"], o.get("origin_book_id"), o["score"], o["score_label"], o["competitor_count"], o["competitor_total"], o["keyword_count"], o["volume_sum"], payload)
            if row:
                oid = row[0]
                conn.execute("UPDATE competitor_opportunities SET label=?, origin_book_id=?, score=?, score_label=?, competitor_count=?, competitor_total=?, keyword_count=?, "
                             "volume_sum=?, payload_json=?, updated_at=? WHERE opportunity_id=?", (*vals, now, oid))
                updated += 1
            else:
                oid = conn.execute("INSERT INTO competitor_opportunities (research_id,cluster_key,label,origin_book_id,status,score,score_label,competitor_count,competitor_total,"
                                   "keyword_count,volume_sum,payload_json,research_source,created_at,updated_at) VALUES (?,?,?,?,'DA_APPROFONDIRE',?,?,?,?,?,?,?,?,?,?)",
                                   (research_id, o["cluster_key"], *vals, name, now, now)).lastrowid
                added += 1
            conn.execute("DELETE FROM competitor_opportunity_keywords WHERE opportunity_id=?", (oid,))
            conn.executemany("INSERT OR IGNORE INTO competitor_opportunity_keywords (opportunity_id,keyword_id) VALUES (?,?)",
                             [(oid, kid[n]) for n in o["keyword_norms"] if n in kid])
        stale = _rows(conn, "SELECT opportunity_id, cluster_key FROM competitor_opportunities WHERE research_id=? AND status='DA_APPROFONDIRE' AND saved_as_project=0 "
                            "AND (notes IS NULL OR notes='')", (research_id,))
        removed = 0
        for s in stale:
            if s["cluster_key"] not in keys:
                conn.execute("DELETE FROM competitor_opportunity_keywords WHERE opportunity_id=?", (s["opportunity_id"],))
                conn.execute("DELETE FROM competitor_opportunities WHERE opportunity_id=?", (s["opportunity_id"],))
                removed += 1
    return {"added": added, "updated": updated, "removed": removed}


def list_opportunities(conn, research_id) -> list:
    rows = _rows(conn, "SELECT * FROM competitor_opportunities WHERE research_id=? ORDER BY score DESC, label", (research_id,))
    for r in rows:
        r["payload"] = json.loads(r.pop("payload_json") or "{}")
        r["keywords"] = [k["keyword_text"] for k in _rows(
            conn, "SELECT k.keyword_text FROM competitor_opportunity_keywords ok JOIN competitor_research_keywords k ON k.keyword_id=ok.keyword_id "
                  "WHERE ok.opportunity_id=? ORDER BY k.keyword_text", (r["opportunity_id"],))]
    return rows


def save_as_editorial_project(conn, opportunity_id: int, title: str | None = None, notes: str | None = None, now=None) -> dict:
    """SALVA COME PROGETTO EDITORIALE: marca il cluster come progetto (status REVIEW) con titolo provvisorio, note, ricerca d'origine e libro origine
    (nullable). NON crea nulla in books.json, NON assegna ASIN, non tocca le tabelle Ads. Il collegamento a book_id/ASIN potra' avvenire dopo la pubblicazione."""
    row = _rows(conn, "SELECT o.*, p.name AS research_name FROM competitor_opportunities o JOIN competitor_research_projects p ON p.research_id=o.research_id "
                      "WHERE o.opportunity_id=?", (opportunity_id,))
    if not row:
        raise StoreError("Opportunità non trovata")
    o = row[0]
    if o["status"] == "REJECTED":
        raise StoreError("Opportunità scartata: riportala in revisione prima di salvarla come progetto")
    ttl = (title or o["project_title"] or o["label"]).strip()
    with conn:
        conn.execute("UPDATE competitor_opportunities SET saved_as_project=1, status=CASE WHEN status='DA_APPROFONDIRE' THEN 'REVIEW' ELSE status END, project_title=?, "
                     "notes=COALESCE(?, notes), research_source=?, updated_at=? WHERE opportunity_id=?",
                     (ttl, notes, o["research_name"], now or now_iso(), opportunity_id))
    return _rows(conn, "SELECT * FROM competitor_opportunities WHERE opportunity_id=?", (opportunity_id,))[0]


def set_opportunity_status(conn, opportunity_id: int, status: str, now=None) -> None:
    if status not in OPP_STATUSES:
        raise StoreError(f"Stato non valido: {status!r}")
    with conn:
        n = conn.execute("UPDATE competitor_opportunities SET status=?, updated_at=? WHERE opportunity_id=?", (status, now or now_iso(), opportunity_id)).rowcount
    if not n:
        raise StoreError("Opportunità non trovata")


def list_editorial_projects(conn, origin_book_id=None) -> list:
    sql = "SELECT * FROM competitor_opportunities WHERE saved_as_project=1"
    params = ()
    if origin_book_id:
        sql, params = sql + " AND origin_book_id=?", (origin_book_id,)
    return _rows(conn, sql + " ORDER BY updated_at DESC", params)


# ------------------------------------------------------------------ integrazione "Suggerimenti di crescita" (SOLA LETTURA)
def growth_section(conn, book_id, marketplace, rules=None, own=None) -> dict:
    """Dati per la sezione DA RICERCA COMPETITOR di un libro: per ogni ricerca EXISTING_BOOK non scartata del libro (stesso marketplace) i suggerimenti
    TESTA IN FRASE e gli ASIN competitor non ancora product target. Separati dai dati Amazon Ads reali; non entrano in nessun KPI."""
    if not has_schema(conn):
        return {"projects": [], "total": 0}
    own = own if own is not None else own_context(conn, book_id, marketplace)
    mp = str(marketplace).upper() if marketplace else None
    out = []
    for p in list_projects(conn, book_id):
        if p["mode"] != "EXISTING_BOOK" or p["status"] == "REJECTED" or (mp and p["marketplace"].upper() != mp) or not p["competitors"]:
            continue
        a = analyze(conn, p["research_id"], rules, own)
        new_asins = [x for x in a["agg"]["asins"] if x not in own["product_asins"]]
        out.append({"research_id": p["research_id"], "name": p["name"], "status": p["status"], "competitors": a["agg"]["total"], "reliability": a["overview"]["reliability"],
                    "book_title": p.get("book_title"), "suggestions": a["ads"], "new_product_targets": new_asins, "launch": C.launch_expansion(a["launch"])})
    return {"projects": out, "total": sum(len({s["keyword_norm"] for s in x["suggestions"]} | {i["keyword_norm"] for i in x["launch"]}) + len(x["new_product_targets"])
                                          for x in out)}
