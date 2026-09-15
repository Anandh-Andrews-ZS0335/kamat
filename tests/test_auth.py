"""Claim: nobody reaches a console or API without signing in, guests cannot change anything, and only admins save configuration."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from lastmile.api import auth

PASSWORDS = {"admin": "admin-pw-123", "manager": "manager-pw-123", "guest": "guest-pw-123"}


@pytest.fixture()
def client(monkeypatch):
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
    login(client, "guest")
    assert client.get("/manager").json() == {"page": "manager"}
    assert client.get("/api/me").json() == {"username": "guest", "role": "guest"}
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
    r = client.post("/login", data={"username": "guest", "password": PASSWORDS["guest"], "next": "//evil.example"})
    assert r.headers["location"] == "/guide"
