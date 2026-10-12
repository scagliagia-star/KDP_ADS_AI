"""Data access READ-ONLY della dashboard. Legge DATABASE\\kdp_ads.db (sqlite3 aperto con mode=ro + query_only) e i CSV degli overlap.

Nessuna business logic del decision engine: le decisioni sono lette come salvate. Le uniche elaborazioni sono aggregazioni di
visualizzazione (totali per snapshot, conteggi) che riusano le funzioni pure gia' presenti (compute_metrics, window_label).
Tollera DB assente, schema precedente (colonne mancanti), valori NULL e run storici."""
import csv
import json
import re
import sqlite3
from pathlib import Path

from kdp_ads.decision import map_target_status
from kdp_ads.metrics import compute_metrics
from kdp_ads.opportunities import compute_opportunities, load_inputs, make_ctx
from kdp_ads.rules import load_rules
from kdp_ads.snapshots import days_inclusive, window_label


class RepoError(Exception):
    """Condizione prevista (DB assente, tabella mancante): la UI la mostra, non crasha."""


_CSV_NUMBERS = ("clicks", "cpc", "orders", "conversion_rate", "spend", "sales", "estimated_direct_ad_profit")
_DELIVERY_RE = re.compile(r"(\w+)/giorno: recente ([\d.]+) \(([^,()]+), (\d+)g\) vs riferimento ([\d.]+) \(([^,()]+), (\d+)g\) = x([\d.]+)")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Repo:
    def __init__(self, db_path, root=None):
        self.db_path = Path(db_path)
        self.root = Path(root) if root else None

    # ------------------------------------------------------------ connessione
    def exists(self) -> bool:
        return self.db_path.is_file()

    def _connect(self) -> sqlite3.Connection:
        if not self.exists():
            raise RepoError(f"Database non trovato: {self.db_path}")
        conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _query(self, sql, params=()):
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        except sqlite3.OperationalError as e:            # tabella/colonna assente (schema precedente)
            raise RepoError(f"Schema del database non compatibile: {e}") from e
        finally:
            conn.close()

    def _tables(self) -> set:
        return {r["name"] for r in self._query("SELECT name FROM sqlite_master WHERE type='table'")}

    def columns(self, table) -> set:
        return {r["name"] for r in self._query(f"PRAGMA table_info({table})")}

    # ------------------------------------------------------------ libri e run
    def list_books(self) -> list:
        tables = self._tables()
        books = []
        if "books" in tables:
            books = self._query("SELECT * FROM books ORDER BY title, book_id")
        known = {b["book_id"] for b in books}
        if "decision_runs" in tables:                   # libro con run ma non (piu') in tabella books
            for r in self._query("SELECT DISTINCT book_id FROM decision_runs"):
                if r["book_id"] not in known:
                    books.append({"book_id": r["book_id"], "title": None})
        return books

    def book(self, book_id):
        return next((b for b in self.list_books() if b["book_id"] == book_id), None)

    # ------------------------------------------------------------ marketplace
    def legacy_marketplace(self, book_id):
        """Marketplace dei run storici (che non lo registravano): quello della riga `books` (il marketplace principale)."""
        b = self.book(book_id) if "books" in self._tables() else None
        return str(b["marketplace"]).upper() if b and b.get("marketplace") else None

    def marketplaces(self, book_id) -> list:
        """Marketplace del libro con la loro economia (prezzo, royalty, ACOS, valuta). Da `book_marketplaces`; per DB precedenti
        al multi-marketplace: l'unico marketplace della riga `books`."""
        out = []
        if "book_marketplaces" in self._tables():
            out = self._query("SELECT * FROM book_marketplaces WHERE book_id=? ORDER BY marketplace", (book_id,))
        if not out:
            b = self.book(book_id) if "books" in self._tables() else None
            if b and b.get("marketplace"):
                out = [{**b, "marketplace": str(b["marketplace"]).upper()}]
        legacy = self.legacy_marketplace(book_id)
        if legacy and all(str(m["marketplace"]).upper() != legacy for m in out):
            # marketplace storico del libro (riga `books`) assente da `book_marketplaces` (es. aggiunto un altro marketplace dopo): resta visibile
            b = self.book(book_id) if "books" in self._tables() else None
            if b and self._query("SELECT 1 FROM decision_runs WHERE book_id=? LIMIT 1", (book_id,)) if "decision_runs" in self._tables() else False:
                out = [{**b, "marketplace": legacy}, *out]
        return sorted(out, key=lambda m: (m["marketplace"] != legacy, m["marketplace"]))      # marketplace principale per primo

    def _mp_clause(self, table, book_id, marketplace):
        """Filtro per marketplace; i run storici (marketplace NULL) appartengono al marketplace legacy del libro.
        Su DB senza la colonna non filtra."""
        if not marketplace or "marketplace" not in self.columns(table):
            return "", ()
        mp = str(marketplace).upper()
        return " AND (UPPER(marketplace)=? OR (marketplace IS NULL AND ?=?))", (mp, mp, self.legacy_marketplace(book_id))

    def decision_runs(self, book_id, marketplace=None) -> list:
        if "decision_runs" not in self._tables():
            return []
        sql, args = self._mp_clause("decision_runs", book_id, marketplace)
        return self._query(f"SELECT * FROM decision_runs WHERE book_id=?{sql} ORDER BY run_id DESC", (book_id, *args))

    def latest_decision_run(self, book_id, marketplace=None):
        runs = self.decision_runs(book_id, marketplace)
        return runs[0] if runs else None

    def analysis_run(self, run_id):
        if run_id is None or "analysis_runs" not in self._tables():
            return None
        rows = self._query("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def latest_analysis_run(self, book_id, marketplace=None):
        if "analysis_runs" not in self._tables():
            return None
        sql, args = self._mp_clause("analysis_runs", book_id, marketplace)
        rows = self._query(f"SELECT * FROM analysis_runs WHERE book_id=?{sql} ORDER BY run_id DESC LIMIT 1", (book_id, *args))
        return rows[0] if rows else None

    def analysis_run_for_decision_run(self, decision_run):
        """Mapping decision run -> analysis run da decision_runs.analysis_run_id."""
        return self.analysis_run(decision_run.get("analysis_run_id")) if decision_run else None

    # ------------------------------------------------------------ decisioni
    def decisions(self, decision_run_id) -> list:
        """Tutte le righe del run, come salvate. Colonne mancanti in DB vecchi = chiave assente (usare .get)."""
        if "decisions" not in self._tables():
            return []
        rows = self._query("SELECT * FROM decisions WHERE run_id=? ORDER BY pk", (decision_run_id,))
        order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        rows.sort(key=lambda d: (order.get(d.get("priority"), 9), d["pk"]))
        return rows

    # ------------------------------------------------------------ warning
    def warnings(self, decision_run, analysis_run=None) -> list:
        """DATA_WARNING del run: quelli salvati nel decision run (con prefisso di provenienza) piu', per i run storici che non li
        ereditavano, quelli dell'analysis run collegato (marcati come tali). Nessuna severita' nel DB: 'n/d'."""
        if not decision_run:
            return []
        try:
            saved = json.loads(decision_run.get("warnings_json") or "[]")
        except ValueError:
            saved = []
        campaigns = self._campaign_ids(decision_run.get("book_id"), decision_run.get("marketplace"))
        out, seen = [], set()

        def add(text, origin):
            body = text
            m = re.match(r"^\[(analysis_run \d+|decision)\]\s*(.*)$", text, re.S)
            if m:
                origin, body = m.group(1), m.group(2)
            if body in seen:
                return
            seen.add(body)
            kind = "DATA_WARNING" if body.startswith("DATA_WARNING") else "NOTA" if body.startswith("NOTA") else "ALTRO"
            out.append({"origin": origin, "campaign": next((c for c in campaigns if c in body), None), "text": body,
                        "kind": kind, "severity": "n/d"})
        for w in saved:
            add(w, "decision (legacy, senza prefisso)")
        if analysis_run:
            try:
                inherited = json.loads(analysis_run.get("warnings_json") or "[]")
            except ValueError:
                inherited = []
            for w in inherited:
                add(w, f"analysis_run {analysis_run['run_id']} (non salvato nel decision run)")
        return out

    def _campaign_ids(self, book_id, marketplace=None) -> list:
        if "campaigns" not in self._tables():
            return []
        mp = " AND UPPER(marketplace)=?" if marketplace and "marketplace" in self.columns("campaigns") else ""
        args = (book_id, str(marketplace).upper()) if mp else (book_id,)
        return [r["campaign_id"] for r in self._query(f"SELECT campaign_id FROM campaigns WHERE book_id=?{mp}", args)]

    # ------------------------------------------------------------ campagne
    def campaign_overview(self, book_id, royalty, marketplace=None) -> list:
        """Totali per campagna e per finestra. Ogni finestra viene dal PROPRIO snapshot (una sola fonte per finestra):
        le finestre sovrapposte non vengono mai sommate. Preferenza Target report, altrimenti Search Term."""
        tables = self._tables()
        if "imports" not in tables:
            return []
        mpc = " AND UPPER(marketplace)=?" if marketplace and "marketplace" in self.columns("campaigns") else ""
        mpa = (str(marketplace).upper(),) if mpc else ()
        camps = {r["campaign_id"]: r for r in self._query(f"SELECT * FROM campaigns WHERE book_id=?{mpc}", (book_id, *mpa))} if "campaigns" in tables else {}
        snaps = {}
        for table, rtype in (("target_rows", "TARGETING"), ("search_term_rows", "SEARCH_TERM")):
            if table not in tables:
                continue
            for r in self._query(
                    f"SELECT campaign_id, report_scope, date_from, date_to, period_id, COUNT(*) n, SUM(impressions) impressions, "
                    f"SUM(clicks) clicks, SUM(spend) spend, SUM(orders) orders, SUM(sales) sales FROM {table} "
                    f"WHERE book_id=?{' AND UPPER(marketplace)=?' if mpa else ''} GROUP BY import_id", (book_id, *mpa)):
                label = window_label(r["report_scope"], r["date_from"], r["date_to"])
                snaps.setdefault(r["campaign_id"], {}).setdefault(r["period_id"], {})[rtype] = {**r, "report_type": rtype}
        out = []
        for cid in sorted(set(camps) | set(snaps)):
            wins = []
            for _pid, by_rt in snaps.get(cid, {}).items():
                s = by_rt.get("TARGETING") or by_rt.get("SEARCH_TERM")
                label = window_label(s["report_scope"], s["date_from"], s["date_to"])
                m = compute_metrics(s["impressions"], s["clicks"], s["spend"], s["orders"], s["sales"], royalty or 0)
                days = days_inclusive(s["date_from"], s["date_to"]) if s["report_scope"] == "DATE_RANGE" else None
                wins.append({"window": label, "report_type": s["report_type"], "period_id": s["period_id"], "days": days, "rows": s["n"],
                             "clicks": m.clicks, "spend": m.spend, "orders": m.orders, "sales": m.sales, "acos": m.acos,
                             "estimated_profit": m.est_profit,
                             "spend_per_day": (m.spend / days) if days else None, "clicks_per_day": (m.clicks / days) if days else None,
                             "orders_per_day": (m.orders / days) if days else None})
            wins.sort(key=lambda w: (w["window"] != "LIFETIME", -(w["days"] or 0)))
            out.append({"campaign_id": cid, "campaign_type": (camps.get(cid) or {}).get("campaign_type"), "windows": wins})
        return out

    # ------------------------------------------------------------ opportunita' search term (calcolo in memoria, nessuna scrittura)
    def rules(self) -> dict:
        """rules.json del progetto (sola lettura): soglie dei suggerimenti di crescita."""
        candidates = [Path(self.root) / "CONFIG" / "rules.json"] if self.root else []
        candidates.append(Path(__file__).resolve().parents[2] / "CONFIG" / "rules.json")
        path = next((c for c in candidates if c.is_file()), None)
        return load_rules(path) if path else {}

    def search_term_opportunities(self, book, marketplace) -> dict:
        """Opportunita' Search Term di UN libro e UN marketplace, calcolate al volo dai dati importati (kdp_ads.opportunities).
        `book` = riga del libro con l'economia del marketplace (royalty, target ACOS, break-even). Nessuna persistenza."""
        for k in ("royalty_per_copy", "target_acos", "break_even_acos"):
            if book.get(k) is None:
                raise RepoError(f"Economia del libro incompleta ({k}): impossibile calcolare le opportunita'.")
        candidates = [Path(self.root) / "CONFIG" / "rules.json"] if self.root else []
        candidates.append(Path(__file__).resolve().parents[2] / "CONFIG" / "rules.json")      # rules.json del progetto
        rules_path = next((c for c in candidates if c.is_file()), None)
        if rules_path is None:
            raise RepoError(f"rules.json non trovato: {candidates[0]}")
        conn = self._connect()
        try:
            data = load_inputs(conn, book["book_id"], marketplace, self.legacy_marketplace(book["book_id"]))
        except sqlite3.OperationalError as e:
            raise RepoError(f"Schema del database non compatibile: {e}") from e
        finally:
            conn.close()
        ctx = make_ctx({**book, "marketplace": marketplace}, load_rules(rules_path))
        return compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], ctx, map_target_status)

    # ------------------------------------------------------------ ricerca competitor (Helium 10, fonte ESTERNA; sola lettura)
    def competitor_research(self, book, marketplace, opportunity_records=None) -> dict:
        """Sezione DA RICERCA COMPETITOR per i Suggerimenti di crescita: keyword Cerebro da testare in FRASE e ASIN competitor non ancora target prodotto,
        per le ricerche del libro. Separata dai dati Amazon Ads reali (non entra in nessun KPI). DB senza le tabelle competitor_* => sezione vuota."""
        from kdp_ads import competitor_store as CS
        conn = self._connect()
        try:
            if not CS.has_schema(conn):
                return {"projects": [], "total": 0}
            negatives = CS.negatives_from_opportunities(opportunity_records)
            own = CS.own_context(conn, book["book_id"], marketplace, negatives)
            return CS.growth_section(conn, book["book_id"], marketplace, self.rules(), own)
        except sqlite3.Error as e:
            raise RepoError(f"Schema del database non compatibile: {e}") from e
        finally:
            conn.close()

    # ------------------------------------------------------------ cross-campaign
    def overlaps(self, analysis_run):
        """Overlap dell'analysis run: (gruppi, messaggio). Letti dal CSV registrato in analysis_runs.overlaps_csv_path."""
        if not analysis_run:
            return [], "Nessun analysis run collegato."
        p = analysis_run.get("overlaps_csv_path")
        if not p:
            return [], "Nessun file overlap registrato per questo analysis run."
        path = Path(p)
        if not path.is_file():
            alt = (self.root or Path(".")) / "OUTPUT" / "recommendations" / path.name
            path = alt if alt.is_file() else path
        if not path.is_file():
            return [], f"File overlap non trovato: {p}"
        groups = {}
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                key = (r.get("search_term"), r.get("period_id"))
                g = groups.setdefault(key, {
                    "search_term": r.get("search_term"), "period_id": r.get("period_id"),
                    "classification": r.get("overlap_classification"),
                    "possible_inefficiency": str(r.get("possible_inefficiency")).strip().lower() == "true",
                    "better_campaign": r.get("better_campaign") or None, "worse_campaign": r.get("worse_campaign") or None,
                    "recommendation": r.get("recommendation") or None, "detail": r.get("detail") or None, "members": []})
                m = {"campaign_id": r.get("campaign_id"), "campaign_type": r.get("campaign_type"), "acos": r.get("acos")}
                m.update({k: _num(r.get(k)) for k in _CSV_NUMBERS})
                m["acos_value"] = _num(r.get("acos"))
                g["members"].append(m)
        return sorted(groups.values(), key=lambda g: (not g["possible_inefficiency"], g["period_id"] or "", g["search_term"] or "")), ""


# ------------------------------------------------------------ riepiloghi puri sulle decisioni gia' lette
def summarize(decisions, warnings) -> dict:
    """Conteggi dalle decisioni salvate. None = non disponibile (run precedente alle colonne target_status / operationally_actionable)."""
    def have(col):
        return any(d.get(col) is not None for d in decisions)
    return {
        "total": len(decisions),
        "actionable": sum(1 for d in decisions if d.get("operationally_actionable")) if have("operationally_actionable") else None,
        "review_required": sum(1 for d in decisions if str(d.get("final_action") or "").startswith("REVIEW")),
        "warnings": len(warnings),
        "paused": sum(1 for d in decisions if d.get("target_status") == "PAUSED") if have("target_status") else None,
        "paused_with_underlying": sum(1 for d in decisions if d.get("target_status") == "PAUSED" and d.get("underlying_candidate_action")) if have("target_status") else None,
        "has_new_fields": have("target_status") and have("operationally_actionable"),
    }


def filter_decisions(decisions, campaign=None, priority=None, target_status=None, final_action=None, confidence=None,
                     actionable=None, delivery_signal=None, delivery_source=None, search=""):
    """Filtri della tabella. Ogni argomento None/'ALL' = nessun filtro. actionable: True/False."""
    def ok(val, want):
        return want in (None, "", "ALL") or val == want
    s = (search or "").strip().lower()
    out = []
    for d in decisions:
        if not (ok(d.get("campaign_id"), campaign) and ok(d.get("priority"), priority) and ok(d.get("target_status"), target_status)
                and ok(d.get("final_action"), final_action) and ok(d.get("confidence"), confidence)
                and ok(d.get("delivery_signal"), delivery_signal) and ok(d.get("delivery_source"), delivery_source)):
            continue
        if actionable is not None and bool(d.get("operationally_actionable")) != bool(actionable):
            continue
        if s and s not in str(d.get("target") or "").lower():
            continue
        out.append(d)
    return out


def parse_delivery_detail(reason):
    """Estrae dal reason (testo salvato dal decision layer) le impressioni/giorno recente e di riferimento. Nessun ricalcolo."""
    m = _DELIVERY_RE.search(reason or "")
    if not m:
        return None
    return {"metric": m.group(1), "recent": float(m.group(2)), "recent_period": m.group(3), "recent_days": int(m.group(4)),
            "reference": float(m.group(5)), "reference_period": m.group(6), "reference_days": int(m.group(7)), "ratio": float(m.group(8))}
