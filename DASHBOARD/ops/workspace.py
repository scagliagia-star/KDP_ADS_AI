"""Percorsi e impostazioni di lavoro. `current()` usa la cartella del progetto; KDP_ADS_ROOT la sposta (solo test/diagnosi)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

import bootstrap
from kdp_ads.config import CONFIG_DIR as REAL_CONFIG_DIR
from kdp_ads.config import load_mapping


@dataclass
class Workspace:
    root: Path
    settings: dict
    rules_path: Path
    mapping: dict

    @property
    def input_dir(self) -> Path:
        return Path(self.settings["input_dir"])

    @property
    def books_path(self) -> Path:
        return Path(self.settings["books_path"])

    @property
    def db_path(self) -> Path:
        return Path(self.settings["db_path"])

    @property
    def logs_dir(self) -> Path:
        return Path(self.settings["logs_dir"])

    @property
    def out_dir(self) -> Path:
        return self.root / "OUTPUT"

    @property
    def backups_dir(self) -> Path:
        return self.db_path.parent / "backups"


def from_root(root) -> Workspace:
    root = Path(root)
    cfg = {}
    f = root / "CONFIG" / "settings.json"
    if f.is_file():
        cfg = json.loads(f.read_text(encoding="utf-8"))
    env_db = os.environ.get("KDP_ADS_DB")
    settings = {
        "read_only": True,        # il progetto resta in sola lettura rispetto ad Amazon
        "search_term_max_days": cfg.get("search_term_max_days", 65),
        "break_even_mismatch_tolerance_pp": cfg.get("break_even_mismatch_tolerance_pp", 0.5),
        "db_path": env_db or str(root / "DATABASE" / "kdp_ads.db"),
        "logs_dir": str(root / "LOGS"),
        "input_dir": str(root / "INPUT"),
        "books_path": str(root / "CONFIG" / "books.json"),
    }
    mf = root / "CONFIG" / "column_mapping.json"
    mapping = load_mapping(mf) if mf.is_file() else load_mapping()
    rf = root / "CONFIG" / "rules.json"
    return Workspace(root, settings, rf if rf.is_file() else REAL_CONFIG_DIR / "rules.json", mapping)


def current() -> Workspace:
    return from_root(os.environ.get("KDP_ADS_ROOT") or bootstrap.ROOT)
