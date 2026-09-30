"""Registra A MANO un suggerimento Amazon (es. Campaign Overview Beta). Nessun browser, nessuna rete.
Uso interattivo:  python record_amazon_recommendation.py
Uso da file:      python record_amazon_recommendation.py --from-json CONFIG\\amazon_observations\\<file>.json
(il file contiene una lista di record con gli stessi campi delle domande interattive)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kdp_ads import amazon
from kdp_ads.config import load_settings
from kdp_ads.db import connect
from kdp_ads.logs import setup_logger


def ask(label, default=None):
    raw = input(f"{label}" + (f" [{default}]" if default not in (None, "") else "") + ": ").strip()
    return raw if raw else (default if default is not None else "")


def interactive() -> dict:
    print("=== Nuovo suggerimento Amazon (inserimento manuale) ===")
    return {
        "book_id": ask("book_id"),
        "campaign_id": ask("campaign_id"),
        "period_from": ask("period_from (YYYY-MM-DD)"),
        "period_to": ask("period_to (YYYY-MM-DD)"),
        "source": ask("source", "Amazon Campaign Overview Beta"),
        "recommendation_type": ask("recommendation_type (es. KEYWORD_BID_REDUCTION, TOP_OF_SEARCH_REDUCTION, CAMPAIGN_OBSERVATION)"),
        "target": ask("target (keyword/ASIN; vuoto = livello campagna)") or None,
        "observed_spend": ask("spesa osservata da Amazon (USD, vuoto se non mostrata)") or None,
        "observed_orders": ask("acquisti osservati da Amazon (vuoto se non mostrati)") or None,
        "observation": ask("Amazon observation (testo)"),
        "suggested_action": ask("suggested action (testo, come mostrato da Amazon)"),
        "suggested_direction": ask(f"direzione {amazon.DIRECTIONS}", "UNSPECIFIED").upper(),
        "suggested_pct": ask("percentuale suggerita da Amazon (SOLO se mostrata, altrimenti vuoto)") or None,
        "notes": ask("notes"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", help="file JSON con una lista di record")
    a = ap.parse_args()
    settings = load_settings()
    logger, _ = setup_logger(settings["logs_dir"], "amazon_rec")
    conn = connect(settings["db_path"])
    records = json.loads(Path(a.from_json).read_text(encoding="utf-8-sig")) if a.from_json else [interactive()]
    rc = 0
    for r in records:
        try:
            rid = amazon.record(conn, r, settings)
            logger.info("Registrato suggerimento Amazon id=%s %s/%s %s target=%s", rid, r["book_id"], r["campaign_id"],
                        r["recommendation_type"], r.get("target"))
        except amazon.AmazonRecError as e:
            logger.error("NON registrato (%s/%s %s target=%s): %s", r.get("book_id"), r.get("campaign_id"),
                         r.get("recommendation_type"), r.get("target"), e)
            rc = 1
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
