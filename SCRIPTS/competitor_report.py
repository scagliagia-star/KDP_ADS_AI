"""Verifica funzionale RAPIDA di 1-5 file Helium 10 Cerebro, SENZA database e senza scrivere nulla: keyword comuni e cluster editoriali derivati.
Uso:  python SCRIPTS\\competitor_report.py <cerebro1.csv> <cerebro2.csv> <cerebro3.csv> [--asin NOMEFILE=ASIN ...] [--expect "short stories" --expect mystery ...]
L'ASIN si legge dal nome del file (US_AMAZON_cerebro_<ASIN>_<data>.csv); se manca: --asin nomefile.csv=B0XXXXXXXX.
--expect TERMINE: controlla che il termine compaia in almeno un'etichetta di cluster o in una keyword comune (nessun numero atteso e' fissato).
Dati ESTERNI/STIMATI (Helium 10 Cerebro): nessuna decisione Amazon Ads."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kdp_ads import cerebro, competitor as C  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--asin", action="append", default=[], help="NOMEFILE=ASIN per i file senza ASIN nel nome")
    ap.add_argument("--expect", action="append", default=[], help="termine atteso in cluster o keyword comuni")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)
    manual = dict(a.split("=", 1) for a in args.asin if "=" in a)
    sources = []
    for f in args.files:
        p = Path(f)
        parsed = cerebro.parse_cerebro(p.name, p.read_bytes(), asin=manual.get(p.name))
        if not parsed["asin"]:
            print(f"ASIN mancante per {p.name}: usa --asin {p.name}=B0XXXXXXXX", file=sys.stderr)
            return 2
        print(f"{p.name}: ASIN {parsed['asin']} ({parsed['asin_source']}), {len(parsed['rows'])} keyword, {len(parsed['columns']) - len(parsed['unmapped'])} colonne riconosciute")
        for w in parsed["warnings"]:
            print(f"   avviso: {w}")
        sources.append({"asin": parsed["asin"], "rows": cerebro.merge_duplicate_rows(parsed["rows"])})
    if len(sources) > 5:
        print("Massimo 5 competitor", file=sys.stderr)
        return 2
    cfg = C.config()
    agg = C.aggregate(sources)
    opps = C.build_opportunities(agg, cfg)
    ov = C.overview(agg, None, opps, "NEW_OPPORTUNITY")
    print(f"\nFonte esterna: Helium 10 Cerebro · {agg['total']} competitor · affidabilità {ov['reliability']}")
    for cnt, n in ov["by_count"].items():
        print(f"  {n} keyword presenti su {cnt}/{agg['total']}")
    print(f"\nKEYWORD COMUNI (prime {args.top}):")
    for k in agg["keywords"][:args.top]:
        print(f"  {k['competitor_share']:>4}  rank {k['best_organic_rank'] if k['best_organic_rank'] is not None else '-':>5}  vol {k['search_volume'] if k['search_volume'] is not None else '-':>7}  "
              f"{k['keyword']}{'  [BRAND/IP]' if k['brand_or_ip'] else ''}")
    lc = ov["launch"]
    launch = C.launch_keywords(agg, cfg)
    print(f"\nKEYWORD DI LANCIO: {lc['any']} in top 10 · Organic Top 10 {lc['organic']} · Sponsored Top 10 {lc['sponsored']} · Organic + Sponsored {lc['both']}")
    for i in launch[:args.top]:
        k = i["agg"]
        print(f"  {i['category_label']:<27} {k['competitor_share']:>4}  org {k['best_organic_rank'] if k['best_organic_rank'] is not None else '-':>5}  "
              f"spons {k['best_sponsored_rank'] if k['best_sponsored_rank'] is not None else '-':>5}  vol {k['search_volume'] if k['search_volume'] is not None else '-':>7}  "
              f"{'GENERICA ' if i['generic'] else ''}{'BRAND ' if i['status'] == 'BRAND' else ''}{i['keyword']}")
    print("\nOPPORTUNITÀ EDITORIALI (DA APPROFONDIRE):")
    print(f"  cluster totali {len(opps['opportunities'])} · opportunità principali {len(opps['primary'])} · segnali secondari {len(opps['secondary'])}")
    for o in opps["primary"][:args.top]:
        print(f"  {o['score_label']:<13} {o['score']:>5}  {o['label']}  [{o['competitor_share']} competitor, {o['keyword_count']} keyword]")
        print(f"      {', '.join(o['keywords'][:8])}")
    if opps["secondary"]:
        print("SEGNALI SECONDARI (generici/deboli): " + ", ".join(o["label"] for o in opps["secondary"][:12]) + " ...")
    if not opps["opportunities"]:
        print("  nessun cluster con almeno 2 keyword correlate")
    if opps["brand_signals"]:
        print("\nSEGNALI DI MERCATO (brand/IP): " + ", ".join(k["keyword"] for k in opps["brand_signals"][:10]))
    rc = 0
    if args.expect:
        hay = " ".join([o["label"].lower() for o in opps["opportunities"]] + [k["keyword_norm"] for k in agg["keywords"] if k["competitor_count"] > 1])
        print("\nCONTROLLO TERMINI ATTESI:")
        for t in args.expect:
            ok = t.lower() in hay
            rc = rc or (0 if ok else 1)
            print(f"  {'OK ' if ok else 'MANCA'} {t}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
