import logging
from datetime import datetime
from pathlib import Path


def setup_logger(logs_dir: str | Path, name: str) -> tuple[logging.Logger, Path]:
    """Un file di log nuovo per ogni esecuzione (timestamp nel nome, mai sovrascritto)."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"{name}_{stamp}.log"
    n = 1
    while path.exists():
        path = logs_dir / f"{name}_{stamp}_{n}.log"
        n += 1
    logger = logging.getLogger(f"kdp_ads.{name}.{stamp}.{n}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger, path
