"""The true mapping for the Harbor demo bank: the answer a correct Onboarding Agent must reach."""

HARBOR_PROPOSAL = {
 "endpoints": {"health_endpoint": "/v2/status", "catalogue_endpoint": "/v2/catalogue",
               "release_endpoint": "/v2/collections/instructions", "simulation_endpoint": "/v2/sim/close-business-day"},
 "feeds": {"scores": "/v2/risk/pd-scores", "accounts": "/v2/lending/loans", "members": "/v2/crm/customers",
           "consents": "/v2/crm/contact-permissions", "queue_history": "/v2/collections/worklog", "outcomes": "/v2/collections/results"},
 "field_map": {
  "scores": {"account_id": "acct_ref", "risk_grade": "pd_180d", "score_date": "scored_on", "model_version": "model_id", "pd_horizon_days": "horizon_days"},
  "accounts": {"account_id": "acct_ref", "member_id": "cust_ref", "product": "product_type", "secured": "is_secured", "orig_amt": "original_amount",
               "curr_bal": "outstanding", "dpd": "days_overdue", "min_payment": "min_due", "status": "loan_state", "litigation": "in_litigation",
               "fraud_hold": "fraud_flag", "hardship_active": "hardship_now", "hardship_plans_12m": "hardship_count_12m",
               "ptp_kept_rate_12m": "promise_kept_ratio", "complaints_12m": "complaints_last_year", "pmts_missed_12m": "missed_payments_12m",
               "mos_since_last_pmt": "months_since_payment"},
  "members": {"member_id": "cust_ref", "first_name": "given_name", "last_name": "family_name", "phone": "mobile_no", "email": "email_address",
              "language": "preferred_language", "tenure_mos": "months_as_customer", "direct_deposit": "salary_credited",
              "deceased": "deceased_flag", "bankruptcy": "bankruptcy_flag", "cease_desist": "cease_contact_flag"},
  "consents": {"member_id": "cust_ref", "phone_consent": "may_call", "sms_consent": "may_text", "email_consent": "may_email",
               "dnc": "do_not_call", "contacts_7d": "contacts_last_7d", "last_rpc_date": "last_reached_on"},
  "queue_history": {"queue_date": "worked_on", "account_id": "acct_ref", "member_id": "cust_ref", "dpd_at_t": "days_overdue_then",
                    "balance_at_t": "balance_then", "risk_grade_at_t": "pd_180d_then", "pmts_missed_12m_at_t": "missed_payments_then",
                    "mos_since_last_pmt_at_t": "months_since_payment_then", "action": "treatment", "rpc": "reached_customer"},
  "outcomes": {"account_id": "acct_ref", "queue_date": "worked_on", "cured_30d": "recovered_within_30d", "amt_paid_30d": "paid_within_30d"}},
 "pii_fields": ["first_name", "last_name", "phone", "email"],
 "score": {"input_type": "probability", "source_horizon_days": 180},
 "customer_noun": "customer",
}
