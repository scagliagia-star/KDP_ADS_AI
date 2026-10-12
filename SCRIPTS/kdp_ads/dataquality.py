"""Data-quality dei report Search Term: righe con metriche IMPOSSIBILI (nessun clic ma spesa o acquisti > 0). Funzioni pure: non modificano mai i dati
originali (le righe restano nel DB), le escludono solo dal calcolo di raccomandazioni/opportunita'/negative/conversion rate e producono un avviso dati."""
from collections import defaultdict

IMPOSSIBLE_SEARCH_TERM_METRICS = "IMPOSSIBLE_SEARCH_TERM_METRICS"


def is_impossible_search_term(row) -> bool:
    """clicks == 0 AND (spend > 0 OR orders > 0): un acquisto o una spesa senza clic non e' coerente per decisioni operative."""
    return not (row.get("clicks") or 0) and ((row.get("spend") or 0) > 0 or (row.get("orders") or 0) > 0)


def split_search_terms(rows):
    """(righe valide, righe impossibili). L'ordine e il contenuto delle righe valide non cambiano."""
    good, bad = [], []
    for r in rows:
        (bad if is_impossible_search_term(r) else good).append(r)
    return good, bad


def impossible_warnings(bad_rows) -> list:
    """Un avviso per (campagna, periodo) con il numero di righe problematiche."""
    groups = defaultdict(int)
    for r in bad_rows:
        period = r.get("period_id") or f"{r.get('date_from') or 'inizio'}__{r.get('date_to') or 'n/d'}"
        groups[(r.get("campaign_id"), period)] += 1
    return [f"DATA_WARNING {IMPOSSIBLE_SEARCH_TERM_METRICS} {cid} periodo {period}: {n} righe Search Term con 0 clic ma spesa o acquisti > 0 (dato incoerente): "
            "escluse da opportunita' EXACT/FRASE, negative e conversion rate; i dati originali non sono modificati"
            for (cid, period), n in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]
