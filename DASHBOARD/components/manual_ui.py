"""REGISTRO AZIONI MANUALI: unica parte della Panoramica che SCRIVE, e solo nella tabella locale `manual_actions` («HO FATTO QUESTA MODIFICA» / «ANNULLA REGISTRAZIONE»).
Non modifica Amazon Ads. Resta separata da overview_ui.py (che e' pura presentazione) cosi' il resto della Panoramica rimane a sola lettura."""
import ui_labels as U


def register_box(st, reg, key):
    """«HO FATTO QUESTA MODIFICA»: registra SOLO localmente la modifica fatta a mano su Amazon Ads (data e valori). `reg` = payload della card (overview.decision_register / item_register)."""
    if not reg:
        return
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
