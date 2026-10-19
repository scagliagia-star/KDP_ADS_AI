"""Suggerimenti di crescita: card GUARDA ANCHE (Home) e sezioni della pagina dedicata. Solo presentazione; nessuna azione su Amazon Ads."""
import formatting as F
import ui_labels as U
from data_access import overview as O

NAMES: dict = {}                                            # {ID account: nome}, impostato dalla pagina prima di mostrare le schede

PAGE = "pages/11_Suggerimenti_di_crescita.py"
CATEGORY_TITLES = {"SCALE": U.GROW_SCALE, "AUTO_TO_PHRASE": U.GROW_AUTO_PHRASE, "TO_EXACT": U.GROW_TO_EXACT, "DEVELOP": U.GROW_DEVELOP, "NEW_PRODUCT": U.GROW_NEW_PRODUCT}
PAGE_ORDER = ("SCALE", "AUTO_TO_PHRASE", "TO_EXACT", "DEVELOP", "NEW_PRODUCT")


def guarda_anche(st, lines, total):
    """Card compatta in Home: solo categorie con conteggio > 0; nessun peso nei KPI delle azioni difensive."""
    st.header(U.GROW_LOOK_ALSO)
    with st.container(border=True):
        if not total:
            st.markdown(U.GROW_NONE)
            return
        st.markdown(f"**Hai {total} {'opportunità' if total != 1 else 'opportunità'} di crescita**")
        for line in lines:
            st.markdown(f"- {line}")
        try:
            st.page_link(PAGE, label=U.GROW_SEE)
        except Exception:                                      # pagina non registrata (esecuzione isolata): testo semplice
            st.caption(U.GROW_SEE + " (menu a sinistra)")


def _metrics(st, i):
    st.markdown(f"Clic {F.num(i.get('clicks'))} · Ordini {F.num(i.get('orders'))} · Spesa {F.money(i.get('spend'))} · Vendite {F.money(i.get('sales'))} · "
                f"ACOS {F.acos(i.get('acos'))}")


def _conf(i):
    c = i.get("confidence")
    return U.label_confidence(c) if c in ("HIGH", "MEDIUM", "LOW") else (c or F.ND)


def card(st, i):
    cat = i["category"]
    acct = O.account_text(((i.get("decision") or i.get("record") or {}).get("amazon_account_id")), NAMES)
    with st.container(border=True):
        if acct:
            st.caption(acct)
        src = i.get("decision") or i.get("record") or {}
        line = O.campaign_text(src.get("campaign_name"), src.get("amazon_campaign_id"))
        if line:
            st.caption(line)
        if cat == "SCALE":
            st.markdown(f"{F.badge('ALZA OFFERTA 5%', 'green')} **{F.esc(str(i['campaign']))} · {F.esc(U.display_target(i['target']))}**", unsafe_allow_html=True)
            st.caption(f"Tipo: {U.label_target_type(i.get('target_type'))} · Campagna: {i['campaign']}")
            st.markdown(f"Offerta attuale {F.money(i['bid'])} → suggerita **{F.money(i['suggested_bid'])}** (+{i['step_pct']}%)")
            h, r = i["hist"], i["recent"]
            st.markdown(f"Storico: {F.num(h['clicks'])} clic · {F.num(h['orders'])} ordini · spesa {F.money(h['spend'])} · vendite {F.money(h['sales'])} · "
                        f"ACOS {F.acos(h['acos'])}")
            st.markdown(f"Ultimi {r['days']} giorni: {F.num(r['clicks'])} clic · {F.num(r['orders'])} ordini · ACOS {F.acos(r['acos'])}")
        elif cat in ("AUTO_TO_PHRASE", "TO_EXACT"):
            badge = "AGGIUNGI IN FRASE" if cat == "AUTO_TO_PHRASE" else "METTI IN EXACT"
            st.markdown(f"{F.badge(badge, 'blue' if cat == 'AUTO_TO_PHRASE' else 'green')} **{F.esc(i['term'])}**", unsafe_allow_html=True)
            st.caption(f"Origine: {i['origin']} · Campagna: {i['campaign']} · Target padre: {U.display_target(i.get('parent'))}")
            _metrics(st, i)
        elif cat == "DEVELOP":
            tone = "grey" if i["secondary"] else "orange"
            st.markdown(f"{F.badge('SVILUPPA CAMPAGNA', tone)} **{F.esc(str(i['campaign']))}**" + (f" {F.badge('secondaria', 'grey')}" if i["secondary"] else ""),
                        unsafe_allow_html=True)
            st.caption(f"Tipo: {i['campaign_type']} · Target attivi: {i['active_targets']}")
            _metrics(st, i)
        else:
            st.markdown(f"{F.badge('NUOVO TARGET PRODOTTO', 'purple')} **ASIN {F.esc(i['asin'])}**", unsafe_allow_html=True)
            st.caption(f"Visto in: {i['campaign']}")
            _metrics(st, i)
        st.markdown(f"_{i['reason']}_")
        st.caption(f"Affidabilità: {_conf(i)} · Suggerimento manuale: la dashboard non modifica nulla su Amazon Ads.")


def sections(st, growth):
    for cat in PAGE_ORDER:
        items = growth[cat]
        st.header(f"{CATEGORY_TITLES[cat]} ({len(items)})")
        if not items:
            st.caption(U.GROW_EMPTY[cat])
            continue
        for i in items:
            card(st, i)
