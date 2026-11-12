"""RICERCA KEYWORD / COMPETITOR: riconoscimento automatico del formato (Publisher Rocket / Helium 10 Cerebro), unione delle evidenze per keyword e PIANO KEYWORD di lancio.
Modulo PURO e LOCALE: legge byte in memoria, nessuna rete, nessuna scrittura (il salvataggio nel progetto e' in competitor_store / DASHBOARD/ops/competitor_ops.py), nessun accesso
al DB Ads ne' a books.json. I dati sono FONTI ESTERNE/STIMATE: NON entrano mai nelle decisioni 7D/30D/90D/Lifetime delle campagne esistenti (due pipeline distinte) e NON creano
campagne Amazon: il piano e' una PROPOSTA MANUALE, PHRASE-first.

RICONOSCIMENTO (l'utente non sceglie il formato). Cerebro: la riga di intestazione contiene almeno Keyword Phrase + Search Volume + Organic Rank (meglio se anche Cerebro IQ Score e
Sponsored Rank). Publisher Rocket: il riconoscimento esistente (launch_research). Un file con colonne tipiche Cerebro ma incomplete NON viene forzato nel parser Rocket: formato
sconosciuto con l'elenco delle colonne rilevate.

UNIONE Rocket + Cerebro: UNA riga per keyword normalizzata (minuscolo, trim, spazi multipli ridotti a uno; nessuno stemming, nessuna parola tolta; il testo originale resta per la
visualizzazione). Ogni fonte conserva i propri campi: il volume Cerebro e quello Rocket sono DUE misure diverse e restano separati (`cerebro_search_volume`, `rocket_search_volume`);
per l'ordinamento si usa quello Cerebro, altrimenti quello Rocket. '-' e' un dato mancante (None), mai zero; Search Volume 0 e' uno zero vero. Rank: il piu' BASSO e' il migliore.

PRIORITA' (non dal solo Search Volume, che e' un segnale a basso peso e non esclude mai): presenza su piu' competitor, Organic Rank, Sponsored Rank / presenza advertising, Search Volume,
Cerebro IQ Score, Keyword Sales, pertinenza semantica con titolo/sottotitolo/descrizione/keyword KDP del libro, intento fuori tema. Componenti non disponibili = esclusi (pesi
riproporzionati: il dato mancante non e' ne' buono ne' cattivo)."""
import re
import statistics
from collections import Counter

from . import cerebro as CB
from . import competitor as C
from . import launch_research as LR
from .launch_keywords import INTENT_CONFLICT

SOURCE_CEREBRO, SOURCE_ROCKET, SOURCE_UNKNOWN = CB.SOURCE, LR.SOURCE, "UNKNOWN"
FORMAT_LABELS = {SOURCE_CEREBRO: "Helium 10 Cerebro", SOURCE_ROCKET: "Publisher Rocket", SOURCE_UNKNOWN: "Formato sconosciuto"}
CEREBRO_REQUIRED = ("keyword", "search_volume", "organic_rank")
CEREBRO_SPECIFIC = ("cerebro_iq", "organic_rank", "sponsored_rank", "ppc_bid")
CEREBRO_PREFERRED = ("cerebro_iq", "sponsored_rank")
CEREBRO_XLSX_MESSAGE = "Export Cerebro riconosciuto, ma al momento usa il formato CSV."
P1, P2, P3, EXCLUDED, WEAK = "P1", "P2", "P3", "FUORI INTENTO", "NON PRIORITARIA"
PRIORITY_LABELS = {P1: "P1 CORE", P2: "P2 STRONG", P3: "P3 TEST", EXCLUDED: "ESCLUDI / FUORI INTENTO", WEAK: "NON PRIORITARIA"}
PRIORITY_ORDER = {P1: 0, P2: 1, P3: 2, WEAK: 3, EXCLUDED: 4}
LAUNCH_CORE, LAUNCH_TEST, LAUNCH_EXACT = "PHRASE CORE", "PHRASE TEST", "CANDIDATA EXACT — EVIDENZA MOLTO FORTE"

DEFAULTS = {
    "weights": {"presence": 25, "organic": 20, "sponsored": 10, "volume": 10, "iq": 5, "sales": 5, "relevance": 25},
    "organic_bands": [[3, 1.0], [10, 0.9], [25, 0.65], [50, 0.4], [10_000, 0.2]],             # (rank massimo, valore): rank basso = migliore
    "sponsored_bands": [[10, 1.0], [30, 0.7], [10_000, 0.4]],
    "volume_bands": [[3000, 1.0], [1000, 0.8], [300, 0.55], [100, 0.3], [0, 0.1]],           # (volume minimo, valore): il volume e' un segnale, non la decisione
    "p1_min_score": 62, "p2_min_score": 45, "p3_min_score": 30,
    "p1_min_relevance": 0.55, "p2_min_relevance": 0.40, "p3_min_relevance": 0.30, "exclude_below_relevance": 0.25,
    "p1_min_competitors": 3, "consensus_relevance_floor": 0.35,
    "exact_min_competitors": 4, "exact_min_volume": 100, "exact_max_organic_rank": 5, "exact_max_sponsored_rank": 10, "exact_min_relevance": 0.8,
    "top10": 10, "organic_top10_bonus": 4, "sponsored_top10_bonus": 3,         # Organic/Sponsored Rank 1-10 di un competitor: peso esplicito nel punteggio (mai senza pertinenza: i gate di priorita' la richiedono)
}

# ------------------------------------------------------------------ riconoscimento del formato
_CEREBRO_HEADER_SCAN = LR.HEADER_SCAN_ROWS


def _canon_cerebro(headers) -> dict:
    """{campo canonico: intestazione originale} secondo gli alias Cerebro (il primo header per campo vince)."""
    out = {}
    for h in headers:
        c = CB._ALIAS_INDEX.get(LR._hnorm(h))
        if c and c not in out:
            out[c] = LR.clean(h)
    return out


def detect_format(name: str, data: bytes) -> dict:
    """Riconosce il formato SENZA importare nulla. Ritorna {format: HELIUM10_CEREBRO|PUBLISHER_ROCKET|UNKNOWN, label, columns[intestazioni rilevate], missing[], message, preferred_missing[]}.
    Un file illeggibile o vuoto e' UNKNOWN con il motivo in `message`."""
    base = {"format": SOURCE_UNKNOWN, "label": FORMAT_LABELS[SOURCE_UNKNOWN], "columns": [], "missing": [], "preferred_missing": [], "message": ""}
    try:
        table = LR.read_table(name, data)
    except LR.ResearchError as e:
        return {**base, "message": f"{name}: {e}"}
    rows = table["rows"]
    header_row, headers = None, []
    for i, r in enumerate(rows[:_CEREBRO_HEADER_SCAN]):
        canon = _canon_cerebro(r)
        rocket = {LR._ALIAS_INDEX.get(LR._hnorm(h)) for h in r}
        if "keyword" in canon or rocket & {"keyword", "asin"}:
            header_row, headers = i, [LR.clean(h) for h in r if LR.clean(h)]
            break
    if header_row is None:                                  # nessuna colonna keyword/ASIN: si elencano le colonne della prima riga non vuota
        first = next((r for r in rows if any(LR.clean(c) for c in r)), [])
        headers = [LR.clean(c) for c in first if LR.clean(c)]
        return {**base, "columns": headers, "message": f"{name}: formato non riconosciuto. Colonne rilevate: {', '.join(headers) or 'nessuna'}. "
                                                       "Servono un export Publisher Rocket (Keyword, Search Volume, Competition…) o Helium 10 Cerebro (Keyword Phrase, Search Volume, Organic Rank…)."}
    canon = _canon_cerebro(rows[header_row])
    if all(k in canon for k in CEREBRO_REQUIRED):
        pref = [k for k in CEREBRO_PREFERRED if k not in canon]
        return {**base, "format": SOURCE_CEREBRO, "label": FORMAT_LABELS[SOURCE_CEREBRO], "columns": headers, "preferred_missing": pref,
                "message": f"{name}: riconosciuto come Helium 10 Cerebro" + (f" (mancano colonne facoltative: {', '.join(pref)})" if pref else "")}
    if any(k in canon for k in CEREBRO_SPECIFIC):         # colonne Cerebro ma incomplete: non si forza nel parser Rocket
        missing = [k for k in CEREBRO_REQUIRED if k not in canon]
        return {**base, "columns": headers, "missing": missing,
                "message": f"{name}: sembra un export Helium 10 Cerebro ma mancano le colonne obbligatorie ({', '.join(missing)}). Colonne rilevate: {', '.join(headers)}."}
    return {**base, "format": SOURCE_ROCKET, "label": FORMAT_LABELS[SOURCE_ROCKET], "columns": headers, "message": f"{name}: riconosciuto come Publisher Rocket"}


def parse_any(name: str, data: bytes, manual_competitor: str | None = None) -> dict:
    """Riconosce e legge UN file. {format, label, detect, cerebro|None (parsed di cerebro.parse_cerebro), rocket|None (record di launch_research.parse_file), competitor_id,
    competitor_id_source, marketplace, needs_competitor, error|None}. Nessuna scrittura. Un file sconosciuto non viene mai passato a un parser."""
    det = detect_format(name, data)
    out = {"format": det["format"], "label": det["label"], "detect": det, "cerebro": None, "rocket": None, "competitor_id": None, "competitor_id_source": None,
           "marketplace": CB.marketplace_from_filename(name), "needs_competitor": False, "error": None, "name": name}
    if det["format"] == SOURCE_UNKNOWN:
        out["error"] = det["message"]
        return out
    if det["format"] == SOURCE_CEREBRO and not str(name or "").lower().endswith(".csv"):
        out["error"] = f"{name}: {CEREBRO_XLSX_MESSAGE}"           # riconosciuto, ma non importabile: mai presentato come supportato
        out["recognised_unsupported"] = True
        return out
    try:
        if det["format"] == SOURCE_CEREBRO:
            p = CB.parse_cerebro(name, data, asin=manual_competitor or None)
            out.update(cerebro=p, competitor_id=p["asin"], competitor_id_source=p["asin_source"], needs_competitor=p["asin"] is None)
        else:
            rec = LR.parse_file(name, data)
            out.update(rocket=rec, competitor_id=rec.get("reverse_asin"), competitor_id_source="FILENAME" if rec.get("reverse_asin") else None)
    except (CB.CerebroError, LR.ResearchError) as e:
        out["error"] = f"{name}: {e}"
    return out


def file_summary(p: dict) -> dict:
    """Riga di riepilogo per file dopo l'upload: formato, competitor, keyword, avvisi."""
    if p["error"]:
        return {"name": p["name"], "ok": False, "label": p["label"], "text": p["error"]}
    if p["cerebro"]:
        c = p["cerebro"]
        return {"name": p["name"], "ok": True, "label": p["label"], "competitor": p["competitor_id"], "keywords": len(c["rows"]), "needs_competitor": p["needs_competitor"],
                "text": f"✓ {p['label']} · Competitor: {p['competitor_id'] or 'da indicare'} · {len(c['rows'])} keyword", "warnings": c["warnings"]}
    r = p["rocket"]
    return {"name": p["name"], "ok": True, "label": p["label"], "competitor": p["competitor_id"], "keywords": len(r["keywords"]), "needs_competitor": False,
            "text": f"✓ {p['label']} · {len(r['keywords'])} keyword" + (f" · {len(r['competitors'])} competitor" if r["competitors"] else ""), "warnings": r["warnings"]}


# ------------------------------------------------------------------ profilo del libro e pertinenza
_SUPPORT = {"title": 1.0, "kdp_keywords": 0.9, "subtitle": 0.8, "categories": 0.7, "description": 0.4}


def build_profile(title=None, subtitle=None, kdp_keywords=None, categories=None, description=None) -> dict:
    """Profilo del libro: {token (stemmed, di contenuto): supporto 0-1}, piu' i testi per il bonus «la frase intera compare nel titolo». Vuoto se non c'e' nessun testo."""
    parts = {"title": title, "subtitle": subtitle, "kdp_keywords": " ".join(kdp_keywords) if isinstance(kdp_keywords, (list, tuple)) else kdp_keywords,
             "categories": " ".join(categories) if isinstance(categories, (list, tuple)) else categories, "description": description}
    tok = {}
    for src, text in parts.items():
        for t in C.content_tokens(text or ""):
            tok[t] = max(tok.get(t, 0.0), _SUPPORT[src])
    raw = set(re.findall(r"[a-z0-9]+", " ".join(str(v or "") for v in parts.values()).lower()))
    return {"tokens": tok, "raw_words": raw, "title_norm": CB.normalize_keyword(title or ""), "has_profile": bool(tok)}


def relevance(kw_norm: str, profile: dict | None) -> dict:
    """{value 0-1|None, intent_conflict: [parole], reason}. None = nessun profilo (es. nuova opportunita' senza libro): la pertinenza non e' valutata."""
    if not profile or not profile.get("has_profile"):
        return {"value": None, "intent_conflict": [], "reason": "pertinenza non valutata (nessun libro/profilo)"}
    words = re.findall(r"[a-z0-9]+", kw_norm)
    conflict = sorted({w for w in words if w in INTENT_CONFLICT and w not in profile["raw_words"]})
    toks = C.content_tokens(kw_norm)
    if not toks:
        val = 0.2
    else:
        val = sum(profile["tokens"].get(t, 0.0) for t in toks) / len(toks)
        if profile["title_norm"] and kw_norm in profile["title_norm"]:
            val = min(1.0, val + 0.15)
    return {"value": round(val, 3), "intent_conflict": conflict, "reason": ""}


# ------------------------------------------------------------------ aggregazione Cerebro (riuso di competitor.aggregate)
def cerebro_sources(parsed_files: list) -> list:
    """[{asin: competitor_id, rows}] dai parsed di cerebro.parse_cerebro: una sola osservazione per keyword normalizzata in ogni file. Due file con lo stesso competitor sono errore del chiamante."""
    return [{"asin": p["asin"], "rows": CB.merge_duplicate_rows(p["rows"])} for p in parsed_files]


def _rocket_index(records: list) -> dict:
    """{keyword_norm: {keyword, files, search_volume, search_volume_text, competition, title_count, avg_*...}} da record Publisher Rocket (piu' file: metriche = mediana dei valori presenti)."""
    by = {}
    for rec in records:
        for k in rec.get("keywords", []):
            n = CB.normalize_keyword(k.get("keyword"))
            if not n:
                continue
            e = by.setdefault(n, {"keyword": k["keyword"], "files": [], "_v": {}})
            if rec["file_name"] not in e["files"]:
                e["files"].append(rec["file_name"])
            for f, v in k.items():
                if f in ("keyword", "source", "source_file", "marketplace", "competitor_asin", "competitor_asin_source", "extra"):
                    continue
                e["_v"].setdefault(f, []).append(v)
    out = {}
    for n, e in by.items():
        row = {"keyword": e["keyword"], "files": e["files"]}
        for f, vals in e["_v"].items():
            nums = [v for v in vals if isinstance(v, (int, float))]
            row[f] = statistics.median(nums) if nums else vals[0]       # testo (es. "<100"): resta testo
        out[n] = row
    return out


# ------------------------------------------------------------------ punteggio e priorita'
def _band_ge(v, bands):
    for floor, val in bands:
        if v >= floor:
            return val
    return bands[-1][1]


def _band_le(v, bands):
    for cap, val in bands:
        if v <= cap:
            return val
    return bands[-1][1]


def _percentile(v, sorted_vals):
    if v is None or not sorted_vals:
        return None
    below = sum(1 for x in sorted_vals if x < v) + 0.5 * sum(1 for x in sorted_vals if x == v)
    return below / len(sorted_vals)


def config(rules: dict | None = None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in ((rules or {}).get("keyword_research") or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def _components(r: dict, rel, dist: dict, cfg: dict) -> dict:
    """Componenti 0..1 (None = non disponibile, escluso dal punteggio)."""
    comp = {}
    total, n = r["competitor_total"], r["competitor_count"]
    comp["presence"] = (min(1.0, 0.5 * (n / total) + 0.5 * min(n, 4) / 4) if total else None)
    if r.get("best_organic_rank") is not None:
        med = r.get("median_organic_rank")
        comp["organic"] = 0.7 * _band_le(r["best_organic_rank"], cfg["organic_bands"]) + 0.3 * _band_le(med if med is not None else r["best_organic_rank"], cfg["organic_bands"])
    else:
        comp["organic"] = None
    if r.get("best_sponsored_rank") is not None:
        comp["sponsored"] = 0.6 * _band_le(r["best_sponsored_rank"], cfg["sponsored_bands"]) + 0.4 * (r["sponsored_competitor_count"] / total if total else 0.0)
    else:
        comp["sponsored"] = None                                             # nessun competitor sponsorizzato: nessuna informazione, non un demerito
    vol = r.get("search_volume_used")
    comp["volume"] = _band_ge(vol, cfg["volume_bands"]) if vol is not None else None
    comp["iq"] = _percentile(r.get("cerebro_iq"), dist["iq"])
    comp["sales"] = _percentile(r.get("keyword_sales"), dist["sales"])
    comp["relevance"] = rel["value"]
    return comp


def _score(comp: dict, weights: dict) -> tuple:
    used = {k: v for k, v in comp.items() if v is not None and weights.get(k)}
    w = sum(weights[k] for k in used)
    return (round(100 * sum(weights[k] * v for k, v in used.items()) / w, 1) if w else 0.0), sorted(used)


def _reasons(r: dict, rel: dict, prio: str) -> list:
    out = []
    if r["competitor_total"]:
        out.append(f"presente su {r['competitor_count']}/{r['competitor_total']} competitor Cerebro")
    if r.get("best_organic_rank") is not None:
        med = r.get("median_organic_rank")
        out.append(f"Organic Rank migliore {r['best_organic_rank']:g}" + (f" (mediana {med:g})" if med is not None and r["organic_rank_count"] > 1 else ""))
    if r.get("best_sponsored_rank") is not None:
        out.append(f"Sponsored Rank migliore {r['best_sponsored_rank']:g} ({r['sponsored_competitor_count']} competitor sponsorizzati)")
    sv = r.get("search_volume_used")
    if sv is not None:
        out.append(f"Search Volume {sv:g}" + (" (basso, ma non è un motivo di esclusione)" if sv < 100 else ""))
    if rel["value"] is not None:
        out.append("pertinenza " + ("alta" if rel["value"] >= 0.7 else "buona" if rel["value"] >= 0.5 else "parziale" if rel["value"] >= 0.3 else "bassa") + " con il libro")
    if rel["intent_conflict"]:
        out.append("intento diverso dal libro: " + ", ".join(rel["intent_conflict"]))
    if r.get("brand_or_ip"):
        out.append("keyword brand/IP")
    if r.get("rocket"):
        out.append("dati Publisher Rocket presenti")
    return out


def _badges(r: dict) -> list:
    b = []
    if r.get("organic_top10"):
        b.append("ORGANIC TOP 10")
    if r.get("sponsored_top10"):
        b.append("SPONSORED TOP 10")
    if r["competitor_count"] >= 2:
        b.append("MULTI-COMPETITOR")
    return b


def unify(cerebro_agg: dict | None, rocket_records: list | None = None, profile: dict | None = None, rules: dict | None = None, brand_terms=None) -> dict:
    """Unisce le evidenze Cerebro (aggregato di competitor.aggregate) e Rocket in UNA riga per keyword, con punteggio, priorita', badge e tipo di lancio.
    `profile` = build_profile(...) (None = pertinenza non valutata). Ritorna {rows (ordinate per priorita'), summary}. Nessuna scrittura."""
    cfg = config(rules)
    agg_rows = {k["keyword_norm"]: k for k in (cerebro_agg or {}).get("keywords", [])}
    total = (cerebro_agg or {}).get("total", 0)
    rocket = _rocket_index(rocket_records or [])
    dist = {"iq": sorted(v for v in (k.get("cerebro_iq") for k in agg_rows.values()) if v is not None),
            "sales": sorted(v for v in (k.get("keyword_sales") for k in agg_rows.values()) if v is not None)}
    rows = []
    for norm in sorted(set(agg_rows) | set(rocket)):
        a = agg_rows.get(norm)
        ro = rocket.get(norm)
        base = dict(a) if a else {"keyword_norm": norm, "keyword": ro["keyword"], "competitor_count": 0, "competitor_total": total, "competitor_share": f"0/{total}", "share": 0.0, "asins": [],
                                  "best_organic_rank": None, "median_organic_rank": None, "organic_rank_count": 0, "best_sponsored_rank": None, "sponsored_competitor_count": 0,
                                  "search_volume": None, "search_volume_max": None, "keyword_sales": None, "title_density": None, "competing_products": None, "cerebro_iq": None,
                                  "suggested_bid": None, "bid_min": None, "bid_max": None, "per_competitor": {}, "brand_or_ip": bool(C.brand_match(norm, brand_terms or C.BRAND_SEED)),
                                  "is_asin_like": C.is_asin_like(norm), "organic_top10": False, "sponsored_top10": False, "top10_category": "NONE", "organic_top10_asins": [],
                                  "sponsored_top10_asins": [], "top10_competitor_count": 0}
        base["sources"] = (["HELIUM10_CEREBRO"] if a else []) + (["PUBLISHER_ROCKET"] if ro else [])
        base["source_label"] = " + ".join(s for s, f in (("ROCKET", ro), ("CEREBRO", a)) if f)
        base["cerebro_search_volume"] = a["search_volume"] if a else None
        base["rocket_search_volume"] = ro.get("search_volume") if ro else None
        base["rocket"] = ({k: v for k, v in ro.items() if k not in ("keyword",)} if ro else None)
        base["search_volume_used"] = base["cerebro_search_volume"] if base["cerebro_search_volume"] is not None else base["rocket_search_volume"]
        rows.append(base)
    for r in rows:
        rel = relevance(r["keyword_norm"], profile)
        r["relevance"], r["intent_conflict"] = rel["value"], rel["intent_conflict"]
        eff = rel["value"]
        if eff is not None and r["competitor_count"] >= cfg["p1_min_competitors"] and r.get("organic_top10"):
            eff = max(eff, cfg["consensus_relevance_floor"])             # forte consenso dei competitor: mai scartata per il solo lessico
        rel_eff = {**rel, "value": eff}
        comp = _components(r, rel_eff, dist, cfg)
        r["score"], r["score_components"] = _score(comp, cfg["weights"])
        bonus = (cfg["organic_top10_bonus"] if r.get("organic_top10") else 0) + (cfg["sponsored_top10_bonus"] if r.get("sponsored_top10") else 0)
        r["score"] = round(min(100.0, r["score"] + bonus), 1) if r["score"] else r["score"]
        r["top10_bonus"] = bonus
        prio, why = _priority(r, eff, cfg)
        r["priority"], r["priority_label"], r["priority_reason"] = prio, PRIORITY_LABELS[prio], why
        r["badges"] = _badges(r)
        r["launch_type"] = _launch_type(r, eff, cfg) if prio in (P1, P2, P3) else None
        r["recommended_match"] = "PHRASE" if prio in (P1, P2, P3) else None
        r["reasons"] = _reasons(r, rel, prio)
        r["reason"] = "; ".join(r["reasons"]) or "evidenza limitata"
    rows.sort(key=lambda r: (PRIORITY_ORDER[r["priority"]], -r["score"], -r["competitor_count"], C.rank_sort_key(r["best_organic_rank"]), r["keyword_norm"]))
    return {"rows": rows, "summary": summary(rows, total, rocket_records or [], cerebro_agg)}


def _priority(r: dict, rel, cfg: dict) -> tuple:
    if r.get("brand_or_ip"):
        return EXCLUDED, "keyword brand/IP"
    if r.get("is_asin_like"):
        return EXCLUDED, "è un ASIN/ISBN, non una keyword"
    if r["intent_conflict"]:
        return EXCLUDED, "intento diverso dal libro (" + ", ".join(r["intent_conflict"]) + ")"
    if rel is not None and rel < cfg["exclude_below_relevance"]:
        return EXCLUDED, "pertinenza troppo bassa con il libro"
    n, total, s = r["competitor_count"], r["competitor_total"], r["score"]
    top10 = r.get("organic_top10") or r.get("sponsored_top10")
    ok = lambda lo: rel is None or rel >= lo                        # noqa: E731  (senza libro la pertinenza non blocca: resta una proposta da verificare)
    p1_n = min(cfg["p1_min_competitors"], max(total, 2))
    if ok(cfg["p1_min_relevance"]) and n >= p1_n and total >= 2 and r.get("organic_top10") and s >= cfg["p1_min_score"]:
        return P1, "keyword molto rilevante, presente su più competitor con rank forti"
    if ok(cfg["p2_min_relevance"]) and n >= 1 and (s >= cfg["p2_min_score"] or (top10 and n >= 2)):
        return P2, "buona rilevanza e volume/rank interessanti"
    if ok(cfg["p3_min_relevance"]) and (s >= cfg["p3_min_score"] or n >= 1 or r["rocket"]):
        return P3, "keyword coerente con evidenza limitata (da testare in piccolo)"
    return WEAK, "prove insufficienti"


def _launch_type(r: dict, rel, cfg: dict) -> str:
    """PHRASE-first. EXACT solo con evidenza MOLTO forte: >= exact_min_competitors competitor, Organic Rank <= soglia, Sponsored Rank <= soglia, pertinenza alta, volume minimo.
    L'etichetta significa «da considerare eventualmente anche in Exact», MAI «crea subito Exact»: il piano resta una proposta manuale PHRASE-first."""
    strong = (r["competitor_count"] >= cfg["exact_min_competitors"] and r.get("best_organic_rank") is not None and r["best_organic_rank"] <= cfg["exact_max_organic_rank"]
              and r.get("best_sponsored_rank") is not None and r["best_sponsored_rank"] <= cfg["exact_max_sponsored_rank"] and rel is not None and rel >= cfg["exact_min_relevance"]
              and r.get("search_volume_used") is not None and r["search_volume_used"] >= cfg["exact_min_volume"])
    if strong and r["priority"] == P1:
        return LAUNCH_EXACT
    return LAUNCH_CORE if r["priority"] == P1 else LAUNCH_TEST


def summary(rows: list, total: int, rocket_records: list, cerebro_agg: dict | None) -> dict:
    cer = [r for r in rows if "HELIUM10_CEREBRO" in r["sources"]]
    by_count = Counter(r["competitor_count"] for r in cer)
    return {"cerebro_competitors": total, "rocket_files": len(rocket_records), "unique_keywords": len(rows), "cerebro_unique_keywords": len(cer),
            "rocket_unique_keywords": sum(1 for r in rows if "PUBLISHER_ROCKET" in r["sources"]), "both_sources": sum(1 for r in rows if len(r["sources"]) == 2),
            "at_least_2": sum(1 for r in cer if r["competitor_count"] >= 2), "at_least_3": sum(1 for r in cer if r["competitor_count"] >= 3),
            "by_competitor_count": {k: by_count[k] for k in sorted(by_count, reverse=True)},
            "by_priority": {p: sum(1 for r in rows if r["priority"] == p) for p in (P1, P2, P3, WEAK, EXCLUDED)}}


def launch_plan(rows: list, limit: int = 25) -> list:
    """PIANO KEYWORD di lancio (proposta MANUALE, nessuna campagna creata): le candidate P1/P2/P3 con i campi richiesti. Ordinate per priorita' e punteggio."""
    out = []
    for r in rows:
        if r["priority"] not in (P1, P2, P3):
            continue
        out.append({"keyword": r["keyword"], "priority": PRIORITY_LABELS[r["priority"]], "source": r["source_label"], "competitor_count": r["competitor_count"],
                    "competitor_total": r["competitor_total"], "search_volume": r["search_volume_used"], "best_organic_rank": r["best_organic_rank"],
                    "best_sponsored_rank": r["best_sponsored_rank"], "suggested_bid": r["suggested_bid"], "bid_min": r["bid_min"], "bid_max": r["bid_max"], "reason": r["reason"],
                    "launch_type": r["launch_type"], "badges": r["badges"], "score": r["score"]})
        if len(out) >= limit:
            break
    return out


def agg_from_parsed(parsed_files: list) -> dict | None:
    """Aggregato multi-competitor (competitor.aggregate) dai parsed Cerebro; None se non ci sono file."""
    return C.aggregate(cerebro_sources(parsed_files)) if parsed_files else None


def build(agg: dict | None, rocket_records: list | None, profile: dict | None, rules: dict | None = None) -> dict:
    """UNICO punto di ingresso dell'analisi (stessa logica per «Ricerca keyword / competitor» e «Lancio nuovo libro»): unione per keyword + piano di lancio + riepilogo."""
    res = unify(agg, rocket_records or [], profile, rules)
    res["plan"] = launch_plan(res["rows"])
    res["has_profile"] = bool(profile and profile.get("has_profile"))
    res["top10_count"] = sum(1 for r in res["rows"] if r.get("organic_top10") or r.get("sponsored_top10"))
    return res


def profile_from_plan(plan: dict, extra: str | None = None) -> dict:
    """Profilo del libro dalla BOZZA di lancio (titolo, sottotitolo, descrizione, keyword KDP, categorie): nessun dato da reinserire. `extra` = integrazione facoltativa."""
    b = (plan or {}).get("book") or {}
    desc = " ".join(x for x in (b.get("description"), extra) if x)
    return build_profile(b.get("title"), b.get("subtitle"), list(b.get("kdp_keywords") or []), list(b.get("categories") or []), desc or None)


PRIORITY_GROUPS = (("P1", (P1,)), ("P2", (P2,)), ("P3", (P3,)), ("OUT", (WEAK, EXCLUDED)))
