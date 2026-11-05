"""REGISTRO AZIONI MANUALI: unica parte della Panoramica che SCRIVE, e solo nella tabella locale `manual_actions` («HO FATTO QUESTA MODIFICA» / «ANNULLA REGISTRAZIONE»).
Non modifica Amazon Ads. Resta separata da overview_ui.py (che e' pura presentazione) cosi' il resto della Panoramica rimane a sola lettura."""
import ui_labels as U


def _scope(other, key):
    """Chiave dei widget isolata per libro + marketplace (+ account): cambiando libro nessuno stato di un widget del libro precedente puo' sopravvivere."""
    if not other:
        return key
    return f"{other.get('book_id')}_{other.get('marketplace')}_{str(other.get('account_id') or '')[-6:]}_{key}"


def register_box(st, reg, key, other=None):
    """«HO FATTO QUESTA MODIFICA»: registra SOLO localmente la modifica fatta a mano su Amazon Ads (data e valori). `reg` = payload della card (overview.decision_register / item_register).
    `other` = identita' della card (overview.other_identity / item_other_identity): abilita anche «HO FATTO UN'ALTRA MODIFICA» (azione scelta dall'utente)."""
    key = _scope(other, key)
    if reg:
        _quick_box(st, reg, key)
    if other:
        _other_box(st, other, key)


def _quick_box(st, reg, key):
    try:
        from ops import manual_ops, workspace
    except Exception:                                            # moduli di scrittura non disponibili (esecuzione isolata): nessun pulsante
        return
    from datetime import date
    with st.expander(U.OV_REGISTER):
        st.caption(U.OV_REGISTER_NOTE)
        with st.form(key=f"ma_form_{key}"):
            when = st.date_input("Data della modifica", value=date.today(), key=f"ma_date_{key}")
            after = None
            if reg["action_type"] in ("BID_DOWN", "BID_UP"):
                after = st.number_input("Offerta impostata", min_value=0.0, step=0.01, format="%.2f", value=float(reg.get("value_after") or 0.0), key=f"ma_val_{key}")
            note = st.text_input("Nota (facoltativa)", key=f"ma_note_{key}")
            if st.form_submit_button(U.OV_REGISTER_DONE) is True:
                r = manual_ops.record_action(workspace.current(), reg, when, after, note)
                if r.ok:
                    st.rerun()
                else:
                    st.error(" ".join(r.errors))


def undo_button(st, action_id):
    """«ANNULLA REGISTRAZIONE»: la riga resta come audit (UNDONE) e l'azione torna proponibile."""
    if action_id is None:
        return
    try:
        from ops import manual_ops, workspace
    except Exception:
        return
    if st.button("ANNULLA REGISTRAZIONE", key=f"ma_undo_{action_id}") is True:
        manual_ops.undo_action(workspace.current(), action_id)
        st.rerun()


def _other_box(st, other, key):
    """«HO FATTO UN'ALTRA MODIFICA»: l'utente sceglie tra i soli tipi compatibili con l'elemento (mai un testo libero) e registra l'azione REALE, non la raccomandazione.
    Per le offerte inserisce il valore realmente impostato (value_after: mai il suggerimento); la conferma mostra cosa verra' salvato prima del salvataggio."""
    from datetime import date

    from data_access import overview as O
    from kdp_ads import manual_actions as MA
    from ops import manual_ops, workspace
    with st.expander(U.OV_OTHER):
        st.caption(U.OV_OTHER_NOTE)
        choices = MA.OTHER_ACTIONS[other["kind"]]
        pick = st.selectbox("Che modifica hai fatto?", [None, *choices], format_func=lambda a: "— scegli —" if a is None else MA.ACTION_CHOICES[a], key=f"ma_oa_{key}")
        when = st.date_input("Data della modifica", value=date.today(), key=f"ma_od_{key}")
        after = None
        if pick in MA.BID_FAMILY:
            base = other.get("suggested_bid") if other.get("suggested_bid") is not None else (other.get("current_bid") or 0.0)
            after = st.number_input("Offerta realmente impostata", min_value=0.0, step=0.01, format="%.2f", value=float(base), key=f"ma_ov_{key}")
        note = st.text_input("Nota (facoltativa)", key=f"ma_on_{key}")
        if pick is None:
            return
        subject = other["label"]
        line = f"Stai per registrare: **{MA.ACTION_CHOICES[pick]}** su **{subject}** · {when.strftime('%d/%m/%Y')}"
        if pick in MA.BID_FAMILY:
            cur = other.get("current_bid")
            line += f" · offerta nel report {'n/d' if cur is None else f'${cur:.2f}'} → impostata ${after:.2f}"
            if other.get("suggested_bid") is not None and abs(other["suggested_bid"] - after) > 0.004:
                line += f" (il suggerimento era ${other['suggested_bid']:.2f}: registro il valore che hai davvero impostato)"
        st.info(line + ". Solo nel database locale: Amazon Ads non viene toccato.")
        if st.button(U.OV_OTHER_CONFIRM, key=f"ma_oc_{key}") is True:
            try:
                payload = O.other_payload(other, pick, after)
            except MA.ManualActionError as e:
                st.error(str(e))
                return
            r = manual_ops.record_action(workspace.current(), payload, when, None, note)
            if r.ok:
                st.rerun()
            else:
                st.error(" ".join(r.errors))
