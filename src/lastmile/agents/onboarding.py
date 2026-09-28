"""Onboarding Agent: connects a bank Last Mile has never seen.

The brain is the LLM. Given only the bank's API description and a privacy-safe profile of sampled data, it decides:
  * which endpoint is the status check, the feed catalogue, each of the six feeds, and where approved actions go
  * which bank column is which Last Mile field
  * what the bank's risk score is - grades or probabilities, and over what horizon - which is how Last Mile adapts
    to whatever predictive model the bank runs (scorecard, logistic, random forest, gradient boosting)
  * which fields are personal and must go to the identity vault

Around the brain, tools do what must not be left to judgement: fetching, profiling without exposing anyone's data,
checking the proposal against the columns that really exist, and building the pack. A failed check goes back to
the LLM once with the exact problems. Nothing is saved until an admin approves.
"""

from __future__ import annotations

import json

import pandas as pd

from lastmile.agents.base import Agent
from lastmile.agents.llm.base import LLMError
from lastmile.agents.registry import Registry
from lastmile.config.resolve import load_institution
from lastmile.ingest import onboarding as ob
from lastmile.store import llm_calls

ONBOARDING = "Onboarding Agent"
REFERENCE_INSTITUTION = "riverbend_cu"
SAMPLE_ROWS = 300


class OnboardingError(RuntimeError):
    pass


def _ask(ctx, tool: str, purpose: str, system: str, prompt: dict) -> tuple[dict | None, int, str | None]:
    """One LLM call, recorded in full whatever happens."""
    user = json.dumps(prompt, default=str)
    ctx.llm.last_exchange = None
    try:
        res = ctx.llm.generate_json(system, user)
        error = None if isinstance(res, dict) else f"expected a JSON object, got {type(res).__name__}"
    except LLMError as e:
        res, error = None, str(e)
    call_id = llm_calls.record(ctx.run_id, ctx.tracer.stage, ONBOARDING, tool, purpose, getattr(ctx.llm, "provider", "unknown"),
                               getattr(ctx.llm, "model", "unknown"), system, user, getattr(ctx.llm, "last_exchange", None),
                               res, error, ctx.data_dir)
    return (res if isinstance(res, dict) else None), call_id, error


PLAN_SYSTEM = (
    "You are the Onboarding Agent of Last Mile, a collections decision engine. A new bank has exposed an API. "
    "Read its API description and decide which endpoint serves which purpose. Last Mile needs: a status endpoint "
    "(returns the bank's business date), a catalogue endpoint (lists the feeds), six data feeds described under "
    "'feed_roles', the endpoint that RECEIVES approved collection actions (a POST), and, if present, an endpoint that "
    "closes the simulated business day. Choose only paths that appear in the description. Explain each choice briefly "
    "in plain English. Return JSON only.")

MAP_SYSTEM = (
    "You are the Onboarding Agent of Last Mile. You now see a privacy-safe profile of each feed the bank sends: for "
    "every column its kind (integer, number, text, date), null share, ranges, code values, or - for personal or "
    "identifier columns - only the SHAPE of values (A=capital letter, a=letter, 9=digit). No real customer values are "
    "shown. Decide which bank column fills each Last Mile field ('canonical_fields' gives meaning, expected kind and "
    "whether it is required). Use null only when no column fits. Never map one column to two fields in the same feed. "
    "Then decide what the bank's risk score is, because the bank may run any predictive model: a scorecard sends grade "
    "letters (input_type 'band', and you must give band_to_pd: the default probability each grade stands for); a "
    "logistic, random forest or gradient-boosted model sends probabilities between 0 and 1 (input_type 'probability'). "
    "Work out the horizon in days of that score from column names, values and descriptions. List which Last Mile "
    "fields are personal data (from first_name, last_name, phone, email). Say what the bank calls its customers. "
    "Give a one-sentence reason for every mapping and for the score decision, and list anything a person should confirm "
    "with the bank. Return JSON only.")


def register(r: Registry) -> None:
    @r.register("onboard.read_api_description", "lastmile.ingest.bank_client  GET /openapi.json", ONBOARDING,
                "Read the bank's published API description: every path, method and summary", "api")
    def read_api(ctx):
        spec = ctx.bank.get("/openapi.json")
        paths = []
        for path, ops in (spec.get("paths") or {}).items():
            for method, op in ops.items():
                if method.lower() in ("get", "post", "put"):
                    paths.append({"method": method.upper(), "path": path, "summary": op.get("summary") or "",
                                  "description": (op.get("description") or "")[:300],
                                  "query_params": [p.get("name") for p in op.get("parameters", []) if p.get("in") == "query"]})
        title = (spec.get("info") or {}).get("title", "")
        return {"title": title, "paths": paths}, {"title": title, "endpoints": len(paths),
                                                   "_message": f"'{title}' publishes {len(paths)} endpoints"}

    @r.register("llm.onboard_plan", "lastmile.agents.llm", ONBOARDING,
                "Decide which endpoint is the status check, the catalogue, each feed and the action receiver", "llm")
    def plan(ctx, api: dict, feedback: list[str] | None = None):
        prompt = {"api_title": api["title"], "api_paths": api["paths"], "feed_roles": ob.FEED_ROLES,
                  "output_format": {"endpoints": {"health_endpoint": "/path", "catalogue_endpoint": "/path",
                                                  "release_endpoint": "/path", "simulation_endpoint": "/path or null"},
                                    "feeds": {role: "/path" for role in ob.FEED_ROLES},
                                    "reasons": {"<endpoint or feed role>": "why"}, "confidence": "0-1",
                                    "concerns": ["..."]}}
        if feedback:
            prompt["previous_attempt_problems"] = feedback
        res, call_id, error = _ask(ctx, "llm.onboard_plan", "choose endpoints", PLAN_SYSTEM, prompt)
        if res is None:
            raise OnboardingError(f"the LLM could not produce an endpoint plan: {error}")
        return res, {"llm_call_id": call_id, "feeds": res.get("feeds"), "endpoints": res.get("endpoints"),
                     "confidence": res.get("confidence"),
                     "_message": f"LLM chose endpoints for {len(res.get('feeds') or {})} feeds (confidence {res.get('confidence')})"}

    @r.register("onboard.sample_feeds", "lastmile.ingest.bank_client  GET <feed>?page_size=300", ONBOARDING,
                "Fetch a small first page of each chosen feed, plus the bank's status and catalogue", "api")
    def sample(ctx, plan: dict):
        ep, feeds = plan.get("endpoints") or {}, plan.get("feeds") or {}
        status = ctx.bank.get(ep.get("health_endpoint") or "/")
        catalogue = ctx.bank.get(ep.get("catalogue_endpoint") or "/")
        frames, errors = {}, []
        for role in ob.FEED_ROLES:
            path = feeds.get(role)
            if not path:
                errors.append(f"no path chosen for {role}")
                continue
            try:
                frames[role] = pd.DataFrame(ctx.bank.get(path, {"page": 1, "page_size": SAMPLE_ROWS})["items"])
            except Exception as e:  # a wrong path is a planning problem the LLM gets to correct
                errors.append(f"{role}: GET {path} failed ({str(e)[:120]})")
        return (status, catalogue, frames, errors), {
            "institution": status.get("institution"), "as_of": status.get("as_of"),
            "sampled": {k: len(v) for k, v in frames.items()}, "errors": errors,
            "_message": f"sampled {len(frames)} feed(s) from {status.get('institution')}" + (f"; {len(errors)} failed" if errors else "")}

    @r.register("onboard.profile_data", "lastmile.ingest.onboarding", ONBOARDING,
                "Describe every sampled column by kind, range and value shape - no customer values leave this step")
    def profile(ctx, frames: dict):
        profiles = {role: ob.profile_frame(df) for role, df in frames.items()}
        return profiles, {"feeds": {k: len(v["columns"]) for k, v in profiles.items()},
                          "_message": f"profiled {sum(len(v['columns']) for v in profiles.values())} columns privately"}

    @r.register("llm.onboard_map", "lastmile.agents.llm", ONBOARDING,
                "Map bank columns to Last Mile fields, decide what the risk score is and which fields are personal", "llm")
    def map_fields(ctx, profiles: dict, catalogue: dict, plan: dict, previous: dict | None = None,
                   problems: list[str] | None = None):
        canonical = {feed: {f: {"meaning": m, "kind": k, "required": req} for f, (m, k, req) in fields.items()}
                     for feed, fields in ob.CANONICAL.items()}
        prompt = {"canonical_fields": canonical, "feed_roles": ob.FEED_ROLES, "bank_catalogue": catalogue.get("feeds"),
                  "chosen_feed_paths": plan.get("feeds"), "feed_profiles": profiles,
                  "output_format": {"field_map": {"<feed>": {"<canonical field>": "<bank column or null>"}},
                                    "field_reasons": {"<feed>": {"<canonical field>": "why"}},
                                    "score": {"input_type": "band or probability", "source_horizon_days": "integer",
                                              "band_to_pd": "{grade: probability} only for band", "reasoning": "why"},
                                    "pii_fields": ["first_name", "..."], "customer_noun": "customer / member / client",
                                    "institution_type": "bank / credit_union / building_society / ...",
                                    "open_questions": ["..."], "confidence": "0-1"}}
        if problems:
            prompt["previous_answer"] = previous
            prompt["problems_found_by_checks"] = problems
        res, call_id, error = _ask(ctx, "llm.onboard_map", "fix the mapping" if problems else "map fields and read the score",
                                   MAP_SYSTEM + (" Your previous answer failed automatic checks: fix exactly the listed "
                                                 "problems." if problems else ""), prompt)
        if res is None:
            raise OnboardingError(f"the LLM could not produce a field mapping: {error}")
        mapped = sum(1 for f in (res.get("field_map") or {}).values() if isinstance(f, dict) for v in f.values() if v)
        score = res.get("score") or {}
        return res, {"llm_call_id": call_id, "mapped_fields": mapped, "score": {k: score.get(k) for k in ("input_type", "source_horizon_days")},
                     "pii_fields": res.get("pii_fields"), "confidence": res.get("confidence"),
                     "_message": f"LLM mapped {mapped} fields; risk score read as {score.get('input_type')} over "
                                 f"{score.get('source_horizon_days')} days" + (" (rewrite)" if problems else "")}

    @r.register("onboard.check_proposal", "lastmile.ingest.onboarding", ONBOARDING,
                "Check the proposal against the real columns and values: required fields, types, 0/1 flags, score range, horizon")
    def check(ctx, proposal: dict, profiles: dict):
        problems = ob.check_proposal(proposal, profiles)
        return problems, {"problems": problems, "passed": not problems,
                          "_message": "all checks passed" if not problems else f"{len(problems)} problem(s) found"}

    @r.register("onboard.build_pack", "lastmile.ingest.onboarding + lastmile.config.schema", ONBOARDING,
                "Build the Institution Pack from the proposal and validate it with the same schema a run uses")
    def build(ctx, proposal: dict, institution_name: str, base_url: str):
        from lastmile.config.schema import InstitutionPack
        pack = ob.build_institution(proposal, load_institution(REFERENCE_INSTITUTION), institution_name, base_url)
        InstitutionPack.model_validate(pack)
        return pack, {"institution_id": pack["institution"]["id"], "reference_rules_from": REFERENCE_INSTITUTION,
                      "_message": f"Institution Pack '{pack['institution']['id']}' built and valid"}


class OnboardingAgent(Agent):
    name = ONBOARDING
    title = "Connects a new bank"
    responsibility = ("Read a new bank's API, decide with the LLM which endpoints and columns mean what and what its "
                      "risk score is, check that against real data, and propose an Institution Pack for approval.")
    modules = ("lastmile.agents.onboarding", "lastmile.ingest.onboarding", "lastmile.agents.llm")

    def run(self, base_url: str) -> dict:
        if self.ctx.llm is None:
            raise OnboardingError("onboarding needs a language model configured; the mapping decisions are made by it")
        api = self.use("onboard.read_api_description", input_summary={"base_url": base_url})

        plan, sampled = None, None
        for attempt in (1, 2):
            plan = self.use("llm.onboard_plan", input_summary={"endpoints": len(api["paths"]), "attempt": attempt}, api=api,
                            feedback=sampled[3] if sampled else None)
            self.note("Endpoint plan: " + "; ".join(f"{k} = {v}" for k, v in (plan.get("feeds") or {}).items()),
                      reasons=plan.get("reasons"), concerns=plan.get("concerns"))
            try:
                sampled = self.use("onboard.sample_feeds", input_summary={"feeds": plan.get("feeds")}, plan=plan)
            except Exception as e:
                sampled = (None, None, {}, [f"status or catalogue endpoint failed: {str(e)[:160]}"])
            if not sampled[3]:
                break
            self.note(f"{len(sampled[3])} endpoint(s) could not be read; asking the LLM to re-plan", errors=sampled[3])
        status, catalogue, frames, errors = sampled
        if errors:
            raise OnboardingError("the chosen endpoints could not be read: " + "; ".join(errors))

        profiles = self.use("onboard.profile_data", input_summary={"feeds": list(frames)}, frames=frames)
        proposal = self.use("llm.onboard_map", input_summary={"feeds": len(profiles)}, profiles=profiles,
                            catalogue=catalogue, plan=plan)
        self._explain(proposal)
        proposal = {**proposal, "endpoints": plan.get("endpoints") or {}, "feeds": plan.get("feeds") or {}}
        problems = self.use("onboard.check_proposal", input_summary={"attempt": 1}, proposal=proposal, profiles=profiles)
        attempts = 1
        if problems:
            self.note(f"{len(problems)} problem(s) in the LLM's mapping; sending them back for one rewrite", problems=problems)
            rewrite = self.use("llm.onboard_map", input_summary={"retry": True, "problems": len(problems)}, profiles=profiles,
                               catalogue=catalogue, plan=plan, previous=proposal, problems=problems)
            proposal = {**rewrite, "endpoints": plan.get("endpoints") or {}, "feeds": plan.get("feeds") or {}}
            self._explain(proposal)
            problems = self.use("onboard.check_proposal", input_summary={"attempt": 2}, proposal=proposal, profiles=profiles)
            attempts = 2

        name = status.get("institution") or api["title"] or "New bank"
        pack = None
        if not problems:
            try:
                pack = self.use("onboard.build_pack", input_summary={"institution": name}, proposal=proposal,
                                institution_name=name, base_url=base_url)
            except Exception as e:
                problems = [f"the built Institution Pack is not valid: {str(e)[:300]}"]
        self.note("Proposal ready for an admin to review" if not problems else
                  f"Proposal still has {len(problems)} problem(s); it needs a person before it can be used", problems=problems)
        return {"institution_name": name, "plan": plan, "proposal": proposal, "profiles": profiles,
                "problems": problems, "pack": pack, "attempts": attempts}

    def _explain(self, proposal: dict) -> None:
        score = proposal.get("score") or {}
        self.note(f"Risk score read as {score.get('input_type')} over {score.get('source_horizon_days')} days: "
                  f"{score.get('reasoning', '')}", open_questions=proposal.get("open_questions"))
