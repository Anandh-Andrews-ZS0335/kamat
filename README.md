# Last Mile — agentic prescriptive analytics for collections

A bank already knows who is likely to default. This system decides **who to contact today, with what action,
and why** — using causal uplift estimates, a real MIP solver and simulation for the maths, agents for
orchestration and explanation, and a human approval gate before anything reaches a member.

Two separate applications:

| App | Port | What it is |
|---|---|---|
| `bank_api` | 8001 | A synthetic credit union. Paged JSON feeds in the bank's own column names, plus an endpoint that receives approved actions. Keeps the true effect of every action sealed on disk. |
| `lastmile` | 8000 | The decision engine, its agents, and its consoles: **Guided tour** (`/guide` - plain-language, client-facing walkthrough of a run, technical detail on a toggle), **Demo & Showcase** (`/demo`), **Admin** (`/admin` - every stage, agent, and tool call), **Manager** (`/manager` - today's team, per-collector queues, validate rankings, approve, release, close the day), **Daily report** (`/report`) and **Configuration** (`/admin/config`). |

The engine talks to the bank **over HTTP only** — an import-linter contract fails the build otherwise.

---

## Run it

```bash
make setup      # uv installs Python 3.11 + deps, creates .env
make seed       # generate the synthetic credit union (≈1s)
make up         # bank API :8001 + engine & consoles :8000
```

### Sign-in

Every console and API call requires a login. `make users` creates three accounts with random passwords and prints
the `LASTMILE_USERS` / `LASTMILE_SESSION_SECRET` lines for `.env` (restart to apply):

| Role | Can do |
|---|---|
| `admin` | everything, including saving configuration |
| `manager` | team, runs, approve / edit / reject, release, close the day; reads configuration |
| `guest` | read-only |

Approvals, releases, team changes, day closures and configuration saves are recorded under the signed-in username.
Serve it over HTTPS when it leaves your machine: passwords and the session cookie must not travel in clear text.

For a client or first-time viewer open **http://127.0.0.1:8000/guide**: eight short chapters, a live or replayed run narrated in
plain language, one member's journey, the honest results, and live checks of each governance promise.
For operators open `/admin` or `/manager` (or `/demo`), press **Run Pipeline**, and watch the agents work (~10s).
When it reaches *awaiting approval*, open the **Manager Console**.

### The consoles

`/manager`, `/report`, `/admin` and `/admin/config` share one application shell (`static/app.css`, `static/shell.js`):
a left sidebar (Today, Daily reports, Runs & agents, Configuration, Guided tour, Bank API) with a badge for undecided
recommendations, a top bar with the page title, business date, bank and LLM status and the configuration hash, and a
user menu showing the signed-in account and role with a light/dark/system theme switch. The shell reads `/api/me`
and hides what the role may not do: guests see no write actions, managers see no *Save* in the configuration editor.
The client-facing pages (`/guide`, `/demo`) keep their own presentation layout and `console.css`.

### The manager's business day (`/manager`)

The top of the Manager Console walks through the day, and nothing moves on its own:

1. **Confirm today's team** — who is working and for how long (full day, part time, custom minutes). Saved per day,
   carried forward to the next day, audited. The institution pack's `capacity.agents × minutes_per_agent` is only the default.
2. **Start today's run** — the planner fills **one queue per collector**, each to 90% of that person's shift
   (`capacity.planning_buffer`). Texts go to an automated queue. The worklist and the **Team queues** tab show who works what, in order.
3. **Review and approve** — as before. If the team changes after the run, **Re-plan** reuses the day's data and model and
   plans again in seconds; the old worklist is marked *superseded* and its decisions have to be made again.
4. **Release** approved actions to the bank.
5. **Close the day** — freezes the **daily report** (`/report?date=`) and, on the simulated bank, runs the bank's business
   day: released actions are executed, new accounts fall behind, matured outcomes appear, and the date moves on.
   The report's *What happened next* section is read live from the bank (execution next day, cures only after 30 days).

A bank seeded before this existed cannot move day by day: run `make seed` once (and `make clean-runs` if old runs confuse you).

### Configuration editor (`/admin/config`)

Edit the three YAML packs in the browser. A guide beside the editor explains each section and setting, which agent reads it
and what changes if you edit it. **Check changes** applies the same validation a run does (YAML, schema, cross-pack references,
unknown data fields) and points at the line. Saving needs a reason, keeps the previous version (`config/.history/`),
writes an audit event, and takes effect from the next run. Saving is refused while a run is in progress.

Other targets: `make run` (one run from the terminal), `make test`, `make lint`,
`make evaluate RUN=run_...` (score a run against sealed truth), `make clean-runs`.

### LLM (Gemini)

Only the Explanation Agent uses an LLM, and only to draft rationale/script **templates** per
segment × action. It is sent no account data and is not allowed to write numbers.

```bash
# .env
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash
```

With no key the run uses deterministic templates and the consoles say so. Every template — LLM or
built-in — is validated; any digit outside a `{{placeholder}}` is rejected.
To change provider, add a class satisfying `lastmile.agents.llm.base.LLMClient` and select it in
`lastmile/agents/llm/factory.py`. No agent changes.

> ⚠️ The hackfest rules disqualify proprietary tools. A hosted model API may count. Confirm with the
> organisers before the demo; the template mode keeps the system fully open-source if needed.

---

## What happens in a run

| # | Stage | Accountable agent | Tools (module) |
|---|---|---|---|
| 1 | Data ingestion | Ingestion Agent | `bank.health`, `bank.list_feeds`, `bank.fetch_feed` (HTTP), `landing.store` |
| 2 | Configuration | Configuration Agent | `config.load_packs` (validate + hash 3 packs), `config.map_fields` |
| 3 | Data quality & privacy | Data Steward Agent | `quality.run_gates` (13 gates), `privacy.tokenise` (HMAC tokens, PII vault) |
| 4 | Feature layer | Feature Agent | portfolio join, PD calibration (grade→PD, 365d→30d), LGD, point-in-time training set, online features, leakage guard, feasibility profile |
| 5 | Decision engine | Decision Agent (+ Policy Agent handoff) | holdout validation (Qini), bagged T-learner, action scoring, value (numexpr), segments, **CBC MIP**, baselines, Monte Carlo, SimPy |
| 6 | Policy & governance | Policy Agent | independent post-solve verification, escalations, hash-chained audit |
| 7 | Explanation | Explanation Agent | stamped facts, LLM template drafting, template validation |
| 8 | Human approval | Supervisor → Collections manager | publish worklist; approve / edit / reject; release to bank |

Sequencing is deterministic so runs are reproducible and auditable. Tool ownership is enforced:
an agent calling a tool it does not own is refused and the refusal is traced.

---

## Project structure

```
kamat/
├── config/                         LAYER 0 — the only thing that changes per scenario / client
│   ├── scenarios/                  what decision (objective, actions, causal target, features)
│   ├── institutions/               whose data & policy (field map, calibration, LGD, capacity, rules)
│   └── runs/                       today's parameters
├── src/
│   ├── bank_api/                   separate app: synthetic credit union + REST API
│   └── lastmile/
│       ├── config/                 L0 loader: Pydantic schemas, structured rules, hashing
│       ├── store/                  SQLite (runs, trace, recommendations, approvals, audit) + Parquet artifacts
│       ├── ingest/                 HTTP bank client, field mapping, quality gates, tokenisation
│       ├── features/               calibration, LGD, point-in-time features, leakage, feasibility
│       ├── engine/                 uplift, value, segments, optimiser, baselines, Monte Carlo, SimPy — no LLM
│       ├── governance/             policy, escalation, audit chain, approvals, release
│       ├── kpi/                    pure metric functions
│       ├── agents/                 registry, trace, tools, the eight agents, provenance, LLM client
│       ├── pipeline/               run entry points
│       └── api/                    FastAPI + static Admin / Manager consoles
├── scripts/
│   ├── dev.py                      start both apps
│   └── evaluate_against_truth.py   the only non-test code that reads sealed truth
├── tests/test_claims.py            19 tests, one per claim
└── data/                           generated at runtime (gitignored)
```

Architecture is enforced by `make lint` (import-linter): layers point downward, `engine`/`features`/
`governance`/`kpi` may not import `agents`, and `lastmile` may not import `bank_api`.

---

## Results — read this before presenting numbers

The synthetic bank lets us score plans against the true effects, which is impossible on a real portfolio.
`Manager Console → Sort vs Optimised → Run synthetic benchmark` shows it for any run.

On the default seed, same capacity and policy for every plan:

| Plan | True avoided loss |
|---|---|
| Sort by risk grade | ≈ $68.7k |
| Sort by expected loss | ≈ $91.5k |
| **Optimised (uplift + CBC)** | **≈ $86.9k** — +26% vs risk sort, −5% vs expected-loss sort |
| Oracle (true uplift) | ≈ $131.5k |

- The optimiser clearly beats working the list **by risk**, and takes fewer actions that truly harm
  (63) than either sort (76–82).
- It narrowly **loses to a well-built expected-loss sort** here. The limit is account-level uplift
  ranking (correlation with truth 0.23–0.48), notably within the CALL arm. Bagging, shrinkage, pooling,
  AIPW calibration, a longer history and blending towards average effects were all tested; none closed
  the gap, and choosing among them on sealed truth would overfit.
- The model is optimistic: estimates run ≈2× the true value. Treat estimated dollars as relative.

Levers that should move this on real data: a randomised holdout (the brief's own recommendation),
more treatment history per action, and features that identify contact-sensitive members.

---

## Known limitations

- Static member traits (e.g. PTP kept rate) are read as today's values in training — a stated simplification.
- PD horizon conversion assumes constant hazard.
- Hardship referrals are rarely chosen: payment plans dominate them per agent-minute under the current effects.
- The NPA scenario pack loads and validates; running it needs a loan-level feed from the bank.
