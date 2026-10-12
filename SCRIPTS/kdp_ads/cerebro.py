"""RICERCA COMPETITOR: parser dei CSV Helium 10 Cerebro. Modulo PURO e LOCALE: legge byte in memoria, nessuna rete, nessuna scrittura, nessun accesso
al DB Ads ne' a books.json. NON passa dal parser dei report Amazon Ads.

FONTE ESTERNA / STIMATA: i dati Cerebro non sono ordini, clic, ACOS o vendite del nostro account Amazon Ads. Qui si leggono soltanto.

Riconoscimento colonne TOLLERANTE (alias normalizzati: minuscolo, senza punteggiatura). Nessuna colonna e' obbligatoria tranne la keyword. Cio' che non e'
riconosciuto resta in `raw` (valore originale). Un dato mancante o '-' resta None: nessun valore e' mai inventato.

Per ogni riga si conserva:
  raw          {intestazione originale: testo originale della cella}        -> valore ORIGINALE
  campi canonici numerici gia' normalizzati (search_volume, organic_rank, ...) -> valore NORMALIZZATO
  keyword      testo originale; keyword_norm = minuscolo + trim + spazi ridotti a uno (la stessa regola di metrics.normalize_term)

Nel file Cerebro di UN ASIN, "Organic Rank" / "Sponsored Rank" sono le posizioni di QUELL'ASIN per la keyword. L'ASIN si legge dal nome del file
(es. US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv) oppure va indicato a mano."""
import re
import statistics

from . import launch_research as LR

PARSER_VERSION = 1
SOURCE = "HELIUM10_CEREBRO"
SOURCE_LABEL = "Fonte esterna: Helium 10 Cerebro"
MAX_ROWS = 100_000

# campo canonico -> alias (gia' in forma normalizzata da LR._hnorm: minuscolo, senza punteggiatura)
ALIASES = {
    "keyword": ["keyword phrase", "keyword", "keywords", "search term", "phrase"],
    "search_volume": ["search volume", "searches", "monthly search volume", "volume"],
    "search_volume_trend": ["search volume trend", "sv trend", "trend"],
    "keyword_sales": ["keyword sales", "kw sales", "keyword sales est", "monthly keyword sales"],
    "cerebro_iq": ["cerebro iq score", "cerebro iq", "iq score"],
    "competing_products": ["competing products", "competitors", "competing product count", "number of competing products"],
    "title_density": ["title density", "title dens"],
    "organic_rank": ["organic rank", "organic position"],           # "Organic" da solo e' un indicatore 0/1 (resta in raw), NON un rank
    "sponsored_rank": ["sponsored rank", "sponsored position"],
    "amazon_recommended_rank": ["amazon recommended rank", "amazons choice rank", "amazon rec rank"],
    "ppc_bid": ["h10 ppc suggested bid", "h10 ppc sugg bid", "ppc suggested bid", "suggested bid", "h10 suggested bid", "suggested ppc bid"],
    "ppc_bid_min": ["suggested bid min", "h10 ppc sugg min bid", "h10 ppc suggested min bid", "ppc suggested min bid", "ppc bid min", "min bid"],
    "ppc_bid_max": ["suggested bid max", "h10 ppc sugg max bid", "h10 ppc suggested max bid", "ppc suggested max bid", "ppc bid max", "max bid"],
    "competitor_rank_avg": ["competitor rank avg", "competitor rank average", "competitor rank", "avg competitor rank"],
    "ranking_competitors": ["ranking competitors count", "ranking competitors", "number of ranking competitors"],
    "relative_rank": ["relative rank", "position relative rank"],
    "cpr": ["cpr", "cerebro product rank", "giveaway needed"],
    "sponsored_asins": ["sponsored asins", "sponsored asin count", "number of sponsored asins"],
}
_ALIAS_INDEX = {a: canon for canon, al in ALIASES.items() for a in al}
NUMERIC = tuple(k for k in ALIASES if k != "keyword")
RANK_FIELDS = ("organic_rank", "sponsored_rank", "amazon_recommended_rank", "relative_rank")
_ASIN_HEADER = re.compile(r"^(b0[a-z0-9]{8}|\d{9}[\dx])$", re.I)
_ASIN_IN_NAME = re.compile(r"(?<![A-Za-z0-9])(B0[A-Z0-9]{8}|\d{9}[\dX])(?![A-Za-z0-9])", re.I)
_CEREBRO_ASIN = re.compile(r"cerebro[^A-Za-z0-9]{1,6}(B0[A-Z0-9]{8}|\d{9}[\dX])(?![A-Za-z0-9])", re.I)
_MP_PREFIX = re.compile(r"^([A-Za-z]{2,3})[_\- ]AMAZON", re.I)
ASIN_RE = re.compile(r"^(B0[A-Z0-9]{8}|\d{9}[\dX])$")


class CerebroError(ValueError):
    """File non leggibile o non riconoscibile come export Cerebro (messaggio in italiano). Nessun import."""


def normalize_keyword(s) -> str:
    return re.sub(r"\s+", " ", LR.clean(s).lower()).strip()


def normalize_asin(raw) -> str | None:
    a = re.sub(r"\s+", "", str(raw or "")).upper()
    return a if ASIN_RE.match(a) else None


def asin_from_filename(name: str) -> str | None:
    """ASIN dal nome del file Cerebro (es. US_AMAZON_cerebro_B0H5MZP4QW_2026-10-12.csv). Prima il token dopo 'cerebro', poi qualunque ASIN isolato.
    Solo il formato; l'esistenza non e' verificata. Date come 2026-10-12 non sono scambiate per ISBN-10."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    m = _CEREBRO_ASIN.search(base) or _ASIN_IN_NAME.search(base)
    return m.group(1).upper() if m else None


def marketplace_from_filename(name: str) -> str | None:
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    m = _MP_PREFIX.match(base)
    return m.group(1).upper() if m else None


def _num(v):
    """Numero da testo; '-', '—', '' -> None. Un limite come '<100' non e' un valore (None). Convenzione US ('1,234' = 1234)."""
    s = LR.clean(v)
    if not s or s in {"-", "--", "—", "–", "n/a", "N/A", "NA", "null"}:
        return None
    s = s.replace("%", "").replace("$", "").strip()
    return LR.parse_number(s)


def _find_header(rows) -> int | None:
    for i, r in enumerate(rows[:LR.HEADER_SCAN_ROWS]):
        if any(_ALIAS_INDEX.get(LR._hnorm(h)) == "keyword" for h in r):
            return i
    return None


def parse_cerebro(name: str, data: bytes, asin: str | None = None) -> dict:
    """Legge e normalizza UN file Cerebro (un competitor). Nessuna scrittura. Ritorna
      {file_name, sha256, size, asin, asin_source (FILENAME|MANUAL|COLUMN|None), marketplace_hint, rows[], columns[{header, canonical}], unmapped[],
       rows_skipped, warnings[]}
    `asin` passato = indicazione manuale (vince sul nome del file). CerebroError se il file non e' leggibile o non ha la colonna keyword."""
    if not data:
        raise CerebroError("Il file e' vuoto")
    if len(data) > LR.MAX_BYTES:
        raise CerebroError(f"Il file supera {LR.MAX_BYTES // (1024 * 1024)} MB")
    if not str(name or "").lower().endswith(".csv"):
        raise CerebroError("Formato non supportato: servono i file .csv esportati da Helium 10 Cerebro")
    try:
        table = [[LR.clean(c) for c in r] for r in LR.read_csv(data)]
    except LR.ResearchError as e:
        raise CerebroError(str(e)) from e
    h = _find_header(table)
    if h is None:
        raise CerebroError("Colonna keyword non trovata (es. 'Keyword Phrase'): il file non sembra un export Helium 10 Cerebro")
    headers = table[h]
    cols, seen = [], set()
    for i, hd in enumerate(headers):
        canon = _ALIAS_INDEX.get(LR._hnorm(hd))
        if canon in seen:                                  # primo header per campo vince; i doppioni restano solo in raw
            canon = None
        if canon:
            seen.add(canon)
        cols.append({"index": i, "header": hd, "canonical": canon})
    kw_i = next(c["index"] for c in cols if c["canonical"] == "keyword")
    warnings = []
    file_asin = normalize_asin(asin) if asin else None
    asin_source = "MANUAL" if file_asin else None
    if asin and not file_asin:
        raise CerebroError(f"ASIN non valido: {asin!r} (servono 10 caratteri, es. B0H5MZP4QW)")
    if not file_asin:
        file_asin = asin_from_filename(name)
        asin_source = "FILENAME" if file_asin else None
    # colonna con l'ASIN del file come intestazione (alcuni export multi-ASIN) = posizione organica di quell'ASIN
    asin_cols = [c for c in cols if not c["canonical"] and _ASIN_HEADER.match(c["header"] or "")]
    if file_asin and "organic_rank" not in seen:
        own = next((c for c in asin_cols if c["header"].upper() == file_asin), None)
        if own:
            own["canonical"] = "organic_rank"
            seen.add("organic_rank")
            warnings.append(f"Colonna '{own['header']}' interpretata come Organic Rank dell'ASIN del file")
    rows, skipped = [], 0
    for r in table[h + 1:]:
        if not any(r):
            continue
        if len(rows) >= MAX_ROWS:
            warnings.append(f"Lette solo le prime {MAX_ROWS} righe")
            break
        kw = LR.clean(r[kw_i]) if kw_i < len(r) else ""
        if not kw:
            skipped += 1
            continue
        row = {"keyword": kw, "keyword_norm": normalize_keyword(kw), "raw": {}}
        for c in cols:
            cell = LR.clean(r[c["index"]]) if c["index"] < len(r) else ""
            if cell:
                row["raw"][c["header"] or f"col{c['index']}"] = cell
            canon = c["canonical"]
            if canon and canon != "keyword":
                v = _num(cell)
                if canon in RANK_FIELDS and v is not None and v <= 0:
                    v = None                                # rank 0 / negativo = nessuna posizione
                if canon == "competing_products" and v is not None and v <= 0:
                    v = None                                # 0 prodotti in competizione non e' realistico: dato non calcolato (resta in raw)
                row[canon] = v
        for k in NUMERIC:
            row.setdefault(k, None)
        rows.append(row)
    if not rows:
        raise CerebroError("Nessuna keyword nel file")
    if skipped:
        warnings.append(f"{skipped} righe senza keyword ignorate")
    if "organic_rank" not in seen and "sponsored_rank" not in seen:
        warnings.append("Nessuna colonna di rank (Organic/Sponsored): le keyword non hanno posizioni, l'affidabilita' del confronto e' ridotta")
    if "search_volume" not in seen:
        warnings.append("Colonna Search Volume non trovata")
    return {"file_name": str(name), "sha256": LR.sha256(data), "size": len(data), "asin": file_asin, "asin_source": asin_source,
            "marketplace_hint": marketplace_from_filename(name), "rows": rows, "columns": cols,
            "unmapped": [c["header"] for c in cols if not c["canonical"]], "rows_skipped": skipped, "warnings": warnings,
            "parser_version": PARSER_VERSION}


def merge_duplicate_rows(rows: list) -> list:
    """Dentro UN file la stessa keyword normalizzata puo' comparire due volte ('Mystery Books' e 'mystery  books'): una sola osservazione per keyword.
    Rank = il MIGLIORE (minimo); metriche di volume = il massimo; le altre = prima non nulla. Il testo originale e' quello della prima riga."""
    out = {}
    for r in rows:
        cur = out.get(r["keyword_norm"])
        if cur is None:
            out[r["keyword_norm"]] = dict(r, raw=dict(r["raw"]))
            continue
        for k in NUMERIC:
            a, b = cur.get(k), r.get(k)
            if b is None:
                continue
            if a is None:
                cur[k] = b
            elif k in RANK_FIELDS:
                cur[k] = min(a, b)
            elif k in ("search_volume", "keyword_sales", "competing_products", "title_density"):
                cur[k] = max(a, b)
        cur["raw"].update({k: v for k, v in r["raw"].items() if k not in cur["raw"]})
    return list(out.values())


def median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None
