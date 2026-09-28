"""Claim: a worklist is scoped on the server. A collection agent gets their own rows, and only the
fields needed to work them - the money and the model never reach the wire."""

from __future__ import annotations

import pytest

from lastmile.api import services


@pytest.fixture()
def engine(monkeypatch, dirs):
    """Point the store at the shared run's directory, the way the API sees it in production."""
    from lastmile.store import artifacts, db

    monkeypatch.setattr(db, "DATA_DIR", dirs["engine"])
    monkeypatch.setattr(artifacts, "DATA_DIR", dirs["engine"])
    return dirs["engine"]


def test_a_collectors_payload_carries_no_money_and_no_model(engine, run):
    """The fields are removed, not hidden in the page: what is not sent cannot leak to devtools."""
    items = services.worklist(run)
    assert items, "the shared run should have produced a worklist"
    full = services.project_items(items, "full")
    execution = services.project_items(items, "execution")

    assert full[0]["action_value"] and "segment_label" in full[0]        # the manager still sees everything
    for row in execution:
        for field in services.MANAGER_ONLY_ITEM_FIELDS:
            assert field not in row, f"{field} reached a collection agent"
    # but everything needed to actually work the account survives
    for field in ("account_token", "action", "minutes", "dpd", "product", "queue_position", "decision"):
        assert field in execution[0]
    assert execution[0]["escalated"] in (True, False)   # that it needs care, not the exposure behind it


def test_a_collectors_queue_summary_hides_the_value(engine, run):
    queues = [q for q in services.queues(run, services.worklist(run)) if q["est_value"]]
    assert queues, "at least one collector should have value on their queue"
    assert "est_value" not in services.project_queue(queues[0], "execution")
    assert "est_value" in services.project_queue(queues[0], "full")
    assert services.project_queue(None, "execution") is None


@pytest.mark.parametrize("status", ["awaiting_approval", "released"])
def test_capabilities_follow_the_role_and_the_run(status):
    live = status == "awaiting_approval"
    agent = services.capabilities("collector", status, own_queue_only=True)
    assert agent == {"reorder": live, "record_outcome": True, "approve": False, "release": False,
                     "replan": False, "close_day": False, "see_all_queues": False,
                     "see_money": False, "see_model": False}
    for role in ("admin", "manager"):
        boss = services.capabilities(role, status, own_queue_only=False)
        assert boss["see_money"] and boss["see_all_queues"] and boss["release"]
        assert boss["approve"] is live            # approving a released run is not a thing
    # the projection is driven by the capability, so the two can never disagree
    assert ("full" if agent["see_money"] else "execution") == "execution"


def test_a_browser_supplied_collector_id_is_discarded():
    """The scope comes from the session. Asking for somebody else's queue returns your own, rather
    than an error - there is nothing to probe, because the parameter is never read."""
    assert services.resolve_collector("collector", "C01", "C02") == "C01"
    assert services.resolve_collector("collector", "C01", None) == "C01"
    assert services.resolve_collector("collector", None, "C02") is None      # unlinked account: no queue
    # a manager may legitimately narrow to one collector, or see the whole day
    assert services.resolve_collector("manager", None, "C02") == "C02"
    assert services.resolve_collector("manager", None, None) is None


def test_the_endpoint_scopes_rows_and_fields_together(monkeypatch, engine, run):
    """End to end through the real handler: a manager narrowing to one collector still sees the
    money; the same rows fetched as that collector do not carry it."""
    from fastapi.testclient import TestClient

    from lastmile.api import auth
    from lastmile.api.app import app

    items = services.worklist(run)
    collector = next(i["collector_id"] for i in items if i["collector_id"] and i["minutes"] > 0)

    pw = "scope-test-pw-123"
    monkeypatch.setenv("LASTMILE_USERS", f"boss:manager:{auth.hash_password(pw, 1000)}")
    monkeypatch.setenv("LASTMILE_SESSION_SECRET", "test-secret")
    auth._failures.clear()
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"username": "boss", "password": pw})

    body = client.get(f"/api/worklist?run={run}&collector={collector}").json()
    assert body["scope"] == {"kind": "collector", "collector_id": collector,
                             "display": body["scope"]["display"], "detail": "full"}
    assert body["can"]["see_money"] and body["can"]["see_all_queues"]
    assert body["items"] and all(i["collector_id"] == collector for i in body["items"])
    assert "action_value" in body["items"][0] and "est_value" in body["summary"]

    # the same run seen through the execution projection, which is what a collector receives
    stripped = services.project_items(body["items"], "execution")
    assert all(f not in stripped[0] for f in services.MANAGER_ONLY_ITEM_FIELDS)


# ------------------------------------------------------- what the collector reports back
def test_an_attempt_is_appended_never_overwritten(engine, run):
    """Correcting a comment writes a new row, so what was said at the time survives the edit."""
    from lastmile.governance import outcomes

    token = next(i["account_token"] for i in services.worklist(run) if i["collector_id"])
    outcomes.record(run, token, "C01", "NO_ANSWER", "rang out", None, None, "priya")
    outcomes.record(run, token, "C01", "PROMISE", "called back, will pay Friday", "2026-03-06", 250.0, "priya")

    latest = outcomes.latest(run)[token]
    assert latest["disposition"] == "PROMISE" and latest["promise_amount"] == 250.0
    assert latest["revisions"] == 2
    assert [h["disposition"] for h in outcomes.history(run, token)] == ["NO_ANSWER", "PROMISE"]

    with pytest.raises(Exception, match="append-only"):
        from lastmile.store import db
        with db.connect() as con:
            con.execute("UPDATE attempt_outcomes SET comment = 'rewritten' WHERE run_id = ?", (run,))


def test_an_attempt_must_be_a_known_disposition_and_a_promise_needs_a_date(engine, run):
    from lastmile.governance import outcomes

    token = next(i["account_token"] for i in services.worklist(run) if i["collector_id"])
    for bad in [("MAYBE", None, None), ("PROMISE", None, None), ("NO_ANSWER", "2026-03-06", None)]:
        with pytest.raises(outcomes.OutcomeError):
            outcomes.record(run, token, "C01", bad[0], None, bad[1], bad[2], "priya")


def test_the_worklist_carries_the_state_and_the_report(engine, run):
    """A collector sees where each account stands and what they themselves reported - and that
    report survives the execution projection, because it is their own work, not the manager's frame."""
    from lastmile.governance import outcomes

    items = services.worklist(run)
    token = next(i["account_token"] for i in items if i["collector_id"])
    outcomes.record(run, token, "C01", "REACHED", "spoke to him", None, None, "priya")

    row = next(i for i in services.worklist(run) if i["account_token"] == token)
    assert row["attempt"]["disposition"] == "REACHED" and row["attempt"]["label"] == "Spoke to the customer"
    assert services.work_state(row) == "worked"
    assert services.work_state({"attempt": None, "released": True, "decision": "approved"}) == "released"
    assert services.work_state({"attempt": None, "released": False, "decision": "pending"}) == "awaiting_approval"

    stripped = services.project_items([row], "execution")[0]
    assert stripped["attempt"]["comment"] == "spoke to him" and stripped["state"] == "worked"
    assert all(f not in stripped for f in services.MANAGER_ONLY_ITEM_FIELDS)


def test_a_collector_can_now_record_an_outcome():
    assert services.capabilities("collector", "released", own_queue_only=True)["record_outcome"] is True
    assert services.capabilities("manager", "released", own_queue_only=False)["record_outcome"] is False


def test_the_endpoint_refuses_accounts_that_are_not_yours_or_not_released(monkeypatch, engine, run):
    """A collector signs in, sees their own queue, and can only report on work that actually went out."""
    from fastapi.testclient import TestClient

    from lastmile.api import app as app_mod
    from lastmile.api import auth
    from lastmile.api.app import app
    from lastmile.store import db

    items = services.worklist(run)
    mine = next(i for i in items if i["collector_id"] and i["minutes"] > 0)
    theirs = next(i for i in items if i["collector_id"] and i["collector_id"] != mine["collector_id"])

    pw = "collector-pw-1234"
    with db.connect() as con:
        con.execute("INSERT INTO app_users (username, display_name, role, collector_id, pw_hash, active, created_by, created_at)"
                    " VALUES (?,?,?,?,?,1,?,?)",
                    ("priya", "Priya", "collector", mine["collector_id"], auth.hash_password(pw, 1000), "test", db.now()))
    monkeypatch.setenv("LASTMILE_USERS", "")
    monkeypatch.setenv("LASTMILE_SESSION_SECRET", "test-secret")
    monkeypatch.setattr(app_mod, "_bank_today", lambda: "2026-03-02")
    monkeypatch.setattr(services, "live_run_for", lambda *a, **k: {"run_id": run, "as_of_date": "2026-03-02", "status": "released"})
    auth._failures.clear()
    client = TestClient(app, follow_redirects=False)
    assert client.post("/login", data={"username": "priya", "password": pw}).status_code == 303

    body = client.get("/api/worklist").json()
    assert body["scope"]["collector_id"] == mine["collector_id"] and body["can"]["record_outcome"]
    assert all(i["collector_id"] == mine["collector_id"] for i in body["items"])
    assert "action_value" not in body["items"][0]          # still no money on the wire

    def post(token, **kw):
        return client.post("/api/agent/today/outcome",
                           json={"account_token": token, "disposition": "NO_ANSWER", **kw})

    assert post(theirs["account_token"]).status_code == 404        # not in your queue
    assert post(mine["account_token"]).status_code == 409          # released nothing yet in this fixture
    assert client.get("/api/agent/dispositions").json()["needs_promise"] == "PROMISE"
