"""LANCIO NUOVO LIBRO: ricerca Publisher Rocket (file CSV / XLSX / XLS caricati A MANO dall'utente). Modulo PURO e LOCALE: legge byte in memoria,
nessuna rete, nessuna scrittura (la scrittura nella bozza e' in DASHBOARD/ops/launch_ops.py), nessun accesso a INPUT, books.json o al DB Ads.

NESSUNO SCHEMA FISSO: le colonne vengono riconosciute per ALIAS normalizzati (minuscolo, senza punteggiatura). Cio' che non e' riconosciuto resta
in "extra" solo se la cella ha davvero un valore. Un dato mancante resta None: nessun valore viene mai inventato ne' stimato.
Formati: CSV (UTF-8 con/senza BOM, UTF-16, cp1252; separatore , ; tab |), XLSX (letto con la sola libreria standard: zip + XML, primo foglio non
vuoto), XLS (solo se e' installato `xlrd`; altrimenti errore chiaro: salva il file come XLSX o CSV).

FORMATO INTERNO (un file importato => un "research record"):
  keywords:    [{keyword, source="PUBLISHER_ROCKET", source_file, marketplace, competitor_asin|None, <metriche presenti>, extra{}}]
  competitors: [{asin, source="PUBLISHER_ROCKET", source_file, title, price, rating, review_count, bsr, categories[], ..., extra{}}]
Metriche canoniche: search_volume (numero) / search_volume_text (es. "<100"), searches_color, competition, competition_color, title_count,
avg_price, avg_reviews, avg_pages, avg_monthly_earnings, price, rating, review_count, bsr, sales_month, pages, categories.
"<100" non viene convertito in un numero: resta testo (search_volume None, search_volume_text "<100")."""
import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from . import launch as L

PARSER_VERSION = 2                      # 2: alias "Search Per Month"/"Average Sales", ASIN dal nome dei file "reverse ASIN lookup"
SOURCE = "PUBLISHER_ROCKET"
PROVIDER_LABEL = "Publisher Rocket"
MAX_BYTES = 15 * 1024 * 1024
MAX_ROWS = 50_000
MAX_KEYWORDS = 5_000
MAX_UNZIPPED = 100 * 1024 * 1024
HEADER_SCAN_ROWS = 20
EXTENSIONS = {".csv": "CSV", ".xlsx": "XLSX", ".xls": "XLS"}
KIND_KEYWORDS, KIND_COMPETITORS, KIND_BOTH, KIND_UNKNOWN = "KEYWORDS", "COMPETITORS", "KEYWORDS+COMPETITORS", "UNKNOWN"

# alias normalizzati (vedi _hnorm) -> campo canonico. Ordine irrilevante.
ALIASES = {
    "keyword": ["keyword", "keywords", "ams keyword", "key word", "search term", "customer search term", "query", "phrase"],
    "asin": ["asin", "asin isbn", "product asin", "amazon asin", "asin number"],
    "title": ["title", "book title", "product title", "product name", "name"],
    "subtitle": ["subtitle", "sub title"],
    "author": ["author", "authors", "brand"],
    "price": ["price", "list price", "kindle price", "paperback price", "book price"],
    "avg_price": ["average price", "avg price", "average book price"],
    "reviews": ["reviews", "review count", "number of reviews", "reviews count", "num reviews", "ratings count", "customer reviews"],
    "avg_reviews": ["avg reviews", "average reviews", "average number of reviews", "avg number of reviews"],
    "rating": ["rating", "avg rating", "average rating", "stars", "star rating"],
    "bsr": ["bsr", "sales rank", "best sellers rank", "best seller rank", "kindle sales rank", "amazon best sellers rank", "rank"],
    "avg_sales": ["average sales", "avg sales", "average sales month", "est sales"],
    "search_volume": ["search per month", "searches per month", "est amazon searches month", "estimated searches", "estimated monthly searches", "search volume", "monthly searches",
                      "amazon searches month", "searches month", "est searches", "est monthly searches", "estimated amazon searches month"],
    "searches_color": ["search per month color", "searches per month color", "amazon searches month color", "search volume color", "searches color"],
    "competition": ["competitive score", "competition score", "competition", "competitiveness"],
    "competition_color": ["competitive score color", "competition color", "competition score color"],
    "title_count": ["number of competitors", "competitors", "title count", "number of titles", "competing titles", "number of results", "results"],
    "avg_pages": ["average pages", "avg pages", "average page count"],
    "pages": ["pages", "page count", "number of pages", "print length"],
    "avg_monthly_earnings": ["average monthly earnings", "avg monthly earnings", "est monthly earnings", "monthly earnings", "est monthly revenue"],
    "sales_month": ["est monthly sales", "monthly sales", "sales month", "est sales month", "estimated monthly sales"],
    "categories": ["categories", "category", "categorie", "bsr categories", "amazon categories", "book categories"],
    "marketplace": ["marketplace", "country", "store", "amazon store"],
}
_ALIAS_INDEX = {a: canon for canon, al in ALIASES.items() for a in al}
NUMERIC_FIELDS = ("avg_sales", "price", "avg_price", "reviews", "avg_reviews", "rating", "bsr", "search_volume", "competition", "title_count", "avg_pages",
                  "pages", "avg_monthly_earnings", "sales_month")
TEXT_FIELDS = ("searches_color", "competition_color", "author", "subtitle", "marketplace")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_BOUND = re.compile(r"^\s*([<>])\s*=?\s*([\d.,]+)\s*\+?\s*$")


class ResearchError(ValueError):
    """File non leggibile o non riconoscibile (messaggio in italiano). Nessun import."""


def _hnorm(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def clean(v) -> str:
    return _CTRL.sub("", str(v if v is not None else "")).strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_format(name: str) -> str:
    n = str(name or "").lower()
    for ext, fmt in EXTENSIONS.items():
        if n.endswith(ext):
            return fmt
    raise ResearchError("Formato non supportato: servono file .csv, .xlsx o .xls")


# ------------------------------------------------------------------ lettura delle tabelle
def _decode(data: bytes) -> str:
    if b"\x00" in data[:4096] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ResearchError("Il file non e' un CSV di testo (contiene dati binari)")
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise ResearchError("Codifica del CSV non riconosciuta")


def read_csv(data: bytes) -> list:
    text = _decode(data)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ResearchError("Il file e' vuoto")
    probe = "\n".join(lines[:5])
    delim = max((",", ";", "\t", "|"), key=lambda d: probe.count(d))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data: bytes) -> tuple:
    """(righe, nome foglio). Solo libreria standard; primo foglio con almeno una riga non vuota. Nessuna formula valutata (si legge il valore)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ResearchError("Il file non e' un XLSX valido (archivio non leggibile)")
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ResearchError("Il file XLSX e' troppo grande una volta decompresso")
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        raise ResearchError("Il file non e' un XLSX valido (manca xl/workbook.xml)")
    shared = []
    if "xl/sharedStrings.xml" in names:
        for ev, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
            if el.tag == f"{{{_NS['m']}}}si":
                shared.append("".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t")))
                el.clear()
    wb = ET.parse(z.open("xl/workbook.xml")).getroot()
    rels = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for r in ET.parse(z.open("xl/_rels/workbook.xml.rels")).getroot():
            rels[r.get("Id")] = r.get("Target")
    sheets = []
    for s in wb.iter(f"{{{_NS['m']}}}sheet"):
        target = rels.get(s.get(f"{{{_NS['r']}}}id")) or ""
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        sheets.append((s.get("name"), path if path in names else None))
    if not sheets:
        sheets = [(n, n) for n in sorted(names) if n.startswith("xl/worksheets/sheet")]
    for sname, path in sheets:
        if not path:
            continue
        rows = []
        for ev, el in ET.iterparse(z.open(path)):
            if el.tag != f"{{{_NS['m']}}}row":
                continue
            cells = {}
            for c in el.iter(f"{{{_NS['m']}}}c"):
                t, v = c.get("t"), c.find("m:v", _NS)
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
                elif v is None or v.text is None:
                    continue
                elif t == "s":
                    val = shared[int(v.text)] if int(v.text) < len(shared) else ""
                elif t == "b":
                    val = "TRUE" if v.text == "1" else "FALSE"
                else:
                    val = v.text
                    if t in (None, "n") and re.fullmatch(r"-?\d+\.0", val):
                        val = val[:-2]
                cells[_col_index(c.get("r"))] = val
            el.clear()
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
            if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
                raise ResearchError(f"Troppe righe (oltre {MAX_ROWS})")
        if rows:
            return rows, sname
    raise ResearchError("Il file XLSX non contiene dati")


def read_xls(data: bytes) -> tuple:
    try:
        import xlrd
    except ImportError:
        raise ResearchError("Il formato XLS (Excel 97-2003) richiede la libreria 'xlrd', non installata: apri il file in Excel e salvalo come XLSX o CSV, poi caricalo")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as e:                                           # noqa: BLE001 - qualunque errore di xlrd = file non leggibile
        raise ResearchError(f"Il file XLS non e' leggibile: {e}")
    for sh in book.sheets():
        rows = [[clean(sh.cell_value(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
        rows = [r for r in rows if any(r)]
        if rows:
            return rows, sh.name
    raise ResearchError("Il file XLS non contiene dati")


def read_table(name: str, data: bytes) -> dict:
    """{format, sheet, rows: [[str]]} dal contenuto del file; ResearchError se non leggibile."""
    if not data:
        raise ResearchError("Il file e' vuoto")
    if len(data) > MAX_BYTES:
        raise ResearchError(f"Il file supera {MAX_BYTES // (1024 * 1024)} MB")
    fmt = file_format(name)
    sheet = None
    if fmt == "CSV":
        rows = read_csv(data)
    elif fmt == "XLSX":
        rows, sheet = read_xlsx(data)
    else:
        rows, sheet = read_xls(data)
    return {"format": fmt, "sheet": sheet, "rows": [[clean(c) for c in r] for r in rows]}


# ------------------------------------------------------------------ riconoscimento colonne
def find_header(rows: list) -> int | None:
    """Indice della riga di intestazione: la prima (tra le prime righe) con almeno una colonna riconosciuta come keyword o ASIN."""
    for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
        canon = {_ALIAS_INDEX.get(_hnorm(h)) for h in r}
        if canon & {"keyword", "asin"}:
            return i
    return None


def map_columns(headers: list) -> list:
    """[{index, header, canonical|None}]; il primo header per campo canonico vince, i doppioni restano non mappati (finiscono in extra)."""
    seen, out = set(), []
    for i, h in enumerate(headers):
        canon = _ALIAS_INDEX.get(_hnorm(h))
        if canon in seen:
            canon = None
        if canon:
            seen.add(canon)
        out.append({"index": i, "header": clean(h), "canonical": canon})
    return out


def parse_number(v):
    """Numero da testo ('$ 12', '1,494', '4.5'); None se non e' un numero. Convenzione US (come i file Publisher Rocket): ',' migliaia, '.' decimali."""
    if re.match(r"^\s*[<>~≈]", clean(v)):                  # "<100", ">1,000": un limite, non un valore: resta testo
        return None
    s = re.sub(r"[^\d,.\-]", "", clean(v))
    if not s or s in ("-", ".", ","):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    if not _NUM.match(s):
        return None
    x = float(s)
    return int(x) if x == int(x) and "." not in s else x


def _asin_of(cell) -> str | None:
    raw = clean(cell)
    if not raw:
        return None
    try:
        return L.parse_asin_input(raw, "US" if "amazon." in raw.lower() and ".com/" in raw.lower() else _any_mp(raw))
    except L.LaunchError:
        return None


def _any_mp(raw: str) -> str:
    """Per gli URL accettiamo qualunque dominio Amazon noto (il controllo di marketplace lo fa il chiamante sul competitor)."""
    host = re.sub(r"^https?://(www\.)?", "", raw.lower()).split("/")[0]
    return next((mp for dom, mp in L.MARKETPLACE_DOMAINS.items() if host == dom or host.endswith("." + dom)), "US")


_REVERSE_ASIN = re.compile(r"reverse\s+asin\s+lookup[^A-Za-z0-9]{0,12}(?:book\s+)?([A-Z0-9]{10})(?![A-Za-z0-9])", re.I)


def asin_from_filename(name: str) -> str | None:
    """ASIN del competitor nel nome di un export "REVERSE ASIN LOOKUP - BOOK <ASIN>" di Publisher Rocket (le keyword sono quelle per cui quell'ASIN
    compare). Solo il formato ASIN; l'esistenza non e' verificata. Nessun altro nome di file viene interpretato."""
    m = _REVERSE_ASIN.search(str(name or ""))
    return m.group(1).upper() if m else None


# ------------------------------------------------------------------ analisi del file (dry run) e normalizzazione
def parse_file(name: str, data: bytes, marketplace: str | None = None, now: str | None = None) -> dict:
    """Legge e normalizza un file Publisher Rocket SENZA scrivere nulla. Ritorna un 'research record':
    {file_name, format, sheet, sha256, size, kind, rows, columns[], unmapped[], keywords[], competitors[], metrics_present{}, problems[], warnings[]}.
    ResearchError se il file non e' leggibile o non e' riconoscibile (nessuna keyword ne' ASIN)."""
    t = read_table(name, data)
    rows = t["rows"]
    h = find_header(rows)
    if h is None:
        raise ResearchError("File non riconosciuto: non trovo una colonna keyword o ASIN nelle prime righe. Colonne attese (nomi equivalenti accettati): "
                            "Keyword, ASIN, Title, Search Volume, Competition, Reviews, Price, BSR, Categories")
    cols = map_columns(rows[h])
    by = {c["canonical"]: c["index"] for c in cols if c["canonical"]}
    body = rows[h + 1:]
    if len(body) > MAX_ROWS:
        raise ResearchError(f"Troppe righe (oltre {MAX_ROWS})")
    kind = KIND_BOTH if ("keyword" in by and "asin" in by) else KIND_KEYWORDS if "keyword" in by else KIND_COMPETITORS
    problems, warnings = [], []
    keywords, competitors, seen_kw, seen_asin = [], [], {}, {}
    metrics = {}
    dup_kw = bad_asin = skipped = 0
    ts = now or datetime.now().isoformat(timespec="seconds")
    file_asin = asin_from_filename(name) if "asin" not in by else None      # file "reverse ASIN lookup": le keyword sono quelle del competitor nel nome

    def cell(r, canon):
        i = by.get(canon)
        return clean(r[i]) if i is not None and i < len(r) else ""

    def extras(r):
        out = {}
        for c in cols:
            if c["canonical"] is None and c["index"] < len(r) and clean(r[c["index"]]) and len(out) < 20:
                out[c["header"] or f"colonna_{c['index'] + 1}"] = clean(r[c["index"]])[:300]
        return out

    def metrics_of(r, fields):
        out = {}
        for f in fields:
            raw = cell(r, f)
            if not raw:
                continue
            if f == "search_volume":
                num = parse_number(raw)
                if num is not None:
                    out["search_volume"] = num
                else:
                    out["search_volume_text"] = raw[:30]                    # es. "<100": NON si trasforma in un numero
                continue
            if f == "categories":
                out["categories"] = [p.strip() for p in re.split(r"[;|\n]+", raw) if p.strip()][:20]
            elif f in NUMERIC_FIELDS:
                num = parse_number(raw)
                if num is not None:
                    out[f] = num
                else:
                    out[f + "_text"] = raw[:30]
            else:
                out[f] = raw[:200]
        return out

    kw_fields = ("search_volume", "searches_color", "competition", "competition_color", "title_count", "avg_price", "avg_reviews", "avg_pages",
                 "avg_sales", "avg_monthly_earnings", "price", "reviews", "rating", "bsr", "sales_month", "pages", "categories")
    for r in body:
        if not any(clean(c) for c in r):
            continue
        asin = _asin_of(cell(r, "asin")) if "asin" in by else None
        if "asin" in by and cell(r, "asin") and not asin:
            bad_asin += 1
        kw = re.sub(r"\s+", " ", cell(r, "keyword").lower()).strip() if "keyword" in by else ""
        if kw:
            if len(kw) > 120:
                skipped += 1
            elif kw in seen_kw:
                dup_kw += 1
            elif len(keywords) >= MAX_KEYWORDS:
                skipped += 1
            else:
                m = metrics_of(r, kw_fields)
                rec = {"keyword": kw, "source": SOURCE, "source_file": name, "marketplace": marketplace or cell(r, "marketplace") or None,
                       "competitor_asin": asin or file_asin, **{k: v for k, v in m.items()}}
                if file_asin and not asin:
                    rec["competitor_asin_source"] = "file_name"
                ex = extras(r)
                if ex:
                    rec["extra"] = ex
                seen_kw[kw] = len(keywords)
                keywords.append(rec)
                for k in m:
                    metrics[k] = metrics.get(k, 0) + 1
        if asin and (kind != KIND_KEYWORDS):
            m = metrics_of(r, ("price", "reviews", "rating", "bsr", "sales_month", "pages", "categories", "avg_monthly_earnings"))
            comp = {"asin": asin, "source": SOURCE, "source_file": name}
            for src, dst in (("title", "title"), ("subtitle", "subtitle"), ("author", "author")):
                v = cell(r, src)
                if v:
                    comp[dst] = v[:300]
            if "reviews" in m:
                m["review_count"] = m.pop("reviews")
            comp.update(m)
            ex = extras(r)
            if ex:
                comp["extra"] = ex
            if asin in seen_asin:
                prev = competitors[seen_asin[asin]]
                for k, v in comp.items():                          # stesso ASIN su piu' righe: si completano solo i campi mancanti
                    prev.setdefault(k, v)
            else:
                seen_asin[asin] = len(competitors)
                competitors.append(comp)
    comp_list = competitors
    if not keywords and not comp_list:
        raise ResearchError("File non riconosciuto: le colonne ci sono ma non contengono nessuna keyword ne' ASIN utilizzabile")
    if dup_kw:
        warnings.append(f"{dup_kw} keyword duplicate nel file: tenuta la prima occorrenza")
    if bad_asin:
        warnings.append(f"{bad_asin} valori nella colonna ASIN non sono ASIN validi: ignorati")
    if skipped:
        warnings.append(f"{skipped} keyword ignorate (troppo lunghe o oltre il limite di {MAX_KEYWORDS})")
    for c in comp_list:
        for k in c:
            if k not in ("asin", "source", "source_file", "extra"):
                metrics[k] = metrics.get(k, 0) + 1
    if not keywords and kind != KIND_COMPETITORS:
        warnings.append("Nessuna keyword utilizzabile nel file")
    return {"file_name": name, "format": t["format"], "sheet": t["sheet"], "sha256": sha256(data), "size": len(data), "kind": kind,
            "rows": len(body), "header_row": h + 1, "columns": [{"header": c["header"], "canonical": c["canonical"]} for c in cols],
            "unmapped": [c["header"] for c in cols if not c["canonical"] and c["header"]], "keywords": keywords, "competitors": comp_list,
            "metrics_present": dict(sorted(metrics.items())), "problems": problems, "warnings": warnings, "parsed_at": ts,
            "parser_version": PARSER_VERSION, "reverse_asin": file_asin}


# ------------------------------------------------------------------ collegamento alla bozza
def match_competitors(plan: dict, record: dict) -> dict:
    """ASIN del file confrontati con i competitor GIA' inseriti nella bozza: {matched:[{asin,status}], candidates:[{asin,title,...}]}.
    Un ASIN fuori dalla bozza e' solo un "competitor candidato": non entra mai tra i competitor validati."""
    have = {c["asin"]: c for c in plan.get("competitors", [])}
    matched, candidates = [], []
    for c in record["competitors"]:
        if c["asin"] in have:
            matched.append({"asin": c["asin"], "status": have[c["asin"]].get("status"), "title": c.get("title")})
        else:
            candidates.append({k: c.get(k) for k in ("asin", "title", "price", "review_count", "rating", "bsr") if c.get(k) is not None})
    for k in record["keywords"]:                       # un ASIN citato solo come competitor_asin di una keyword
        a = k.get("competitor_asin")
        if a and a not in have and all(x["asin"] != a for x in candidates):
            candidates.append({"asin": a})
    return {"matched": matched, "candidates": candidates}


def enrichment_from(comp: dict, now: str | None = None) -> dict:
    """Dati di arricchimento (formato launch.ENRICHMENT_FIELDS) dai soli campi realmente presenti nel file."""
    e = {k: comp[k] for k in ("title", "subtitle", "categories", "price", "rating", "review_count") if comp.get(k) not in (None, "", [])}
    extra = {k: comp[k] for k in ("bsr", "author", "pages", "sales_month", "avg_monthly_earnings") if comp.get(k) is not None}
    if comp.get("extra"):
        extra.update(comp["extra"])
    if extra:
        e["extra"] = extra
    e.update({"provider": SOURCE, "provider_label": PROVIDER_LABEL, "source_file": comp.get("source_file"), "enriched_at": now or L._now()})
    return e


def apply_enrichment(plan: dict, record: dict, now: str | None = None) -> list:
    """Arricchisce IN PLACE i competitor della bozza gia' presenti; ritorna gli ASIN arricchiti. Non aggiunge mai competitor.
    Un secondo file completa i campi ma non cancella quelli gia' noti."""
    done = []
    by = {c["asin"]: c for c in record["competitors"]}
    for c in plan.get("competitors", []):
        src = by.get(c["asin"])
        if not src:
            continue
        new = enrichment_from(src, now)
        old = c.get("enrichment") or {}
        merged = {**old, **new}
        if old.get("extra") or new.get("extra"):
            merged["extra"] = {**(old.get("extra") or {}), **(new.get("extra") or {})}
        c["enrichment"] = merged
        done.append(c["asin"])
    return done


def research_for_plan(records: list) -> dict:
    """{keyword: [record keyword, ...]} da piu' file importati (per il motore keyword)."""
    out: dict = {}
    for rec in records:
        for k in rec.get("keywords", []):
            out.setdefault(k["keyword"], []).append(k)
    return out


# ------------------------------------------------------------------ dry run per la pagina (nessuna scrittura)
def dry_run_report(plan: dict, name: str, data: bytes) -> dict:
    """Esito del DRY RUN di un file per la bozza `plan`: cosa contiene, cosa verrebbe collegato, quali keyword risulterebbero pertinenti o scartate.
    status: DRY_RUN_OK / SKIPPED_DUPLICATE (stesso contenuto gia' importato nella bozza) / UNRECOGNIZED / ERROR. NON scrive nulla e non modifica `plan`."""
    from copy import deepcopy

    from . import launch_keywords as K
    base = {"file_name": name, "ok": False, "problems": [], "warnings": []}
    try:
        rec = parse_file(name, data, (plan.get("book") or {}).get("marketplace"))
    except ResearchError as e:
        try:
            fmt = file_format(name)
        except ResearchError:
            fmt = None
        status = "UNRECOGNIZED" if "non riconosciuto" in str(e).lower() else "ERROR"
        return {**base, "status": status, "format": fmt, "size": len(data or b""), "problems": [str(e)]}
    done = {f.get("sha256") for f in (plan.get("research") or {}).get("files", [])}
    dup = rec["sha256"] in done
    match = match_competitors(plan, rec)
    tmp = deepcopy(plan)
    apply_enrichment(tmp, rec)
    # i dati gia' importati nella bozza + quelli di questo file: anteprima di cosa diventerebbero le keyword nel piano
    prior = plan.get("_research_records") or []
    kp = K.build_keyword_plan(tmp, research=research_for_plan(list(prior) + [rec]))
    cls = {e["keyword"]: e for k in ("exact", "phrase", "discovery", "rejected") for e in kp[k]}
    pert, disc = [], []
    for k in rec["keywords"]:
        e = cls.get(k["keyword"])
        if e is None:
            continue
        (disc if e["recommended_match_type"] == "REJECT" else pert).append({"keyword": k["keyword"], "match": e["recommended_match_type"],
                                                                           "confidence": e["confidence"], "research_bonus": e.get("research_bonus", 0)})
    return {**base, "status": "SKIPPED_DUPLICATE" if dup else "DRY_RUN_OK", "ok": not dup, "format": rec["format"], "sheet": rec["sheet"],
            "size": rec["size"], "sha256": rec["sha256"], "rows": rec["rows"], "header_row": rec["header_row"], "kind": rec["kind"],
            "columns": rec["columns"], "unmapped": rec["unmapped"], "keywords_found": len(rec["keywords"]),
            "keywords_sample": [k["keyword"] for k in rec["keywords"][:10]], "asins_found": len(rec["competitors"]),
            "asins_sample": [c["asin"] for c in rec["competitors"][:10]], "metrics_present": rec["metrics_present"],
            "competitors_enriched": match["matched"], "competitor_candidates": match["candidates"],
            "keyword_preview": {"pertinent": len(pert), "discarded": len(disc), "pertinent_sample": pert[:8], "discarded_sample": disc[:8]},
            "warnings": rec["warnings"], "problems": (["Stesso contenuto gia' importato in questa bozza: nessun nuovo import"] if dup else []),
            "record": rec}
