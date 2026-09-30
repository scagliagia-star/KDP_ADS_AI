"""Metriche derivate e normalizzazione termini. Nessuna decisione qui."""
import re
from dataclasses import dataclass

ESTIMATE_NOTE = ("STIMA: un ordine attribuito ad Amazon Ads non equivale automaticamente a una royalty KDP "
                 "effettivamente maturata (resi, prezzo di vendita, Kindle Unlimited, ecc.). Verificare sui report KDP.")

_ASIN_TARGET = re.compile(r'^\s*(asin(?:-expanded)?)\s*=\s*"?([a-z0-9]{10})"?\s*$', re.I)
_CATEGORY_TARGET = re.compile(r"^\s*category\s*=", re.I)


def normalize_term(s: str | None) -> str:
    """lowercase + trim + spazi multipli ridotti a uno. Nient'altro: niente stemming, typo, sinonimi, riordino parole."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def product_asin(target: str | None) -> str | None:
    m = _ASIN_TARGET.match(target or "")
    return m.group(2).lower() if m else None


def target_type(target: str | None) -> str:
    """PRODUCT_TARGET per ASIN/categorie (mai trasformati in keyword), altrimenti KEYWORD_TARGET."""
    if product_asin(target) or _CATEGORY_TARGET.match(target or ""):
        return "PRODUCT_TARGET"
    return "KEYWORD_TARGET"


def term_matches_target(search_term: str | None, target: str | None) -> bool:
    """True se il termine e' esattamente il target (stessa identita' normalizzata; per gli ASIN: il prodotto corrispondente)."""
    t = normalize_term(search_term)
    if not t:
        return False
    if t == normalize_term(target):
        return True
    a = product_asin(target)
    return bool(a and t == a)


def _div(a, b):
    return a / b if b else None


@dataclass
class Metrics:
    impressions: float
    clicks: float
    spend: float
    orders: float
    sales: float
    ctr: float | None
    cpc: float | None
    cvr: float | None
    acos: float | None          # % ; None se sales == 0 (NO_SALES: mai 0)
    roas: float | None
    est_royalty: float
    est_profit: float
    profit_per_order: float | None

    @property
    def est_loss(self) -> float:
        return max(0.0, -self.est_profit)


def compute_metrics(impressions, clicks, spend, orders, sales, royalty) -> Metrics:
    impressions, clicks, spend, orders, sales = (float(x or 0) for x in (impressions, clicks, spend, orders, sales))
    ctr = _div(clicks, impressions)
    cvr = _div(orders, clicks)
    acos = _div(spend, sales) if sales > 0 else None
    est_royalty = orders * royalty
    profit = est_royalty - spend
    return Metrics(
        impressions, clicks, spend, orders, sales,
        ctr * 100 if ctr is not None else None,
        _div(spend, clicks),
        cvr * 100 if cvr is not None else None,
        acos * 100 if acos is not None else None,
        _div(sales, spend),
        est_royalty, profit,
        _div(profit, orders) if orders > 0 else None)
