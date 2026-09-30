"""RULE ENGINE V1 - deterministico. Produce solo RACCOMANDAZIONI (candidate); non esegue nulla.
Dati economici (royalty, target ACOS, break-even) sempre da BookCtx (books.json), mai hardcodati."""
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import Metrics

CONF_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
ACTIONS = ("NO_ACTION", "WATCH", "HOLD", "BID_UP_5_CANDIDATE", "BID_DOWN_5_CANDIDATE", "BID_DOWN_10_CANDIDATE",
           "BID_DOWN_15_CANDIDATE", "PAUSE_CANDIDATE", "NEGATIVE_CANDIDATE", "INSUFFICIENT_DATA")
BID_DOWN = {5: "BID_DOWN_5_CANDIDATE", 10: "BID_DOWN_10_CANDIDATE", 15: "BID_DOWN_15_CANDIDATE"}


# ------------------------------------------------------------------ configurazione
def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_rules(path, overrides: dict | None = None) -> dict:
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    rules = {k: v for k, v in rules.items() if not k.startswith("_")}
    return deep_merge(rules, overrides or {})


@dataclass
class BookCtx:
    book_id: str
    title: str
    marketplace: str
    currency: str
    price: float
    royalty: float
    target_acos: float
    break_even_acos: float
    phase: str          # letta e portata negli output; V1 non varia le regole per fase
    rules: dict

    @property
    def bands(self) -> dict:
        """Soglie ACOS effettive (%). strong<=target<=break-even garantito anche per libri con economie diverse."""
        w, r = self.rules["with_orders"], self.rules
        strong = min(w["winner_max_acos_pct"], self.target_acos)
        mild = max(w["bid_down_5_max_acos_pct"], self.break_even_acos + 1)
        unprof = max(w["bid_down_10_max_acos_pct"], mild + 1)
        return {"strong": strong, "target": self.target_acos, "break_even": self.break_even_acos,
                "mild": mild, "unprofitable": unprof}


def ctx_from_book(book: dict, eco: dict, rules_base: dict) -> BookCtx:
    rules = deep_merge(rules_base, book.get("rule_overrides"))
    return BookCtx(str(book["book_id"]), book["title"], book["marketplace"], book["currency"],
                   float(book["price"]), float(book["royalty_per_copy"]), float(book["target_acos"]),
                   float(eco["break_even_acos"]), book["phase"], rules)


# ------------------------------------------------------------------ classificazioni
def economic_classification(acos: float | None, ctx: BookCtx) -> str:
    """Distingue TARGET ACOS (obiettivo operativo) da BREAK-EVEN ACOS (limite economico)."""
    if acos is None:
        return "NO_SALES"          # sales == 0: ACOS non definito, MAI trattato come 0/ottimo
    b = ctx.bands
    if acos <= b["strong"]:
        return "STRONG"
    if acos <= b["target"]:
        return "ON_TARGET"
    if acos <= b["break_even"]:
        return "PROFITABLE_BUT_ABOVE_TARGET"
    if acos <= b["mild"]:
        return "MILDLY_UNPROFITABLE"
    if acos <= b["unprofitable"]:
        return "UNPROFITABLE"
    return "HIGHLY_UNPROFITABLE"


def sample_classification(orders: float, rules: dict) -> str:
    """Solo dagli ordini (0-2 LOW, 3-9 MEDIUM, >=10 HIGH). La confidence integra anche clic e spesa."""
    c = rules["confidence"]
    return "HIGH" if orders >= c["orders_high_from"] else "MEDIUM" if orders >= c["orders_medium_from"] else "LOW"


def calculate_confidence(orders: float, clicks: float, spend: float, royalty: float, rules: dict) -> str:
    """Confidence LOW/MEDIUM/HIGH della raccomandazione.

    Base dagli ordini: 0-2 LOW, 3-9 MEDIUM, >=10 HIGH. Poi si integrano clic e spesa, quindi gli ordini
    non bastano da soli:
      * 0 ordini: la confidence misura quanto e' solido il giudizio di "spreco" (non la conversione):
          HIGH   se clic >= zero_orders_high_clicks, oppure (clic >= zero_orders_high_clicks_with_spend
                 e spesa >= zero_orders_high_spend_mult * royalty);
          MEDIUM se clic >= zero_orders_medium_clicks;
          LOW    altrimenti (es. 2 clic).
      * 1-2 ordini: LOW, salvo MEDIUM se i clic sono molti (>= few_orders_upgrade_clicks).
      * 3-9 ordini: MEDIUM; HIGH se clic >= medium_upgrade_clicks; LOW se clic < medium_downgrade_clicks_below
        (tasso di conversione anomalo su pochi clic).
      * >=10 ordini: HIGH; MEDIUM se clic < high_downgrade_clicks_below.
    Ritorna sempre una stringa in {LOW, MEDIUM, HIGH}."""
    c = rules["confidence"]
    if orders <= 0:
        if clicks >= c["zero_orders_high_clicks"] or (
                clicks >= c["zero_orders_high_clicks_with_spend"]
                and spend >= c["zero_orders_high_spend_mult"] * royalty):
            return "HIGH"
        return "MEDIUM" if clicks >= c["zero_orders_medium_clicks"] else "LOW"
    if orders < c["orders_medium_from"]:
        return "MEDIUM" if clicks >= c["few_orders_upgrade_clicks"] else "LOW"
    if orders < c["orders_high_from"]:
        if clicks >= c["medium_upgrade_clicks"]:
            return "HIGH"
        return "LOW" if clicks < c["medium_downgrade_clicks_below"] else "MEDIUM"
    return "MEDIUM" if clicks < c["high_downgrade_clicks_below"] else "HIGH"


# ------------------------------------------------------------------ decisione
@dataclass
class Decision:
    action: str
    bid_change_pct: int | None
    rule_classification: str
    rule_id: str
    reason: str
    confidence: str
    economic: str
    sample: str
    flags: list = field(default_factory=list)


def _money(x):
    return f"${x:,.2f}"


def _num(m: Metrics, ctx: BookCtx) -> str:
    acos = f"{m.acos:.1f}%" if m.acos is not None else "N/A (NO_SALES)"
    return (f"{m.orders:g} orders, {m.clicks:g} clicks, spend {_money(m.spend)}, sales {_money(m.sales)}, ACOS {acos} "
            f"(target {ctx.target_acos:g}%, break-even {ctx.break_even_acos:g}%), "
            f"est. direct ad profit {_money(m.est_profit)}")


def decide(m: Metrics, ctx: BookCtx, report_type: str, action_scope: str = "TARGET",
           campaign_avg_cvr: float | None = None) -> Decision:
    """action_scope: 'TARGET' se il bid change riguarda esattamente questa riga (target, o termine == target);
    'PARENT_TARGET' se la riga e' un search term diverso dal target che l'ha catturato: un bid change
    colpirebbe anche altri termini serviti dallo stesso target."""
    R, rules = ctx.royalty, ctx.rules
    z, w, bu = rules["zero_orders"], rules["with_orders"], rules["bid_up"]
    conf = calculate_confidence(m.orders, m.clicks, m.spend, R, rules)
    eco = economic_classification(m.acos, ctx)
    smp = sample_classification(m.orders, rules)
    nums = _num(m, ctx)
    flags: list[str] = []
    scope_note = ("" if action_scope == "TARGET" else
                  " Il termine differisce dal target che l'ha catturato: un bid change colpirebbe anche altri termini "
                  "di quel target, valutare la riga Target.")

    def out(action, pct, klass, rid, why):
        return Decision(action, pct, klass, rid, f"{why} [{nums}] [rule {rid}]", conf, eco, smp, flags)

    # ---------------------------------------------------------------- 0 ordini
    if m.orders == 0:
        if m.spend >= z["high_cpc_spend_mult"] * R and m.clicks < z["sample_clicks_min"]:
            flags.append("HIGH_CPC")
        hc = (f" HIGH_CPC: spesa {_money(m.spend)} >= {z['high_cpc_spend_mult']:g}x royalty con soli {m.clicks:g} clic "
              f"(CPC {_money(m.cpc or 0)}).") if "HIGH_CPC" in flags else ""
        if m.clicks < z["insufficient_clicks_below"]:
            return out("NO_ACTION", None, "INSUFFICIENT_DATA", "Z1",
                       f"0 ordini con soli {m.clicks:g} clic (< {z['insufficient_clicks_below']}): dati insufficienti." + hc)
        if m.clicks <= z["watch_clicks_max"]:
            return out("WATCH", None, "WATCH", "Z2",
                       f"0 ordini con {m.clicks:g} clic ({z['insufficient_clicks_below']}-{z['watch_clicks_max']}): monitorare." + hc)
        if m.spend >= z["pause_spend_mult"] * R:
            flags.append("ZERO_ORDER_WASTE")
            if report_type == "SEARCH_TERM":
                return out("NEGATIVE_CANDIDATE", None, "ZERO_ORDER_WASTE", "Z5N",
                           f"0 ordini, {m.clicks:g} clic, spesa {_money(m.spend)} >= {z['negative_spend_mult']:g}x royalty "
                           f"({_money(z['negative_spend_mult'] * R)}): candidato a negative (raccomandazione separata dalla pausa).")
            return out("PAUSE_CANDIDATE", None, "ZERO_ORDER_WASTE", "Z5",
                       f"0 ordini, {m.clicks:g} clic, spesa {_money(m.spend)} >= {z['pause_spend_mult']:g}x royalty "
                       f"({_money(z['pause_spend_mult'] * R)}): candidato alla pausa (solo raccomandazione).")
        if m.spend >= z["bid_down_10_spend_mult"] * R:
            flags.append("ZERO_ORDER_WASTE")
            return out("BID_DOWN_10_CANDIDATE", -10, "ZERO_ORDER_WASTE", "Z4",
                       f"0 ordini, {m.clicks:g} clic, spesa {_money(m.spend)} tra 1x e {z['pause_spend_mult']:g}x royalty." + scope_note)
        flags.append("ZERO_ORDER_WASTE")
        return out("BID_DOWN_5_CANDIDATE", -5, "ZERO_ORDER_WASTE", "Z3",
                   f"0 ordini, {m.clicks:g} clic (>= {z['sample_clicks_min']}), spesa {_money(m.spend)} < 1x royalty." + scope_note)

    # ---------------------------------------------------------------- con ordini
    if m.acos is None:
        flags.append("DATA_ANOMALY")
        return out("WATCH", None, "DATA_ANOMALY", "D1", "Ordini > 0 ma vendite = 0: dato incoerente, verificare il CSV.")
    a, b = m.acos, ctx.bands
    n = m.orders

    if n >= w["winner_min_orders"] and a <= b["strong"]:
        promising = n < w["high_volume_orders"] and a <= w["promising_max_acos_pct"]
        klass = "PROMISING_WINNER" if promising else "WINNER"
        rid = "W2" if promising else "W1"
        base = (f"{'Promettente (' + format(n, 'g') + ' ordini < ' + str(w['high_volume_orders']) + ', nessun aumento aggressivo)' if promising else 'Winner'}: "
                f"ACOS {a:.1f}% <= {b['strong']:g}%.")
        # BID_UP_5 solo se TUTTE le condizioni sono vere (mai con < 3 ordini)
        ok, why_not = True, []
        if action_scope != "TARGET":
            ok = False; why_not.append("riga con termine diverso dal target")
        if n < bu["min_orders"]:
            ok = False; why_not.append("ordini insufficienti")
        if CONF_ORDER[conf] < CONF_ORDER[bu["min_confidence"]]:
            ok = False; why_not.append(f"confidence {conf} < {bu['min_confidence']}")
        if a > bu["max_acos_pct_of_target"] * ctx.target_acos:
            ok = False; why_not.append(f"ACOS non significativamente sotto target (soglia {bu['max_acos_pct_of_target'] * ctx.target_acos:.1f}%)")
        if a * (1 + bu["step_pct"] / 100) > ctx.target_acos:
            ok = False; why_not.append("CPC non sostenibile: ACOS dopo +5% supererebbe il target")
        if m.cvr is not None and (m.cvr > bu["max_cvr_pct"] or (
                campaign_avg_cvr and m.cvr > bu["max_cvr_vs_campaign_avg"] * campaign_avg_cvr)):
            ok = False; why_not.append(f"conversion rate anomala ({m.cvr:.1f}%)")
        if ok:
            return out("BID_UP_5_CANDIDATE", bu["step_pct"], klass, rid + "U",
                       base + f" Condizioni per +{bu['step_pct']}% soddisfatte (conf {conf}, ACOS ben sotto target, CVR {m.cvr:.1f}%, CPC sostenibile).")
        return out("HOLD", None, klass, rid, base + f" HOLD, nessun aumento ({'; '.join(why_not)}).")

    if n < w["hold_min_orders"]:                     # 1-4 ordini non winner: campione troppo piccolo per agire
        flags.append("LOW_SAMPLE")
        if n <= w["promising_low_sample_max_orders"] and a < w["promising_max_acos_pct"]:
            return out("HOLD", None, "PROMISING_LOW_SAMPLE", "L1",
                       f"ACOS {a:.1f}% < {w['promising_max_acos_pct']:g}% ma solo {n:g} ordini: promettente, campione piccolo, nessun aumento.")
        if a <= b["break_even"]:
            return out("HOLD", None, "LOW_SAMPLE_HOLD", "L2",
                       f"{n:g} ordini (< {w['hold_min_orders']}): campione troppo piccolo per agire, ACOS {a:.1f}% <= break-even.")
        if m.est_profit < 0:
            flags.append("LOSS_UNDER_LOW_SAMPLE")
        return out("WATCH", None, "LOW_SAMPLE_WATCH", "L3",
                   f"{n:g} ordini (< {w['hold_min_orders']}) con ACOS {a:.1f}% > break-even: campione piccolo, monitorare "
                   f"(nessun bid down finche' orders < {w['hold_min_orders']}).")

    # >= hold_min_orders
    if a <= b["target"]:
        return out("HOLD", None, "ON_TARGET_HOLD", "H1", f"ACOS {a:.1f}% tra {b['strong']:g}% e target {b['target']:g}%: HOLD.")
    if a <= b["break_even"]:
        return out("HOLD", None, "PROFITABLE_HOLD", "H2",
                   f"ACOS {a:.1f}% sopra target {b['target']:g}% ma sotto break-even {b['break_even']:g}%: profittevole, HOLD.")
    step = 5 if a <= b["mild"] else 10 if a <= b["unprofitable"] else 15
    step = min(step, w["max_bid_down_pct"])
    high_vol = n >= w["high_volume_orders"]
    if high_vol:
        flags.append("HIGH_VOLUME")
    klass = "HIGH_VOLUME_INEFFICIENT" if high_vol else "LOSER"
    safety = (f" Safety: >= {w['high_volume_orders']} ordini, mai pausa/negative per solo ACOS, massimo -{w['max_bid_down_pct']}%."
              if high_vol else "")
    return out(BID_DOWN[step], -step, klass, f"B{step}",
               f"ACOS {a:.1f}% > break-even {b['break_even']:g}%: {klass}, riduzione progressiva -{step}%." + safety + scope_note)


# ------------------------------------------------------------------ priorita'
def priority(action: str, m: Metrics, confidence: str, ctx: BookCtx) -> str:
    """P1-P4 non dal solo ACOS: usa perdita stimata (in multipli di royalty), ordini/spesa (via perdita) e confidence.
    P1: perdita >= p1*royalty e confidence HIGH | P2: perdita >= p2*royalty e confidence >= MEDIUM |
    P3: perdita >= p3*royalty (o bid-up con profitto stimato >= soglia e conf >= MEDIUM, o perdita nascosta >= p2) |
    P4: watch / insufficient data / hold / opportunita' minori."""
    p, R, loss = ctx.rules["priority"], ctx.royalty, m.est_loss
    if action.startswith("BID_DOWN") or action in ("PAUSE_CANDIDATE", "NEGATIVE_CANDIDATE"):
        if loss >= p["p1_loss_mult"] * R and confidence == "HIGH":
            return "P1"
        if loss >= p["p2_loss_mult"] * R and CONF_ORDER[confidence] >= 1:
            return "P2"
        return "P3" if loss >= p["p3_loss_mult"] * R else "P4"
    if action == "BID_UP_5_CANDIDATE":
        return "P3" if m.est_profit >= p["bid_up_p3_profit_mult"] * R and CONF_ORDER[confidence] >= 1 else "P4"
    return "P3" if loss >= p["p2_loss_mult"] * R else "P4"
