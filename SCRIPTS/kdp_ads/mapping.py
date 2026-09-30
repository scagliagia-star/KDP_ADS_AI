"""Interpretazione header CSV Amazon Ads (EN + IT). Solo alias espliciti da CONFIG/column_mapping.json: niente fuzzy matching."""
import re
from dataclasses import dataclass, field


def norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def dedupe_headers(headers: list[str]) -> tuple[list[str], list[str]]:
    """Rinomina le occorrenze successive di un header duplicato: 'Stato' -> 'Stato', 'Stato__2', 'Stato__3'.
    Ritorna (nuovi_header, basi_duplicate)."""
    seen: dict[str, int] = {}
    out, dups = [], []
    for h in headers:
        key = h.strip()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 1:
            out.append(key)
        else:
            out.append(f"{key}__{seen[key]}")
            if key not in dups:
                dups.append(key)
    return out, dups


@dataclass
class MappingResult:
    report_type: str | None = None
    field_to_header: dict = field(default_factory=dict)   # canonico -> header
    unmapped: list = field(default_factory=list)          # sconosciute -> WARNING
    informational: list = field(default_factory=list)     # note, non usate nei calcoli
    duplicate_headers: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)         # conflitti bloccanti
    missing_required: list = field(default_factory=list)
    attribution_window: str | None = None

    @property
    def ok(self) -> bool:
        return not (self.ambiguous or self.missing_required or self.report_type is None)


def analyze_headers(headers: list[str], mapping: dict, duplicate_bases: list[str] | None = None) -> MappingResult:
    """`headers` gia' deduplicati con dedupe_headers()."""
    res = MappingResult(duplicate_headers=list(duplicate_bases or []))
    alias_index: dict[str, list[str]] = {}
    for canon, aliases in mapping["fields"].items():
        for a in aliases:
            lst = alias_index.setdefault(norm(a), [])
            if canon not in lst:
                lst.append(canon)
    info = {norm(h) for h in mapping.get("informational_columns", {}).get("headers", [])}

    # Un duplicato e' innocuo solo se la colonna NON e' mappata a un campo usato nei calcoli.
    for base in res.duplicate_headers:
        if norm(base) in alias_index:
            res.ambiguous.append(f"header duplicato '{base}' mappato a un campo di calcolo {alias_index[norm(base)]}: "
                                 "impossibile scegliere quale colonna usare")

    for h in headers:
        n = norm(h)
        if not n:
            res.ambiguous.append(f"header vuoto/non leggibile: {h!r}")
            continue
        canons = alias_index.get(n)
        if not canons:
            (res.informational if n in info else res.unmapped).append(h)
            continue
        if len(canons) > 1:
            res.ambiguous.append(f"{h!r} corrisponde a piu' campi: {canons}")
            continue
        c = canons[0]
        if c in res.field_to_header:
            res.ambiguous.append(f"campo '{c}' coperto da due colonne: {res.field_to_header[c]!r} e {h!r}")
            continue
        res.field_to_header[c] = h

    windows = {m.group(1) for h in res.field_to_header.values()
               if (m := re.match(r"\s*(\d+)\s*day", h, re.I))}
    if len(windows) > 1:
        res.ambiguous.append(f"finestre di attribuzione miste nello stesso file: {sorted(windows)}")
    elif windows:
        res.attribution_window = f"{windows.pop()} Day"

    # Precedenza al Search Term: 'Parole chiave' (target) compare anche nei Search Term.
    if "search_term" in res.field_to_header:
        res.report_type = "SEARCH_TERM"
    elif "target" in res.field_to_header:
        res.report_type = "TARGETING"
    else:
        res.ambiguous.append("tipo report non determinabile: nessuna colonna search term / target riconosciuta")

    if res.report_type:
        res.missing_required = [f for f in mapping["required"][res.report_type]
                                if f not in res.field_to_header]
    return res
