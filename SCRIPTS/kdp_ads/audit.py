"""Audit preliminare dei dati importati, per SNAPSHOT. Segnala DATA_WARNING, non corregge nulla.
Le finestre temporali sovrapposte non vengono mai sommate: ogni totale appartiene a un solo snapshot."""
from pathlib import Path

from . import amazon as amz
from .importer import _read_table
from .mapping import analyze_headers, dedupe_headers
from .metrics import normalize_term
from .parsing import parse_number
from .snapshots import days_inclusive, period_id, window_label

_TABLES = (("target_rows", "TARGETING"), ("search_term_rows", "SEARCH_TERM"))


def _contained(a: dict, b: dict) -> bool:
    """La finestra di `a` e' contenuta in quella di `b` (il lifetime contiene tutto fino al suo date_to)."""
    if not a["date_to"] or not b["date_to"] or a["report_scope"] == "LIFETIME" and b["report_scope"] != "LIFETIME":
        return False
    if b["report_scope"] == "LIFETIME":
        return a["date_to"] <= b["date_to"]
    if a["report_scope"] != "DATE_RANGE" or not a["date_from"] or not b["date_from"]:
        return False
    return b["date_from"] <= a["date_from"] and a["date_to"] <= b["date_to"]


def _label(t: dict) -> str:
    if t["report_type"] == "TARGETING":
        return "Target lifetime" if t["report_scope"] == "LIFETIME" else f"Target {t['period_id']}"
    return f"Search Term recente {t['period_id']}"


def audit_book(conn, book_id: str, rules: dict, mapping: dict, royalty: float) -> dict:
    """Ricalcola i totali dal DB per campagna/report/SNAPSHOT e confronta con i valori dichiarati nei CSV.
    Ritorna {'totals': [...], 'warnings': [...], 'amazon_pending': [...]}."""
    a = rules["audit"]
    warnings, totals = [], []
    for table, rt in _TABLES:
        for r in conn.execute(
                f"SELECT campaign_id,campaign_type,report_scope,date_from,date_to,period_id,COUNT(*) n,SUM(impressions) i,SUM(clicks) c,"
                f"SUM(spend) s,SUM(orders) o,SUM(sales) v FROM {table} WHERE book_id=? "
                f"GROUP BY campaign_id, report_scope, date_from, date_to ORDER BY campaign_id, date_to, date_from", (book_id,)):
            i, c, s, o, v = (r[k] or 0 for k in ("i", "c", "s", "o", "v"))
            pid = r["period_id"] or period_id(r["report_scope"], r["date_from"], r["date_to"])
            days = days_inclusive(r["date_from"], r["date_to"]) if r["report_scope"] == "DATE_RANGE" else None
            totals.append({"campaign_id": r["campaign_id"], "campaign_type": r["campaign_type"], "report_type": rt,
                           "report_scope": r["report_scope"], "date_from": r["date_from"], "date_to": r["date_to"],
                           "period_id": pid, "window_id": window_label(r["report_scope"], r["date_from"], r["date_to"]),
                           "days": days, "rows": r["n"],
                           "impressions": i, "clicks": c, "spend": s, "orders": o, "sales": v,
                           "ctr": c / i * 100 if i else None, "cpc": s / c if c else None, "cvr": o / c * 100 if c else None,
                           "acos": s / v * 100 if v else None, "roas": v / s if s else None,
                           "est_royalty": o * royalty, "est_profit": o * royalty - s,
                           "spend_per_day": s / days if days else None, "orders_per_day": o / days if days else None,
                           "clicks_per_day": c / days if days else None, "impressions_per_day": i / days if days else None})
        # righe con rapporti dichiarati incoerenti col ricalcolo
        bad = {"ACOS": [], "CTR": [], "CPC": []}
        sel = "search_term" if rt == "SEARCH_TERM" else "NULL AS search_term"
        for r in conn.execute(f"SELECT campaign_id,target,{sel},impressions,clicks,spend,sales,acos_reported_pct,ctr_reported_pct,"
                              f"cpc_reported FROM {table} WHERE book_id=?", (book_id,)):
            if r["acos_reported_pct"] is not None and r["sales"]:
                if abs(r["spend"] / r["sales"] * 100 - r["acos_reported_pct"]) > a["acos_tol_pp"]:
                    bad["ACOS"].append(r)
            if r["ctr_reported_pct"] is not None and r["impressions"]:
                if abs(r["clicks"] / r["impressions"] * 100 - r["ctr_reported_pct"]) > a["ctr_tol_pp"]:
                    bad["CTR"].append(r)
            if r["cpc_reported"] is not None and r["clicks"]:
                if abs(r["spend"] / r["clicks"] - r["cpc_reported"]) > a["cpc_tol_usd"]:
                    bad["CPC"].append(r)
        for k, rows in bad.items():
            if rows:
                warnings.append(f"DATA_WARNING [{rt}] {k} dichiarato incoerente con il ricalcolo in {len(rows)} righe "
                                f"(es. campagna {rows[0]['campaign_id']}, target {rows[0]['target']!r})")

    # integrita' DB <-> import <-> file CSV sorgente
    for imp in conn.execute("SELECT * FROM imports WHERE book_id=?", (book_id,)):
        tbl = "search_term_rows" if imp["report_type"] == "SEARCH_TERM" else "target_rows"
        got = conn.execute(f"SELECT COUNT(*) n,SUM(clicks) c,SUM(spend) s,SUM(orders) o,SUM(sales) v FROM {tbl} WHERE import_id=?",
                           (imp["import_id"],)).fetchone()
        if got["n"] != imp["row_count"]:
            warnings.append(f"DATA_WARNING import {imp['import_id']}: righe DB {got['n']} != row_count {imp['row_count']}")
        p = Path(imp["file_path"])
        if not p.exists():
            warnings.append(f"DATA_WARNING import {imp['import_id']}: file sorgente non trovato ({p}); verifica CSV saltata")
            continue
        headers, data = _read_table(p)
        headers, dups = dedupe_headers(headers)
        m = analyze_headers(headers, mapping, dups)
        idx = {h: i for i, h in enumerate(headers)}
        tot = {}
        for f in ("clicks", "spend", "orders", "sales"):
            j = idx[m.field_to_header[f]]
            tot[f] = sum(parse_number(row[j]) or 0 for row in data if j < len(row))
        for f, dbv in (("clicks", got["c"]), ("spend", got["s"]), ("orders", got["o"]), ("sales", got["v"])):
            if abs(tot[f] - (dbv or 0)) > a["sum_rel_tol"] * max(1, abs(tot[f])):
                warnings.append(f"DATA_WARNING import {imp['import_id']} ({imp['source_filename']}): {f} CSV {tot[f]:.2f} != DB {(dbv or 0):.2f}")

    # coerenza fra snapshot della stessa campagna: una finestra CONTENUTA in un'altra non puo' superarla.
    # Search Term <= Target (stessa finestra o contenente); stesso tipo: finestra interna <= finestra esterna.
    by_camp = {}
    for t in totals:
        by_camp.setdefault(t["campaign_id"], []).append(t)
    for cid, ts in by_camp.items():
        for x in ts:
            for y in ts:
                if x is y or not _contained(x, y):
                    continue
                if not (x["report_type"] == y["report_type"] or (x["report_type"] == "SEARCH_TERM" and y["report_type"] == "TARGETING")):
                    continue
                if x["report_type"] == y["report_type"] and _contained(y, x):
                    continue          # finestre identiche dello stesso tipo: impossibile (snapshot univoco)
                for f in ("clicks", "spend", "orders", "sales"):
                    if x[f] > y[f] * (1 + a["sum_rel_tol"]):
                        warnings.append(f"DATA_WARNING {cid}: {f} {_label(x)} ({x[f]:.2f}) > {_label(y)} ({y[f]:.2f})")
        for x in ts:
            if x["report_type"] == "SEARCH_TERM" and not any(y["report_type"] == "TARGETING" and _contained(x, y) for y in ts):
                warnings.append(f"DATA_WARNING {cid}: Search Term {x['period_id']} senza un Target report importato che lo contenga")
    if any(t["report_scope"] == "UNKNOWN" for t in totals):
        warnings.append("DATA_WARNING: almeno un report ha scope/date UNKNOWN")

    # osservazioni Amazon a livello campagna: confronto con i totali Target dello STESSO intervallo, se importato
    cfg = rules.get("amazon_compare", {})
    pending = []
    for ar in amz.load(conn, book_id):
        if ar["target_normalized"] or ar["observed_spend"] is None:
            continue
        same = [t for t in totals if t["campaign_id"] == ar["campaign_id"] and t["report_type"] == "TARGETING"
                and t["report_scope"] == "DATE_RANGE" and t["date_from"] == ar["period_from"] and t["date_to"] == ar["period_to"]]
        if not same:
            pending.append(ar)
            continue
        t = same[0]
        tol = max(cfg.get("spend_abs_tol_usd", 1.0), cfg.get("spend_rel_tol", 0.10) * max(t["spend"], ar["observed_spend"]))
        if abs(t["spend"] - ar["observed_spend"]) > tol:
            warnings.append(f"DATA_WARNING {ar['campaign_id']} {ar['period_from']}→{ar['period_to']}: spesa Target importata "
                            f"${t['spend']:.2f} differisce materialmente dall'osservazione Amazon ${ar['observed_spend']:.2f} "
                            f"(tolleranza ${tol:.2f})")
        if ar["observed_orders"] is not None and abs(t["orders"] - ar["observed_orders"]) > cfg.get("orders_abs_tol", 0.5):
            warnings.append(f"DATA_WARNING {ar['campaign_id']} {ar['period_from']}→{ar['period_to']}: ordini Target importati "
                            f"{t['orders']:g} differiscono dall'osservazione Amazon {ar['observed_orders']:g}")
    return {"totals": totals, "warnings": warnings, "amazon_pending": pending}


def duplicate_key_warnings(target_rows, st_rows) -> list[str]:
    """Chiavi duplicate DENTRO lo stesso snapshot (lo stesso target in snapshot diversi e' normale)."""
    out = []
    seen = {}
    for t in target_rows:
        k = (t["campaign_id"], t.get("period_id"), normalize_term(t["target"]), t["match_type"])
        seen[k] = seen.get(k, 0) + 1
    out += [f"DATA_WARNING target duplicato (campagna {k[0]}, {k[1]}, {k[2]!r}, {k[3]}) x{n}" for k, n in seen.items() if n > 1]
    seen = {}
    for s in st_rows:
        k = (s["campaign_id"], s.get("period_id"), normalize_term(s["search_term"]), normalize_term(s["target"]))
        seen[k] = seen.get(k, 0) + 1
    out += [f"DATA_WARNING search term duplicato (campagna {k[0]}, {k[1]}, {k[2]!r}, target {k[3]!r}) x{n}" for k, n in seen.items() if n > 1]
    return out
