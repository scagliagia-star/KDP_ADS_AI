"""Backup del DB locale prima delle operazioni che lo modificano. Mai sovrascrive un backup precedente."""
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_db(ws, reason: str) -> Path | None:
    """Copia consistente (API di backup di sqlite) in DATABASE/backups/kdp_ads_<data_ora>_<motivo>.db. None se il DB non esiste."""
    src = ws.db_path
    if not src.is_file():
        return None
    ws.backups_dir.mkdir(parents=True, exist_ok=True)
    tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)[:40] or "backup"
    base = f"kdp_ads_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}"
    dest, n = ws.backups_dir / f"{base}.db", 1
    while dest.exists():
        n += 1
        dest = ws.backups_dir / f"{base}_{n}.db"
    s = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dest))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return dest
