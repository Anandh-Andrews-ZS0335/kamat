"""Deterministic fallback templates. Used when no LLM is configured, or when an LLM template fails validation."""

from __future__ import annotations

SEGMENT_SENTENCE = {
    "persuadable": "A contact is expected to change the outcome for this {{member_noun}}.",
    "sure_thing": "This {{member_noun}} is likely to cure without contact; the action is kept only because it still adds value at low cost.",
    "lost_cause": "Recovery is unlikely whatever we do; the action is kept only because its estimated value remains positive.",
    "sleeping_dog": "Contact may make the outcome worse for this {{member_noun}}; review carefully before approving.",
    "uncertain": "The model is not confident about this {{member_noun}}; treat the estimate as indicative.",
}

RATIONALE = (
    "{segment_sentence} The {{product_label}} balance of {{exposure}} is {{dpd}} days past due with a bank risk "
    "rating of {{risk_grade}}, carrying {{expected_loss}} of expected loss if it does not cure. The uplift model estimates that {{action_label}} "
    "changes the chance of curing within {{horizon_days}} days by {{uplift}} (between {{uplift_low}} and "
    "{{uplift_high}}), worth {{action_value}} in avoided loss. The next best option, {{runner_up_action}}, "
    "is worth {{runner_up_value}}."
)

SCRIPTS = {
    "SMS": "Hi {{first_name}}, this is {{institution_name}}. Your {{product_label}} payment is past due. "
           "Reply HELP or call us and we will find a payment that works for you.",
    "CALL": "Hello {{first_name}}, this is {{institution_name}} calling about your {{product_label}}. "
            "I would like to understand what is happening and help bring the account up to date. "
            "The minimum payment due is {{min_payment}}. What would work for you?",
    "PLAN": "Hello {{first_name}}, this is {{institution_name}}. We can offer a payment plan for your "
            "{{product_label}} that spreads the past-due amount into smaller instalments. "
            "Can we walk through an amount that fits your budget?",
    "HARDSHIP": "Hello {{first_name}}, this is {{institution_name}}. If you are going through a difficult time, "
                "our hardship programme may be able to pause or reduce payments on your {{product_label}} "
                "while you get back on your feet. Would you like to hear how it works?",
}


def fallback(segment: str, action: str) -> dict:
    # str.replace, not str.format: format would collapse every {{placeholder}} to {placeholder}.
    sentence = SEGMENT_SENTENCE.get(segment, SEGMENT_SENTENCE["uncertain"])
    return {"rationale": RATIONALE.replace("{segment_sentence}", sentence), "script": SCRIPTS[action]}
