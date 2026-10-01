"""Orchestrazione dell'analisi per libro: DB -> metriche -> regole -> raccomandazioni. Sola lettura dei dati locali."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from . import registry
from .audit import audit_book, duplicate_key_warnings
from .crosscampaign import analyze_overlaps_by_period, build_signals
from .metrics import ESTIMATE_NOTE, compute_metrics, normalize_term, target_type, term_matches_target
from .rules import CONF_ORDER, BookCtx, ctx_from_book, decide, load_rules, priority
from .snapshots import SAME_CONTENT_DIFFERENT_PERIOD, SAME_CONTENT_MESSAGE, days_inclusive, period_id as make_period_id, per_day, window_label, window_role


@dataclass
class Analysis:
    ctx: BookCtx
    generated_at: str
    recs: list = field(default_factory=list)
    overlaps: list = field(default_factory=list)
    audit: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    campaigns: dict = field(default_factory=dict)


def _load(conn, table, book_id, marketplace=None):
    """Righe di un libro; con `marketplace` SOLO quelle di quel marketplace (mai mescolare marketplace diversi)."""
    sql, args = f"SELECT * FROM {table} WHERE book_id=?", [book_id]
    if marketplace:
        sql, args = sql + " AND UPPER(marketplace)=?", args + [str(marketplace).upper()]
    return [dict(r) for r in conn.execute(sql + " ORDER BY campaign_id, id", args)]


def same_content_warnings(conn, book_id, marketplace) -> list:
    """SAME_CONTENT_DIFFERENT_PERIOD: stesso hash per stesso libro/marketplace/campagna/tipo report ma periodi diversi. Calcolato dagli
    import presenti nel DB (vale anche per import fatti prima e per qualunque ordine di caricamento). Non blocca nulla."""
    out = []
    for r in conn.execute("SELECT campaign_id, report_type, file_sha256, GROUP_CONCAT(import_id) ids, GROUP_CONCAT(period_id) pids "
                          "FROM imports WHERE book_id=? AND COALESCE(UPPER(marketplace), ?)=? "
                          "GROUP BY campaign_id, report_type, file_sha256 HAVING COUNT(DISTINCT period_id) > 1 "
                          "ORDER BY campaign_id, report_type", (book_id, str(marketplace).upper(), str(marketplace).upper())):
        out.append(f"DATA_WARNING {SAME_CONTENT_DIFFERENT_PERIOD} {r['campaign_id']} {r['report_type']}: {SAME_CONTENT_MESSAGE} "
                   f"(import {r['ids']}; periodi {r['pids']})")
    return out


def analyze_book(conn, settings: dict, mapping: dict, rules_path, book_id: str, logger=None, marketplace=None) -> Analysis:
    """Analizza UN marketplace del libro. `marketplace` puo' mancare solo se il libro ne ha uno."""
    books = registry.load_books(settings["books_path"])
    if book_id not in books:
        raise registry.RegistryError(f"book_id {book_id} non presente in books.json")
    full = books[book_id]
    book = registry.book_view(full, marketplace)                       # economia (prezzo, royalty, ACOS) del marketplace corretto
    errs = registry.validate_book(book)
    if errs:
        raise registry.RegistryError(f"libro {book_id} ({book['marketplace']}) non valido: {'; '.join(errs)}")
    eco, book_warns = registry.book_economics(book, settings.get("break_even_mismatch_tolerance_pp", 0.5))
    ctx = ctx_from_book(book, eco, load_rules(rules_path), registry.file_tag(full, book["marketplace"]))
    now = datetime.now().isoformat(timespec="seconds")
    an = Analysis(ctx=ctx, generated_at=now)
    an.warnings += [f"DATA_WARNING {w}" for w in book_warns]

    mp = ctx.marketplace
    trows, srows = _load(conn, "target_rows", book_id, mp), _load(conn, "search_term_rows", book_id, mp)
    snap_cfg = ctx.rules["snapshots"]
    comparable = defaultdict(set)      # campagna -> {(date_from, date_to)} presenti sia come Target che come Search Term
    tp = {(r["campaign_id"], r["date_from"], r["date_to"]) for r in trows if r["report_scope"] == "DATE_RANGE"}
    for r in srows:
        if r["report_scope"] == "DATE_RANGE" and (r["campaign_id"], r["date_from"], r["date_to"]) in tp:
            comparable[r["campaign_id"]].add((r["date_from"], r["date_to"]))
    for r in trows + srows:
        r["_m"] = compute_metrics(r["impressions"], r["clicks"], r["spend"], r["orders"], r["sales"], ctx.royalty)
        r["target_type"] = target_type(r["target"])
        r["period_id"] = r.get("period_id") or make_period_id(r["report_scope"], r["date_from"], r["date_to"])
        r["_days"] = days_inclusive(r["date_from"], r["date_to"]) if r["report_scope"] == "DATE_RANGE" else None
        r["_comparable"] = int(r["report_scope"] == "DATE_RANGE" and (r["date_from"], r["date_to"]) in comparable[r["campaign_id"]])

    an.audit = audit_book(conn, book_id, ctx.rules, mapping, ctx.royalty, mp)
    an.warnings += same_content_warnings(conn, book_id, mp)
    an.warnings += an.audit["warnings"] + duplicate_key_warnings(trows, srows)
    for r in trows + srows:
        if r["orders"] and not r["sales"]:
            an.warnings.append(f"DATA_WARNING ordini > 0 con vendite = 0 ({r['campaign_id']}, {r['target']!r})")

    # CVR medio per (campagna, tipo report): riferimento per rilevare conversion rate anomali
    agg = defaultdict(lambda: [0.0, 0.0])
    for r, rt in [(x, "T") for x in trows] + [(x, "S") for x in srows]:
        a = agg[(r["campaign_id"], rt, r["period_id"])]
        a[0] += r["orders"] or 0
        a[1] += r["clicks"] or 0
    avg_cvr = {k: (v[0] / v[1] * 100 if v[1] else None) for k, v in agg.items()}

    signals = build_signals(trows, srows, ctx)
    an.overlaps = analyze_overlaps_by_period(srows, ctx)
    ov_by_term = {(o["period_id"], o["search_term"]): o for o in an.overlaps}

    def ov_note(term, pid):
        o = ov_by_term.get((pid, normalize_term(term)))
        if not o:
            return ""
        others = ", ".join(f"{c['campaign_id']}({c['campaign_type']}): {c['m'].clicks:g}clk/{c['m'].orders:g}ord/ACOS "
                           f"{'N/A' if c['m'].acos is None else format(c['m'].acos, '.1f') + '%'}" for c in o["campaigns"])
        return f"CROSS_CAMPAIGN_OVERLAP {pid} [{o['classification']}] {others}"

    for kind, rows in (("T", trows), ("S", srows)):
        for r in rows:
            rt = "TARGETING" if kind == "T" else "SEARCH_TERM"
            scope = "TARGET" if kind == "T" or term_matches_target(r["search_term"], r["target"]) else "PARENT_TARGET"
            m = r["_m"]
            d = decide(m, ctx, rt, scope, avg_cvr.get((r["campaign_id"], kind, r["period_id"])))
            sig, sig_detail = signals.get((kind, r["id"]), ("UNKNOWN", "nessun confronto verificabile"))
            rec = {
                "run_id": None, "generated_at": now, "book_id": ctx.book_id, "marketplace": ctx.marketplace, "title": ctx.title,
                "campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"], "campaign_type": r["campaign_type"],
                "report_type": rt, "report_scope": r["report_scope"], "date_from": r["date_from"], "date_to": r["date_to"],
                "target": r["target"], "search_term": r.get("search_term"), "match_type": r["match_type"],
                "added_as": r["added_as"], "target_type": r["target_type"], "action_scope": scope,
                "period_id": r["period_id"], "window_id": window_label(r["report_scope"], r["date_from"], r["date_to"]),
                "window_role": window_role(r["report_scope"], r["date_from"], r["date_to"], snap_cfg),
                "window_days": r["_days"], "comparable_period": r["_comparable"],
                "impressions_per_day": per_day(m.impressions, r["_days"]), "clicks_per_day": per_day(m.clicks, r["_days"]),
                "spend_per_day": per_day(m.spend, r["_days"]), "orders_per_day": per_day(m.orders, r["_days"]),
                "impressions": m.impressions, "clicks": m.clicks, "ctr": m.ctr, "spend": m.spend, "cpc": m.cpc,
                "orders": m.orders, "sales": m.sales, "conversion_rate": m.cvr, "acos": m.acos, "roas": m.roas,
                "price": ctx.price, "royalty_per_copy": ctx.royalty, "target_acos": ctx.target_acos,
                "break_even_acos": ctx.break_even_acos,
                "estimated_royalty": m.est_royalty, "estimated_direct_ad_profit": m.est_profit,
                "estimated_profit_per_order": m.profit_per_order, "estimated_profit_note": "ESTIMATE - see methodology",
                "confidence": d.confidence, "economic_classification": d.economic, "sample_classification": d.sample,
                "rule_classification": d.rule_classification, "recent_vs_historical_signal": sig, "signal_detail": sig_detail,
                "recommended_action": d.action, "recommended_bid_change_pct": d.bid_change_pct,
                "priority": priority(d.action, m, d.confidence, ctx), "flags": ";".join(d.flags),
                "cross_campaign_note": ov_note(r.get("search_term"), r["period_id"]) if kind == "S" else "",
                "reason": d.reason,
                "amazon_recommendation": None, "amazon_recommendation_period": None,
                "amazon_recommendation_source": None, "recommendation_agreement": None,
                "_loss": m.est_loss,
            }
            an.recs.append(rec)

    an.recs.sort(key=lambda x: (x["priority"], -x["_loss"], -x["spend"]))
    for c in {r["campaign_id"]: r for r in trows + srows}.values():
        an.campaigns[c["campaign_id"]] = (c["campaign_name"], c["campaign_type"])
    if logger:
        logger.info("Analisi libro %s: %d righe (%d target, %d search term), %d overlap, %d warning",
                    book_id, len(an.recs), len(trows), len(srows), len(an.overlaps), len(an.warnings))
    return an
