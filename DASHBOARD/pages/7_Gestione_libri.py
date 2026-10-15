"""Gestione libri (V2): LIBRO -> MARKETPLACE -> CAMPAGNE. Elenco, aggiungi libro, aggiungi marketplace al libro, aggiungi campagna.
Scrive solo books.json e le cartelle INPUT tramite la logica gia' esistente (kdp_ads.registry). Nessuna rete, nessuna Amazon.
Ogni marketplace ha valuta, prezzo, royalty e ACOS propri: nessuna conversione di valuta."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import ops_ui  # noqa: E402
from kdp_ads import registry  # noqa: E402
from ops import catalog, workspace  # noqa: E402
from ops import global_import as GI  # noqa: E402

ws = workspace.current()
S = st.session_state
ops_ui.setup(st, "Gestione libri")

# ---------------------------------------------------------------- A. elenco libri (una riga per libro e marketplace)
st.subheader("Libri esistenti")
books = catalog.list_books(ws)
n_assoc = GI.book_campaign_counts(ws)                   # campagne Amazon (ID) associate a ciascun libro dai report globali
if not books:
    st.info("Nessun libro registrato: aggiungine uno qui sotto.")
else:
    rows = []
    for b in books:
        for m in (b["marketplaces"] or [{}]):
            rows.append({"Titolo": b["title"], "Book ID / ASIN": b["book_id"], "Marketplace": m.get("marketplace") or "—",
                         "Valuta": m.get("currency") or F.ND, "Prezzo": F.money(m.get("price")) if m else F.ND,
                         "Royalty per copia": F.money(m.get("royalty_per_copy")) if m else F.ND,
                         "ACOS obiettivo": F.pct(m.get("target_acos")), "ACOS di pareggio": F.pct(m.get("break_even_acos")),
                         "Fase": m.get("phase") or F.ND, "Campagne registrate": m.get("campaigns", 0),
                         "Campagne Amazon associate": n_assoc.get(b["book_id"], 0)})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    with st.expander("Gestisci campagne associate (report globali Amazon Ads)"):
        pick = st.selectbox("Libro", [b["book_id"] for b in books], format_func=lambda x: next((f"{b['title']} · {x}" for b in books if b["book_id"] == x), x), key="gl_camp_book")
        assoc = GI.book_campaigns(ws, pick)
        if not assoc:
            st.caption("Nessuna campagna Amazon associata a questo libro.")
        else:
            st.markdown(f"**Campagne associate: {len(assoc)}**")
            st.dataframe([{"ID campagna Amazon": a["amazon_campaign_id"], "Campagna interna": a["campaign_id"], "Nome in Amazon": a["campaign_name_last_seen"],
                           "Portfolio": a["portfolio_last_seen"] or F.ND, "Tipo": a["campaign_type"], "Marketplace": a["marketplace"], "Stato": a["status"]} for a in assoc],
                         use_container_width=True, hide_index=True)
        try:
            st.page_link("pages/14_Campagne_da_associare.py", label="Vai a «Campagne da associare»")
        except Exception:
            st.caption("Apri «Campagne da associare» dal menu.")
    st.caption("Ogni marketplace ha economia e campagne separate: i dati di marketplace diversi non vengono mai sommati né confrontati, "
               "e non c'è nessuna conversione di valuta.")

# ---------------------------------------------------------------- B. aggiungi libro (titolo + Book ID)
st.subheader("Aggiungi nuovo libro")
with st.form("form_nuovo_libro"):
    title = st.text_input("Titolo")
    book_id = st.text_input("Book ID / ASIN")
    add_book = st.form_submit_button("AGGIUNGI LIBRO")
st.caption("Il libro ha solo titolo e Book ID/ASIN. Prezzo, royalty e ACOS si impostano per ogni marketplace, nel passo successivo.")
if add_book:
    r = catalog.add_book_shell(ws, {"title": title, "book_id": book_id})
    if r.ok:
        st.success(U.OPS_BOOK_ADDED)
        st.caption(f"Libro {r.data['book_id']} · cartella INPUT\\{r.data['book_id']} creata. "
                   "Passo successivo: aggiungi un marketplace al libro qui sotto.")
    else:
        for e in r.errors:
            st.error(e)

# ---------------------------------------------------------------- C. aggiungi marketplace al libro
st.subheader("Aggiungi marketplace al libro")
books = catalog.list_books(ws)
if not books:
    st.info("Aggiungi prima un libro.")
else:
    labels = {f"{b['title']} · {b['book_id']}": b["book_id"] for b in books}
    mp_book = labels[st.selectbox("Libro", list(labels), key="gl_mp_book")]
    with st.form("form_nuovo_marketplace"):
        c = st.columns(3)
        mp_code = c[0].selectbox("Marketplace", list(catalog.MARKETPLACE_CHOICES))
        mp_other = c[1].text_input("Altro marketplace (codice, facoltativo)")
        currency = c[2].text_input("Valuta", value="USD")
        c = st.columns(4)
        price = c[0].text_input("Prezzo")
        royalty = c[1].text_input("Royalty per copia")
        target = c[2].text_input("ACOS obiettivo (%)", value="35")
        phase = c[3].selectbox("Fase libro", list(registry.PHASES), index=list(registry.PHASES).index("PROFIT_STABLE"))
        add_mp = st.form_submit_button("AGGIUNGI MARKETPLACE")
    st.caption("Valute abituali: US→USD, UK→GBP, DE/IT/FR/ES→EUR, CA→CAD, AU→AUD. Prezzo e royalty sono nella valuta del marketplace; "
               "l'ACOS di pareggio è calcolato (royalty ÷ prezzo).")
    if add_mp:
        r = catalog.add_marketplace(ws, {"book_id": mp_book, "marketplace": (mp_other or "").strip() or mp_code, "currency": currency,
                                         "price": price, "royalty": royalty, "target_acos": target, "phase": phase})
        if r.ok:
            st.success("Marketplace aggiunto correttamente")
            st.caption(f"{r.data['marketplace']} · {r.data['currency']} · ACOS di pareggio {F.pct(r.data['break_even_acos'])}. "
                       "Passo successivo: aggiungi le campagne di questo marketplace.")
        else:
            for e in r.errors:
                st.error(e)

# ---------------------------------------------------------------- D. aggiungi campagna: libro -> marketplace -> campagna
st.subheader("Aggiungi campagna")
books = catalog.list_books(ws)
if not books:
    st.info("Aggiungi prima un libro.")
else:
    labels = {f"{b['title']} · {b['book_id']}": b["book_id"] for b in books}
    sel = labels[st.selectbox("Libro", list(labels), key="gl_book")]
    mps = catalog.list_marketplaces(ws, sel)
    if not mps:
        st.info("Questo libro non ha ancora marketplace: aggiungine uno qui sopra. Non si può creare una campagna senza marketplace.")
    else:
        mlabels = {f"{m['marketplace']} · {m['currency']}": m["marketplace"] for m in mps}
        sel_mp = mlabels[st.selectbox("Marketplace", list(mlabels), key="gl_camp_mp")]
        with st.form("form_nuova_campagna"):
            cid = st.text_input("ID campagna (nome della cartella, es. LIBRO_EXACT; unico nel libro)")
            cname = st.text_input("Nome leggibile (facoltativo)")
            ctype = st.selectbox("Tipo", list(catalog.CAMPAIGN_TYPES))
            add_camp = st.form_submit_button("AGGIUNGI CAMPAGNA")
        fields = {"book_id": sel, "marketplace": sel_mp, "campaign_id": cid, "campaign_name": cname, "campaign_type": ctype}
        if add_camp:
            r = catalog.add_campaign(ws, fields)
            if r.ok:
                S.pop("gl_pending", None)
                st.success(U.OPS_CAMPAIGN_ADDED)
                st.caption(f"Cartella creata: {r.data}. Passo successivo: pagina 'Importa report'.")
            elif r.data and r.data.get("duplicate"):
                S["gl_pending"] = fields
                st.error(r.errors[0])
            else:
                for e in r.errors:
                    st.error(e)
        pending = S.get("gl_pending")
        if pending and pending.get("book_id") == sel and pending.get("marketplace") == sel_mp:
            st.warning(f"La campagna {pending['campaign_id']} esiste già. Non la sovrascrivo senza conferma: confermando si aggiornano "
                       "solo nome e tipo (gli snapshot già dichiarati restano).")
            if st.button("CONFERMA AGGIORNAMENTO CAMPAGNA", key="gl_confirm"):
                r = catalog.add_campaign(ws, pending, overwrite=True)
                S.pop("gl_pending", None)
                if r.ok:
                    st.success("Campagna aggiornata correttamente")
                else:
                    for e in r.errors:
                        st.error(e)
    camps = catalog.list_campaigns(ws, sel)
    st.markdown("**Campagne registrate per questo libro, per marketplace**")
    if camps:
        st.dataframe([{"Marketplace": c["marketplace"] or F.ND, "ID campagna": c["campaign_id"], "Nome": c["campaign_name"],
                       "Tipo": c["campaign_type"], "Snapshot dichiarati": c["snapshots"], "File CSV": c["csv_files"],
                       "Metadata": "ok" if c["valid"] else "non valido"} for c in camps], use_container_width=True, hide_index=True)
    else:
        st.info("Nessuna campagna registrata per questo libro.")
