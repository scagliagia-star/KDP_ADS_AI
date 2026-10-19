"""FILTRO DI INTENTO PER LE PROMOZIONI DEI SEARCH TERM. Funzioni PURE: nessuna rete, nessuna scrittura, nessuna Amazon.

Distingue PERFORMANCE BUONA da PERTINENZA SUFFICIENTE PER PROMOZIONE: una query puo' aver convertito (ACOS ottimo) ma indicare un intento d'acquisto diverso dal contenuto di un
libro/manuale/guida (es. «route 66 shirt»: abbigliamento). Una query FUORI INTENTO non viene promossa (ne' AUTO -> FRASE, ne' AUTO/FRASE -> EXACT, ne' nei suggerimenti di crescita) ma
NON diventa una negativa per questo motivo: resta un search term performante «FUORI INTENTO PER PROMOZIONE», con un motivo chiaro. La decisione sulla negativa resta quella delle
regole sui risultati (spreco), indipendente dall'intento.

Come funziona (prudente e spiegabile):
  - la query e' normalizzata in TOKEN (minuscolo, solo lettere/cifre; «t-shirt» -> «t», «shirt») e confrontata con liste di segnali per CATEGORIA (abbigliamento, merchandising /
    arredo, altri oggetti fisici) per token interi o sequenze di token: mai sottostringhe («shirtwaist» o «pinterest» non scattano). Il plurale semplice (-s) e' ricondotto al singolare;
  - un segnale bloccato RESTA bloccato anche se la query contiene «book», «guide», «journal»...: «route 66 sticker book» e' un libro di adesivi, non il nostro libro. Lo sblocca solo
    una ECCEZIONE esplicita. (Esiste ancora l'interruttore `book_intent_wins`, SPENTO di default, per chi volesse il comportamento permissivo);
  - ECCEZIONI estensibili: `allow` globale e per libro (`rules.json -> intent.allow` e `intent.book_allow[<book_id>]`, oppure `rule_overrides.intent.allow` in books.json): un libro su
    t-shirt design puo' sbloccare «shirt»; `extra_blocked` aggiunge segnali propri."""
import re

BLOCKED = {
    "APPAREL": ["shirt", "tshirt", "tee", "hoodie", "sweatshirt", "hat", "cap", "jersey", "clothing", "apparel"],
    "MERCH_DECOR": ["sticker", "decal", "poster", "wall art", "sign", "mug", "tumbler", "ornament", "keychain", "patch", "pin", "magnet"],
    "OTHER_OBJECTS": ["toy", "plush", "costume", "necklace", "bracelet", "earring", "phone case"],
}
CATEGORY_LABELS = {"APPAREL": "abbigliamento", "MERCH_DECOR": "merchandising / arredo", "OTHER_OBJECTS": "oggetto fisico", "CUSTOM": "segnale personalizzato"}
# segnali di intento LIBRO: usati SOLO se `book_intent_wins` e' acceso (default: spento, un segnale bloccato non e' annullato da «book»)
BOOK_INTENT = frozenset({"book", "ebook", "guide", "guidebook", "handbook", "manual", "workbook", "journal", "planner", "logbook", "cookbook", "paperback", "hardcover", "audiobook"})
REASON = "Ha convertito, ma la query indica un intento di acquisto diverso dal contenuto del libro."
LABEL = "FUORI INTENTO PER PROMOZIONE"


def _singular(tok: str) -> str:
    if len(tok) > 4 and tok.endswith(("ches", "shes", "xes")):          # patches -> patch
        return tok[:-2]
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def tokens(text) -> list:
    """Token normalizzati: minuscolo, solo [a-z0-9] (le altre lettere restano), plurale semplice ricondotto al singolare."""
    return [_singular(t) for t in re.findall(r"[^\W_]+", str(text or "").lower())]


def _seq(entry) -> tuple:
    return tuple(tokens(entry))


def _contains(seq_tokens: list, entry: tuple) -> bool:
    n = len(entry)
    return n > 0 and any(tuple(seq_tokens[i:i + n]) == entry for i in range(len(seq_tokens) - n + 1))


def config(rules: dict | None, book_id=None) -> dict:
    """{enabled, allow, extra_blocked, book_intent_wins} dalle regole (globali + per libro)."""
    c = (rules or {}).get("intent") or {}
    allow = list(c.get("allow") or []) + list((c.get("book_allow") or {}).get(str(book_id), []) if book_id is not None else [])
    return {"enabled": c.get("enabled", True), "allow": allow, "extra_blocked": list(c.get("extra_blocked") or []), "book_intent_wins": c.get("book_intent_wins", False)}


def check(term, allow=(), extra_blocked=(), book_intent_wins=False, enabled=True) -> dict | None:
    """None se la query e' compatibile con un libro; altrimenti {category, matched, reason}. Una query vuota non e' mai bloccata."""
    if not enabled:
        return None
    toks = tokens(term)
    if not toks:
        return None
    if book_intent_wins and BOOK_INTENT & set(toks):
        return None
    allowed = [_seq(a) for a in allow if _seq(a)]
    hits = []
    for cat, entries in list(BLOCKED.items()) + [("CUSTOM", list(extra_blocked))]:
        for e in entries:
            seq = _seq(e)
            if not seq or not _contains(toks, seq):
                continue
            if any(set(seq) & set(a) for a in allowed):          # eccezione esplicita (libro/nicchia): sblocca il segnale
                continue
            hits.append((cat, " ".join(seq)))
    if not hits:
        return None
    cat, matched = hits[0]
    return {"category": cat, "matched": matched, "reason": REASON, "detail": f"segnale «{matched}» ({CATEGORY_LABELS[cat]})"}
