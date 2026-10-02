"""LANCIO NUOVO LIBRO: bozza di lancio locale (titolo, sottotitolo, descrizione, 7 keyword KDP, categorie, marketplace, prezzo, royalty,
0-5 ASIN competitor forniti dall'utente). MANUAL DECISION SUPPORT: nessuna chiamata di rete, nessuna modifica di Amazon, nessuna campagna creata.

MODELLO DATI (un file JSON per bozza, schema_version 1):
  launch_id, schema_version, status ("DRAFT"), created_at, updated_at
  book: {title, subtitle, description, kdp_keywords[<=7], categories[], marketplace, currency, price, royalty_per_copy, book_id|None}
  competitors: [{asin, marketplace, source="USER_COMPETITOR_ASIN", note, status("VALIDATO"|"NON VERIFICATO"), checks[], added_at,
                 enrichment|None}]
  Un competitor e' SEMPRE un competitor fornito dall'utente (tipicamente da Publisher Rocket), mai scoperto automaticamente.
  VALIDATO = superati i controlli FORMALI LOCALI (formato ASIN, marketplace coerente col libro, nessun duplicato, non e' il nostro ASIN).
  NON significa che l'ASIN esista su Amazon o sia un buon competitor: nessuna verifica esterna viene fatta. NON VERIFICATO = 10 caratteri
  alfanumerici ma formato non riconosciuto (ne' B0xxxxxxxx ne' ISBN-10 con checksum valido): salvato, escluso da suggerimenti ed evidenza.
  enrichment resta None finche' un provider esterno (interfaccia CompetitorProvider, oggi NullProvider: nessun dato) non lo valorizza;
  i dati mancanti non vengono mai inventati.

RICERCA PUBLISHER ROCKET: chiave facoltativa "research" = {files:[{id, file_name, stored_as, normalized, sha256, format, kind, rows, imported_at}]}; i file
originali e i dati normalizzati stanno in LAUNCH/<launch_id>/research/ (vedi kdp_ads.launch_research e DASHBOARD/ops/launch_ops.py).

PIANO KEYWORD (V2): chiave facoltativa "keyword_plan" (vedi kdp_ads.launch_keywords), scritta SOLO nella bozza; books.json non si tocca.

SALVATAGGIO: LAUNCH/<launch_id>.json (scrittura atomica: file temporaneo + os.replace). Nessun id viene sovrascritto da una bozza nuova."""
import json
import os
import re
import unicodedata
from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
SOURCE_USER_COMPETITOR = "USER_COMPETITOR_ASIN"
STATUS_VALID, STATUS_UNVERIFIED = "VALIDATO", "NON VERIFICATO"
MAX_COMPETITORS = 5
KDP_KEYWORD_SLOTS = 7
KDP_KEYWORD_MAX_CHARS = 50                    # limite abituale KDP per keyword: solo AVVISO, non blocca
SUGGESTION_COMPETITOR_CAMPAIGN = "Valuta campagna COMPETITOR"
EVIDENCE_LEVELS = ("NONE", "LOW", "MEDIUM", "HIGH", "VERY_HIGH")
OWN_SOURCES = ("title", "subtitle", "kdp_keywords", "description")
MARKETPLACE_DOMAINS = {"amazon.com": "US", "amazon.co.uk": "UK", "amazon.de": "DE", "amazon.it": "IT", "amazon.fr": "FR",
                       "amazon.es": "ES", "amazon.ca": "CA", "amazon.com.au": "AU"}
CURRENCY_BY_MARKETPLACE = {"US": "USD", "UK": "GBP", "DE": "EUR", "IT": "EUR", "FR": "EUR", "ES": "EUR", "CA": "CAD", "AU": "AUD"}
_MP_RE = re.compile(r"^[A-Z]{2,3}$")
_ASIN_ALNUM = re.compile(r"^[A-Z0-9]{10}$")
_ASIN_STD = re.compile(r"^B0[A-Z0-9]{8}$")
_ISBN10 = re.compile(r"^\d{9}[\dX]$")
_URL_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Za-z0-9]{10})(?:[/?#]|$)")
_URL_HOST = re.compile(r"^(?:https?://)?(?:www\.|smile\.)?([a-z0-9.\-]+?)(?:[:/?#]|$)", re.I)


class LaunchError(ValueError):
    """Errore di input dell'utente (messaggio in italiano)."""


# ------------------------------------------------------------------ ASIN
def isbn10_ok(s: str) -> bool:
    if not _ISBN10.match(s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def parse_asin_input(text: str, marketplace: str) -> str:
    """Accetta un ASIN o un URL di prodotto Amazon e ritorna l'ASIN in maiuscolo. L'URL di un altro marketplace e' rifiutato.
    Nessuna richiesta di rete: solo analisi del testo."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        raise LaunchError("ASIN vuoto")
    if "/" in raw or "." in raw:
        m, h = _URL_ASIN.search(raw), _URL_HOST.match(raw)
        if not m:
            raise LaunchError(f"URL non riconosciuto come pagina prodotto Amazon: {raw!r}")
        host = (h.group(1) if h else "").lower()
        mp = next((code for dom, code in MARKETPLACE_DOMAINS.items() if host == dom or host.endswith("." + dom)), None)
        if mp is None:
            raise LaunchError(f"Dominio Amazon non riconosciuto nell'URL: {host!r}")
        if mp != marketplace:
            raise LaunchError(f"L'URL appartiene al marketplace {mp}, il libro e' nel marketplace {marketplace}: i competitor devono essere dello stesso marketplace")
        raw = m.group(1)
    asin = raw.strip().upper()
    if not _ASIN_ALNUM.match(asin):
        raise LaunchError(f"ASIN non valido: {text!r} (servono 10 caratteri alfanumerici)")
    return asin


def asin_status(asin: str) -> tuple[str, list]:
    """(stato, controlli). Solo controlli formali locali: l'esistenza su Amazon NON e' verificata."""
    checks = ["formato 10 caratteri alfanumerici: ok", "esistenza su Amazon: NON verificata (nessun accesso esterno)"]
    if _ASIN_STD.match(asin):
        return STATUS_VALID, ["formato ASIN standard B0xxxxxxxx: ok"] + checks
    if isbn10_ok(asin):
        return STATUS_VALID, ["ISBN-10 con checksum valido: ok"] + checks
    return STATUS_UNVERIFIED, ["formato non riconosciuto (ne' B0xxxxxxxx ne' ISBN-10 valido): da verificare a mano"] + checks


def make_competitor(asin: str, marketplace: str, note: str = "", added_at: str | None = None) -> dict:
    status, checks = asin_status(asin)
    return {"asin": asin, "marketplace": marketplace, "source": SOURCE_USER_COMPETITOR, "note": (note or "").strip(),
            "status": status, "checks": checks, "added_at": added_at or _now(), "enrichment": None}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ validazione e costruzione della bozza
def _decimal(v, label):
    s = str(v if v is not None else "").strip().replace(",", ".")
    if not s:
        raise LaunchError(f"{label}: campo obbligatorio")
    try:
        x = float(s)
    except ValueError:
        raise LaunchError(f"{label}: valore numerico non valido ({v!r})")
    if x != x or x in (float("inf"), float("-inf")):
        raise LaunchError(f"{label}: valore numerico non valido ({v!r})")
    return x


def norm_text(s) -> str:
    """minuscolo, senza accenti ne' punteggiatura, spazi ridotti: base del confronto di keyword."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def build_plan(form: dict, existing: dict | None = None, now: str | None = None) -> tuple[dict | None, list, list]:
    """form: title, subtitle, description, kdp_keywords (lista o testo a righe), categories (lista o testo a righe), marketplace, currency,
    price, royalty_per_copy, book_id (facoltativo), competitors [{asin, note}]. Ritorna (bozza|None, errori, avvisi).
    Con `existing` aggiorna la bozza mantenendo launch_id, created_at e added_at/enrichment dei competitor gia' presenti."""
    errors, warnings = [], []
    now = now or _now()

    def lines(v):
        items = list(v) if isinstance(v, (list, tuple)) else str(v or "").splitlines()
        return [str(x).strip() for x in items if str(x or "").strip()]
    title = str(form.get("title") or "").strip()
    if not title:
        errors.append("Titolo: campo obbligatorio")
    mp = str(form.get("marketplace") or "").strip().upper()
    if not _MP_RE.match(mp):
        errors.append("Marketplace: codice non valido (2-3 lettere, es. US, UK, DE)")
    currency = str(form.get("currency") or CURRENCY_BY_MARKETPLACE.get(mp, "")).strip().upper()
    if not re.match(r"^[A-Z]{3}$", currency):
        errors.append("Valuta: codice non valido (3 lettere, es. USD, GBP, EUR)")
    price = royalty = None
    for key, label in (("price", "Prezzo"), ("royalty_per_copy", "Royalty")):
        try:
            v = _decimal(form.get(key), label)
            if v <= 0:
                errors.append(f"{label}: deve essere maggiore di zero")
            elif key == "price":
                price = v
            else:
                royalty = v
        except LaunchError as e:
            errors.append(str(e))
    if price and royalty and royalty > price:
        errors.append("La royalty non puo' superare il prezzo")

    kws, seen = [], set()
    for k in lines(form.get("kdp_keywords")):
        n = norm_text(k)
        if n in seen:
            warnings.append(f"Keyword KDP duplicata ignorata: {k!r}")
            continue
        seen.add(n)
        kws.append(k)
    if len(kws) > KDP_KEYWORD_SLOTS:
        errors.append(f"Keyword KDP: al massimo {KDP_KEYWORD_SLOTS} (ne hai inserite {len(kws)})")
    for k in kws:
        if len(k) > KDP_KEYWORD_MAX_CHARS:
            warnings.append(f"Keyword KDP oltre {KDP_KEYWORD_MAX_CHARS} caratteri (limite abituale KDP): {k[:30]!r}...")
    cats = lines(form.get("categories"))

    own_id = str(form.get("book_id") or "").strip().upper() or None
    prev = {c["asin"]: c for c in (existing or {}).get("competitors", [])}
    comps, asins = [], set()
    entries = [c for c in (form.get("competitors") or []) if str((c or {}).get("asin") or "").strip()]
    if len(entries) > MAX_COMPETITORS:
        errors.append(f"ASIN competitor: al massimo {MAX_COMPETITORS} (ne hai inseriti {len(entries)})")
    for i, c in enumerate(entries[:MAX_COMPETITORS], 1):
        try:
            asin = parse_asin_input(c["asin"], mp)
        except LaunchError as e:
            errors.append(f"Competitor {i}: {e}")
            continue
        if asin in asins:
            errors.append(f"Competitor {i}: ASIN {asin} duplicato")
            continue
        if own_id and asin == own_id:
            errors.append(f"Competitor {i}: {asin} e' l'ASIN del nostro libro")
            continue
        asins.add(asin)
        comp = make_competitor(asin, mp, c.get("note"), (prev.get(asin) or {}).get("added_at") or now)
        if asin in prev and prev[asin].get("enrichment"):
            comp["enrichment"] = prev[asin]["enrichment"]
        if comp["status"] == STATUS_UNVERIFIED:
            warnings.append(f"Competitor {asin}: formato non riconosciuto, salvato come {STATUS_UNVERIFIED}")
        comps.append(comp)
    if errors:
        return None, errors, warnings
    plan = {"schema_version": SCHEMA_VERSION, "launch_id": (existing or {}).get("launch_id"), "status": "DRAFT",
            "created_at": (existing or {}).get("created_at") or now, "updated_at": now,
            "book": {"title": title, "subtitle": str(form.get("subtitle") or "").strip(), "description": str(form.get("description") or "").strip(),
                     "kdp_keywords": kws, "categories": cats, "marketplace": mp, "currency": currency, "price": price,
                     "royalty_per_copy": royalty, "book_id": own_id},
            "competitors": comps}
    for key in ("keyword_plan", "research"):               # piano keyword e ricerca importata restano nella bozza (piano segnalato superato se cambiano gli ingressi)
        if (existing or {}).get(key):
            plan[key] = existing[key]
    return plan, [], warnings


# ------------------------------------------------------------------ archiviazione locale
def slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm_text(s))).strip("-")[:40] or "libro"


def new_launch_id(directory, title: str, marketplace: str, now: datetime | None = None) -> str:
    """<YYYYMMDD>_<titolo-slug>_<MP>; se esiste gia' si aggiunge _2, _3, ...: una bozza nuova non sovrascrive mai un'altra."""
    base = f"{(now or datetime.now()).strftime('%Y%m%d')}_{slug(title)}_{marketplace.upper()}"
    d, n, cand = Path(directory), 1, base
    while (d / f"{cand}.json").exists():
        n += 1
        cand = f"{base}_{n}"
    return cand


def plan_path(directory, launch_id: str) -> Path:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$", launch_id or ""):
        raise LaunchError(f"launch_id non valido: {launch_id!r}")
    return Path(directory) / f"{launch_id}.json"


def save_plan(directory, plan: dict, overwrite: bool = False) -> Path:
    """Scrittura atomica. Senza overwrite=True non sostituisce un file esistente (aggiornare una bozza e' un atto esplicito)."""
    p = plan_path(directory, plan["launch_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise LaunchError(f"Esiste gia' una bozza {plan['launch_id']}: non la sovrascrivo")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_plan(directory, launch_id: str) -> dict:
    p = plan_path(directory, launch_id)
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LaunchError(f"Bozza non trovata: {launch_id}")
    except ValueError as e:
        raise LaunchError(f"Bozza {launch_id} non leggibile (JSON non valido): {e}")
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION or "book" not in plan:
        raise LaunchError(f"Bozza {launch_id}: schema non supportato")
    return plan


def list_plans(directory) -> list:
    """[{launch_id, title, marketplace, updated_at, competitors, error}] piu' recenti per primi; i file illeggibili compaiono con error."""
    out = []
    d = Path(directory)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            pl = load_plan(d, p.stem)
            out.append({"launch_id": p.stem, "title": pl["book"].get("title"), "marketplace": pl["book"].get("marketplace"),
                        "updated_at": pl.get("updated_at"), "competitors": len(pl.get("competitors") or []), "error": None})
        except LaunchError as e:
            out.append({"launch_id": p.stem, "title": None, "marketplace": None, "updated_at": None, "competitors": 0, "error": str(e)})
    return sorted(out, key=lambda r: r["updated_at"] or "", reverse=True)


# ------------------------------------------------------------------ provider esterno (FUTURO): nessuna rete in questo modulo
ENRICHMENT_FIELDS = ("title", "subtitle", "categories", "price", "currency", "rating", "review_count", "extra")


class CompetitorProvider(ABC):
    """Interfaccia per arricchire un ASIN con dati pubblici (titolo, sottotitolo, categorie, prezzo, recensioni, altro).
    Un'implementazione futura potra' usare la rete; questo modulo e la suite di test NON ne contengono. Ritorna None se non ha dati:
    nessun valore viene mai inventato."""
    name = "abstract"

    @abstractmethod
    def fetch(self, asin: str, marketplace: str) -> dict | None:
        ...


class NullProvider(CompetitorProvider):
    """Provider predefinito: non fornisce nessun dato (nessuna rete)."""
    name = "null"

    def fetch(self, asin, marketplace):
        return None


class StaticProvider(CompetitorProvider):
    """Provider da dizionario {(ASIN, MARKETPLACE): dati}: per test e per import manuali. Nessuna rete."""
    name = "static"

    def __init__(self, data: dict):
        self.data = {(k[0].upper(), k[1].upper()): v for k, v in data.items()}

    def fetch(self, asin, marketplace):
        return self.data.get((asin.upper(), marketplace.upper()))


def enrich_competitors(plan: dict, provider: CompetitorProvider, now: str | None = None) -> dict:
    """Ritorna una COPIA della bozza con enrichment valorizzato solo con cio' che il provider restituisce (campi in ENRICHMENT_FIELDS).
    Competitor senza dati restano con enrichment None. La bozza originale non viene modificata."""
    out = deepcopy(plan)
    for c in out["competitors"]:
        data = provider.fetch(c["asin"], c["marketplace"])
        if not data:
            continue
        c["enrichment"] = {**{k: data[k] for k in ENRICHMENT_FIELDS if data.get(k) is not None},
                           "provider": provider.name, "enriched_at": now or _now()}
    return out


def competitor_texts(plan: dict) -> dict:
    """{asin: {title, subtitle, categories}} dai SOLI competitor VALIDATO con dati di arricchimento. Vuoto se non ce ne sono."""
    out = {}
    for c in plan.get("competitors", []):
        e = c.get("enrichment")
        if c.get("status") == STATUS_VALID and e:
            out[c["asin"]] = {"title": e.get("title") or "", "subtitle": e.get("subtitle") or "",
                              "categories": " ".join(e.get("categories") or [])}
    return out


# ------------------------------------------------------------------ evidenza keyword (funzione PURA)
def _contains(text, kw_norm) -> bool:
    return bool(kw_norm) and f" {kw_norm} " in f" {norm_text(text)} "


def keyword_evidence(keyword: str, plan: dict, competitor_data: dict | None = None) -> dict:
    """Livello di evidenza di una keyword. Fonti NOSTRE: titolo, sottotitolo, keyword KDP, descrizione (una keyword compare in una fonte se
    la frase, normalizzata, e' contenuta a parole intere). Livello base dal numero di fonti nostre: 0 NONE, 1 LOW, 2 MEDIUM, >=3 HIGH.
    BONUS competitor: se compare in almeno una fonte nostra E in almeno un competitor con dati, il livello sale di UNO (max VERY_HIGH).
    competitor_data = {asin: {title, subtitle, categories}} (vedi competitor_texts): senza dati competitor il bonus non scatta e
    competitor_data_available e' False. Nessun dato esterno viene inventato; nessuna rete."""
    kw = norm_text(keyword)
    b = plan["book"]
    sources = [s for s, txt in (("title", b.get("title")), ("subtitle", b.get("subtitle")),
                                ("kdp_keywords", " | ".join(b.get("kdp_keywords") or [])), ("description", b.get("description"))) if _contains(txt, kw)]
    cd = competitor_data or {}
    hits = sorted(a for a, t in cd.items() if any(_contains(v, kw) for v in t.values()))
    base = {0: "NONE", 1: "LOW", 2: "MEDIUM"}.get(len(sources), "HIGH")
    level = base
    boosted = bool(sources and hits)
    if boosted:
        level = EVIDENCE_LEVELS[min(len(EVIDENCE_LEVELS) - 1, EVIDENCE_LEVELS.index(base) + 1)]
    return {"keyword": keyword, "own_sources": sources, "competitor_hits": hits, "competitor_data_available": bool(cd),
            "base_level": base, "level": level, "competitor_boost": boosted,
            "note": ("dati competitor non disponibili: nessun bonus" if not cd else
                     "bonus competitor applicato" if boosted else "nessun bonus competitor")}


def evidence_table(plan: dict, competitor_data: dict | None = None) -> list:
    """Evidenza per ciascuna delle keyword KDP del libro. Se competitor_data e' None usa quelli arricchiti presenti nella bozza."""
    cd = competitor_texts(plan) if competitor_data is None else competitor_data
    return [keyword_evidence(k, plan, cd) for k in plan["book"].get("kdp_keywords") or []]


# ------------------------------------------------------------------ struttura consigliata (NON operativa)
def recommended_structure(plan: dict) -> list:
    """Suggerimenti strutturali NON operativi: nessuna campagna viene creata. Con almeno un ASIN competitor VALIDATO (fornito dall'utente)
    suggerisce 'Valuta campagna COMPETITOR' con l'elenco degli ASIN forniti. Gli ASIN NON VERIFICATO non entrano."""
    valid = [c for c in plan.get("competitors", []) if c.get("status") == STATUS_VALID]
    out = []
    if valid:
        out.append({"suggestion": SUGGESTION_COMPETITOR_CAMPAIGN, "campaign_type": "COMPETITOR", "operational": False,
                    "asins": [c["asin"] for c in valid], "source": SOURCE_USER_COMPETITOR,
                    "note": "ASIN competitor forniti dall'utente; l'esistenza su Amazon non e' verificata. Nessuna campagna viene creata."})
    return out
