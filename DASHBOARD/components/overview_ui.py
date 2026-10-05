"""Rendering della PANORAMICA operativa (solo presentazione, nessun pulsante di azione). Tutti i testi sono in italiano con la terminologia di
Amazon Ads; i codici tecnici compaiono SOLO in PERCHÉ? > DETTAGLIO TECNICO. Le collezioni arrivano da data_access.overview.build_overview."""
import formatting as F
import ui_labels as U
from data_access import overview as O


def _acos(v):
    return F.pct(v) if v is not None else F.ND


def _clean(text):
    return O.plain_text(U.label_reason(text))


# ------------------------------------------------------------------ KPI
def kpis(st, ov):
    k = ov["kpi"]
    keys = ("actions", "exact", "negatives", "paused", "warnings", "delivery") + (("conflicts",) if k.get("conflicts") else ())
    c = st.columns(len(keys))
    for col, key in zip(c, keys):
        col.metric(U.OV_KPI[key], F.ND if k[key] is None else k[key])
    if not ov["has_actionable_field"]:
        st.caption("Run precedente all'introduzione del campo «azionabile ora»: alcuni conteggi non sono disponibili.")
    if ov["exact"] is not None and len(ov["exact"]) > O.MAX_ITEMS:
        st.caption(f"Opportunità EXACT: {len(ov['exact'])} in totale, qui sotto le prime {O.MAX_ITEMS}.")
    if ov["negatives"] is not None and len(ov["negatives"]) > O.MAX_ITEMS:
        st.caption(f"Candidati negativi: {len(ov['negatives'])} in totale, qui sotto i primi {O.MAX_ITEMS}.")


def _see_all(st):
    try:
        st.page_link("pages/9_Opportunita_Search_Term.py", label=U.OV_SEE_ALL_OPP)
    except Exception:                                    # pagina non registrata (es. esecuzione isolata): testo semplice
        st.caption(U.OV_SEE_ALL_OPP + " (menu a sinistra)")


# ------------------------------------------------------------------ PERCHÉ? (dettaglio completo; i codici solo in fondo)
def _metrics(st, title, d, prefix):
    st.markdown(f"**{title}**")
    c = st.columns(5)
    c[0].metric("Clic", F.num(d.get(f"{prefix}_clicks")))
    c[1].metric("Acquisti", F.num(d.get(f"{prefix}_orders")))
    c[2].metric("Spesa", F.money(d.get(f"{prefix}_spend")))
    c[3].metric("Vendite", F.money(d.get(f"{prefix}_sales")))
    c[4].metric("ACOS", F.acos(d.get(f"{prefix}_acos")) if d.get(f"{prefix}_clicks") is not None else F.ND)


def why(st, d, book):
    """Expander PERCHÉ?: motivazione completa in italiano + periodi + erogazione + tra campagne + Search Term figli, poi DETTAGLIO TECNICO."""
    with st.expander(U.OV_WHY):
        st.markdown("**Motivazione completa**")
        for part in [p.strip() for p in _clean(d.get("reason")).split(" | ") if p.strip()]:
            st.markdown(f"- {part}")
        st.markdown(f"**Storico completo** · profitto stimato {F.money(d.get('lifetime_estimated_profit'))} (stima)")
        _metrics(st, U.label_window("LIFETIME"), d, "lifetime")
        _metrics(st, f"{U.label_window(d.get('recent_window') or 'RECENT_65D')} · {d.get('recent_period') or F.ND}", d, "recent")
        _metrics(st, f"{U.label_window(d.get('tactical_window') or 'RECENT_30D')} · {d.get('tactical_period') or F.ND}", d, "tactical")
        st.markdown(f"**Erogazione:** {U.label_delivery(d.get('delivery_signal'))} · {U.label_delivery_source(d.get('delivery_source'))}"
                    + (f" · rapporto ×{d['delivery_ratio']:.2f}" if d.get("delivery_ratio") is not None else ""))
        cross = d.get("cross_campaign_overlap")
        st.markdown(f"**Tra campagne:** {_clean(cross) if cross else 'nessuna sovrapposizione'}")
        if d.get("child_terms_note"):
            st.markdown(f"**Termini di ricerca collegati:** {O.plain_text(d['child_terms_note'])}")
        st.markdown(f"**Osservazione Amazon:** {O.plain_text(d.get('amazon_recommendation')) or F.ND} · "
                    f"{U.label_agreement(d.get('recommendation_agreement'))}")
        st.markdown(f"**{U.OV_TECH}**")
        lines = [f"regola: {d.get('decision_rule') or F.ND}", f"azione finale: {d.get('final_action') or F.ND}",
                 f"azione potenziale: {d.get('underlying_candidate_action') or F.ND}", f"segnalazioni: {d.get('flags') or F.ND}",
                 f"segnale erogazione: {d.get('delivery_signal') or F.ND} ({d.get('delivery_source') or F.ND})",
                 f"stato target: {d.get('target_status') or F.ND}", f"tipo target: {d.get('target_type') or F.ND}", f"target: {d.get('target')}",
                 f"id decisione: {d.get('decision_id') or F.ND}", f"affidabilità: {d.get('confidence') or F.ND}", f"priorità: {d.get('priority') or F.ND}",
                 f"motivo (con i codici del motore): {U.label_reason(d.get('reason'))}"]
        st.code("\n".join(lines), language=None)


# ------------------------------------------------------------------ COSA FARE OGGI
def action_card(st, c, book):
    d = c["decision"]
    with st.container(border=True):
        st.markdown(f"## {c['headline']}")
        st.markdown(f"**{F.esc(c['title'])}** · {U.label_priority(c['priority']) if c['priority'] else F.ND}")
        if c["bid"]:
            b = c["bid"]
            m = st.columns(3)
            m[0].metric("Offerta attuale", F.money(b["current"]))
            m[1].metric("Offerta suggerita", F.money(b["suggested"]))
            m[2].metric("Variazione", F.bid_change(b["change_pct"]))
            st.caption(f"{F.money(b['current'])} → {F.money(b['suggested'])} ({F.bid_change(b['change_pct'])}). Suggerimento: non viene applicato nulla su Amazon Ads.")
        st.markdown(f"**Motivo:** {c['reason']}")
        a = c["acos"]
        m = st.columns(6)
        m[0].metric("ACOS obiettivo", _acos(a["target"]))
        m[1].metric("ACOS di pareggio", _acos(a["break_even"]))
        m[2].metric("ACOS storico completo", _acos(a["lifetime"]))
        m[3].metric(f"ACOS ultimi {a['recent_days']} giorni", _acos(a["recent"]))
        m[4].metric(f"ACOS ultimi {a['tactical_days']} giorni", _acos(a["tactical"]))
        m[5].metric("Affidabilità", U.label_confidence(c["confidence"]))
        if c.get("underlying"):
            st.caption(f"Azione potenziale conservata: {O.simple_action({'final_action': c['underlying']})['word'].lower()}.")
        why(st, d, book)


def today(st, ov, book):
    st.header(U.OV_TODAY)
    k = ov["kpi"]
    parts = [f"{n} × {w}" for w, n in sorted(ov["actions_by_word"].items())]
    if k["exact"]:
        parts.append(f"{k['exact']} keyword da mettere in EXACT")
    if k["negatives"]:
        parts.append(f"{k['negatives']} da valutare come negative")
    if k.get("conflicts"):
        parts.append(f"{k['conflicts']} conflitti da verificare")
    if k["delivery"]:
        parts.append(f"{k['delivery']} target da verificare per l'erogazione")
    st.caption("In sintesi: " + (" · ".join(parts) if parts else "nessuna azione oggi."))
    if not ov["has_actionable_field"]:
        st.info("Questo run è precedente al campo «azionabile ora»: la sezione non è disponibile per questo run.")
    elif not ov["actions"]:
        st.success("Nessuna decisione da valutare ora su offerte o pause in questo run.")
    for c in ov["actions"]:
        action_card(st, c, book)


# ------------------------------------------------------------------ opportunita' Search Term
def _opp_line(st, i):
    st.markdown(f"{F.badge(i['badge'], 'green' if i['kind'] == 'EXACT' else 'red')} **{F.esc(i['term'])}**", unsafe_allow_html=True)
    extra = f" (anche in {', '.join(i['also_in'])})" if i["also_in"] else ""
    st.caption(f"Campagna origine: {i['campaign']}{extra} · Target padre: {i['parent']} · Tipo origine: {i['origin']}"
               + (f" · Già presente in EXACT: {'sì' if i['already_in_exact'] else 'no'}" if i["kind"] == "EXACT" else ""))
    st.markdown(f"Clic {F.num(i['clicks'])} · Acquisti {F.num(i['orders'])} · Spesa {F.money(i['spend'])} · Vendite {F.money(i['sales'])} · "
                f"ACOS {F.acos(i['acos'])} · Affidabilità {U.label_confidence(i['confidence'])}")
    st.markdown(f"_{i['reason']}_")


def exact_section(st, ov):
    new = ov["exact_new_campaign"]
    st.header(U.OV_EXACT_NEW if new else U.OV_EXACT)
    items = ov["exact"]
    if items is None:
        st.info("Opportunità Search Term non calcolabili per questo libro e marketplace (dati o economia incompleti).")
        return
    if new:
        st.caption("In questo marketplace non esiste ancora una campagna EXACT: queste keyword sono buone candidate per crearla. "
                   "La dashboard non crea nessuna campagna.")
    if not items:
        st.success("Nessun termine di ricerca da mettere in EXACT con i dati attuali.")
        return
    for i in ov["exact_shown"]:
        with st.container(border=True):
            _opp_line(st, i)
    if len(items) > len(ov["exact_shown"]):
        st.caption(f"Mostrati {len(ov['exact_shown'])} su {len(items)}.")
        _see_all(st)


def negative_section(st, ov):
    st.header(U.OV_NEGATIVE)
    items = ov["negatives"]
    if items is None:
        st.info("Opportunità Search Term non calcolabili per questo libro e marketplace.")
        return
    if not items:
        st.success("Nessuna keyword da valutare come negativa con i dati attuali.")
        return
    st.caption("La negativa non viene mai applicata automaticamente; il tipo (exact o a frase) lo decidi tu.")
    for i in ov["negatives_shown"]:
        with st.container(border=True):
            _opp_line(st, i)
    if len(items) > len(ov["negatives_shown"]):
        st.caption(f"Mostrati {len(ov['negatives_shown'])} su {len(items)}.")
        _see_all(st)


def conflicts_section(st, ov):
    """Mostrata solo se esistono conflitti. Nessuna azione suggerita: solo verifica manuale."""
    items = ov.get("conflicts")
    already = ov.get("already_negative")
    if not items and not already:
        return
    if items:
        st.divider()
        st.header(U.OV_CONFLICTS)
        for i in items:
            with st.container(border=True):
                st.markdown(f"{F.badge(i['badge'], 'orange')} **{F.esc(i['term'])}**", unsafe_allow_html=True)
                st.caption(f"Campagna: {i['campaign']} · Target padre: {i['parent']} · Tipo origine: {i['origin']}")
                st.markdown(f"Clic {F.num(i['clicks'])} · Acquisti {F.num(i['orders'])} · Spesa {F.money(i['spend'])} · Vendite {F.money(i['sales'])} · "
                            f"ACOS {F.acos(i['acos'])}")
                st.markdown(i["reason"])
                st.markdown(f"_{i['text']}_")
                st.markdown(f"**{i['hint']}**")
    if already:
        st.caption("GIÀ NEGATIVA (solo informazione, nessuna azione): "
                   + ", ".join(f"{a['term']} ({'esatta' if a['negative_match'] == 'EXACT' else 'a frase'}, {a['campaign']})" for a in already))


# ------------------------------------------------------------------ erogazione, pause, monitor, bene
def delivery_section(st, ov):
    st.header(U.OV_DELIVERY)
    if not ov["delivery"]:
        st.success("Nessun target attivo con problemi di erogazione.")
        return
    st.caption("Target attivi senza traffico, con pochissimo traffico o con un forte calo rispetto al riferimento. "
               "Non conviene alzare l'offerta solo perché un target non eroga: prima verifica offerta e pertinenza.")
    for i in ov["delivery"]:
        with st.container(border=True):
            ref = i["reference"]
            st.markdown(f"{F.badge(i['badge'], 'orange')} **{F.esc(i['title'])}**", unsafe_allow_html=True)
            refs = (f"{F.num(ref['per_day'], 1)} al giorno ({ref['scope']}, ultimi {ref['days']} giorni)" if ref["per_day"] is not None else F.ND)
            st.markdown(f"Offerta {F.money(i['bid'])} · Impressioni ultimi {i['days']} giorni: **{F.num(i['impressions_30d'])}** · "
                        f"Impressioni di riferimento: {refs} · Clic: {F.num(i['clicks_30d'])} · Stato target: {U.label_status(i['status'])}")
            st.markdown(f"_{i['reason']}_")


def paused_section(st, ov):
    st.header(U.OV_PAUSED)
    if not ov["paused"]:
        st.success("Nessun target in pausa rilevante.")
    for c in ov["paused"]:
        with st.container(border=True):
            st.markdown(f"{F.badge(c['badge'], 'grey')} **{F.esc(c['title'])}**", unsafe_allow_html=True)
            st.markdown(f"_{c['reason']}_ · Spesa storica {F.money(c['spend'])} · Clic {F.num(c['clicks'])} · ACOS storico {_acos(c['acos']['lifetime'])}")
            if c["note"]:
                st.caption(c["note"])
    rest = len([r for r in ov["others"] if r["status"] == "PAUSED"])
    if rest:
        st.caption(f"Altri {rest} target in pausa senza dati rilevanti: nell'elenco «{U.OV_OTHERS}» in fondo.")


def monitor_section(st, ov):
    st.header(U.OV_MONITOR)
    if not ov["monitor"]:
        st.success("Niente da monitorare in modo particolare.")
    for c in ov["monitor"]:
        with st.container(border=True):
            st.markdown(f"{F.badge(c['badge'], 'yellow')} **{F.esc(c['title'])}**", unsafe_allow_html=True)
            st.markdown(f"_{c['reason']}_")
            st.caption(f"ACOS storico {_acos(c['lifetime_acos'])} · ACOS ultimi {c['tactical_days']} giorni {_acos(c['tactical_acos'])} · "
                       f"Acquisti ultimi {c['tactical_days']} giorni {F.num(c['tactical_orders'])}")


def good_section(st, ov):
    st.header(U.OV_GOOD)
    if not ov["good"]:
        st.info("Nessun target importante da lasciare com'è.")
    for c in ov["good"]:
        with st.container(border=True):
            st.markdown(f"{F.badge(c['badge'], 'green')} **{F.esc(c['title'])}**", unsafe_allow_html=True)
            st.markdown(f"_{c['reason']}_")
            st.caption(f"ACOS storico {_acos(c['lifetime_acos'])} · ACOS ultimi {c['recent_days']} giorni {_acos(c['recent_acos'])} · "
                       f"Acquisti {F.num(c['orders'])} · Profitto stimato {F.money(c['profit'])}")


def warnings_section(st, ov):
    st.header(f"{U.OV_WARNINGS} ({len(ov['warnings'])})")
    if not ov["warnings"]:
        st.success("Nessun avviso per questo run.")
        return
    for w in ov["warnings"]:
        with st.container(border=True):
            sev = w.get("severity")
            st.markdown(f"{F.badge(U.label_warning_kind(w['kind']), 'orange')} {F.badge(U.label_origin(w['origin']), 'grey')} "
                        f"{F.badge(w['campaign'] or 'nessuna campagna', 'blue')} {F.badge(f'{U.SEVERITY}: ' + (F.ND if sev in (None, '', 'n/d') else str(sev)), 'grey')}",
                        unsafe_allow_html=True)
            st.write(O.plain_text(w["text"]))


def others_section(st, ov):
    with st.expander(f"{U.OV_OTHERS} ({len(ov['others'])})", expanded=False):
        if not ov["others"]:
            st.write("Nessun'altra decisione.")
        else:
            st.dataframe([{"Campagna": r["campaign"], "Target": r["target"], "Azione": r["action"], "Stato target": U.label_status(r["status"]),
                           "Priorità": r["priority"] or F.ND} for r in ov["others"]], use_container_width=True, hide_index=True)


def rules_section(st, ov):
    with st.expander("Regole usate in questa pagina (soglie)", expanded=False):
        for v in ov["rules"].values():
            st.markdown(f"- {v}")
