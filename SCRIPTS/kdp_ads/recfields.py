"""Colonne del CSV decisionale / tabella `recommendations` (nome, tipo SQLite). Unica fonte di verita'."""

REC_FIELDS = [
    ("run_id", "INTEGER"), ("generated_at", "TEXT"),
    ("book_id", "TEXT"), ("title", "TEXT"), ("campaign_id", "TEXT"), ("campaign_name", "TEXT"),
    ("campaign_type", "TEXT"), ("report_type", "TEXT"), ("report_scope", "TEXT"), ("date_from", "TEXT"), ("date_to", "TEXT"),
    ("target", "TEXT"), ("search_term", "TEXT"), ("match_type", "TEXT"), ("added_as", "TEXT"),
    ("target_type", "TEXT"), ("action_scope", "TEXT"),
    ("period_id", "TEXT"), ("window_id", "TEXT"), ("window_role", "TEXT"), ("window_days", "INTEGER"), ("comparable_period", "INTEGER"),
    ("impressions_per_day", "REAL"), ("clicks_per_day", "REAL"), ("spend_per_day", "REAL"), ("orders_per_day", "REAL"),
    ("impressions", "REAL"), ("clicks", "REAL"), ("ctr", "REAL"), ("spend", "REAL"), ("cpc", "REAL"),
    ("orders", "REAL"), ("sales", "REAL"), ("conversion_rate", "REAL"), ("acos", "REAL"), ("roas", "REAL"),
    ("price", "REAL"), ("royalty_per_copy", "REAL"), ("target_acos", "REAL"), ("break_even_acos", "REAL"),
    ("estimated_royalty", "REAL"), ("estimated_direct_ad_profit", "REAL"), ("estimated_profit_per_order", "REAL"),
    ("estimated_profit_note", "TEXT"),
    ("confidence", "TEXT"), ("economic_classification", "TEXT"), ("sample_classification", "TEXT"),
    ("rule_classification", "TEXT"), ("recent_vs_historical_signal", "TEXT"), ("signal_detail", "TEXT"),
    ("recommended_action", "TEXT"), ("recommended_bid_change_pct", "REAL"), ("priority", "TEXT"),
    ("flags", "TEXT"), ("cross_campaign_note", "TEXT"), ("reason", "TEXT"),
    # audit futuro vs suggerimenti Amazon (inserimento manuale, per ora NULL)
    ("amazon_recommendation", "TEXT"), ("amazon_recommendation_period", "TEXT"),
    ("amazon_recommendation_source", "TEXT"), ("recommendation_agreement", "TEXT"),
]
REC_COLUMNS = [n for n, _ in REC_FIELDS]
