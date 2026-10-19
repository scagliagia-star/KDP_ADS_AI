"""SNAPSHOT DETERMINISTICO per i test sui libri reali.

I test di regressione sui libri reali (National Parks, Rare Coins, Dry Mix, Calisthenics, RV Camping, Route 66...) NON devono dipendere dallo stato operativo del DB: import
globali, nuove campagne associate, nuovi run, economie cambiate in books.json... mutano il DB reale nel normale uso e rompevano i valori attesi.

`snapshot()` costruisce UNA volta per processo (in una cartella temporanea) una COPIA CONTROLLATA del DB reale, aperta in SOLA LETTURA (API di backup sqlite), riportata allo stato
STORICO (legacy):
  - rimossi i dati arrivati dai report globali (righe con ID campagna Amazon, import GLOBAL:, mappa campagne, tabelle global_report_*, campagne create dall'associazione);
  - rimossi i run di analisi/decisioni creati dopo il primo import globale (la lista dei run torna quella storica);
  - economia dei libri CONGELATA su costanti (FROZEN) sia nelle tabelle books/book_marketplaces sia in un books.json temporaneo per le pipeline (`settings()`).
Il DB reale e CONFIG/books.json non vengono mai scritti. Se il DB reale manca, `snapshot()` restituisce un percorso inesistente e i test relativi si saltano.
Restano dipendenti dal DB reale SOLO i dati storici dei libri (immutabili con l'uso normale: i nuovi import non li toccano)."""
import atexit
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "SCRIPTS"))
REAL = ROOT / "DATABASE" / "kdp_ads.db"

# economia congelata (marketplace, valuta, prezzo, royalty, ACOS obiettivo, fase): i valori con cui sono stati scritti i test
FROZEN = {
    "1805565796": ("Stronger Bones After 50", [("US", "USD", 19.99, 7.76, 35.0, "PROFIT_STABLE")]),
    "1805566547": ("Customizer Engraving Pen", [("US", "USD", 29.99, 11.88, 35.0, "PROFIT_STABLE")]),
    "1805566555": ("Crochet Flowers", [("US", "USD", 16.99, 7.34, 35.0, "PROFIT_STABLE")]),
    "1805566563": ("Chair Tai Chi for Weight Loss After 50", [("US", "USD", 19.99, 8.55, 35.0, "PROFIT_STABLE"), ("UK", "GBP", 14.77, 6.0, 35.0, "PROFIT_STABLE")]),
    "1805566598": ("Dry Mix Pantry Cookbook for Beginners", [("US", "USD", 17.99, 7.5, 35.0, "PROFIT_STABLE")]),
    "B0GNZFLXCV": ("Calisthenics for Beginners", [("US", "USD", 19.99, 8.0, 35.0, "PROFIT_STABLE")]),
    "B0GQFR3M1K": ("Military Workout for Beginners", [("US", "USD", 19.99, 9.0, 35.0, "PROFIT_STABLE")]),
    "B0GRP7LZ9J": ("Military Calisthenics for Women", [("US", "USD", 22.99, 9.0, 35.0, "PROFIT_STABLE")]),
    "B0GSVGRV56": ("WHY YOUR GLUTES WON'T GROW", [("US", "USD", 19.99, 8.0, 35.0, "PROFIT_STABLE")]),
    "B0GY42H3W4": ("Rare Coins Book for Beginners", [("US", "USD", 23.99, 11.0, 35.0, "PROFIT_STABLE")]),
    "B0GZNG6N1D": ("Route 66 Day by Day", [("US", "USD", 22.99, 9.0, 35.0, "PROFIT_STABLE")]),
    "B0GZNKDLGM": ("RV Camping Across America for Beginners", [("US", "USD", 20.99, 8.0, 35.0, "PROFIT_STABLE")]),
    "B0H7P3C4D7": ("NATIONAL PARKS ROAD TRIP FOR BEGINNERS", [("US", "USD", 19.99, 9.0, 35.0, "PROFIT_STABLE")]),
}
_STATE = {}


def _tables(c) -> set:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, t) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}


def _sanitize(path: Path) -> None:
    c = sqlite3.connect(path)
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        tabs = _tables(c)
        first = c.execute("SELECT MIN(imported_at) FROM global_report_imports").fetchone()[0] if "global_report_imports" in tabs else None
        created = []
        if "campaign_book_mapping" in tabs and "backfill_of" in _cols(c, "campaign_book_mapping"):
            created = [(r[0], r[1]) for r in c.execute("SELECT book_id, campaign_id FROM campaign_book_mapping WHERE status='ASSIGNED' AND backfill_of IS NULL AND campaign_id IS NOT NULL")]
        elif "campaign_book_mapping" in tabs:
            created = [(r[0], r[1]) for r in c.execute("SELECT book_id, campaign_id FROM campaign_book_mapping WHERE status='ASSIGNED' AND campaign_id IS NOT NULL")]
        for t in ("target_rows", "search_term_rows"):
            if "amazon_campaign_id" in _cols(c, t):
                c.execute(f"DELETE FROM {t} WHERE amazon_campaign_id IS NOT NULL")
        c.execute("DELETE FROM imports WHERE file_path LIKE 'GLOBAL:%'")
        for b, cid in created:
            c.execute("DELETE FROM campaigns WHERE book_id=? AND campaign_id=?", (b, cid))
        if "amazon_account_id" in _cols(c, "campaigns"):
            c.execute("UPDATE campaigns SET amazon_account_id=NULL")
        for t in ("global_report_rows", "global_report_imports", "campaign_book_mapping", "campaign_book_mapping_v6_old"):
            if t in tabs:
                c.execute(f"DELETE FROM {t}")
        if first:                                              # run creati dopo il primo import globale: via (la lista dei run torna quella storica)
            drs = [r[0] for r in c.execute("SELECT run_id FROM decision_runs WHERE generated_at >= ?", (first,))]
            ars = [r[0] for r in c.execute("SELECT run_id FROM analysis_runs WHERE generated_at >= ?", (first,))]
            for rid in drs:
                c.execute("DELETE FROM decisions WHERE run_id=?", (rid,))
                c.execute("DELETE FROM decision_runs WHERE run_id=?", (rid,))
            for rid in ars:
                c.execute("DELETE FROM recommendations WHERE run_id=?", (rid,))
                c.execute("DELETE FROM analysis_runs WHERE run_id=?", (rid,))
        for book, (_title, mps) in FROZEN.items():              # economia congelata (stessa formula del registro: pareggio = royalty / prezzo x 100)
            for mp, cur, price, roy, tgt, phase in mps:
                be = round(roy / price * 100, 2)
                for t in ("books", "book_marketplaces"):
                    if t in tabs:
                        c.execute(f"UPDATE {t} SET price=?, royalty_per_copy=?, target_acos=?, break_even_acos=?, break_even_acos_calculated=?, phase=?, currency=? "
                                  "WHERE book_id=? AND marketplace=?", (price, roy, tgt, be, be, phase, cur, book, mp))
        c.commit()
        c.execute("VACUUM")
    finally:
        c.close()


def snapshot() -> Path:
    """Percorso del DB di snapshot (costruito una volta per processo). Se il DB reale manca: percorso inesistente."""
    if "snap" in _STATE:
        return _STATE["snap"]
    tmp = Path(tempfile.mkdtemp(prefix="kdp_realdata_"))
    atexit.register(shutil.rmtree, str(tmp), True)
    _STATE["tmp"] = tmp
    path = tmp / "snapshot.db"
    if not REAL.is_file():
        _STATE["snap"] = tmp / "missing.db"
        return _STATE["snap"]
    _STATE["real_hash_before"] = hashlib.sha256(REAL.read_bytes()).hexdigest()
    src = sqlite3.connect(f"{REAL.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _sanitize(path)
    _STATE["snap"] = path
    _STATE["real_hash_after"] = hashlib.sha256(REAL.read_bytes()).hexdigest()
    return path


def real_hashes() -> tuple:
    snapshot()
    return _STATE.get("real_hash_before"), _STATE.get("real_hash_after")


def books_json() -> Path:
    """books.json TEMPORANEO con l'economia congelata (mai CONFIG/books.json)."""
    snapshot()
    if "books" not in _STATE:
        data = {"books": [{"book_id": b, "title": title, "active": True,
                           "marketplaces": [{"marketplace": mp, "currency": cur, "price": price, "royalty_per_copy": roy, "target_acos": tgt, "phase": phase, "active": True}
                                            for mp, cur, price, roy, tgt, phase in mps]} for b, (title, mps) in FROZEN.items()]}
        p = _STATE["tmp"] / "books.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _STATE["books"] = p
    return _STATE["books"]


def settings() -> dict:
    """settings.json del progetto con books_path sul books.json temporaneo (le pipeline di analisi/decisioni leggono l'economia da qui)."""
    from kdp_ads.config import load_settings
    return {**load_settings(), "books_path": str(books_json())}
