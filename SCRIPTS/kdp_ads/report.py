"""Output: CSV raccomandazioni, CSV overlap, report Markdown, salvataggio nel DB. File sempre con timestamp (mai sovrascritti)."""
import csv
import json
from collections import Counter
from pathlib import Path

from .metrics import ESTIMATE_NOTE, set_currency, sym
from .recfields import REC_COLUMNS

NON_ACTIONS = ("NO_ACTION", "HOLD", "WATCH", "INSUFFICIENT_DATA")
SECTIONS = [
    ("2. WINNERS", ("WINNER", "PROMISING_WINNER")),
    ("3. PROFITABLE / HOLD", ("ON_TARGET_HOLD", "LOW_SAMPLE_HOLD", "PROMISING_LOW_SAMPLE")),
    ("4. ABOVE TARGET BUT PROFITABLE", ("PROFITABLE_HOLD",)),
    ("5. HIGH-VOLUME INEFFICIENT", ("HIGH_VOLUME_INEFFICIENT",)),
    ("6. LOSERS", ("LOSER",)),
    ("7. ZERO-ORDER WASTE", ("ZERO_ORDER_WASTE",)),
    ("8. WATCHLIST", ("WATCH", "LOW_SAMPLE_WATCH", "DATA_ANOMALY")),
    ("9. INSUFFICIENT DATA", ("INSUFFICIENT_DATA",)),
]


def _path(out_dir, prefix, book_id, ts, ext):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{prefix}_{book_id}_{ts}.{ext}"
    n = 1
    while p.exists():
        p = out_dir / f"{prefix}_{book_id}_{ts}_{n}.{ext}"
        n += 1
    return p


def _fmt(v, nd=2):
    if v is None:
        return ""
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


# ------------------------------------------------------------------ CSV
def write_recommendations_csv(an, out_dir, ts) -> Path:
    p = _path(out_dir, "recommendations", an.ctx.file_tag or an.ctx.book_id, ts, "csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=REC_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in an.recs:
            row = {k: ("" if r.get(k) is None else r[k]) for k in REC_COLUMNS}
            row["acos"] = "N/A_NO_SALES" if r["acos"] is None else round(r["acos"], 2)   # mai 0
            for k in ("ctr", "cpc", "conversion_rate", "roas", "estimated_direct_ad_profit", "estimated_royalty",
                      "estimated_profit_per_order"):
                if isinstance(row[k], float):
                    row[k] = round(row[k], 4 if k in ("ctr", "cpc", "roas") else 2)
            w.writerow(row)
    return p


def write_overlaps_csv(an, out_dir, ts) -> Path:
    p = _path(out_dir, "cross_campaign_overlaps", an.ctx.file_tag or an.ctx.book_id, ts, "csv")
    cols = ["book_id", "period_id", "search_term", "overlap_classification", "possible_inefficiency", "better_campaign",
            "worse_campaign", "recommendation", "detail", "campaign_id", "campaign_type", "clicks", "cpc", "orders",
            "conversion_rate", "spend", "sales", "acos", "estimated_direct_ad_profit"]
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for o in an.overlaps:
            for c in o["campaigns"]:
                m = c["m"]
                w.writerow({"book_id": o["book_id"], "period_id": o.get("period_id"), "search_term": o["search_term"],
                            "overlap_classification": o["classification"],
                            "possible_inefficiency": o["possible_inefficiency"],
                            "better_campaign": o["better"]["campaign_id"] if o["better"] else "",
                            "worse_campaign": o["worse"]["campaign_id"] if o["worse"] else "",
                            "recommendation": o["recommendation"], "detail": o["detail"],
                            "campaign_id": c["campaign_id"], "campaign_type": c["campaign_type"], "clicks": m.clicks,
                            "cpc": _fmt(m.cpc, 4), "orders": m.orders, "conversion_rate": _fmt(m.cvr),
                            "spend": _fmt(m.spend), "sales": _fmt(m.sales),
                            "acos": "N/A_NO_SALES" if m.acos is None else _fmt(m.acos),
                            "estimated_direct_ad_profit": _fmt(m.est_profit)})
    return p


# ------------------------------------------------------------------ DB
def save_to_db(conn, an, csv_path, md_path, ov_path, rules) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO analysis_runs (book_id,generated_at,csv_path,md_path,overlaps_csv_path,row_count,warnings_json,rules_json,marketplace) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (an.ctx.book_id, an.generated_at, str(csv_path), str(md_path), str(ov_path), len(an.recs),
             json.dumps(an.warnings, ensure_ascii=False), json.dumps(rules, ensure_ascii=False), an.ctx.marketplace))
        run_id = cur.lastrowid
        sql = f"INSERT INTO recommendations ({','.join(REC_COLUMNS)}) VALUES ({','.join('?' * len(REC_COLUMNS))})"
        for r in an.recs:
            r["run_id"] = run_id
            conn.execute(sql, [r.get(k) for k in REC_COLUMNS])
    return run_id


# ------------------------------------------------------------------ Markdown
def _cell(x, n=48):
    s = "" if x is None else str(x).replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def _table(headers, rows):
    if not rows:
        return "_nessuna riga_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c, 60) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _acos(v):
    return "N/A (NO_SALES)" if v is None else f"{v:.1f}%"


def _row(r):
    return [f"{r['campaign_id']} ({r['campaign_type']})", r["target"], r["search_term"] or "",
            f"{r['clicks']:g}", f"{r['orders']:g}", f"{r['spend']:.2f}", f"{r['sales']:.2f}", _acos(r["acos"]),
            f"{r['estimated_direct_ad_profit']:.2f}", r["confidence"], r["recent_vs_historical_signal"],
            r["recommended_action"], r["priority"]]


def _row_head():
    s = sym().strip()
    return ["Campaign", "Target", "Search term", "Clicks", "Orders", f"Spend {s}", f"Sales {s}", "ACOS", f"Est. profit {s}",
            "Conf", "Signal", "Action", "P"]


def _totals_line(t, ctx):
    acos = f"{t['acos']:.1f}%" if t["acos"] is not None else "N/A (NO_SALES)"
    roas = f"{t['roas']:.2f}" if t["roas"] is not None else "N/A"
    return [f"{t['impressions']:,.0f}", f"{t['clicks']:,.0f}", f"{t['spend']:,.2f}", f"{t['orders']:,.0f}",
            f"{t['sales']:,.2f}", acos, roas, f"{t['est_royalty']:,.2f}", f"{t['est_profit']:,.2f}"]


def write_markdown(an, out_dir, ts) -> Path:
    ctx, recs = an.ctx, an.recs
    set_currency(ctx.currency)
    p = _path(out_dir, "analysis", ctx.file_tag or ctx.book_id, ts, "md")
    L = []
    A = L.append
    A(f"# KDP Ads analysis — {ctx.title}\n")
    A(f"_Generato: {an.generated_at} · **READ-ONLY**: analisi di dati locali, nessuna modifica su Amazon Ads. "
      "Tutte le azioni sono raccomandazioni (candidate) da valutare manualmente._\n")
    A("## BOOK SUMMARY\n")
    A(f"- **Title:** {ctx.title}\n- **Book ID:** {ctx.book_id}\n- **Marketplace / currency:** {ctx.marketplace} / {ctx.currency}\n"
      f"- **Phase:** {ctx.phase}\n- **Price:** {ctx.price:.2f} {ctx.currency}\n- **Royalty per copy:** {ctx.royalty:.2f} {ctx.currency}\n"
      f"- **Target ACOS:** {ctx.target_acos:g}% (obiettivo operativo)\n- **Break-even ACOS:** {ctx.break_even_acos:g}% (limite economico teorico)\n")

    cs = sym().strip()
    heads = ["Campaign", "Impr.", "Clicks", f"Spend {cs}", "Orders", f"Sales {cs}", "ACOS", "ROAS", f"Est. royalty {cs}", f"Est. direct ad profit {cs}"]
    A("## CAMPAIGN SUMMARY\n")
    A("> **I due dataset NON sono periodi equivalenti e NON vengono sommati tra loro.** Il Targeting e' LIFETIME; il Search Term "
      "copre solo un intervallo recente e ne e' (per costruzione) un sottoinsieme.\n")
    A("> Ogni **snapshot** (report + intervallo) e' un dataset a se': snapshot con finestre diverse coesistono e non vengono mai sommati.\n")
    pd_heads = heads + ["Impr./day", "Clicks/day", f"Spend/day {cs}", "Orders/day"]

    def _pd(t, k, nd=2):
        return "n/d" if t.get(k) is None else f"{t[k]:.{nd}f}"
    for rt, title in (("TARGETING", "TARGET DATA"), ("SEARCH_TERM", "SEARCH TERM DATA")):
        snaps = {}
        for t in an.audit["totals"]:
            if t["report_type"] == rt:
                snaps.setdefault((t["report_scope"], t["date_from"], t["date_to"]), []).append(t)
        if not snaps:
            A(f"### {title}\n\n_nessuno snapshot_\n")
        for (scope, d_from, d_to), ts_ in sorted(snaps.items(), key=lambda kv: (kv[0][0] != "LIFETIME", str(kv[0][2]), str(kv[0][1]))):
            label = "LIFETIME" if scope == "LIFETIME" else "RECENT"
            A(f"### {label} {title} — {ts_[0]['period_id']} ({ts_[0]['window_id']})\n")
            A(f"_Scope: {scope} · date {d_from or '—'} → {d_to or '—'}_\n")
            rows = [[f"{t['campaign_id']} ({t['campaign_type']})"] + _totals_line(t, ctx)
                    + [_pd(t, "impressions_per_day", 1), _pd(t, "clicks_per_day", 1), _pd(t, "spend_per_day"), _pd(t, "orders_per_day")]
                    for t in ts_]
            tot = {k: sum(t[k] for t in ts_) for k in ("impressions", "clicks", "spend", "orders", "sales", "est_royalty", "est_profit")}
            tot["acos"] = tot["spend"] / tot["sales"] * 100 if tot["sales"] else None
            tot["roas"] = tot["sales"] / tot["spend"] if tot["spend"] else None
            rows.append(["**Totale (solo questo snapshot)**"] + _totals_line(tot, ctx) + ["", "", "", ""])
            A(_table(pd_heads, rows))
    A(f"> {ESTIMATE_NOTE}\n> `estimated_direct_ad_profit = orders × royalty_per_copy − spend`.\n")

    # 1. Executive summary
    A("## 1. EXECUTIVE SUMMARY\n")
    by_action, by_prio = Counter(r["recommended_action"] for r in recs), Counter(r["priority"] for r in recs)
    A(f"- Righe analizzate: **{len(recs)}** (Target {sum(r['report_type'] == 'TARGETING' for r in recs)}, "
      f"Search Term {sum(r['report_type'] == 'SEARCH_TERM' for r in recs)}).")
    A("- Priorita': " + ", ".join(f"{k}: {by_prio.get(k, 0)}" for k in ("P1", "P2", "P3", "P4")) + ".")
    A("- Azioni: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_action.items())) + ".")
    A(f"- Overlap cross-campaign: {len(an.overlaps)} termini in >=2 campagne, "
      f"{sum(o['possible_inefficiency'] for o in an.overlaps)} con possibile inefficienza.")
    A(f"- Data warnings: {len(an.warnings)}.\n")
    acts = [r for r in recs if r["recommended_action"] not in NON_ACTIONS and r["priority"] in ("P1", "P2")]
    if acts:
        A("**Azioni a priorita' P1/P2 (candidate, da valutare manualmente):**\n")
        for r in acts[:10]:
            A(f"- **{r['priority']} {r['recommended_action']}** — {r['campaign_id']} · {r['target'] or ''}"
              f"{' → ' + r['search_term'] if r['search_term'] else ''}: spend {sym()}{r['spend']:.2f}, {r['orders']:g} ordini, "
              f"ACOS {_acos(r['acos'])}, est. profit {sym()}{r['estimated_direct_ad_profit']:.2f}, conf {r['confidence']}")
        A("")

    for title, classes in SECTIONS:
        rows = [r for r in recs if r["rule_classification"] in classes]
        A(f"## {title} ({len(rows)})\n")
        A(_table(_row_head(), [_row(r) for r in rows]))

    prod = [r for r in recs if r["target_type"] == "PRODUCT_TARGET"]
    A(f"## 10. PRODUCT TARGETS ({len(prod)})\n")
    A("_Riferimento trasversale (le stesse righe compaiono anche nella sezione di classe). Gli ASIN restano ASIN: "
      "nessuna trasformazione in keyword._\n")
    A(_table(_row_head(), [_row(r) for r in prod]))

    A(f"## 11. CROSS-CAMPAIGN OVERLAPS ({len(an.overlaps)})\n")
    A("_Stesso search term normalizzato in >=2 campagne dello stesso libro. `CROSS_CAMPAIGN_OVERLAP` non implica cannibalizzazione ne' causalita'. "
      "Search Term = periodo recente._\n")
    for o in an.overlaps:
        A(f"### `{o['search_term']}` [{o.get('period_id')}] — {o['classification']}{' · POSSIBLE INEFFICIENCY' if o['possible_inefficiency'] else ''}\n")
        rows = [[f"{c['campaign_id']} ({c['campaign_type']})", f"{c['m'].clicks:g}",
                 _cell(c["m"].cpc and f"{c['m'].cpc:.2f}"), f"{c['m'].orders:g}",
                 _cell(c["m"].cvr is not None and f"{c['m'].cvr:.1f}%"), f"{c['m'].spend:.2f}", f"{c['m'].sales:.2f}",
                 _acos(c["m"].acos), f"{c['m'].est_profit:.2f}"] for c in o["campaigns"]]
        A(_table(["Campaign", "Clicks", f"CPC {cs}", "Orders", "CVR", f"Spend {cs}", f"Sales {cs}", "ACOS", f"Est. profit {cs}"], rows))
        if o["recommendation"]:
            A(f"**Recommendation:** {o['recommendation']}\n")
        A(f"{o['detail']}\n")

    A("## 12. RECOMMENDED ACTIONS\n")
    A("_Solo raccomandazioni: nessuna e' stata eseguita. Ordinate per priorita' e perdita stimata._\n")
    for pr in ("P1", "P2", "P3"):
        rows = [r for r in recs if r["priority"] == pr and r["recommended_action"] not in NON_ACTIONS]
        A(f"### {pr} ({len(rows)})\n")
        for r in rows:
            pct = r["recommended_bid_change_pct"]
            pct_txt = "" if pct is None else f" ({pct:+g}%)"
            st = f" → `{r['search_term']}`" if r["search_term"] else ""
            A(f"- **{r['recommended_action']}**{pct_txt} — {r['campaign_id']} · {r['report_type']} · `{r['target'] or ''}`{st} "
              f"· scope {r['action_scope']} · conf {r['confidence']}\n  {r['reason']}")
        A("")
    ov = [o for o in an.overlaps if o["possible_inefficiency"]]
    A(f"### Da cross-campaign ({len(ov)})\n")
    for o in ov:
        A(f"- `{o['search_term']}` — {o['recommendation']}\n  {o['detail']}")
    p4 = [r for r in recs if r["priority"] == "P4"]
    A(f"\n### P4 — watch / insufficient data / hold ({len(p4)})\n")
    A("_Nessuna azione richiesta; dettaglio nelle sezioni 3, 4, 8, 9 e nel CSV._\n")

    A(f"## 13. DATA WARNINGS ({len(an.warnings)})\n")
    A("\n".join(f"- {w}" for w in an.warnings) if an.warnings else "_nessun warning_")
    A("")
    A("## 14. METHODOLOGY NOTES\n")
    notes = [
        "Fonte dati: solo i CSV importati nel DB locale (`kdp_ads.db`). Nessuna chiamata di rete, nessuna modifica a Amazon Ads.",
        "Target = report LIFETIME; Search Term = intervallo recente. Non equivalenti, non sommati. Il lifetime include il periodo recente.",
        "`recent_vs_historical_signal` (IMPROVING/STABLE/DECLINING/UNKNOWN) e' un segnale DIREZIONALE, non un trend statistico ne' un confronto fra periodi indipendenti. "
        "Calcolato solo per la stessa campagna quando il termine coincide col target (stessa identita' normalizzata, per gli ASIN il prodotto corrispondente) e "
        "l'associazione e' univoca; altrimenti UNKNOWN.",
        "Normalizzazione termini: minuscolo, trim, spazi multipli -> uno. Nessuno stemming, nessuna correzione typo, nessun sinonimo, nessun riordino parole "
        "('chair tai chi' != 'tai chi chair').",
        "ACOS = spend/sales. Con sales = 0 l'ACOS e' N/A (NO_SALES), mai 0.",
        f"Target ACOS ({ctx.target_acos:g}%) = obiettivo operativo; Break-even ACOS ({ctx.break_even_acos:g}%) = limite economico teorico. "
        "Fasce: STRONG <= 30; ON_TARGET <= target; PROFITABLE_BUT_ABOVE_TARGET <= break-even; MILDLY_UNPROFITABLE <= 50; UNPROFITABLE <= 60; "
        "HIGHLY_UNPROFITABLE oltre (soglie in `CONFIG/rules.json`, override per libro in `books.json`).",
        "Confidence: `calculate_confidence()` parte dagli ordini (0-2 LOW, 3-9 MEDIUM, >=10 HIGH) e integra clic e spesa; con 0 ordini misura la solidita' "
        "del giudizio di spreco.",
        "Con 0 ordini le regole sui clic (6/10) hanno precedenza su quelle sulla spesa; con <10 clic e spesa >= 1x royalty si aggiunge il flag HIGH_CPC senza azione di bid.",
        "Con 1-4 ordini non si suggerisce mai un bid down (campione troppo piccolo): HOLD o WATCH. BID_UP_5 solo con >=3 ordini, confidence >= MEDIUM, ACOS <= 80% del target, "
        "ACOS dopo +5% ancora <= target e CVR non anomalo; mai su termini diversi dal target. Con >=10 ordini mai pausa/negative per solo ACOS, massimo -15%.",
        "Righe Search Term con termine != target ricevono `action_scope = PARENT_TARGET`: un bid change colpirebbe anche altri termini del target; "
        "NEGATIVE_CANDIDATE solo per Search Term con 0 ordini e spesa >= 2x royalty (separato da PAUSE_CANDIDATE, riservato ai Target).",
        "Priorita' P1-P4 da perdita stimata (multipli di royalty), confidence e azione, non dal solo ACOS.",
        f"**{ESTIMATE_NOTE}** `estimated_direct_ad_profit = orders × royalty − spend`.",
        "Campi `amazon_recommendation*` e `recommendation_agreement` riservati all'audit manuale futuro (per ora vuoti).",
    ]
    A("\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1)))
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p
