"""Claim: nobody reaches a console or API without signing in, each role is sent somewhere it can actually open,
and the roles a person may hand out are capped by their own."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from lastmile.api import auth

PASSWORDS = {"admin": "admin-pw-123", "manager": "manager-pw-123", "collector": "collector-pw-123"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # load_users() merges locally managed accounts, so point the store at an empty directory:
    # otherwise a real app_users row of the same name shadows the account this fixture sets up
    from lastmile.config import settings
    from lastmile.store import db
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    users = ";".join(f"{r}:{r}:{auth.hash_password(pw, 1000)}" for r, pw in PASSWORDS.items())
    monkeypatch.setenv("LASTMILE_USERS", users)
    monkeypatch.setenv("LASTMILE_SESSION_SECRET", "test-secret")
    auth._failures.clear()
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)
    app.include_router(auth.router)

    @app.get("/manager")
    def page():
        return {"page": "manager"}

    @app.post("/api/runs")
    def start(request: Request):
        return {"by": auth.actor(request)}

    @app.put("/api/config/files/{kind}/{pack_id}")
    def save(kind: str, pack_id: str, request: Request):
        return {"by": auth.actor(request)}

    return TestClient(app, follow_redirects=False)


def login(c: TestClient, user: str, pw: str | None = None):
    return c.post("/login", data={"username": user, "password": pw or PASSWORDS[user], "next": "/manager"})


def test_signed_out_is_blocked(client):
    r = client.get("/manager?x=1")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/manager%3Fx%3D1"
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/runs").status_code == 401
    assert client.get("/login").status_code == 200


def test_wrong_password_and_lockout(client):
    for _ in range(auth.MAX_FAILURES):
        assert login(client, "admin", "nope").status_code == 401
    assert login(client, "admin").status_code == 429


def test_roles(client):
    login(client, "collector")
    assert client.get("/manager").status_code == 403          # a page refusal is a page, not a JSON blob
    assert "text/html" in client.get("/manager").headers["content-type"]
    assert client.get("/api/me").json() == {"username": "collector", "role": "collector"}
    assert client.post("/api/runs").status_code == 403

    client.cookies.clear()
    login(client, "manager")
    assert client.post("/api/runs").json() == {"by": "manager"}
    assert client.put("/api/config/files/institutions/x").status_code == 403

    client.cookies.clear()
    r = login(client, "admin")
    assert r.status_code == 303 and r.headers["location"] == "/manager"
    assert client.put("/api/config/files/institutions/x").json() == {"by": "admin"}


def test_forged_expired_or_stale_sessions_rejected(monkeypatch):
    monkeypatch.setenv("LASTMILE_SESSION_SECRET", "test-secret")
    user = auth.User("admin", "admin", auth.hash_password("pw", 1000))
    users = {"admin": user}
    token = auth.make_session(user)
    assert auth.read_session(token, users) == user
    payload, sig = token.rsplit(".", 1)
    assert auth.read_session(payload + "." + sig[::-1], users) is None                     # tampered
    assert auth.read_session(token, users, now=__import__("time").time() + auth.SESSION_S + 1) is None  # expired
    changed = {"admin": auth.User("admin", "admin", auth.hash_password("new-pw", 1000))}
    assert auth.read_session(token, changed) is None                                          # password changed


def test_open_redirect_blocked(client):
    r = client.post("/login", data={"username": "manager", "password": PASSWORDS["manager"], "next": "//evil.example"})
    assert r.headers["location"] == "/guide"


# ------------------------------------------------------- who may create an account for someone else
@pytest.fixture()
def access(monkeypatch, tmp_path):
    """Auth plus the users console, against an empty database."""
    from lastmile.api import users
    from lastmile.config import settings
    from lastmile.store import db

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init(tmp_path)
    creds = ";".join(f"{r}:{r}:{auth.hash_password(pw, 1000)}" for r, pw in PASSWORDS.items())
    monkeypatch.setenv("LASTMILE_USERS", creds)
    monkeypatch.setenv("LASTMILE_SESSION_SECRET", "test-secret")
    auth._failures.clear()
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)
    app.include_router(auth.router)
    app.include_router(users.router)
    return TestClient(app, follow_redirects=False)


def _add(c: TestClient, username: str, role: str, collector_id: str | None = None):
    return c.post("/api/users", json={"username": username, "display_name": username.title(),
                                      "role": role, "password": "a-long-enough-pw", "collector_id": collector_id})


def test_a_manager_staffs_their_floor_but_cannot_mint_a_peer(access):
    """A manager adds collection agents. Creating another manager would propagate access without an
    admin ever being involved, so the server refuses it whatever the form offers."""
    login(access, "manager")
    assert _add(access, "agent.one", "collector", "C01").status_code == 200
    assert _add(access, "agent.two", "manager").status_code == 403
    assert _add(access, "agent.four", "admin").status_code == 403
    # and a collection agent still needs a roster id to have a queue at all
    assert _add(access, "agent.five", "collector").status_code == 422

    body = access.get("/api/users").json()
    assert body["roles"] == ["collector"]                       # the form can only offer this
    assert [u["username"] for u in body["users"]] == ["agent.one"]


def test_an_admin_sees_and_creates_every_role_a_manager_cannot(access):
    login(access, "admin")
    assert _add(access, "boss", "manager").status_code == 200
    assert _add(access, "agent.one", "collector", "C01").status_code == 200
    body = access.get("/api/users").json()
    assert set(body["roles"]) == {"manager", "collector"}
    assert {u["username"] for u in body["users"]} == {"boss", "agent.one"}


def test_a_collector_reaches_nothing_but_their_own_queue(access):
    assert not auth.allowed("collector", "GET", "/admin/users")
    assert not auth.allowed("collector", "POST", "/api/users")
    assert auth.allowed("manager", "POST", "/api/users")
    assert not auth.allowed("manager", "GET", "/admin/config")
    # the retired role now reaches nothing at all, wherever an old session or .env line survives
    for path in ("/manager", "/report", "/admin/config", "/admin/users"):
        assert not auth.allowed("guest", "GET", path)


def test_every_role_lands_somewhere_it_can_open(client):
    """A collector cannot open the guided tour, so the shared default would strand them on a 403
    immediately after a valid sign-in. Sign-in has to resolve the landing page per role."""
    r = client.post("/login", data={"username": "collector", "password": PASSWORDS["collector"]})
    assert r.status_code == 303 and r.headers["location"] == "/agent"
    assert auth.allowed("collector", "GET", r.headers["location"])

    client.cookies.clear()
    # a stale link to a page this role cannot open is corrected rather than followed
    r = client.post("/login", data={"username": "collector", "password": PASSWORDS["collector"], "next": "/admin/config"})
    assert r.headers["location"] == "/agent"

    client.cookies.clear()
    r = client.post("/login", data={"username": "manager", "password": PASSWORDS["manager"]})
    assert r.headers["location"] == "/guide" and auth.allowed("manager", "GET", "/guide")


def test_a_refusal_says_what_is_actually_wrong(client):
    """The old message told everyone it was about saving configuration, whatever they had asked for."""
    login(client, "collector")
    body = client.get("/manager").text
    assert "own queue only" in body and "configuration" not in body
    assert '/agent' in body                                  # and offers the way back


def test_a_retired_role_in_the_environment_does_not_stop_the_engine(monkeypatch, tmp_path):
    """An .env written before guest was withdrawn must not raise on startup; the entry is skipped."""
    from lastmile.config import settings
    from lastmile.store import db
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    users = auth.load_users(f"admin:admin:{auth.hash_password('x', 1000)};guest:guest:{auth.hash_password('y', 1000)}")
    assert set(users) == {"admin"}
    with pytest.raises(ValueError):
        auth.load_users(f"someone:wizard:{auth.hash_password('z', 1000)}")
