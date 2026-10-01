"""Percorsi comuni della dashboard. Importato per primo da app.py e da ogni pagina. Nessuna scrittura, nessuna rete."""
import json
import os
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
ROOT = DASHBOARD_DIR.parent
SCRIPTS_DIR = ROOT / "SCRIPTS"
for _p in (str(DASHBOARD_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def db_path() -> Path:
    """DATABASE\\kdp_ads.db (da CONFIG/settings.json). KDP_ADS_DB sovrascrive il percorso, solo per test/diagnosi."""
    env = os.environ.get("KDP_ADS_DB")
    if env:
        return Path(env)
    try:
        with open(ROOT / "CONFIG" / "settings.json", encoding="utf-8") as f:
            return Path(json.load(f)["db_path"])
    except (OSError, ValueError, KeyError):
        return ROOT / "DATABASE" / "kdp_ads.db"
