"""Lancio nuovo libro: bozza di lancio locale con competitor seed (0-5 ASIN forniti dall'utente). Scrive SOLO LAUNCH\\<bozza>.json.
Nessuna rete, nessuna Amazon, nessuna campagna creata."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import ops_ui  # noqa: E402
from kdp_ads import launch, launch_keywords, launch_research  # noqa: E402
from ops import launch_ops, workspace  # noqa: E402


ws = workspace.current()
S = st.session_state
ops_ui.setup(st, U.LAUNCH_PAGE_TITLE)
st.caption(U.LAUNCH_INTRO)

# ---------------------------------------------------------------- scelta della bozza (la selezione vive in st.session_state)
# kb_launch_pick = valore del selectbox (launch_id o None per "Nuova bozza"); la sua chiave di stato NON si puo' modificare dopo che il widget e'
# stato creato in questa esecuzione: dopo un salvataggio si scrive kb_pending_pick, si fa st.rerun() e qui, PRIMA del widget, lo si applica.
# current_launch_id / selected_launch_id = bozza corrente: la usano "SALVA BOZZA" (aggiorna) e "GENERA PIANO KEYWORD" (stesso file, mai nuove bozze).
plans = launch_ops.list_plans(ws)
NEW = "Nuova bozza"
titles = {p["launch_id"]: f"{p['title'] or '(bozza non leggibile)'} · {p['marketplace'] or F.ND} · {p['launch_id']}" for p in plans}
if "kb_pending_pick" in S:
    S["kb_launch_pick"] = S.pop("kb_pending_pick")
if S.get("kb_launch_pick") not in titles:               # bozza inesistente (file rimosso): torna a "Nuova bozza"
    S["kb_launch_pick"] = None
choice = st.selectbox("Bozza di lancio", [None, *titles], key="kb_launch_pick", format_func=lambda v: NEW if v is None else titles[v])
launch_id = choice
S["selected_launch_id"] = S["current_launch_id"] = launch_id
plan = launch_ops.get_plan(ws, launch_id) if launch_id else None
for p in plans:
    if p["error"]:
        st.warning(f"Bozza {p['launch_id']} non leggibile: {p['error']}")
book = (plan or {}).get("book", {})
comps = (plan or {}).get("competitors", [])
suffix = launch_id or "new"

# ---------------------------------------------------------------- modulo
with st.form(f"form_lancio_{suffix}"):
    title = st.text_input("Titolo", value=book.get("title", ""))
    subtitle = st.text_input("Sottotitolo", value=book.get("subtitle", ""))
    description = st.text_area("Descrizione", value=book.get("description", ""), height=160)
    st.markdown("**Keyword KDP** (fino a 7)")
    kws = book.get("kdp_keywords", [])
    kw_inputs = []
    cols = st.columns(4)
    for i in range(launch.KDP_KEYWORD_SLOTS):
        kw_inputs.append(cols[i % 4].text_input(f"Keyword {i + 1}", value=kws[i] if i < len(kws) else "", key=f"kw_{suffix}_{i}"))
    categories = st.text_area("Categorie (una per riga)", value="\n".join(book.get("categories", [])), height=90)
    c = st.columns(4)
    mp_choices = list(launch.CURRENCY_BY_MARKETPLACE)
    cur_mp = book.get("marketplace") or "US"
    mp_sel = c[0].selectbox("Marketplace", mp_choices, index=mp_choices.index(cur_mp) if cur_mp in mp_choices else 0)
    mp_other = c[1].text_input("Altro marketplace (codice, facoltativo)", value=cur_mp if cur_mp not in mp_choices else "")
    currency = c[2].text_input("Valuta (vuoto = abituale del marketplace)", value=book.get("currency", ""))
    own_asin = c[3].text_input("ASIN del nostro libro (facoltativo)", value=book.get("book_id") or "")
    c = st.columns(2)
    price = c[0].text_input("Prezzo", value="" if book.get("price") is None else str(book["price"]))
    royalty = c[1].text_input("Royalty per copia", value="" if book.get("royalty_per_copy") is None else str(book["royalty_per_copy"]))
    st.markdown(f"**ASIN competitor** (da 0 a {launch.MAX_COMPETITORS}, inseriti da te; stesso marketplace del libro; puoi incollare anche l'URL del prodotto)")
    comp_inputs = []
    for i in range(launch.MAX_COMPETITORS):
        cc = st.columns([2, 3])
        cur = comps[i] if i < len(comps) else {}
        a = cc[0].text_input(f"ASIN competitor {i + 1}", value=cur.get("asin", ""), key=f"ca_{suffix}_{i}")
        n = cc[1].text_input(f"Nota competitor {i + 1}", value=cur.get("note", ""), key=f"cn_{suffix}_{i}")
        comp_inputs.append({"asin": a, "note": n})
    submitted = st.form_submit_button("SALVA BOZZA LANCIO")

if submitted:
    form = {"title": title, "subtitle": subtitle, "description": description, "kdp_keywords": [k for k in kw_inputs if str(k).strip()],
            "categories": categories, "marketplace": (mp_other or "").strip() or mp_sel, "currency": currency, "price": price,
            "royalty_per_copy": royalty, "book_id": own_asin, "competitors": comp_inputs}
    r = launch_ops.save_form(ws, form, launch_id)         # launch_id None = bozza nuova; altrimenti aggiorna ESPLICITAMENTE la stessa
    if r.ok:
        saved = r.data["plan"]["launch_id"]
        S["current_launch_id"] = saved
        S["kb_pending_pick"] = saved                      # la bozza appena salvata resta selezionata dopo il rerun
        S["kb_flash"] = {"ok": True, "msg": f"{r.message}: {saved} (LAUNCH/{saved}.json)", "warnings": (r.data or {}).get("warnings", [])}
        st.rerun()
    for w in (r.data or {}).get("warnings", []):
        st.warning(w)
    for e in r.errors:
        st.error(e)

flash = S.pop("kb_flash", None)                           # esito dell'ultima operazione, mostrato una sola volta dopo il rerun
if flash:
    for w in flash.get("warnings", []):
        st.warning(w)
    (st.success if flash["ok"] else st.error)(flash["msg"])

# ---------------------------------------------------------------- piano keyword Ads iniziale (V2)
def pr_metrics_text(e):
    """Metriche Publisher Rocket realmente presenti per la keyword (nessun dato inventato: se mancano, trattino)."""
    rs = (e.get("research") or {}).get("records") or []
    parts = []
    for r in rs[:1]:
        for k, v in r.items():
            if k in ("source_file", "marketplace", "extra", "competitor_asin") or v in (None, "", []):
                continue
            parts.append(f"{U.label_research_field(k)}: {', '.join(v) if isinstance(v, list) else v}")
    return "; ".join(parts) if parts else F.ND


def kw_rows(items):
    return [{"Keyword": e["keyword"], "Match consigliato": U.label_match(e["recommended_match_type"]),
             "Confidenza": U.label_launch_confidence(e["confidence"]), "Fonti": U.label_kw_sources(e["source_types"]),
             "Pertinenza": e.get("relevance_score", e["evidence_score"]),
             "Mercato": e.get("market_evidence_score"),                                  # vuoto = nessun dato di mercato (mai un valore inventato)
             "Punteggio finale": e.get("final_launch_score", e["evidence_score"]),
             "Metriche Publisher Rocket": pr_metrics_text(e), "Competitor a supporto": ", ".join(e.get("competitor_hits") or []) or F.ND,
             "Motivo": e["reason"]} for e in items]


SHOW_LIMITS = {"exact": 15, "phrase": 30, "discovery": 30, "rejected": 30}      # cosa si vede all'inizio; tutto resta nel piano salvato


def show_dry_run(rep):
    {"DRY_RUN_OK": st.success, "SKIPPED_DUPLICATE": st.warning}.get(rep["status"], st.error)(U.LAUNCH_RESEARCH_STATUS_LABELS.get(rep["status"], rep["status"]))
    for p in rep.get("problems", []):
        st.error(p)
    if rep["status"] in ("UNRECOGNIZED", "ERROR"):
        return
    st.table({"Nome file": rep["file_name"], "Formato": rep["format"] + (f" (foglio {rep['sheet']})" if rep.get("sheet") else ""), "Righe": str(rep["rows"]),
              "Tipo di contenuto": rep["kind"], "Keyword trovate": str(rep["keywords_found"]), "ASIN trovati": str(rep["asins_found"])})
    st.markdown("**Colonne trovate**")
    st.dataframe([{"Colonna nel file": c["header"] or F.ND, "Riconosciuta come": U.label_research_field(c["canonical"]) if c["canonical"] else "non riconosciuta (conservata come campo extra)"}
                  for c in rep["columns"]], use_container_width=True, hide_index=True)
    if rep["keywords_sample"]:
        st.caption("Keyword trovate (prime 10): " + "; ".join(rep["keywords_sample"]))
    if rep["asins_sample"]:
        st.caption("ASIN trovati (primi 10): " + ", ".join(rep["asins_sample"]))
    if rep["metrics_present"]:
        st.markdown("**Metriche presenti** (numero di valori nel file)")
        st.table({U.label_research_field(k): str(v) for k, v in rep["metrics_present"].items()})
    for c in rep["competitors_enriched"]:
        st.info(f"{U.LAUNCH_RESEARCH_ENRICHED}: {c['asin']}" + (f" ({c['title']})" if c.get("title") else ""))
    for c in rep["competitor_candidates"]:
        st.warning(f"{U.LAUNCH_RESEARCH_CANDIDATE}: {c['asin']}" + (f" ({c['title']})" if c.get("title") else "") + f" — {U.LAUNCH_RESEARCH_CANDIDATE_NOTE}")
    kp = rep["keyword_preview"]
    st.markdown(f"**Keyword pertinenti: {kp['pertinent']} · scartate: {kp['discarded']}** (anteprima rispetto al tema del libro)")
    if kp["pertinent_sample"]:
        st.caption("Pertinenti: " + "; ".join(f"{x['keyword']} → {x['match']}" for x in kp["pertinent_sample"]))
    if kp["discarded_sample"]:
        st.caption("Scartate: " + "; ".join(x["keyword"] for x in kp["discarded_sample"]))
    for w in rep.get("warnings", []):
        st.warning(w)


# ---------------------------------------------------------------- ricerca competitor / Publisher Rocket
if plan:
    st.divider()
    st.header(U.LAUNCH_RESEARCH_SECTION)
    st.caption(U.LAUNCH_RESEARCH_INTRO)
    cur = S["current_launch_id"]
    n_up = S.get("kb_res_n", 0)
    uploads = st.file_uploader(U.LAUNCH_RESEARCH_UPLOAD, type=["csv", "xlsx", "xls"], accept_multiple_files=True, key=f"kb_res_file_{suffix}_{n_up}")
    files = [(f.name, f.getvalue()) for f in (uploads or [])]
    if files:
        st.caption(U.LAUNCH_RESEARCH_BATCH_NOTE)
        # impronta del lotto: il dry run vale solo per QUESTI file e QUESTA bozza (se cambi i file va rifatto); sopravvive ai rerun
        key = [cur, sorted((n, launch_research.sha256(d)) for n, d in files)]
        if st.button(U.LAUNCH_RESEARCH_DRY, key=f"btn_res_dry_{suffix}"):
            S["kb_res_batch"] = {"key": key, "rows": launch_ops.research_batch_dry_run(ws, cur, files).data["rows"]}
        batch = S.get("kb_res_batch")
        batch = batch if (batch and batch["key"] == key) else None
        n_valid = sum(1 for r in batch["rows"] if r["status"] == "DRY_RUN_OK") if batch else 0
        if batch:
            st.dataframe([{"File": r["file_name"], "Formato": r["format"] or F.ND, "Righe": r["rows"] if r["rows"] is not None else F.ND,
                           "Keyword trovate": r["keywords_found"], "ASIN trovati": r["asins_found"],
                           "Metriche riconosciute": ", ".join(U.label_research_field(m) for m in r["metrics"]) or F.ND,
                           "Stato": U.LAUNCH_RESEARCH_BATCH_STATUS.get(r["status"], r["status"]), "Errore": r["error"] or ""} for r in batch["rows"]],
                         use_container_width=True, hide_index=True)
            for r in batch["rows"]:
                if r["status"] == "DRY_RUN_OK":
                    with st.expander(f"Dettaglio dry run: {r['file_name']}"):
                        show_dry_run(r["report"])
            st.markdown(f"**File validi: {n_valid} su {len(batch['rows'])}**")
        else:
            st.info("Esegui il dry run: l'import è possibile solo dopo un dry run, e solo per i file validi.")
        if st.button(U.LAUNCH_RESEARCH_IMPORT, key=f"btn_res_import_{suffix}", disabled=not n_valid):
            r = launch_ops.import_research_batch(ws, cur, files)
            if r.ok:
                S["kb_res_summary"] = {"launch_id": cur, **r.data["summary"]}
                S["kb_flash"] = {"ok": True, "msg": f"{r.message} ({r.data['summary']['files_imported']} su {r.data['summary']['files_loaded']}) in LAUNCH/{cur}/research/", "warnings": []}
                S.pop("kb_res_batch", None)
                S["kb_res_n"] = n_up + 1                                                          # svuota il caricamento file
                st.rerun()
            for e in r.errors:
                st.error(e)
    summ = S.get("kb_res_summary")
    if summ and summ.get("launch_id") == cur:                                                     # riepilogo dell'ultimo import, resta dopo il rerun
        st.markdown("**Riepilogo ultimo import**")
        c = st.columns(4)
        c[0].metric("File caricati", summ["files_loaded"])
        c[1].metric("File validi", summ["files_valid"])
        c[2].metric("File importati", summ["files_imported"])
        c[3].metric("File con errore", summ["errors"])
        c = st.columns(4)
        c[0].metric("Duplicati nel lotto", summ["duplicates"])
        c[1].metric("Già importati", summ["already_imported"])
        c[2].metric("Keyword totali trovate", summ["keywords_total"])
        c[3].metric("Keyword uniche", summ["keywords_unique"])
        c = st.columns(2)
        c[0].metric(U.LAUNCH_RESEARCH_ENRICHED + " (n.)", len(summ["competitors_enriched"]))
        c[1].metric(U.LAUNCH_RESEARCH_CANDIDATE + " (n.)", len(summ["competitors_candidates"]))
        st.dataframe([{"File": f["file_name"], "Stato": U.LAUNCH_RESEARCH_BATCH_STATUS.get(f["status"], f["status"]), "Keyword trovate": f["keywords_found"],
                       "Errore": f["error"] or ""} for f in summ["files"]], use_container_width=True, hide_index=True)
    ov = launch_ops.research_overview(ws, cur)
    if ov:
        st.markdown("**File importati nella bozza**")
        st.dataframe([{"File": f["file_name"], "Formato": f["format"], "Righe": f["rows"], "Keyword": f["keywords"], "ASIN": f["competitors"],
                       "Importato il": f["imported_at"], "Salvato come": f"research/{f['stored_as']}"} for f in ov["files"]],
                     use_container_width=True, hide_index=True)
        c = st.columns(3)
        c[0].metric("Keyword trovate", ov["keywords_total"])
        c[1].metric("Keyword pertinenti", ov["keywords_pertinent"])
        c[2].metric("Keyword scartate", ov["keywords_discarded"])
        if ov["metrics_present"]:
            st.caption("Metriche disponibili: " + ", ".join(f"{U.label_research_field(k)} ({v})" for k, v in ov["metrics_present"].items()))
        for e in ov["enriched"]:
            st.success(f"{U.LAUNCH_RESEARCH_ENRICHED}: {e['asin']}" + (f" ({e['title']})" if e.get("title") else ""))
        for e in ov["candidates"]:
            st.warning(f"{U.LAUNCH_RESEARCH_CANDIDATE}: {e['asin']}" + (f" ({e['title']})" if e.get("title") else "") + f" — {U.LAUNCH_RESEARCH_CANDIDATE_NOTE}")


if plan:
    st.divider()
    st.caption(U.LAUNCH_KW_INTRO)
    if st.button(U.LAUNCH_KW_BUTTON, key=f"btn_kwplan_{suffix}"):
        r = launch_ops.generate_keyword_plan(ws, S["current_launch_id"])        # la bozza corrente: stesso file, nessuna bozza nuova
        if r.ok:
            S["kb_flash"] = {"ok": True, "msg": f"{r.message} (LAUNCH/{S['current_launch_id']}.json)", "warnings": []}
            st.rerun()                                                          # la selezione resta (kb_launch_pick) e il piano si ricarica dal file
        else:
            for e in r.errors:
                st.error(e)
    kp = plan.get("keyword_plan")
    if kp:
        if launch_keywords.is_stale(plan):
            st.warning(U.LAUNCH_STALE_NOTE)
        st.caption(f"Piano generato il {kp['generated_at']} · regole {kp['ruleset']} · "
                   + ("dati competitor presenti" if kp["competitor_data_available"] else "nessun dato competitor reale"))
        pr = kp["profile"]
        st.header(U.LAUNCH_PROFILE_SECTION)
        st.table({"Titolo": pr["title"], "Sottotitolo": pr["subtitle"] or F.ND, "Marketplace": f"{pr['marketplace']} · {pr['currency']}",
                  "Prezzo / royalty": f"{pr['price']} / {pr['royalty_per_copy']}",
                  "ACOS di pareggio (royalty ÷ prezzo)": f"{pr['break_even_acos']}%" if pr["break_even_acos"] is not None else F.ND,
                  "Categorie": "; ".join(pr["categories"]) or F.ND, "Keyword KDP": "; ".join(pr["kdp_keywords"]) or F.ND,
                  "Tema del titolo (TITLE_TOPIC)": ", ".join(pr.get("title_topic") or []) or F.ND,
                  "Tema commerciale di ricerca (COMMERCIAL_SEARCH_TOPIC)": ", ".join(pr.get("commercial_search_topic") or pr["main_terms"]) or F.ND,
                  "Titolo creativo": "sì: le keyword si valutano sul tema commerciale, non sulle parole del titolo" if pr.get("title_creative") else "no",
                  "Competitor": f"{pr['competitors_valid']} validati, {pr['competitors_unverified']} non verificati, {pr['competitors_with_data']} con dati"})
        # ---------------------------------------------------------- strategia di lancio (preset PHRASE_FIRST_AUTO)
        st.header(U.LAUNCH_STRATEGY_SECTION)
        ls = kp.get("launch_strategy")
        if not ls:
            st.info("Questo piano è stato generato prima della strategia di lancio: premi GENERA PIANO KEYWORD per aggiornarlo.")
        else:
            st.caption(U.LAUNCH_STRATEGY_INTRO)
            st.table({"Preset": ls["preset"], "Campagna iniziale consigliata": " + ".join(ls["campaigns_at_launch"]),
                      "Campagna EXACT": "NON necessaria al lancio: " + ls["exact"]["status"]})
            cur_sym = (kp["profile"].get("currency") or "")
            st.subheader(f"{U.LAUNCH_STRATEGY_PHRASE} ({ls['phrase']['count']})")
            st.caption(ls["phrase"]["note"])
            if ls["phrase_launch"]:
                st.dataframe([{"Keyword": x["keyword"], "Classificazione analitica": x["analytical_classification"],
                               "Match di lancio consigliato": x["recommended_launch_match"], "Pertinenza": x["relevance_score"],
                               "Mercato": x["market_evidence_score"], "Punteggio finale": x["final_launch_score"],
                               "Volume": F.ND if x["volume"] is None else str(x["volume"]),
                               "Competizione": x["competition"], "Competitor a supporto": ", ".join(x["competitor_support"]) or F.ND,
                               "Bid suggerito": f"{x['suggested_bid']:.2f} {cur_sym}".strip(), "Fascia": U.LAUNCH_STRATEGY_TIERS.get(x["tier"], x["tier"]),
                               "Motivo": x["reason"]} for x in ls["phrase_launch"]], use_container_width=True, hide_index=True)
                st.text_area(U.LAUNCH_STRATEGY_COPY, value=ls["phrase"]["copy_text"], height=min(520, 40 + 24 * ls["phrase"]["count"]),
                             key=f"kb_copy_phrase_{suffix}")
            else:
                st.info("Nessuna keyword supera le soglie di lancio: non riempio la lista.")
            st.subheader(U.LAUNCH_STRATEGY_AUTO)
            a = ls["auto"]
            st.table({"Consigliata": "Sì" if a["recommended"] else "No", "Keyword richieste": "nessuna",
                      "Bid iniziale suggerito (più prudente)": f"{a['suggested_bid']:.2f} {cur_sym}".strip(), "Motivo": a["reason"]})
            st.subheader(U.LAUNCH_STRATEGY_EXACT)
            st.info(f"{U.LAUNCH_STRATEGY_EXACT_TEXT}. {ls['exact']['how']}")
            st.caption("Pagina collegata: Opportunità Search Term (azione «Valuta promozione in EXACT»).")
            with st.expander(f"Varianti note e keyword escluse dal lancio ({len(ls['excluded'])})"):
                st.dataframe([{"Keyword": x["keyword"], "Tipo": x["kind"], "Analisi": x.get("analytical_classification"), "Motivo": x["reason"]}
                              for x in ls["excluded"][:300]], use_container_width=True, hide_index=True)
            for n in ls["notes"]:
                st.caption("Nota: " + n)
        st.caption(U.LAUNCH_SCORES_NOTE)
        show_all = st.checkbox(U.LAUNCH_SHOW_ALL, key=f"kb_show_all_{suffix}")
        for title, key, empty in ((U.LAUNCH_EXACT_SECTION, "exact", "Nessuna keyword abbastanza pertinente per EXACT: non riempio la lista."),
                                  (U.LAUNCH_PHRASE_SECTION, "phrase", "Nessuna keyword PHRASE consigliata."),
                                  (U.LAUNCH_DISCOVERY_SECTION, "discovery", "Nessuna keyword da testare in discovery."),
                                  (U.LAUNCH_REJECTED_SECTION, "rejected", "Nessuna keyword scartata.")):
            items = kp.get(key) or []
            shown = items if show_all else items[:SHOW_LIMITS[key]]
            st.header(f"{title} ({len(items)})")
            if items:
                st.dataframe(kw_rows(shown), use_container_width=True, hide_index=True)
                if len(shown) < len(items):
                    st.caption(U.LAUNCH_SHOW_LIMIT_NOTE.format(n=len(shown), tot=len(items)))
            else:
                st.info(empty)
        if kp.get("title_queries"):
            st.header(f"{U.LAUNCH_TITLE_QUERY_SECTION} ({len(kp['title_queries'])})")
            st.caption(U.LAUNCH_TITLE_QUERY_NOTE)
            st.dataframe(kw_rows(kp["title_queries"]), use_container_width=True, hide_index=True)
        if kp.get("dropped_candidates"):
            st.caption(f"{kp['dropped_candidates']} frasi derivate dai testi (frammenti incompleti, frasi di marketing) non sono state elencate.")
        for lim in kp["limits"]:
            st.caption("Nota: " + lim)

# ---------------------------------------------------------------- sezioni di sola visualizzazione sulla bozza salvata
if plan:
    st.divider()
    st.header(U.LAUNCH_COMPETITOR_SECTION)
    st.caption(U.LAUNCH_COMPETITOR_NOTE)
    if comps:
        st.dataframe([{"ASIN": c["asin"], "Fonte": U.LAUNCH_SOURCE_LABELS.get(c["source"], c["source"]) + f" ({c['source']})",
                       "Marketplace": c["marketplace"], "Nota": c.get("note") or "-", "Stato": c["status"]} for c in comps],
                     use_container_width=True, hide_index=True)
        unverified = [c["asin"] for c in comps if c["status"] == launch.STATUS_UNVERIFIED]
        if unverified:
            st.warning("Formato non riconosciuto (NON VERIFICATO), esclusi da struttura consigliata ed evidenza: " + ", ".join(unverified))
    else:
        st.info("Nessun competitor inserito (da 0 a 5 sono ammessi).")
    st.caption(U.LAUNCH_PROVIDER_NOTE)

    st.header(U.LAUNCH_STRUCTURE_SECTION)
    kp = plan.get("keyword_plan")
    if kp:
        cp = kp["campaigns"]
        st.table({"EXACT": f"{len(cp['EXACT']['keywords'])} keyword consigliate" if cp["EXACT"]["keywords"] else "nessuna keyword abbastanza pertinente",
                  "PHRASE": f"{len(cp['PHRASE']['keywords'])} keyword consigliate" if cp["PHRASE"]["keywords"] else "nessuna keyword consigliata",
                  "AUTO": "sempre valutabile come discovery iniziale, nessuna keyword richiesta",
                  "COMPETITOR": ("ASIN validati: " + ", ".join(cp["COMPETITOR"]["asins"])) if cp["COMPETITOR"]["available"]
                  else "non proposta: nessun ASIN competitor validato"})
        st.caption("Struttura consigliata, non operativa: nessuna campagna viene creata su Amazon.")
    suggestions = launch.recommended_structure(plan)
    if suggestions:
        for s in suggestions:
            st.info(f"**{U.LAUNCH_COMPETITOR_CAMPAIGN}** — ASIN forniti dall'utente: " + ", ".join(s["asins"])
                    + ". Suggerimento non operativo: nessuna campagna viene creata.")
    else:
        st.info("Nessun suggerimento strutturale: servono ASIN competitor VALIDATO.")

    st.header(U.LAUNCH_EVIDENCE_SECTION)
    st.caption(U.LAUNCH_EVIDENCE_NOTE)
    ev = launch.evidence_table(plan)
    if ev:
        st.dataframe([{"Keyword KDP": e["keyword"], "Compare in": U.label_launch_sources(e["own_sources"]),
                       "Competitor": (", ".join(e["competitor_hits"]) or "nessuno") if e["competitor_data_available"] else "dati non disponibili",
                       "Livello di evidenza": U.label_launch_level(e["level"]) + (" (bonus competitor)" if e["competitor_boost"] else "")} for e in ev],
                     use_container_width=True, hide_index=True)
    else:
        st.info("Nessuna keyword KDP inserita.")
