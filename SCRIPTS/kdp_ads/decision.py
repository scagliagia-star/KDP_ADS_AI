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
from .dataquality import impossible_warnings, split_search_terms
from .crosscampaign import analyze_overlaps_by_period
from .metrics import Metrics, compute_metrics, normalize_term, product_asin, product_kind, set_currency, sym, target_type, term_matches_target
from .rules import CONF_ORDER, ctx_from_book, load_rules, priority
from . import windows as win
from .snapshots import days_inclusive, period_id as make_period_id, window_label, window_role
from . import trend as TR

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
TARGET_STATUS_MAP = {"ABILITATO": "ENABLED", "IN PAUSA": "PAUSED", "ENABLED": "ENABLED", "PAUSED": "PAUSED"}


def bid_txt(row) -> str:
    return "n/d" if not row or row.get("bid") is None else format(row["bid"], ".2f")


def _mild_deterioration(L, P, bid, ctx, cfg) -> bool:
    """C4B: NON ridurre l'offerta in modo meccanico (monitora) quando TUTTE queste condizioni valgono (soglie in rules.json -> decision):
      - lo storico e' profittevole: ACOS lifetime sopra l'obiettivo ma <= pareggio, con scostamento piccolo: ACOS <= obiettivo x (1 + mild_max_gap_pct/100);
      - l'offerta attuale e' gia' molto bassa: <= mild_low_bid_royalty_frac x royalty;
      - il peggioramento recente ha poca evidenza: spesa recente < mild_recent_spend_mult x royalty (perdita ancora contenuta)."""
    if not cfg.get("mild_monitor_enabled", True) or L is None or P is None or bid is None or L.m.acos is None:
        return False
    return bool(ctx.target_acos < L.m.acos <= ctx.break_even_acos
                and L.m.acos <= ctx.target_acos * (1 + cfg["mild_max_gap_pct"] / 100.0)
                and bid <= cfg["mild_low_bid_royalty_frac"] * ctx.royalty
                and P.m.spend < cfg["mild_recent_spend_mult"] * ctx.royalty)


def _strong_cut(L, P, ctx, cfg) -> int:
    """Taglio piu' forte (10% o 15%) quando il problema e' FORTE e CONFERMATO, altrimenti 0. Condizioni (soglie in rules.json -> decision):
      storico e recente (evidenza primaria) ENTRAMBI sopra il pareggio, recente >= strong_cut_min_recent_acos_mult x ACOS obiettivo,
      recente con >= strong_cut_min_recent_clicks clic, storico con >= strong_cut_min_hist_orders acquisti  => 10%;
      recente >= strong_cut_15_recent_acos_mult x obiettivo con >= strong_cut_15_min_recent_clicks clic       => 15%.
    Con campioni piccoli (poche decine di clic o pochi acquisti storici) non scatta mai: resta il passo prudente."""
    if L is None or P is None or L.m.acos is None or P.m.acos is None:
        return 0
    if not (L.m.acos > ctx.break_even_acos and P.m.acos > ctx.break_even_acos):
        return 0
    if not (P.m.acos >= cfg["strong_cut_min_recent_acos_mult"] * ctx.target_acos and P.m.clicks >= cfg["strong_cut_min_recent_clicks"]
            and L.m.orders >= cfg["strong_cut_min_hist_orders"]):
        return 0
    if P.m.acos >= cfg["strong_cut_15_recent_acos_mult"] * ctx.target_acos and P.m.clicks >= cfg["strong_cut_15_min_recent_clicks"]:
        return 15
    return 10


def _no_recent_traffic(Tv, tpid, bid, status, ctx, cfg) -> bool:
    """Target ATTIVO, storicamente da ridurre, ma nel periodo tattico senza clic ne' spesa e con offerta gia' bassa
    (<= mild_low_bid_royalty_frac x royalty, la stessa soglia di C4B): ridurre l'offerta non serve, il target non sta spendendo."""
    if not cfg.get("no_traffic_monitor_enabled", True) or tpid is None or status != "ENABLED" or bid is None:
        return False
    return bool((Tv is None or (Tv.m.clicks == 0 and Tv.m.spend == 0)) and bid <= cfg["mild_low_bid_royalty_frac"] * ctx.royalty)


def _account_of(conn, book_id, rows) -> dict:
    """{campaign_id interno: (ID account, nome account)} dalle campagne del libro (colonna amazon_account_id) con ripiego sulle righe dati; le campagne legacy senza
    account restano assenti: mai un account dedotto da nome/tipo/target."""
    out = {}
    try:
        for r in conn.execute("SELECT campaign_id, amazon_account_id FROM campaigns WHERE book_id=?", (book_id,)):
            if r[1]:
                out[r[0]] = r[1]
    except Exception:                                       # DB senza la colonna (schema precedente): nessun account
        pass
    for r in rows:
        if r.get("amazon_account_id"):
            out.setdefault(r["campaign_id"], r["amazon_account_id"])
    names = {}
    try:
        for r in conn.execute("SELECT account_id, MAX(account_name_last_seen) FROM campaign_book_mapping WHERE account_id<>'' GROUP BY account_id"):
            names[r[0]] = r[1]
    except Exception:
        pass
    return {cid: (a, names.get(a)) for cid, a in out.items()}


def _amazon_campaign_ids(conn, book_id, rows) -> dict:
    """{campaign_id interno: ID campagna Amazon} dal mapping confermato (con ripiego sulle righe dati). Le campagne storiche senza ID Amazon restano assenti."""
    out = {}
    try:
        for r in conn.execute("SELECT campaign_id, amazon_campaign_id FROM campaign_book_mapping WHERE book_id=? AND status='ASSIGNED' AND campaign_id IS NOT NULL", (book_id,)):
            out[r[0]] = r[1]
    except Exception:                                       # DB senza la tabella (schema precedente)
        pass
    for r in rows:
        if r.get("amazon_campaign_id"):
            out.setdefault(r["campaign_id"], r["amazon_campaign_id"])
    return out


def map_target_status(raw_json) -> str:
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        return "UNKNOWN"
    raw_state = raw.get("Stato") or raw.get("Stato del target")             # report legacy: "Stato"; report globali Amazon: "Stato del target"
    return TARGET_STATUS_MAP.get(str(raw_state or "").strip().upper(), "UNKNOWN")


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
    if a:                                                  # asin= resta 'asin:' (chiavi storiche); asin-expanded= ha la propria chiave
        return f"{book_id}|{campaign_id}|{product_kind(target)}:{a.upper()}"
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
    # DIFESA IN PROFONDITA' del gate zero-order: le raccomandazioni analitiche salvate possono venire da un motore con regole piu' vecchie (processo
    # Streamlit con moduli gia' caricati). Un bid-down ZERO-ORDER sotto i gate (clic o spesa/royalty) diventa WATCH anche qui, qualunque sia l'origine.
    z = ctx.rules.get("zero_orders") or {}
    if (action in ACTION_OF.values() and action != "PAUSE_CANDIDATE" and rec["rule_classification"] == "ZERO_ORDER_WASTE" and m.orders == 0 and z
            and (m.clicks < z.get("bid_down_min_clicks", 0) or m.spend < z.get("bid_down_min_spend_mult", 0) * R)):
        action, conf = "WATCH", conf
        note = (f"zero-order sotto i gate di riduzione ({m.clicks:g} clic < {z.get('bid_down_min_clicks')} o spesa {sym()}{m.spend:.2f} < "
                f"{z.get('bid_down_min_spend_mult')}x royalty): monitorare")
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
    trend_t: list = field(default_factory=list)    # Target di finestre 7D (SHORT_TREND): solo overlay di trend, mai evidenza decisionale
    trend_s: list = field(default_factory=list)    # Search Term 7D con termine == target (fallback del solo overlay)


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
    amz_cid_of: dict = field(default_factory=dict)          # campaign_id interno -> ID campagna Amazon (assente = campagna legacy senza ID Amazon)
    acct_of: dict = field(default_factory=dict)             # campaign_id interno -> (ID account Amazon Ads, nome account) (assente = legacy_account_unknown)

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
    srows, bad_st = split_search_terms(srows)                           # metriche impossibili: escluse (cross-campaign/CVR), avviso dati
    ds.warnings += impossible_warnings(bad_st)
    ds.acct_of = _account_of(conn, book_id, trows + srows)          # ogni decisione risale al suo account Amazon Ads
    ds.amz_cid_of = _amazon_campaign_ids(conn, book_id, trows + srows)      # ...e alla sua campagna Amazon (ID), per distinguere campagne dello stesso tipo
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
    status_of, _st_key, win_status, _win_key = {}, {}, {}, {}
    for t in trows:
        k = (t["campaign_id"], amz.target_identity(t["target"]))
        if t["report_scope"] != "LIFETIME":
            # report globali Amazon (nessun lifetime): lo stato e' nella colonna "Stato del target" del report a finestra piu' recente (30D prima del 7D). I report
            # legacy a finestra NON entrano qui: il comportamento storico (stato dal solo lifetime) resta identico.
            if "Stato del target" in str(t.get("raw_json") or ""):
                wkey = (t["date_to"] or "", days_inclusive(t["date_from"], t["date_to"]) or 0, t["id"])
                if k not in _win_key or wkey > _win_key[k]:
                    _win_key[k], win_status[k] = wkey, map_target_status(t.get("raw_json"))
            continue
        key = (t["date_to"] or "", t["id"])
        if k not in _st_key or key > _st_key[k]:
            _st_key[k], status_of[k] = key, map_target_status(t.get("raw_json"))
    for k, v in win_status.items():
        status_of.setdefault(k, v)

    # ---- raggruppamento in entita'
    ents: dict[str, Entity] = {}

    def ent(campaign_id, target, kind=None, term=None, campaign_type=None):
        key = action_key(book_id, campaign_id, target, kind, term, campaign_type)
        if key not in ents:
            k = kind or ("asin" if product_asin(target) else "cat" if target_type(target) == "PRODUCT_TARGET"
                         else "auto" if target_type(target, campaign_type) == "AUTO_TARGETING_GROUP" else "kw")
            ents[key] = Entity(key, campaign_id, k, target)
        return ents[key]

    short_max = ctx.rules["snapshots"].get("short_trend_max_days", 10)
    trend_rows = []                                  # righe 7D: non entrano in nessuna evidenza decisionale (L/30D/contesto/delivery/figli/negative)
    for rec in recs:
        ct = rec.get("campaign_type")
        if rec["report_scope"] == "DATE_RANGE" and (days_inclusive(rec["date_from"], rec["date_to"]) or 10 ** 6) <= short_max:
            trend_rows.append(rec)
            continue
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
    for rec in trend_rows:                           # le righe 7D si agganciano solo a entita' gia' esistenti (mai una decisione creata dal solo 7D)
        ct = rec.get("campaign_type")
        if rec["report_type"] == "TARGETING":
            e7, dest = ents.get(action_key(book_id, rec["campaign_id"], rec["target"], None, None, ct)), "trend_t"
        elif term_matches_target(rec["search_term"], rec["target"], ct):
            e7, dest = ents.get(action_key(book_id, rec["campaign_id"], rec["target"], None, None, ct)), "trend_s"
        else:
            e7 = dest = None
        if e7 is not None:
            getattr(e7, dest).append(rec)
    ds.accounting["trend_7d_rows"] = len(trend_rows)
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
            ident_a = amz.target_identity(a.get("target")) if a.get("target") and product_asin(a["target"]) else a["target_normalized"]
            amz_by_target[(a["campaign_id"], ident_a)].append(a)
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
    # periodo 7D piu' recente importato per QUALUNQUE campagna del libro: una campagna assente da quel report ha dati 7D NON DISPONIBILI (MISSING_7D), non zero traffico
    book_trend_pid = None
    if not (cc.get("trend_pid") or cc.get("trend_st_pid")):
        cands = [(i["date_to"] or "", p) for c in camp_ctx.values() for p, i in c["snaps"]["TARGETING"].items() if i["role"] == "SHORT_TREND"]
        book_trend_pid = max(cands)[1] if cands else None
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

    # ---- TREND 7D (overlay temporale, vedi kdp_ads/trend.py): conferma/smentisce/rende incerta la lettura 30D; mai decisionale da solo
    tr_cfg = TR.config(ctx.rules)
    trend = None
    # RIGA ASSENTE != ZERO TRAFFICO: NO_TRAFFIC_7D solo se il report 7D contiene davvero la riga del target (con 0 clic); altrimenti MISSING_7D (nessuna riga 7D per questo
    # target, oppure campagna assente dal 7D, oppure fonte Search Term che non rappresenta tutto il traffico del target): nessun effetto sulla decisione.
    t7_pid = cc.get("trend_pid") or cc.get("trend_st_pid") or book_trend_pid
    if tr_cfg["enabled"] and e.kind != "neg" and t7_pid:
        from_targeting = bool(cc.get("trend_pid"))
        pool = e.trend_t if from_targeting else e.trend_s
        t7_rec = next((r for r in pool if win.rec_period(r) == t7_pid), None)
        if t7_rec is None or (not from_targeting and not (t7_rec["clicks"] or 0)):
            trend = {"state": "MISSING_7D", "evidence": None, "m": None, "period": t7_pid, "weak30": False,
                     "note": "nessuna riga 7D per questo target: i dati 7D non sono disponibili (l'assenza NON significa zero traffico)"}
        else:
            m7 = compute_metrics(t7_rec["impressions"], t7_rec["clicks"], t7_rec["spend"], t7_rec["orders"], t7_rec["sales"], R)
            ev7 = TR.evidence_7d(m7.clicks, m7.spend, R, tr_cfg)
            weak30 = P is not None and TR.weak_30d(P.m.acos, P.m.orders, P.m.clicks, P.step, P.action, ctx.target_acos)
            t_state, t_note = TR.classify_trend(ev7, m7.acos, m7.orders, weak30, ctx.target_acos, life_step >= 100 or p_step >= 100)
            trend = {"state": t_state, "evidence": ev7, "m": m7, "period": t7_pid, "note": t_note, "weak30": weak30}
    trend_watch = False

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
    elif (life_step > 0 and p_step == 0 and Tv is not None and Tv.action == "WATCH" and Tv.m.orders == 0 and Tv.m.clicks > 0
          and ((Rc is not None and Rc.m.acos is not None and Rc.m.acos <= ctx.target_acos and Rc.m.orders >= cfg["recent_good_min_orders"])
               or (life_step <= 5 and L.m.orders > 0))):
        # Recente zero-order ancora SOTTO i gate di riduzione (clic o spesa/royalty): niente scorciatoia verso il bid-down dal solo storico.
        #  - se un periodo recente VALIDO e' profittevole (ACOS <= obiettivo), oppure
        #  - se l'eccesso storico e' solo lieve (passo -5%, storico con acquisti): non e' un'evidenza forte e indipendente.
        # Un lifetime zero-order (0 acquisti) o con eccesso forte (passo >= 10%) resta una evidenza propria e mantiene il bid-down.
        good = Rc is not None and Rc.m.acos is not None and Rc.m.acos <= ctx.target_acos and Rc.m.orders >= cfg["recent_good_min_orders"]
        conf, rule, resolved = cap((Rc if good else Tv).conf, "MEDIUM"), "C2D_ZERO_ORDER_UNDER_GATE_WATCH", "WATCH"
        flags.append("RECENT_GOOD_ZERO_ORDER_WATCH" if good else "LIFETIME_MILD_ZERO_ORDER_WATCH")
        logic = (f"Storico {L.action} (ACOS {'n/d' if L.m.acos is None else format(L.m.acos, '.1f') + '%'}), "
                 + (f"periodo recente profittevole (ACOS {Rc.m.acos:.1f}% con {Rc.m.orders:g} acquisti) e " if good else "eccesso storico lieve e ")
                 + f"ultimo periodo senza acquisti ({Tv.m.clicks:g} clic, {Tv.m.spend:.2f}) ancora sotto i gate zero-order: nessun bid-down, monitorare.")
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
        elif _mild_deterioration(L, P, (bid_of.get((e.campaign_id, ident)) or {}).get("bid"), ctx, cfg):
            conf, rule, resolved = cap(P.conf, "MEDIUM"), "C4B_MILD_DETERIORATION_MONITOR", "WATCH"
            flags.append("MILD_DETERIORATION_MONITOR")
            logic = (f"Storico ancora profittevole (ACOS {L.m.acos:.1f}%, sopra l'obiettivo ma sotto il pareggio), recente peggiorato con poca evidenza "
                     f"(spesa {P.m.spend:.2f} < {cfg['mild_recent_spend_mult']:g}x royalty) e offerta gia' bassa ({bid_txt(bid_of.get((e.campaign_id, ident)))}): "
                     f"nessun bid-down meccanico, monitorare prima di ridurre.")
        else:
            step, rule = min(p_step, cfg["recent_deterioration_cap_step"]), "C4_RECENT_DETERIORATION"
            if (p_step >= 10 and P.conf == cfg["recent_deterioration_step10_needs_conf"] and signal == "DECLINING_SIGNAL"):
                step = 10
            conf = cap(P.conf, "MEDIUM")
            flags.append("RECENT_DETERIORATION")
            logic = (f"DETERIORAMENTO RECENTE: lifetime {L.action} (ACOS "
                     f"{'N/A' if L.m.acos is None else format(L.m.acos, '.1f') + '%'}) ma il periodo recente segnala {P.action}; "
                     f"le evidenze discordano quindi il passo e' limitato a -{step}% e la confidence a {conf}.")
    elif (Tv is not None and Tv.action == "WATCH" and Tv.m.orders == 0 and L is not None and L.action in ("HOLD", "WATCH")
          and _mild_deterioration(L, Tv, (bid_of.get((e.campaign_id, ident)) or {}).get("bid"), ctx, cfg)):
        # stesso caso di C4B, ma il recente (0 acquisti, spesa ancora sotto la soglia zero-order) e' gia' WATCH e non un bid-down
        conf, rule, resolved = cap(Tv.conf, "MEDIUM"), "C4B_MILD_DETERIORATION_MONITOR", "WATCH"
        flags.append("MILD_DETERIORATION_MONITOR")
        logic = (f"Storico ancora profittevole (ACOS {L.m.acos:.1f}%, sopra l'obiettivo ma sotto il pareggio), recente senza acquisti con poca spesa "
                 f"({Tv.m.spend:.2f} < {cfg['mild_recent_spend_mult']:g}x royalty) e offerta gia' bassa: nessun bid-down meccanico, monitorare prima di ridurre.")
    elif supports:
        step, rule = 5, "C5_CROSS_ONLY"
        conf = "MEDIUM"
        logic = "Nessun bid-down da lifetime/recente; il cross-campaign indica questa campagna materialmente peggiore sullo stesso termine: -5%."

    if trend and trend["m"] is not None and e.kind != "neg":
        t_state = trend["state"]
        m7 = trend["m"]
        t_txt = f"{m7.clicks:g} clic, {m7.orders:g} ordini, ACOS {'n/d' if m7.acos is None else format(m7.acos, '.1f') + '%'}"
        if t_state == "RECENT_RECOVERY" and rule == "C4_RECENT_DETERIORATION":
            # bid-down basato SOLO sul peggioramento 30D mentre lo storico e' sano e il 7D (evidenza sufficiente) e' tornato entro l'obiettivo: nessun bid-down, mai un aumento
            conf, rule, resolved, step = cap(conf, "MEDIUM"), "T2_RECENT_RECOVERY_NO_DOWN", "WATCH", 0
            flags = [f for f in flags if f != "RECENT_DETERIORATION"] + ["RECENT_RECOVERY_7D"]
            logic = (f"Il 30D e' peggiorato ma negli ultimi 7 giorni ({t_txt}, evidenza {trend['evidence']}) il target e' tornato entro l'obiettivo con conversioni "
                     "e lo storico e' sano: bid-down sospeso (recupero recente), monitorare. Nessun aumento dell'offerta.")
        elif (t_state == "CONFIRMED_DECLINE" and rule == "C0_NO_DOWN" and resolved is None and step == 0 and not tac_good
              and P is not None and P.m.clicks >= ctx.rules["zero_orders"]["insufficient_clicks_below"]):
            # peggioramento confermato da 30D + 7D (evidenza forte): al massimo una riduzione prudente -5% (mai -10/-15 dal solo 7D), che passa dagli stessi controlli (delivery, stato target)
            step, rule, conf = 5, "T1_TREND_CONFIRMED_DECLINE", "MEDIUM"
            flags.append("TREND_CONFIRMED_DECLINE")
            logic = (f"PEGGIORAMENTO CONFERMATO: 30D sopra l'obiettivo (ACOS {'n/d' if P.m.acos is None else format(P.m.acos, '.1f') + '%'}, {P.m.clicks:g} clic) e 7D con "
                     f"evidenza {trend['evidence']} ancora sfavorevole ({t_txt}): riduzione prudente -5% (il 7D da solo non decide, mai oltre -5%).")
        elif t_state == "WATCH_RECENT" and rule == "C0_NO_DOWN" and resolved is None:
            trend_watch = True

    strong_cut = False
    if e.kind != "neg" and step > 0:
        _bid = (bid_of.get((e.campaign_id, ident)) or {}).get("bid")
        _tst = (status_of or {}).get((e.campaign_id, ident), "UNKNOWN")
        if rule in ("C1_DOWN_CONFIRMED", "C2_LIFETIME_DOWN", "C2B_LIFETIME_DOWN_RECOVERING") and _no_recent_traffic(Tv, tpid, _bid, _tst, ctx, cfg):
            conf, rule, resolved, step = cap(conf, "MEDIUM"), "C2C_NO_RECENT_TRAFFIC", "WATCH", 0
            flags.append("NO_RECENT_TRAFFIC")
            logic = (f"Storicamente da ridurre, ma negli ultimi periodi il target non ha traffico ne' spesa e l'offerta e' gia' bassa ({bid_txt({'bid': _bid})}): "
                     "nessun bid-down utile, verificare l'erogazione.")
        else:
            cut = _strong_cut(L, P, ctx, cfg) if rule in ("C1_DOWN_CONFIRMED", "C2_LIFETIME_DOWN") else 0
            if cut > step:
                logic += (f" Problema forte e confermato (storico ACOS {L.m.acos:.1f}% e recente {P.m.acos:.1f}% sopra il pareggio, recente >= "
                          f"{cfg['strong_cut_min_recent_acos_mult']:g}x obiettivo, {P.m.clicks:g} clic recenti): riduzione {cut}% invece di {step}%.")
                step, strong_cut = cut, True
                flags.append("STRONG_CONFIRMED_CUT")
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
                if ((eff_sig == "DELIVERY_DECLINING" or (inherited and eff_sig in dcfg["review_on"])) and step > dcfg["declining_cap_step"]
                        and not strong_cut):
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
            if (cfg.get("recent_zero_orders_watch_enabled", True) and Tv is not None and Tv.action == "WATCH" and Tv.m.orders == 0 and Tv.m.clicks > 0
                    and L is not None and L.action == "HOLD" and base is not Tv):
                # storico in HOLD ma nel periodo tattico clic e spesa SENZA acquisti (ancora sotto le soglie zero-order di bid-down): monitorare,
                # non 'tutto a posto'. Nessuna riduzione dell'offerta: il campione recente e' ancora piccolo.
                base, final_action, conf, rule = Tv, "WATCH", cap(Tv.conf, "MEDIUM"), "C0W_RECENT_ZERO_ORDERS_WATCH"
                flags.append("RECENT_ZERO_ORDERS_WATCH")
                logic = (f"Storico in HOLD (ACOS {'n/d' if L.m.acos is None else format(L.m.acos, '.1f') + '%'}), ma nel periodo recente {Tv.m.clicks:g} clic e "
                         f"{Tv.m.spend:.2f} di spesa senza acquisti, ancora sotto le soglie per ridurre l'offerta: monitorare.")
            if trend_watch and final_action == "HOLD":
                final_action, rule = "WATCH", "T3_RECENT_WATCH"
                flags.append("TREND_WATCH_RECENT")
                logic += f" TREND 7D: {trend['note']}: da monitorare invece di HOLD."
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
        "campaign_type": base_rec["campaign_type"], "amazon_account_id": (ds.acct_of.get(e.campaign_id) or (None, None))[0],
        "amazon_account_name": (ds.acct_of.get(e.campaign_id) or (None, None))[1], "amazon_campaign_id": ds.amz_cid_of.get(e.campaign_id), "target": e.target, "target_type": tgt_type,
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
        "trend_state": trend["state"] if trend else None, "trend_evidence": trend["evidence"] if trend else None,
        "trend_period": trend["period"] if trend else None, "trend_clicks": trend["m"].clicks if trend and trend["m"] else None,
        "trend_orders": trend["m"].orders if trend and trend["m"] else None, "trend_spend": trend["m"].spend if trend and trend["m"] else None,
        "trend_sales": trend["m"].sales if trend and trend["m"] else None, "trend_acos": trend["m"].acos if trend and trend["m"] else None,
        "trend_note": trend["note"] if trend else None,
        "manual_approval_required": 1, "status": "PROPOSED", "status_updated_at": now,
        "_loss": basis.est_loss, "_growth": bool(growth), "_ev": {"L": L, "T": Tv, "R": Rc, "neg": negs, "child": e.child, "cross": xs},
    }
    return d
