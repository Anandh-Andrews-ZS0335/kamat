"""Console accounts. An admin manages everyone; a collections manager may add collection agents only."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lastmile.api import auth
from lastmile.governance import audit
from lastmile.store import db

router = APIRouter()
USER_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,80}$")


# Which roles each actor may create and see. A manager staffs their own floor and nothing more:
# letting them mint another manager would propagate access without an admin ever being involved.
GRANTABLE = {"admin": ("manager", "collector"), "manager": ("collector",)}


def _may_manage(request: Request) -> tuple[str, tuple[str, ...]]:
    role = request.state.user.role
    if role not in GRANTABLE:
        raise HTTPException(403, "only an admin or a collections manager can manage user access")
    return role, GRANTABLE[role]


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=100)
    role: str
    password: str = Field(min_length=12, max_length=256)
    collector_id: str | None = Field(default=None, max_length=40)


@router.get("/api/users")
def list_users(request: Request):
    """Every account for an admin; only the collection agents for a manager."""
    _, grantable = _may_manage(request)
    placeholders = ",".join("?" * len(grantable))
    with db.connect() as con:
        users = db.rows(con, "SELECT username, display_name, role, collector_id, active, created_by, created_at "
                             f"FROM app_users WHERE role IN ({placeholders}) ORDER BY role, display_name",
                        tuple(grantable))
    return {"users": users, "roles": list(grantable)}


@router.post("/api/users")
def create_user(body: UserIn, request: Request):
    actor_role, grantable = _may_manage(request)
    username = body.username.strip()
    if not USER_RE.fullmatch(username):
        raise HTTPException(422, "username may contain only letters, numbers, dot, underscore and hyphen")
    if body.role not in grantable:
        raise HTTPException(403, "admins are provisioned outside this console" if body.role == "admin" else
                            f"a {actor_role} may only create: {', '.join(grantable)}")
    if body.role == "collector" and not (body.collector_id or "").strip():
        raise HTTPException(422, "a collection agent needs a collector ID matching the daily roster")
    if body.role != "collector" and body.collector_id:
        raise HTTPException(422, "only collection-agent accounts may have a collector ID")
    try:
        with db.connect() as con:
            con.execute("INSERT INTO app_users (username, display_name, role, collector_id, pw_hash, active, created_by, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (username, body.display_name.strip(), body.role, (body.collector_id or "").strip() or None,
                         auth.hash_password(body.password), 1, auth.actor(request), db.now()))
    except Exception as e:
        raise HTTPException(409, f"could not create user: {e}") from e
    audit.append(None, "user.created", auth.actor(request), {"username": username, "role": body.role,
                                                               "collector_id": body.collector_id})
    return {"created": True, "username": username}
