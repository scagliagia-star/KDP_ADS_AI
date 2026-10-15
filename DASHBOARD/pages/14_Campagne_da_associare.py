"""Campagne da associare: coda delle campagne Amazon (ID campagna) non ancora collegate a un libro. NON sono errori critici né azioni Ads: è configurazione dei dati.
Un suggerimento (portfolio, nome campagna, storico) è solo una proposta: l'associazione diventa definitiva solo con la tua conferma. Nessuna azione su Amazon Ads."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap  # noqa: F401,E402
import streamlit as st  # noqa: E402

import formatting as F  # noqa: E402
import ui_labels as U  # noqa: E402
from components import ops_ui  # noqa: E402
from kdp_ads import globalreport as G  # noqa: E402
from ops import catalog  # noqa: E402
from ops import global_import as GI  # noqa: E402
from ops import workspace  # noqa: E402

ws = workspace.current()
S = st.session_state
ops_ui.setup(st, U.ASSOC_PAGE_TITLE)
st.caption(U.ASSOC_INTRO)
for kind, text in (S.pop("ca_flash", None) or []):
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(kind, st.info)(text)


def flash_and_rerun(res, ok_text):
    msgs = [("ok" if res.ok else "err", ok_text if res.ok else "; ".join(res.errors))]
    msgs += [("warn", e) for e in (res.errors if res.ok else [])]
    for x in (res.data or {}).get("recalc", []) if isinstance(res.data, dict) else []:
        msgs.append(("ok" if x["ok"] else "warn", f"{x['book_id']} ({x['marketplace']}): {x['message']}"))
    S["ca_flash"] = msgs
    st.rerun()


books = GI.books_dict(ws)
titles = {bid: f"{b.get('title') or bid} · {bid}" for bid, b in books.items()}
cnt = GI.counts(ws)
c = st.columns(3)
c[0].metric("Da associare", F.num(cnt["UNASSIGNED"]))
c[1].metric("Associate", F.num(cnt["ASSIGNED"]))
c[2].metric("Ignorate", F.num(cnt["IGNORED"]))
tabs = st.tabs([f"DA ASSOCIARE ({cnt['UNASSIGNED']})", f"IGNORATE ({cnt['IGNORED']})", f"ASSOCIATE ({cnt['ASSIGNED']})"])


def conf_badge(c_):
    return F.badge(f"Confidenza {c_}", {"alta": "green", "media": "orange"}.get(c_, "grey")) if c_ else ""


def stats_line(m):
    s = m.get("stats")
    if not s:
        return "nessun dato"
    return f"{F.num(s['impressions'])} impressioni · {F.num(s['clicks'])} clic · spesa {F.money(s['spend'])} · {F.num(s['orders'])} acquisti ({s['window_label'].replace('RECENT_', '')})"


# ---------------------------------------------------------------- DA ASSOCIARE
with tabs[0]:
    un = GI.campaign_list(ws, "UNASSIGNED")
    if not un:
        st.success("Nessuna campagna da associare.")
    else:
        st.info(f"{len(un)} campagne nuove non ancora associate. Non è un errore e non è un'azione Ads: le altre campagne sono già state importate normalmente.")
    if books:
        with st.expander("CREA NUOVO LIBRO e associa campagne"):
            with st.form("ca_newbook"):
                f1, f2 = st.columns(2)
                n_title = f1.text_input("Titolo")
                n_asin = f2.text_input("ASIN")
                g1, g2, g3, g4 = st.columns(4)
                n_mp = g1.selectbox("Marketplace", ["US", "UK", "DE", "IT", "FR", "ES", "CA", "AU"], key="ca_nb_mp")
                n_price = g2.text_input("Prezzo")
                n_roy = g3.text_input("Royalty per copia")
                n_target = g4.text_input("ACOS obiettivo (%)", value="35")
                chosen = st.multiselect("Campagne da associare al nuovo libro", [m["amazon_campaign_id"] for m in un],
                                        format_func=lambda x: next((f"{m['campaign_name_last_seen']} · {x}" for m in un if m["amazon_campaign_id"] == x), x), key="ca_nb_c")
                go = st.form_submit_button("CREA LIBRO E ASSOCIA")
            try:
                be = catalog.registry.break_even_calc(catalog.parse_decimal(n_price, "Prezzo"), catalog.parse_decimal(n_roy, "Royalty"))
                st.caption(f"ACOS di pareggio calcolato = royalty / prezzo × 100 = {be:.2f}%")
            except Exception:
                st.caption("ACOS di pareggio = royalty / prezzo × 100 (si calcola dai valori inseriti)")
            if go is True:
                res = GI.create_book_and_associate(ws, {"title": n_title, "book_id": n_asin, "marketplace": n_mp, "price": n_price, "royalty": n_roy, "target_acos": n_target}, chosen)
                flash_and_rerun(res, "Libro creato e campagne associate")
    bf = [x for x in GI.backfill_candidates(ws) if x["level"] == "ALTA"]
    if bf:
        with st.expander(f"BACKFILL SUGGERITO ({len(bf)}): campagne Amazon che corrispondono, con prova, a campagne già presenti nello storico"):
            st.caption("Prova richiesta: almeno 3 target distintivi in comune, il 90% dei target attivi già presenti nella campagna storica, offerte uguali, stesso libro e tipo, nessuna "
                       "ambiguità. Mai per solo tipo o nome. Se confermi, la campagna Amazon eredita lo storico (Lifetime) della campagna storica indicata; se non confermi resta una campagna nuova.")
            for x in bf:
                r1, r2 = st.columns([4, 1])
                r1.markdown(f"**{F.esc(x['amazon_name'])}** · ID `{x['amazon_campaign_id']}` ↔ campagna storica **{F.esc(x['legacy_campaign_id'])}** di "
                            f"{F.esc(titles.get(x['book_id'], x['book_id']))} · target in comune {x['shared']}/{x['amazon_targets']} · offerte uguali {x['bids_equal']}/{x['bids_compared']}",
                            unsafe_allow_html=True)
                if r2.button("CONFERMA BACKFILL", key=f"ca_bf_{x['amazon_campaign_id']}") is True:
                    flash_and_rerun(GI.associate(ws, x["amazon_campaign_id"], x["book_id"], backfill_of=x["legacy_campaign_id"]), "Campagna collegata alla campagna storica")
    by_pf = {}
    for m in un:
        by_pf.setdefault(m["portfolio_last_seen"] or "Senza portfolio", []).append(m)
    for pf, items in by_pf.items():
        sugg = next((m for m in items if m["suggested_book_id"]), None)
        head = f"{pf} · {len(items)} campagne" + (f" · suggerimento: {titles.get(sugg['suggested_book_id'], sugg['suggested_book_id'])}" if sugg else "")
        with st.expander(head):
            if sugg:
                st.markdown(f"Suggerimento: **{F.esc(titles.get(sugg['suggested_book_id'], sugg['suggested_book_id']))}** {conf_badge(sugg['suggestion_confidence'])} — {F.esc(sugg['suggestion_reason'])}",
                            unsafe_allow_html=True)
                st.caption("È solo una proposta: nulla viene associato finché non confermi.")
            if books:
                opts = list(books)
                default = opts.index(sugg["suggested_book_id"]) if sugg and sugg["suggested_book_id"] in opts else 0
                pick = st.selectbox("Libro per tutte le campagne di questo portfolio", opts, index=default, format_func=lambda b: titles[b], key=f"ca_pf_{pf}")
                ok_all = st.checkbox(f"Confermo: associa le {len(items)} campagne a questo libro", key=f"ca_pfok_{pf}")
                if st.button("ASSOCIA TUTTE", key=f"ca_pfgo_{pf}", disabled=not ok_all) is True:
                    res = GI.associate_many(ws, [{"amazon_campaign_id": m["amazon_campaign_id"], "book_id": pick} for m in items])
                    flash_and_rerun(res, f"{res.message}")
            for m in items:
                cid = m["amazon_campaign_id"]
                with st.container(border=True):
                    st.markdown(f"**{F.esc(m['campaign_name_last_seen'] or cid)}** · ID `{cid}` · {F.badge(m['detected_type'] or 'n/d', 'grey')}", unsafe_allow_html=True)
                    st.caption(f"Portfolio: {m['portfolio_last_seen'] or 'nessuno'} · Ad group: {m['ad_group_last_seen'] or 'n/d'} · Date: {m['first_seen'] or 'n/d'} → {m['last_seen'] or 'n/d'} · "
                               f"Report: {', '.join(m['report_labels']) or 'n/d'}")
                    st.caption(stats_line(m))
                    if m["suggested_book_id"]:
                        st.markdown(f"Suggerimento: **{F.esc(titles.get(m['suggested_book_id'], m['suggested_book_id']))}** {conf_badge(m['suggestion_confidence'])}", unsafe_allow_html=True)
                    if books:
                        opts = list(books)
                        d_i = opts.index(m["suggested_book_id"]) if m["suggested_book_id"] in opts else 0
                        b = st.selectbox("Libro", opts, index=d_i, format_func=lambda x: titles[x], key=f"ca_b_{cid}")
                        st.caption("Una campagna Amazon nuova diventa SEMPRE una campagna nuova: due Campaign ID diverse non vengono mai fuse, nemmeno nello stesso libro. "
                                   "Lo storico (Lifetime) di una campagna vecchia si collega solo con un BACKFILL dimostrato e confermato (vedi sopra).")
                        types = ["PHRASE", "EXACT", "COMPETITOR", "OTHER"]
                        ctype = st.selectbox("Tipo campagna (solo per una campagna nuova)", types, index=types.index(m["detected_type"]) if m["detected_type"] in types else 0, key=f"ca_t_{cid}")
                        a1, a2 = st.columns(2)
                        if a1.button("ASSOCIA A LIBRO ESISTENTE", key=f"ca_go_{cid}") is True:
                            flash_and_rerun(GI.associate(ws, cid, b, None, ctype), "Campagna associata")
                        if a2.button("IGNORA", key=f"ca_ig_{cid}") is True:
                            flash_and_rerun(GI.ignore(ws, cid), "Campagna ignorata")
                    else:
                        st.warning("Nessun libro registrato: crea prima un libro.")

# ---------------------------------------------------------------- IGNORATE
with tabs[1]:
    ig = GI.campaign_list(ws, "IGNORED")
    if not ig:
        st.caption("Nessuna campagna ignorata.")
    for m in ig:
        cid = m["amazon_campaign_id"]
        r1, r2 = st.columns([4, 1])
        r1.markdown(f"**{F.esc(m['campaign_name_last_seen'] or cid)}** · ID `{cid}` · {F.esc(m['portfolio_last_seen'] or 'senza portfolio')}", unsafe_allow_html=True)
        if r2.button("RIMUOVI IGNORA", key=f"ca_un_{cid}") is True:
            flash_and_rerun(GI.unignore(ws, cid), "La campagna è di nuovo da associare")
    st.caption("Ignorare non cancella nessun dato: la campagna smette solo di comparire fra quelle da associare.")

# ---------------------------------------------------------------- ASSOCIATE
with tabs[2]:
    asg = GI.campaign_list(ws, "ASSIGNED")
    if not asg:
        st.caption("Nessuna campagna associata.")
    else:
        st.dataframe([{"Libro": titles.get(m["book_id"], m["book_id"]), "Marketplace": m["marketplace"], "Campagna interna": m["campaign_id"], "Tipo": m["campaign_type"],
                       "Nome in Amazon": m["campaign_name_last_seen"], "ID campagna Amazon": m["amazon_campaign_id"], "Portfolio": m["portfolio_last_seen"] or "n/d"} for m in asg],
                     use_container_width=True, hide_index=True)
    st.caption("Un'associazione già confermata non viene cambiata in silenzio.")
