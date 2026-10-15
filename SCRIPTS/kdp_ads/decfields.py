"""Colonne del CSV decisionale / tabella `decisions` (nome, tipo SQLite). Unica fonte di verita'."""

STATUSES = ("PROPOSED", "APPROVED", "REJECTED", "EXECUTED", "REVIEW_LATER")

DEC_FIELDS = [
    ("decision_id", "TEXT"), ("action_key", "TEXT"), ("run_id", "INTEGER"), ("analysis_run_id", "INTEGER"),
    ("generated_at", "TEXT"),
    ("book_id", "TEXT"), ("marketplace", "TEXT"), ("campaign_id", "TEXT"), ("campaign_name", "TEXT"), ("campaign_type", "TEXT"),
    ("amazon_account_id", "TEXT"), ("amazon_account_name", "TEXT"),            # account Amazon Ads della campagna (NULL = legacy_account_unknown)
    ("target", "TEXT"), ("target_type", "TEXT"), ("match_type", "TEXT"), ("search_term", "TEXT"),
    ("current_bid", "REAL"),
    ("final_action", "TEXT"), ("bid_change_pct", "REAL"), ("confidence", "TEXT"), ("priority", "TEXT"),
    ("lifetime_clicks", "REAL"), ("lifetime_orders", "REAL"), ("lifetime_spend", "REAL"), ("lifetime_sales", "REAL"),
    ("lifetime_acos", "REAL"), ("lifetime_estimated_profit", "REAL"),
    ("recent_clicks", "REAL"), ("recent_orders", "REAL"), ("recent_spend", "REAL"), ("recent_sales", "REAL"),
    ("recent_acos", "REAL"), ("recent_estimated_profit", "REAL"), ("recent_period", "TEXT"),
    ("recent_vs_historical_signal", "TEXT"),
    ("lifetime_action", "TEXT"), ("recent_action", "TEXT"),
    ("cross_campaign_overlap", "TEXT"), ("better_campaign_if_any", "TEXT"),
    ("growth_opportunity", "TEXT"),
    ("amazon_recommendation", "TEXT"), ("amazon_recommendation_period", "TEXT"), ("recommendation_agreement", "TEXT"),
    ("amazon_agreement_note", "TEXT"),
    ("decision_rule", "TEXT"), ("flags", "TEXT"), ("child_terms_note", "TEXT"), ("reason", "TEXT"),
    ("lifetime_evidence", "TEXT"), ("recent_evidence", "TEXT"), ("cross_campaign_evidence", "TEXT"),
    ("amazon_evidence", "TEXT"),
    # --- period snapshot / recency priority / delivery
    ("latest_comparable_snapshot", "TEXT"), ("comparable_period", "INTEGER"), ("tactical_source", "TEXT"),
    ("tactical_window", "TEXT"), ("tactical_period", "TEXT"),
    ("tactical_clicks", "REAL"), ("tactical_orders", "REAL"), ("tactical_spend", "REAL"), ("tactical_sales", "REAL"),
    ("tactical_acos", "REAL"), ("tactical_estimated_profit", "REAL"),
    ("lifetime_impressions_per_day", "REAL"), ("lifetime_clicks_per_day", "REAL"), ("lifetime_spend_per_day", "REAL"), ("lifetime_orders_per_day", "REAL"),
    ("recent_window", "TEXT"), ("recent_period_id", "TEXT"),
    ("recent_impressions_per_day", "REAL"), ("recent_clicks_per_day", "REAL"), ("recent_spend_per_day", "REAL"), ("recent_orders_per_day", "REAL"),
    ("tactical_impressions_per_day", "REAL"), ("tactical_clicks_per_day", "REAL"), ("tactical_spend_per_day", "REAL"), ("tactical_orders_per_day", "REAL"),
    ("delivery_signal", "TEXT"), ("delivery_ratio", "REAL"), ("delivery_source", "TEXT"), ("campaign_delivery_signal", "TEXT"),
    ("latest_period_action", "TEXT"), ("historical_context", "TEXT"), ("final_consolidated_action", "TEXT"),
    ("tactical_evidence", "TEXT"),
    # --- trend 7D (overlay temporale: separato dalla final_action)
    ("trend_state", "TEXT"), ("trend_evidence", "TEXT"), ("trend_period", "TEXT"), ("trend_clicks", "REAL"), ("trend_orders", "REAL"),
    ("trend_spend", "REAL"), ("trend_sales", "REAL"), ("trend_acos", "REAL"), ("trend_note", "TEXT"),
    ("target_status", "TEXT"), ("underlying_candidate_action", "TEXT"), ("operationally_actionable", "INTEGER"),
    ("manual_approval_required", "INTEGER"), ("status", "TEXT"), ("status_updated_at", "TEXT"),
]
DEC_COLUMNS = [n for n, _ in DEC_FIELDS]
