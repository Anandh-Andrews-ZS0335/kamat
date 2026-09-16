"""Stamped facts and placeholder rendering.

The LLM never writes a number. It writes templates with {{placeholders}}; this module fills them from
facts produced by models, solvers and config, each carrying its source. Any digit in a template outside
a placeholder is a violation, so "every figure came from a tool" is checkable, not just promised.
"""

from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
DIGIT = re.compile(r"\d")
# "an {{expected_loss}}" renders as "an $9,894": a value placeholder written as though it were a noun.
ARTICLE_BEFORE_VALUE = re.compile(r"\b(a|an)\s+\{\{\s*(exposure|expected_loss|action_value|runner_up_value|min_payment|"
                                  r"uplift|uplift_low|uplift_high|base_cure|pd_horizon|lgd|dpd|minutes)\s*\}\}", re.I)

FORMATS = {
    "exposure": "money", "expected_loss": "money", "action_value": "money", "runner_up_value": "money",
    "min_payment": "money", "uplift": "pts", "uplift_low": "pts", "uplift_high": "pts", "base_cure": "pct",
    "pd_horizon": "pct", "lgd": "pct", "dpd": "int", "minutes": "int", "rank": "int", "horizon_days": "int",
    "risk_grade": "str", "product_label": "str", "action_label": "str", "runner_up_action": "str",
    "segment_label": "str", "member_noun": "str", "first_name": "str", "institution_name": "str",
}

RATIONALE_KEYS = {k for k in FORMATS if k != "first_name"}
# Member-facing scripts: no internal labels (action names such as "Collector call"), no model outputs.
SCRIPT_KEYS = {"first_name", "institution_name", "min_payment", "product_label", "member_noun"}

# What each placeholder means and how it renders - sent to the LLM so it writes around values, not labels.
MEANINGS = {
    "member_noun": ("what the institution calls its customers", "member"),
    "institution_name": ("the institution's name", "Riverbend Credit Union"),
    "product_label": ("the product, lower case", "auto loan"),
    "exposure": ("the balance owed", "$33,826"),
    "dpd": ("days past due, a whole number", "160"),
    "min_payment": ("the minimum payment due", "$990"),
    "risk_grade": ("the bank's own risk rating as it sent it: a grade letter or a default probability", "E"),
    "pd_horizon": ("chance of default within the decision window", "4.2%"),
    "lgd": ("share of the balance lost if it defaults", "45.0%"),
    "expected_loss": ("expected loss if the account does not catch up", "$9,894"),
    "base_cure": ("chance the member catches up with no contact", "22.8%"),
    "uplift": ("how much the action changes the chance of catching up", "+28.8 pts"),
    "uplift_low": ("low end of the uplift estimate", "+19.0 pts"),
    "uplift_high": ("high end of the uplift estimate", "+38.6 pts"),
    "action_label": ("name of the recommended action", "Collector call"),
    "action_value": ("avoided loss the action is worth", "$2,851"),
    "minutes": ("agent minutes the action takes", "12"),
    "rank": ("position in today's worklist", "3"),
    "segment_label": ("how the member is expected to respond to contact", "Persuadable"),
    "runner_up_action": ("name of the next best action", "Payment plan offer"),
    "runner_up_value": ("what the next best action is worth", "$1,044"),
    "horizon_days": ("the decision window in days", "30"),
    "first_name": ("the member's first name", "Morgan"),
}
PRODUCT_LABELS = {"AUTO": "auto loan", "PERS": "personal loan", "CARD": "credit card"}


def fmt(key: str, value) -> str:
    kind = FORMATS.get(key, "str")
    if value is None:
        return "n/a"
    if kind == "money":
        return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"
    if kind == "pts":
        return f"{value * 100:+.1f} pts"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{int(round(value)):,}"
    return str(value)


def stamp(key: str, value, source: str, run_id: str) -> dict:
    return {"value": value, "source": source, "run_id": run_id, "display": fmt(key, value)}


def template_violations(template: str, allowed: set[str], required: set[str] = frozenset(),
                        max_repeats: int | None = None) -> list[str]:
    occurrences = PLACEHOLDER.findall(template)
    found = set(occurrences)
    problems = []
    if max_repeats is not None:
        repeated = sorted(k for k in found if occurrences.count(k) > max_repeats)
        if repeated:  # e.g. "an {{expected_loss}} of {{expected_loss}}" - a placeholder used as its own label
            problems.append(f"placeholders used more than {max_repeats} time(s): {repeated}")
    if ARTICLE_BEFORE_VALUE.search(template):
        problems.append(f"article before a value placeholder: '{ARTICLE_BEFORE_VALUE.search(template).group(0)}'")
    stripped = PLACEHOLDER.sub("", template)
    if DIGIT.search(stripped):
        problems.append(f"digits outside placeholders: '{DIGIT.findall(stripped)[:5]}'")
    if found - allowed:
        problems.append(f"unknown placeholders {sorted(found - allowed)}")
    if required - found:
        problems.append(f"missing required placeholders {sorted(required - found)}")
    if not stripped.strip():
        problems.append("empty template")
    return problems


_TRAILING_ARTICLE = re.compile(r"\b(a|an|A|An)\s+$")


def _agree_article(before: str, value: str) -> str:
    """Fix 'a auto loan' / 'a no action' at render time. Only text runs change; values are never altered."""
    m = _TRAILING_ARTICLE.search(before)
    if not m or not value or not value[0].isalpha():
        return before
    if value.lower().startswith("no "):
        return before[:m.start()]                      # "a no action" -> "no action"
    article = "an" if value[0].lower() in "aeiou" else "a"
    if m.group(1)[0].isupper():
        article = article.capitalize()
    return before[:m.start()] + article + before[m.end(1):]


def render_segments(template: str, facts: dict[str, dict]) -> list[dict]:
    """Text split into plain runs and stamped values, so the UI can show each figure's source."""
    out, pos = [], 0
    for m in PLACEHOLDER.finditer(template):
        if m.start() > pos:
            out.append({"text": template[pos:m.start()]})
        key = m.group(1)
        f = facts.get(key)
        if f and out and "key" not in out[-1]:
            out[-1]["text"] = _agree_article(out[-1]["text"], str(f["display"]))
        out.append({"text": f["display"], "key": key, "source": f["source"], "run_id": f["run_id"]} if f
                   else {"text": f"[{key}?]", "key": key, "source": "MISSING", "run_id": None})
        pos = m.end()
    if pos < len(template):
        out.append({"text": template[pos:]})
    return out


def render_text(template: str, facts: dict[str, dict]) -> str:
    return "".join(s["text"] for s in render_segments(template, facts))
