"""Lancio nuovo libro: servizi di SCRITTURA LOCALE (LAUNCH/<launch_id>.json). Riusano kdp_ads.launch (modello, validazione, archiviazione).
Nessuna rete, nessuna Amazon, nessuna campagna creata."""
from kdp_ads import launch, launch_keywords

from . import OpResult


def launch_dir(ws):
    return ws.root / "LAUNCH"


def list_plans(ws) -> list:
    return launch.list_plans(launch_dir(ws))


def get_plan(ws, launch_id):
    try:
        return launch.load_plan(launch_dir(ws), launch_id)
    except launch.LaunchError:
        return None


def save_form(ws, form: dict, launch_id: str | None = None) -> OpResult:
    """Nuova bozza (launch_id None: id nuovo, mai sovrascrive) oppure aggiornamento esplicito di una bozza esistente."""
    d = launch_dir(ws)
    existing = None
    if launch_id:
        try:
            existing = launch.load_plan(d, launch_id)
        except launch.LaunchError as e:
            return OpResult(False, [str(e)])
    plan, errors, warnings = launch.build_plan(form, existing)
    if errors:
        return OpResult(False, errors, data={"warnings": warnings})
    try:
        if existing:
            plan["launch_id"] = launch_id
            path = launch.save_plan(d, plan, overwrite=True)
        else:
            plan["launch_id"] = launch.new_launch_id(d, plan["book"]["title"], plan["book"]["marketplace"])
            path = launch.save_plan(d, plan)
    except (launch.LaunchError, OSError) as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Bozza di lancio salvata", data={"plan": plan, "warnings": warnings, "path": str(path)})


def generate_keyword_plan(ws, launch_id: str, provider=None) -> OpResult:
    """Genera il piano keyword Ads iniziale dalla bozza e lo salva NELLA bozza (LAUNCH/<id>.json, chiave keyword_plan, scrittura atomica).
    Con un provider esterno (futuro) i competitor VALIDATI vengono arricchiti prima del calcolo; senza provider nessun dato competitor.
    Non tocca books.json, non crea campagne, nessuna rete (il provider predefinito non ne usa)."""
    d = launch_dir(ws)
    try:
        plan = launch.load_plan(d, launch_id)
    except launch.LaunchError as e:
        return OpResult(False, [str(e)])
    if provider is not None:
        plan = launch.enrich_competitors(plan, provider)
    research = launch_research.research_for_plan(load_research(ws, launch_id))      # keyword Publisher Rocket importate nella bozza (se ci sono)
    plan["keyword_plan"] = launch_keywords.build_keyword_plan(plan, research=research)
    plan["updated_at"] = launch._now()
    try:
        path = launch.save_plan(d, plan, overwrite=True)
    except (launch.LaunchError, OSError) as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Piano keyword generato e salvato nella bozza", data={"plan": plan, "path": str(path)})


# ------------------------------------------------------------------ ricerca Publisher Rocket (file locali, nella cartella della bozza)
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from kdp_ads import launch_research  # noqa: E402


def research_dir(ws, launch_id: str) -> Path:
    launch.plan_path(launch_dir(ws), launch_id)             # valida l'id (niente percorsi arbitrari)
    return launch_dir(ws) / launch_id / "research"


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._() \-]", "_", Path(str(name or "").replace("\\", "/")).name).strip(" .")
    return (base or "file")[:100]


def load_research(ws, launch_id: str) -> list:
    """Record normalizzati dei file importati nella bozza (nell'ordine di import). I file mancanti o illeggibili vengono saltati."""
    plan = get_plan(ws, launch_id) or {}
    out = []
    for f in (plan.get("research") or {}).get("files", []):
        d = research_dir(ws, launch_id)
        try:
            rec = json.loads((d / f.get("normalized", "")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("parser_version") != launch_research.PARSER_VERSION:
            # importato con un parser piu' vecchio: si rilegge l'ORIGINALE conservato, in memoria (i file su disco non vengono toccati)
            try:
                rec = launch_research.parse_file(f["file_name"], (d / f["stored_as"]).read_bytes(), (plan.get("book") or {}).get("marketplace"))
            except (OSError, launch_research.ResearchError, KeyError):
                pass
        out.append(rec)
    return out


def research_dry_run(ws, launch_id: str, name: str, data: bytes) -> OpResult:
    """DRY RUN obbligatorio: legge il file in memoria, non scrive nulla (ne' bozza, ne' cartella research, ne' books.json, ne' DB, ne' INPUT)."""
    plan = get_plan(ws, launch_id)
    if plan is None:
        return OpResult(False, ["Bozza non trovata"])
    plan = {**plan, "_research_records": load_research(ws, launch_id)}
    rep = launch_research.dry_run_report(plan, name, data)
    return OpResult(rep["status"] == "DRY_RUN_OK", rep["problems"], data=rep)


def import_research(ws, launch_id: str, name: str, data: bytes) -> OpResult:
    """Importa il file NELLA BOZZA: LAUNCH/<id>/research/<hash>__<nome> (originale) + <hash>.normalized.json, indice in plan["research"] ed
    arricchimento dei SOLI competitor gia' inseriti. Rifa' il dry run: un file non valido o gia' importato non scrive nulla."""
    r = research_dry_run(ws, launch_id, name, data)
    if not r.ok:
        return r
    rep = r.data
    rec = rep["record"]
    plan = launch.load_plan(launch_dir(ws), launch_id)
    d = research_dir(ws, launch_id)
    rid = rec["sha256"][:10]
    stored, norm = f"{rid}__{_safe_name(name)}", f"{rid}.normalized.json"
    written = []
    try:
        d.mkdir(parents=True, exist_ok=True)
        for fname, content in ((stored, data), (norm, json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"))):
            with open(d / fname, "xb") as fh:                # 'x': non sovrascrive mai
                fh.write(content)
            written.append(d / fname)
        enriched = launch_research.apply_enrichment(plan, rec)
        entry = {"id": rid, "file_name": name, "stored_as": stored, "normalized": norm, "sha256": rec["sha256"], "format": rec["format"],
                 "kind": rec["kind"], "rows": rec["rows"], "keywords": len(rec["keywords"]), "competitors": len(rec["competitors"]),
                 "enriched_asins": enriched, "imported_at": launch._now()}
        plan.setdefault("research", {"files": []})["files"].append(entry)
        plan["updated_at"] = launch._now()
        path = launch.save_plan(launch_dir(ws), plan, overwrite=True)
    except (launch.LaunchError, OSError) as e:
        for p in written:                                   # nessun file orfano se qualcosa fallisce
            p.unlink(missing_ok=True)
        return OpResult(False, [str(e)])
    return OpResult(True, message="Ricerca importata nella bozza", data={"plan": plan, "report": {k: v for k, v in rep.items() if k != "record"},
                                                                           "path": str(path), "stored": str(d / stored)})


def research_overview(ws, launch_id: str) -> dict | None:
    """Riepilogo dei file importati: competitor arricchiti/candidati, keyword trovate/pertinenti/scartate, metriche disponibili. Sola lettura."""
    plan = get_plan(ws, launch_id)
    if not plan or not (plan.get("research") or {}).get("files"):
        return None
    records = load_research(ws, launch_id)
    research = launch_research.research_for_plan(records)
    kp = launch_keywords.build_keyword_plan(plan, research=research)
    cls = {e["keyword"]: e["recommended_match_type"] for k in ("exact", "phrase", "discovery", "rejected") for e in kp[k]}
    kws = list(research)
    have = {c["asin"] for c in plan.get("competitors", [])}
    enriched = [{"asin": c["asin"], "title": (c.get("enrichment") or {}).get("title"), "source_file": (c.get("enrichment") or {}).get("source_file")}
                for c in plan.get("competitors", []) if (c.get("enrichment") or {}).get("provider") == launch_research.SOURCE]
    cand, seen = [], set()
    for rec in records:
        for c in launch_research.match_competitors(plan, rec)["candidates"]:
            if c["asin"] not in have and c["asin"] not in seen:
                seen.add(c["asin"])
                cand.append(c)
    metrics: dict = {}
    for rec in records:
        for k, n in rec.get("metrics_present", {}).items():
            metrics[k] = metrics.get(k, 0) + n
    return {"files": plan["research"]["files"], "enriched": enriched, "candidates": cand, "keywords_total": len(kws),
            "keywords_found_total": sum(f.get("keywords", 0) for f in plan["research"]["files"]),
            "keywords_pertinent": sum(1 for k in kws if cls.get(k) not in (None, "REJECT")), "keywords_discarded": sum(1 for k in kws if cls.get(k) == "REJECT"),
            "metrics_present": dict(sorted(metrics.items()))}


# ------------------------------------------------------------------ caricamento MULTIPLO: ogni file e' analizzato e importato separatamente
BATCH_OK, BATCH_DUP, BATCH_ALREADY = "DRY_RUN_OK", "DUPLICATE_IN_BATCH", "ALREADY_IMPORTED"


def _row(name, data, rep):
    """Riga di tabella di un file del lotto (dal report del dry run)."""
    return {"file_name": name, "format": rep.get("format"), "rows": rep.get("rows"), "keywords_found": rep.get("keywords_found", 0),
            "asins_found": rep.get("asins_found", 0), "metrics": sorted((rep.get("metrics_present") or {}).keys()), "status": rep["status"],
            "error": "; ".join(rep.get("problems") or []) or None, "sha256": launch_research.sha256(data), "report": rep}


def research_batch_dry_run(ws, launch_id: str, files: list) -> OpResult:
    """DRY RUN di piu' file: ognuno e' analizzato SEPARATAMENTE (un file errato non blocca gli altri) e non si scrive nulla.
    Stati per file: DRY_RUN_OK / DUPLICATE_IN_BATCH (identico a un file precedente dello stesso lotto) / ALREADY_IMPORTED (gia' nella bozza) /
    UNRECOGNIZED / ERROR. `files` = [(nome, byte)]. data = {rows: [riga per file], valid: n}."""
    plan = get_plan(ws, launch_id)
    if plan is None:
        return OpResult(False, ["Bozza non trovata"])
    plan = {**plan, "_research_records": load_research(ws, launch_id)}
    seen, rows = {}, []
    for name, data in files:
        sha = launch_research.sha256(data or b"")
        if sha in seen and data:
            rep = {"status": BATCH_DUP, "ok": False, "file_name": name, "problems": [f"Identico al file {seen[sha]} dello stesso caricamento: verra' importato una sola volta"],
                   "format": launch_research.file_format(name) if str(name).lower().endswith((".csv", ".xlsx", ".xls")) else None}
        else:
            rep = launch_research.dry_run_report(plan, name, data or b"")
            if rep["status"] == "SKIPPED_DUPLICATE":
                rep["status"] = BATCH_ALREADY
            seen.setdefault(sha, name)
        rows.append(_row(name, data or b"", rep))
    return OpResult(True, data={"rows": rows, "valid": sum(1 for r in rows if r["status"] == BATCH_OK)})


def import_research_batch(ws, launch_id: str, files: list) -> OpResult:
    """Importa nella bozza SOLO i file con stato DRY_RUN_OK (rifa' il dry run). Gli altri restano esclusi senza bloccare nessuno.
    Ogni file e' importato da solo (import_research): un errore su uno non annulla gli altri. Ritorna anche il riepilogo del lotto."""
    dry = research_batch_dry_run(ws, launch_id, files)
    if not dry.ok:
        return dry
    rows = dry.data["rows"]
    imported, shas = [], []
    for (name, data), row in zip(files, rows):
        if row["status"] != BATCH_OK:
            continue
        r = import_research(ws, launch_id, name, data)
        if r.ok:
            imported.append(row)
            shas.append(row["sha256"])
        else:
            row["status"], row["error"] = "ERROR", "; ".join(r.errors) or "import non riuscito"
    plan = get_plan(ws, launch_id) or {}
    have = {c["asin"] for c in plan.get("competitors", [])}
    kws = set()
    for rec in load_research(ws, launch_id):
        if rec.get("sha256") in shas:
            kws |= {k["keyword"] for k in rec.get("keywords", [])}
    enriched = sorted({a for f in (plan.get("research") or {}).get("files", []) if f.get("sha256") in shas for a in f.get("enriched_asins", [])})
    cands = sorted({c["asin"] for row in imported for c in row["report"].get("competitor_candidates", []) if c["asin"] not in have})
    by = lambda s: sum(1 for r in rows if r["status"] == s)  # noqa: E731
    summary = {"files_loaded": len(files), "files_valid": dry.data["valid"], "files_imported": len(imported), "duplicates": by(BATCH_DUP),
               "already_imported": by(BATCH_ALREADY), "errors": sum(1 for r in rows if r["status"] in ("ERROR", "UNRECOGNIZED")),
               "keywords_total": sum(r["keywords_found"] for r in imported), "keywords_unique": len(kws),
               "competitors_enriched": enriched, "competitors_candidates": cands,
               "files": [{"file_name": r["file_name"], "status": r["status"], "keywords_found": r["keywords_found"], "error": r["error"]} for r in rows]}
    if not imported:
        return OpResult(False, ["Nessun file valido da importare"], data={"rows": rows, "summary": summary})
    return OpResult(True, message="File validi importati nella bozza", data={"rows": rows, "summary": summary, "plan": plan})
