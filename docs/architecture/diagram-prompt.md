# Prompt: generate the Last Mile solution architecture diagram

Paste everything below the line into an LLM or diagramming assistant.

---

You are a solutions architect and information designer. Produce a **solution architecture
diagram** for a system called **Last Mile** — an agentic decision system that turns a bank's
raw collections data into a per-collector daily worklist that a human manager approves before
anything is sent to a customer.

## What to produce

A single top-to-bottom layered diagram, plus a legend and a short caption.
Output it as **<pick one: an SVG in a self-contained HTML page / a Mermaid `flowchart TB` /
a draw.io XML / a PowerPoint-ready layout spec>**.

Rules for the artwork:
- Portrait-ish landscape canvas, roughly 1440 x 2000, one layer per horizontal band,
  each band a rounded container with a small uppercase label on its left edge.
- Flow strictly top → bottom, with two clearly marked **return paths** drawn up the right
  margin: (a) outcomes → treatment/outcome history → future model runs, (b) approved actions →
  the bank's execution systems.
- Every box carries a bold title and one line of 11px supporting text naming the concrete
  technique, tool or table. No empty boxes, no vague words like "AI magic" or "analytics".
- Colour is meaning, not decoration. Use exactly four accents:
  **teal = agent**, **violet = LLM call**, **amber = human gate or policy stop**,
  **grey/neutral = deterministic engine, store or external system**.
  Keep it legible in both light and dark mode, and readable when printed greyscale
  (differentiate by border weight and label, not colour alone).
- Label the arrows with what actually flows ("eligible actions", "uplift per action",
  "expected value", "per-collector queues", "approved actions", "30-day outcomes").

## The layers, top to bottom

Draw these fourteen bands in this order. The contents listed are the real components —
use them verbatim.

**1. Data sources**
External bank systems, reached over the bank's own HTTP API, never a direct database link.
Two live banks prove the system is bank-agnostic: *Riverbend Credit Union* (`/api/v1/...`,
risk supplied as grade bands A–E) and *Harbor Community Bank* (`/v2/...`, different column
names, risk supplied as a 180-day probability).
Feeds: accounts · members · consents & DNC · collections queue history · outcomes ·
payments · contact history · risk scores / PD.

**2. Data ingestion & data preparation**
`bank.health` → `bank.list_feeds` → `bank.fetch_feed` (paged) → `landing.store` (raw snapshot,
Parquet) → `privacy.tokenise` (identity vault; names and account numbers are replaced by tokens
and never leave it) → `quality.run_gates` (row counts, nulls, ranges, freshness) →
`config.map_fields` (YAML field map, bank columns → canonical fields) →
`features.calibrate_pd` (band-table or probability input → constant-hazard conversion →
12-month PD and decision-horizon PD) → `features.lookup_lgd` →
`features.build_training_set` / `features.build_online_features` →
`features.leakage_guard` (point-in-time correctness: nothing a feature could not have known
on the as-of date) → `features.join_portfolio`.

**3. Customer / account decision state**
The single joined record the rest of the system reasons over: account · payment behaviour ·
contact history & fatigue · risk (PD, exposure, LGD) · behaviour features · exposure ·
consent flags · hardship indicators. Point-in-time, one row per account per business day.
Stored as Parquet artifacts + SQLite; every field carries a provenance stamp.

**4. Business rules / policy engine** *(amber — a stop, not a score)*
`policy.eligibility`: consent · DNC · legal suppression · contact limits (per day and per
week) · hardship · quiet hours · channel rules. Declared in YAML config packs, versioned by a
config hash. Output = the set of **eligible actions** per account. A policy stop is absolute:
no expected value can override it.

**5. Risk / prediction layer**
Where the bank's own prediction lands. Last Mile does **not** retrain the bank's PD model —
it consumes whatever shape the bank emits (band, probability, any horizon, any algorithm:
logistic, random forest, XGBoost) and calibrates it to a common scale. Show this explicitly
as an adapter, with a note: *the bank's model type does not matter, only the shape of its
output*. Components: PD calibration · LGD lookup · exposure · `engine.assign_segments`
(four segments: Persuadable · Sure Thing · Lost Cause · Sleeping Dog).

**6. Causal / uplift model**
`engine.train_uplift` — a **bagged T-learner**: one outcome model per treatment arm, six bags
of `HistGradientBoostingClassifier` (scikit-learn), giving an uplift estimate with a low/high
interval. Potential-outcomes framing: P(cure | action) − P(cure | no action), per action
(CALL · SMS · PLAN · HARDSHIP · NONE). Validated by `kpi.validate_uplift` (**Qini curve**).
`engine.score_actions` applies it; `engine.baselines` keeps two naive comparators
(sort by risk, sort by value) so the uplift plan can be shown to beat them.

**7. Financial value engine**
`engine.compute_value`: **expected value = PD × exposure × LGD × uplift − action cost**,
per account per action. Deterministic arithmetic, no model, fully reproducible; every input
traceable to its source field.

**8. Optimization / assignment engine**
`engine.optimise` — a **mixed-integer program** (PuLP, CBC solver): maximise total expected
value subject to team capacity minutes, per-action handling time, contact caps, one action per
account, channel limits.
`engine.assign_collectors` — **first-fit-decreasing bin packing** into each collector's own
shift, with a repair loop that re-solves at a reduced capacity when a queue will not fit
(a 45-minute hardship referral cannot be split).
`engine.simulate_policies` — **Monte Carlo**, 2,000 runs, giving p10/p50/p90 on the day's value.
`engine.simulate_floor` — **discrete-event simulation** (SimPy) of each collector's shift,
predicting the completion rate.
Inputs: today's roster from `roster.load` (who is in, how long each person works).

**9. Agent / orchestration layer** *(teal band — draw it as a vertical spine alongside layers
2–12, not as a box in the middle of the flow)*
A **SupervisorAgent** runs a fixed daily pipeline of eight agents in order:
ConfigurationAgent → IngestionAgent → DataStewardAgent → FeatureAgent → PolicyAgent →
DecisionAgent → ExplanationAgent, under SupervisorAgent.
A ninth, the **OnboardingAgent**, runs off to the side, only when a new bank is added.
Key property to make visible: **each agent may call only the tools it owns** — ownership is
enforced by a registry, so an agent physically cannot reach another agent's tools. Every call
is written to a trace (tool, inputs summary, outputs summary, duration) and to a hash-chained
audit log. The whole codebase is layered and the layering is enforced in CI by import-linter:
`api > pipeline > agents > governance | engine | kpi > features > ingest > store > config`.

**10. Recommendation / policy layer**
`provenance.build_facts` (every figure on the screen carries the field and run it came from) →
`llm.draft_templates` *(violet — LLM)* → `provenance.validate_templates` (checks the wording
against the facts, with one bounded rewrite if it fails) → `engine.explain_selection` →
`governance.escalate` (hardship, large exposure, unusual action → needs a second look) →
`policy.verify_plan` (an independent re-check of the finished plan against every rule) →
`worklist.publish`.
Annotate clearly: **the LLM writes the sentence; it never produces a number, never chooses an
action, and never sees customer data — only tokens and aggregate facts.**

**11. Human-in-the-loop** *(amber)*
The **collections manager**: sets today's roster (how many collectors, how long each works),
triggers the daily run (there is no timer), reviews the worklist and per-collector queues,
approves / edits / rejects each item, may re-plan for a changed team, releases the approved
actions, and closes the day. An **admin** edits the YAML config packs through a validating
editor and approves a new bank's onboarding proposal. Roles: admin · manager · guest, enforced
on every page and API call. **Nothing reaches a customer without a release.**

**12. Action / execution layer**
Released actions are posted back to the bank's API (CALL · SMS · PLAN · HARDSHIP), with the
approver and the source run id stamped on each one. The bank executes them on its own systems
when its day advances.

**13. Outcome & feedback layer**
The bank returns execution status, right-party contact, amount paid, cure / re-default /
complaint, at a **30-day outcome maturity** (no outcome younger than 30 days is published, so
training labels cannot leak). These become treatment/outcome history, which feeds the next
uplift training run — draw this as the loop back up to layer 6. The **Business KPIs** console
reads them: value per collector hour · cure rate · worsening after contact · collector time
used · contact-cap breaches · customer impact (sleeping dogs left alone, hardship, complaints)
· explainability coverage. Mark honestly on the diagram: *without a random holdout these are
observed, not proven to be caused by Last Mile.*

**14. Observability & governance** *(draw as a vertical column down the right side, touching
every layer)*
Run registry and trace viewer · per-agent timings · **stored LLM request/response log** ·
**hash-chained audit** with chain verification · provenance stamps on every displayed figure ·
config hash pinned to each run · append-only tables (SQLite triggers) and migrations ·
frozen daily reports · model run id reused on re-plan so a re-plan is not a retrain ·
identity vault kept outside the analytics store.

## Cross-cutting bands to draw around the stack

- **Top band — users and access:** collections manager · admin · guest · client/judges →
  HTTPS → sign-in gate (PBKDF2 password hashes, HMAC-signed session cookie, role checked on
  every request) → consoles: Today (worklist & queues) · Daily reports · Runs & agents ·
  Configuration · Onboard a bank · Business KPIs · Guided tour · Demo.
- **Application layer:** FastAPI (:8000) — business services & APIs, the daily-run workflow /
  orchestration endpoints, and the rules engine; vanilla-JS front end with a shared design
  system.
- **Integration layer:** the bank API client (paged, retrying, timeout-bounded), the field
  mapping pack per institution, and the Onboarding path.
- **Persistence:** SQLite in WAL mode (runs, recommendations, approvals, releases, trace,
  audit, LLM calls, rosters, day closures) · Parquet artifacts per run · YAML config packs ·
  identity vault.

## Where the LLM is used — show only these, in violet

1. **ExplanationAgent → `llm.draft_templates`** — drafts the "why this customer, why this
   action" wording, from tokenised facts only, then is checked against those facts and
   rewritten once if it fails.
2. **OnboardingAgent → `llm.onboard_plan` and `llm.onboard_map`** — reads a new bank's OpenAPI
   description, plans which feeds it needs, then maps that bank's columns to the canonical
   fields. It sees only column names and privacy-safe *value shapes*, never real values.
   Its proposal is checked deterministically and must be approved by an admin before it is used.

Everywhere else, state plainly on the diagram: **deterministic maths, no LLM.**

## Constraints

- Do not invent components, vendors, cloud services or model names that are not listed above.
- Do not put the LLM anywhere in the scoring, valuation, optimisation or policy path.
- Keep the honest caveats visible rather than hiding them: no holdout yet; outcomes mature at
  30 days; the bank's contact log and Last Mile's plan are two separate sources of contacts.
- Prefer clarity over density. If a band has more than six boxes, group them.

## Reference sketch (topology only — improve on it, don't copy its crudeness)

```
BANK SYSTEMS  →  INGESTION & PREPARATION  →  CUSTOMER DECISION STATE
                                                     │
                            ┌────────────────────────┴────────────────────────┐
                    BUSINESS / POLICY RULES                        CAUSAL / UPLIFT MODEL
                     (consent, DNC, legal,                          (bagged T-learner,
                      contact limits, hardship)                      per-action uplift)
                            └──── eligible actions ──┬── uplift ────┘
                                                     ↓
                                        FINANCIAL VALUE ENGINE
                                  PD × exposure × LGD × uplift − cost
                                                     ↓
                                   OPTIMIZATION / ASSIGNMENT  (MIP + packing + MC + DES)
                                                     ↓
                              RECOMMENDATION & EXPLANATION  (facts → LLM wording → validation)
                                                     ↓
                                        HUMAN APPROVAL  (collections manager)
                                                     ↓
                                          ACTION EXECUTION
                                                     ↓
                                         OUTCOME & FEEDBACK  ──→ treatment/outcome history
                                                                        ──→ future model runs
```
