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
st.info(U.GLOBAL_FREQ_NOTE)
try:
    st.page_link("pages/8_Importa_report.py", label=U.GLOBAL_LEGACY_LINK)
except Exception:                                              # pagina non registrata (esecuzione isolata)
    st.caption(U.GLOBAL_LEGACY_LINK + " (menu a sinistra)")

n_up = S.get("gr_n", 0)
for kind, text in (S.pop("gr_flash", None) or []):
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(kind, st.info)(text)

# ---------------------------------------------------------------- 1. quattro slot
st.header(U.GLOBAL_SLOTS_HEADER)
order = ["TARGETING_7D", "TARGETING_30D", "TARGETING_LIFETIME", "SEARCH_TERM_7D", "SEARCH_TERM_30D", "SEARCH_TERM_65D"]
files = {}
for pair in (order[:3], order[3:]):                           # prima riga: Targeting 7D | 30D | Lifetime (mensile); seconda riga: Search Term 7D | 30D | 65D
    cols = st.columns(3)
    for col, slot in zip(cols, pair):
        with col:
            f = st.file_uploader(GI.SLOT_LABELS[slot], type=["csv"], key=f"gr_{slot}_{n_up}")
            if f is not None and hasattr(f, "getvalue") and isinstance(getattr(f, "name", None), str):
                files[slot] = (f.name, f.getvalue())
# Un report Amazon puo' contenere campagne di PIU' marketplace (es. USD + GBP): il marketplace NON si sceglie per l'intero file ma per ogni gruppo valuta. USD -> US e GBP -> UK
# sono automatici; una valuta ambigua (es. EUR) va scelta solo per il proprio gruppo.
mpmap = S.setdefault("gr_mpmap", {})
mp = dict(mpmap) or None
fingerprint = hashlib.sha256(("|".join(f"{s}:{n}:{len(d)}:{hashlib.sha256(d).hexdigest()[:12]}" for s, (n, d) in sorted(files.items())) + f"|{sorted(mpmap.items())}").encode()).hexdigest()
st.caption("Il tipo di report e la finestra (7D / 30D) sono quelli dello slot in cui carichi il file: «Intervallo di date» è solo un metadato di riga e non definisce lo snapshot. "
           "Il marketplace si riconosce per ogni gruppo di campagne dalla valuta del report: un file con USD e GBP produce uno snapshot US e uno UK.")

if st.button("ANALIZZA FILE", key="gr_analyze", disabled=not files) is True:
    S["gr_pv"] = {"fp": fingerprint, "data": GI.preview(ws, files, mp)}
    S.pop("gr_result", None)
if S.pop("gr_reanalyze", False) and files:                       # scelta del marketplace di un gruppo non risolto: l'anteprima si aggiorna da sola
    S["gr_pv"] = {"fp": fingerprint, "data": GI.preview(ws, files, mp)}

def do_import(replace=()):
    """Importa (UN backup) e passa all'esito. `replace` = slot con «USA VERSIONE PIÙ RECENTE» confermato dall'utente."""
    r = GI.run_import(ws, files, mp, replace=replace)
    S["gr_mpmap"] = {}
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
            acc_line = (f"**ACCOUNT AMAZON** · Nome: **{F.esc(p.get('account_name') or 'non indicato nel file')}** · ID: `{p['account_id']}`" if p.get("account_id")
                        else f"**ACCOUNT AMAZON** · {F.badge('NON DETERMINABILE', 'red')} · ID rilevati nel file: {', '.join(p.get('account_ids') or []) or 'nessuno'}")
            st.markdown(acc_line, unsafe_allow_html=True)
            c = st.columns(4)
            c[0].metric("Tipo di report", p["report_label"])
            c[1].metric("Finestra assegnata", ("Storico completo (Lifetime)" if p["window_label"] == "LIFETIME" else p["window_label"].replace("RECENT_", "Ultimi ").replace("D", " giorni")))
            c[2].metric("Righe valide", F.num(p["rows"]))
            c[3].metric("Snapshot", f"fino al {p['date_to']}" if p["window_label"] == "LIFETIME" else f"{p['date_from']} → {p['date_to']}")
            for g in p.get("groups") or []:
                with st.container(border=True):
                    if g["marketplace"] is None:
                        st.markdown(f"**Valuta: {F.esc(g['currency'] or 'non indicata')}** · Marketplace: {F.badge('DA SELEZIONARE', 'red')} · {F.num(g['rows'])} righe · {F.num(g['campaigns'])} campagne",
                                    unsafe_allow_html=True)
                        opts = ["— scegli —"] + (g.get("choices") or []) + [m for m in ("US", "UK", "DE", "IT", "FR", "ES", "CA", "AU") if m not in (g.get("choices") or [])]
                        key = f"gr_mpg_{slot}_{g['currency'] or 'nd'}"
                        pick = st.selectbox(f"Marketplace del gruppo con valuta {g['currency'] or 'non indicata'}", opts, key=key)
                        if pick != "— scegli —" and mpmap.get(g["currency"] or "") != pick:
                            mpmap[g["currency"] or ""] = pick
                            S["gr_reanalyze"] = True
                            st.rerun()
                        continue
                    src = {"CURRENCY": "dalla valuta", "USER": "scelto da te", "METADATA": "dal report"}.get(g["source"], "")
                    st.markdown(f"**Gruppo {g['marketplace']} / {F.esc(g['currency'] or 'n/d')}** · {F.badge(g['status'], {'NUOVO': 'green', 'DUPLICATO': 'grey', 'REVISIONE': 'orange'}.get(g['status'], 'grey'))}"
                                f" · marketplace {src} · snapshot {g['date_from']} → {g['date_to']}", unsafe_allow_html=True)
                    c = st.columns(6)
                    c[0].metric("Righe", F.num(g["rows"]))
                    c[1].metric("Campagne", F.num(g["campaigns"]))
                    c[2].metric("Portfolio", F.num(g["portfolios"]))
                    c[3].metric("Già associate", F.num(g["assigned"]))
                    c[4].metric("Nuove / da associare", F.num(g["new"] + g["unassigned_known"]))
                    c[5].metric("Ignorate", F.num(g["ignored"]))
                    if g.get("portfolio_names"):
                        st.caption("Portfolio: " + ", ".join(g["portfolio_names"]))
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
    batch_accounts = sorted({(v["preview"].get("account_id"), v["preview"].get("account_name")) for v in pv["data"].values() if "preview" in v and v["preview"].get("account_id")})
    if len(batch_accounts) > 1:
        st.warning("Questo gruppo di file contiene report di " + str(len(batch_accounts)) + " account Amazon Ads diversi (" + "; ".join(f"{n or 'senza nome'} · {i}" for i, n in batch_accounts)
                   + "). Ogni file viene importato nel proprio account, senza collisioni; per chiarezza conviene caricare un gruppo di 4 file per account.")
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

mixed = GI.mixed_imports(ws)
if mixed:
    with st.expander(f"RIPARAZIONE MARKETPLACE ({len(mixed)} import da correggere)", expanded=True):
        st.warning("Alcuni report importati in passato contenevano campagne di più marketplace (es. USD + GBP) ma sono stati registrati come un solo marketplace. "
                   "La riparazione sposta le righe nel marketplace giusto (nuovo snapshot per ogni gruppo), senza cancellare nulla: gli import originali restano come audit, "
                   "le associazioni già valide non cambiano e le campagne da verificare restano da associare. Prima di scrivere viene creato un backup.")
        plan = GI.repair_marketplace_plan(ws)
        st.dataframe([{"Import": m["gimport_id"], "File": m["file"], "Registrato come": m["marketplace"],
                       "Righe da spostare": ", ".join(f"{mv['marketplace']} ({mv['rows']} righe, {mv['campaigns']} campagne)" for mv in m["moves"] if not mv.get("blocked")) or "—"}
                      for m in plan["imports"]], use_container_width=True, hide_index=True)
        for w in plan["conflicts"]:
            st.warning(w)
        ok_rep = st.checkbox("Confermo la riparazione (viene creato un backup del DB)", key="gr_repair_ok")
        if st.button("ESEGUI RIPARAZIONE", key="gr_repair_go", disabled=not ok_rep or not plan["created_imports"]) is True:
            rr = GI.repair_marketplace_apply(ws)
            S["gr_flash"] = [("ok" if rr.ok else "err", rr.message if rr.ok else "; ".join(rr.errors))] + [("warn", e) for e in (rr.errors if rr.ok else [])]
            st.rerun()

with st.expander("Storico dei report globali importati"):
    hist = GI.list_imports(ws)
    if not hist:
        st.caption("Nessun report globale importato.")
    else:
        st.dataframe([{"Account": (f"{h.get('account_name') or 'senza nome'} · {h['account_id']}" if h.get("account_id") else "non noto"),
                       "Report": ("Targeting" if h["report_type"] == "TARGETING" else "Search Term"), "Finestra": h["window_label"].replace("RECENT_", ""), "Dal": h["date_from"],
                       "Al": h["date_to"], "Marketplace": h["marketplace"], "Valuta": h.get("currency") or "n/d", "File": h["file_name"], "Importato il": str(h["imported_at"])[:16].replace("T", " "),
                       "Righe": F.num(h["row_count"]), "Campagne": F.num(h["campaign_count"]), "Termini vuoti": F.num(h["empty_search_term_rows"])} for h in hist],
                     use_container_width=True, hide_index=True)
