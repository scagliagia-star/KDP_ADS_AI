"""Componenti UI Streamlit (solo presentazione). Niente pulsanti di azione: la V1 e' READ-ONLY.
Tutti i testi mostrati sono in italiano (ui_labels.py); i valori tecnici restano quelli del database."""
import formatting as F
import ui_labels as U
from kdp_ads.metrics import set_currency
from components import style


def header(st):
    st.markdown(style.CSS, unsafe_allow_html=True)
    st.markdown(style.READ_ONLY_BANNER, unsafe_allow_html=True)


def page_setup(st, title, load, small=None):
    """Intestazione + sidebar + contesto. `load` = funzione che ritorna il Context. Ferma la pagina se non c'e' nulla da mostrare.
    `small` = testo piccolo opzionale accanto al titolo (es. '(Delivery)')."""
    st.set_page_config(page_title=U.APP_TITLE if title == U.APP_TITLE else f"{U.APP_TITLE} · {title}", layout="wide")
    header(st)
    ctx = load()
    if ctx.error or not ctx.ok:
        st.error(U.label_error(ctx.error) if ctx.error else U.NO_DATA)
        st.stop()
    set_currency(ctx.book.get("currency") if ctx.book else None)     # simbolo degli importi = valuta del marketplace
    if ctx.analysis is None:
        st.warning("Run di analisi collegato non trovato nel database: alcune sezioni (confronto tra campagne, avvisi ereditati) "
                   "potrebbero mancare.")
    st.title(title)
    if small:
        st.markdown(f'<span class="muted">{F.esc(small)}</span>', unsafe_allow_html=True)
    return ctx


def badges(*pairs) -> str:
    return " ".join(F.badge(text, tone) for text, tone in pairs if text)


def decision_badges(d) -> str:
    actionable = bool(d.get("operationally_actionable"))
    return badges((U.label_priority(d.get("priority")) if d.get("priority") else None, F.PRIORITY_TONE.get(d.get("priority"), "grey")),
                  (U.label_action(d.get("final_action")), F.action_tone(d.get("final_action"))),
                  (f"Stato target: {U.label_status(d.get('target_status'))}", F.status_tone(d.get("target_status"))),
                  (f"{U.label_delivery(d.get('delivery_signal'))} · {U.label_delivery_source(d.get('delivery_source'))}",
                   F.source_tone(d.get("delivery_source"))),
                  ("AZIONABILE ORA" if actionable else "NON AZIONABILE ORA", "green" if actionable else "grey"))


def metrics_block(st, title, d, prefix, extra=None):
    st.markdown(f"**{title}**")
    cols = st.columns(5)
    cols[0].metric("Clic", F.num(d.get(f"{prefix}_clicks")))
    cols[1].metric("Ordini", F.num(d.get(f"{prefix}_orders")))
    cols[2].metric("Spesa", F.money(d.get(f"{prefix}_spend")))
    cols[3].metric("Vendite", F.money(d.get(f"{prefix}_sales")))
    cols[4].metric("ACOS", F.acos(d.get(f"{prefix}_acos")) if d.get(f"{prefix}_clicks") is not None else F.ND)
    if extra:
        st.caption(extra)


def decision_card(st, d):
    """Card completa di una decisione (Centro azioni)."""
    with st.container(border=True):
        st.markdown(f"### {F.esc(d.get('campaign_id'))} · `{d.get('target')}`")
        st.markdown(decision_badges(d), unsafe_allow_html=True)
        c = st.columns(4)
        c[0].metric("Bid attuale", F.money(d.get("current_bid")))
        c[1].metric("Variazione bid", F.bid_change(d.get("bid_change_pct")))
        c[2].metric("Affidabilità", U.label_confidence(d.get("confidence")))
        c[3].metric("Origine del segnale di erogazione", U.label_delivery_source(d.get("delivery_source")))
        if d.get("underlying_candidate_action"):
            st.markdown(f"Azione potenziale: **{F.esc(U.label_action(d['underlying_candidate_action']))}**")
        if d.get("delivery_source") == "CAMPAIGN_FALLBACK":
            st.info("Segnale di erogazione ereditato dalla campagna (non è un'evidenza diretta su questo target).")

        l_extra = f"Profitto stimato: {F.money(d.get('lifetime_estimated_profit'))} (stima, vedi metodologia)"
        metrics_block(st, U.label_window("LIFETIME").upper(), d, "lifetime", l_extra)
        metrics_block(st, f"{U.label_window(d.get('recent_window') or 'RECENT_65D').upper()} · {d.get('recent_period') or F.ND}", d, "recent")
        metrics_block(st, f"{U.label_window(d.get('tactical_window') or 'RECENT_30D').upper()} · {d.get('tactical_period') or F.ND}", d, "tactical")

        st.markdown("**PERCHÉ?**")
        for part in [p.strip() for p in U.label_reason(d.get("reason")).split(" | ")]:
            st.markdown(f"- {part}")
        if d.get("flags"):
            st.markdown("Segnalazioni: " + badges(*[(U.label_flag(f), "orange") for f in str(d["flags"]).split(";") if f]),
                        unsafe_allow_html=True)
        cross = d.get("cross_campaign_overlap")
        st.markdown(f"**Evidenze tra campagne:** {F.esc(U.label_reason(cross) if cross else 'nessuna sovrapposizione')}")
        st.markdown(f"**Osservazione Amazon:** {F.esc(d.get('amazon_recommendation') or F.ND)}")
        st.markdown(f"**Concordanza con Amazon:** {F.esc(U.label_agreement(d.get('recommendation_agreement')))}")
        if d.get("amazon_agreement_note"):
            st.caption(d["amazon_agreement_note"])
        if d.get("child_terms_note"):
            st.caption(d["child_terms_note"])


def kpi_row(st, summary):
    s = summary
    c = st.columns(5)
    c[0].metric("DECISIONI TOTALI", s["total"])
    c[1].metric("DA VALUTARE ORA", F.ND if s["actionable"] is None else s["actionable"])
    c[2].metric("REVISIONE RICHIESTA", s["review_required"])
    c[3].metric(U.WARNINGS_TITLE, s["warnings"])
    c[4].metric("TARGET IN PAUSA", F.ND if s["paused"] is None else s["paused"])
    if not s["has_new_fields"]:
        st.caption("Run precedente all'introduzione di target_status / operationally_actionable: alcuni conteggi non sono disponibili.")


def warnings_section(st, warnings):
    st.subheader(f"{U.DATA_WARNINGS_TITLE} ({len(warnings)})")
    if not warnings:
        st.success("Nessun avviso per questo run.")
        return
    for w in warnings:
        with st.container(border=True):
            sev = w.get("severity")
            st.markdown(badges((U.label_warning_kind(w["kind"]), "orange"), (U.label_origin(w["origin"]), "grey"),
                               (w["campaign"] or "nessuna campagna", "blue"),
                               (f"{U.SEVERITY}: {F.ND if sev in (None, '', 'n/d') else sev}", "grey")), unsafe_allow_html=True)
            st.write(w["text"])


def table_rows(decisions, columns):
    """Lista di dict leggibili per st.dataframe (valori NULL -> n/d). Intestazioni e valori tecnici invariati."""
    return [{c: (F.ND if d.get(c) is None else d.get(c)) for c in columns} for d in decisions]


def display_rows(decisions, columns):
    """Come table_rows ma per l'utente: intestazioni e codici tecnici mostrati in italiano (i dati non cambiano)."""
    return [{U.label_column(c): U.display_value(c, d.get(c)) for c in columns} for d in decisions]
