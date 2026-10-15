"""REPORT GLOBALI AMAZON ADS (Targeting / Search Term, 7D / 30D): parser PURO e LOCALE. Legge byte in memoria: nessuna rete, nessuna scrittura, nessuna Amazon.

Un file contiene MOLTE campagne e molti libri. Qui si legge soltanto: lo smistamento per libro e' in kdp_ads/globalstore.py.

FORMATO REALE (intestazioni italiane): Valuta budget, Intervallo di date, ID account inserzionista, ID/Nome portfolio, ID/Nome della campagna, ID/Nome del gruppo di
annunci, [Termine di ricerca], ID target, Targeting, Tipo di corrispondenza del targeting, [Offerta per il targeting, Tipo di target, Stato del target],
Impressioni, Clic, Costo totale, Acquisti, Vendite, Unita' vendute, ... Gli ID arrivano nel formato Excel-like ="176948463003177": sono sempre STRINGHE, mai numeri
(nessuna perdita di precisione).

REGOLE
  - il tipo di report (TARGETING / SEARCH_TERM) e' riconosciuto dalle colonne; la FINESTRA (RECENT_7D / RECENT_30D) la sceglie l'utente per file: NON si deduce mai da
    "Intervallo di date", che e' un metadato della singola riga (un target comparso a meta' finestra ha un intervallo piu' corto);
  - snapshot: date_to = ultima data finale delle righe (o quella indicata dall'utente), date_from = date_to - (giorni - 1);
  - Search Term vuoto: la riga e' CONSERVATA (flag empty_search_term) ma non e' mai un search term reale (nessun "nan", nessuna promozione, nessuna negativa);
  - target parent del Search Term = colonne "Targeting" / "ID target" / "Tipo di corrispondenza del targeting" del report (mai dedotto dal testo del termine);
  - identita' Auto (close-match, loose-match, complements, substitutes) e prodotto (asin=, asin-expanded=) restano nel testo di "Targeting" cosi' come sono."""
import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import date, timedelta

PARSER_VERSION = 1
SOURCE = "AMAZON_GLOBAL_REPORT"
WINDOWS = {"RECENT_7D": 7, "RECENT_30D": 30}
REPORT_TYPES = {"TARGETING": "Targeting", "SEARCH_TERM": "Search Term"}
MAX_BYTES = 60 * 1024 * 1024
MAX_ROWS = 400_000

_MONTHS = {"gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
           "jan": 1, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "dec": 12}
CURRENCY_MARKETPLACE = {"USD": "US", "GBP": "UK", "CAD": "CA", "AUD": "AU", "JPY": "JP"}      # EUR e' ambiguo (DE/IT/FR/ES): si sceglie a mano

# campo canonico -> alias normalizzati (minuscolo, senza accenti/punteggiatura)
ALIASES = {
    "currency": ["valuta budget", "valuta", "currency"], "date_range": ["intervallo di date", "date range"],
    "account_id": ["id account inserzionista"], "account_name": ["nome account inserzionista"],
    "portfolio_id": ["id portfolio"], "portfolio_name": ["nome portfolio"],
    "campaign_id": ["id della campagna", "id campagna"], "campaign_name": ["nome della campagna", "nome campagna"],
    "ad_group_id": ["id del gruppo di annunci"], "ad_group_name": ["nome del gruppo di annunci"],
    "search_term": ["termine di ricerca", "termine di ricerca del cliente"],
    "target_id": ["id target"], "targeting": ["targeting"], "match_type": ["tipo di corrispondenza del targeting"],
    "bid": ["offerta per il targeting", "offerta"], "target_kind": ["tipo di target"], "target_state": ["stato del target"],
    "impressions": ["impressioni"], "clicks": ["clic"], "spend": ["costo totale"], "orders": ["acquisti"], "sales": ["vendite"], "units": ["unita vendute"],
}
_ALIAS_INDEX = {a: c for c, al in ALIASES.items() for a in al}
REQUIRED = ("campaign_id", "campaign_name", "impressions", "clicks", "spend", "orders", "sales")
# tipo di corrispondenza Amazon -> etichetta italiana usata dal resto del motore (opportunita', gap, decisioni)
MATCH_IT = {"PHRASE": "A frase", "EXACT": "Esatta", "BROAD": "Generica", "TARGETING_EXPRESSION_PREDEFINED": "", "TARGETING_EXPRESSION": ""}


class GlobalReportError(ValueError):
    """File non leggibile o non riconoscibile come report globale Amazon Ads (messaggio italiano). Nessun import."""


def _hnorm(h) -> str:
    s = unicodedata.normalize("NFKD", str(h or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def clean_id(v) -> str | None:
    """'="176948463003177"' -> '176948463003177'. Sempre testo; vuoto/None se assente."""
    s = str(v if v is not None else "").strip()
    m = re.fullmatch(r'=?\s*"?([^"]*)"?\s*', s)
    s = (m.group(1) if m else s).strip().strip('"').strip()
    return s or None


def num(v):
    """Numero da testo ('0.12', '1,234.50', '100.0000%', '0,12'); None se vuoto o non numerico."""
    s = str(v if v is not None else "").strip().replace("%", "").replace("$", "").replace("£", "").replace("€", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_it_date(s) -> date | None:
    """'13 ott 2026' -> date(2026, 10, 13). Accetta anche ISO (2026-10-13)."""
    t = str(s or "").strip().lower().rstrip(".")
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?\s+(\d{4})", t)
    if m and m.group(2) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def parse_range(s) -> tuple:
    """'7 ott 2026 - 13 ott 2026' -> (date, date); (None, None) se non leggibile. Metadato di RIGA, non definisce lo snapshot."""
    parts = re.split(r"\s+-\s+", str(s or "").strip())
    if len(parts) == 2:
        a, b = parse_it_date(parts[0]), parse_it_date(parts[1])
        if a and b:
            return a, b
    return None, None


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise GlobalReportError("Codifica del CSV non riconosciuta")


def detect_report_type(canon: set) -> str | None:
    if "search_term" in canon:
        return "SEARCH_TERM"
    if "target_id" in canon and ("target_state" in canon or "bid" in canon):
        return "TARGETING"
    return None


def snapshot_dates(rows: list, days: int, date_to_override: date | None = None) -> tuple:
    """(date_from, date_to) ISO dello SNAPSHOT: date_to = override utente oppure la data finale piu' recente fra le righe; date_from = date_to - (giorni - 1)."""
    ends = [r["row_to"] for r in rows if r.get("row_to")]
    end = date_to_override or (max(ends) if ends else None)
    if end is None:
        return None, None
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def parse_global_report(name: str, data: bytes) -> dict:
    """Legge UN report globale. Nessuna scrittura. Ritorna
      {file_name, sha256, size, report_type, currency, marketplace_hint, rows[], columns[{header, canonical}], unmapped[], row_count, date_min, date_max,
       range_counts{}, empty_search_term_rows, warnings[]}
    GlobalReportError se non e' un CSV leggibile o mancano colonne essenziali."""
    if not data:
        raise GlobalReportError("Il file e' vuoto")
    if len(data) > MAX_BYTES:
        raise GlobalReportError(f"Il file supera {MAX_BYTES // (1024 * 1024)} MB")
    if not str(name or "").lower().endswith(".csv"):
        raise GlobalReportError("Formato non supportato: serve un file .csv esportato da Amazon Ads")
    text = _decode(data)
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    table = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not table:
        raise GlobalReportError("Il file e' vuoto")
    headers = table[0]
    cols, seen = [], set()
    for i, h in enumerate(headers):
        c = _ALIAS_INDEX.get(_hnorm(h))
        if c in seen:
            c = None                                           # primo header per campo vince; i doppioni restano solo in raw
        if c:
            seen.add(c)
        cols.append({"index": i, "header": h.strip(), "canonical": c})
    rtype = detect_report_type(seen)
    if rtype is None:
        raise GlobalReportError("Tipo di report non riconosciuto: servono le colonne di un report Search Term («Termine di ricerca») o Targeting («ID target», «Stato del target»)")
    missing = [f for f in REQUIRED if f not in seen]
    if missing:
        raise GlobalReportError("Colonne obbligatorie mancanti: " + ", ".join(missing))
    idx = {c["canonical"]: c["index"] for c in cols if c["canonical"]}
    warnings, rows, bad = [], [], 0
    currencies, range_counts = set(), {}
    for n, r in enumerate(table[1:], start=2):
        if len(rows) >= MAX_ROWS:
            warnings.append(f"Lette solo le prime {MAX_ROWS} righe")
            break

        def g(c):
            i = idx.get(c)
            return r[i].strip() if i is not None and i < len(r) else ""
        cid = clean_id(g("campaign_id"))
        if not cid:
            bad += 1
            continue
        rf, rt = parse_range(g("date_range"))
        range_counts[g("date_range")] = range_counts.get(g("date_range"), 0) + 1
        term = g("search_term") if rtype == "SEARCH_TERM" else None
        mt_raw = g("match_type")
        pid = clean_id(g("portfolio_id"))
        row = {
            "campaign_id": cid, "campaign_name": g("campaign_name") or None, "portfolio_id": None if pid in (None, "-1") else pid,
            "portfolio_name": (g("portfolio_name") if pid not in (None, "-1") else "") or None, "ad_group_id": clean_id(g("ad_group_id")),
            "ad_group_name": g("ad_group_name") or None, "target_id": clean_id(g("target_id")), "targeting": g("targeting") or None,
            "match_type_raw": mt_raw or None, "match_type": MATCH_IT.get(mt_raw.upper(), mt_raw), "search_term": (term or None) if rtype == "SEARCH_TERM" else None,
            "empty_search_term": bool(rtype == "SEARCH_TERM" and not (term or "").strip()), "row_from": rf, "row_to": rt, "account_id": clean_id(g("account_id")), "account_name": g("account_name") or None,
            "bid": num(g("bid")), "target_state": g("target_state") or None, "target_kind": g("target_kind") or None,
            "impressions": num(g("impressions")), "clicks": num(g("clicks")), "spend": num(g("spend")), "orders": num(g("orders")), "sales": num(g("sales")),
            "units": num(g("units")),
            "raw": {h["header"]: (r[h["index"]].strip() if h["index"] < len(r) else "") for h in cols},
        }
        if any(row[k] is None for k in ("clicks", "spend", "orders", "sales")):
            bad += 1
            warnings.append(f"riga {n}: metriche mancanti (clic/costo/acquisti/vendite): riga scartata")
            continue
        currencies.add(g("currency"))
        rows.append(row)
    if not rows:
        raise GlobalReportError("Nessuna riga valida nel file")
    if bad and not any("scartata" in w for w in warnings):
        warnings.append(f"{bad} righe senza ID campagna scartate")
    warnings = warnings[:25]
    # chiavi duplicate (stessa riga ripetuta): segnalate, NON sommate
    keys = {}
    dups = 0
    for r_ in rows:
        k = (r_["campaign_id"], r_["ad_group_id"], r_["target_id"], r_["search_term"], r_["row_from"], r_["row_to"])
        dups += k in keys
        keys[k] = True
    if dups:
        warnings.append(f"{dups} righe con la stessa chiave (campagna, ad group, target, termine, intervallo): conservate, non sommate")
    cur = sorted(c for c in currencies if c)
    # ACCOUNT ADS: ogni report Amazon appartiene a UN solo account (ID account inserzionista). L'account e' parte dell'identita' operativa: lo si espone qui per
    # anteprima e import; piu' account nello stesso file e' un errore (decisione presa in globalstore), nessun account e' un file non attribuibile.
    acc_ids = sorted({r_["account_id"] for r_ in rows if r_["account_id"]})
    acc_names = {}
    for r_ in rows:
        if r_["account_id"] and r_["account_name"]:
            acc_names.setdefault(r_["account_id"], r_["account_name"])
    rows_without_account = sum(1 for r_ in rows if not r_["account_id"])
    ends = [r_["row_to"] for r_ in rows if r_["row_to"]]
    starts = [r_["row_from"] for r_ in rows if r_["row_from"]]
    return {"file_name": str(name), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "report_type": rtype,
            "currency": cur[0] if len(cur) == 1 else None, "currencies": cur, "marketplace_hint": CURRENCY_MARKETPLACE.get(cur[0]) if len(cur) == 1 else None,
            "account_ids": acc_ids, "account_id": acc_ids[0] if len(acc_ids) == 1 else None, "account_names": acc_names,
            "account_name": acc_names.get(acc_ids[0]) if len(acc_ids) == 1 else None, "rows_without_account": rows_without_account,
            "rows": rows, "columns": cols, "unmapped": [c["header"] for c in cols if not c["canonical"]], "row_count": len(rows),
            "date_min": min(starts).isoformat() if starts else None, "date_max": max(ends).isoformat() if ends else None, "range_counts": range_counts,
            "empty_search_term_rows": sum(1 for r_ in rows if r_["empty_search_term"]), "warnings": warnings, "parser_version": PARSER_VERSION}


def campaign_summary(rows: list) -> dict:
    """{campaign_id: {name, portfolio_id, portfolio_name, ad_groups[], first, last, impressions, clicks, spend, orders, sales, matches{}, rows}} — per preview e coda associazioni."""
    out = {}
    for r in rows:
        c = out.setdefault(r["campaign_id"], {"campaign_id": r["campaign_id"], "name": r["campaign_name"], "portfolio_id": r["portfolio_id"], "portfolio_name": r["portfolio_name"],
                                              "ad_groups": set(), "first": None, "last": None, "impressions": 0.0, "clicks": 0.0, "spend": 0.0, "orders": 0.0, "sales": 0.0,
                                              "matches": {}, "rows": 0, "targets": set(), "account_id": r.get("account_id"), "account_name": r.get("account_name")})
        if r["ad_group_name"]:
            c["ad_groups"].add(r["ad_group_name"])
        if r["targeting"]:
            c["targets"].add(r["targeting"])
        for f in ("impressions", "clicks", "spend", "orders", "sales"):
            c[f] += r[f] or 0
        if r["row_from"] and (c["first"] is None or r["row_from"] < c["first"]):
            c["first"] = r["row_from"]
        if r["row_to"] and (c["last"] is None or r["row_to"] > c["last"]):
            c["last"] = r["row_to"]
        k = (r["match_type_raw"] or "").upper()
        c["matches"][k] = c["matches"].get(k, 0) + 1
        c["rows"] += 1
    for c in out.values():
        c["ad_groups"], c["targets"] = sorted(c["ad_groups"]), sorted(c["targets"])
        c["first"] = c["first"].isoformat() if c["first"] else None
        c["last"] = c["last"].isoformat() if c["last"] else None
        c["campaign_type"] = detect_campaign_type(c["matches"], c["targets"])
    return out


def detect_campaign_type(matches: dict, targets=()) -> str:
    """Tipo campagna dal tipo di corrispondenza dei target: PREDEFINED (close/loose/complements/substitutes) = AUTO ('OTHER'), asin= = COMPETITOR, PHRASE, EXACT.
    E' un SUGGERIMENTO modificabile all'associazione; prevale la maggioranza, a pari merito PHRASE > EXACT."""
    n = lambda *k: sum(matches.get(x, 0) for x in k)                                  # noqa: E731
    auto, expr, phrase, exact = n("TARGETING_EXPRESSION_PREDEFINED"), n("TARGETING_EXPRESSION"), n("PHRASE", "BROAD"), n("EXACT")
    if auto and auto >= max(expr, phrase, exact):
        return "OTHER"
    if expr and expr > max(phrase, exact):
        return "COMPETITOR"
    return "EXACT" if exact > phrase else "PHRASE"


def to_json(rows) -> str:
    return json.dumps(rows, ensure_ascii=False, default=str)
