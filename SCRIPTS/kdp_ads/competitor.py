"""RICERCA COMPETITOR: analisi multi-competitor dei dati Helium 10 Cerebro. Funzioni PURE (nessun DB, nessuna rete, nessuna scrittura, nessun LLM).

FONTE ESTERNA / STIMATA. Nulla qui diventa una decisione Amazon Ads: nessuna keyword Cerebro e' mai Exact, negativa, cambio offerta o pausa. Gli output
sono solo SUGGERIMENTI DI RICERCA/TEST ("TESTA IN FRASE") e cluster "DA APPROFONDIRE". analyze/decide di Amazon Ads non importano questo modulo.

STRATEGIA DI AGGREGAZIONE (stessa keyword normalizzata su piu' competitor; il dettaglio per competitor resta in `per_competitor`):
  rank (organico/sponsorizzato)        -> best = MINIMO; median_organic_rank = mediana dei soli competitor che hanno un rank
  search_volume, keyword_sales,
  title_density, competing_products,
  cerebro_iq, ppc_bid                  -> MEDIANA dei valori presenti (search_volume_max = massimo, per trasparenza)
  ppc_bid_min / ppc_bid_max            -> minimo dei minimi / massimo dei massimi
  competitor_count                     -> numero di competitor (ASIN) in cui la keyword compare; share = count / competitor totali della ricerca

ADS OPPORTUNITY SCORE (0-100, trasparente, pesi in `DEFAULTS["ads_weights"]`, soglie in DEFAULTS, sovrascrivibili con rules["competitor_research"]):
  somma pesata di componenti 0..1 (overlap competitor, rank organico, sponsored, volume, rilevanza verso il libro, title density). Un componente non
  disponibile (es. nessun Title Density) e' ESCLUSO e i pesi degli altri sono riproporzionati: il dato mancante non e' trattato come buono ne' come cattivo.
  Penalita' sottratte e sempre elencate: keyword generica, brand/IP, gia' inefficiente nei dati reali, concorrenza estrema, gia' coperta da una frase piu' ampia.
  Il punteggio e' solo PRIORITA': il risultato espone sempre i fattori.

BOOK OPPORTUNITY SCORE (0-100, separato): sul CLUSTER di keyword: overlap competitor, forza rank organico, volume (distinto, non duplicato), title density,
  competing products, coerenza del cluster, numero di keyword correlate, distinzione dal libro origine. Sponsored Rank NON entra nel punteggio (non e' prova
  di opportunita' editoriale). Etichette DEBOLE / DA OSSERVARE / INTERESSANTE / FORTE; FORTE richiede >= 3 keyword e >= 2 competitor che confermano, e con
  ricerca poco affidabile (1-2 competitor) il massimo e' INTERESSANTE. Mai "profittevole": lo stato resta DA APPROFONDIRE.

CLUSTERING (deterministico, rule-based): token di contenuto (minuscolo, senza stopword, plurali ridotti) divisi in TOPIC (cosa), PUBBLICO (senior, woman, ...)
  e FORMATO (large print). Si raggruppa per (topic, pubblico); le keyword senza pubblico si uniscono al gruppo piu' grande dello stesso topic; i gruppi di una
  sola keyword si uniscono a un gruppo con topic piu' generico e stesso pubblico, altrimenti restano fuori. Il formato non divide i cluster ma entra
  nell'etichetta se presente in almeno meta' delle keyword. Le keyword BRAND/IP e gli ASIN sono esclusi dai cluster (restano visibili come segnali di mercato)."""
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict

from .cerebro import median
from .metrics import product_asin

DEFAULTS = {
    "min_competitors_for_ads": 2,             # keyword presente in >= 2 competitor (con una ricerca di 1 competitor: 1)
    "ads_min_score": 40,
    "ads_max_suggestions": 25,
    "ads_weights": {"overlap": 30, "organic": 20, "sponsored": 10, "volume": 20, "relevance": 15, "title_density": 5},
    "ads_label_high": 65, "ads_label_medium": 45,
    "organic_bands": [[10, 1.0], [25, 0.8], [50, 0.6], [100, 0.35], [10_000, 0.15]],        # (rank massimo, valore)
    "volume_bands": [[3000, 1.0], [1000, 0.8], [300, 0.55], [100, 0.3], [0, 0.1]],           # (volume minimo, valore)
    "title_density_bands": [[5, 1.0], [20, 0.7], [50, 0.4], [10_000_000, 0.1]],              # (densita' massima, valore)
    "competing_bands": [[1000, 1.0], [5000, 0.7], [20000, 0.45], [100000, 0.25], [1e12, 0.1]],
    "penalty_generic": 15, "penalty_inefficient": 30, "penalty_extreme": 10, "penalty_covered_phrase": 10,
    "extreme_title_density": 100, "extreme_competing_products": 200_000,
    "inefficient_min_clicks": 10,
    "min_cluster_keywords": 2,
    "book_weights": {"overlap": 25, "organic": 20, "volume": 20, "title_density": 10, "competing": 10, "coherence": 10, "breadth": 5, "distinct": 10},
    "book_min_volume_for_strong": 300,         # sotto questo volume (base: max(keyword piu' forte, meta' della somma)) il cluster non puo' essere FORTE
    "book_label_weak": 35, "book_label_watch": 50, "book_label_interesting": 68,
    "breadth_bands": [[8, 1.0], [5, 0.75], [3, 0.5], [2, 0.3], [0, 0.1]],
    "overlap_flag": 0.75,                      # sovrapposizione con un titolo/progetto esistente
    "similar_to_origin": 0.8,
}


def config(rules: dict | None = None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in ((rules or {}).get("competitor_research") or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


# ------------------------------------------------------------------ testo e token
STOP = frozenset("a an the for of and to in on with by my your from at is are as or &".split())
GENERIC = frozenset("book ebook kindle paperback hardcover best top new free cheap buy amazon online edition read reading series "
                    "sale clearance seller release audiobook discount price deal dollar today".split())       # intenti commerciali/temporali: non sono un tema editoriale
_YEAR = re.compile(r"^20\d\d$")
FORMAT_MODS = frozenset("large print bold jumbo giant".split())
AUDIENCE = frozenset("senior woman man adult beginner kid child teen teenager boy girl mom dad grandma grandpa grandmother grandfather "
                     "retiree widow veteran couple elder elderly".split())
_IRREG = {"women": "woman", "men": "man", "children": "child", "wives": "wife", "elders": "elder", "seniors": "senior", "kids": "kid"}
_KEEP = frozenset("series species news lens bonus focus bias plus gas christmas canvas atlas".split())


def stem(t: str) -> str:
    if t in _IRREG:
        return _IRREG[t]
    if t in _KEEP or len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith(("sses", "shes", "ches", "xes")):
        return t[:-2]
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokens(text) -> list:
    """Token stemmati in ordine, senza stopword (i token GENERIC restano: li filtra content_tokens)."""
    s = re.sub(r"['’]s\b", "", str(text or "").lower())
    return [stem(t) for t in re.findall(r"[a-z0-9]+", s) if t not in STOP]


def content_tokens(text) -> list:
    return [t for t in tokens(text) if t not in GENERIC and not _YEAR.match(t)]


def split_tokens(text) -> tuple:
    """(topic, pubblico, formato): liste ordinate di token di contenuto."""
    toks = content_tokens(text)
    aud = [t for t in toks if t in AUDIENCE]
    fmt = [t for t in toks if t in FORMAT_MODS]
    top = [t for t in toks if t not in AUDIENCE and t not in FORMAT_MODS]
    return top, aud, fmt


# ------------------------------------------------------------------ brand / IP
BRAND_SEED = (
    "golden girls", "harry potter", "star wars", "disney", "marvel", "pokemon", "minecraft", "bluey", "barbie", "lego", "nintendo", "hello kitty",
    "peppa pig", "paw patrol", "taylor swift", "bob ross", "dr seuss", "readers digest", "guideposts", "murder she wrote", "agatha christie",
    "sherlock holmes", "hercule poirot", "miss marple", "nancy drew", "hardy boys", "stephen king", "james patterson", "louise penny",
    "janet evanovich", "lee child", "danielle steel", "erma bombeck", "jimmy carter", "billy graham", "john grisham", "robyn carr", "nora roberts", "mike rowe", "hallmark", "columbo", "downton abbey", "game of thrones", "lord of the rings", "hunger games", "dungeons dragons",
    "scooby doo", "mickey mouse", "winnie the pooh", "peanuts", "snoopy", "garfield", "hoyle",
)
# Lista PRUDENTE di franchise/marchi molto riconoscibili. Termini generici (sudoku, crossword, ...) non ci sono. Il flag manuale per keyword
# (BRAND_OR_IP / non brand) vince sempre sulla lista.


def _seq_in(seq: list, sub: list) -> bool:
    n = len(sub)
    return n > 0 and any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def brand_match(keyword, brand_terms=BRAND_SEED) -> str | None:
    """Termine brand/franchise contenuto (come sequenza di parole intere) nella keyword, altrimenti None."""
    kt = tokens(keyword)
    for term in brand_terms:
        tt = tokens(term)
        if tt and _seq_in(kt, tt):
            return term
    return None


def is_asin_like(keyword) -> bool:
    k = re.sub(r"\s+", "", str(keyword or "")).lower()
    return bool(re.match(r"^(b0[a-z0-9]{8}|\d{9}[\dx])$", k) or product_asin(k))


# ------------------------------------------------------------------ aggregazione multi-competitor
def _band_ge(value, bands):
    for floor, val in bands:
        if value >= floor:
            return val
    return bands[-1][1]


def _band_le(value, bands):
    for cap, val in bands:
        if value <= cap:
            return val
    return bands[-1][1]


def rank_sort_key(v):
    return v if v is not None else 10 ** 9


def aggregate(sources: list, brand_terms=BRAND_SEED, brand_overrides: dict | None = None) -> dict:
    """`sources` = [{asin, rows:[{keyword, keyword_norm, <metriche>}]}] SOLO i file ATTIVI (un file per competitor).
    Ritorna {total, asins, keywords:[agg]} gia' ordinato come 'keyword comuni'. `brand_overrides` {keyword_norm: True|False} = flag manuale."""
    overrides = brand_overrides or {}
    total = len(sources)
    by_kw = defaultdict(dict)                                     # keyword_norm -> {asin: riga}
    surface = defaultdict(Counter)
    for s in sources:
        for r in s["rows"]:
            by_kw[r["keyword_norm"]][s["asin"]] = r
            surface[r["keyword_norm"]][r["keyword"]] += 1
    out = []
    for norm, per in by_kw.items():
        vals = lambda f: [o.get(f) for o in per.values() if o.get(f) is not None]       # noqa: E731
        org, spo = vals("organic_rank"), vals("sponsored_rank")
        text = sorted(surface[norm].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        manual = overrides.get(norm)
        seed = brand_match(norm, brand_terms)
        brand = bool(manual) if manual is not None else bool(seed)
        out.append({
            "keyword_norm": norm, "keyword": text, "competitor_count": len(per), "competitor_total": total,
            "competitor_share": f"{len(per)}/{total}", "share": len(per) / total if total else 0.0, "asins": sorted(per),
            "best_organic_rank": min(org) if org else None, "median_organic_rank": statistics.median(org) if org else None, "organic_rank_count": len(org),
            "best_sponsored_rank": min(spo) if spo else None, "sponsored_competitor_count": len(spo),
            "search_volume": median(vals("search_volume")), "search_volume_max": max(vals("search_volume"), default=None),
            "keyword_sales": median(vals("keyword_sales")), "title_density": median(vals("title_density")),
            "competing_products": median(vals("competing_products")), "cerebro_iq": median(vals("cerebro_iq")),
            "suggested_bid": median(vals("ppc_bid")), "bid_min": min(vals("ppc_bid_min"), default=None), "bid_max": max(vals("ppc_bid_max"), default=None),
            "per_competitor": {a: {k: v for k, v in o.items() if k not in ("raw", "keyword", "keyword_norm")} for a, o in per.items()},
            "brand_or_ip": brand, "brand_term": seed if brand and manual is None else None, "brand_manual": manual is not None,
            "is_asin_like": is_asin_like(norm), "generic_keyword": _generic_keyword(norm),
            **top10_fields(per),
        })
    out.sort(key=common_sort_key)
    return {"total": total, "asins": sorted(s["asin"] for s in sources), "keywords": out}


# ------------------------------------------------------------------ KEYWORD DI LANCIO (criterio operativo: almeno un competitor con rank 1-10)
TOP10 = 10
LAUNCH_CATEGORIES = ("BOTH", "ORGANIC", "SPONSORED")                       # in ordine di priorita'; NONE = non top 10
LAUNCH_LABELS = {"BOTH": "ORGANIC + SPONSORED TOP 10", "ORGANIC": "ORGANIC TOP 10", "SPONSORED": "SPONSORED TOP 10", "NONE": "NON TOP 10"}
LAUNCH_LETTER = {"BOTH": "A", "ORGANIC": "B", "SPONSORED": "C", "NONE": "D"}


def is_top10(rank) -> bool:
    """Rank 1-10. Mancante (None) e rank 0 o negativo NON sono top 10; l'11 e' fuori."""
    return rank is not None and 1 <= rank <= TOP10


def top10_fields(per: dict) -> dict:
    """Classificazione top 10 di una keyword da {asin: osservazione}: organic_top10 / sponsored_top10 su QUALSIASI competitor."""
    org = sorted(a for a, o in per.items() if is_top10(o.get("organic_rank")))
    spo = sorted(a for a, o in per.items() if is_top10(o.get("sponsored_rank")))
    o10, s10 = bool(org), bool(spo)
    return {"organic_top10": o10, "sponsored_top10": s10, "top10_category": "BOTH" if o10 and s10 else "ORGANIC" if o10 else "SPONSORED" if s10 else "NONE",
            "organic_top10_asins": org, "sponsored_top10_asins": spo, "top10_competitor_count": len(set(org) | set(spo))}


def common_sort_key(k):
    """Keyword comuni: 1) presenza su piu' competitor 2) rank organico 3) volume 4) title density / competing products. NON il solo Search Volume."""
    return (-k["competitor_count"], rank_sort_key(k["best_organic_rank"]), rank_sort_key(k["median_organic_rank"]), -(k["search_volume"] or 0),
            k["title_density"] if k["title_density"] is not None else 10 ** 9, k["competing_products"] if k["competing_products"] is not None else 10 ** 12,
            k["keyword_norm"])


def filter_common(keywords, min_competitors=1, min_volume=None, max_organic_rank=None, sponsored=None, max_title_density=None, text=None) -> list:
    """Filtri della sezione KEYWORD COMUNI. sponsored: True = solo con sponsored, False = solo senza, None = tutti. Un filtro su un dato mancante ESCLUDE la keyword."""
    q = (text or "").strip().lower()
    out = []
    for k in keywords:
        if k["competitor_count"] < min_competitors:
            continue
        if min_volume is not None and (k["search_volume"] is None or k["search_volume"] < min_volume):
            continue
        if max_organic_rank is not None and (k["best_organic_rank"] is None or k["best_organic_rank"] > max_organic_rank):
            continue
        if sponsored is not None and (k["sponsored_competitor_count"] > 0) != sponsored:
            continue
        if max_title_density is not None and (k["title_density"] is None or k["title_density"] > max_title_density):
            continue
        if q and q not in k["keyword_norm"]:
            continue
        out.append(k)
    return out


def reliability_level(total: int) -> str:
    """Affidabilita' della RICERCA dal numero di competitor: 1-2 file = BASSA, 3 = MEDIA, 4-5 = ALTA."""
    return "BASSA" if total <= 2 else "MEDIA" if total == 3 else "ALTA"


_LV = {"BASSA": 0, "MEDIA": 1, "ALTA": 2}


def keyword_reliability(k) -> str:
    total = k["competitor_total"]
    if k["share"] >= 0.75 and k["organic_rank_count"] >= 2:
        lvl = "ALTA"
    elif k["share"] >= 0.5:
        lvl = "MEDIA"
    else:
        lvl = "BASSA"
    cap = reliability_level(total)
    return lvl if _LV[lvl] <= _LV[cap] else cap


# ------------------------------------------------------------------ contesto dei nostri dati reali (gap)
def classify_match(match_type, campaign_type) -> str:
    mt, ct = str(match_type or "").lower(), str(campaign_type or "").upper()
    if "esatt" in mt or "exact" in mt or ct == "EXACT":
        return "EXACT"
    if "frase" in mt or "phrase" in mt or ct == "PHRASE":
        return "PHRASE"
    if "gener" in mt or "broad" in mt:
        return "BROAD"
    return "OTHER"


def build_own_context(targets=(), search_terms=(), negatives=()) -> dict:
    """Contesto dei dati REALI Amazon Ads del libro, solo per confronto (nessuna scrittura). targets: [{target, match_type, campaign_type, campaign_name}];
    search_terms: [{search_term, clicks, orders, spend}]; negatives: keyword gia' negative (o candidate negative)."""
    from .metrics import normalize_term
    ctx = {"phrase": defaultdict(set), "exact": defaultdict(set), "other": defaultdict(set), "negative": {normalize_term(n) for n in negatives if n},
           "observed": {}, "product_asins": set()}
    for t in targets:
        norm = normalize_term(t.get("target"))
        if not norm:
            continue
        if is_asin_like(norm):
            ctx["product_asins"].add(re.sub(r"^(asin(-expanded)?=)", "", norm).strip('"').upper())
            continue
        kind = classify_match(t.get("match_type"), t.get("campaign_type"))
        ctx["phrase" if kind == "PHRASE" else "exact" if kind == "EXACT" else "other"][norm].add(t.get("campaign_name") or t.get("campaign_id") or "")
    for s in search_terms:
        norm = normalize_term(s.get("search_term"))
        if not norm:
            continue
        o = ctx["observed"].setdefault(norm, {"clicks": 0.0, "orders": 0.0, "spend": 0.0})
        for f in ("clicks", "orders", "spend"):
            o[f] += float(s.get(f) or 0)
    return ctx


GAP_LABELS = {"NOT_PRESENT": "NON PRESENTE NELLE CAMPAGNE", "IN_PHRASE": "GIÀ PRESENTE IN FRASE", "IN_EXACT": "GIÀ PRESENTE IN EXACT",
              "NEGATIVE": "GIÀ NEGATIVA", "OBSERVED_SEARCH_TERM": "GIÀ OSSERVATA NEI SEARCH TERM", "IN_OTHER": "GIÀ TARGET IN ALTRO TIPO"}
GAP_LETTER = {"NOT_PRESENT": "A", "IN_PHRASE": "B", "IN_EXACT": "C", "NEGATIVE": "D", "OBSERVED_SEARCH_TERM": "E"}
_GAP_PRECEDENCE = ("NEGATIVE", "IN_EXACT", "IN_PHRASE", "IN_OTHER", "OBSERVED_SEARCH_TERM", "NOT_PRESENT")


def gap_for(kw_norm: str, own: dict) -> dict:
    """Categorie del keyword rispetto ai dati del libro: puo' averne piu' d'una; `primary` = la piu' vincolante (negativa > exact > frase > ...)."""
    cats = []
    if kw_norm in own["negative"]:
        cats.append("NEGATIVE")
    if kw_norm in own["exact"]:
        cats.append("IN_EXACT")
    if kw_norm in own["phrase"]:
        cats.append("IN_PHRASE")
    if kw_norm in own["other"]:
        cats.append("IN_OTHER")
    if kw_norm in own["observed"]:
        cats.append("OBSERVED_SEARCH_TERM")
    if not cats:
        cats = ["NOT_PRESENT"]
    kt = tokens(kw_norm)
    covering = sorted(p for p in own["phrase"] if p != kw_norm and _seq_in(kt, tokens(p)))      # frase piu' ampia che gia' intercetta la keyword
    return {"categories": cats, "primary": next(c for c in _GAP_PRECEDENCE if c in cats), "covered_by_phrase": covering[:3],
            "observed": own["observed"].get(kw_norm)}


# ------------------------------------------------------------------ ADS OPPORTUNITY SCORE
def book_tokens(title) -> set:
    return set(content_tokens(title))


def relevance_to_book(kw_norm: str, btoks: set) -> float | None:
    """Rilevanza semantica semplice verso il libro: sovrapposizione dei token TOPIC (75%) e del pubblico (25%). None se non c'e' un libro."""
    if not btoks:
        return None
    top, aud, _ = split_tokens(kw_norm)
    t = (len([x for x in top if x in btoks]) / len(top)) if top else 0.3
    a = (1.0 if any(x in btoks for x in aud) else 0.0) if aud else 0.5
    return round(0.75 * t + 0.25 * a, 3)


def _generic_keyword(kw_norm: str) -> bool:
    top, aud, fmt = split_tokens(kw_norm)
    return len(top) + len(aud) + len(fmt) <= 1 or not (top or aud)          # una parola, oppure solo formato (es. "large print books")


def ads_opportunity_score(k: dict, cfg: dict, btoks: set | None = None, gap: dict | None = None) -> dict:
    """{score 0-100, priority ALTA|MEDIA|BASSA, components[{name,label,value,weight,points,note}], penalties[{name,points,note}], excluded}.
    I componenti con dato mancante sono esclusi e i pesi riproporzionati. Brand/IP e ASIN: `excluded` (mai suggeriti)."""
    w = cfg["ads_weights"]
    total = k["competitor_total"]
    comps = []

    def add(name, label, value, note):
        comps.append({"name": name, "label": label, "value": None if value is None else round(value, 3), "weight": w[name], "note": note})

    add("overlap", "Presenza su più competitor", k["share"], f"{k['competitor_share']} competitor")
    if k["best_organic_rank"] is not None:
        add("organic", "Rank organico", _band_le(k["best_organic_rank"], cfg["organic_bands"]),
            f"miglior rank {k['best_organic_rank']:g} ({k['organic_rank_count']} competitor con rank)")
    else:
        add("organic", "Rank organico", None, "nessun rank organico nei file")
    add("sponsored", "Presenza sponsorizzata", (k["sponsored_competitor_count"] / total) if total else None,
        f"sponsored su {k['sponsored_competitor_count']}/{total} competitor")
    add("volume", "Search Volume", _band_ge(k["search_volume"], cfg["volume_bands"]) if k["search_volume"] is not None else None,
        f"mediana {k['search_volume']:g}" if k["search_volume"] is not None else "volume non disponibile")
    rel = relevance_to_book(k["keyword_norm"], btoks or set())
    add("relevance", "Rilevanza verso il libro", rel, "nessun libro associato" if rel is None else f"sovrapposizione di parole con il titolo {rel:.0%}")
    add("title_density", "Title Density", _band_le(k["title_density"], cfg["title_density_bands"]) if k["title_density"] is not None else None,
        f"mediana {k['title_density']:g}" if k["title_density"] is not None else "non disponibile")
    avail = [c for c in comps if c["value"] is not None and c["weight"]]
    wsum = sum(c["weight"] for c in avail) or 1
    for c in comps:
        c["points"] = round(100 * c["weight"] / wsum * c["value"], 1) if c in avail else None
    pen = []
    if _generic_keyword(k["keyword_norm"]):
        pen.append({"name": "generica", "points": cfg["penalty_generic"], "note": "keyword troppo generica (una sola parola utile)"})
    if (k["title_density"] or 0) > cfg["extreme_title_density"] or (k["competing_products"] or 0) > cfg["extreme_competing_products"]:
        pen.append({"name": "concorrenza estrema", "points": cfg["penalty_extreme"], "note": "Title Density o Competing Products molto alti"})
    if gap:
        obs = gap.get("observed")
        if obs and obs["clicks"] >= cfg["inefficient_min_clicks"] and obs["orders"] == 0:
            pen.append({"name": "inefficiente nei tuoi dati", "points": cfg["penalty_inefficient"],
                        "note": f"già osservata come Search Term: {obs['clicks']:g} clic e 0 ordini"})
        if gap.get("covered_by_phrase"):
            pen.append({"name": "già coperta da una frase", "points": cfg["penalty_covered_phrase"],
                        "note": "intercettata da: " + ", ".join(gap["covered_by_phrase"])})
    excluded = None
    if k.get("brand_or_ip"):
        excluded = "BRAND_OR_IP"
        pen.append({"name": "brand/IP", "points": 100, "note": "brand/franchise: mai suggerito (segnale di mercato)"})
    elif k.get("is_asin_like"):
        excluded = "ASIN"
        pen.append({"name": "ASIN", "points": 100, "note": "e' un ASIN, non una keyword (eventuale target prodotto)"})
    score = max(0.0, min(100.0, sum(c["points"] or 0 for c in comps) - sum(p["points"] for p in pen)))
    prio = "ALTA" if score >= cfg["ads_label_high"] else "MEDIA" if score >= cfg["ads_label_medium"] else "BASSA"
    return {"score": round(score, 1), "priority": prio, "components": comps, "penalties": pen, "excluded": excluded}


def gap_table(agg: dict, own: dict, cfg: dict, book_title: str | None = None) -> list:
    """KEYWORD GAP: per ogni keyword aggregata, categorie rispetto ai dati del libro + ADS score. Ordine = keyword comuni."""
    btoks = book_tokens(book_title) if book_title else set()
    rows = []
    for k in agg["keywords"]:
        g = gap_for(k["keyword_norm"], own)
        rows.append({"keyword": k["keyword"], "keyword_norm": k["keyword_norm"], "agg": k, "gap": g, "score": ads_opportunity_score(k, cfg, btoks, g),
                     "reliability": keyword_reliability(k)})
    return rows


def ads_suggestions(gap_rows: list, cfg: dict, total: int) -> list:
    """KEYWORD COMPETITOR DA TESTARE (solo EXISTING_BOOK): keyword rilevanti non presenti nelle campagne => TESTA IN FRASE (mai Exact diretto).
    Esclude: brand/IP, ASIN, keyword gia' presente/negativa/osservata, sotto soglia. Ordinate per ADS score."""
    need = min(cfg["min_competitors_for_ads"], max(total, 1))
    out = []
    for r in gap_rows:
        k, sc = r["agg"], r["score"]
        if r["gap"]["primary"] != "NOT_PRESENT" or sc["excluded"] or k["competitor_count"] < need or sc["score"] < cfg["ads_min_score"]:
            continue
        out.append({
            "action": "TESTA IN FRASE", "keyword": k["keyword"], "keyword_norm": k["keyword_norm"], "present": k["competitor_share"],
            "search_volume": k["search_volume"], "best_organic_rank": k["best_organic_rank"],
            "sponsored": f"{k['sponsored_competitor_count']}/{total}", "title_density": k["title_density"], "suggested_bid": k["suggested_bid"],
            "source": "Helium 10 · fonte esterna", "reliability": r["reliability"], "score": sc["score"], "priority": sc["priority"], "factors": sc,
            "reason": ("Ricorre su più competitor e non è presente nelle tue campagne." if k["competitor_count"] >= 2
                       else "Compare nel competitor analizzato e non è presente nelle tue campagne (un solo competitor: affidabilità limitata)."),
        })
    out.sort(key=lambda s: (-s["score"], s["keyword_norm"]))
    return out[:cfg["ads_max_suggestions"]]


LAUNCH_STATUS = {"NEW": "NUOVA — TESTA IN FRASE", "IN_PHRASE": "GIÀ IN FRASE", "IN_EXACT": "GIÀ IN EXACT", "NEGATIVE": "GIÀ NEGATIVA", "OBSERVED": "GIÀ OSSERVATA",
                 "GENERIC": "GENERICA / DA VALUTARE", "BRAND": "BRAND/IP — NON SUGGERITA", "FUTURE": "KEYWORD POTENZIALE PER IL FUTURO LANCIO",
                 "OTHER": "GIÀ TARGET (ALTRO TIPO)"}


def launch_status(k: dict, gap: dict | None) -> str:
    """Stato della keyword di lancio rispetto alle campagne. Senza libro: FUTURE (nessuna decisione Ads). NEW = candidata TESTA IN FRASE (mai Exact)."""
    if k["brand_or_ip"] or k["is_asin_like"]:
        return "BRAND"
    if gap is None:
        return "FUTURE"
    primary = gap["primary"]
    if primary == "NOT_PRESENT":
        return "GENERIC" if k["generic_keyword"] else "NEW"
    return {"NEGATIVE": "NEGATIVE", "IN_EXACT": "IN_EXACT", "IN_PHRASE": "IN_PHRASE", "IN_OTHER": "OTHER", "OBSERVED_SEARCH_TERM": "OBSERVED"}[primary]


def launch_sort_key(item):
    """Categoria (la genericita' scende di un gradino: priorita' ridotta), poi presenza su piu' competitor, miglior rank, rilevanza verso il libro,
    Search Volume, Title Density / Competing Products. NON il solo volume."""
    k = item["agg"]
    ranks = [r for r in (k["best_organic_rank"], k["best_sponsored_rank"]) if is_top10(r)]
    return (LAUNCH_CATEGORIES.index(k["top10_category"]) * 2 + (1 if k["generic_keyword"] else 0), -k["competitor_count"], min(ranks) if ranks else 99,
            -(item["relevance"] or 0), -(k["search_volume"] or 0), k["title_density"] if k["title_density"] is not None else 10 ** 9,
            k["competing_products"] if k["competing_products"] is not None else 10 ** 12, k["keyword_norm"])


def launch_priority(item: dict) -> str:
    """PRIORITA' UX (ALTA / MEDIA / BASSA / GENERICA): sola rappresentazione dell'ordinamento e dei segnali GIA' esistenti (categoria top 10, competitor, priorita' del
    punteggio Ads). Non e' un nuovo algoritmo e non modifica ne' lo score ne' l'ordine.
      GENERICA  generic_keyword
      ALTA      categoria BOTH/ORGANIC, su almeno 2 competitor, priorita' score non BASSA
      MEDIA     buon segnale ma conferma inferiore (BOTH/ORGANIC su 1 competitor, oppure SPONSORED su almeno 2 competitor)
      BASSA     il resto (un solo competitor sponsored, score basso, brand/IP) e qualunque keyword con Search Volume realmente 0.
                Un volume mancante (None, "n/d") NON e' penalizzato."""
    k = item["agg"]
    if item["generic"]:
        return "GENERICA"
    if item["status"] == "BRAND" or item["score"]["priority"] == "BASSA":
        return "BASSA"
    if k["search_volume"] is not None and k["search_volume"] == 0:        # volume realmente 0 (non mancante): resta in lista ma mai ALTA/MEDIA
        return "BASSA"
    if item["category"] in ("BOTH", "ORGANIC") and k["competitor_count"] >= 2:
        return "ALTA"
    if item["category"] in ("BOTH", "ORGANIC") or k["competitor_count"] >= 2:
        return "MEDIA"
    return "BASSA"


def launch_keywords(agg: dict, cfg: dict, gap_rows: list | None = None, book_title: str | None = None) -> list:
    """KEYWORD DI LANCIO: tutte le keyword con almeno un competitor in top 10 (organico e/o sponsorizzato). Le generiche restano visibili (flag, priorita' ridotta);
    brand/IP e ASIN restano elencati ma con stato BRAND e mai suggeriti. `gap_rows` = gap_table(...) (solo EXISTING_BOOK), altrimenti None."""
    btoks = book_tokens(book_title) if book_title else set()
    by_norm = {r["keyword_norm"]: r for r in gap_rows or []}
    out = []
    for k in agg["keywords"]:
        if k["top10_category"] == "NONE":
            continue
        g = by_norm.get(k["keyword_norm"])
        status = launch_status(k, g["gap"] if g else None)
        sc = g["score"] if g else ads_opportunity_score(k, cfg, btoks, None)
        out.append({"keyword": k["keyword"], "keyword_norm": k["keyword_norm"], "agg": k, "category": k["top10_category"], "category_label": LAUNCH_LABELS[k["top10_category"]],
                    "generic": k["generic_keyword"], "status": status, "status_label": LAUNCH_STATUS[status], "score": sc, "relevance": relevance_to_book(k["keyword_norm"], btoks),
                    "badge": ("TOP 10 · GENERICA" if k["generic_keyword"] else LAUNCH_LABELS[k["top10_category"]]), "reliability": keyword_reliability(k),
                    "action": "TESTA IN FRASE" if status == "NEW" else None})
    for i in out:
        i["priority"] = launch_priority(i)
        i["action_label"] = i["action"] or i["status_label"]          # colonna Azione: TESTA IN FRASE solo per keyword nuove; altrimenti lo stato coerente
    out.sort(key=launch_sort_key)
    return out


def filter_launch(items, category="ALL", min_competitors=1, min_volume=None, exclude_generic=False, exclude_brand=False) -> list:
    """category: ALL | BOTH | ORGANIC | SPONSORED (ORGANIC/SPONSORED = ha quel top 10, anche se ha pure l'altro). Volume mancante con filtro volume = escluso."""
    out = []
    for i in items:
        k = i["agg"]
        if category == "BOTH" and i["category"] != "BOTH":
            continue
        if category == "ORGANIC" and not k["organic_top10"]:
            continue
        if category == "SPONSORED" and not k["sponsored_top10"]:
            continue
        if k["competitor_count"] < min_competitors or (min_volume and (k["search_volume"] is None or k["search_volume"] < min_volume)):
            continue
        if (exclude_generic and i["generic"]) or (exclude_brand and i["status"] == "BRAND"):
            continue
        out.append(i)
    return out


def launch_expansion(items: list) -> list:
    """Per i Suggerimenti di crescita (EXISTING_BOOK): solo keyword top 10 NUOVE (non presenti/negative/osservate), non generiche, non brand. Azione: TESTA IN FRASE."""
    return [i for i in items if i["status"] == "NEW"]


def launch_counts(agg: dict) -> dict:
    c = Counter(k["top10_category"] for k in agg["keywords"])
    return {"organic": c["BOTH"] + c["ORGANIC"], "sponsored": c["BOTH"] + c["SPONSORED"], "both": c["BOTH"], "any": c["BOTH"] + c["ORGANIC"] + c["SPONSORED"],
            "organic_only": c["ORGANIC"], "sponsored_only": c["SPONSORED"]}


# ------------------------------------------------------------------ CLUSTERING EDITORIALE
def _core_of(top, aud):
    return tuple(sorted(set(top))), tuple(sorted(set(aud)))


def cluster_keywords(keywords: list, cfg: dict) -> dict:
    """{clusters:[{cluster_key, topic, audience, keywords[agg]}], unclustered:[agg], brand:[agg], asin:[agg]}. Deterministico: nessun valore casuale,
    ordinamento stabile. Le keyword brand/IP e gli ASIN non entrano nei cluster."""
    brand = [k for k in keywords if k["brand_or_ip"]]
    asin = [k for k in keywords if not k["brand_or_ip"] and k["is_asin_like"]]
    pool = [k for k in keywords if not k["brand_or_ip"] and not k["is_asin_like"]]
    groups = defaultdict(list)
    sig = {}
    for k in pool:
        top, aud, _ = split_tokens(k["keyword_norm"])
        s = _core_of(top, aud)
        sig[k["keyword_norm"]] = s
        groups[s].append(k)

    def gsize(g):
        return (len(groups[g]), sum(x["competitor_count"] for x in groups[g]))

    minimum = cfg["min_cluster_keywords"]
    # 1) keyword senza pubblico che NON bastano a formare un cluster: si uniscono al gruppo piu' grande con stesso topic e un pubblico (se esiste)
    for g in sorted([g for g in groups if not g[1] and g[0] and len(groups[g]) < minimum], key=lambda g: g[0]):
        same = sorted([h for h in groups if h[0] == g[0] and h[1]],      # prima i gruppi con pubblico che hanno ancora bisogno di keyword, poi il piu' grande
                      key=lambda h: (len(groups[h]) >= minimum, -gsize(h)[0], -gsize(h)[1], h[1]))
        if same:
            groups[same[0]].extend(groups.pop(g))
    # 2) gruppi di 1 keyword: si uniscono a un gruppo con topic piu' generico (sottoinsieme) e stesso pubblico
    for g in sorted([g for g in groups if len(groups[g]) < minimum], key=lambda g: (g[0], g[1])):
        if g not in groups:
            continue
        parents = sorted([h for h in groups if h != g and h[1] == g[1] and h[0] and set(h[0]) < set(g[0]) and len(groups[h]) >= minimum],
                         key=lambda h: (-gsize(h)[0], -len(h[0]), h[0]))
        if parents:
            groups[parents[0]].extend(groups.pop(g))
    clusters, unclustered = [], []
    for g, ks in groups.items():
        if len(ks) >= minimum and (g[0] or g[1]):
            ks = sorted(ks, key=common_sort_key)
            clusters.append({"cluster_key": "|".join(g[0]) + "~" + "|".join(g[1]), "topic": list(g[0]), "audience": list(g[1]), "keywords": ks})
        else:
            unclustered.extend(ks)
    clusters.sort(key=lambda c: c["cluster_key"])
    return {"clusters": clusters, "unclustered": sorted(unclustered, key=common_sort_key), "brand": brand, "asin": asin}


def _cap(words):
    return " ".join(w.capitalize() for w in words)


def cluster_label(cluster: dict) -> tuple:
    """(etichetta, formato dominante). Costruita da forme di superficie piu' frequenti: [formato] [topic] [for pubblico]; es. 'Large Print Mystery for Seniors'."""
    ks = cluster["keywords"]
    surf = defaultdict(Counter)
    pos = defaultdict(list)
    fmt_count = Counter()
    for k in ks:
        raw = [t for t in re.findall(r"[a-z0-9]+", re.sub(r"['’]s\b", "", k["keyword_norm"])) if t not in STOP]
        fmts = set()
        for i, t in enumerate(raw):
            st = stem(t)
            surf[st][t] += 1
            pos[st].append(i)
            if st in FORMAT_MODS:
                fmts.add(st)
        fmt_count.update(fmts)
    fmt = sorted([f for f, c in fmt_count.items() if c * 2 >= len(ks)], key=lambda f: ("large", "print", "bold", "jumbo", "giant").index(f))

    def word(st):
        return sorted(surf[st].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if surf[st] else st

    topic = sorted(cluster["topic"], key=lambda t: (statistics.mean(pos[t]) if pos[t] else 99, t))
    parts = [word(f) for f in fmt] + [word(t) for t in topic]
    label = _cap(parts)
    if cluster["audience"]:
        aud = sorted(cluster["audience"], key=lambda t: (statistics.mean(pos[t]) if pos[t] else 99, t))
        label = (label + " for " if label else "For ") + _cap([word(a) for a in aud])
    return label.strip(), fmt


def book_opportunity(cluster: dict, total: int, cfg: dict, origin_tokens: set | None = None, with_origin: bool = False) -> dict:
    """BOOK OPPORTUNITY SCORE del cluster. Ritorna il cluster arricchito con metriche aggregate, score, etichetta, fattori."""
    ks = cluster["keywords"]
    label, fmt = cluster_label(cluster)
    asins = sorted({a for k in ks for a in k["asins"]})
    vols = [k["search_volume"] for k in ks if k["search_volume"] is not None]
    best_ranks = [k["best_organic_rank"] for k in ks if k["best_organic_rank"] is not None]
    spons_asins = sorted({a for k in ks for a, o in k["per_competitor"].items() if o.get("sponsored_rank") is not None})
    tds = [k["title_density"] for k in ks if k["title_density"] is not None]
    cps = [k["competing_products"] for k in ks if k["competing_products"] is not None]
    core = set(cluster["topic"]) | set(cluster["audience"])
    full = sum(1 for k in ks if core <= set(content_tokens(k["keyword_norm"])))
    coherence = full / len(ks)
    volume_sum = sum(vols) if vols else None
    volume_max = max(vols) if vols else None
    volume_basis = max(volume_max, 0.5 * volume_sum) if vols else None
    w = cfg["book_weights"]
    comps = []

    def add(name, label_, value, note):
        comps.append({"name": name, "label": label_, "value": None if value is None else round(value, 3), "weight": w[name], "note": note})

    overlap_share = len(asins) / total if total else 0
    mean_share = statistics.mean(k["share"] for k in ks)
    add("overlap", "Overlap competitor", (overlap_share + mean_share) / 2, f"{len(asins)}/{total} competitor su almeno una keyword; quota media per keyword {mean_share:.0%}")
    add("organic", "Forza rank organico",
        statistics.mean(_band_le(r, cfg["organic_bands"]) for r in best_ranks) if best_ranks else None,
        f"miglior rank mediano {statistics.median(best_ranks):g} su {len(best_ranks)} keyword con rank" if best_ranks else "nessun rank organico")
    add("volume", "Search Volume", _band_ge(volume_basis, cfg["volume_bands"]) if vols else None,
        f"somma keyword distinte {volume_sum:g} · keyword più forte {volume_max:g}" if vols else "volume non disponibile")
    add("title_density", "Title Density", _band_le(statistics.median(tds), cfg["title_density_bands"]) if tds else None,
        f"mediana {statistics.median(tds):g}" if tds else "non disponibile")
    add("competing", "Competing Products", _band_le(statistics.median(cps), cfg["competing_bands"]) if cps else None,
        f"mediana {statistics.median(cps):g}" if cps else "non disponibile")
    add("coherence", "Coerenza del cluster", coherence, f"{full}/{len(ks)} keyword contengono tutti i termini centrali")
    add("breadth", "Keyword correlate", _band_ge(len(ks), cfg["breadth_bands"]), f"{len(ks)} keyword")
    origin_overlap = None
    if with_origin and origin_tokens:
        origin_overlap = len(core & origin_tokens) / len(core) if core else 0.0
        add("distinct", "Distinzione dal libro origine", 1 - origin_overlap, f"{origin_overlap:.0%} dei termini centrali è già nel titolo del libro origine")
    else:
        add("distinct", "Distinzione dal libro origine", None, "nessun libro origine")
    avail = [c for c in comps if c["value"] is not None and c["weight"]]
    wsum = sum(c["weight"] for c in avail) or 1
    for c in comps:
        c["points"] = round(100 * c["weight"] / wsum * c["value"], 1) if c in avail else None
    score = round(max(0.0, min(100.0, sum(c["points"] or 0 for c in comps))), 1)
    lab = ("DEBOLE" if score < cfg["book_label_weak"] else "DA OSSERVARE" if score < cfg["book_label_watch"]
           else "INTERESSANTE" if score < cfg["book_label_interesting"] else "FORTE")
    caps = []
    if lab == "FORTE" and (len(ks) < 3 or len(asins) < min(2, total)):
        lab = "INTERESSANTE"
        caps.append("FORTE richiede almeno 3 keyword e 2 competitor che confermano")
    if lab == "FORTE" and reliability_level(total) == "BASSA":
        lab = "INTERESSANTE"
        caps.append("ricerca con 1-2 competitor: massimo INTERESSANTE")
    rel = reliability_level(total)
    # tetti: segnali deboli non possono diventare FORTE/INTERESSANTE (ordine: FORTE > INTERESSANTE > DA OSSERVARE > DEBOLE)
    order = ["DEBOLE", "DA OSSERVARE", "INTERESSANTE", "FORTE"]

    def ceil(max_label, why):
        nonlocal lab
        if order.index(lab) > order.index(max_label):
            lab = max_label
            caps.append(why)
    if volume_basis is None or volume_basis < cfg["book_min_volume_for_strong"]:
        ceil("INTERESSANTE", "volume basso o assente (< %d): massimo INTERESSANTE" % cfg["book_min_volume_for_strong"])
    if total >= 3 and len(asins) < 2:
        ceil("DA OSSERVARE", "confermato da un solo competitor su %d: massimo DA OSSERVARE" % total)
    if len(core) < 2 and not (cluster["topic"] and cluster["audience"]):
        ceil("INTERESSANTE", "cluster ampio: un solo termine centrale (categoria, non un concetto di libro)")
    if not cluster["topic"]:
        ceil("DA OSSERVARE", "nessun tema (solo pubblico/formato)")
    generic_standalone = len(core) < 2 or not cluster["topic"]       # un solo termine (Stories, Mystery, Seniors, Women...) o solo pubblico: categoria, non un concetto di libro
    return {
        "cluster_key": cluster["cluster_key"], "label": label, "format": fmt, "topic": cluster["topic"], "audience": cluster["audience"],
        "core_tokens": sorted(core | set(fmt)), "keywords": [k["keyword"] for k in ks], "keyword_norms": [k["keyword_norm"] for k in ks], "keyword_count": len(ks),
        "competitor_count": len(asins), "competitor_total": total, "competitor_share": f"{len(asins)}/{total}", "competitor_asins": asins,
        "volume_sum": volume_sum, "volume_max": volume_max,
        "volume_note": "Somma delle keyword DISTINTE (ogni keyword conta una volta anche se presente su più competitor). Keyword correlate si sovrappongono: stima indicativa, non un volume di nicchia.",
        "best_organic_rank": min(best_ranks) if best_ranks else None, "median_best_organic_rank": statistics.median(best_ranks) if best_ranks else None,
        "sponsored_competitor_count": len(spons_asins), "sponsored_share": f"{len(spons_asins)}/{total}",
        "title_density_median": statistics.median(tds) if tds else None, "competing_products_median": statistics.median(cps) if cps else None,
        "coherence": round(coherence, 3), "score": score, "score_label": lab, "components": comps, "caps": caps, "reliability": rel,
        "origin_overlap": origin_overlap,
        "similar_to_origin": bool(origin_overlap is not None and origin_overlap >= cfg["similar_to_origin"]),
        "status": "DA APPROFONDIRE", "generic_standalone": generic_standalone,
        "reason": _book_reason(label, len(ks), len(asins), total, lab),
    }


def _book_reason(label, n_kw, n_comp, total, lab):
    return (f"{n_kw} keyword correlate confermate da {n_comp}/{total} competitor: cluster «{label}» da approfondire ({lab}). "
            "Non è una raccomandazione a pubblicare né una stima di profittabilità.")


def find_overlaps(opportunity: dict, others: list, cfg: dict) -> list:
    """POTENZIALE SOVRAPPOSIZIONE: altri titoli/ricerche/progetti i cui token contengono >= `overlap_flag` dei termini centrali del cluster.
    others = [{kind: LIBRO|RICERCA|PROGETTO, name, tokens: iterable}]. Solo segnalazione: nessun cluster e' mai scartato come duplicato."""
    core = set(opportunity["topic"]) | set(opportunity["audience"])          # il formato (large print) non identifica un titolo
    if not core:
        return []
    hits = []
    for o in others:
        share = len(core & set(o["tokens"])) / len(core)
        if share >= cfg["overlap_flag"]:
            hits.append({"kind": o["kind"], "name": o["name"], "overlap": round(share, 2), "ref": o.get("ref")})
    return sorted(hits, key=lambda h: (-h["overlap"], h["name"]))


def build_opportunities(agg: dict, cfg: dict, origin_title: str | None = None, origin_book_id: str | None = None, others: list | None = None) -> dict:
    """OPPORTUNITA' EDITORIALI DERIVATE (sempre, con o senza libro origine). Ritorna {opportunities (per punteggio decrescente), unclustered, brand_signals, asin_keywords}."""
    cl = cluster_keywords(agg["keywords"], cfg)
    otoks = set(content_tokens(origin_title)) if origin_title else set()
    with_origin = origin_book_id is not None
    opps = []
    for c in cl["clusters"]:
        o = book_opportunity(c, agg["total"], cfg, otoks, with_origin)
        o["origin_book_id"] = origin_book_id
        o["origin_title"] = origin_title
        o["derived"] = with_origin and not o["similar_to_origin"]
        o["derived_label"] = ("NUOVA OPPORTUNITÀ DERIVATA" if o["derived"] else "SIMILE AL LIBRO ORIGINE" if with_origin else None)
        o["overlaps"] = find_overlaps(o, others or [], cfg)
        o["overlap_warning"] = "POTENZIALE SOVRAPPOSIZIONE CON PROGETTO ESISTENTE" if o["overlaps"] else None
        opps.append(o)
    for o in opps:
        o["tier"], o["secondary_reason"] = classify_tier(o)
    rank = {"FORTE": 0, "INTERESSANTE": 1, "DA OSSERVARE": 2, "DEBOLE": 3}
    opps.sort(key=lambda o: (o["tier"] != "PRINCIPALE", rank[o["score_label"]], -o["score"], o["label"]))
    return {"opportunities": opps, "primary": [o for o in opps if o["tier"] == "PRINCIPALE"], "secondary": [o for o in opps if o["tier"] != "PRINCIPALE"],
             "unclustered": cl["unclustered"], "brand_signals": cl["brand"], "asin_keywords": cl["asin"]}


def classify_tier(o: dict) -> tuple:
    """OPPORTUNITA' PRINCIPALE = cluster specifico e azionabile. SEGNALE SECONDARIO (mai cancellato) = cluster generico standalone (un solo termine centrale o solo
    pubblico: Stories, Mystery, Seniors, Women...) oppure debole (etichetta DEBOLE / DA OSSERVARE). Il tier NON cambia punteggio ne' etichetta."""
    if o["generic_standalone"]:
        return "SECONDARIO", "cluster generico standalone (categoria, non un concetto di libro)"
    if o["score_label"] in ("DEBOLE", "DA OSSERVARE"):
        return "SECONDARIO", "segnale debole (" + o["score_label"] + ")"
    return "PRINCIPALE", None


def project_fingerprint(opportunity: dict) -> str:
    return hashlib.sha1(json.dumps(sorted(opportunity["keyword_norms"])).encode()).hexdigest()[:12]


def overview(agg: dict, ads: list | None, opps: dict, mode: str) -> dict:
    """Numeri della PANORAMICA: keyword su N/N, su (N-1)/N, suggerimenti Ads, cluster editoriali."""
    total = agg["total"]
    cnt = Counter(k["competitor_count"] for k in agg["keywords"])
    return {"total": total, "reliability": reliability_level(total),
            "by_count": {n: cnt.get(n, 0) for n in range(total, 0, -1)},
            "all": cnt.get(total, 0), "most": cnt.get(total - 1, 0) if total > 1 else 0,
            "keywords_total": len(agg["keywords"]),
            "ads_count": None if ads is None else len(ads), "mode": mode,
            "clusters": [{"label": o["label"], "score_label": o["score_label"], "score": o["score"]} for o in opps["primary"]],
            "secondary_count": len(opps["secondary"]), "clusters_total": len(opps["opportunities"]),
            "brand_count": len(opps["brand_signals"]), "launch": launch_counts(agg)}
