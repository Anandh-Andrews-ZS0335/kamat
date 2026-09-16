"""Login for the consoles: three roles, a signed session cookie, and one gate in front of every page and API call.

  admin    everything, including saving configuration
  manager  operates the business day (team, runs, approvals, release, close); can read but not save configuration
  guest    read-only: every page and GET endpoint, no changes

Accounts come from LASTMILE_USERS in the environment, never from the repository:
  LASTMILE_USERS=admin:admin:<hash>;manager:manager:<hash>;guest:guest:<hash>
Create them with `python -m lastmile.api.auth users` (prints random passwords once, and the line for .env).
Standard library only: PBKDF2-SHA256 password hashes, HMAC-SHA256 signed cookies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from lastmile.config.settings import TOKEN_SECRET

ROLES = ("admin", "manager", "guest")
COOKIE = "lastmile_session"
SESSION_S = 8 * 3600
PBKDF2_ITERATIONS = 600_000
MAX_FAILURES, LOCK_S = 5, 15 * 60
PUBLIC_PATHS = {"/login", "/logout"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class User:
    username: str
    role: str
    pw_hash: str


# ------------------------------------------------------------------------------------ passwords
def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256.{iterations}.{salt}.{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split(".")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), digest)


def load_users(raw: str | None = None) -> dict[str, User]:
    raw = os.environ.get("LASTMILE_USERS", "") if raw is None else raw
    users = {}
    for entry in filter(None, (e.strip() for e in raw.split(";"))):
        username, role, pw_hash = entry.split(":", 2)
        if role not in ROLES:
            raise ValueError(f"LASTMILE_USERS: unknown role {role!r} for {username!r}")
        users[username] = User(username, role, pw_hash)
    return users


# ------------------------------------------------------------------------------------- sessions
def _secret() -> bytes:
    explicit = os.environ.get("LASTMILE_SESSION_SECRET")
    if explicit:
        return explicit.encode()
    return hmac.new(TOKEN_SECRET.encode(), b"lastmile-session-cookie", hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(payload: str) -> str:
    return _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())


def make_session(user: User, now: float | None = None) -> str:
    # pw is a fingerprint of the password hash: changing a password ends that account's sessions.
    body = {"u": user.username, "exp": int((now or time.time()) + SESSION_S), "pw": user.pw_hash[-12:]}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None, users: dict[str, User], now: float | None = None) -> User | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    user = users.get(body.get("u"))
    if not user or body.get("exp", 0) < (now or time.time()) or body.get("pw") != user.pw_hash[-12:]:
        return None
    return user


def allowed(role: str, method: str, path: str) -> bool:
    if role == "admin" or method in READ_METHODS:
        return True
    if role == "manager":
        return not (method == "PUT" and path.startswith("/api/config/files/"))
    return False


def actor(request: Request) -> str:
    """The logged-in username, recorded in approvals, releases and the audit chain instead of a client-supplied name."""
    user = getattr(request.state, "user", None)
    return user.username if user else "anonymous"


# ------------------------------------------------------------------------------------- the gate
class AuthMiddleware:
    """Every request needs a valid session except the login page. Pages redirect to /login; APIs answer 401/403."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = request.url.path, request.method
        if path in PUBLIC_PATHS:
            return await self.app(scope, receive, send)
        user = read_session(request.cookies.get(COOKIE), load_users())
        if user is None:
            if path.startswith("/api/") or method not in READ_METHODS:
                response = JSONResponse({"detail": "sign in required"}, status_code=401)
            else:
                target = path + (f"?{request.url.query}" if request.url.query else "")
                response = RedirectResponse(f"/login?next={quote(target)}", status_code=303)
            return await response(scope, receive, send)
        if not allowed(user.role, method, path):
            msg = ("guest accounts are read-only" if user.role == "guest"
                   else "only an admin can save configuration changes")
            return await JSONResponse({"detail": msg}, status_code=403)(scope, receive, send)
        scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)


# --------------------------------------------------------------------------------- login routes
router = APIRouter()
_failures: dict[str, list[float]] = {}


def _locked(key: str, now: float) -> bool:
    recent = [t for t in _failures.get(key, []) if now - t < LOCK_S]
    _failures[key] = recent
    return len(recent) >= MAX_FAILURES


def _safe_next(target: str | None) -> str:
    # only same-site relative paths, so the login page cannot be used to bounce people elsewhere
    return target if target and target.startswith("/") and not target.startswith("//") and "\\" not in target else "/guide"


def _page(error: str = "", next_path: str = "/guide", status: int = 200) -> HTMLResponse:
    err = f'<p class="err" role="alert">{escape(error)}</p>' if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{{error}}", err).replace("{{next}}", escape(next_path)), status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: str | None = None):
    if read_session(request.cookies.get(COOKIE), load_users()):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _page(next_path=_safe_next(next))


@router.post("/login", include_in_schema=False)
async def login(request: Request):
    form = parse_qs((await request.body()).decode(errors="replace"))
    username = form.get("username", [""])[0].strip()
    password = form.get("password", [""])[0]
    next_path = _safe_next(form.get("next", [None])[0])
    ip, now = request.client.host if request.client else "?", time.time()
    if _locked(f"ip:{ip}", now) or _locked(f"user:{username}", now):
        return _page("Too many failed attempts. Try again in 15 minutes.", next_path, 429)
    user = load_users().get(username)
    if not user or not verify_password(password, user.pw_hash):
        if not user:
            verify_password(password, hash_password("timing-equaliser", 1000))
        # only real usernames get a counter, so guessing random names cannot grow this dict without bound
        for key in (f"ip:{ip}", *([f"user:{username}"] if user else [])):
            _failures.setdefault(key, []).append(now)
        return _page("Incorrect username or password.", next_path, 401)
    _failures.pop(f"user:{username}", None)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(COOKIE, make_session(user), max_age=SESSION_S, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return response


@router.post("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/api/me")
def me(request: Request):
    user = request.state.user
    return {"username": user.username, "role": user.role}


LOGIN_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Last Mile</title>
<style>
/* Self-contained: the stylesheets live behind the sign-in gate, so this page carries its own. */
:root{--ground:#EEF2F2;--surface:#FFFFFF;--surface-2:#F6F9F9;--line:#DBE4E4;--line-strong:#C0CDCD;
  --ink:#11262A;--ink-2:#44585B;--ink-3:#6C7E80;--brand:#10535E;--on-brand:#fff;--bad:#9E3B34;--bad-soft:#F8E4E1;
  --sh:0 1px 2px rgba(16,32,36,.06),0 20px 50px -30px rgba(16,32,36,.45)}
@media (prefers-color-scheme:dark){:root{--ground:#0B1113;--surface:#121A1D;--surface-2:#172124;--line:#243336;--line-strong:#35484C;
  --ink:#E6EEEE;--ink-2:#AEBFC0;--ink-3:#7E9294;--brand:#5FB6C0;--on-brand:#05181C;--bad:#E0776E;--bad-soft:#321A18;
  --sh:0 1px 2px rgba(0,0,0,.5),0 24px 60px -34px rgba(0,0,0,.95)}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:32px 16px;background:var(--ground);color:var(--ink);
  font:14px/1.55 "Archivo","Inter","Helvetica Neue",Arial,sans-serif}
.card{width:100%;max-width:380px;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--sh);
  padding:28px 28px 24px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:20px}
.mark{width:34px;height:34px;border-radius:9px;background:var(--brand);color:var(--on-brand);display:grid;place-items:center;
  font-weight:800;font-size:14px;letter-spacing:-.02em}
.brand b{display:block;font-size:16px;letter-spacing:-.01em}
.brand small{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);font-weight:700}
h1{margin:0;font-size:19px;letter-spacing:-.01em}
.sub{margin:4px 0 20px;color:var(--ink-3);font-size:13px}
label{display:block;font-weight:600;font-size:12px;margin:14px 0 6px;color:var(--ink-2)}
input{width:100%;padding:10px 12px;font:inherit;color:inherit;background:var(--surface-2);border:1px solid var(--line-strong);border-radius:7px}
input:focus-visible{outline:2px solid var(--brand);outline-offset:1px}
button{width:100%;margin-top:22px;padding:11px;font:inherit;font-weight:600;border:0;border-radius:7px;background:var(--brand);
  color:var(--on-brand);cursor:pointer}
button:hover{filter:brightness(1.08)}
.err{margin:0 0 4px;padding:9px 11px;border-radius:7px;background:var(--bad-soft);color:var(--bad);font-size:13px}
.roles{margin:20px 0 0;padding-top:16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink-3);line-height:1.6}
.roles b{color:var(--ink-2)}
</style></head>
<body>
<form class="card" method="post" action="/login">
  <div class="brand"><span class="mark">LM</span><div><b>Last Mile</b><small>Collections decisions</small></div></div>
  <h1>Sign in</h1><p class="sub">Use the account your administrator gave you.</p>
  {{error}}
  <input type="hidden" name="next" value="{{next}}">
  <label for="username">Username</label>
  <input id="username" name="username" autocomplete="username" required autofocus>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  <p class="roles"><b>admin</b> — everything, including saving configuration.<br>
    <b>manager</b> — runs the business day: team, runs, approvals, release.<br>
    <b>guest</b> — read-only.</p>
</form>
</body></html>"""


# ------------------------------------------------------------------------------------------ CLI
def _cli() -> None:
    if sys.argv[1:] != ["users"]:
        sys.exit("usage: python -m lastmile.api.auth users")
    creds = {role: secrets.token_urlsafe(15) for role in ROLES}
    print("Passwords (shown once; store them safely):")
    for role, pw in creds.items():
        print(f"  {role:<8} {pw}")
    line = ";".join(f"{role}:{role}:{hash_password(pw)}" for role, pw in creds.items())
    print(f"\nAdd to .env:\nLASTMILE_USERS={line}\nLASTMILE_SESSION_SECRET={secrets.token_hex(32)}")


if __name__ == "__main__":
    _cli()
