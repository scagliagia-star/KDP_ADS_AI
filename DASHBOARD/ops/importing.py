"""Importazione guidata di un report: pianificazione upload, periodo/snapshot, DRY-RUN obbligatorio, import reale.
Riusa kdp_ads.importer.import_all (la stessa logica di import_csv.py) e kdp_ads.registry.add_snapshot (add_snapshot.py).
Il dry-run gira su una COPIA TEMPORANEA della cartella campagna: nessun effetto su INPUT, metadata o DB prima dell'import."""
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from kdp_ads import registry
from kdp_ads.db import connect
from kdp_ads.importer import import_all
from kdp_ads.logs import setup_logger
from kdp_ads.snapshots import period_id, snapshot_id

from . import ValidationError
from .backup import backup_db

REPORT_TYPES = {"TARGETING": "Target", "SEARCH_TERM": "Search Term"}
PERIOD_KINDS = {"LIFETIME": "Storico completo (LIFETIME)", "D65": "Ultimi 65 giorni", "D30": "Ultimi 30 giorni", "D7": "Ultimi 7 giorni (solo trend)",
                "CUSTOM": "Intervallo personalizzato"}
LEGACY_KINDS = {"D28": "Ultimi 28 giorni (storico, non piu' proposto)"}      # solo compatibilita': snapshot 28D gia' esistenti
_SAFE = re.compile(r"[^A-Za-z0-9._() \-]")


def close_logger(logger):
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ periodo
@dataclass
class Period:
    scope: str
    date_from: str | None
    date_to: str | None


def _d(v, label) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise ValidationError(f"{label}: data non valida")


def resolve_period(kind, date_from=None, date_to=None) -> Period:
    """LIFETIME: serve la data di esportazione (date_to). 65/30 giorni (28 solo legacy): date_from = date_to - (n-1) (estremi inclusi).
    Personalizzato: servono entrambe le date."""
    if kind not in PERIOD_KINDS and kind not in LEGACY_KINDS:
        raise ValidationError("Tipo di periodo non valido")
    if date_to in (None, ""):
        raise ValidationError("Data di fine: campo obbligatorio")
    to = _d(date_to, "Data di fine")
    if kind == "LIFETIME":
        return Period("LIFETIME", None, to.isoformat())
    if kind in ("D65", "D30", "D28", "D7"):
        n = {"D65": 65, "D30": 30, "D28": 28, "D7": 7}[kind]
        return Period("DATE_RANGE", (to - timedelta(days=n - 1)).isoformat(), to.isoformat())
    if date_from in (None, ""):
        raise ValidationError("Data di inizio: campo obbligatorio")
    frm = _d(date_from, "Data di inizio")
    if frm > to:
        raise ValidationError("La data di inizio non può essere successiva alla data di fine")
    return Period("DATE_RANGE", frm.isoformat(), to.isoformat())


def build_entry(file_name, report_type, period: Period, attribution_window=None) -> dict:
    if report_type not in REPORT_TYPES:
        raise ValidationError("Tipo di report non valido")
    e = {"file": file_name, "report_type": report_type, "report_scope": period.scope,
         "date_from": period.date_from, "date_to": period.date_to}
    if attribution_window and str(attribution_window).strip():
        e["attribution_window"] = str(attribution_window).strip()
    return e


def campaign_folder(ws, book_id, campaign_id) -> Path | None:
    """Cartella della campagna (struttura legacy o per marketplace)."""
    return registry.find_campaign_dir(ws.input_dir, book_id, campaign_id)


def campaign_marketplace(ws, book_id, campaign_id) -> str | None:
    folder = campaign_folder(ws, book_id, campaign_id)
    meta, _ = registry.load_metadata(folder) if folder else (None, [])
    return str(meta["marketplace"]).strip().upper() if meta and meta.get("marketplace") else None


def preview(book_id, campaign_id, file_name, entry, marketplace=None) -> dict:
    p = period_id(entry["report_scope"], entry["date_from"], entry["date_to"])
    head = {"Libro": book_id}
    if marketplace:
        head["Marketplace"] = marketplace
    return {**head, "Campagna": campaign_id, "Tipo report": REPORT_TYPES[entry["report_type"]],
            "Periodo": (f"{entry['date_from']} → {entry['date_to']}" if entry["report_scope"] == "DATE_RANGE"
                        else f"Storico completo fino al {entry['date_to']}"),
            "File": file_name,
            "Snapshot ID previsto": snapshot_id(book_id, campaign_id, entry["report_type"], entry["report_scope"],
                                                entry["date_from"], entry["date_to"]) + f"  ({p})"}


# ------------------------------------------------------------------ file caricato
def safe_filename(name) -> str:
    base = Path(str(name or "").replace("\\", "/")).name
    base = _SAFE.sub("_", base).strip(" .")
    if not base.lower().endswith(".csv") or len(base) <= 4:
        raise ValidationError("Il file deve essere un CSV (estensione .csv)")
    return base if len(base) <= 120 else base[:116] + ".csv"


@dataclass
class UploadPlan:
    final_name: str
    status: str                  # NEW / REUSE_IDENTICAL / RENAMED
    note: str = ""
    sha: str = ""


def plan_upload(campaign_dir, original_name, data: bytes, period: "Period | None" = None) -> UploadPlan:
    """Nome definitivo del file nella cartella campagna. Non sovrascrive MAI: se esiste un file diverso con lo stesso nome
    ne genera uno sicuro (nome__2.csv ...). Un file con lo stesso contenuto (anche con altro nome, es. "(1)") viene riusato, purche'
    non sia gia' dichiarato con un periodo DIVERSO da quello richiesto: lo stesso contenuto con un altro periodo e' un altro snapshot e
    ottiene un proprio nome (la dichiarazione del file esistente non va mai riscritta). Cartella _originali e staging non partecipano."""
    if not data:
        raise ValidationError("Il file è vuoto")
    folder = Path(campaign_dir)
    name, digest = safe_filename(original_name), sha256(data)
    meta, _ = registry.load_metadata(folder)
    declared = {s["file"]: (s.get("report_scope"), s.get("date_from"), s.get("date_to")) for s in (meta or {}).get("snapshots") or []}
    want = (period.scope, period.date_from, period.date_to) if period else None

    def reusable(fname):
        d = declared.get(fname)
        return d is None or (want is not None and tuple(d) == want)
    stem, ext = name[:-4], name[-4:]
    if folder.is_dir():
        for other in sorted(folder.glob("*.csv")):     # stesso contenuto con altro nome: e' lo stesso report
            if other.name != name and sha256(other.read_bytes()) == digest and reusable(other.name):
                return UploadPlan(other.name, "REUSE_IDENTICAL", f"Stesso contenuto di {other.name}, già presente nella cartella campagna: "
                                  "viene riusato", digest)
    cand, i = name, 1
    while True:
        f = folder / cand
        if not f.exists():
            if cand == name:
                return UploadPlan(cand, "NEW", sha=digest)
            return UploadPlan(cand, "RENAMED", f"Esisteva già un file con lo stesso nome (contenuto o periodo diverso): salvato come {cand}", digest)
        if sha256(f.read_bytes()) == digest and reusable(cand):
            return UploadPlan(cand, "REUSE_IDENTICAL", "Esiste già un file identico nella cartella campagna: viene riusato", digest)
        i += 1
        cand = f"{stem}__{i}{ext}"


# ------------------------------------------------------------------ dry-run
@dataclass
class DryRun:
    status: str                  # DRY_RUN_OK / SKIPPED_DUPLICATE / SNAPSHOT_CONFLICT / ERROR
    ok: bool
    rows: int = 0
    marketplace: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    report_type: str | None = None
    period_id: str | None = None
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unmapped: list = field(default_factory=list)


def _classify(r) -> str:
    if r.status == "DRY_RUN_OK":
        return "DRY_RUN_OK"
    if r.status == "SKIPPED_DUPLICATE":
        return "SKIPPED_DUPLICATE"
    if r.status == "REJECTED" and any("SNAPSHOT_CONFLICT" in p for p in r.problems):
        return "SNAPSHOT_CONFLICT"
    return "ERROR"


def _result_to_dry(r) -> DryRun:
    st = _classify(r)
    return DryRun(st, st == "DRY_RUN_OK", r.rows, None, r.date_from, r.date_to, r.report_type, r.period_id, list(r.problems),
                  list(r.warnings), list(r.unmapped))


def dry_run(ws, book_id, campaign_id, final_name, data: bytes, entry: dict) -> DryRun:
    """Esegue import_all(dry_run=True) su una copia temporanea (metadata + file). Zero effetti su INPUT e sul DB (righe)."""
    real_dir = campaign_folder(ws, book_id, campaign_id)
    meta, errs = registry.load_metadata(real_dir) if real_dir else (None, ["campagna non trovata"])
    if meta is None or errs:
        return DryRun("ERROR", False, problems=errs or ["metadata.json della campagna non trovato"])
    snaps = [s for s in meta.get("snapshots") or [] if s["file"] != final_name] + [entry]
    new_meta = {**meta, "snapshots": snaps}
    errs = registry.validate_metadata(new_meta)
    if errs:
        return DryRun("ERROR", False, problems=errs)
    logger, _ = setup_logger(ws.logs_dir, "dashboard_dryrun")
    conn = connect(ws.db_path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp) / book_id / campaign_id
            shadow.mkdir(parents=True)
            (shadow / "metadata.json").write_text(json.dumps(new_meta), encoding="utf-8")
            (shadow / final_name).write_bytes(data)
            settings = {**ws.settings, "input_dir": tmp}
            res = import_all(conn, settings, ws.mapping, logger, only_book=book_id, only_campaign=campaign_id,
                             dry_run=True, only_file=final_name)
        if not res:
            return DryRun("ERROR", False, problems=["Nessun risultato dal dry-run"])
        out = _result_to_dry(res[-1])
        out.marketplace = str(meta["marketplace"]).strip().upper()
        return out
    finally:
        conn.close()
        close_logger(logger)


# ------------------------------------------------------------------ import reale
@dataclass
class ImportOutcome:
    status: str                  # IMPORTED / SKIPPED_DUPLICATE / ERROR
    import_id: int | None = None
    rows: int = 0
    period_id: str | None = None
    snapshot_id: str | None = None
    warnings: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    file_path: str | None = None
    original_copy: str | None = None
    backup: str | None = None


def commit_import(ws, book_id, campaign_id, original_name, final_name, data: bytes, entry: dict) -> ImportOutcome:
    """Da chiamare SOLO dopo un dry-run DRY_RUN_OK. Backup DB -> salva file (mai sovrascrive) + copia dell'originale ->
    dichiara lo snapshot -> import reale. Se l'import non riesce, file e dichiarazione vengono annullati."""
    folder = campaign_folder(ws, book_id, campaign_id)
    if folder is None or not (folder / "metadata.json").is_file():
        return ImportOutcome("ERROR", problems=["Campagna non trovata"])
    meta_file = folder / "metadata.json"
    bk = backup_db(ws, "pre_import")
    bk_s = str(bk) if bk else None
    meta_before = meta_file.read_text(encoding="utf-8")
    target = folder / final_name
    wrote = False
    if target.exists():
        if sha256(target.read_bytes()) != sha256(data):
            return ImportOutcome("ERROR", problems=[f"{final_name} esiste già con contenuto diverso: non sovrascrivo"], backup=bk_s)
    else:
        with open(target, "xb") as fh:                     # 'x': fallisce se il file compare nel frattempo
            fh.write(data)
        wrote = True
    orig_dir = folder / "_originali"
    orig_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    orig, n = orig_dir / f"{stamp}__{safe_filename(original_name)}", 1
    while orig.exists():
        n += 1
        orig = orig_dir / f"{stamp}_{n}__{safe_filename(original_name)}"
    orig.write_bytes(data)

    def rollback():
        meta_file.write_text(meta_before, encoding="utf-8")
        if wrote and target.exists():
            target.unlink()
        if orig.exists():
            orig.unlink()
    logger, _ = setup_logger(ws.logs_dir, "dashboard_import")
    conn = connect(ws.db_path)
    try:
        try:
            registry.add_snapshot(folder, entry, overwrite=True)
        except registry.RegistryError as e:
            rollback()
            return ImportOutcome("ERROR", problems=[str(e)], backup=bk_s)
        res = import_all(conn, ws.settings, ws.mapping, logger, only_book=book_id, only_campaign=campaign_id, only_file=final_name)
        r = res[-1] if res else None
        sid = snapshot_id(book_id, campaign_id, entry["report_type"], entry["report_scope"], entry["date_from"], entry["date_to"])
        if r is None or r.status not in ("IMPORTED", "SKIPPED_DUPLICATE"):
            rollback()
            return ImportOutcome("ERROR", problems=list(r.problems) if r else ["Nessun risultato dall'import"], backup=bk_s)
        if r.status == "SKIPPED_DUPLICATE":
            rollback()
        return ImportOutcome(r.status, r.import_id, r.rows, r.period_id, sid, list(r.warnings), [], str(target), str(orig), bk_s)
    finally:
        conn.close()
        close_logger(logger)
