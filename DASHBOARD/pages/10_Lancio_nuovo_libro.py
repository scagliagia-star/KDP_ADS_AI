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
from kdp_ads import launch, launch_keywords  # noqa: E402
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
def kw_rows(items):
    return [{"Keyword": e["keyword"], "Fonte": U.label_kw_sources(e["source_types"]), "Confidenza": U.label_launch_confidence(e["confidence"]),
             "Punteggio": e["evidence_score"], "Motivo": e["reason"]} for e in items]


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
                  "Termini tematici principali": ", ".join(pr["main_terms"]) or F.ND,
                  "Competitor": f"{pr['competitors_valid']} validati, {pr['competitors_unverified']} non verificati, {pr['competitors_with_data']} con dati"})
        for title, key, empty in ((U.LAUNCH_EXACT_SECTION, "exact", "Nessuna keyword abbastanza pertinente per EXACT: non riempio la lista."),
                                  (U.LAUNCH_PHRASE_SECTION, "phrase", "Nessuna keyword PHRASE consigliata."),
                                  (U.LAUNCH_DISCOVERY_SECTION, "discovery", "Nessuna keyword da testare in discovery."),
                                  (U.LAUNCH_REJECTED_SECTION, "rejected", "Nessuna keyword scartata.")):
            st.header(f"{title} ({len(kp[key])})")
            if kp[key]:
                st.dataframe(kw_rows(kp[key]), use_container_width=True, hide_index=True)
            else:
                st.info(empty)
        if kp.get("dropped_candidates"):
            st.caption(f"{kp['dropped_candidates']} frasi derivate dai testi (rumore, frasi di marketing) non sono state elencate.")
        for lim in kp["limits"]:
            st.caption("Limite: " + lim)

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
