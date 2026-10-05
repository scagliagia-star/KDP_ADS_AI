"""Metriche derivate e normalizzazione termini. Nessuna decisione qui."""
import re
from contextvars import ContextVar
from dataclasses import dataclass

_SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "CA$", "AUD": "A$"}
_SYM: ContextVar[str] = ContextVar("currency_symbol", default="$")


def currency_symbol(code: str | None) -> str:
    """Simbolo mostrato accanto agli importi del marketplace (USD $, GBP £, EUR €, ...); altre valute: codice + spazio.
    Solo presentazione: gli importi non vengono mai convertiti fra valute."""
    c = str(code or "USD").strip().upper()
    return _SYMBOLS.get(c, f"{c} ")


def set_currency(code: str | None) -> None:
    _SYM.set(currency_symbol(code))


def sym() -> str:
    return _SYM.get()


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


def product_kind(target: str | None) -> str | None:
    """Tipo di targeting per prodotto: 'asin' oppure 'asin-expanded' (None se non e' un target ASIN). Fa parte dell'IDENTITA' del target."""
    m = _ASIN_TARGET.match(target or "")
    return m.group(1).lower() if m else None


def product_key(target: str | None) -> str | None:
    """Chiave di identita' di un product target: 'asin:<asin>' o 'asin-expanded:<asin>'. Lo stesso ASIN con tipo diverso sono target DIVERSI
    (stato, offerta, metriche e decisione separati). La stringa originale Amazon non viene mai modificata."""
    k = product_kind(target)
    return f"{k}:{product_asin(target)}" if k else None


# Gruppi di targeting AUTOMATICO Amazon (colonna 'Gruppi di annunci con targeting automatico'): NON sono keyword digitate dall'utente.
AUTO_CAMPAIGN_TYPES = frozenset({"AUTO", "OTHER"})
AUTO_TARGET_GROUPS = frozenset({"loose-match", "close-match", "complements", "substitutes"})
AUTO_TARGETING_GROUP = "AUTO_TARGETING_GROUP"


def is_auto_group(target: str | None, campaign_type: str | None) -> bool:
    """True solo per i 4 gruppi Auto DENTRO una campagna AUTO/OTHER: la stessa stringa in una campagna EXACT/PHRASE resta una keyword."""
    return (campaign_type or "").strip().upper() in AUTO_CAMPAIGN_TYPES and normalize_term(target) in AUTO_TARGET_GROUPS


def target_type(target: str | None, campaign_type: str | None = None) -> str:
    """PRODUCT_TARGET per ASIN/categorie (mai trasformati in keyword), AUTO_TARGETING_GROUP per i gruppi Auto
    (richiede campaign_type AUTO/OTHER), altrimenti KEYWORD_TARGET.
    Per un Search Term il tipo e' quello del TARGET che l'ha catturato: il termine resta un search term reale, il gruppo Auto che
    l'ha generato (campo target) non e' mai confuso con la keyword che in futuro se ne potra' ricavare."""
    if product_asin(target) or _CATEGORY_TARGET.match(target or ""):
        return "PRODUCT_TARGET"
    if is_auto_group(target, campaign_type):
        return AUTO_TARGETING_GROUP
    return "KEYWORD_TARGET"


def term_matches_target(search_term: str | None, target: str | None, campaign_type: str | None = None) -> bool:
    """True se il termine e' esattamente il target (stessa identita' normalizzata; per gli ASIN: il prodotto corrispondente).
    Un gruppo Auto non e' una query: nessun termine 'coincide' con esso."""
    if is_auto_group(target, campaign_type):
        return False
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
