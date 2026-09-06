"""Login / logout / status endpoints. All three are intentionally public
(no `require_auth` dependency, see app/main.py's router registration) —
login obviously cannot require a session, and logout/status are safe to
call from a logged-out browser (clearing an empty session, or reporting
`authenticated: false`, are both no-ops with no sensitive data disclosed).
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import (
    check_login_rate_limit,
    record_login_failure,
    record_login_success,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    check_login_rate_limit(request)
    if not verify_password(payload.password):
        record_login_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    record_login_success(request)
    request.session["authenticated"] = True
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/status")
def auth_status(request: Request):
    return {"authenticated": bool(request.session.get("authenticated"))}
