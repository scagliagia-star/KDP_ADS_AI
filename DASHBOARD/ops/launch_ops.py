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
    plan["keyword_plan"] = launch_keywords.build_keyword_plan(plan)
    plan["updated_at"] = launch._now()
    try:
        path = launch.save_plan(d, plan, overwrite=True)
    except (launch.LaunchError, OSError) as e:
        return OpResult(False, [str(e)])
    return OpResult(True, message="Piano keyword generato e salvato nella bozza", data={"plan": plan, "path": str(path)})
