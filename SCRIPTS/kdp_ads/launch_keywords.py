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
6. RICERCA PUBLISHER ROCKET (facoltativa): le keyword dei file importati diventano candidati con fonte "publisher_rocket" e le loro metriche reali
   (volume, competizione, ...) si mostrano accanto alla keyword. NON sostengono mai il profilo del libro (non si auto-confermano). Collegamento a
   external_evidence: il tipo "research_data" viene attivato SOLO se la keyword e' PERTINENTE al tema (pertinenza >= soglia PHRASE, termine tematico,
   non scartata dalle regole) E confermata da un'altra fonte (titolo/sottotitolo/keyword KDP/categoria, un competitor validato con dati, o piu' file).
   Bonus punteggio (max +10) solo se pertinente. Una keyword fuori tema resta REJECT anche con volume alto. Metriche assenti: nessuna invenzione.
7. TITLE_TOPIC e COMMERCIAL_SEARCH_TOPIC. Un titolo puo' essere creativo ("Twenty Women, Twenty Mysteries") e non descrivere cio' che la gente
   cerca. Il titolo e' CREATIVO se il suo segmento principale contiene numeri scritti a parole o se meta' o piu' dei suoi termini non compaiono in
   nessun'altra fonte. Per i titoli creativi si usa il COMMERCIAL_SEARCH_TOPIC: concetti pesati da sottotitolo (1.0), keyword KDP (0.9), categorie
   (0.8), descrizione (0.55), frequenza nelle keyword Publisher Rocket (0.5, solo come conferma di termini gia' presenti) e titolo (0.35); un
   concetto e' FORTE se pesa >= 0.6 (x0.7 se compare in una sola fonte). I numeri ("twenty", "20") non sono mai concetti commerciali. EXACT = la
   keyword contiene il concetto "testa" del libro (quello presente nella gran parte delle keyword KDP) e copre >= 4 concetti forti (>= 3 se e' una
   keyword KDP/sottotitolo intera); PHRASE = >= 3 concetti forti, oppure la testa + almeno 1 altro concetto forte; DISCOVERY = largo o parziale
   ("short stories", "large print", "senior women"). La frase quasi identica al titolo e' una TITLE_QUERY (query brand del libro): elencata a
   parte, mai tra le keyword di lancio principali. Un'intento diverso (es. "kids" in un libro per adulti) o un'assenza totale di concetti forti
   significa REJECT; l'assenza delle parole del titolo NO.
8. NATURAL_QUERY_QUALITY: una frase DERIVATA automaticamente (n-gramma di titolo/sottotitolo/descrizione o sotto-frase di una keyword KDP) deve
   sembrare una query possibile: niente numeri, niente aggettivo/numero/preposizione finale ("mystery short", "20 large", "stories for senior").
   Le keyword scritte dall'utente e quelle Publisher Rocket NON vengono scartate per questa euristica.
9. TRE PUNTEGGI auditabili: RELEVANCE_SCORE (pertinenza commerciale), MARKET_EVIDENCE_SCORE (volume, competizione, titoli concorrenti, guadagni, conferma
   di competitor, piu' file: solo dai dati realmente presenti; None se non ce ne sono; i colori Green/Yellow/Red NON contano) e FINAL_LAUNCH_SCORE
   (la pertinenza viene prima: il peso del mercato cresce con la pertinenza e non puo' salvare una keyword fuori tema).
10. PIANO CAMPAGNE (non operativo): EXACT <= 15, PHRASE <= 30 (si mostra meno se i candidati validi sono meno), AUTO sempre come discovery iniziale
   (nessuna keyword), COMPETITOR solo con ASIN competitor VALIDATO forniti dall'utente."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime

from . import launch as L

PLAN_VERSION = 1
RULESET = "launch_keywords_v3"

RULES = {
    "weights": {"relevance": 0.60, "source_diversity": 0.20, "form": 0.12, "competitor": 0.08},
    "source_weights": {"title": 1.0, "kdp_keyword": 0.9, "subtitle": 0.8, "category": 0.7, "description": 0.4},
    "phrase_bonus": {"title": 0.25, "subtitle": 0.15, "category": 0.15},
    "generic_token_weight": 0.4, "single_source_factor": 0.55, "kdp_modifier_credit": 0.4, "topic_min_sources": 2,
    "exact_min_relevance": 0.75, "exact_min_relevance_full_topic": 0.55, "phrase_min_relevance": 0.50, "discovery_min_relevance": 0.25,
    "general_score_factor": 0.65, "general_score_cap": 60, "max_core_concepts": 4,
    "external_evidence_kinds": ["competitor", "search_term", "provider", "research_data"],
    "commercial": {"source_weights": {"subtitle": 1.0, "kdp_keyword": 0.9, "category": 0.8, "description": 0.55, "publisher_rocket": 0.5, "title": 0.35},
                   "single_source_factor": 0.7, "strong_min_weight": 0.6, "head_kdp_share": 0.6, "pr_df_min_count": 5, "pr_df_share": 0.02, "pr_filter_min_keywords": 50,
                   "exact_min_relevance": 0.7, "phrase_min_relevance": 0.5, "discovery_min_relevance": 0.25, "exact_min_strong": 4,
                   "exact_min_strong_explicit": 3, "phrase_min_strong": 3, "phrase_min_strong_with_head": 2, "exact_words": (3, 9),
                   "exact_min_market_if_data": 30, "exceptional_market": 80, "exceptional_volume": 1000, "pair_bonus": 0.05, "pair_bonus_max": 0.15,
                   "final_market_weight": 0.3, "final_market_full_at": 70, "phrase_bonus": {"subtitle": 0.25, "kdp_keyword": 0.25, "category": 0.15,
                                                                                         "description": 0.1, "title": 0.1}},
    "market": {"volume_max": 40, "competition_max": 20, "title_count_max": 10, "earnings_max": 10, "competitor_max": 15, "multi_file_max": 5},
    "research_bonus_base": 4, "research_bonus_competitor": 3, "research_bonus_multi_source": 3, "research_bonus_max": 10,
    "exact_words": (2, 5), "max_words": 6, "max_words_whole": 12, "min_chars": 3,
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

NUMBER_WORDS = frozenset("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
 twenty thirty forty fifty sixty seventy eighty ninety hundred thousand first second third""".split())
# parole che non possono chiudere una query naturale (aggettivi/modificatori che richiedono un nome dopo di se')
DANGLING_END = frozenset("""short large small big little long tiny easy simple quick best good great new old young funny gentle cozy cosy senior elderly
 older complete full free light dark clean historical modern classic graphic hopeful sweet warm cold hot cheap perfect ultimate essential basic
 beginner illustrated printed natural total whole entire first last next second third other another many several some any each every""".split())
# audience / formato / genere che, se non presenti nel libro, indicano un INTENTO diverso (es. "kids" in un libro per adulti)
INTENT_CONFLICT = frozenset("""kids kid children child toddlers toddler preschool teen teens teenager boys girls coloring colouring puzzle puzzles workbook
 cookbook cookbooks audiobook comic comics manga horror erotica erotic romance thriller thrillers fantasy paranormal western scifi dystopian christian
 bible devotional poetry poems textbook""".split())
SOURCE_LABEL = {"title": "titolo", "subtitle": "sottotitolo", "kdp_keyword": "keyword KDP", "category": "categoria", "description": "descrizione",
                "publisher_rocket": "Publisher Rocket"}
OWN_CORE_SOURCES = ("title", "subtitle", "kdp_keyword", "category")
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


_IRREGULAR = {"women": "woman", "men": "man", "children": "child", "people": "person"}


def _is_number(t: str) -> bool:
    return t in NUMBER_WORDS or bool(re.fullmatch(r"\d+(st|nd|rd|th)?", t or ""))


def _stem(t: str) -> str:
    """Radice grossolana SOLO per collegare singolare/plurale (mai per fondere i candidati)."""
    if t in _IRREGULAR:
        return _IRREGULAR[t]
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("i", "o"), ("e", "a")):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            return t[:-len(suf)] + rep
    return t


_DANGLING_STEMS = frozenset(_stem(w) for w in DANGLING_END)


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


def generate_candidates(plan: dict, research: dict | None = None) -> dict:
    """{chiave_normalizzata: _Cand} da titolo, sottotitolo, keyword KDP, categorie, descrizione e (facoltativo) keyword Publisher Rocket.
    Nessun dato inventato."""
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
    for kw in sorted(research or {}):                       # keyword dei file Publisher Rocket importati (frasi intere)
        add(kw, "publisher_rocket", kw, whole=True)
    return cands


# ------------------------------------------------------------------ pertinenza
def _profile(plan: dict, research: dict | None = None) -> dict:
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
    prof["commercial"] = _commercial_profile(plan, research) if title_is_creative(plan) else None
    return prof


def _is_content(t) -> bool:
    return t not in STOPWORDS and not _is_generic(t) and t not in PROMO_TOKENS and not _is_number(t)


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
    head = (prof.get("core") or {}).get("head", [])         # i concetti del segmento principale del titolo definiscono il tema: pieno sostegno
    return max(ws) * (1.0 if (len(ws) >= RULES["topic_min_sources"] or token in head) else RULES["single_source_factor"])


def _is_topic(token, prof, exclude_kdp: set) -> bool:
    token = _stem(token)
    if token in (prof.get("core") or {}).get("head", []):
        return True
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
    types = {s for s, _ in cand.src}
    derived = not cand.whole and not ({"category", "publisher_rocket"} & types)
    if len(k) < RULES["min_chars"] or len(words) > (RULES["max_words"] if derived else RULES["max_words_whole"]):
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


_RESEARCH_SKIP = ("keyword", "source", "source_file", "marketplace", "competitor_asin", "extra")


def _research_summary(records: list) -> dict | None:
    """Metriche Publisher Rocket REALMENTE presenti, per file. None se la keyword non e' nei file."""
    if not records:
        return None
    per_file = [{"source_file": r.get("source_file"), "marketplace": r.get("marketplace"),
                 **{k: v for k, v in r.items() if k not in _RESEARCH_SKIP}, **({"extra": r["extra"]} if r.get("extra") else {}),
                 **({"competitor_asin": r["competitor_asin"]} if r.get("competitor_asin") else {})} for r in records]
    return {"source": "PUBLISHER_ROCKET", "files": sorted({r.get("source_file") for r in records}), "records": per_file}


def _metrics_text(rec: dict) -> str:
    names = (("search_volume", "ricerche stimate/mese"), ("search_volume_text", "ricerche stimate/mese"), ("competition", "punteggio competitivo"),
             ("title_count", "titoli concorrenti"), ("avg_price", "prezzo medio"), ("avg_reviews", "recensioni medie"),
             ("avg_sales", "vendite medie"), ("avg_monthly_earnings", "guadagno mensile medio"))
    return ", ".join(f"{label} {rec[k]}" for k, label in names if rec.get(k) not in (None, ""))


def evaluate(cand: _Cand, plan: dict, prof: dict, competitor_data: dict | None, all_keys: dict, external: dict | None = None,
             research_records: list | None = None) -> dict:
    nat = _natural_fail(cand)
    if nat and not (prof.get("commercial") and _title_query(cand, plan)):      # la query brand del titolo e' una classe a parte
        return _natural_reject(cand, nat)                      # NATURAL_QUERY_QUALITY: frase derivata che non sembra una query
    if prof.get("commercial"):
        return _evaluate_commercial(cand, plan, prof, competitor_data, all_keys, external, research_records)
    rel = relevance(cand, plan, prof)
    anc = _anchor_analysis(cand, rel, prof)
    R = rel["relevance"]
    types = sorted({s for s, _ in cand.src}, key=list(SOURCE_LABEL).index)
    cd = competitor_data or {}
    kf = fold(cand.key)
    hits = sorted(a for a, t in cd.items() if any(_contains(v, kf) for v in t.values()))
    n_words = len(cand.key.split())
    core_types = [t for t in types if t not in ("description", "publisher_rocket")]
    S = min(1.0, (len(core_types) + 0.5 * ("description" in types) + 0.5 * ("publisher_rocket" in types)) / 3.0)
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
    # --- evidenza Publisher Rocket: conta solo se la keyword e' PERTINENTE al tema e confermata da un'altra fonte
    records = research_records or []
    pr_bonus, pr_note, pr_lift = 0, None, False
    if records:
        valid = {c["asin"] for c in plan.get("competitors", []) if c.get("status") == L.STATUS_VALID}
        linked = bool({r.get("competitor_asin") for r in records} & valid)
        n_files = len({r.get("source_file") for r in records})
        pertinent = (not _reject_reason(cand, rel)) and bool(rel["topic"]) and R >= RULES["phrase_min_relevance"]
        own_core = bool(set(OWN_CORE_SOURCES) & set(types))
        confirmed = own_core or bool(hits) or linked or n_files >= 2
        if pertinent:
            pr_bonus = min(RULES["research_bonus_max"], RULES["research_bonus_base"] + (RULES["research_bonus_competitor"] if (hits or linked) else 0)
                           + (RULES["research_bonus_multi_source"] if (own_core or n_files >= 2) else 0))
            pr_lift = confirmed
            if pr_lift and "research_data" not in ext_kinds:
                ext_kinds = sorted(ext_kinds + ["research_data"])
            score = min(100, score + pr_bonus)
            pr_note = ("dati Publisher Rocket: " + (_metrics_text(records[0]) or "nessuna metrica numerica nel file")
                       + (f" (presente in {n_files} file)" if n_files > 1 else ""))
            if not confirmed:
                pr_note += " (nessuna conferma da altra fonte: nessun innalzamento di classe)"
        else:
            pr_note = "presente in Publisher Rocket ma NON pertinente al tema del libro: nessun bonus, anche con volume alto"
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
    if pr_note:
        reason += f"; {pr_note}"
    if variants:
        reason += f"; variante singolare/plurale di: {', '.join(variants)} (non fuse)"
    sources = [{"source": s, "original": o} for s, o in cand.src] + [{"source": "competitor", "asin": a} for a in hits]
    linked_asins, _nf = _research_links(plan, records)
    market, market_parts = _market_evidence(records, hits, linked_asins, _nf)
    derived = not cand.whole and "category" not in types
    edge_generic = bool(rel["tokens"]) and (_is_generic(tokens(cand.key)[0]) or _is_generic(tokens(cand.key)[-1])) and not ({"kdp_keyword"} & set(types))
    return {"_derived": derived, "_edge_generic": edge_generic, "_distinctive": len(rel["distinctive"]), "_all_topic": len(rel["topic"]) == len(rel["distinctive"]),
            "keyword": cand.key, "sources": sources, "source_types": types + (["competitor"] if hits else []),
            "evidence_score": score, "confidence": conf, "recommended_match_type": cls, "reason": reason,
            "relevance": round(R, 3), "competitor_hits": sorted(set(hits) | set(linked_asins)), "variant_of": variants,
            "research": _research_summary(records), "research_bonus": pr_bonus, "relevance_score": round(100 * R),
            "market_evidence_score": market, "market_parts": market_parts, "final_launch_score": score,
            "core_topic": {"covers_all_core_concepts": anc["full"], "specific": anc["specific"],
                           "covered": anc["covered_label"] or None, "external_evidence": ext_kinds}}


# ------------------------------------------------------------------ TITLE_TOPIC / COMMERCIAL_SEARCH_TOPIC
def _content_stems(text) -> list:
    out = []
    for w in tokens(text):
        if _is_content(w):
            s = _stem(w)
            if s not in out:
                out.append(s)
    return out


def title_is_creative(plan: dict) -> bool:
    """Titolo editoriale/creativo che non descrive la query di ricerca: numeri scritti a parole nel segmento principale, oppure meta' o piu' dei
    suoi termini assenti da ogni altra fonte (sottotitolo, descrizione, keyword KDP, categorie). Senza altre fonti: mai creativo."""
    b = plan["book"]
    head = _SPLIT_TITLE.split(b.get("title") or "")[0]
    words = tokens(head)
    others = [b.get("subtitle"), b.get("description"), *(b.get("kdp_keywords") or []), *(b.get("categories") or [])]
    other_stems = set()
    for o in others:
        other_stems |= set(_content_stems(o))
    if not other_stems:
        return False
    if any(w in NUMBER_WORDS for w in words):
        return True
    content = _content_stems(head)
    return bool(content) and sum(1 for s in content if s not in other_stems) / len(content) >= 0.5


def _commercial_profile(plan: dict, research: dict | None) -> dict:
    """COMMERCIAL_SEARCH_TOPIC: peso di ogni concetto (radice) dalle fonti, concetti FORTI, concetto "testa", coppie adiacenti."""
    b = plan["book"]
    C = RULES["commercial"]
    sw = C["source_weights"]
    entries = [("subtitle", None, b.get("subtitle")), ("description", None, b.get("description")), ("title", None, b.get("title"))]
    for c in b.get("categories") or []:
        entries.append(("category", None, c))                     # tutto il percorso: "Fiction > Mystery & Detective > Cozy" conta per intero
    kdp = list(b.get("kdp_keywords") or [])
    for i, k in enumerate(kdp):
        entries.append(("kdp_keyword", i, k))
    srcs: dict = {}                                               # radice -> [(fonte, indice)]
    pairs: set = set()
    for src, idx, text in entries:
        for seg in _SPLIT_CLAUSE.split(str(text or "")):
            ws = [w for w in tokens(seg)]
            cs = [_stem(w) if _is_content(w) else None for w in ws]
            for s in cs:
                if s:
                    srcs.setdefault(s, set()).add((src, idx))
            for a, c2 in zip(cs, cs[1:]):
                if a and c2:
                    pairs.add((a, c2, src, idx))
    df = {}
    if research:                                                  # frequenza nelle keyword Publisher Rocket: solo conferma di termini GIA' nelle fonti
        for kw in research:
            for s in set(_content_stems(kw)):
                df[s] = df.get(s, 0) + 1
    n_research = len(research or {})
    pr_boost = {s for s, n in df.items() if s in srcs and n >= C["pr_df_min_count"] and n / max(1, n_research) >= C["pr_df_share"]}
    surf = _surfaces(plan)
    freq = {}
    for k in kdp:
        for s in set(_content_stems(k)):
            freq[s] = freq.get(s, 0) + 1
    # coppie adiacenti presenti in almeno 2 elementi distinti delle fonti proprie ("cozy mystery", "large print"): frasi-concetto CONFERMATE
    pair_entries: dict = {}
    for a, c2, src, idx in pairs:
        pair_entries.setdefault((a, c2), set()).add((src, idx))
    prof = {"srcs": srcs, "pairs": {(a, c2) for a, c2, _, _ in pairs}, "confirmed_pairs": {k for k, v in pair_entries.items() if len(v) >= 2},
            "pr_boost": pr_boost, "creative": True, "surfaces": surf, "kdp_freq": freq, "head": [],
            "title_topic": [s for s in _content_stems(_SPLIT_TITLE.split(b.get("title") or "")[0])]}

    def is_strong(s):
        types = {x for x, _ in srcs[s]}
        corroborated = len(types) >= 2 or freq.get(s, 0) >= 2
        market_ok = (len(research or {}) < C["pr_filter_min_keywords"]) or s in pr_boost or freq.get(s, 0) >= 2   # con molti dati di mercato il concetto deve comparire anche li' (es. "sitting" no)
        return _cw(s, prof, set()) >= C["strong_min_weight"] and corroborated and market_ok
    strong = sorted(s for s in srcs if is_strong(s))
    prof["head"] = ([sorted(freq, key=lambda s: (-freq[s], s))[0]] if freq and max(freq.values()) >= 2 and sorted(freq, key=lambda s: (-freq[s], s))[0] in strong
                    else sorted(strong, key=lambda s: (-_cw(s, prof, set()), s))[:1])
    prof["strong"] = strong
    return prof


def _cw(stem, com, exclude_kdp: set, exclude_src: frozenset = frozenset()) -> float:
    """Peso commerciale di un concetto: massimo peso delle fonti, x0.7 se compare in una sola fonte. Una keyword KDP non sostiene se stessa, ne' un
    segmento intero di sottotitolo/titolo/descrizione (exclude_src)."""
    C = RULES["commercial"]
    sw = C["source_weights"]
    own = [(s, i) for s, i in com["srcs"].get(stem, ()) if not (s == "kdp_keyword" and i in exclude_kdp) and s not in exclude_src]
    if not own:
        return 0.0
    ws = [sw["title"] if s == "title" else sw[s] for s, _ in own]
    n = len({s for s, _ in own}) + (1 if stem in com["pr_boost"] else 0)
    return max(ws) * (1.0 if n >= 2 or len(own) >= 2 else C["single_source_factor"])


def _title_query(cand: _Cand, plan: dict) -> bool:
    """La keyword contiene per intero il titolo (o il suo segmento principale) del NOSTRO libro: e' una query brand, non una keyword commerciale."""
    b = plan["book"]
    k = fold(cand.key)
    full = " ".join(tokens(b.get("title")))
    head = " ".join(tokens(_SPLIT_TITLE.split(b.get("title") or "")[0]))
    return any(p and len(p.split()) >= 2 and f" {p} " in f" {k} " for p in (full, head))


# ------------------------------------------------------------------ NATURAL_QUERY_QUALITY
def _natural_fail(cand: _Cand) -> str | None:
    """Motivo per cui una frase DERIVATA automaticamente non sembra una query di ricerca. Keyword intere (utente, categorie) e Publisher Rocket: mai."""
    types = {s for s, _ in cand.src}
    ws = tokens(cand.key)
    if cand.whole and set(cand.whole) <= {"title", "subtitle"} and any(_is_number(w) for w in ws):
        return "contiene un numero (frammento di titolo/sottotitolo)"       # es. l'intero sottotitolo "20 Large-Print Cozy Mystery ..."
    if cand.whole or "category" in types or "publisher_rocket" in types:
        return None
    if not ws:
        return None
    if any(_is_number(w) for w in ws):
        return "contiene un numero (frammento di titolo/sottotitolo)"
    if ws[-1] in DANGLING_END or ws[-1] in STOPWORDS:
        return f"finisce con '{ws[-1]}' (frase incompleta)"
    if any(a == b for a, b in zip(ws, ws[1:])):
        return "parola ripetuta"
    return None


def _natural_reject(cand: _Cand, why: str) -> dict:
    types = sorted({s for s, _ in cand.src}, key=list(SOURCE_LABEL).index)
    keep = bool({"title", "subtitle"} & set(types))               # i frammenti di titolo/sottotitolo si mostrano tra le scartate, il resto e' rumore
    return {"_derived": True, "_keep": keep, "_edge_generic": False, "_distinctive": 0, "_all_topic": False, "keyword": cand.key,
            "sources": [{"source": s, "original": o} for s, o in cand.src], "source_types": types, "evidence_score": 0, "confidence": "HIGH",
            "recommended_match_type": "REJECT", "reason": f"NATURAL_QUERY_QUALITY: {why}: non sembra una query di ricerca reale", "relevance": 0.0,
            "competitor_hits": [], "variant_of": [], "research": None, "research_bonus": 0, "relevance_score": 0, "market_evidence_score": None,
            "market_parts": {}, "final_launch_score": 0,
            "core_topic": {"covers_all_core_concepts": False, "specific": False, "covered": None, "external_evidence": []}}


# ------------------------------------------------------------------ MARKET_EVIDENCE_SCORE
def _research_links(plan: dict, records: list) -> tuple:
    """(ASIN competitor VALIDATI collegati ai record, numero di file). Il collegamento viene dai dati reali (colonna ASIN o nome del file reverse ASIN)."""
    valid = {c["asin"] for c in plan.get("competitors", []) if c.get("status") == L.STATUS_VALID}
    linked = sorted({r.get("competitor_asin") for r in records if r.get("competitor_asin") in valid})
    return linked, len({r.get("source_file") for r in records})


def _market_evidence(records: list, hits: list, linked: list, n_files: int) -> tuple:
    """(score 0-100 | None, dettaglio). SOLO dai dati realmente presenti; una metrica assente non penalizza. I colori Green/Yellow/Red NON contano."""
    import math
    M = RULES["market"]
    if not records and not hits:
        return None, {}

    def num(k, best):
        vals = [r[k] for r in records if isinstance(r.get(k), (int, float))]
        return best(vals) if vals else None
    avail = pts = 0.0
    parts: dict = {}
    vol = num("search_volume", max)
    if vol is not None:
        p = min(M["volume_max"], 10 + 20 * math.log10(vol / 100)) if vol >= 100 else 3
        pts, avail, parts["volume"] = pts + p, avail + M["volume_max"], round(p, 1)
    elif any(r.get("search_volume_text") for r in records):          # es. "<100": sotto 100, non un numero
        pts, avail, parts["volume"] = pts + 3, avail + M["volume_max"], 3
    comp = num("competition", min)
    if comp is not None:
        p = M["competition_max"] * (1 - min(max(comp, 0), 100) / 100)
        pts, avail, parts["competition"] = pts + p, avail + M["competition_max"], round(p, 1)
    tc = num("title_count", min)
    if tc is not None:
        p = M["title_count_max"] * (1 - min(math.log10(tc + 1) / 4, 1))
        pts, avail, parts["title_count"] = pts + p, avail + M["title_count_max"], round(p, 1)
    earn = max([v for v in (num("avg_sales", max), num("avg_monthly_earnings", max), num("sales_month", max)) if v is not None], default=None)
    if earn is not None:
        p = min(M["earnings_max"], 5 * math.log10(max(earn, 10) / 10))
        pts, avail, parts["earnings"] = pts + p, avail + M["earnings_max"], round(p, 1)
    if hits or linked:
        pts, avail, parts["competitor"] = pts + M["competitor_max"], avail + M["competitor_max"], M["competitor_max"]
    if n_files >= 2:
        pts, avail, parts["multi_file"] = pts + M["multi_file_max"], avail + M["multi_file_max"], M["multi_file_max"]
    return (round(100 * pts / avail) if avail else None), parts


def _final_score(rel_score: int, market) -> int:
    """La pertinenza viene prima: il mercato pesa fino al 30% e solo in proporzione alla pertinenza (fuori tema non lo salva)."""
    if market is None:
        return rel_score
    C = RULES["commercial"]
    wm = C["final_market_weight"] * min(1.0, rel_score / C["final_market_full_at"])
    return round(rel_score * (1 - wm) + market * wm)


# ------------------------------------------------------------------ classificazione COMMERCIALE (titoli creativi)
def _evaluate_commercial(cand: _Cand, plan: dict, prof: dict, competitor_data: dict | None, all_keys: dict, external: dict | None,
                         research_records: list | None) -> dict:
    com = prof["commercial"]
    C = RULES["commercial"]
    records = research_records or []
    b = plan["book"]
    types = sorted({s for s, _ in cand.src}, key=list(SOURCE_LABEL).index)
    words = tokens(cand.key)
    n_words = len(words)
    content = [w for w in words if _is_content(w)]
    generic = [w for w in words if w not in STOPWORDS and _is_generic(w) and w not in PROMO_TOKENS and not _is_number(w)]
    stems = [_stem(w) for w in content]
    derived_c = not cand.whole and not ({"category", "publisher_rocket"} & set(types))
    if derived_c:
        cs0 = [_stem(w) if _is_content(w) else None for w in words]
        confirmed = all(cs0) and len(cs0) >= 2 and all((a, c2) in com["confirmed_pairs"] for a, c2 in zip(cs0, cs0[1:]))
        if confirmed:
            cut = any((a, cs0[0]) in com["confirmed_pairs"] and a in _DANGLING_STEMS for a in {x for x, _ in com["confirmed_pairs"]})
            confirmed = not cut                                  # "print cozy mystery" inizia dopo "large": e' un taglio, non una frase
        if not confirmed:                                         # taglio a meta' di un concetto ("print mystery", "stories for senior women"): rumore
            return {**_natural_reject(cand, "frammento non confermato"), "_drop": True, "_keep": False}
    rel = {"tokens": content + generic, "distinctive": content, "topic": content}
    excl = frozenset(s for s in cand.whole if s in ("subtitle", "title", "description"))
    w_of = {s: _cw(s, com, cand.kdp_idx, excl) for s in set(stems)}
    strong_set = set(com["strong"])
    covered_strong = sorted({s for s in stems if s in strong_set and w_of[s] >= C["strong_min_weight"]})
    n_strong = len(covered_strong)
    gw = RULES["generic_token_weight"]
    num = sum(w_of[_stem(w)] for w in content) + sum(gw * _cw(_stem(w), com, cand.kdp_idx, excl) for w in generic)
    den = len(content) + gw * len(generic)
    base = num / den if den else 0.0
    kf = fold(cand.key)
    bonus = 0.0
    for src, text in (("subtitle", b.get("subtitle")), ("title", b.get("title")), *[("category", c) for c in b.get("categories") or []]):
        if _contains(text, kf):
            bonus = max(bonus, C["phrase_bonus"][src])
    if _contains(b.get("description"), kf):
        bonus = max(bonus, C["phrase_bonus"]["description"])
    if any(_contains(k, kf) for i, k in enumerate(b.get("kdp_keywords") or []) if i not in cand.kdp_idx):
        bonus = max(bonus, C["phrase_bonus"]["kdp_keyword"])
    cs = [_stem(w) if _is_content(w) else None for w in words]
    n_pairs = len({(a, c2) for a, c2 in zip(cs, cs[1:]) if a and c2 and (a, c2) in com["confirmed_pairs"]})
    pair_bonus = min(C["pair_bonus_max"], C["pair_bonus"] * n_pairs)       # frasi-concetto reali del libro ("large print", "cozy mystery")
    R = min(1.0, base * min(1.0, 1.0) + bonus * min(1.0, base / 0.8) + pair_bonus)
    if n_strong == 0:
        R = min(R, 0.2)
    head = com["head"]
    has_head = any(h in stems for h in head) if head else True
    explicit = bool({"kdp_keyword", "subtitle"} & set(cand.whole))
    cd = competitor_data or {}
    hits = sorted(a for a, tx in cd.items() if any(_contains(v, kf) for v in tx.values()))
    linked, n_files = _research_links(plan, records)
    market, parts = _market_evidence(records, hits, linked, n_files)
    specificity = min(1.0, 0.55 + 0.15 * n_strong)                 # 1 concetto forte 0.70, 2: 0.85, >=3: 1.0
    rel_score = round(100 * R * specificity)
    final = _final_score(rel_score, market)
    sources = [{"source": s, "original": o} for s, o in cand.src] + [{"source": "competitor", "asin": a} for a in hits]
    names = lambda ss: ", ".join("/".join(com["surfaces"].get(s, [s])) for s in ss)  # noqa: E731
    reject = _reject_reason(cand, rel)
    conflict = sorted(w for w in content if w in INTENT_CONFLICT and w_of[_stem(w)] == 0)
    foreign = sorted(w for w in content if w_of[_stem(w)] == 0 and w not in INTENT_CONFLICT)
    cls, reason = None, ""
    ext_kinds = sorted(set((external or {}).get(cand.key) or []) & set(RULES["external_evidence_kinds"]))
    only_desc = types == ["description"]
    if _title_query(cand, plan):
        cls = "TITLE_QUERY"
        reason = ("Query del titolo del nostro libro (BRANDED_SELF / TITLE_QUERY): non e' una keyword commerciale generica. Non fa parte delle keyword di "
                  "lancio principali; valutala solo come campagna brand/difensiva")
        vol = max([r["search_volume"] for r in records if isinstance(r.get("search_volume"), (int, float))], default=None)
        if vol is not None:
            reason += f" (Publisher Rocket: ricerche stimate/mese {vol})"
    elif reject:
        cls, reason = "REJECT", reject[0]
    elif conflict:
        cls, reason = "REJECT", f"Intento diverso: contiene {', '.join(conflict)}, assente dal libro"
    elif R < C["discovery_min_relevance"] or n_strong == 0:
        cls = "REJECT"
        reason = "Fuori tema: nessun concetto commerciale forte del libro (sottotitolo, keyword KDP, categorie, descrizione)"
    else:
        exact_ok = (has_head and R >= C["exact_min_relevance"] and C["exact_words"][0] <= n_words <= C["exact_words"][1]
                    and (n_strong >= C["exact_min_strong"] or (explicit and n_strong >= C["exact_min_strong_explicit"])) and not only_desc)
        weak_market = bool(records) and market is not None and market < C["exact_min_market_if_data"] and not explicit
        phrase_ok = (R >= C["phrase_min_relevance"] and not only_desc
                     and (n_strong >= C["phrase_min_strong"] or (has_head and n_strong >= C["phrase_min_strong_with_head"])))
        vol = max([r["search_volume"] for r in records if isinstance(r.get("search_volume"), (int, float))], default=0)
        exceptional = (market is not None and market >= C["exceptional_market"] and vol >= C["exceptional_volume"] and R >= C["phrase_min_relevance"]
                       and n_strong >= 2 and not only_desc)
        if len(foreign) >= 2:                                       # tanti termini estranei al libro: probabile titolo/brand di un altro libro
            exact_ok = phrase_ok = exceptional = False
        if exact_ok and not weak_market:
            cls = "EXACT"
            reason = (f"Long-tail ad alta intenzione: copre {n_strong} concetti commerciali forti ({names(covered_strong)}) e il concetto centrale "
                      f"({names(head) or 'n/d'})")
        elif exact_ok or phrase_ok:
            cls = "PHRASE"
            reason = (f"Pertinente: copre {n_strong} concetti commerciali forti ({names(covered_strong)})"
                      + (" e il concetto centrale" if has_head and head else "")
                      + ("; EXACT non assegnata perche' i dati di mercato Publisher Rocket sono deboli" if exact_ok and weak_market else ""))
        elif exceptional:
            cls = "PHRASE"
            reason = f"Keyword ampia elevata per evidenza di mercato eccezionale (Publisher Rocket: {vol} ricerche/mese, mercato {market})"
        elif len(foreign) >= 2:
            cls = "DISCOVERY"
            reason = f"Contiene termini estranei al libro ({', '.join(foreign)}): probabile titolo/brand di un altro libro, da testare con cautela"
        else:
            cls = "DISCOVERY"
            reason = (f"Troppo ampia: copre solo {names(covered_strong) or 'concetti generici'} senza la combinazione di concetti centrali; da testare "
                      "con cautela" if not only_desc else "Ricavata solo dalla descrizione: da testare con cautela")
    if records and cls not in ("TITLE_QUERY",):
        mt = _metrics_text(records[0])
        reason += "; dati Publisher Rocket: " + (mt or "nessuna metrica numerica") + (f" (presente in {n_files} file)" if n_files > 1 else "")
        if cls == "REJECT":
            reason += " (il volume non promuove una keyword non pertinente)"
    if linked:
        reason += f"; presente nel reverse ASIN dei competitor validati {', '.join(linked)}"
    if hits:
        reason += f"; confermata da competitor reali: {', '.join(hits)}"
    variants = sorted({o for o in all_keys if o != cand.key and " ".join(_stem(t) for t in tokens(o)) == " ".join(_stem(t) for t in tokens(cand.key))})
    if variants:
        reason += f"; variante singolare/plurale di: {', '.join(variants)} (non fuse)"
    derived = not cand.whole and "category" not in types
    pr_bonus = max(0, final - rel_score) if (records and cls in ("EXACT", "PHRASE", "DISCOVERY")) else 0
    return {"_derived": derived, "_edge_generic": False, "_distinctive": len(content), "_all_topic": True, "keyword": cand.key, "sources": sources,
            "source_types": types + (["competitor"] if hits else []), "evidence_score": final, "confidence": _confidence(final) if cls != "REJECT" else "HIGH",
            "recommended_match_type": cls, "reason": reason, "relevance": round(R, 3), "competitor_hits": sorted(set(hits) | set(linked)),
            "variant_of": variants, "research": _research_summary(records), "research_bonus": pr_bonus, "relevance_score": rel_score,
            "market_evidence_score": market, "market_parts": parts, "final_launch_score": final,
            "core_topic": {"covers_all_core_concepts": bool(head) and has_head, "specific": cls in ("EXACT", "PHRASE"),
                           "covered": names(covered_strong) or None, "external_evidence": ext_kinds, "mode": "commercial", "strong_concepts": n_strong}}


# ------------------------------------------------------------------ piano completo
def input_fingerprint(plan: dict) -> str:
    """Impronta degli ingressi che influenzano il piano: se cambia, il piano salvato e' superato (stale)."""
    b = plan["book"]
    ent = [(c["asin"], c.get("status"), c.get("enrichment")) for c in plan.get("competitors", [])]
    raw = json.dumps([b.get("title"), b.get("subtitle"), b.get("description"), b.get("kdp_keywords"), b.get("categories"),
                      b.get("marketplace"), b.get("price"), b.get("royalty_per_copy"), ent,
                      [f.get("sha256") for f in (plan.get("research") or {}).get("files", [])]], ensure_ascii=False, sort_keys=True, default=str)
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
            "title_topic": [("/".join(_surfaces(plan).get(s, [s]))) for s in _content_stems(_SPLIT_TITLE.split(b.get("title") or "")[0])],
            "title_creative": bool(prof.get("commercial")),
            "commercial_search_topic": ([("/".join(prof["commercial"]["surfaces"].get(s, [s]))) for s in prof["commercial"]["strong"]]
                                        if prof.get("commercial") else ["/".join(prof["core"]["surfaces"][s]) for s in prof["core"]["head"]]),
            "commercial_head": ([("/".join(prof["commercial"]["surfaces"].get(s, [s]))) for s in prof["commercial"]["head"]] if prof.get("commercial") else []),
            "secondary_anchors": ["/".join(prof["core"]["surfaces"][s]) for s in prof["core"]["secondary"]],
            "competitors_valid": sum(1 for c in comps if c.get("status") == L.STATUS_VALID),
            "competitors_unverified": sum(1 for c in comps if c.get("status") != L.STATUS_VALID),
            "competitors_with_data": sum(1 for c in comps if c.get("enrichment"))}


def build_keyword_plan(plan: dict, competitor_data: dict | None = None, now: str | None = None, external_evidence: dict | None = None,
                       research: dict | None = None) -> dict:
    """Piano keyword + struttura campagne da una bozza. Funzione pura (non modifica la bozza, non scrive).
    competitor_data = {asin: {title, subtitle, categories}}; None = quelli arricchiti presenti nella bozza (oggi nessuno => nessun bonus).
    external_evidence = {keyword: [tipo, ...]} con tipo in RULES["external_evidence_kinds"] (search_term, provider, research_data, competitor): evidenza
    ESTERNA forte che puo' elevare una keyword generale da DISCOVERY a PHRASE. Oggi nessuna fonte la fornisce: parametro pronto per il futuro."""
    external_evidence = {kw_norm(k): list(v) for k, v in (external_evidence or {}).items()}
    research = {kw_norm(k): list(v) for k, v in (research or {}).items() if kw_norm(k)}    # {keyword: [record Publisher Rocket]}
    cd = L.competitor_texts(plan) if competitor_data is None else competitor_data
    prof = _profile(plan, research)
    cands = generate_candidates(plan, research)
    evaluated, dropped = [], 0
    for key in sorted(cands):
        c = cands[key]
        ev = evaluate(c, plan, prof, cd, cands, external_evidence, research.get(key))
        derived, only_desc = ev.pop("_derived"), ev["source_types"] == ["description"]
        n_dist, all_topic, edge_generic = ev.pop("_distinctive"), ev.pop("_all_topic"), ev.pop("_edge_generic")
        keep_reject = ev.pop("_keep", False)
        if ev.pop("_drop", False):
            dropped += 1
            continue
        if keep_reject:                                        # frammento innaturale di titolo/sottotitolo: resta tra le scartate (auditabile)
            evaluated.append(ev)
            continue
        # le frasi DERIVATE (n-grammi, non scelte dall'utente) sono proposte solo se tutti i termini distintivi sono tematici
        # (e, dalla sola descrizione, se ne hanno almeno 2): il resto e' rumore e viene solo contato, non elencato
        if derived and (ev["recommended_match_type"] == "REJECT" or not all_topic or edge_generic or (only_desc and n_dist < 2)):
            dropped += 1
            continue
        evaluated.append(ev)

    def order(x):
        return (-x["final_launch_score"], -x["relevance"], x["keyword"])
    title_queries = sorted((e for e in evaluated if e["recommended_match_type"] == "TITLE_QUERY"), key=order)
    exact = sorted((e for e in evaluated if e["recommended_match_type"] == "EXACT"), key=order)
    phrase = sorted((e for e in evaluated if e["recommended_match_type"] == "PHRASE"), key=order)
    discovery = sorted((e for e in evaluated if e["recommended_match_type"] == "DISCOVERY"), key=order)
    # scartate: prima quelle con dati Publisher Rocket / pertinenza piu' alta ("da rivedere"), i frammenti innaturali in fondo
    rejected = sorted((e for e in evaluated if e["recommended_match_type"] == "REJECT"),
                      key=lambda x: (x["reason"].startswith("NATURAL_QUERY_QUALITY"), -x["relevance"], -x["final_launch_score"], x["keyword"]))
    # Tutte le keyword restano nella classe assegnata (nessun declassamento per quantita'). I limiti valgono per le LISTE DI CAMPAGNA consigliate
    # (top EXACT / top PHRASE) e per cio' che la pagina mostra all'inizio; mai riempimento artificiale: se i candidati validi sono meno, sono meno.

    valid = [c for c in plan.get("competitors", []) if c.get("status") == L.STATUS_VALID][:RULES["max_competitors"]]
    campaigns = {
        "EXACT": {"keywords": [e["keyword"] for e in exact[:RULES["max_exact"]]], "operational": False, "candidates_total": len(exact),
                  "note": "Lista consigliata: nessuna campagna viene creata. Se vuota, non ci sono keyword abbastanza pertinenti."},
        "PHRASE": {"keywords": [e["keyword"] for e in phrase[:RULES["max_phrase"]]], "operational": False, "candidates_total": len(phrase),
                   "note": "Lista consigliata (le migliori per punteggio finale): nessuna campagna viene creata."},
        "AUTO": {"keywords_required": False, "keywords": [], "operational": False, "role": "discovery iniziale",
                 "note": "Sempre valutabile come campagna di scoperta iniziale: non richiede keyword. Suggerimento non operativo."},
        "COMPETITOR": {"available": bool(valid), "asins": [c["asin"] for c in valid], "operational": False,
                       "source": L.SOURCE_USER_COMPETITOR,
                       "note": ("ASIN forniti dall'utente e VALIDATI solo formalmente (l'esistenza su Amazon non e' verificata). Suggerimento non operativo."
                                if valid else "Nessun ASIN competitor VALIDATO: campagna COMPETITOR non proposta.")},
    }
    return {"version": PLAN_VERSION, "ruleset": RULESET, "generated_at": now or datetime.now().isoformat(timespec="seconds"),
            "input_fingerprint": input_fingerprint(plan), "profile": book_profile(plan, prof),
            "competitor_data_available": bool(cd),
            "research": {"source": "PUBLISHER_ROCKET", "files": sorted({r.get("source_file") for rs in research.values() for r in rs}),
                         "keywords": len(research)} if research else None,
            "exact": exact, "phrase": phrase, "discovery": discovery, "rejected": rejected, "title_queries": title_queries,
            "dropped_candidates": dropped, "campaigns": campaigns, "rules": {k: (list(v) if isinstance(v, tuple) else v) for k, v in RULES.items()},
            "data_notes": _data_notes(plan, research, cd), "limits": _limits(plan, research, cd)}


_ALL_METRICS = ("search_volume", "competition", "title_count", "avg_price", "avg_reviews", "avg_pages", "avg_monthly_earnings", "avg_sales", "price",
                "rating", "bsr")


def _data_notes(plan: dict, research: dict, cd: dict) -> dict:
    """Quali dati reali sono disponibili e quali mancano (per le note finali della pagina)."""
    recs = [r for rs in (research or {}).values() for r in rs]
    present = sorted({k for r in recs for k in r if k in _ALL_METRICS} | ({"search_volume"} if any(r.get("search_volume_text") for r in recs) else set()))
    comps = plan.get("competitors", [])
    return {"research_available": bool(research), "research_files": sorted({r.get("source_file") for r in recs}), "research_keywords": len(research or {}),
            "metrics_available": present, "metrics_missing": [m for m in _ALL_METRICS if m not in present] if research else list(_ALL_METRICS),
            "competitors_total": len(comps), "competitors_enriched": sum(1 for c in comps if c.get("enrichment")),
            "competitor_data_available": bool(cd)}


def _limits(plan: dict, research: dict, cd: dict) -> list:
    n = _data_notes(plan, research, cd)
    out = []
    if n["research_available"]:
        out.append(f"Dati Publisher Rocket disponibili: {n['research_keywords']} keyword da {len(n['research_files'])} file. Metriche presenti: "
                   f"{', '.join(n['metrics_available']) or 'nessuna'}; mancanti: {', '.join(n['metrics_missing']) or 'nessuna'}.")
    else:
        out.append("Nessun dato di ricerca (volume, concorrenza): il piano si basa solo sui testi della bozza. Importa i file Publisher Rocket per usarli.")
    out.append("La pertinenza e' lessicale (termini condivisi con il profilo del libro), non semantica: un sinonimo non presente nei testi non viene riconosciuto.")
    if n["competitors_enriched"]:
        out.append(f"Dati competitor reali per {n['competitors_enriched']} ASIN su {n['competitors_total']}.")
    elif n["competitors_total"]:
        out.append("Nessun dato competitor reale: gli ASIN inseriti non sono stati arricchiti.")
    out.append("Piano consigliato, non operativo: nessuna campagna Amazon creata o modificata.")
    return out


def is_stale(plan: dict) -> bool:
    kp = plan.get("keyword_plan")
    return bool(kp) and (kp.get("input_fingerprint") != input_fingerprint(plan) or kp.get("ruleset") != RULESET)   # regole cambiate: da rigenerare
