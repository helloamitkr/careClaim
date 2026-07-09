"""Patient portal API.

Note the route shapes: `/api/portal/me/...`. There is no `/api/portal/cases/{id}`
and no endpoint anywhere that accepts a `patient_id`. The authorization key is
read from the server-side session and nothing else, which makes the usual
"change the id in the URL" attack unrepresentable rather than merely blocked.

Every PHI read writes a phi_access_log row before the response is returned, and
every denial writes one too — the denials are the IDOR-attempt signal.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from loguru import logger

from carebridge.portal import auth, repository
from carebridge.portal.audit import AuditWriteError, PhiAccessAudit
from carebridge.portal.bot import context as bot_context
from carebridge.portal.bot import intent
from carebridge.portal.bot.answer import answer_status_question
from carebridge.portal.schemas import (
    ChatMessageIn,
    ChatReplyOut,
    DevSignInRequest,
    EnrollRequest,
    IssueEnrollmentRequest,
    LoginRequest,
    LoginTokenRequest,
    PortalCaseOut,
)
from carebridge.staff_auth import dev_mode, require_staff

router = APIRouter(prefix="/api/portal", tags=["portal"])

# Set PORTAL_COOKIE_SECURE=false only for local http development.
_COOKIE_SECURE = os.environ.get("PORTAL_COOKIE_SECURE", "true").strip().lower() != "false"


@lru_cache(maxsize=1)
def _engine():
    """App-role engine: owns portal_user / sessions / tokens / audit rows.
    Clinical reads do *not* go through this — see repository.portal_engine().

    Cached: a fresh Database() per request would build a new connection pool
    per request.
    """
    from carebridge.persistence import Database

    return Database().engine


def _audit() -> PhiAccessAudit:
    return PhiAccessAudit(_engine())


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def current_session(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=auth.SESSION_COOKIE),
) -> auth.Session:
    if not session_cookie:
        raise HTTPException(status_code=401, detail="Not signed in")
    session = auth.resolve_session(_engine(), cookie_value=session_cookie)
    if session is None:
        raise HTTPException(status_code=401, detail="Session expired")
    request.state.portal_session = session
    return session


# ---------------------------------------------------------------------------
# Enrollment — staff mints, patient redeems
# ---------------------------------------------------------------------------


@router.post("/admin/enrollment-tokens", status_code=201)
async def issue_enrollment(
    body: IssueEnrollmentRequest,
    request: Request,
    staff: str = Depends(require_staff),
) -> dict[str, str]:
    token = auth.issue_enrollment_token(
        _engine(), patient_id=body.patient_id, issued_by=staff
    )
    _audit().record(
        actor_type="staff",
        actor_id=staff,
        action="issue_enrollment_token",
        outcome="allow",
        patient_id=body.patient_id,
        source_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    # Returned once. In production this is handed to the patient out of band,
    # not echoed to whoever called the API.
    return {"enrollment_token": token}


@router.post("/auth/enroll", status_code=201)
async def enroll(body: EnrollRequest, request: Request) -> dict[str, str]:
    try:
        patient_id = auth.redeem_enrollment_token(
            _engine(), token=body.token, email=body.email
        )
    except auth.EnrollmentError:
        _audit().record(
            actor_type="patient",
            actor_id="anonymous",
            action="enroll",
            outcome="deny",
            source_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=400, detail="Invalid or expired enrollment token")

    _audit().record(
        actor_type="patient",
        actor_id=patient_id,
        action="enroll",
        outcome="allow",
        patient_id=patient_id,
        source_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "enrolled"}


# ---------------------------------------------------------------------------
# Passwordless login
# ---------------------------------------------------------------------------


@router.post("/auth/login")
async def request_login_link(body: LoginRequest, request: Request) -> dict[str, str]:
    """Always returns the same body, whether or not the account exists.

    For a patient portal the existence of an account is itself PHI: it discloses
    that this person was discharged from this facility.
    """
    token = auth.request_login_token(_engine(), email=body.email)

    _audit().record(
        actor_type="patient",
        actor_id="anonymous",
        action="request_login_link",
        outcome="allow" if token else "deny",
        source_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    message = "If that email is registered, a sign-in link has been sent."

    # PRODUCTION: mail this link. Never log it, never return it. There is no
    # mailer locally, so behind an explicit dev flag we hand it straight back —
    # which is exactly the account-takeover primitive the flag's name warns about.
    if token and os.environ.get("PORTAL_DEV_ECHO_TOKENS", "").lower() == "true":
        logger.bind(component="portal").warning(
            "PORTAL_DEV_ECHO_TOKENS is on — returning a login token over the API. "
            "This must never be set in production."
        )
        return {"status": message, "dev_login_token": token}

    return {"status": message}


@router.post("/auth/session")
async def create_session(body: LoginTokenRequest, response: Response, request: Request):
    try:
        cookie_value = auth.redeem_login_token(_engine(), token=body.token)
    except auth.AuthError:
        _audit().record(
            actor_type="patient",
            actor_id="anonymous",
            action="login",
            outcome="deny",
            source_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in link")

    session = auth.resolve_session(_engine(), cookie_value=cookie_value)
    assert session is not None  # just minted

    response.set_cookie(
        auth.SESSION_COOKIE,
        cookie_value,
        httponly=True,       # a JWT in localStorage is XSS-exfiltratable
        secure=_COOKIE_SECURE,
        samesite="strict",   # the whole of our CSRF defence — see auth.py
        max_age=int(auth.SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
        path="/api/portal",
    )
    _audit().record(
        actor_type="patient",
        actor_id=session.patient_id,
        action="login",
        outcome="allow",
        patient_id=session.patient_id,
        source_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "signed_in"}


@router.post("/auth/dev-session")
async def dev_session(body: DevSignInRequest, response: Response, request: Request):
    """DEV ONLY — sign in with just a username (the patient_id). No enrollment
    code, no magic link, no staff token: the same "username + role" model the
    clinician page uses.

    404s unless PORTAL_DEV_MODE=true, so this route does not exist in production.
    Row-level security, the repository chokepoint, and the PHI access log all
    still apply to the session it mints.
    """
    if not dev_mode():
        raise HTTPException(status_code=404, detail="Not found")

    patient_id = body.username.strip()
    if not patient_id:
        raise HTTPException(status_code=422, detail="A username is required")

    cookie_value = auth.create_session_for(_engine(), patient_id=patient_id)
    response.set_cookie(
        auth.SESSION_COOKIE,
        cookie_value,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="strict",
        max_age=int(auth.SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
        path="/api/portal",
    )
    _audit().record(
        actor_type="patient",
        actor_id=patient_id,
        action="dev_login",
        outcome="allow",
        patient_id=patient_id,
        source_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "signed_in", "patient_id": patient_id}


@router.post("/auth/logout")
async def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias=auth.SESSION_COOKIE),
) -> dict[str, str]:
    if session_cookie:
        auth.revoke_session(_engine(), cookie_value=session_cookie)
    response.delete_cookie(auth.SESSION_COOKIE, path="/api/portal")
    return {"status": "signed_out"}


# ---------------------------------------------------------------------------
# The PHI reads. Note: no patient_id or case-owner parameter anywhere.
# ---------------------------------------------------------------------------


@router.get("/me/cases", response_model=list[PortalCaseOut])
async def my_cases(
    request: Request, session: auth.Session = Depends(current_session)
) -> list[PortalCaseOut]:
    rows = repository.fetch_my_cases(session.patient_id)
    try:
        _audit().record(
            actor_type="patient",
            actor_id=session.patient_id,
            action="list_cases",
            outcome="allow",
            patient_id=session.patient_id,
            source_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuditWriteError:
        # Fail closed: an unauditable disclosure of PHI does not happen.
        raise HTTPException(status_code=503, detail="Temporarily unavailable")
    return [PortalCaseOut.from_row(r) for r in rows]


@router.get("/me/cases/{case_id}", response_model=PortalCaseOut)
async def my_case(
    case_id: str, request: Request, session: auth.Session = Depends(current_session)
) -> PortalCaseOut:
    row = repository.fetch_my_case(session.patient_id, case_id)

    ip, ua = _client_ip(request), request.headers.get("user-agent")
    try:
        _audit().record(
            actor_type="patient",
            actor_id=session.patient_id,
            action="read_case",
            outcome="allow" if row else "deny",
            patient_id=session.patient_id,
            case_id=case_id,
            source_ip=ip,
            user_agent=ua,
        )
    except AuditWriteError:
        raise HTTPException(status_code=503, detail="Temporarily unavailable")

    if row is None:
        # 404, never 403: a 403 would confirm the case_id exists, turning this
        # endpoint into an enumeration oracle for other patients' case ids.
        raise HTTPException(status_code=404, detail="Case not found")
    return PortalCaseOut.from_row(row)


@router.post("/me/cases/{case_id}/chat", response_model=ChatReplyOut)
async def chat(
    case_id: str,
    body: ChatMessageIn,
    request: Request,
    session: auth.Session = Depends(current_session),
) -> ChatReplyOut:
    """Ask the assistant why this case is held up.

    The four controls, in the order this function applies them, are described in
    carebridge/portal/bot/__init__.py. Note especially that the clinical-question
    refusal happens before any context is fetched: a patient asking "should I stop
    my beta blocker?" never causes a PHI read at all.
    """
    try:
        question = intent.validate(body.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ip, ua = _client_ip(request), request.headers.get("user-agent")

    # (2) Refused in Python, before the model and before the database.
    if intent.is_clinical_question(question):
        try:
            _audit().record(
                actor_type="patient",
                actor_id=session.patient_id,
                action="chat_refused_clinical",
                outcome="deny",
                patient_id=session.patient_id,
                case_id=case_id,
                source_ip=ip,
                user_agent=ua,
            )
        except AuditWriteError:
            raise HTTPException(status_code=503, detail="Temporarily unavailable")
        return ChatReplyOut(reply=intent.CARE_TEAM_REFUSAL, refused=True)

    # (3) RLS-bounded. None for "no such case" and for "not yours", alike.
    context = bot_context.fetch_case_context(session.patient_id, case_id)

    try:
        _audit().record(
            actor_type="patient",
            actor_id=session.patient_id,
            action="chat",
            outcome="allow" if context else "deny",
            patient_id=session.patient_id,
            case_id=case_id,
            source_ip=ip,
            user_agent=ua,
        )
    except AuditWriteError:
        raise HTTPException(status_code=503, detail="Temporarily unavailable")

    if context is None:
        raise HTTPException(status_code=404, detail="Case not found")

    # (4) sanitize() runs inside answer_status_question(). `refused` is also set
    # there, when the model classifies the question as not about the care plan.
    answer = answer_status_question(context, question)
    return ChatReplyOut(reply=answer.text, refused=answer.refused)
