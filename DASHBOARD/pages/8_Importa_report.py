"""Importa report (V2): flusso guidato libro -> MARKETPLACE -> campagna -> tipo -> periodo -> CSV -> DRY-RUN obbligatorio -> import reale ->
analisi -> decisioni (per marketplace), piu' la routine settimanale separata per marketplace. Usa la logica esistente
(import_all, add_snapshot, analyze, decide). Nessuna rete, nessuna Amazon."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import ops_ui  # noqa: E402
from ops import catalog, importing, pipeline_ops, weekly, workspace  # noqa: E402
from ops import ValidationError  # noqa: E402

GUIDED, WEEKLY = "Importazione guidata", "Importazione settimanale"
ws = workspace.current()
S = st.session_state
ops_ui.setup(st, "Importa report")

books = catalog.list_books(ws)
if not books:
    st.info("Nessun libro registrato: aggiungilo prima in 'Gestione libri'.")
    st.stop()


def prefill(book_label, mp, cid, rtype, kind, ref):
    """Callback dei pulsanti 'Carica ...' della routine settimanale: passa alla modalita' guidata gia' compilata."""
    S["imp_mode"], S["imp_book"], S["imp_mp"] = GUIDED, book_label, mp
    S["imp_campaign"], S["imp_rtype"], S["imp_period"], S["imp_date_to"] = cid, rtype, kind, ref
    S["imp_upl_n"] = S.get("imp_upl_n", 0) + 1


def reset_upload():
    S["imp_upl_n"] = S.get("imp_upl_n", 0) + 1
    for k in ("imp_dry", "imp_dry_fp", "imp_import", "imp_done_sha"):
        S.pop(k, None)


mode = st.radio("Modalità", [GUIDED, WEEKLY], horizontal=True, key="imp_mode")
labels = {f"{b['title']} · {b['book_id']}": b["book_id"] for b in books}
book_label = st.selectbox("1 · Libro", list(labels), key="imp_book")
book_id = labels[book_label]
mps = catalog.list_marketplaces(ws, book_id)

# =============================================================== modalita' settimanale: una checklist per marketplace
if mode == WEEKLY:
    st.subheader("Importazione settimanale")
    st.caption("Per ogni campagna servono 4 report. Non è obbligatorio caricarli tutti insieme: qui vedi quali mancano. "
               "Ogni marketplace è indipendente dagli altri.")
    ref = st.date_input("Data di riferimento", value=date.today(), key="imp_ref", format="DD/MM/YYYY")
    if not mps:
        st.info("Questo libro non ha marketplace: aggiungili in 'Gestione libri'.")
    for m in mps:
        code = m["marketplace"]
        rows = weekly.checklist(ws, book_id, ref, marketplace=code)
        st.markdown(f"## Marketplace {code} · {m['currency']}")
        if not rows:
            st.info(f"Nessuna campagna per {code}: aggiungila in 'Gestione libri'.")
        for c in rows:
            with st.container(border=True):
                st.markdown(f"### {code} · {c['campaign_id']} · {c['campaign_type']}")
                for it in c["items"]:
                    mark = "[x]" if it["status"] == "PRESENTE" else "[ ]"
                    extra = f" · fino al {it['date_to']}" if it["date_to"] else ""
                    st.markdown(f"- {mark} **{it['label']}** — {U.WEEKLY_STATUS_LABELS[it['status']]}{extra}")
                    if it["status"] != "PRESENTE":
                        st.button(f"Carica {it['label']} ({code} · {c['campaign_id']})", key=f"wk_{code}_{c['campaign_id']}_{it['label']}",
                                  on_click=prefill, args=(book_label, code, c["campaign_id"], it["report_type"], it["kind"], ref))
                if c["complete"]:
                    st.success(U.OPS_WEEKLY_COMPLETE)
        if weekly.marketplace_complete(rows):
            st.success(f"Set report completo per {code}")

# =============================================================== modalita' guidata
else:
    if not mps:
        st.warning("Questo libro non ha marketplace: aggiungili in 'Gestione libri'.")
        st.page_link("pages/7_Gestione_libri.py", label="Vai a Gestione libri")
        st.stop()
    mlabels = {f"{m['marketplace']} · {m['currency']}": m["marketplace"] for m in mps}
    mp_label = st.selectbox("2 · Marketplace", list(mlabels), key="imp_mp")
    mp = mlabels[mp_label]
    camps = catalog.list_campaigns(ws, book_id, mp)
    if not camps:
        st.warning(f"Il marketplace {mp} non ha campagne: aggiungile in 'Gestione libri'.")
        st.page_link("pages/7_Gestione_libri.py", label="Vai a Gestione libri")
        st.stop()
    cid = st.selectbox("3 · Campagna", [c["campaign_id"] for c in camps], key="imp_campaign",
                       format_func=lambda x: f"{x} ({next(c['campaign_type'] for c in camps if c['campaign_id'] == x)})")
    rtype = st.selectbox("4 · Tipo di report", list(importing.REPORT_TYPES), key="imp_rtype", format_func=importing.REPORT_TYPES.get)
    kind = st.selectbox("5 · Tipo di periodo", list(importing.PERIOD_KINDS), key="imp_period", format_func=importing.PERIOD_KINDS.get)
    c = st.columns(2)
    S.setdefault("imp_date_to", date.today())            # valore iniziale via session_state (impostabile anche dai pulsanti settimanali)
    S.setdefault("imp_date_from", date.today())
    d_to = c[1].date_input("6 · Data di fine" + (" (data di esportazione)" if kind == "LIFETIME" else ""),
                           key="imp_date_to", format="DD/MM/YYYY")
    d_from = None
    if kind == "CUSTOM":
        d_from = c[0].date_input("6 · Data di inizio", key="imp_date_from", format="DD/MM/YYYY")
    attribution = st.text_input("Finestra di attribuzione (se nota, es. 14 Day)", key="imp_attr")
    st.markdown(f"**Marketplace selezionato: {mp} · {next(m['currency'] for m in mps if m['marketplace'] == mp)}** — "
                "il report verrà importato solo in questo marketplace.")
    upload = st.file_uploader("7 · File CSV", type=["csv"], key=f"imp_file_{S.get('imp_upl_n', 0)}")

    if upload is None:
        st.info("Carica un file CSV per continuare.")
    else:
        data = upload.getvalue()
        folder = importing.campaign_folder(ws, book_id, cid)
        sha = importing.sha256(data)
        if S.get("imp_done_sha") == sha and S.get("imp_import") is not None:
            plan = None            # gia' importato in questa sessione: mostro solo l'esito
        else:
            try:
                period = importing.resolve_period(kind, d_from, d_to)
                plan = importing.plan_upload(folder, upload.name, data, period)
                entry = importing.build_entry(plan.final_name, rtype, period, attribution)
            except ValidationError as e:
                st.error(str(e))
                st.stop()
        if plan is not None:
            st.markdown("**Anteprima**")
            st.table(importing.preview(book_id, cid, plan.final_name, entry, marketplace=mp))
            if plan.note:
                st.info(plan.note)
            fp = json.dumps([book_id, mp, cid, plan.final_name, plan.sha, entry], sort_keys=True)
            if S.get("imp_dry_fp") != fp:
                S.pop("imp_dry", None)
                S.pop("imp_import", None)
            st.markdown("**8 · Dry-run (obbligatorio)**")
            st.caption("Il dry-run controlla il file senza scrivere nulla: l'import reale è possibile solo se il dry-run è valido.")
            if st.button("ESEGUI DRY-RUN", key="btn_dry"):
                S["imp_dry"], S["imp_dry_fp"] = importing.dry_run(ws, book_id, cid, plan.final_name, data, entry), fp
                S.pop("imp_import", None)
            dry = S.get("imp_dry")
            if dry is not None:
                {"DRY_RUN_OK": st.success, "SKIPPED_DUPLICATE": st.warning}.get(dry.status, st.error)(U.DRY_RUN_LABELS[dry.status])
                st.table({"Marketplace": dry.marketplace or F.ND, "Righe": str(dry.rows), "Date": f"{dry.date_from or '—'} → {dry.date_to or '—'}",
                          "Tipo rilevato": importing.REPORT_TYPES.get(dry.report_type, F.ND), "Snapshot": dry.period_id or F.ND})
                for w in dry.warnings:
                    st.warning(w)
                for p in dry.problems:
                    st.error(p)
            st.markdown("**9 · Importa**")
            if st.button("IMPORTA REPORT", key="btn_import", disabled=not (dry is not None and dry.ok)):
                out = importing.commit_import(ws, book_id, cid, upload.name, plan.final_name, data, entry)
                S["imp_import"] = out
                if out.status != "ERROR":
                    S["imp_done_sha"] = sha
                st.rerun()
        out = S.get("imp_import")
        if out is not None:
            if out.status == "IMPORTED":
                st.success("Report importato")
                st.table({"import_id": str(out.import_id), "Marketplace": mp, "Righe importate": str(out.rows), "Snapshot": out.snapshot_id,
                          "File salvato": out.file_path, "Copia originale": out.original_copy, "Backup DB": out.backup or F.ND})
            elif out.status == "SKIPPED_DUPLICATE":
                st.warning("Report già importato (duplicato): nessuna modifica")
            for w in out.warnings:
                st.warning(w)
            for p in out.problems:
                st.error(p)
            st.button("IMPORTA UN ALTRO REPORT", key="btn_again", on_click=reset_upload)

# =============================================================== analisi e decisioni: UN marketplace alla volta
st.divider()
st.subheader("Analisi e decisioni")
amlabels = {f"{m['marketplace']} · {m['currency']}": m["marketplace"] for m in mps}
if not amlabels:
    st.info("Nessun marketplace configurato per questo libro.")
    st.stop()
guided = amlabels.get(S.get("imp_mp")) if mode == GUIDED else None       # di default: il marketplace appena usato per l'import
an_mp = amlabels[st.selectbox("Marketplace da analizzare", list(amlabels), index=list(amlabels.values()).index(guided) if guided else 0)]
st.caption(f"L'analisi e le decisioni riguardano solo il marketplace {an_mp}: nessun dato di altri marketplace viene incluso o confrontato. "
           "Dopo l'import: analizza (stessa logica di analyze.py) e poi genera le decisioni (stessa logica di decide.py). "
           "Prima di ogni scrittura nel database viene creato un backup.")
if st.button("ANALIZZA LIBRO", key="btn_analyze"):
    r = pipeline_ops.run_book_analysis(ws, book_id, an_mp)
    S["imp_analysis"] = {"book_id": book_id, "marketplace": an_mp, "res": r}
    S.pop("imp_decisions", None)
a = S.get("imp_analysis")
a_ok = bool(a and a["book_id"] == book_id and a["marketplace"] == an_mp)
if a_ok:
    if a["res"].ok:
        d = a["res"].data
        st.success("Analisi completata")
        st.table({"analysis_run_id": d["run_id"], "Marketplace": d["marketplace"], "Righe analizzate": d["rows"], "Overlap": d["overlaps"],
                  "Avvisi sui dati": len(d["warnings"]), "Data/ora": U.label_datetime(d["generated_at"]), "Backup DB": d.get("backup") or F.ND})
        if d["warnings"]:
            with st.expander(f"Avvisi sui dati ({len(d['warnings'])})"):
                for w in d["warnings"]:
                    st.write(w)
    else:
        for e in a["res"].errors:
            st.error(e)
run_id = a["res"].data["run_id"] if (a_ok and a["res"].ok) else None
if run_id is None:
    latest = pipeline_ops.latest_analysis_run(ws, book_id, an_mp)
    run_id = latest["run_id"] if latest else None
    if run_id is not None:
        st.caption(f"Nessuna analisi in questa sessione per {an_mp}: verrebbe usato l'ultimo run di analisi esistente (#{run_id}).")
if run_id is not None:
    if st.button("GENERA DECISIONI", key="btn_decide"):
        r = pipeline_ops.run_book_decisions(ws, book_id, run_id, an_mp)
        S["imp_decisions"] = {"book_id": book_id, "marketplace": an_mp, "res": r}
    dd = S.get("imp_decisions")
    if dd and dd["book_id"] == book_id and dd["marketplace"] == an_mp:
        if dd["res"].ok:
            d = dd["res"].data
            st.success("Decisioni generate")
            st.table({"decision_run_id": d["run_id"], "Marketplace": d["marketplace"], "Run di analisi usato": d["analysis_run_id"],
                      "Decisioni": d["decisions"], "Da valutare ora": d["actionable"], "Target in pausa": d["paused"],
                      "Avvisi ereditati": d["analysis_warnings"], "Avvisi propri": d["own_warnings"],
                      "Backup DB": d.get("backup") or F.ND})
            st.page_link("panoramica.py", label="VAI ALLA PANORAMICA")
        else:
            for e in dd["res"].errors:
                st.error(e)
