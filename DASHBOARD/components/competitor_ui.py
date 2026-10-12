"""Ricerca competitor: righe di tabella OMOGENEE (ogni colonna tutta testo, "n/d" dove manca il dato: nessun ArrowTypeError) e card. Solo presentazione.
Tutti i dati mostrati qui sono ESTERNI/STIMATI (Helium 10 Cerebro): la UI lo dice sempre."""
import formatting as F
import ui_labels as U


def n(v, digits=0):
    return F.num(v, digits)


def source_badge() -> str:
    return F.badge(U.COMP_BADGE, "purple")


def common_rows(keywords) -> list:
    return [{"Keyword": k["keyword"], "Competitor": k["competitor_share"], "Miglior Organic Rank": n(k["best_organic_rank"]),
             "Organic Rank mediano": n(k["median_organic_rank"], 1), "Con rank": n(k["organic_rank_count"]), "Miglior Sponsored Rank": n(k["best_sponsored_rank"]),
             "Competitor sponsored": n(k["sponsored_competitor_count"]), "Search Volume": n(k["search_volume"]), "Vendite keyword (stima)": n(k["keyword_sales"]),
             "Title Density": n(k["title_density"], 1), "Competing Products": n(k["competing_products"]), "Offerta suggerita H10": F.money(k["suggested_bid"]),
             "Brand/IP": "SÌ" if k["brand_or_ip"] else "no", "ASIN presenti": ", ".join(k["asins"])} for k in keywords]


def gap_rows(gap) -> list:
    out = []
    for r in gap:
        g, k = r["gap"], r["agg"]
        obs = g["observed"]
        out.append({"Keyword": r["keyword"], "Categoria": U.COMP_GAP_LABELS.get(g["primary"], g["primary"]),
                    "Tutte le categorie": ", ".join(U.COMP_GAP_LABELS.get(c, c) for c in g["categories"]),
                    "Competitor": k["competitor_share"], "Search Volume": n(k["search_volume"]), "Miglior Organic Rank": n(k["best_organic_rank"]),
                    "Search Term osservato": (f"{n(obs['clicks'])} clic · {n(obs['orders'])} ordini" if obs else "n/d"),
                    "Coperta da frase": ", ".join(g["covered_by_phrase"]) or "no", "ADS score": n(r["score"]["score"], 1), "Priorità": r["score"]["priority"],
                    "Affidabilità": r["reliability"], "Esclusa": r["score"]["excluded"] or "no"})
    return out


def launch_rows(items, book_label=None) -> list:
    return [{"Keyword": i["keyword"], "Categoria Top 10": i["badge"], "Competitor": i["agg"]["competitor_share"], "Miglior Organic Rank": n(i["agg"]["best_organic_rank"]),
             "Miglior Sponsored Rank": n(i["agg"]["best_sponsored_rank"]), "Search Volume": n(i["agg"]["search_volume"]), "Title Density": n(i["agg"]["title_density"], 1),
             "Competing Products": n(i["agg"]["competing_products"]), "ADS Opportunity Score": n(i["score"]["score"], 1), "Generica": "SÌ" if i["generic"] else "no",
             "Libro associato": book_label or "nessuno", "Stato rispetto alle campagne": i["status_label"], "Affidabilità": i["reliability"]} for i in items]


def launch_badge(i) -> str:
    tone = "grey" if i["generic"] else {"BOTH": "green", "ORGANIC": "blue", "SPONSORED": "orange"}[i["category"]]
    return F.badge(i["badge"], tone)


def competitor_rows(sources) -> list:
    return [{"ASIN": s["asin"], "File": s["file_name"], "Importato il": str(s["imported_at"])[:16].replace("T", " "), "Keyword": n(s["row_count"]),
             "Origine ASIN": {"FILENAME": "nome file", "MANUAL": "manuale"}.get(s["asin_source"], s["asin_source"] or "n/d"), "Marketplace": s["marketplace"] or "n/d",
             "Stato": "attivo" if s["status"] == "ACTIVE" else "sostituito (storico)", "Avvisi": "; ".join(s["warnings"]) or "nessuno"} for s in sources]


def raw_rows(raw) -> list:
    import json
    out = []
    for r in raw:
        out.append({"ASIN": r["asin"], "File": r["file_name"], "Stato file": r["source_status"], "Keyword": r["keyword_original"],
                    "Search Volume": n(r["search_volume"]), "Organic Rank": n(r["organic_rank"]), "Sponsored Rank": n(r["sponsored_rank"]),
                    "Title Density": n(r["title_density"], 1), "Competing Products": n(r["competing_products"]),
                    "Valori originali": json.dumps(json.loads(r["raw_json"] or "{}"), ensure_ascii=False)})
    return out


def score_lines(sc) -> list:
    """Fattori del punteggio come righe di testo: sempre visibili, non solo il numero."""
    lines = []
    for c in sc["components"]:
        pts = "esclusa (dato mancante)" if c["points"] is None else f"{c['points']:.1f} punti"
        lines.append(f"- **{c['label']}**: {c['note']} → {pts} (peso {c['weight']})")
    for p in sc.get("penalties", []):
        lines.append(f"- ⚠ **Penalità {p['name']}**: −{p['points']} · {p['note']}")
    return lines


def ads_card(st, s):
    with st.container(border=True):
        st.markdown(f"{F.badge('TESTA IN FRASE', 'blue')} {source_badge()} **{F.esc(s['keyword'])}**", unsafe_allow_html=True)
        st.markdown(f"Presente: **{s['present']}** competitor · Search Volume: {n(s['search_volume'])} · Miglior Organic Rank: {n(s['best_organic_rank'])} · "
                    f"Sponsored competitor: {s['sponsored']} · Title Density: {n(s['title_density'], 1)}")
        st.caption(f"Fonte: {s['source']} · Affidabilità: {s['reliability']} · ADS Opportunity Score {n(s['score'], 1)} (priorità {s['priority']})")
        st.markdown(f"_{s['reason']}_")
        with st.expander("Fattori del punteggio"):
            for line in score_lines(s["factors"]):
                st.markdown(line)


def opportunity_card(st, o, saved=None):
    """Card di un cluster editoriale. `saved` = riga persistita (stato, progetto salvato)."""
    tone = {"FORTE": "green", "INTERESSANTE": "blue", "DA OSSERVARE": "orange", "DEBOLE": "grey"}.get(o["score_label"], "grey")
    with st.container(border=True):
        head = f"{F.badge(o['score_label'], tone)} {source_badge()} **{F.esc(o['label'])}**"
        if o.get("derived_label"):
            head += " " + F.badge(o["derived_label"], "purple" if o.get("derived") else "grey")
        st.markdown(head, unsafe_allow_html=True)
        if o.get("origin_title"):
            st.caption(f"Origine: {o['origin_title']} ({o['origin_book_id']})")
        if o.get("overlap_warning"):
            st.warning(o["overlap_warning"] + ": " + "; ".join(f"{h['kind'].lower()} «{h['name']}» ({h['overlap']:.0%})" for h in o["overlaps"]))
        st.markdown(f"Stato: **{(saved or {}).get('status', 'DA_APPROFONDIRE').replace('_', ' ')}** · BOOK OPPORTUNITY SCORE {n(o['score'], 1)} · "
                    f"Affidabilità: {o['reliability']}")
        st.markdown("Keyword del cluster: " + ", ".join(f"`{k}`" for k in o["keywords"]))
        st.markdown(f"Confermato da **{o['competitor_share']}** competitor · Volume (keyword distinte): {n(o['volume_sum'])} (più forte: {n(o['volume_max'])}) · "
                    f"Miglior Organic Rank: {n(o['best_organic_rank'])} · Sponsored competitor: {o['sponsored_share']} · "
                    f"Title Density mediana: {n(o['title_density_median'], 1)} · Competing Products mediano: {n(o['competing_products_median'])}")
        st.caption(o["volume_note"])
        st.markdown(f"_{o['reason']}_")
        for c in o.get("caps", []):
            st.caption("Limite applicato: " + c)
        with st.expander("Fattori del BOOK OPPORTUNITY SCORE"):
            for c in o["components"]:
                pts = "esclusa (dato mancante)" if c["points"] is None else f"{c['points']:.1f} punti"
                st.markdown(f"- **{c['label']}**: {c['note']} → {pts} (peso {c['weight']})")
            st.caption("Sponsored Rank non entra nel punteggio: non è prova sufficiente di opportunità editoriale.")
        if saved and saved.get("saved_as_project"):
            st.success(f"Progetto editoriale: «{saved.get('project_title') or o['label']}» · stato {saved['status'].replace('_', ' ')}")


def growth_section(st, section, ops_page=None):
    """Sezione DA RICERCA COMPETITOR nella pagina Suggerimenti di crescita. Separata dai dati Amazon Ads reali."""
    st.header(f"{U.COMP_GROW_SECTION} ({section['total']})")
    st.markdown(source_badge(), unsafe_allow_html=True)
    st.caption(U.COMP_GROW_NOTE)
    for p in section["projects"]:
        st.subheader(f"{p['name']} · {p['competitors']} competitor · affidabilità {p['reliability']}")
        if ops_page:
            try:
                st.page_link(ops_page, label="Apri la ricerca completa")
            except Exception:
                st.caption("Ricerca completa: pagina «Ricerca competitor» nel menu.")
        if not p["suggestions"]:
            st.caption("Nessuna keyword competitor da testare in FRASE.")
        for s in p["suggestions"]:
            ads_card(st, s)
        if p.get("launch"):
            st.markdown("**KEYWORD DI LANCIO / ESPANSIONE** — nuove keyword con un competitor in top 10 (organico e/o sponsorizzato), non ancora nelle tue campagne. "
                        "Azione: TESTA IN FRASE (mai Exact diretto). Non entrano in «Da valutare ora».")
            st.dataframe(launch_rows(p["launch"][:30], p.get("book_title")), use_container_width=True, hide_index=True)
        if p["new_product_targets"]:
            st.markdown("**ASIN competitor non ancora target prodotto** (da valutare, non aggiunti): " + ", ".join(f"`{a}`" for a in p["new_product_targets"]))
