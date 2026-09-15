"""Plain-language documentation for every section and field of the three config packs.

Shown beside the YAML editor in the Admin Console, so someone new can tell what a setting does, which agent
reads it, and what changes when they edit it. Written against the code that actually reads each field:
where the engine records a field but does not act on it, the text says so.

Risk levels: "safe" (day-to-day tuning), "careful" (changes what gets recommended or who is contacted),
"expert" (must match the bank's data or the engine's code; a mistake stops the next run).
"""

from __future__ import annotations

PACKS = {
    "scenarios": {
        "title": "Scenario Pack",
        "summary": "What decision is being made: the goal in dollars, the model, the actions a collector can take, "
                   "and how members are grouped. Nothing here is specific to one bank.",
        "folder": "config/scenarios",
    },
    "institutions": {
        "title": "Institution Pack",
        "summary": "Everything specific to one client: how to reach its data, what its columns are called, its risk "
                   "and loss tables, team size, contact rules, eligibility rules and approval reasons.",
        "folder": "config/institutions",
    },
    "runs": {
        "title": "Run Config",
        "summary": "Which scenario and which institution a run uses, and optionally a fixed decision date.",
        "folder": "config/runs",
    },
}

SECTIONS: dict[str, dict[str, dict]] = {
    # ------------------------------------------------------------------------------------ scenario
    "scenarios": {
        "scenario": {
            "title": "Identity of the decision",
            "what": "Names this decision so every run, report and audit entry can say which one it made.",
            "used_by": ["Configuration Agent"], "risk": "safe",
            "effect": "Changing the title only changes labels. Changing the id breaks the link from the Run Config, "
                      "which names the scenario by id.",
            "fields": {
                "id": "Unique name of this scenario. Must match the file name and the Run Config's scenario.",
                "version": "Your own version label for this pack. Recorded with each run.",
                "title": "Human-readable name shown in the consoles.",
                "entity": "What one decision is about (an account, a loan). Descriptive: recorded, not acted on.",
                "group_by": "Who contact limits apply to. Descriptive today: the engine always limits contact per member.",
                "decision_period": "How often the decision is made. Descriptive: the manager starts each run.",
            },
        },
        "objective": {
            "title": "What counts as a good decision",
            "what": "The two formulas that turn model outputs into dollars. The planner picks the set of actions with "
                    "the highest total action value that fits the team's time and the rules.",
            "used_by": ["Decision Agent", "Explanation Agent"], "risk": "careful",
            "effect": "Changes every dollar figure and therefore which members are chosen. Formulas may use only "
                      "column names, numbers and + - * / ( ).",
            "fields": {
                "currency": "Currency label for money figures. Descriptive.",
                "horizon_days": "The decision window: 'did the member catch up within this many days?'. Also converts the "
                                "bank's 12-month risk into risk within the window.",
                "expected_loss": "Dollars likely to be lost if the account does not catch up. Default: 12-month default "
                                 "chance × balance × share lost on default.",
                "action_value": "What one action is worth: how much it raises the chance of catching up × expected loss, "
                                "minus its cash cost. Actions worth zero or less are never recommended.",
            },
        },
        "causal": {
            "title": "The uplift model",
            "what": "How the engine learns, from the bank's past decisions, how much each action changes a member's "
                    "chance of catching up.",
            "used_by": ["Feature Agent", "Decision Agent"], "risk": "expert",
            "effect": "Changes the model itself. Adding a feature that is only known after the outcome would let the "
                      "model cheat; the leakage guard refuses anything listed in exclude_features.",
            "fields": {
                "treatment_column": "Column in the history that says which action was taken.",
                "control_value": "The value meaning 'no action'. The model compares every action against it.",
                "outcome_column": "Column that records whether the member caught up.",
                "method": "Model family. Descriptive: the engine uses a T-learner.",
                "bootstrap_rounds": "How many models are trained per action (0 to 10). More rounds give a steadier estimate "
                                    "and a more honest uncertainty range, but the run takes longer.",
                "label_maturity_days": "Only past decisions older than this are used for training, because their "
                                       "outcome is known by then.",
                "features": "The facts the model may use about each account. Each must be available before the decision.",
                "exclude_features": "Facts the model must never use because they leak the outcome. A request for any of "
                                    "these stops the run.",
                "validation": "Holdout check before the model may publish a worklist.",
                "validation.holdout_fraction": "Share of past decisions kept aside to test the model (between 0 and 0.6).",
                "validation.min_qini": "Minimum ranking quality on the holdout. Below it, the run refuses to publish. "
                                       "0 means 'better than random'.",
            },
        },
        "actions": {
            "title": "What a collector can do",
            "what": "The menu of actions. Each one has a handling time, a cash cost, a channel, and the eligibility "
                    "rules (from the Institution Pack) a member must pass to receive it.",
            "used_by": ["Policy Agent", "Decision Agent", "Explanation Agent"], "risk": "careful",
            "effect": "Handling time decides how many actions fit into each collector's shift. Removing a rule from "
                      "'requires' lets more members receive that action. The ids SMS, CALL, PLAN and HARDSHIP are also "
                      "known to the bank and to parts of the planner: renaming or adding ids needs a code change.",
            "fields": {
                "id": "Short code sent to the bank on release. Keep SMS, CALL, PLAN, HARDSHIP.",
                "label": "Name shown to managers and used in rationales.",
                "channel": "How the action reaches the member (sms, voice). Sent to the bank with each action.",
                "cost_minutes": "Collector minutes one action takes. 0 means automated: no one's shift is used.",
                "cost_cash": "Cash cost per action, subtracted from its value.",
                "is_contact": "Whether it counts toward the member's contact limits.",
                "requires": "Eligibility rule sets (defined in the Institution Pack) a member must pass.",
            },
        },
        "segments": {
            "title": "How members are grouped",
            "what": "Thresholds that sort members into persuadable, sure thing, lost cause, sleeping dog or uncertain, "
                    "from the model's estimates.",
            "used_by": ["Decision Agent", "Explanation Agent"], "risk": "careful",
            "effect": "Changes the labels and the wording of explanations. It does not change which actions are chosen: "
                      "the planner uses dollar value.",
            "fields": {
                "persuadable_min_uplift": "Smallest uplift (as a fraction, 0.05 = 5 points) for 'contact changes the outcome'.",
                "sleeping_dog_max_uplift": "If some contact lowers the chance of catching up by at least this much, the "
                                           "member is a sleeping dog.",
                "sure_thing_min_p0": "Chance of catching up with no contact above which a member is a sure thing.",
                "lost_cause_max_p0": "Chance of catching up with no contact below which a member is a lost cause.",
            },
        },
        "simulation": {
            "title": "Checking the plan before anyone approves it",
            "what": "How many times the day is simulated: a Monte Carlo range of dollars, and a floor simulation of "
                    "whether each collector's queue fits their shift.",
            "used_by": ["Decision Agent"], "risk": "safe",
            "effect": "More runs make the ranges steadier and the run slower. It never changes the plan itself.",
            "fields": {
                "monte_carlo_runs": "Simulated days for the dollar range (at least 100).",
                "des_replications": "Simulated working days on the collections floor.",
                "seed": "Fixes the random numbers so the same inputs give the same results.",
            },
        },
        "explanation": {
            "title": "What every explanation must say",
            "what": "Facts every rationale must include. AI-written wording that leaves one out is rejected and "
                    "standard wording is used instead.",
            "used_by": ["Explanation Agent"], "risk": "safe",
            "effect": "Adding a fact makes rationales longer and stricter; removing one lets them omit it.",
            "fields": {"must_state": "Placeholder names that must appear, e.g. expected_loss, uplift, action_value."},
        },
    },
    # --------------------------------------------------------------------------------- institution
    "institutions": {
        "institution": {
            "title": "Who the client is",
            "what": "The institution's name and what it calls its customers.",
            "used_by": ["Configuration Agent", "Explanation Agent"], "risk": "safe",
            "effect": "Changes names in rationales and scripts. The id must match the file name and the Run Config.",
            "fields": {
                "id": "Unique name. Must match the file name.", "name": "Shown in the consoles and in member scripts.",
                "type": "Kind of institution. Descriptive.", "customer_noun": "What members are called in explanations.",
                "currency": "Currency label. Descriptive.",
            },
        },
        "source": {
            "title": "Where the data comes from",
            "what": "How the Ingestion Agent reaches the bank: the address, the page size and which endpoint serves each feed.",
            "used_by": ["Ingestion Agent", "Collections manager (release)"], "risk": "expert",
            "effect": "A wrong address or path stops the next run at ingestion. The address can be overridden by the "
                      "environment variable named in base_url_env.",
            "fields": {
                "base_url_env": "Environment variable that, if set, overrides the address.",
                "default_base_url": "The bank API's address.",
                "page_size": "Rows per request (100 to 20,000).",
                "feeds": "Feed name → bank endpoint. Every feed needs a field_map entry.",
                "release_endpoint": "Where approved actions are sent.",
                "simulation_endpoint": "Synthetic bank only: closes the business day when the manager closes the day. "
                                       "Remove it for a real bank.",
            },
        },
        "field_map": {
            "title": "Translating the bank's column names",
            "what": "For each feed: engine's name → the bank's column name. Everything after ingestion uses the "
                    "engine's names, so a new bank usually needs changes only here.",
            "used_by": ["Configuration Agent"], "risk": "expert",
            "effect": "A column that does not exist in the feed stops the run at the quality gates. Renaming the engine's "
                      "side breaks the features and rules that use it.",
            "fields": {},
        },
        "pii_fields": {
            "title": "Personal details",
            "what": "Fields moved to the identity vault before modelling. They never reach the models or the AI.",
            "used_by": ["Data Steward Agent"], "risk": "careful",
            "effect": "Removing a field here would let it flow into model tables. Only the release step reads the vault.",
            "fields": {},
        },
        "pd_calibration": {
            "title": "Turning risk grades into chances",
            "what": "The bank scores accounts with grades A to E. This table says what each grade means as a 12-month "
                    "chance of default, from the bank's own history.",
            "used_by": ["Feature Agent"], "risk": "careful",
            "effect": "Changes expected loss for every account, so it changes dollar values and who gets chosen. A grade "
                      "missing from the table stops the run.",
            "fields": {
                "input_type": "What the bank sends (band = grade letters). Descriptive.",
                "band_to_pd": "Grade → 12-month chance of default, strictly between 0 and 1.",
                "source_horizon_days": "The period the bank's grade describes (365 days).",
                "horizon_method": "How a 12-month chance becomes a chance within the decision window. Descriptive: "
                                  "constant hazard is used.",
            },
        },
        "lgd_table": {
            "title": "How much is lost on a default",
            "what": "Share of the balance lost if an account defaults, by product, security and days past due.",
            "used_by": ["Feature Agent"], "risk": "careful",
            "effect": "Changes expected loss and dollar values. Accounts matching no row use the default.",
            "fields": {"default": "Share lost when no row matches (0 to 1).",
                       "rows": "Product, secured (1 or 0), days-past-due range, and the share lost."},
        },
        "capacity": {
            "title": "Team size and daily limits",
            "what": "The default team when the manager has not saved a roster, plus daily limits on texts and hardship "
                    "referrals. The manager's roster in the Manager Console replaces agents and minutes_per_agent for the day.",
            "used_by": ["Configuration Agent", "Decision Agent", "Policy Agent"], "risk": "safe",
            "effect": "A bigger buffer plans fewer minutes, so queues are more likely to finish but fewer members are "
                      "reached. Takes effect on the next run.",
            "fields": {
                "agents": "Default number of collectors, used only when no roster has ever been saved.",
                "minutes_per_agent": "Default shift length in minutes (360 = 6 hours of calls).",
                "sms_per_day_max": "Most text reminders that may be sent in a day.",
                "hardship_slots_per_day": "Most hardship referrals the hardship team can take in a day.",
                "planning_buffer": "Share of each shift kept spare because calls run long (0 to 0.5). 0.10 plans 90%.",
            },
        },
        "contact_policy": {
            "title": "How often a member may be contacted",
            "what": "Limits that protect members from being chased too often. Checked before planning and again after.",
            "used_by": ["Policy Agent"], "risk": "careful",
            "effect": "Raising a limit lets more contact through; it may also breach the institution's own conduct rules.",
            "fields": {
                "max_attempts_per_7d": "Contacts allowed in the last 7 days. At the limit, no contact action is allowed.",
                "max_contacts_per_member_per_day": "Contacts per person per day, across all their accounts.",
            },
        },
        "suppression": {
            "title": "Never contact",
            "what": "Flags that block every action for a member: deceased, bankruptcy, cease-and-desist, litigation, "
                    "fraud hold, do-not-call.",
            "used_by": ["Policy Agent"], "risk": "careful",
            "effect": "Removing a flag allows actions for members who carry it. Each name must be a field in the data.",
            "fields": {},
        },
        "eligibility": {
            "title": "Who may receive which action",
            "what": "Named rule sets. Each action in the Scenario Pack lists the sets a member must pass. A rule is "
                    "{field, op, value}; ops are eq, ne, gt, gte, lt, lte, in, not_in. Missing data never passes.",
            "used_by": ["Policy Agent"], "risk": "careful",
            "effect": "Loosening a rule lets more members receive an action. A field that does not exist stops the run. "
                      "Renaming or removing a set that an action requires is refused when you check.",
            "fields": {"all": "Every rule must pass.", "any": "At least one rule must pass."},
        },
        "data_quality": {
            "title": "Is the data good enough to use today?",
            "what": "Gates the Data Steward Agent applies before anything is modelled. A blocking failure stops the run "
                    "instead of producing a worklist from bad data.",
            "used_by": ["Data Steward Agent"], "risk": "careful",
            "effect": "Loosening a gate lets a run continue with staler or thinner data.",
            "fields": {
                "max_score_age_days": "Oldest acceptable risk score, in days.",
                "max_null_fraction": "Largest share of rows with an empty required field. Above it the run stops; "
                                     "below it those rows are set aside (quarantined).",
                "min_rows": "Fewest rows each feed must have.",
                "required_fields": "Fields that must be present and filled, per feed.",
                "valid_grades": "Risk grades the bank may send. Scores with any other grade are set aside.",
            },
        },
        "escalation": {
            "title": "What needs a senior reviewer",
            "what": "Recommendations flagged for individual review. They are never included in 'approve all'.",
            "used_by": ["Policy Agent"], "risk": "safe",
            "effect": "Lower thresholds flag more recommendations, which means more manual review.",
            "fields": {
                "exposure_threshold": "Balances at or above this need a named reviewer.",
                "max_interval_width": "If the model's uncertainty range is wider than this (0.30 = 30 points), review it.",
                "escalate_actions": "Actions that always need review.",
            },
        },
        "approval": {
            "title": "Reasons a manager can give",
            "what": "The reason codes a manager must pick when changing or rejecting a recommendation. They become "
                    "feedback for improving the model.",
            "used_by": ["Collections manager"], "risk": "safe",
            "effect": "Adds or removes options in the Manager Console. Past decisions keep their codes.",
            "fields": {"reason_codes": "Short codes in capitals, e.g. ALREADY_CONTACTED."},
        },
    },
    # ------------------------------------------------------------------------------------------ run
    "runs": {
        "run": {
            "title": "What a run uses",
            "what": "Points a run at one Scenario Pack and one Institution Pack.",
            "used_by": ["Configuration Agent", "Ingestion Agent"], "risk": "expert",
            "effect": "Pointing at a scenario the bank has no data for (npa_early_warning) stops the next run at ingestion.",
            "fields": {
                "scenario": "Scenario Pack id (file name in config/scenarios).",
                "institution": "Institution Pack id (file name in config/institutions).",
                "as_of": "Fixed decision date (YYYY-MM-DD). Leave empty to use the bank's own date, as daily runs should.",
                "approver_default": "Default approver name. Descriptive.",
            },
        },
    },
}
