"""TREND OVERLAY 7D: lettura temporale Lifetime + 30D + 7D. Funzioni PURE (nessun DB).

Ruoli: LIFETIME = storico; 30D (finestra TACTICAL) = finestra DECISIONALE principale; 7D (finestra SHORT_TREND, <= snapshots.short_trend_max_days giorni) =
overlay di TREND che conferma, smentisce o rende incerta la lettura 30D. Le finestre NON si sommano mai e il 7D da solo non decide nulla di aggressivo.

EVIDENZA 7D (soglie in rules.json -> trend_7d):
  NO_TRAFFIC  0 clic
  STRONG      >= strong_min_clicks clic (default 10)  OPPURE  spesa >= strong_spend_royalty_mult x royalty (default 1.0)
  MEDIUM      >= medium_min_clicks clic (default 5)
  LOW         meno di medium_min_clicks clic
Gli ordini 7D si mostrano e si considerano sempre ("conversioni recenti").

TREND STATE (separato da final_action):
  NO_TRAFFIC_7D       il report 7D CONTIENE la riga del target e ha 0 clic: NON e' deterioramento ne' inefficienza ne' zero-order negativo
  MISSING_7D          nessuna riga 7D per il target (o campagna assente dal report 7D, o fonte Search Term): l'ASSENZA non e' zero traffico. Nessun effetto sulla decisione,
                      nessun problema di delivery, nessuno zero-order. UI: "DATI 7D NON DISPONIBILI"
  INSUFFICIENT_7D     7D con evidenza LOW/MEDIUM e 30D non problematico: niente da dichiarare
  WATCH_RECENT        7D con evidenza LOW/MEDIUM e 30D problematico (nessun recupero e nessun peggioramento dichiarati), oppure 7D STRONG cattivo con 30D sano
  CONFIRMED_DECLINE   30D problematico + 7D STRONG + 7D ancora sopra target (o zero-order con evidenza STRONG); storico che non giustifica gia' una pausa
  RECENT_RECOVERY     30D problematico + 7D STRONG + 7D <= target con conversioni recenti
  STABLE              30D e 7D coerenti e sani (7D STRONG)
Nessuno stato (None) se per la campagna non e' stato importato nessun snapshot 7D."""

STATES = ("STABLE", "CONFIRMED_DECLINE", "RECENT_RECOVERY", "WATCH_RECENT", "INSUFFICIENT_7D", "NO_TRAFFIC_7D", "MISSING_7D")
DEFAULTS = {"enabled": True, "strong_min_clicks": 10, "strong_spend_royalty_mult": 1.0, "medium_min_clicks": 5}


def config(rules: dict | None = None) -> dict:
    return {**DEFAULTS, **((rules or {}).get("trend_7d") or {})}


def evidence_7d(clicks, spend, royalty, cfg: dict) -> str:
    """NO_TRAFFIC / STRONG / MEDIUM / LOW. Spesa e clic mancanti = 0."""
    clicks, spend = clicks or 0, spend or 0
    if clicks <= 0:
        return "NO_TRAFFIC"
    if clicks >= cfg["strong_min_clicks"] or (royalty and spend >= cfg["strong_spend_royalty_mult"] * royalty):
        return "STRONG"
    if clicks >= cfg["medium_min_clicks"]:
        return "MEDIUM"
    return "LOW"


def weak_30d(acos, orders, clicks, step, action, target_acos) -> bool:
    """La lettura 30D e' problematica: ACOS sopra l'obiettivo, oppure un'azione di riduzione, oppure clic senza acquisti con azione WATCH."""
    if step and step > 0:
        return True
    if acos is not None:
        return acos > target_acos
    return bool((orders or 0) == 0 and (clicks or 0) > 0 and action == "WATCH")


def classify_trend(ev: str, acos_7d, orders_7d, weak30: bool, target_acos: float, pause_justified: bool = False) -> tuple[str, str]:
    """(trend_state, spiegazione). `ev` = evidence_7d. pause_justified = lo storico/30D giustificano gia' una pausa: il 7D non aggiunge nulla."""
    if ev == "NO_TRAFFIC":
        return "NO_TRAFFIC_7D", "nessun clic negli ultimi 7 giorni: non e' un peggioramento ne' un ACOS negativo"
    if ev in ("LOW", "MEDIUM"):
        if weak30:
            return "WATCH_RECENT", f"dati 7D limitati (evidenza {ev}): non confermano ne' smentiscono il 30D, monitorare"
        return "INSUFFICIENT_7D", f"dati 7D limitati (evidenza {ev}) e 30D non problematico: nessuna lettura"
    bad7 = (acos_7d is not None and acos_7d > target_acos) or (orders_7d or 0) == 0
    good7 = acos_7d is not None and acos_7d <= target_acos and (orders_7d or 0) >= 1
    if weak30 and bad7:
        if pause_justified:
            return None, "storico gia' da pausa: il 7D non aggiunge informazione"
        return "CONFIRMED_DECLINE", "30D sopra l'obiettivo e 7D con evidenza forte ancora sfavorevole: peggioramento confermato"
    if weak30 and good7:
        return "RECENT_RECOVERY", "30D problematico ma 7D con evidenza sufficiente di nuovo entro l'obiettivo e con conversioni: recupero recente"
    if bad7:
        return "WATCH_RECENT", "7D sfavorevole con evidenza forte ma 30D sano: il 7D da solo non decide, monitorare"
    return "STABLE", "30D e 7D coerenti"
