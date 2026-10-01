"""DECISION LAYER: consolida le evidenze analitiche (Target lifetime, Search Term recente, cross-campaign, Amazon)
in UNA decisione operativa per target. Non modifica le recommendations analitiche, non esegue nulla.

Logica di consolidamento (prudente, deterministica). In sintesi: le evidenze NON si sommano mai, si prende il
massimo passo di bid-down fra quelle concordi, e si attenua quando le evidenze discordano.
  C1 lifetime e recente concordi su un bid-down  -> passo = max dei due (mai la somma: 2x -10% resta -10%), confidence = max.
  C2 solo lifetime in bid-down (recente HOLD/assente) -> passo lifetime; se il recente e' HOLD con segnale IMPROVING il
     passo scende di un livello (minimo -5%) e la confidence e' al massimo MEDIUM ("in recupero").
  C3 solo recente in bid-down, nessun lifetime utilizzabile -> passo recente.
  C4 lifetime HOLD/WATCH + recente in bid-down (DETERIORAMENTO RECENTE) -> al massimo -5%; -10% solo se il recente ha
     confidence HIGH e segnale DECLINING; confidence finale al massimo MEDIUM (le evidenze discordano).
  C5 solo cross-campaign (campagna peggiore, termine == target, inefficienza confermata) -> -5%.
  La conferma cross-campaign NON aggiunge passi: alza di un livello la confidence (solo se gia' >= MEDIUM).
  Safety high-volume: ordini lifetime >= 10 -> mai PAUSE, massimo BID_DOWN_15.
  Low sample con ordini (1-4) e perdita stimata >= low_sample_loss_mult x royalty -> bid-down (max -10%), confidence LOW, mai PAUSE.
  Winner: il motore analitico BID_UP_5_CANDIDATE diventa HOLD + growth_opportunity=BID_UP_5_OPPORTUNITY (nessun aumento operativo).
  Amazon: solo evidenza/audit (accordo direzionale), non modifica mai la decisione.
PERIOD SNAPSHOT / RECENCY PRIORITY: l'evidenza tattica principale e' l'ULTIMO PERIODO COMPARABILE (Target e Search Term con le
stesse date); il periodo piu' lungo (es. 65 giorni) e' contesto recente; il lifetime e' contesto storico. Il lifetime non viene
ignorato ma non puo' nascondere un peggioramento recente (C4) ne' imporre un bid-down se il periodo tattico e' sano (C6).
DELIVERY SAFETY: ACOS/risultati sfavorevoli + delivery per-giorno CROLLATA (soglie in rules.json) => nessun bid-down forte,
azione REVIEW_DELIVERY_BEFORE_BID_CHANGE (la priorita' resta quella del candidato sottostante); delivery in calo => passo max -5%."""
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from . import amazon as amz
from . import registry
from .analysis import _load
from .crosscampaign import analyze_overlaps_by_period
from .metrics import Metrics, compute_metrics, normalize_term, product_asin, set_currency, sym, target_type, term_matches_target
from .rules import CONF_ORDER, ctx_from_book, load_rules, priority
from . import windows as win
from .snapshots import days_inclusive, period_id as make_period_id, window_label, window_role

STEP_OF = {"BID_DOWN_5_CANDIDATE": 5, "BID_DOWN_10_CANDIDATE": 10, "BID_DOWN_15_CANDIDATE": 15, "PAUSE_CANDIDATE": 100}
ACTION_OF = {5: "BID_DOWN_5_CANDIDATE", 10: "BID_DOWN_10_CANDIDATE", 15: "BID_DOWN_15_CANDIDATE", 100: "PAUSE_CANDIDATE"}
NON_DOWN_RANK = {"HOLD": 3, "WATCH": 2, "NO_ACTION": 1, "INSUFFICIENT_DATA": 1}
CONF_NAMES = ["LOW", "MEDIUM", "HIGH"]
GROWTH = "BID_UP_5_OPPORTUNITY"
REVIEW = "REVIEW_DELIVERY_BEFORE_BID_CHANGE"
REVIEW_STATUS = "REVIEW_TARGET_STATUS"
HOLD_PAUSED = "HOLD_PAUSED_TARGET"
# azioni che cambiano il bid o richiedono di toccare il target: senza effetto operativo se il target e' in pausa
PAUSED_BLOCKED = frozenset(list(STEP_OF) + ["BID_UP_5_CANDIDATE", "BID_UP_10_CANDIDATE", REVIEW])
OPERATIONAL_ACTIONS = frozenset(list(STEP_OF) + [REVIEW, "NEGATIVE_CANDIDATE"])
# mappatura ESPLICITA dello stato del Target report (prima colonna 'Stato'); nessun altro valore viene dedotto
TARGET_STATUS_MAP = {"ABILITATO": "ENABLED", "IN PAUSA": "PAUSED"}


def map_target_status(raw_json) -> str:
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        return "UNKNOWN"
    return TARGET_STATUS_MAP.get(str(raw.get("Stato") or "").strip().upper(), "UNKNOWN")


def delivery_source_of(eff_src: str | None) -> str:
    """TARGET = segnale calcolato sul singolo target; CAMPAIGN_FALLBACK = ereditato dalla campagna; UNKNOWN = non disponibile."""
    if not eff_src:
        return "UNKNOWN"
    return "TARGET" if eff_src.startswith("ENTITY") else "CAMPAIGN_FALLBACK"


def bump(conf: str) -> str:
    return CONF_NAMES[min(2, CONF_ORDER[conf] + 1)]


def cap(conf: str, top: str) -> str:
    return conf if CONF_ORDER[conf] <= CONF_ORDER[top] else top


def action_key(book_id: str, campaign_id: str, target: str | None, kind_override: str | None = None, term: str | None = None,
               campaign_type: str | None = None) -> str:
    """Chiave stabile: book|campaign|kw:<target normalizzato> oppure asin:<ASIN> (categorie: cat:<normalizzato>)."""
    if kind_override == "neg":
        return f"{book_id}|{campaign_id}|negterm:{normalize_term(term)}"
    a = product_asin(target)
    if a:
        return f"{book_id}|{campaign_id}|asin:{a.upper()}"
    if target_type(target) == "PRODUCT_TARGET":
        return f"{book_id}|{campaign_id}|cat:{normalize_term(target)}"
    if target_type(target, campaign_type) == "AUTO_TARGETING_GROUP":
        return f"{book_id}|{campaign_id}|autogroup:{normalize_term(target)}"
    return f"{book_id}|{campaign_id}|kw:{normalize_term(target)}"


# ------------------------------------------------------------------ evidenza normalizzata
@dataclass
class Ev:
    rec: dict
    action: str                  # azione efficace (BID_UP -> HOLD, low-sample rivalutato)
    step: int
    conf: str
    m: Metrics
    growth: bool = False
    lowsample: bool = False
    note: str = ""


def make_ev(rec: dict, ctx, cfg: dict, term_equals_target: bool) -> Ev:
    R = ctx.royalty
    m = compute_metrics(rec["impressions"], rec["clicks"], rec["spend"], rec["orders"], rec["sales"], R)
    action, conf, note = rec["recommended_action"], rec["confidence"], ""
    growth = action == "BID_UP_5_CANDIDATE"
    if growth:
        action = "HOLD"
    lowsample = False
    if action == "NEGATIVE_CANDIDATE" and term_equals_target:
        action, note = "PAUSE_CANDIDATE", "negative su termine == target equivale a un'azione sul target (pausa candidata)"
    if (rec["rule_classification"] == "LOW_SAMPLE_WATCH" and 1 <= m.orders <= 4 and m.acos is not None
            and m.acos > ctx.break_even_acos and m.est_loss >= cfg["low_sample_loss_mult"] * R):
        b = ctx.bands
        step = 5 if m.acos <= b["mild"] else 10 if m.acos <= b["unprofitable"] else 15
        step = min(step, cfg["low_sample_max_step"])
        action, conf, lowsample = ACTION_OF[step], "LOW", True
        note = (f"campione piccolo ({m.orders:g} ordini) ma perdita stimata {sym()}{m.est_loss:.2f} >= "
                f"{cfg['low_sample_loss_mult']:g}x royalty: bid-down con confidence LOW")
    return Ev(rec, action, STEP_OF.get(action, 0), conf, m, growth, lowsample, note)


def _ev_dict(ev: Ev | None) -> dict | None:
    if ev is None:
        return None
    r = ev.rec
    return {"rec_id": r.get("rec_id"), "report_type": r["report_type"], "report_scope": r["report_scope"],
            "date_from": r["date_from"], "date_to": r["date_to"], "search_term": r["search_term"],
            "analytic_action": r["recommended_action"], "analytic_bid_change_pct": r["recommended_bid_change_pct"],
            "effective_action": ev.action, "rule_classification": r["rule_classification"], "confidence": r["confidence"],
            "effective_confidence": ev.conf, "clicks": r["clicks"], "orders": r["orders"], "spend": r["spend"],
            "sales": r["sales"], "acos": r["acos"], "estimated_direct_ad_profit": r["estimated_direct_ad_profit"],
            "signal": r["recent_vs_historical_signal"], "priority": r["priority"], "reason": r["reason"], "note": ev.note}


def _line(label, ev: Ev | None) -> str:
    if ev is None:
        return f"{label}: n/d"
    r, m = ev.rec, ev.m
    acos = "N/A (NO_SALES)" if m.acos is None else f"{m.acos:.1f}%"
    tag = ev.action + (" (rivalutato low-sample)" if ev.lowsample else "")
    per = "" if r["date_from"] is None and r["report_scope"] == "LIFETIME" else f" {r['date_from']}→{r['date_to']}"
    return (f"{label}{per}: {tag} [{r['rule_classification']}] — {m.clicks:g} clic, {m.orders:g} ordini, spend {sym()}{m.spend:.2f}, "
            f"sales {sym()}{m.sales:.2f}, ACOS {acos}, profit {sym()}{m.est_profit:.2f}, conf {ev.conf}")


# ------------------------------------------------------------------ entita'
@dataclass
class Entity:
    key: str
    campaign_id: str
    kind: str                    # kw / asin / cat / neg
    target: str | None
    life: list = field(default_factory=list)       # Target LIFETIME (o scope non noto)
    tgt_win: list = field(default_factory=list)    # Target di finestre DATE_RANGE
    st_win: list = field(default_factory=list)     # Search Term con termine == target (finestre DATE_RANGE o non note)
    child: list = field(default_factory=list)
    neg: list = field(default_factory=list)


@dataclass
class DecisionSet:
    ctx: object
    generated_at: str
    analysis_run_id: int
    decisions: list = field(default_factory=list)
    accounting: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)            # warning PROPRI del Decision Layer (decision_warnings)
    analysis_warnings: list = field(default_factory=list)   # DATA_WARNING ereditati dall'analysis_run usato
    amazon_unmatched: list = field(default_factory=list)
    amazon_campaign_level: list = field(default_factory=list)
    amazon_campaign_notes: list = field(default_factory=list)
    camp_ctx: dict = field(default_factory=dict)

    @property
    def decision_warnings(self) -> list:
        return self.warnings

    def all_warnings(self) -> list:
        """Warning con prefisso di provenienza: prima gli ereditati dall'analisi, poi i propri."""
        return ([f"[analysis_run {self.analysis_run_id}] {w}" for w in self.analysis_warnings]
                + [f"[decision] {w}" for w in self.warnings])


def _cross_index(overlaps, ctx):
    """{(campaign_id, target_identity): [evidenza cross]} per ogni target che ha catturato un termine in overlap (con period_id)."""
    idx = defaultdict(list)
    for o in overlaps:
        for c in o["campaigns"]:
            role = ("WORSE" if o["worse"] is c else "BETTER" if o["better"] is c else "OTHER")
            for t in c["targets"]:
                if not t:
                    continue
                idx[(c["campaign_id"], amz.target_identity(t))].append({
                    "period_id": o.get("period_id"),
                    "search_term": o["search_term"], "classification": o["classification"], "role": role,
                    "term_equals_target": term_matches_target(o["search_term"], t),
                    "possible_inefficiency": o["possible_inefficiency"],
                    "better_campaign": o["better"]["campaign_id"] if o["better"] else None,
                    "recommendation": o["recommendation"], "detail": o["detail"],
                    "this_campaign": {"clicks": c["m"].clicks, "orders": c["m"].orders, "spend": c["m"].spend,
                                      "sales": c["m"].sales, "acos": c["m"].acos}})
    return idx


def _pick_life(recs):
    """Se esistono piu' snapshot lifetime (date_to diversi) vale il piu' recente; gli altri restano storici."""
    return sorted(recs, key=lambda r: r["date_to"] or "")[-1] if recs else None


def build_decisions(conn, settings: dict, rules_path, book_id: str, analysis_run_id: int | None = None,
                    logger=None, marketplace: str | None = None) -> DecisionSet:
    """Decisioni di UN marketplace del libro: usa l'economia (prezzo, royalty, ACOS) e i dati di quel marketplace, mai altri.
    Il marketplace e' quello richiesto, oppure quello del run di analisi indicato (run storici senza marketplace = marketplace
    legacy del libro), oppure l'unico configurato."""
    books = registry.load_books(settings["books_path"])
    full = books[book_id]
    if analysis_run_id is not None:
        r0 = conn.execute("SELECT marketplace FROM analysis_runs WHERE run_id=? AND book_id=?", (analysis_run_id, book_id)).fetchone()
        if r0 is not None:
            run_mp = registry.run_marketplace(full, r0["marketplace"])
            if marketplace and run_mp and str(marketplace).upper() != run_mp:
                raise registry.RegistryError(f"il run di analisi {analysis_run_id} appartiene al marketplace {run_mp}, non a {str(marketplace).upper()}")
            marketplace = marketplace or run_mp
    book = registry.book_view(full, marketplace)
    mp = book["marketplace"]
    eco, _ = registry.book_economics(book, settings.get("break_even_mismatch_tolerance_pp", 0.5))
    ctx = ctx_from_book(book, eco, load_rules(rules_path), registry.file_tag(full, mp))
    cfg, R = ctx.rules["decision"], ctx.royalty
    set_currency(ctx.currency)
    now = datetime.now().isoformat(timespec="seconds")

    if analysis_run_id is None:
        legacy = registry.run_marketplace(full, None)
        row = conn.execute("SELECT MAX(run_id) FROM analysis_runs WHERE book_id=? AND "
                           "(UPPER(marketplace)=? OR (marketplace IS NULL AND ?=?))", (book_id, mp, mp, legacy)).fetchone()
        analysis_run_id = row[0]
    if analysis_run_id is None:
        raise registry.RegistryError(f"nessuna analisi salvata per il libro {book_id}: esegui prima analyze.py")
    recs = [dict(r) for r in conn.execute(
        "SELECT * FROM recommendations WHERE book_id=? AND run_id=? ORDER BY rec_id", (book_id, analysis_run_id))]
    ds = DecisionSet(ctx=ctx, generated_at=now, analysis_run_id=analysis_run_id)
    ds.accounting["analytic_recommendations"] = len(recs)
    arow = conn.execute("SELECT warnings_json FROM analysis_runs WHERE run_id=? AND book_id=?", (analysis_run_id, book_id)).fetchone()
    if arow and arow["warnings_json"]:
        ds.analysis_warnings = list(json.loads(arow["warnings_json"]))

    # contesto finestre per campagna (snapshot presenti, periodi comparabili, periodo tattico) e totali per snapshot
    ds.camp_ctx = camp_ctx = win.campaign_context(recs, ctx.rules["snapshots"])
    camp_agg = win.campaign_aggregates(recs)

    # dati per bid corrente e overlap (deterministici, stessa funzione del motore analitico)
    trows, srows = _load(conn, "target_rows", book_id, mp), _load(conn, "search_term_rows", book_id, mp)
    for r in srows:
        r["target_type"] = target_type(r["target"], r.get("campaign_type"))
    bid_of = {}
    for t in trows:
        k = (t["campaign_id"], amz.target_identity(t["target"]))
        cur = bid_of.get(k)
        # piu' snapshot dello stesso target: vale il bid dello snapshot piu' recente
        if cur is not None and (cur["date_to"] or "") > (t["date_to"] or ""):
            continue
        bid_of[k] = t
    cross = _cross_index(analyze_overlaps_by_period(srows, ctx), ctx)
    # stato operativo del target: Target LIFETIME piu' recente disponibile (nessun lifetime -> UNKNOWN)
    status_of, _st_key = {}, {}
    for t in trows:
        if t["report_scope"] != "LIFETIME":
            continue
        k = (t["campaign_id"], amz.target_identity(t["target"]))
        key = (t["date_to"] or "", t["id"])
        if k not in _st_key or key > _st_key[k]:
            _st_key[k], status_of[k] = key, map_target_status(t.get("raw_json"))

    # ---- raggruppamento in entita'
    ents: dict[str, Entity] = {}

    def ent(campaign_id, target, kind=None, term=None, campaign_type=None):
        key = action_key(book_id, campaign_id, target, kind, term, campaign_type)
        if key not in ents:
            k = kind or ("asin" if product_asin(target) else "cat" if target_type(target) == "PRODUCT_TARGET"
                         else "auto" if target_type(target, campaign_type) == "AUTO_TARGETING_GROUP" else "kw")
            ents[key] = Entity(key, campaign_id, k, target)
        return ents[key]

    for rec in recs:
        ct = rec.get("campaign_type")
        if rec["report_type"] == "TARGETING":
            (ent(rec["campaign_id"], rec["target"], campaign_type=ct).tgt_win if rec["report_scope"] == "DATE_RANGE"
             else ent(rec["campaign_id"], rec["target"], campaign_type=ct).life).append(rec)
        elif term_matches_target(rec["search_term"], rec["target"], ct):
            ent(rec["campaign_id"], rec["target"], campaign_type=ct).st_win.append(rec)
        elif rec["recommended_action"] == "NEGATIVE_CANDIDATE":
            # negative su un search term REALE (il gruppo Auto resta il target genitore; mai una negative sul nome del gruppo)
            ent(rec["campaign_id"], rec["target"], "neg", rec["search_term"], campaign_type=ct).neg.append(rec)
        else:
            ent(rec["campaign_id"], rec["target"], campaign_type=ct).child.append(rec)
    n_life = sum(len(e.life) for e in ents.values())
    ds.accounting["lifetime_target_rows_used"] = n_life
    ds.accounting["target_window_rows_used"] = sum(len(e.tgt_win) for e in ents.values())
    ds.accounting["recent_rows_matching_target"] = sum(len(e.st_win) for e in ents.values())
    ds.accounting["child_search_term_rows_attached_as_context"] = sum(len(e.child) for e in ents.values())
    ds.accounting["negative_candidate_rows"] = sum(len(e.neg) for e in ents.values())
    ds.accounting["evidence_rows_total"] = sum(ds.accounting[k] for k in list(ds.accounting) if k not in (
        "analytic_recommendations", "evidence_rows_total"))

    mp_campaigns = {r["campaign_id"] for r in trows + srows} | {r["campaign_id"] for r in conn.execute(
        "SELECT campaign_id FROM campaigns WHERE book_id=? AND UPPER(marketplace)=?", (book_id, mp))}
    amz_recs = [a for a in amz.load(conn, book_id) if a["campaign_id"] in mp_campaigns]     # solo campagne di QUESTO marketplace
    amz_by_target = defaultdict(list)
    for a in amz_recs:
        if a["target_normalized"]:
            amz_by_target[(a["campaign_id"], a["target_normalized"])].append(a)
        else:
            ds.amazon_campaign_level.append(a)
    matched_amz = set()

    out = [_consolidate(e, ctx, cfg, cross, bid_of, amz_by_target, matched_amz, book_id, now, analysis_run_id, ds, camp_ctx, camp_agg, status_of)
           for e in ents.values()]
    ds.amazon_unmatched = [a for a in amz_recs if a["target_normalized"] and a["id"] not in matched_amz]
    ds.amazon_campaign_notes = _amazon_campaign_notes(ds.amazon_campaign_level, camp_ctx, camp_agg, ctx)

    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    out.sort(key=lambda d: (order[d["priority"]], -d["_loss"], -(d["lifetime_spend"] or 0)))
    for i, d in enumerate(out, 1):
        d["decision_id"] = f"D{now.replace('-', '').replace(':', '').replace('T', '')}-{i:04d}"   # univoco fra run, identico in CSV e DB
    ds.decisions = out
    ds.accounting["consolidated_decisions"] = len(out)
    if logger:
        logger.info("Decision layer libro %s: %d recs analitiche -> %d decisioni", book_id, len(recs), len(out))
    return ds


def _amazon_campaign_notes(campaign_level, camp_ctx, camp_agg, ctx) -> list[str]:
    """Nota INFORMATIVA (non guida nessuna decisione): delivery al giorno dell'osservazione Amazon manuale a livello campagna
    vs il periodo di riferimento piu' lungo importato. Da rivalutare quando si importano i report dello stesso intervallo."""
    notes = []
    dcfg = ctx.rules["delivery"]
    for a in campaign_level:
        if a["observed_spend"] is None:
            continue
        days = days_inclusive(a["period_from"], a["period_to"])
        c = camp_ctx.get(a["campaign_id"])
        if not days or not c:
            continue
        same = camp_agg.get((a["campaign_id"], "TARGETING", make_period_id("DATE_RANGE", a["period_from"], a["period_to"])))
        if same:
            notes.append(f"{a['campaign_id']}: report Target dello stesso intervallo {a['period_from']}→{a['period_to']} gia' importato: "
                         "vedere DATA_WARNING del confronto totali (analisi).")
            continue
        pair = win.pick_pair(c["snaps"]["SEARCH_TERM"], None)
        ref_pid = max((p for p, i in c["snaps"]["SEARCH_TERM"].items() if i["scope"] == "DATE_RANGE" and i["days"]),
                      key=lambda p: c["snaps"]["SEARCH_TERM"][p]["days"], default=None)
        ref = camp_agg.get((a["campaign_id"], "SEARCH_TERM", ref_pid)) if ref_pid else None
        if not ref or not ref["days"] or not ref["spend"]:
            continue
        ref_spd, am_spd = ref["spend"] / ref["days"], a["observed_spend"] / days
        ratio = am_spd / ref_spd
        sig = ("DELIVERY_INCREASING" if ratio >= dcfg["ratio_increasing_min"] else "DELIVERY_STABLE" if ratio >= dcfg["ratio_declining_max"]
               else "DELIVERY_DECLINING" if ratio >= dcfg["ratio_collapsed_max"] else "DELIVERY_COLLAPSED")
        notes.append(f"{a['campaign_id']}: spesa/giorno secondo Amazon (manuale) {sym()}{am_spd:.2f} ({a['period_from']}→{a['period_to']}, {days}g) vs "
                     f"{sym()}{ref_spd:.2f} nel Search Term {ref_pid} ({ref['days']}g) = x{ratio:.2f} → {sig} (INFORMATIVO: osservazione manuale, "
                     "non guida decisioni finche' non si importano i report dello stesso intervallo)")
    return notes


def _consolidate(e: Entity, ctx, cfg, cross, bid_of, amz_by_target, matched_amz, book_id, now, analysis_run_id, ds,
                 camp_ctx, camp_agg, status_of=None) -> dict:
    R = ctx.royalty
    dcfg, snap_cfg = ctx.rules["delivery"], ctx.rules["snapshots"]
    cc = camp_ctx.get(e.campaign_id) or {"snaps": {"TARGETING": {}, "SEARCH_TERM": {}}, "comparable": set(), "latest_comparable": None,
                                        "tactical_pid": None, "tactical_comparable": False}
    if len(e.life) > 1:
        ds.warnings.append(f"DATA_WARNING {e.key}: {len(e.life)} snapshot lifetime, uso il piu' recente")
    life_rec = _pick_life(e.life)
    L = make_ev(life_rec, ctx, cfg, True) if life_rec else None
    negs = [make_ev(n, ctx, cfg, False) for n in e.neg]

    # ---- selezione evidenze per finestra: tattica (ultimo periodo comparabile) > contesto recente > lifetime storico
    tpid = cc["tactical_pid"]
    tac_rec, tac_src = None, None
    if tpid:
        t_at = [r for r in e.tgt_win if win.rec_period(r) == tpid]
        s_at = [r for r in e.st_win if win.rec_period(r) == tpid]
        if t_at:
            tac_rec, tac_src = t_at[0], "T"
        elif s_at:
            tac_rec, tac_src = s_at[0], "S"
    Tv = make_ev(tac_rec, ctx, cfg, True) if tac_rec else None
    tac_pid = win.rec_period(tac_rec) if tac_rec else None
    ctx_cands = [r for r in e.st_win + e.tgt_win if win.rec_period(r) != tac_pid
                 and (r["report_scope"] != "DATE_RANGE" or window_role(r["report_scope"], r["date_from"], r["date_to"], snap_cfg) != "TACTICAL"
                      or tac_pid is None)]
    ctx_cands.sort(key=lambda r: (-(win.rec_days(r) or 0), 0 if r["report_type"] == "SEARCH_TERM" else 1, -len(r["date_to"] or "")))
    ctx_rec = None
    if tac_pid is None:
        # nessun periodo tattico: il Search Term recente (piu' lungo) e' l'evidenza recente, come nel decision layer V1
        pool = e.st_win or e.tgt_win
        ctx_rec = sorted(pool, key=lambda r: ((win.rec_days(r) or 0), r["date_to"] or ""), reverse=True)[0] if pool else None
    else:
        ctx_rec = ctx_cands[0] if ctx_cands else None
    Rc = make_ev(ctx_rec, ctx, cfg, True) if ctx_rec else None
    if len(e.st_win) > 1 and tac_pid is None:
        ds.warnings.append(f"NOTA {e.key}: {len(e.st_win)} finestre Search Term senza periodo tattico comparabile, uso la piu' lunga come contesto recente")
    P = Tv or Rc                       # evidenza recente PRIMARIA
    used_ids = {id(x.rec) for x in (L, Tv, Rc) if x}
    others = [r for r in e.tgt_win + e.st_win + e.life if id(r) not in used_ids]

    base_rec = (e.life or e.tgt_win or e.st_win or e.neg or e.child)[0]
    ident = amz.target_identity(e.target)

    # ---- cross-campaign: supporta solo se relativo al periodo dell'evidenza primaria (o senza periodo noto)
    xs = cross.get((e.campaign_id, ident), []) if e.kind != "neg" else []
    ev_period = win.rec_period(P.rec) if P else None
    supports = [x for x in xs if x["possible_inefficiency"] and x["role"] == "WORSE" and x["term_equals_target"]
                and (ev_period is None or x.get("period_id") in (None, ev_period))]
    better = next((x["better_campaign"] for x in xs if x["role"] == "WORSE" and x["term_equals_target"] and x["better_campaign"]), None)

    def xs_txt(x):
        return (f"{x['search_term']} [{x.get('period_id')}]: {x['classification']} [{x['role']}{'' if x['term_equals_target'] else ', termine figlio'}"
                f"{', POSSIBILE INEFFICIENZA' if x['possible_inefficiency'] else ''}]")
    cross_txt = " | ".join(xs_txt(x) for x in xs)

    # ---- delivery (per giorno, stesso tipo di report): entita' e campagna
    rows_by_rt = {"TARGETING": {}, "SEARCH_TERM": {}}
    for r in e.tgt_win:
        rows_by_rt["TARGETING"][win.rec_period(r)] = win.agg_of(r)
    for r in e.st_win:
        rows_by_rt["SEARCH_TERM"][win.rec_period(r)] = win.agg_of(r)
    dsig, dratio, dsrc, ddetail, dflags = win.delivery_from_rows(rows_by_rt, cc["snaps"], tpid, dcfg)
    camp_rows = {rt: {pid: camp_agg[(e.campaign_id, rt, pid)] for pid in cc["snaps"][rt] if (e.campaign_id, rt, pid) in camp_agg}
                 for rt in cc["snaps"]}
    csig, cratio, csrc, cdetail, _ = win.delivery_from_rows(camp_rows, cc["snaps"], tpid, dcfg)
    if dsig != "UNKNOWN":
        eff_sig, eff_src, eff_detail = dsig, f"ENTITY:{dsrc}", ddetail
    elif dcfg["use_campaign_level_fallback"] and csig != "UNKNOWN":
        eff_sig, eff_src, eff_detail = csig, f"CAMPAIGN:{csrc}", cdetail
    else:
        eff_sig, eff_src, eff_detail = "UNKNOWN", None, ddetail

    life_step = L.step if L else 0
    p_step = P.step if P else 0
    flags, rule, conf, step = list(dflags), "C0_NO_DOWN", None, 0
    logic = ""
    hv = bool(L and L.m.orders >= ctx.rules["with_orders"]["high_volume_orders"])
    signal = P.rec["recent_vs_historical_signal"] if P else (base_rec["recent_vs_historical_signal"] if L else "UNKNOWN")
    resolved = None                                    # azione non-bid-down risolta esplicitamente (es. C6)
    tac_good = bool(Tv and L and life_step > 0 and Tv.step == 0 and Tv.action == "HOLD"
                    and CONF_ORDER[Tv.conf] >= CONF_ORDER[cfg["tactical_good_min_confidence"]]
                    and Tv.m.acos is not None and Tv.m.acos <= ctx.break_even_acos)

    if e.kind == "neg":
        n = negs[0]
        final_action, step, conf, rule = "NEGATIVE_CANDIDATE", 0, n.conf, "N1_NEGATIVE_SEARCH_TERM"
        logic = "Search term con 0 ordini e spesa >= 2x royalty: candidato negative (raccomandazione separata dal bid del target)."
    elif tac_good:
        rule, resolved, conf = "C6_TACTICAL_RECOVERED", "HOLD", Tv.conf
        logic = (f"Lo storico e' in bid-down ({L.action}) ma il periodo tattico piu' recente ({Tv.rec['period_id'] if Tv.rec.get('period_id') else win.rec_period(Tv.rec)}) "
                 f"e' sano (ACOS {Tv.m.acos:.1f}% <= break-even, confidence {Tv.conf}): l'evidenza recente ha priorita', nessun bid-down; storico come contesto.")
        flags.append("TACTICAL_RECOVERED")
    elif life_step > 0 and p_step > 0:
        step = max(life_step, p_step)
        conf = CONF_NAMES[max(CONF_ORDER[L.conf], CONF_ORDER[P.conf])]
        rule = "C1_DOWN_CONFIRMED"
        logic = (f"Lifetime ({L.action}) e recente ({P.action}) concordano su un bid-down: si applica UNA volta il passo maggiore "
                 f"(non si sommano).")
    elif life_step > 0:
        step, conf, rule = life_step, L.conf, "C2_LIFETIME_DOWN"
        logic = "Bid-down guidato dal lifetime; il recente non lo contraddice o non ha dati sufficienti."
        if P and P.action == "HOLD" and signal == "IMPROVING_SIGNAL" and step < 100:
            step = max(5, step - cfg["recovering_step_reduction"])
            conf = cap(conf, "MEDIUM")
            rule = "C2B_LIFETIME_DOWN_RECOVERING"
            logic = "Lifetime in bid-down ma il recente e' HOLD con segnale IMPROVING: passo ridotto di un livello (minimo -5%), performance in recupero."
    elif p_step > 0:
        life_usable = L is not None and L.action in ("HOLD", "WATCH")
        if not life_usable:
            step, conf, rule = p_step, P.conf, "C3_RECENT_ONLY"
            logic = "Nessun lifetime utilizzabile: bid-down guidato solo dal recente."
        else:
            step, rule = min(p_step, cfg["recent_deterioration_cap_step"]), "C4_RECENT_DETERIORATION"
            if (p_step >= 10 and P.conf == cfg["recent_deterioration_step10_needs_conf"] and signal == "DECLINING_SIGNAL"):
                step = 10
            conf = cap(P.conf, "MEDIUM")
            flags.append("RECENT_DETERIORATION")
            logic = (f"DETERIORAMENTO RECENTE: lifetime {L.action} (ACOS "
                     f"{'N/A' if L.m.acos is None else format(L.m.acos, '.1f') + '%'}) ma il periodo recente segnala {P.action}; "
                     f"le evidenze discordano quindi il passo e' limitato a -{step}% e la confidence a {conf}.")
    elif supports:
        step, rule = 5, "C5_CROSS_ONLY"
        conf = "MEDIUM"
        logic = "Nessun bid-down da lifetime/recente; il cross-campaign indica questa campagna materialmente peggiore sullo stesso termine: -5%."

    review_underlying = None
    if e.kind != "neg":
        if step > 0:
            if supports and rule in ("C1_DOWN_CONFIRMED", "C2_LIFETIME_DOWN", "C3_RECENT_ONLY"):
                if CONF_ORDER[conf] >= 1:
                    conf = bump(conf)
                logic += " Il cross-campaign conferma (non additivo): +1 livello di confidence, nessun passo aggiuntivo."
            if hv and step > 15:
                step = 15
                flags.append("HIGH_VOLUME_CAP")
                logic += f" Safety high-volume ({L.m.orders:g} ordini lifetime >= 10): mai pausa, massimo -15%."
            if hv and step >= 10 and "HIGH_VOLUME" not in flags:
                flags.append("HIGH_VOLUME")
            if L and L.lowsample or (P and P.lowsample and rule in ("C3_RECENT_ONLY",)):
                flags.append("LOW_SAMPLE_LOSS")
            # ---- SAFETY: delivery. ACOS alto + delivery crollata => prima verificare la delivery, non abbassare ancora il bid
            inherited = delivery_source_of(eff_src) == "CAMPAIGN_FALLBACK"
            if eff_sig in dcfg["review_on"] and not inherited:
                review_underlying = ACTION_OF[step]
                flags.append("REVIEW_DELIVERY")
                step = 0
                final_action = REVIEW
                logic += (f" SAFETY DELIVERY: {eff_sig} ({eff_detail}). ACOS/risultati sfavorevoli ma la campagna/target sta gia' quasi "
                          f"smettendo di entrare nelle aste: nessun ulteriore bid-down, prima verificare la delivery (candidato sottostante: {review_underlying}).")
            else:
                if inherited and eff_sig in dcfg["review_on"]:
                    # COLLASSO ereditato dalla campagna: non e' prova sul singolo target, niente REVIEW; trattato come 'in calo' (solo cap del passo)
                    flags.append("DELIVERY_INHERITED_COLLAPSED_NOT_ENFORCED")
                if (eff_sig == "DELIVERY_DECLINING" or (inherited and eff_sig in dcfg["review_on"])) and step > dcfg["declining_cap_step"]:
                    logic += (f" Delivery in calo ({eff_detail}): passo limitato da -{step}% a -{dcfg['declining_cap_step']}%.")
                    step = dcfg["declining_cap_step"]
                    flags.append("DELIVERY_DECLINING_CAP")
                final_action = ACTION_OF[step]
        elif resolved:
            final_action = resolved
        else:
            # nessun bid-down: HOLD > WATCH > NO_ACTION/INSUFFICIENT fra le evidenze utilizzabili (tattica prima)
            cands = [ev for ev in (Tv, L, Rc) if ev]      # spareggio: tattica > lifetime (campione piu' ampio) > contesto recente
            if not cands:
                cands = [Ev(base_rec, "NO_ACTION", 0, "LOW", compute_metrics(0, 0, 0, 0, 0, R), note="solo termini figli")]
            cands.sort(key=lambda ev: -NON_DOWN_RANK.get(ev.action, 0))
            base = cands[0]
            final_action, conf = base.action, base.conf
            src = "tattica" if base is Tv else "recente" if base is Rc else "lifetime"
            logic = f"Nessuna evidenza di bid-down: azione dall'evidenza {src} ({base.action})."
    # ---- stato del target: un target in pausa non puo' avere un'azione di bid operativa (l'analisi economica resta)
    tstatus = (status_of or {}).get((e.campaign_id, ident), "UNKNOWN")
    underlying = None
    if tstatus == "PAUSED" and final_action in PAUSED_BLOCKED:
        underlying = review_underlying or final_action
        logic += (f" Target currently paused. Underlying economics would suggest {underlying} if reactivated. "
                  "Nessuna riattivazione automatica.")
        final_action, step = HOLD_PAUSED, 0
        flags.append("TARGET_PAUSED")
    elif tstatus == "UNKNOWN" and e.kind != "neg" and final_action in PAUSED_BLOCKED:
        underlying = review_underlying or final_action
        logic += (" Target status unknown. Verify current Amazon Ads status before applying any bid action. "
                  f"Underlying candidate: {underlying}.")
        final_action, step = REVIEW_STATUS, 0
        flags.append("TARGET_STATUS_UNKNOWN")
    if tstatus == "PAUSED" and any(ev is not None and ev.m.spend > 0 for ev in (Tv, Rc)):
        flags.append("RECENT_SPEND_ON_PAUSED_TARGET")
        logic += (" Spesa recente > 0 su target in pausa: puo' essere precedente alla data effettiva di pausa o dovuta a differenze "
                  "temporali di reporting; la causa non e' dedotta.")
    actionable = tstatus == "ENABLED" and final_action in OPERATIONAL_ACTIONS
    bid_pct = None if (e.kind == "neg" or step in (0, 100)) else -step

    # ---- growth opportunity (mai un aumento operativo)
    growth = None
    if e.kind != "neg" and final_action == "HOLD" and L and L.growth and L.m.orders >= cfg["growth_min_orders"]:
        recent_ok = P is None or (P.step == 0 and P.action in ("HOLD", "NO_ACTION", "INSUFFICIENT_DATA")
                                  and P.rec["recent_vs_historical_signal"] != "DECLINING_SIGNAL")
        if recent_ok:
            growth = GROWTH
            logic += (f" Opportunita' di crescita: {L.m.orders:g} ordini, ACOS {L.m.acos:.1f}% ben sotto target, confidence {L.conf}, "
                      "recente non peggiorativo. Azione operativa resta HOLD (nessun aumento del bid).")

    # ---- priorita' su perdita stimata massima fra le evidenze; il REVIEW mantiene la priorita' del candidato sottostante
    ms = [ev.m for ev in (L, Tv, Rc) if ev] + [n.m for n in negs]
    basis = max(ms, key=lambda m: m.est_loss) if ms else compute_metrics(0, 0, 0, 0, 0, R)
    prio = priority(underlying or review_underlying or final_action, basis, conf or "LOW", ctx)
    if e.kind == "neg":
        conf = negs[0].conf

    # ---- Amazon (solo audit)
    am_text = am_period = agree = am_note = None
    am_ev = []
    matches = amz_by_target.get((e.campaign_id, ident), []) if e.kind != "neg" else []
    if matches:
        for a in matches:
            matched_amz.add(a["id"])
        am_ev = matches
        parts, agreements, notes = [], [], []
        for a in matches:
            ob = "" if a["observed_spend"] is None else f" spesa {sym()}{a['observed_spend']:.2f}"
            oo = "" if a["observed_orders"] is None else f", {a['observed_orders']:g} acquisti"
            parts.append(f"{a['recommendation_type']} [{a['suggested_direction']}]: {a['suggested_action'] or a['observation'] or ''}"
                         f" ({a['source']}, {a['period_from']}→{a['period_to']};{ob}{oo})")
            agreements.append(amz.agreement(final_action, a["suggested_direction"], bool(growth)))
            if a["observed_spend"] is not None and a["observed_spend"] < cfg["amazon_weak_spend_mult"] * R:
                notes.append(f"evidenza Amazon debole (spesa osservata {sym()}{a['observed_spend']:.2f} < {cfg['amazon_weak_spend_mult']:g}x royalty)")
            rc_rec = Rc.rec if Rc else None
            if a["observed_orders"] == 0 and rc_rec and Rc.m.orders > 0:
                notes.append(f"Amazon vede 0 acquisti in {a['period_from']}→{a['period_to']} mentre il nostro recente "
                             f"({rc_rec['date_from']}→{rc_rec['date_to']}) ha {Rc.m.orders:g} ordini: se i dati sono coerenti gli ordini "
                             f"sono avvenuti fuori dalla finestra Amazon (possibile calo recente da verificare)")
            notes.append(f"periodi diversi: Amazon {a['period_from']}→{a['period_to']} vs lifetime/recente nostri")
        agree = agreements[0] if len(set(agreements)) == 1 else "PARTIAL"
        am_text = " || ".join(parts)
        am_period = "; ".join(sorted({f"{a['period_from']}→{a['period_to']}" for a in matches}))
        am_note = "; ".join(dict.fromkeys(notes))

    child_note = None
    if e.child:
        adverse = [c for c in e.child if c["recommended_action"].startswith("BID_DOWN") or c["recommended_action"] in ("PAUSE_CANDIDATE", "NEGATIVE_CANDIDATE")]
        child_note = (f"{len(e.child)} search term figli (spesa {sym()}{sum(c['spend'] for c in e.child):.2f}, "
                      f"{sum(c['orders'] for c in e.child):g} ordini) attribuiti a questo target; {len(adverse)} con azione analitica avversa: "
                      "contesto, non guidano un secondo bid change")

    match_row = bid_of.get((e.campaign_id, ident))
    tgt_type = target_type(e.target, base_rec["campaign_type"])

    def v(ev, f):
        return None if ev is None else ev.rec[f]

    def pdm(ev):
        return win.per_day_metrics(None if ev is None else win.agg_of(ev.rec))

    life_pd, rec_pd, tac_pd = pdm(L), pdm(Rc), pdm(Tv)
    hist_parts = []
    if L:
        hist_parts.append(f"lifetime {L.action} ({L.m.orders:g} ord, ACOS {'N/A' if L.m.acos is None else format(L.m.acos, '.1f') + '%'})")
    if Rc:
        hist_parts.append(f"{Rc.rec.get('window_id') or window_label(Rc.rec['report_scope'], Rc.rec['date_from'], Rc.rec['date_to'])} {Rc.action} "
                          f"({Rc.m.orders:g} ord, ACOS {'N/A' if Rc.m.acos is None else format(Rc.m.acos, '.1f') + '%'})")
    historical_context = "; ".join(hist_parts) or None

    reason = (f"FINAL {final_action}{'' if bid_pct is None else f' ({bid_pct:+.0f}%)'} [rule {rule}, conf {conf}, prio {prio}]. "
              + " | ".join([_line("Lifetime", L), _line("Tactical", Tv), _line("Recent", Rc)]
                           + ([_line("Search term negative", negs[0])] if negs else [])
                           + [f"Delivery: {eff_sig}{'' if eff_src is None else ' (' + delivery_source_of(eff_src) + ':' + eff_src.split(':')[1] + ')'}"
                              f"{' - delivery signal inherited from campaign' if delivery_source_of(eff_src) == 'CAMPAIGN_FALLBACK' else ''}"
                              f"{'' if not eff_detail else ' ' + eff_detail}",
                              f"Cross: {cross_txt or 'nessun overlap'}", f"Amazon: {am_text or 'n/d'}"])
              + f" | Logica: {logic}" + ("" if not flags else f" Flag: {', '.join(dict.fromkeys(flags))}."))
    tac_ev = _ev_dict(Tv)
    d = {
        "decision_id": None, "action_key": e.key, "run_id": None, "analysis_run_id": analysis_run_id, "generated_at": now,
        "book_id": book_id, "marketplace": ctx.marketplace, "campaign_id": e.campaign_id, "campaign_name": base_rec["campaign_name"],
        "campaign_type": base_rec["campaign_type"], "target": e.target, "target_type": tgt_type,
        "match_type": base_rec["match_type"] if e.kind != "neg" else None,
        "search_term": negs[0].rec["search_term"] if negs else None,
        "current_bid": match_row["bid"] if match_row else None,
        "final_action": final_action, "bid_change_pct": bid_pct, "confidence": conf, "priority": prio,
        "lifetime_clicks": v(L, "clicks"), "lifetime_orders": v(L, "orders"), "lifetime_spend": v(L, "spend"),
        "lifetime_sales": v(L, "sales"), "lifetime_acos": v(L, "acos"), "lifetime_estimated_profit": v(L, "estimated_direct_ad_profit"),
        "recent_clicks": v(Rc, "clicks"), "recent_orders": v(Rc, "orders"), "recent_spend": v(Rc, "spend"),
        "recent_sales": v(Rc, "sales"), "recent_acos": v(Rc, "acos"), "recent_estimated_profit": v(Rc, "estimated_direct_ad_profit"),
        "recent_period": (f"{Rc.rec['date_from']}→{Rc.rec['date_to']}" if Rc else None),
        "recent_vs_historical_signal": signal,
        "lifetime_action": L.action if L else None, "recent_action": Rc.action if Rc else None,
        "cross_campaign_overlap": cross_txt or None, "better_campaign_if_any": better,
        "growth_opportunity": growth,
        "amazon_recommendation": am_text, "amazon_recommendation_period": am_period, "recommendation_agreement": agree,
        "amazon_agreement_note": am_note,
        "decision_rule": rule, "flags": ";".join(dict.fromkeys(flags)), "child_terms_note": child_note, "reason": reason,
        "lifetime_evidence": json.dumps(_ev_dict(L), ensure_ascii=False, default=str) if L else None,
        "recent_evidence": json.dumps(_ev_dict(Rc), ensure_ascii=False, default=str) if Rc else None,
        "cross_campaign_evidence": json.dumps(xs, ensure_ascii=False, default=str) if xs else None,
        "amazon_evidence": json.dumps(am_ev, ensure_ascii=False, default=str) if am_ev else None,
        # --- period snapshot / recency / delivery
        "latest_comparable_snapshot": cc["latest_comparable"], "comparable_period": int(bool(Tv and cc["tactical_comparable"])),
        "tactical_source": tac_src, "tactical_window": (window_label(Tv.rec["report_scope"], Tv.rec["date_from"], Tv.rec["date_to"]) if Tv else None),
        "tactical_period": tac_pid,
        "tactical_clicks": v(Tv, "clicks"), "tactical_orders": v(Tv, "orders"), "tactical_spend": v(Tv, "spend"),
        "tactical_sales": v(Tv, "sales"), "tactical_acos": v(Tv, "acos"), "tactical_estimated_profit": v(Tv, "estimated_direct_ad_profit"),
        "lifetime_impressions_per_day": life_pd["impressions_per_day"], "lifetime_clicks_per_day": life_pd["clicks_per_day"],
        "lifetime_spend_per_day": life_pd["spend_per_day"], "lifetime_orders_per_day": life_pd["orders_per_day"],
        "recent_window": (window_label(Rc.rec["report_scope"], Rc.rec["date_from"], Rc.rec["date_to"]) if Rc else None),
        "recent_period_id": win.rec_period(Rc.rec) if Rc else None,
        "recent_impressions_per_day": rec_pd["impressions_per_day"], "recent_clicks_per_day": rec_pd["clicks_per_day"],
        "recent_spend_per_day": rec_pd["spend_per_day"], "recent_orders_per_day": rec_pd["orders_per_day"],
        "tactical_impressions_per_day": tac_pd["impressions_per_day"], "tactical_clicks_per_day": tac_pd["clicks_per_day"],
        "tactical_spend_per_day": tac_pd["spend_per_day"], "tactical_orders_per_day": tac_pd["orders_per_day"],
        "delivery_signal": eff_sig, "delivery_ratio": (dratio if dsig != "UNKNOWN" else cratio if eff_src and eff_src.startswith("CAMPAIGN") else None),
        "delivery_source": delivery_source_of(eff_src), "campaign_delivery_signal": csig,
        "target_status": tstatus, "underlying_candidate_action": underlying, "operationally_actionable": int(actionable),
        "latest_period_action": Tv.action if Tv else None, "historical_context": historical_context,
        "final_consolidated_action": final_action,
        "tactical_evidence": json.dumps({"tactical": tac_ev, "other_windows": [
            {"rec_id": r.get("rec_id"), "report_type": r["report_type"], "period_id": win.rec_period(r), "action": r["recommended_action"]} for r in others]},
            ensure_ascii=False, default=str) if (tac_ev or others) else None,
        "manual_approval_required": 1, "status": "PROPOSED", "status_updated_at": now,
        "_loss": basis.est_loss, "_growth": bool(growth), "_ev": {"L": L, "T": Tv, "R": Rc, "neg": negs, "child": e.child, "cross": xs},
    }
    return d
