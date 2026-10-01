"""Audit READ-ONLY delle Opportunita' Search Term di un libro/marketplace. Apre il database in SOLA LETTURA, non scrive nulla
(nessun run, nessun file salvo --csv), non si collega ad Amazon. Uso:
    python SCRIPTS\\opportunities.py --book 1805566555 --marketplace US [--top 10] [--csv percorso.csv]"""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads.config import CONFIG_DIR, load_settings
from kdp_ads.decision import map_target_status
from kdp_ads.opportunities import (NEGATIVE, NO_ACTION, PROMOTE, WATCH, compute_opportunities, load_inputs, make_ctx, rank, unique_terms)
from kdp_ads.rules import load_rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--marketplace", required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--csv", help="esporta i record in un CSV (unico file scritto)")
    a = ap.parse_args()
    settings = load_settings()
    db = Path(settings["db_path"])
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    brow = conn.execute("SELECT * FROM book_marketplaces WHERE book_id=? AND UPPER(marketplace)=?", (a.book, a.marketplace.upper())).fetchone()
    if brow is None:
        brow = conn.execute("SELECT * FROM books WHERE book_id=? AND UPPER(marketplace)=?", (a.book, a.marketplace.upper())).fetchone()
    if brow is None:
        print("libro/marketplace non trovato")
        return 1
    book = {**dict(brow), "book_id": a.book, "marketplace": a.marketplace.upper()}
    legacy = conn.execute("SELECT marketplace FROM books WHERE book_id=?", (a.book,)).fetchone()
    data = load_inputs(conn, a.book, a.marketplace, legacy[0] if legacy else None)
    conn.close()
    ctx = make_ctx(book, load_rules(CONFIG_DIR / "rules.json"))
    res = compute_opportunities(data["st_rows"], data["target_rows"], data["campaigns"], ctx, map_target_status)
    recs = res["records"]
    print(f"Libro {a.book} · {a.marketplace} · royalty {ctx.royalty} · ACOS obiettivo {ctx.target_acos} · pareggio {ctx.break_even_acos}")
    print(f"Record: {len(recs)} · esclusi: {res['excluded']}")
    for t in (PROMOTE, NEGATIVE, WATCH, NO_ACTION):
        print(f"  {t}: {sum(1 for r in recs if r['opportunity_type'] == t)} record, {len(unique_terms(recs, t))} termini unici")
    for t in (PROMOTE, NEGATIVE):
        print(f"\nTOP {a.top} {t}")
        for r in rank(recs, t)[:a.top]:
            print(f"  {r['search_term']!r} | {r['campaign_id']} ({r['campaign_type']}) | padre {r['parent_target']!r} [{r['parent_target_type']}] | "
                  f"{r['primary_window']} {r['clicks']:g}c/{r['orders']:g}o/{r['spend']:.2f}/{r['sales']:.2f} ACOS {r['acos'] if r['acos'] is None else round(r['acos'], 1)} | "
                  f"{r['confidence']} {r['windows_agreement']} | {r['reason']}")
    print("\nCampagna EXACT nuova:", res["new_exact_campaign"] or "nessun suggerimento")
    if a.csv:
        cols = ["book_id", "marketplace", "campaign_id", "campaign_type", "parent_target", "parent_target_type", "search_term", "period_id",
                "clicks", "spend", "orders", "sales", "acos", "cvr", "cpc", "estimated_profit", "opportunity_type", "confidence", "reason",
                "already_in_exact", "already_targeted_elsewhere", "operationally_actionable", "manual_review_required", "windows_agreement", "flags"]
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in recs:
                w.writerow({**r, "flags": ";".join(r["flags"])})
        print("CSV:", a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
