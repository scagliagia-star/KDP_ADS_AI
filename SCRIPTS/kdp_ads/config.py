import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "CONFIG"


def load_settings(path: Path | None = None) -> dict:
    """Solo percorsi e soglie tecniche. I parametri dei libri stanno in CONFIG/books.json."""
    with open(path or CONFIG_DIR / "settings.json", encoding="utf-8") as f:
        settings = json.load(f)
    if not settings.get("read_only", False):
        raise RuntimeError("V1 e' READ-ONLY: settings.read_only deve essere true.")
    return settings


def load_mapping(path: Path | None = None) -> dict:
    with open(path or CONFIG_DIR / "column_mapping.json", encoding="utf-8") as f:
        return json.load(f)
