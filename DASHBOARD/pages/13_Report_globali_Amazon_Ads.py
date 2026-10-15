"""Report globali Amazon Ads: 4 CSV (Targeting 7D/30D, Search Term 7D/30D) con MOLTE campagne e MOLTI libri -> anteprima -> import -> smistamento automatico per libro.
Le campagne nuove non bloccano le note: restano da associare in «Campagne da associare». Solo analisi e supporto alle decisioni: nessuna azione su Amazon Ads
(bid, pause, keyword e negative restano modifiche MANUALI). Nessuna rete, nessuna Amazon."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import ops_ui  # noqa: E402
from ops import global_import as GI  # noqa: E402
from ops import workspace  # noqa: E402

ws = workspace.current()
S = st.session_state
ops_ui.setup(st, U.GLOBAL_PAGE_TITLE)
st.markdown(U.GLOBAL_INTRO)
st.caption(U.GLOBAL_NO_ACTIONS)
try:
    st.page_link("pages/8_Importa_report.py", label=U.GLOBAL_LEGACY_LINK)
except Exception:                                              # pagina non registrata (esecuzione isolata)
    st.caption(U.GLOBAL_LEGACY_LINK + " (menu a sinistra)")

n_up = S.get("gr_n", 0)
for kind, text in (S.pop("gr_flash", None) or []):
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(kind, st.info)(text)

# ---------------------------------------------------------------- 1. quattro slot
st.header(U.GLOBAL_SLOTS_HEADER)
order = ["TARGETING_7D", "TARGETING_30D", "SEARCH_TERM_7D", "SEARCH_TERM_30D"]
files = {}
for pair in (order[:2], order[2:]):                           # prima riga: Targeting 7D | 30D; seconda riga: Search Term 7D | 30D
    cols = st.columns(2)
    for col, slot in zip(cols, pair):
        with col:
            f = st.file_uploader(GI.SLOT_LABELS[slot], type=["csv"], key=f"gr_{slot}_{n_up}")
            if f is not None and hasattr(f, "getvalue") and isinstance(getattr(f, "name", None), str):
                files[slot] = (f.name, f.getvalue())
mp_choices = [U.GLOBAL_MP_AUTO, "US", "UK", "DE", "IT", "FR", "ES", "CA", "AU"]
mp_pick = st.selectbox("Marketplace", mp_choices, key="gr_mp")
mp = None if mp_pick == U.GLOBAL_MP_AUTO else mp_pick
fingerprint = hashlib.sha256(("|".join(f"{s}:{n}:{len(d)}:{hashlib.sha256(d).hexdigest()[:12]}" for s, (n, d) in sorted(files.items())) + f"|{mp}").encode()).hexdigest()
st.caption("Il tipo di report e la finestra (7D / 30D) sono quelli dello slot in cui carichi il file: «Intervallo di date» è solo un metadato di riga e non definisce lo snapshot.")

if st.button("ANALIZZA FILE", key="gr_analyze", disabled=not files) is True:
    S["gr_pv"] = {"fp": fingerprint, "data": GI.preview(ws, files, mp)}
    S.pop("gr_result", None)

def do_import(replace=()):
    """Importa (UN backup) e passa all'esito. `replace` = slot con «USA VERSIONE PIÙ RECENTE» confermato dall'utente."""
    r = GI.run_import(ws, files, mp, replace=replace)
    S["gr_result"] = {"ok": r.ok, "errors": r.errors, "data": r.data, "message": r.message}
    S["gr_n"] = n_up + 1                                     # svuota i caricamenti: nessun doppio import involontario
    S.pop("gr_pv", None)
    st.rerun()


# ---------------------------------------------------------------- 2. anteprima
pv = S.get("gr_pv")
if isinstance(pv, dict) and pv.get("fp") == fingerprint and files:
    st.header(U.GLOBAL_PREVIEW_HEADER)
    importable = 0
    for slot in order:
        v = pv["data"].get(slot)
        if v is None:
            continue
        with st.container(border=True):
            if "error" in v:
                st.markdown(f"**{GI.SLOT_LABELS[slot]}** · {F.badge('NON VALIDO', 'red')}", unsafe_allow_html=True)
                st.error(v["error"])
                continue
            p = v["preview"]
            tone = {"NUOVO": "green", "DUPLICATO": "grey", "CONFLITTO": "red"}.get(p["status"], "grey")
            st.markdown(f"**{GI.SLOT_LABELS[slot]}** · {F.badge(p['status'], tone)} · {F.esc(p['file_name'])}", unsafe_allow_html=True)
            c = st.columns(4)
            c[0].metric("Tipo di report", p["report_label"])
            c[1].metric("Finestra assegnata", f"{p['window_label'].replace('RECENT_', 'Ultimi ').replace('D', ' giorni')}")
            c[2].metric("Righe valide", F.num(p["rows"]))
            c[3].metric("Snapshot", f"{p['date_from']} → {p['date_to']}")
            c = st.columns(5)
            c[0].metric("Campagne", F.num(p["campaigns"]))
            c[1].metric("Portfolio", F.num(p["portfolios"]))
            c[2].metric("Già associate", F.num(p["assigned"]))
            c[3].metric("Nuove / da associare", F.num(p["new"] + p["unassigned_known"]))
            c[4].metric("Ignorate", F.num(p["ignored"]))
            st.caption(f"Marketplace {p['marketplace'] or 'da scegliere'} · valuta {p['currency'] or 'n/d'}")
            for w in p["warnings"]:
                st.warning(w)
            for w in p["problems"]:
                st.error(w)
            importable += 1 if p["importable"] else 0
            if p["status"] == "REVISIONE" and p.get("revision"):
                rv = p["revision"]
                st.markdown("**Esiste già un report per questa finestra, ma il file contiene dati diversi.**")
                st.dataframe([{"Versione": f"attiva (rev. {rv['old']['revision']}) · {rv['old']['file']}", "Righe": F.num(rv["old"]["rows"]), "Campagne": F.num(rv["old"]["campaigns"]),
                               "Clic": F.num(rv["old"]["clicks"]), "Spesa": F.money(rv["old"]["spend"]), "Acquisti": F.num(rv["old"]["orders"]), "Vendite": F.money(rv["old"]["sales"])},
                              {"Versione": f"nuovo file · {p['file_name']}", "Righe": F.num(rv["new"]["rows"]), "Campagne": F.num(rv["new"]["campaigns"]), "Clic": F.num(rv["new"]["clicks"]),
                               "Spesa": F.money(rv["new"]["spend"]), "Acquisti": F.num(rv["new"]["orders"]), "Vendite": F.money(rv["new"]["sales"])}],
                             use_container_width=True, hide_index=True)
                st.caption("Se usi la versione più recente: la precedente resta archiviata (SUPERSEDED, nessun dato cancellato dall'audit), i dati dei libri vengono rismistati "
                           "senza doppio conteggio e analisi/decisioni dei libri coinvolti vengono ricalcolate.")
                b1, b2 = st.columns(2)
                if b1.button("USA VERSIONE PIÙ RECENTE", key=f"gr_rev_{slot}") is True:
                    do_import(replace={slot})
                if b2.button("ANNULLA", key=f"gr_cancel_{slot}") is True:
                    S["gr_n"] = n_up + 1
                    S.pop("gr_pv", None)
                    st.rerun()
    st.caption(U.GLOBAL_PREVIEW_NOTE)
    if st.button("IMPORTA", key="gr_import", disabled=importable == 0) is True:
        do_import()

# ---------------------------------------------------------------- 3. esito dell'ultimo import
res = S.get("gr_result")
if isinstance(res, dict):
    st.header(U.GLOBAL_RESULT_HEADER)
    d = res.get("data") or {}
    (st.success if res["ok"] else st.error)(res["message"] if res["ok"] else "; ".join(res["errors"]) or "Import non riuscito")
    for e in res.get("errors") or []:
        if res["ok"]:
            st.warning(e)
    for r in d.get("results", []):
        st.markdown(f"- {GI.SLOT_LABELS[r['slot']]}: {r['message']}")
    for slot in d.get("revisions_pending", []):
        st.warning(f"{GI.SLOT_LABELS[slot]}: esiste già un report per questa finestra con dati diversi. Non è stato sovrascritto: ricarica il file e scegli «USA VERSIONE PIÙ RECENTE» per sostituirlo.")
    m = d.get("materialized") or {}
    cnt = d.get("counts") or {}
    c = st.columns(4)
    c[0].metric("Snapshot importati", F.num(d.get("imported")))
    c[1].metric("Righe smistate sui libri", F.num(m.get("rows")))
    c[2].metric("Campagne da associare", F.num(cnt.get("UNASSIGNED")))
    c[3].metric("Campagne associate", F.num(cnt.get("ASSIGNED")))
    if m.get("skipped_empty"):
        st.caption(f"{m['skipped_empty']} righe con termine di ricerca vuoto conservate ma escluse dalle logiche sui termini reali.")
    for w in d.get("conflicts", []):
        st.warning(w)
    for note in d.get("notes", []):
        st.caption(note)
    if d.get("recalc"):
        st.markdown("**Analisi e decisioni aggiornate per i libri con campagne note:**")
        for x in d["recalc"]:
            st.markdown(f"- {x['book_id']} ({x['marketplace']}): {'✓' if x['ok'] else '✗'} {x['message']}")
    if cnt.get("UNASSIGNED"):
        st.info(f"{cnt['UNASSIGNED']} campagne nuove non ancora associate: non sono errori, vanno solo collegate a un libro.")
        try:
            st.page_link("pages/14_Campagne_da_associare.py", label="ASSOCIA ORA")
        except Exception:
            st.caption("Apri «Campagne da associare» dal menu.")
    st.caption(U.GLOBAL_NO_ACTIONS)

with st.expander("Storico dei report globali importati"):
    hist = GI.list_imports(ws)
    if not hist:
        st.caption("Nessun report globale importato.")
    else:
        st.dataframe([{"Report": ("Targeting" if h["report_type"] == "TARGETING" else "Search Term"), "Finestra": h["window_label"].replace("RECENT_", ""), "Dal": h["date_from"],
                       "Al": h["date_to"], "Marketplace": h["marketplace"], "File": h["file_name"], "Importato il": str(h["imported_at"])[:16].replace("T", " "),
                       "Righe": F.num(h["row_count"]), "Campagne": F.num(h["campaign_count"]), "Termini vuoti": F.num(h["empty_search_term_rows"])} for h in hist],
                     use_container_width=True, hide_index=True)
