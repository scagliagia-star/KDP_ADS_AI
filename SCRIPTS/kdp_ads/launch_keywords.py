"""LANCIO NUOVO LIBRO V2: piano keyword Ads INIZIALE ricavato dalla bozza di lancio. MANUAL DECISION SUPPORT, funzioni PURE e deterministiche:
nessuna rete, nessuna scrittura, nessuna campagna Amazon creata. Il piano viene salvato nella bozza (chiave "keyword_plan") da kdp_ads.launch.

COME FUNZIONA
1. CANDIDATI da titolo, sottotitolo, keyword KDP, categorie e descrizione (mai da dati competitor inventati). Ogni candidato conserva TUTTE le fonti
   e i testi originali. Normalizzazione: minuscolo, punteggiatura e spazi ridotti, duplicati fusi per forma normalizzata. Singolare e plurale
   NON vengono fusi: restano due candidati, collegati da `variant_of`.
2. PERTINENZA (relevance, 0-1) = quanto i termini DISTINTIVI della frase sono sostenuti dal profilo del libro (titolo 1.0, keyword KDP 0.9,
   sottotitolo 0.8, categorie 0.7, descrizione 0.4; i termini generici pesano meno; una keyword KDP non puo' sostenere se stessa). Un termine e'
   TEMATICO solo se compare in almeno 2 fonti indipendenti (es. titolo + keyword KDP): un termine presente in una sola fonte (tipico delle frasi
   di marketing, "balanced projects") sostiene meno (x0.55). Bonus se l'intera frase compare nel titolo (+0.25) o in sottotitolo/categorie (+0.15).
   Le frasi DERIVATE (n-grammi di titolo/sottotitolo/descrizione) sono proposte solo se tutti i loro termini distintivi sono tematici.
3. EVIDENCE_SCORE (0-100) = 60% pertinenza + 20% diversita' delle fonti (non il semplice conteggio) + 12% specificita' della forma
   (2-4 parole e' il massimo) + 8% conferma di competitor reali. Senza dati competitor il termine competitor non c'e' e il punteggio e' riscalato:
   l'assenza di dati non penalizza e non viene inventata. La pertinenza pesa quindi molto piu' del numero di fonti.
4. CORE TOPIC / TOPIC ANCHOR: il tema del libro NON e' un singolo termine ma la COMBINAZIONE dei concetti centrali del titolo (segmento prima di
   ":" o "-", es. "Crochet Flowers" => crochet + flower), confermati/estesi da sottotitolo, keyword KDP, descrizione e categorie (anchor secondari:
   termini di titolo/sottotitolo corroborati da >= 2 fonti). Singolare e plurale sono lo stesso CONCETTO per la pertinenza (flower = flowers) ma
   restano keyword separate nei dati. Una keyword e' SPECIFICA se copre tutti i concetti centrali, oppure ne copre almeno uno e aggiunge un termine
   specifico (una scelta esplicita dell'utente o un anchor secondario). Se contiene solo il concetto ampio della nicchia ("crochet", "crochet book",
   "beginner crochet book") e' GENERALE: mai EXACT, mai PHRASE, punteggio ridotto e al piu' DISCOVERY.
5. CLASSIFICAZIONE: EXACT (specifica, copre tutto il tema, pertinenza alta) / PHRASE (specifica e pertinente) / DISCOVERY (ampia o parziale) /
   REJECT. Una keyword generale puo' salire da DISCOVERY a PHRASE SOLO con evidenza esterna forte (competitor reali, search term reali, provider,
   dati di ricerca): una menzione nella descrizione non basta.
6. PIANO CAMPAGNE (non operativo): EXACT <= 15, PHRASE <= 30 (si mostra meno se i candidati validi sono meno), AUTO sempre come discovery iniziale
   (nessuna keyword), COMPETITOR solo con ASIN competitor VALIDATO forniti dall'utente."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime

from . import launch as L

PLAN_VERSION = 1
RULESET = "launch_keywords_v2"

RULES = {
    "weights": {"relevance": 0.60, "source_diversity": 0.20, "form": 0.12, "competitor": 0.08},
    "source_weights": {"title": 1.0, "kdp_keyword": 0.9, "subtitle": 0.8, "category": 0.7, "description": 0.4},
    "phrase_bonus": {"title": 0.25, "subtitle": 0.15, "category": 0.15},
    "generic_token_weight": 0.4, "single_source_factor": 0.55, "kdp_modifier_credit": 0.4, "topic_min_sources": 2,
    "exact_min_relevance": 0.75, "exact_min_relevance_full_topic": 0.55, "phrase_min_relevance": 0.50, "discovery_min_relevance": 0.25,
    "general_score_factor": 0.65, "general_score_cap": 60, "max_core_concepts": 4,
    "external_evidence_kinds": ["competitor", "search_term", "provider", "research_data"],
    "exact_words": (2, 5), "max_words": 6, "min_chars": 3,
    "confidence": {"HIGH": 75, "MEDIUM": 50},
    "max_exact": 15, "max_phrase": 30, "max_competitors": 5,
    "ngram_sizes_core": (2, 3, 4), "ngram_sizes_description": (2, 3),
}

STOPWORDS = frozenset("""a an and are as at be but by for from how i in into is it its of on or that the their this to up we what with you your
 about after all also any can do does each get has have more most not our out so than then these they those very will without
 il lo la i gli le un uno una di da in con su per tra fra e o ma che come del della dei delle al alla ai alle nel nella non piu""".split())
GENERIC_TOKENS = frozenset("""book books guide guides manual handbook workbook complete ultimate easy simple best new edition step steps beginner beginners
 tips tricks tutorial tutorials learn learning made everyone anyone kindle paperback ebook read reading volume series perfect great good
 essential basic basics practical illustrated full ideas projects project create includes include including inside features covers
 contains gift gifts""".split())
# termini promozionali / di classifica: non utilizzabili come keyword Ads (policy) e privi di intento
PROMO_TOKENS = frozenset("free bestseller bestselling cheap discount sale amazon kindle unlimited ebook pdf download".split())
PROMO_PHRASES = ("best seller", "number one", "new release", "buy now", "click here")

SOURCE_LABEL = {"title": "titolo", "subtitle": "sottotitolo", "kdp_keyword": "keyword KDP", "category": "categoria", "description": "descrizione"}
_SPLIT_TITLE = re.compile(r"[:–—|]| - ")
_SPLIT_CLAUSE = re.compile(r"[,;:|!?.–—]| - ")
_SPLIT_CAT = re.compile(r"\s*(?:>|/|›|»)\s*")


# ------------------------------------------------------------------ normalizzazione
def kw_norm(s) -> str:
    """Forma di visualizzazione e di deduplica: minuscolo, punteggiatura e spazi ridotti (gli accenti restano)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w']+|_", " ", str(s or "").lower())).strip()


def fold(s) -> str:
    """Forma di confronto: come kw_norm ma senza accenti."""
    return L.norm_text(s)


def tokens(s) -> list:
    return fold(s).split()


def _stem(t: str) -> str:
    """Radice grossolana SOLO per collegare singolare/plurale (mai per fondere i candidati)."""
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("i", "o"), ("e", "a")):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[:-len(suf)] + rep
    return t


def _contains(text, phrase_folded) -> bool:
    return bool(phrase_folded) and f" {phrase_folded} " in f" {fold(text)} "


def _ngrams(words: list, sizes) -> list:
    out = []
    for n in sizes:
        for i in range(len(words) - n + 1):
            out.append(words[i:i + n])
    return out


def _edge_ok(words_folded: list) -> bool:
    """Un n-gramma non deve iniziare/finire con una stopword ne' contenerne solo."""
    return bool(words_folded) and words_folded[0] not in STOPWORDS and words_folded[-1] not in STOPWORDS


# ------------------------------------------------------------------ candidati
class _Cand:
    def __init__(self, key):
        self.key = key
        self.src = []                      # [(source_type, original_text)]
        self.kdp_idx = set()               # indici delle keyword KDP da cui deriva (non possono sostenere se stesse)
        self.whole = set()                 # fonti in cui la frase e' un elemento INTERO (titolo/segmento, keyword KDP, categoria...)

    def add(self, source, original, kdp_idx=None, whole=False):
        if (source, original) not in self.src:
            self.src.append((source, original))
        if kdp_idx is not None:
            self.kdp_idx.add(kdp_idx)
        if whole:
            self.whole.add(source)


def generate_candidates(plan: dict) -> dict:
    """{chiave_normalizzata: _Cand} da titolo, sottotitolo, keyword KDP, categorie, descrizione. Nessun dato esterno."""
    b = plan["book"]
    cands: dict = {}

    def add(phrase, source, original, kdp_idx=None, whole=False):
        k = kw_norm(phrase)
        if len(k) < RULES["min_chars"]:
            return
        cands.setdefault(k, _Cand(k)).add(source, original, kdp_idx, whole)

    def from_segment(seg, source, original, kdp_idx=None, whole_ok=True, sizes=RULES["ngram_sizes_core"]):
        words = kw_norm(seg).split()
        if not words:
            return
        if whole_ok and len(words) <= RULES["max_words"]:
            add(" ".join(words), source, original, kdp_idx, whole=True)
        fw = [fold(w) for w in words]
        for n in sizes:
            if n >= len(words):
                continue
            for i in range(len(words) - n + 1):
                if _edge_ok(fw[i:i + n]):
                    add(" ".join(words[i:i + n]), source, original, kdp_idx)

    title = b.get("title") or ""
    for seg in _SPLIT_TITLE.split(title):
        from_segment(seg, "title", title)
    subtitle = b.get("subtitle") or ""
    for seg in _SPLIT_CLAUSE.split(subtitle):
        from_segment(seg, "subtitle", subtitle)
    for i, k in enumerate(b.get("kdp_keywords") or []):
        from_segment(k, "kdp_keyword", k, kdp_idx=i)
    for c in b.get("categories") or []:
        parts = [p for p in _SPLIT_CAT.split(c) if p.strip()]
        if parts:                                                       # solo la FOGLIA: i nodi superiori ("Books", "Crafts, Hobbies & Home") sono troppo generici
            from_segment(parts[-1], "category", c)
    for sent in re.split(r"[.!?;\n]+", b.get("description") or ""):
        for clause in _SPLIT_CLAUSE.split(sent):
            words = kw_norm(clause).split()
            fw = [fold(w) for w in words]
            for n in RULES["ngram_sizes_description"]:
                for i in range(len(words) - n + 1):
                    if _edge_ok(fw[i:i + n]):
                        add(" ".join(words[i:i + n]), "description", clause.strip())
    return cands


# ------------------------------------------------------------------ pertinenza
def _profile(plan: dict) -> dict:
    b = plan["book"]
    w = RULES["source_weights"]
    def stems(s):
        return {_stem(x) for x in tokens(s)}            # singolare/plurale: stesso concetto per la pertinenza (le keyword restano separate)
    entries = [("title", None, stems(b.get("title")), w["title"]),
               ("subtitle", None, stems(b.get("subtitle")), w["subtitle"]),
               ("description", None, stems(b.get("description")), w["description"])]
    for c in b.get("categories") or []:
        leaf = [p for p in _SPLIT_CAT.split(c) if p.strip()]
        entries.append(("category", None, stems(leaf[-1] if leaf else c), w["category"]))
    for i, k in enumerate(b.get("kdp_keywords") or []):
        entries.append(("kdp_keyword", i, stems(k), w["kdp_keyword"]))
    prof = {"entries": entries}
    prof["core"] = _core_topic(plan, prof)
    return prof


def _is_content(t) -> bool:
    return t not in STOPWORDS and not _is_generic(t) and t not in PROMO_TOKENS


def _count(stem, prof) -> int:
    return sum(1 for src, idx, toks, wt in prof["entries"] if stem in toks)


def _surfaces(plan: dict) -> dict:
    """{radice: [forme come compaiono nei testi]}: per mostrare "flower/flowers" come UN concetto."""
    b = plan["book"]
    out: dict = {}
    for text in [b.get("title"), b.get("subtitle"), b.get("description"), *(b.get("kdp_keywords") or []), *(b.get("categories") or [])]:
        for w in tokens(text):
            out.setdefault(_stem(w), set()).add(w)
    return {k: sorted(v) for k, v in out.items()}


def _core_topic(plan: dict, prof: dict) -> dict:
    """CORE TOPIC: concetti centrali (radici) dal segmento principale del TITOLO, anchor secondari da titolo/sottotitolo corroborati.
    Il tema e' la combinazione dei concetti centrali, non un singolo termine."""
    b = plan["book"]
    head_txt = _SPLIT_TITLE.split(b.get("title") or "")[0]
    head = []
    for w in tokens(head_txt):
        s = _stem(w)
        if _is_content(w) and s not in head:
            head.append(s)
    if len(head) > RULES["max_core_concepts"]:             # titolo lungo senza separatore: tengo i concetti piu' corroborati
        keep = sorted(head, key=lambda s: (-_count(s, prof), head.index(s)))[:RULES["max_core_concepts"]]
        head = [s for s in head if s in keep]
    secondary = []
    for text in (b.get("title"), b.get("subtitle")):
        for w in tokens(text):
            s = _stem(w)
            if _is_content(w) and s not in head and s not in secondary and _count(s, prof) >= RULES["topic_min_sources"]:
                secondary.append(s)
    surf = _surfaces(plan)
    return {"head": head, "secondary": secondary, "surfaces": {s: surf.get(s, [s]) for s in head + secondary}}


def _support(token, prof, exclude_kdp: set) -> float:
    """Sostegno di un termine dal profilo (una keyword KDP non sostiene se stessa); x0.55 se il termine compare in UNA sola fonte."""
    token = _stem(token)
    ws = [wt for src, idx, toks, wt in prof["entries"] if token in toks and not (src == "kdp_keyword" and idx in exclude_kdp)]
    if not ws:
        return 0.0
    return max(ws) * (1.0 if len(ws) >= RULES["topic_min_sources"] else RULES["single_source_factor"])


def _is_topic(token, prof, exclude_kdp: set) -> bool:
    token = _stem(token)
    return sum(1 for src, idx, toks, wt in prof["entries"] if token in toks and not (src == "kdp_keyword" and idx in exclude_kdp)) >= RULES["topic_min_sources"]


def _is_generic(t) -> bool:
    return t in GENERIC_TOKENS


def relevance(cand: _Cand, plan: dict, prof: dict) -> dict:
    b = plan["book"]
    toks = [t for t in tokens(cand.key) if t not in STOPWORDS]
    distinctive = [t for t in toks if not _is_generic(t) and t not in PROMO_TOKENS]
    gw = RULES["generic_token_weight"]
    topic = [t for t in distinctive if _is_topic(t, prof, cand.kdp_idx)]
    # keyword KDP INTERA (scelta esplicita dell'utente) ancorata al tema del libro: i modificatori che nessun'altra fonte sostiene
    # (es. "patterns" in "crochet flower patterns") valgono meta'. Senza nessun termine tematico (es. "iphone case") nessun credito.
    credit = RULES["kdp_modifier_credit"] if ("kdp_keyword" in cand.whole and topic) else 0.0
    num = den = 0.0
    for t in toks:
        wt = gw if _is_generic(t) else 1.0
        num += wt * max(_support(t, prof, cand.kdp_idx), credit)
        den += wt
    base = num / den if den else 0.0
    kf = fold(cand.key)
    bonus = 0.0
    if _contains(b.get("title"), kf):
        bonus = max(bonus, RULES["phrase_bonus"]["title"])
    if _contains(b.get("subtitle"), kf):
        bonus = max(bonus, RULES["phrase_bonus"]["subtitle"])
    if any(_contains(c, kf) for c in b.get("categories") or []):
        bonus = max(bonus, RULES["phrase_bonus"]["category"])
    bonus *= min(1.0, base / 0.8)                 # la presenza in titolo vale solo se i termini sono davvero tematici
    return {"relevance": min(1.0, base + bonus), "base": base, "bonus": bonus, "tokens": toks, "distinctive": distinctive, "topic": topic}


# ------------------------------------------------------------------ valutazione e classificazione
def _confidence(score) -> str:
    c = RULES["confidence"]
    return "HIGH" if score >= c["HIGH"] else "MEDIUM" if score >= c["MEDIUM"] else "LOW"


def _reject_reason(cand, rel) -> tuple[str, str] | None:
    """(motivo, confidenza della scelta) se la frase va scartata a prescindere dalla pertinenza."""
    k = fold(cand.key)
    words = k.split()
    if len(k) < RULES["min_chars"] or len(words) > RULES["max_words"]:
        return ("Frase troppo corta o troppo lunga per una keyword Ads", "HIGH")
    promo = [t for t in words if t in PROMO_TOKENS] + [p for p in PROMO_PHRASES if p in k]
    if promo:
        return (f"Termine promozionale/di classifica non adatto alle Ads: {', '.join(sorted(set(promo)))}", "HIGH")
    if not rel["tokens"]:
        return ("Composta solo da parole vuote (articoli, preposizioni)", "HIGH")
    if not rel["distinctive"]:
        return (f"Troppo generica: tutte le parole sono generiche ({', '.join(rel['tokens'])})", "HIGH")
    return None


def _anchor_analysis(cand: _Cand, rel: dict, prof: dict) -> dict:
    """Copertura dei concetti centrali e specificita' della keyword rispetto al CORE TOPIC del libro."""
    core = prof["core"]
    H, S = core["head"], core["secondary"]
    stems = [_stem(t) for t in rel["distinctive"]]
    covered = [h for h in H if h in stems]
    explicit = bool({"kdp_keyword", "title", "subtitle"} & set(cand.whole))          # modificatore scelto esplicitamente dall'utente/autore
    extra = [t for t in rel["distinctive"] if _stem(t) not in H and (_stem(t) in S or explicit)]
    secondary = [s for s in S if s in stems]
    full = bool(H) and len(covered) == len(H)
    if not H:
        specific = True                                       # nessun concetto centrale derivabile: si usa solo la pertinenza
    else:
        specific = full or (bool(covered) and bool(extra)) or (not covered and bool(secondary))

    def names(ss):
        return " + ".join("/".join(core["surfaces"].get(s, [s])) for s in ss)
    return {"covered": covered, "full": full, "specific": specific, "extra": extra, "head": H, "head_label": names(H), "covered_label": names(covered)}


def evaluate(cand: _Cand, plan: dict, prof: dict, competitor_data: dict | None, all_keys: dict, external: dict | None = None) -> dict:
    rel = relevance(cand, plan, prof)
    anc = _anchor_analysis(cand, rel, prof)
    R = rel["relevance"]
    types = sorted({s for s, _ in cand.src}, key=list(SOURCE_LABEL).index)
    cd = competitor_data or {}
    kf = fold(cand.key)
    hits = sorted(a for a, t in cd.items() if any(_contains(v, kf) for v in t.values()))
    n_words = len(cand.key.split())
    core_types = [t for t in types if t != "description"]
    S = min(1.0, (len(core_types) + 0.5 * ("description" in types)) / 3.0)
    F = 1.0 if 2 <= n_words <= 4 else 0.6 if n_words == 5 else 0.4
    w = RULES["weights"]
    used = w["relevance"] + w["source_diversity"] + w["form"] + (w["competitor"] if cd else 0.0)
    total = w["relevance"] * R + w["source_diversity"] * S + w["form"] * F + (w["competitor"] * (1.0 if hits else 0.0) if cd else 0.0)
    score = round(100 * total / used)
    ext_kinds = sorted(set((external or {}).get(cand.key) or []) & set(RULES["external_evidence_kinds"]))
    strong = [a for a, tx in cd.items() if _contains(tx.get("title"), kf) or _contains(tx.get("subtitle"), kf)]   # la sola categoria del competitor non basta
    if strong and n_words >= 2 and "competitor" not in ext_kinds:      # una parola sola in un titolo competitor non e' una conferma forte
        ext_kinds = sorted(ext_kinds + ["competitor"])
    if not anc["specific"]:                                   # keyword solo sul concetto ampio della nicchia: meno priorita'
        score = min(round(score * RULES["general_score_factor"]), RULES["general_score_cap"])
    variants = sorted({o for o in all_keys if o != cand.key and " ".join(_stem(t) for t in tokens(o)) == " ".join(_stem(t) for t in tokens(cand.key))})

    facts = []
    if cand.whole:
        facts.append("frase intera in " + ", ".join(SOURCE_LABEL[t] for t in types if t in cand.whole))
    elif core_types:
        facts.append("deriva da " + ", ".join(SOURCE_LABEL[t] for t in core_types))
    else:
        facts.append("compare solo nella descrizione")
    rej = _reject_reason(cand, rel)
    cls, conf, reason = None, None, None
    only_desc = types == ["description"]
    if rej:
        cls, (reason, conf) = "REJECT", rej
    elif not rel["topic"] and "kdp_keyword" not in types:
        cls, conf = "REJECT", "MEDIUM"
        reason = "Frase non corroborata: i termini compaiono in una sola fonte, probabilmente testo di marketing e non intento di ricerca"
    elif R < RULES["discovery_min_relevance"]:
        cls, conf = "REJECT", "MEDIUM"
        reason = ("Fuori tema: nessun termine distintivo sostenuto da titolo, sottotitolo, categorie o descrizione"
                  + (" (keyword KDP inserita da te: verificala)" if "kdp_keyword" in cand.whole else ""))
    elif (anc["specific"] and (anc["full"] if anc["head"] else True)
          and R >= (RULES["exact_min_relevance_full_topic"] if anc["head"] else RULES["exact_min_relevance"])
          and RULES["exact_words"][0] <= n_words <= RULES["exact_words"][1] and cand.whole
          and len(rel["distinctive"]) >= 2 and rel["topic"] and not only_desc):
        cls, conf = "EXACT", _confidence(score)
        reason = (f"Molto pertinente (pertinenza {R:.2f}): copre il tema centrale del libro ({anc['head_label'] or 'n/d'}), {n_words} parole con intento "
                  "specifico; " + "; ".join(facts))
    elif anc["specific"] and R >= RULES["phrase_min_relevance"] and not only_desc and rel["topic"]:
        cls, conf = "PHRASE", _confidence(score)
        anchor = anc["covered_label"] or ", ".join(anc["extra"])
        why = (f"contiene un anchor specifico ({anchor}" + (f" + {', '.join(anc['extra'])}" if anc["covered"] and anc["extra"] else "")
               + ") ma non copre tutto il tema centrale")
        reason = f"Pertinente (pertinenza {R:.2f}) e {why}: utile alla scoperta; " + "; ".join(facts)
    elif (not anc["specific"] and ext_kinds and R >= RULES["phrase_min_relevance"] and not only_desc and rel["topic"]):
        cls, conf = "PHRASE", _confidence(score)
        reason = (f"Keyword ampia (copre solo {anc['covered_label'] or 'il concetto generale'} di {anc['head_label']}) elevata da DISCOVERY a PHRASE "
                  f"per evidenza esterna forte: {', '.join(ext_kinds)}; " + "; ".join(facts))
    else:
        cls, conf = "DISCOVERY", _confidence(score)
        if only_desc:
            why = "ricavata solo dalla descrizione"
        elif not rel["topic"]:
            why = "nessun termine tematico corroborato"
        elif not anc["specific"]:
            why = (f"troppo ampia: copre solo {anc['covered_label'] or 'il concetto generale'} del tema centrale ({anc['head_label']}) senza un anchor "
                   "specifico; sale a PHRASE solo con evidenza esterna forte (competitor reali, search term reali, provider, dati di ricerca)")
        else:
            why = "pertinenza moderata"
        reason = f"Da testare con cautela: {why} (pertinenza {R:.2f}); " + "; ".join(facts)
    if hits:
        reason += f"; confermata da competitor reali: {', '.join(hits)}"
    if variants:
        reason += f"; variante singolare/plurale di: {', '.join(variants)} (non fuse)"
    sources = [{"source": s, "original": o} for s, o in cand.src] + [{"source": "competitor", "asin": a} for a in hits]
    derived = not cand.whole and "category" not in types
    edge_generic = bool(rel["tokens"]) and (_is_generic(tokens(cand.key)[0]) or _is_generic(tokens(cand.key)[-1])) and not ({"kdp_keyword"} & set(types))
    return {"_derived": derived, "_edge_generic": edge_generic, "_distinctive": len(rel["distinctive"]), "_all_topic": len(rel["topic"]) == len(rel["distinctive"]),
            "keyword": cand.key, "sources": sources, "source_types": types + (["competitor"] if hits else []),
            "evidence_score": score, "confidence": conf, "recommended_match_type": cls, "reason": reason,
            "relevance": round(R, 3), "competitor_hits": hits, "variant_of": variants,
            "core_topic": {"covers_all_core_concepts": anc["full"], "specific": anc["specific"],
                           "covered": anc["covered_label"] or None, "external_evidence": ext_kinds}}


# ------------------------------------------------------------------ piano completo
def input_fingerprint(plan: dict) -> str:
    """Impronta degli ingressi che influenzano il piano: se cambia, il piano salvato e' superato (stale)."""
    b = plan["book"]
    ent = [(c["asin"], c.get("status"), c.get("enrichment")) for c in plan.get("competitors", [])]
    raw = json.dumps([b.get("title"), b.get("subtitle"), b.get("description"), b.get("kdp_keywords"), b.get("categories"),
                      b.get("marketplace"), b.get("price"), b.get("royalty_per_copy"), ent], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def book_profile(plan: dict, prof: dict | None = None) -> dict:
    b = plan["book"]
    prof = prof or _profile(plan)
    weight, count = {}, {}
    for src, idx, toks, wt in prof["entries"]:
        for t in toks:
            if _is_content(t):
                weight[t] = max(weight.get(t, 0.0), wt)
                count[t] = count.get(t, 0) + 1
    weight = {t: w for t, w in weight.items() if count[t] >= RULES["topic_min_sources"]}     # solo i termini tematici (>= 2 fonti)
    all_surf = _surfaces(plan)
    core = ["/".join(all_surf.get(t, [t])) for t in sorted(weight, key=lambda t: (-weight[t], t))[:10]]
    price, roy = b.get("price"), b.get("royalty_per_copy")
    comps = plan.get("competitors", [])
    return {"title": b.get("title"), "subtitle": b.get("subtitle"), "marketplace": b.get("marketplace"), "currency": b.get("currency"),
            "price": price, "royalty_per_copy": roy,
            "break_even_acos": round(roy / price * 100, 2) if price and roy else None,
            "categories": list(b.get("categories") or []), "kdp_keywords": list(b.get("kdp_keywords") or []), "main_terms": core,
            "core_concepts": ["/".join(prof["core"]["surfaces"][s]) for s in prof["core"]["head"]],
            "secondary_anchors": ["/".join(prof["core"]["surfaces"][s]) for s in prof["core"]["secondary"]],
            "competitors_valid": sum(1 for c in comps if c.get("status") == L.STATUS_VALID),
            "competitors_unverified": sum(1 for c in comps if c.get("status") != L.STATUS_VALID),
            "competitors_with_data": sum(1 for c in comps if c.get("enrichment"))}


def build_keyword_plan(plan: dict, competitor_data: dict | None = None, now: str | None = None, external_evidence: dict | None = None) -> dict:
    """Piano keyword + struttura campagne da una bozza. Funzione pura (non modifica la bozza, non scrive).
    competitor_data = {asin: {title, subtitle, categories}}; None = quelli arricchiti presenti nella bozza (oggi nessuno => nessun bonus).
    external_evidence = {keyword: [tipo, ...]} con tipo in RULES["external_evidence_kinds"] (search_term, provider, research_data, competitor): evidenza
    ESTERNA forte che puo' elevare una keyword generale da DISCOVERY a PHRASE. Oggi nessuna fonte la fornisce: parametro pronto per il futuro."""
    external_evidence = {kw_norm(k): list(v) for k, v in (external_evidence or {}).items()}
    cd = L.competitor_texts(plan) if competitor_data is None else competitor_data
    prof = _profile(plan)
    cands = generate_candidates(plan)
    evaluated, dropped = [], 0
    for key in sorted(cands):
        c = cands[key]
        ev = evaluate(c, plan, prof, cd, cands, external_evidence)
        derived, only_desc = ev.pop("_derived"), ev["source_types"] == ["description"]
        n_dist, all_topic, edge_generic = ev.pop("_distinctive"), ev.pop("_all_topic"), ev.pop("_edge_generic")
        # le frasi DERIVATE (n-grammi, non scelte dall'utente) sono proposte solo se tutti i termini distintivi sono tematici
        # (e, dalla sola descrizione, se ne hanno almeno 2): il resto e' rumore e viene solo contato, non elencato
        if derived and (ev["recommended_match_type"] == "REJECT" or not all_topic or edge_generic or (only_desc and n_dist < 2)):
            dropped += 1
            continue
        evaluated.append(ev)

    def order(x):
        return (-x["evidence_score"], -x["relevance"], x["keyword"])
    exact = sorted((e for e in evaluated if e["recommended_match_type"] == "EXACT"), key=order)
    phrase = sorted((e for e in evaluated if e["recommended_match_type"] == "PHRASE"), key=order)
    discovery = sorted((e for e in evaluated if e["recommended_match_type"] == "DISCOVERY"), key=order)
    rejected = sorted((e for e in evaluated if e["recommended_match_type"] == "REJECT"), key=order)
    # limiti di quantita': l'eccedenza scala di un livello (non si butta una keyword pertinente), mai riempimento artificiale
    over = exact[RULES["max_exact"]:]
    exact = exact[:RULES["max_exact"]]
    for e in over:
        e["recommended_match_type"], e["reason"] = "PHRASE", e["reason"] + f"; oltre il limite di {RULES['max_exact']} EXACT: proposta in PHRASE"
    phrase = sorted(phrase + over, key=order)
    over = phrase[RULES["max_phrase"]:]
    phrase = phrase[:RULES["max_phrase"]]
    for e in over:
        e["recommended_match_type"], e["reason"] = "DISCOVERY", e["reason"] + f"; oltre il limite di {RULES['max_phrase']} PHRASE: da valutare dopo"
    discovery = sorted(discovery + over, key=order)

    valid = [c for c in plan.get("competitors", []) if c.get("status") == L.STATUS_VALID][:RULES["max_competitors"]]
    campaigns = {
        "EXACT": {"keywords": [e["keyword"] for e in exact], "operational": False,
                  "note": "Lista consigliata: nessuna campagna viene creata. Se vuota, non ci sono keyword abbastanza pertinenti."},
        "PHRASE": {"keywords": [e["keyword"] for e in phrase], "operational": False, "note": "Lista consigliata: nessuna campagna viene creata."},
        "AUTO": {"keywords_required": False, "keywords": [], "operational": False, "role": "discovery iniziale",
                 "note": "Sempre valutabile come campagna di scoperta iniziale: non richiede keyword. Suggerimento non operativo."},
        "COMPETITOR": {"available": bool(valid), "asins": [c["asin"] for c in valid], "operational": False,
                       "source": L.SOURCE_USER_COMPETITOR,
                       "note": ("ASIN forniti dall'utente e VALIDATI solo formalmente (l'esistenza su Amazon non e' verificata). Suggerimento non operativo."
                                if valid else "Nessun ASIN competitor VALIDATO: campagna COMPETITOR non proposta.")},
    }
    return {"version": PLAN_VERSION, "ruleset": RULESET, "generated_at": now or datetime.now().isoformat(timespec="seconds"),
            "input_fingerprint": input_fingerprint(plan), "profile": book_profile(plan, prof),
            "competitor_data_available": bool(cd), "exact": exact, "phrase": phrase, "discovery": discovery, "rejected": rejected,
            "dropped_candidates": dropped, "campaigns": campaigns, "rules": {k: (list(v) if isinstance(v, tuple) else v) for k, v in RULES.items()},
            "limits": ["Nessun volume di ricerca, CPC, concorrenza o stagionalita': il piano si basa solo sui testi della bozza.",
                       "La pertinenza e' lessicale (termini condivisi col profilo del libro), non semantica: un sinonimo non presente nei testi non viene riconosciuto.",
                       "Nessun dato competitor reale" if not cd else "Dati competitor presenti solo per gli ASIN arricchiti da un provider.",
                       "Piano consigliato, non operativo: nessuna campagna Amazon creata o modificata."]}


def is_stale(plan: dict) -> bool:
    kp = plan.get("keyword_plan")
    return bool(kp) and kp.get("input_fingerprint") != input_fingerprint(plan)
