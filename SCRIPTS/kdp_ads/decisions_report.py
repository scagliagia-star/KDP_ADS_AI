"""Output del Decision Layer: CSV decisionale, sezione Markdown, salvataggio DB. File con timestamp, mai sovrascritti."""
import csv
import json
from collections import Counter
from pathlib import Path

from .decfields import DEC_COLUMNS
from .metrics import set_currency, sym
from .report import _cell, _path, _table

INSERT_MARK = "## 2. WINNERS"


def write_decisions_csv(ds, out_dir, ts) -> Path:
    p = _path(out_dir, "decisions", ds.ctx.file_tag or ds.ctx.book_id, ts, "csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DEC_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for d in ds.decisions:
            row = {k: ("" if d.get(k) is None else d[k]) for k in DEC_COLUMNS}
            for k, pre in (("lifetime_acos", "lifetime"), ("recent_acos", "recent"), ("tactical_acos", "tactical")):
                if d.get(k) is None and d[f"{pre}_clicks"] is not None:
                    row[k] = "N/A_NO_SALES"          # mai 0
                elif isinstance(d.get(k), float):
                    row[k] = round(d[k], 2)
            for k in DEC_COLUMNS:
                if k.endswith("_per_day") and isinstance(d.get(k), float):
                    row[k] = round(d[k], 3)
            for k in ("lifetime_estimated_profit", "recent_estimated_profit", "tactical_estimated_profit", "lifetime_spend", "recent_spend", "tactical_spend", "tactical_sales",
                      "lifetime_sales", "recent_sales"):
                if isinstance(row[k], float):
                    row[k] = round(row[k], 2)
            row["manual_approval_required"] = "true"
            row["operationally_actionable"] = "true" if d.get("operationally_actionable") else "false"
            w.writerow(row)
    return p


def save_decisions_to_db(conn, ds, csv_path, md_path) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO decision_runs (book_id,generated_at,analysis_run_id,csv_path,md_path,decision_count,accounting_json,warnings_json,marketplace) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ds.ctx.book_id, ds.generated_at, ds.analysis_run_id, str(csv_path), str(md_path), len(ds.decisions),
             json.dumps(ds.accounting), json.dumps(ds.all_warnings(), ensure_ascii=False), ds.ctx.marketplace))
        run_id = cur.lastrowid
        sql = f"INSERT INTO decisions ({','.join(DEC_COLUMNS)}) VALUES ({','.join('?' * len(DEC_COLUMNS))})"
        for d in ds.decisions:
            d["run_id"] = run_id
            conn.execute(sql, [d.get(k) for k in DEC_COLUMNS])
    return run_id


def _acos(v):
    return "N/A" if v is None else f"{v:.1f}%"


def _pd(d, pre):
    v = [d.get(f"{pre}_{k}_per_day") for k in ("impressions", "clicks", "spend", "orders")]
    if all(x is None for x in v):
        return " · al giorno: n/d"
    f = lambda x, n=2: "n/d" if x is None else f"{x:.{n}f}"
    return f" · al giorno: impr {f(v[0], 1)}, clic {f(v[1])}, spend {sym()}{f(v[2])}, ordini {f(v[3])}"


def _ev(d, pre):
    if d[f"{pre}_clicks"] is None:
        return "n/d"
    return (f"{d[f'{pre}_clicks']:g} clic, {d[f'{pre}_orders']:g} ordini, spend {sym()}{d[f'{pre}_spend']:.2f}, "
            f"sales {sym()}{d[f'{pre}_sales']:.2f}, ACOS {_acos(d[f'{pre}_acos'])}, profit {sym()}{d[f'{pre}_estimated_profit']:.2f}" + _pd(d, pre))


def _block(d):
    pct = "" if d["bid_change_pct"] is None else f" ({d['bid_change_pct']:+.0f}%)"
    tgt = d["target"] if not d["search_term"] else f"{d['target']} → {d['search_term']}"
    g = f" · growth: **{d['growth_opportunity']}**" if d["growth_opportunity"] else ""
    L = [f"#### {d['priority']} · {d['final_action']}{pct} — {d['campaign_id']} · `{tgt}`{g}",
         f"_{d['decision_id']} · {d['action_key']} · bid attuale {d['current_bid'] if d['current_bid'] is not None else 'n/d'} · "
         f"status **{d['status']}** · approvazione manuale richiesta_\n",
         f"- **LIFETIME** ({d['lifetime_action'] or 'n/d'}): {_ev(d, 'lifetime')}",
         f"- **{d['recent_window'] or 'RECENT (contesto)'}** {d['recent_period'] or ''} ({d['recent_action'] or 'n/d'}): {_ev(d, 'recent')} · signal {d['recent_vs_historical_signal']}",
         f"- **{d['tactical_window'] or '28D / latest comparable period'}** {d['tactical_period'] or ''} "
         f"({'comparabile' if d['comparable_period'] else 'NON comparabile/assente'}; latest_period_action {d['latest_period_action'] or 'n/d'}): {_ev(d, 'tactical')}",
         f"- **Stato target**: {d['target_status']} · azionabile operativamente: **{'si' if d['operationally_actionable'] else 'no'}**"
         + (f" · azione sottostante se riattivato: **{d['underlying_candidate_action']}**" if d["underlying_candidate_action"] else ""),
         f"- **Delivery**: {d['delivery_signal']} · source **{d['delivery_source']}**"
         + (f" (x{d['delivery_ratio']:.2f})" if d["delivery_ratio"] is not None else "")
         + (" — _delivery signal inherited from campaign_" if d["delivery_source"] == "CAMPAIGN_FALLBACK" else "")
         + f" · campagna: {d['campaign_delivery_signal']}",
         f"- **Historical context**: {d['historical_context'] or 'n/d'}",
         f"- **Cross campaign**: {d['cross_campaign_overlap'] or 'nessun overlap'}"
         + (f" · migliore: {d['better_campaign_if_any']}" if d["better_campaign_if_any"] else ""),
         f"- **Amazon AI**: {d['amazon_recommendation'] or 'n/d'}"
         + (f" · accordo **{d['recommendation_agreement']}**" if d["recommendation_agreement"] else "")
         + (f"\n  - _{d['amazon_agreement_note']}_" if d["amazon_agreement_note"] else ""),
         f"- **Final recommendation**: {d['final_action']}{pct} · confidence **{d['confidence']}** · regola `{d['decision_rule']}`"
         + (f" · flag {d['flags']}" if d["flags"] else ""),
         f"- **Reason**: {d['reason']}"]
    if d["child_terms_note"]:
        L.append(f"- _Contesto_: {d['child_terms_note']}")
    return "\n".join(L) + "\n"


def decisions_markdown(ds) -> str:
    D, acc = ds.decisions, ds.accounting
    set_currency(ds.ctx.currency)
    by_p, by_a = Counter(d["priority"] for d in D), Counter(d["final_action"] for d in D)
    A = []
    A.append("## 1b. CONSOLIDATED DECISIONS\n")
    A.append("_Decision Layer: consolida le evidenze analitiche (Target lifetime, Search Term recente, cross-campaign, Amazon) in UNA "
             "decisione per target. Le raccomandazioni analitiche restano invariate come evidenza. **Tutte le decisioni sono PROPOSED e "
             "richiedono approvazione manuale; nulla e' stato eseguito.**_\n")
    A.append(f"- Raccomandazioni analitiche: **{acc['analytic_recommendations']}** → decisioni consolidate: **{len(D)}**. "
             f"Contabilita' evidenze: {acc['lifetime_target_rows_used']} Target lifetime + {acc.get('target_window_rows_used', 0)} Target di finestre recenti + {acc['recent_rows_matching_target']} Search Term "
             f"recenti con termine = target + {acc['child_search_term_rows_attached_as_context']} termini figli (solo contesto) + "
             f"{acc['negative_candidate_rows']} negative candidate = {acc['evidence_rows_total']}.")
    A.append("- Priorita': " + ", ".join(f"{p}: {by_p.get(p, 0)}" for p in ("P1", "P2", "P3", "P4")) + ".")
    A.append("- Azioni finali: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_a.items())) + ".")
    A.append(f"- Growth opportunity (BID_UP_5_OPPORTUNITY, azione resta HOLD): {sum(1 for d in D if d['growth_opportunity'])}.\n")
    A.append("**Logica di consolidamento** (prudente, non additiva): lifetime e recente concordi → si applica UNA volta il passo maggiore "
             "(due BID_DOWN_10 restano −10%, non −20%); lifetime HOLD + recente in bid-down → deterioramento recente, massimo −5% (−10% solo "
             "se recente HIGH e DECLINING) con confidence al massimo MEDIUM; il cross-campaign conferma ma non somma passi; ordini lifetime ≥ 10 → "
             "mai pausa, massimo −15%; periodo tattico = ultimo periodo comparabile (Target e Search Term con le stesse date), 65 giorni = contesto "
             "recente, lifetime = contesto storico (un buon storico non nasconde un peggioramento recente, un cattivo storico non forza un bid-down se il periodo "
             "tattico e' sano); ACOS alto + delivery per-giorno crollata → REVIEW_DELIVERY_BEFORE_BID_CHANGE invece di un ulteriore bid-down; 1–4 ordini con perdita ≥ 2× royalty → bid-down (max −10%) con confidence LOW; "
             "winner → HOLD + growth opportunity. Amazon e' solo audit e non modifica la decisione.\n")
    for p in ("P1", "P2", "P3"):
        rows = [d for d in D if d["priority"] == p]
        A.append(f"### {p} ({len(rows)})\n")
        A.extend(_block(d) for d in rows) if rows else A.append("_nessuna decisione_\n")
    rows = [d for d in D if d["priority"] == "P4"]
    A.append(f"### P4 ({len(rows)})\n")
    A.append("_Watch / hold / dati insufficienti / opportunita' minori. Vista compatta; dettaglio completo e reason nel CSV._\n")
    head = ["Campaign", "Target", "Stato", "Lifetime", "Recent", "Tactical", "Delivery", "Src", "Cross", "Amazon", "Final", "Conf", "Growth"]
    trs = []
    for d in rows:
        life = "n/d" if d["lifetime_clicks"] is None else f"{d['lifetime_orders']:g}o/{d['lifetime_clicks']:g}c/{_acos(d['lifetime_acos'])}"
        rec = "n/d" if d["recent_clicks"] is None else f"{d['recent_orders']:g}o/{d['recent_clicks']:g}c/{_acos(d['recent_acos'])}"
        tac = "n/d" if d["tactical_clicks"] is None else f"{d['tactical_orders']:g}o/{d['tactical_clicks']:g}c/{_acos(d['tactical_acos'])}"
        trs.append([d["campaign_id"].replace("CHAIR_TAI_CHI_", ""), d["target"], d["target_status"], life, rec, tac,
                    (d["delivery_signal"] or "").replace("DELIVERY_", ""), {"TARGET": "T", "CAMPAIGN_FALLBACK": "C*"}.get(d["delivery_source"], "?"),
                    d["cross_campaign_overlap"] or "",
                    d["recommendation_agreement"] or "", d["final_action"], d["confidence"], d["growth_opportunity"] or ""])
    A.append(_table(head, trs))

    if rows:
        A.append("_Src: T = delivery del singolo target; C* = delivery signal inherited from campaign (fallback, non e' prova sul target)._\n")
    paused = [d for d in D if d["target_status"] == "PAUSED"]
    A.append(f"### PAUSED TARGETS WITH UNDERLYING ACTION ({sum(1 for d in paused if d['underlying_candidate_action'])} di {len(paused)} target in pausa)\n")
    A.append("_Target IN PAUSA: il bid non ha effetto operativo, quindi nessun bid-down/review come azione finale (HOLD_PAUSED_TARGET); "
             "l'azione economica sottostante e' conservata. Nulla viene riattivato._\n")

    def _ptr(d):
        life = "n/d" if d["lifetime_clicks"] is None else f"{d['lifetime_clicks']:g}c/{d['lifetime_orders']:g}o/{sym()}{d['lifetime_spend']:.2f}/ACOS {_acos(d['lifetime_acos'])}"
        rec = []
        if d["tactical_clicks"] is not None:
            rec.append(f"28D {d['tactical_clicks']:g}c/{d['tactical_orders']:g}o/{sym()}{d['tactical_spend']:.2f}")
        if d["recent_clicks"] is not None:
            rec.append(f"{d['recent_window'] or 'recente'} {d['recent_clicks']:g}c/{d['recent_orders']:g}o/{sym()}{d['recent_spend']:.2f}")
        return [d["campaign_id"].replace("CHAIR_TAI_CHI_", ""), d["target"], d["target_status"], d["underlying_candidate_action"] or "",
                life, " / ".join(rec) or "n/d", d["final_action"],
                "RECENT_SPEND_ON_PAUSED_TARGET" if "RECENT_SPEND_ON_PAUSED_TARGET" in (d["flags"] or "") else "", d["reason"] or ""]
    ptr = [_ptr(d) for d in paused if d["underlying_candidate_action"] or "RECENT_SPEND_ON_PAUSED_TARGET" in (d["flags"] or "")]
    A.append(_table(["Campaign", "Target", "Stato", "Underlying", "Lifetime", "Recente", "Final", "Flag", "Reason"], ptr)
             if ptr else "_nessun target in pausa con azione sottostante o spesa recente_\n")

    A.append("### Amazon AI — evidenze registrate a mano\n")
    A.append("_Fonte: registrazione manuale (nessuno scraping). Periodo Amazon e periodi dei nostri dati NON coincidono._\n")
    agree = Counter(d["recommendation_agreement"] for d in D if d["recommendation_agreement"])
    A.append("- Accordo Amazon vs decisione: " + (", ".join(f"{k}: {v}" for k, v in sorted(agree.items())) or "nessuna evidenza Amazon abbinata") + ".")
    for a in ds.amazon_campaign_level:
        sp = "" if a["observed_spend"] is None else f" spesa {sym()}{a['observed_spend']:.2f}"
        oo = "" if a["observed_orders"] is None else f", {a['observed_orders']:g} acquisti"
        A.append(f"- Livello campagna {a['campaign_id']} [{a['recommendation_type']}/{a['suggested_direction']}] "
                 f"{a['period_from']}→{a['period_to']}:{sp}{oo} — {a['observation'] or ''} {a['suggested_action'] or ''} {a['notes'] or ''}".rstrip())
    for note in ds.amazon_campaign_notes:
        A.append(f"- {note}")
    for a in ds.amazon_unmatched:
        A.append(f"- **NON abbinato** a un target: {a['campaign_id']} `{a['target']}` ({a['recommendation_type']})")
    A.append("")
    if ds.analysis_warnings:
        A.append(f"### Warning ereditati dall'analisi (run {ds.analysis_run_id})\n")
        A.extend(f"- {w}" for w in ds.analysis_warnings)
        A.append("")
    if ds.warnings:
        A.append("### Decision layer — warning\n")
        A.extend(f"- {w}" for w in ds.warnings)
        A.append("")
    return "\n".join(A) + "\n"


def write_combined_markdown(ds, out_dir, ts, analysis_md_path: str | None) -> Path:
    """Copia del report analitico con la sezione CONSOLIDATED DECISIONS inserita prima di '2. WINNERS'.
    Il report analitico originale non viene modificato."""
    section = decisions_markdown(ds)
    base = None
    if analysis_md_path and Path(analysis_md_path).exists():
        base = Path(analysis_md_path).read_text(encoding="utf-8")
    p = _path(out_dir, "analysis", ds.ctx.file_tag or ds.ctx.book_id, ts + "_with_decisions", "md")
    if base and INSERT_MARK in base:
        text = base.replace(INSERT_MARK, section + "\n" + INSERT_MARK, 1)
    else:
        text = f"# KDP Ads decisions — {ds.ctx.title}\n\n" + section
    p.write_text(text, encoding="utf-8")
    return p
