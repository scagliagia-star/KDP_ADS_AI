"""Servizi di SCRITTURA LOCALE della dashboard V2 (libri, campagne, upload, import, analisi, decisioni).
Riusano la logica esistente (kdp_ads.registry / importer / pipeline): nessuna business logic duplicata.
Scrivono SOLO nei file locali del progetto (books.json, INPUT, DB locale, OUTPUT, LOGS, backup DB). Nessuna rete, nessuna Amazon."""
from dataclasses import dataclass, field
from typing import Any


class ValidationError(ValueError):
    """Input dell'utente non valido (messaggio gia' in italiano)."""


@dataclass
class OpResult:
    ok: bool
    errors: list = field(default_factory=list)
    message: str = ""
    data: Any = None
