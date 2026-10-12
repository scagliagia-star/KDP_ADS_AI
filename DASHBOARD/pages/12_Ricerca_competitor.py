"""Ricerca competitor: import di 1-5 CSV Helium 10 Cerebro (ASIN competitor), keyword comuni, gap rispetto alle campagne, suggerimenti Ads in FRASE e
opportunità editoriali derivate. Dati ESTERNI/STIMATI. Scrive SOLO le tabelle competitor_* del DB locale: non tocca books.json, INPUT, i report Amazon Ads
né le decisioni. Nessuna rete, nessuna Amazon."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import competitor_ui as CU  # noqa: E402
from components import ops_ui  # noqa: E402
from kdp_ads import competitor as C  # noqa: E402
from ops import competitor_ops as ops  # noqa: E402
from ops import workspace  # noqa: E402

ws = workspace.current()
S = st.session_state
ops_ui.setup(st, U.COMP_PAGE_TITLE)
st.markdown(CU.source_badge() + " &nbsp; **" + F.esc(U.COMP_SOURCE) + "**", unsafe_allow_html=True)
st.caption(U.COMP_INTRO)

if "cr_pending_pick" in S:                         # la selezione si applica PRIMA di creare il widget (dopo un import)
    S["cr_pick"] = S.pop("cr_pending_pick")
for kind, text in (S.pop("cr_flash", None) or []):
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(kind, st.info)(text)

# ---------------------------------------------------------------- A. nuova ricerca
st.header("Nuova ricerca")
books = ops.book_choices(ws)
titles = {None: U.COMP_NO_BOOK, **{b["book_id"]: f"{b['title']} · {b['book_id']}" for b in books}}
book_id = st.selectbox(U.COMP_ASSOCIATE, list(titles), format_func=lambda v: titles[v], key="cr_new_book")
book = next((b for b in books if b["book_id"] == book_id), None)
mp_options = (book or {}).get("marketplaces") or ["US", "UK", "DE", "IT", "FR", "ES", "CA", "AU"]
marketplace = st.selectbox("Marketplace", mp_options, key=f"cr_new_mp_{book_id}")
today = date.today().isoformat()
default = f"Competitor Research — {book['title']} — {today}" if book else f"Nuova ricerca {today}"
name = st.text_input("Nome della ricerca (facoltativo)", value="", placeholder=default, key=f"cr_new_name_{book_id}")
st.caption("Senza libro: «ricerca nuova opportunità editoriale» (NEW_OPPORTUNITY). Con un libro: ricerca per quel libro (EXISTING_BOOK), che può comunque far emergere "
           "nuove opportunità editoriali derivate.")
uploaded = list(st.file_uploader("File CSV Cerebro (da 1 a 5, uno per competitor; target 3-5)", type=["csv"], accept_multiple_files=True, key=f"cr_new_files_{S.get('cr_up', 0)}") or [])
manual = {}
files = []
for f in uploaded:
    data = f.getvalue()
    files.append((f.name, data))
    plan = ops.plan_files([(f.name, data)])[0]
    if plan["error"]:
        st.error(f"{f.name}: {plan['error']}")
    elif plan["needs_asin"]:
        manual[f.name] = st.text_input(f"ASIN competitor per «{f.name}» (non ricavabile dal nome del file)", key=f"cr_asin_{f.name}")
    else:
        p = plan["parsed"]
        st.caption(f"{f.name} → ASIN {plan['asin']} ({'dal nome del file' if plan['asin_source'] == 'FILENAME' else 'manuale'}) · {len(p['rows'])} keyword · "
                   f"{len(p['columns']) - len(p['unmapped'])} colonne riconosciute" + (" · " + "; ".join(p["warnings"]) if p["warnings"] else ""))
if files and len(files) > 5:
    st.error("Massimo 5 competitor per ricerca.")
elif files and len(files) <= 2:
    st.warning(U.COMP_LOW_RELIABILITY)
go = st.button("IMPORTA RICERCA", key="cr_new_go", disabled=not files or len(files) > 5)
if go is True:
    r = ops.import_research(ws, book_id, marketplace, name, files, manual, book_title=(book or {}).get("title"))
    flash = [("ok", f"Ricerca importata: {r.message}.")] if r.ok else []
    flash += [("err", e) for e in r.errors] + [("warn", w) for w in ((r.data or {}).get("warnings") or [])]
    if r.ok and r.data and r.data.get("imported"):
        S["cr_flash"] = flash
        S["cr_pending_pick"] = r.data["research_id"]
        S["cr_up"] = S.get("cr_up", 0) + 1                  # svuota il caricamento file: nessun doppio import involontario
        st.rerun()
    for kind, text in flash:
        {"ok": st.success, "warn": st.warning, "err": st.error}[kind](text)

# ---------------------------------------------------------------- B. ricerche esistenti
st.header("Ricerche importate")
projects = ops.list_projects(ws)
if not projects:
    st.info(U.COMP_NONE)
else:
    plabels = {p["research_id"]: f"{p['name']} · {p['competitors']} competitor · {p['status']} · {str(p['created_at'])[:10]}" for p in projects}
    if S.get("cr_pick") not in plabels:
        S["cr_pick"] = next(iter(plabels))
    rid = st.selectbox("Ricerca", list(plabels), format_func=lambda v: plabels[v], key="cr_pick")
    a = ops.analyze(ws, rid)
    if a is None:
        st.error("Ricerca non leggibile.")
    else:
        proj, ov, agg = a["project"], a["overview"], a["agg"]
        existing = proj["mode"] == "EXISTING_BOOK"
        # ---- header
        st.subheader(proj["name"])
        st.markdown(" ".join([CU.source_badge(), F.badge("LIBRO ESISTENTE" if existing else "NUOVA OPPORTUNITÀ", "blue" if existing else "purple"),
                              F.badge(proj["status"], "grey"), F.badge(f"Affidabilità {ov['reliability']}", {"ALTA": "green", "MEDIA": "orange"}.get(ov["reliability"], "red"))]),
                    unsafe_allow_html=True)
        st.caption(f"Modalità: {proj['mode']} · Libro: {proj.get('book_title') or 'nessuno'}{' (' + proj['book_id'] + ')' if proj['book_id'] else ''} · "
                   f"Marketplace {proj['marketplace']} · Competitor caricati: {ov['total']} ({', '.join(agg['asins'])}) · Creata il {str(proj['created_at'])[:10]}")
        if ov["total"] <= 2:
            st.warning(U.COMP_LOW_RELIABILITY)
        with st.expander("Rinomina / stato del progetto"):
            with st.form(f"cr_edit_{rid}"):
                new_name = st.text_input("Nome", value=proj["name"])
                statuses = ["RESEARCH", "REVIEW", "APPROVED", "REJECTED", "CONVERTED_TO_BOOK"]
                new_status = st.selectbox("Stato", statuses, index=statuses.index(proj["status"]) if proj["status"] in statuses else 0)
                save = st.form_submit_button("SALVA")
            if save is True:
                res = ops.update_project(ws, rid, new_name, new_status)
                (st.success if res.ok else st.error)(res.message or "; ".join(res.errors))
                if res.ok:
                    st.rerun()
        tabs = st.tabs(U.COMP_TABS)

        # ---- 1. PANORAMICA
        with tabs[0]:
            st.markdown(f"**{ov['total']} competitor analizzati**")
            for cnt, num in ov["by_count"].items():
                st.markdown(f"- {num} keyword presenti su {cnt}/{ov['total']}")
            if existing:
                st.markdown(f"**ADS:** {ov['ads_count']} keyword da valutare in FRASE")
            lc = ov["launch"]
            st.markdown(f"**KEYWORD DI LANCIO:** {lc['any']} in top 10 ({lc['organic']} Organic · {lc['sponsored']} Sponsored · {lc['both']} entrambi)")
            st.markdown(f"**EDITORIA:** {len(ov['clusters'])} opportunità principali da approfondire ({ov['secondary_count']} segnali secondari)")
            for c in ov["clusters"][:10]:
                st.markdown(f"- {c['label']} — {c['score_label']}")
            if len(ov["clusters"]) > 10:
                st.caption(f"… e altre {len(ov['clusters']) - 10} opportunità principali (tab OPPORTUNITÀ EDITORIALI).")
            if ov["brand_count"]:
                st.caption(f"{ov['brand_count']} keyword brand/IP: segnali di mercato (competitor branded query), non proposte editoriali.")
            st.info(U.COMP_NO_DECISION)

        # ---- 2. KEYWORD COMUNI
        with tabs[1]:
            st.markdown("**KEYWORD COMUNI TRA COMPETITOR** — ordine: presenza su più competitor → rank organico → volume → title density / competing products.")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            min_comp = c1.number_input("Presenti in almeno X competitor", min_value=1, max_value=max(ov["total"], 1), value=min(2, max(ov["total"], 1)), key=f"cr_f1_{rid}")
            min_vol = c2.number_input("Volume minimo", min_value=0, value=0, step=50, key=f"cr_f2_{rid}")
            max_rank = c3.number_input("Organic Rank massimo (0 = nessun limite)", min_value=0, value=0, key=f"cr_f3_{rid}")
            spons = c4.selectbox("Sponsored presente", ["Tutti", "Sì", "No"], key=f"cr_f4_{rid}")
            max_td = c5.number_input("Title Density max (0 = nessun limite)", min_value=0, value=0, key=f"cr_f5_{rid}")
            txt = c6.text_input("Testo keyword", key=f"cr_f6_{rid}")
            rows = C.filter_common(agg["keywords"], int(min_comp), int(min_vol) or None, int(max_rank) or None, {"Sì": True, "No": False}.get(spons),
                                   int(max_td) or None, txt)
            st.caption(f"{len(rows)} keyword su {len(agg['keywords'])}")
            st.dataframe(CU.common_rows(rows), use_container_width=True, hide_index=True)
            with st.expander("Segna keyword come BRAND_OR_IP (flag manuale)"):
                pick = st.multiselect("Keyword", [k["keyword_norm"] for k in agg["keywords"]], key=f"cr_brand_{rid}")
                b1, b2 = st.columns(2)
                if b1.button("SEGNA COME BRAND/IP", key=f"cr_b1_{rid}") is True and pick:
                    res = ops.set_brand(ws, rid, pick, True)
                    (st.success if res.ok else st.error)(res.message or "; ".join(res.errors))
                    st.rerun()
                if b2.button("NON È BRAND/IP", key=f"cr_b2_{rid}") is True and pick:
                    res = ops.set_brand(ws, rid, pick, False)
                    (st.success if res.ok else st.error)(res.message or "; ".join(res.errors))
                    st.rerun()
                st.caption("Le keyword brand/IP non vengono mai suggerite né usate per nuovi titoli: restano segnali di mercato.")

        # ---- 3. KEYWORD DI LANCIO (criterio operativo: almeno un competitor con Organic Rank o Sponsored Rank da 1 a 10)
        with tabs[2]:
            lc = ov["launch"]
            st.markdown("**KEYWORD DI LANCIO** — candidate prioritarie: almeno un competitor ha Organic Rank o Sponsored Rank da 1 a 10. "
                        "Ordine: categoria (la genericità la abbassa) → presenza su più competitor → miglior rank → rilevanza verso il libro → volume → title density / competing products.")
            st.caption(f"{lc['any']} keyword in top 10: {lc['organic']} Organic Top 10 · {lc['sponsored']} Sponsored Top 10 · {lc['both']} Organic + Sponsored Top 10.")
            f1, f2, f3, f4, f5 = st.columns(5)
            cats = {"ALL": "Tutte", "BOTH": "Organic + Sponsored Top 10", "ORGANIC": "Organic Top 10", "SPONSORED": "Sponsored Top 10"}
            cat = f1.selectbox("Categoria", list(cats), format_func=lambda c: cats[c], key=f"cr_l1_{rid}")
            mc = f2.selectbox("Competitor", [1, 2, 3], format_func=lambda n_: {1: "tutte", 2: "presenti su almeno 2", 3: "presenti su almeno 3"}[n_], key=f"cr_l2_{rid}")
            lv = f3.number_input("Search Volume minimo", min_value=0, value=0, step=50, key=f"cr_l3_{rid}")
            xg = f4.checkbox("Escludi keyword generiche", key=f"cr_l4_{rid}")
            xb = f5.checkbox("Escludi brand/IP", key=f"cr_l5_{rid}")
            items = C.filter_launch(a["launch"], cat, int(mc), int(lv) or None, bool(xg), bool(xb))
            st.caption(f"{len(items)} keyword su {len(a['launch'])}")
            if not existing:
                st.info("KEYWORD POTENZIALI PER IL FUTURO LANCIO: servono a preparare il lancio del nuovo libro se il progetto verrà approvato. "
                        "Non generano nessuna decisione Amazon Ads.")
            else:
                st.caption("Una keyword nuova in top 10 può diventare TESTA IN FRASE; mai Exact automatico.")
            st.dataframe(CU.launch_rows(items, proj.get("book_title")), use_container_width=True, hide_index=True)
            st.caption("Badge: ORGANIC TOP 10 · SPONSORED TOP 10 · ORGANIC + SPONSORED TOP 10 · TOP 10 · GENERICA (visibile ma con priorità ridotta). "
                       f"{U.COMP_SOURCE}.")

        # ---- 4. KEYWORD GAP
        with tabs[3]:
            if not existing:
                st.info(U.COMP_GAP_NA)
            else:
                st.markdown("**KEYWORD GAP** rispetto ai target, Search Term e negative candidate del libro (dati Amazon Ads reali, solo lettura).")
                labels = list(U.COMP_GAP_LABELS)[:5]
                sel = st.multiselect("Categorie", labels, default=labels, format_func=lambda c: U.COMP_GAP_LABELS[c], key=f"cr_gapcat_{rid}")
                shown = [r for r in a["gap"] if r["gap"]["primary"] in sel or any(c in sel for c in r["gap"]["categories"])]
                st.caption(f"{len(shown)} keyword su {len(a['gap'])}. Le negative effettive di Amazon non sono importate: «già negativa» = candidate negative del motore Search Term.")
                st.dataframe(CU.gap_rows(shown), use_container_width=True, hide_index=True)

        # ---- 4. OPPORTUNITÀ ADS
        with tabs[4]:
            if not existing:
                st.info(U.COMP_GAP_NA)
            else:
                st.markdown("**KEYWORD COMPETITOR DA TESTARE** — solo in FRASE, mai Exact diretto. Non entrano in «Da valutare ora».")
                if not a["ads"]:
                    st.caption("Nessuna keyword competitor rilevante da testare: le keyword forti sono già presenti, negative, brand/IP o sotto soglia.")
                for s in a["ads"] or []:
                    CU.ads_card(st, s)

        # ---- 5. OPPORTUNITÀ EDITORIALI
        with tabs[5]:
            st.markdown("**OPPORTUNITÀ EDITORIALI DERIVATE** — cluster da approfondire, non raccomandazioni a pubblicare.")
            if existing:
                st.caption(f"Ricerca associata a «{proj.get('book_title')}»: i cluster distinti dal libro origine sono NUOVE OPPORTUNITÀ DERIVATE.")
            ed = a["editorial"]
            st.subheader(f"OPPORTUNITÀ PRINCIPALI ({len(ed['primary'])})")
            st.caption("Cluster specifici e azionabili. I cluster generici (es. «Stories», «Mystery») o deboli sono nei SEGNALI SECONDARI, più sotto: non sono cancellati.")
            if not ed["primary"]:
                st.caption("Nessun cluster specifico con almeno 2 keyword correlate.")
            for o in ed["primary"][:15]:
                saved = a["saved"].get(o["cluster_key"])
                CU.opportunity_card(st, o, saved)
                if saved:
                    with st.expander(f"Salva / stato · {o['label']}"):
                        ttl = st.text_input("Titolo provvisorio", value=saved.get("project_title") or o["label"], key=f"cr_t_{saved['opportunity_id']}")
                        note = st.text_area("Note", value=saved.get("notes") or "", key=f"cr_n_{saved['opportunity_id']}")
                        if st.button("SALVA COME PROGETTO EDITORIALE", key=f"cr_s_{saved['opportunity_id']}") is True:
                            res = ops.save_as_project(ws, saved["opportunity_id"], ttl, note)
                            (st.success if res.ok else st.error)(res.message or "; ".join(res.errors))
                            if res.ok:
                                st.rerun()
                        st.caption("Crea solo un progetto di ricerca (stato REVIEW): non aggiunge libri a books.json e non assegna ASIN.")
            rest = ed["primary"][15:]
            if rest or ed["secondary"]:
                with st.expander(f"SEGNALI SECONDARI ({len(rest) + len(ed['secondary'])}) — cluster generici o deboli"):
                    st.caption("Cluster generici standalone (un solo termine centrale o solo pubblico) o con segnale debole. Restano come contesto di mercato, "
                               "non come proposte editoriali principali; un cluster generico non può essere FORTE.")
                    st.dataframe([{"Cluster": o["label"], "Etichetta": o["score_label"], "Punteggio": f"{o['score']:.1f}", "Motivo": o.get("secondary_reason") or "oltre i primi 15",
                                   "Competitor": o["competitor_share"], "Keyword": str(o["keyword_count"]), "Keyword del cluster": ", ".join(o["keywords"][:6])}
                                  for o in rest + ed["secondary"]], use_container_width=True, hide_index=True)
            if ed["brand_signals"]:
                st.subheader("Segnale di mercato / competitor branded query")
                st.caption("Keyword brand/franchise: restano visibili ma non sono proposte come titolo, keyword editoriale primaria né opportunità da replicare.")
                st.dataframe(CU.common_rows(ed["brand_signals"]), use_container_width=True, hide_index=True)
            if ed["unclustered"]:
                with st.expander(f"Keyword non raggruppate ({len(ed['unclustered'])})"):
                    st.dataframe(CU.common_rows(ed["unclustered"]), use_container_width=True, hide_index=True)

        # ---- 6. COMPETITOR
        with tabs[6]:
            st.dataframe(CU.competitor_rows(a["sources"]), use_container_width=True, hide_index=True)
            st.caption("Un file sostituito resta nello storico (stato «sostituito») e non entra più nelle analisi.")
            st.markdown("**Aggiungi o sostituisci un competitor**")
            more = list(st.file_uploader("File CSV Cerebro", type=["csv"], accept_multiple_files=True, key=f"cr_more_{rid}") or [])
            more_files, more_manual = [], {}
            for f in more:
                more_files.append((f.name, f.getvalue()))
                pl = ops.plan_files([(f.name, f.getvalue())])[0]
                if pl["error"]:
                    st.error(f"{f.name}: {pl['error']}")
                elif pl["needs_asin"]:
                    more_manual[f.name] = st.text_input(f"ASIN competitor per «{f.name}»", key=f"cr_masin_{rid}_{f.name}")
            replace = st.checkbox("Sostituisci l'ASIN se già presente (il vecchio file resta nello storico)", key=f"cr_rep_{rid}")
            if st.button("IMPORTA NEL PROGETTO", key=f"cr_add_{rid}", disabled=not more_files) is True:
                res = ops.import_research(ws, proj["book_id"], proj["marketplace"], None, more_files, more_manual, replace=bool(replace), research_id=rid)
                S["cr_flash"] = ([("ok", res.message)] if res.ok else []) + [("err", e) for e in res.errors] + [("warn", w) for w in ((res.data or {}).get("warnings") or [])]
                S["cr_pending_pick"] = rid
                st.rerun()

        # ---- 7. DATI GREZZI
        with tabs[7]:
            st.caption(f"{U.COMP_SOURCE} · valori originali del CSV insieme a quelli normalizzati. Non sono dati Amazon Ads.")
            st.dataframe(CU.raw_rows(a["raw"]), use_container_width=True, hide_index=True)
