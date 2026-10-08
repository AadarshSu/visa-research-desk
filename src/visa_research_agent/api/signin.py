"""Signing in with Ofself (TODO item 55).

The flow, as Ofself's authorize page actually runs it — read from the page's own code on
2026-09-17, because the developer guide shows it only through SDK helpers it does not define:

1. `/oauth/login` sends the browser to `app.ofself.ai/authorize?client_id=…&redirect_uri=…`.
2. The person approves there. The page sends the browser to the redirect URI with
   `code=success`, `client_id`, `user_id`, `username` and a single-use `sid_code`.
3. `/oauth/callback` exchanges `sid_code` with Paradigm, server to server, using this app's API key.
   The user id it answers is the only one this app believes.

**Nothing in the callback's query string is trusted.** `code` is the literal `success`, and
`user_id` is whatever the address says, so a callback that read it would sign in as anyone a link
names. A missing `sid_code`, another app's `client_id`, or a `user_id` that disagrees with the
exchange is refused.

**What the session holds is the Ofself user id and nothing else**, in a cookie signed with
`SESSION_SECRET` and expiring after `SESSION_MAX_AGE_HOURS`. No traveller detail is stored, here or
anywhere, under that id (DECISIONS entry 180).

**One limit this cannot close from our side.** The authorize page does not echo a `state` value, so
a callback cannot be tied to the login that started it. A short-lived cookie set by `/oauth/login`
at least refuses a callback in a browser that never began signing in. The gap is recorded in
`OFSELF_FEEDBACK.md`.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from visa_research_agent.api.allowance import (
    AllowanceSpent,
    AllowanceStoreError,
    AnonymousAllowance,
)
from visa_research_agent.api.countries import normalise_country
from visa_research_agent.api.ofself import (
    OfselfAuthorizationLost,
    OfselfError,
    OfselfIdentity,
    OfselfSignInRejected,
    OfselfWriteRefused,
    SavedTrip,
    TravellerDefaults,
    TripToSave,
)
from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.domain.models import StrictModel, TravelPurpose, TripDates

SESSION_COOKIE = "visa_desk_session"
PENDING_COOKIE = "visa_desk_signin"
PENDING_MAX_AGE_SECONDS = 600
"""Ten minutes from starting sign-in to coming back, for the approval page to be read."""


logger = logging.getLogger(__name__)

_SESSION_CODE = re.compile(r"(sid_code=)[^&\s\"]+")


class RedactSessionCodes(logging.Filter):
    """Keep a sign-in's single-use code out of the access log.

    Ofself puts `sid_code` in the callback's address, and uvicorn logs every address in full. A
    callback refused before its exchange leaves that code unredeemed, and whoever reads the log
    could redeem it and be signed in as that user. Seen in this app's own log on 2026-09-17.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SESSION_CODE.sub(r"\1[redacted]", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _SESSION_CODE.sub(r"\1[redacted]", record.msg)
        return True


def redact_session_codes_from_access_log() -> None:
    """Install the redaction on uvicorn's access logger, once however many apps are built."""

    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, RedactSessionCodes) for existing in access.filters):
        access.addFilter(RedactSessionCodes())


class SignedCookies:
    """A JSON payload signed with HMAC-SHA256 and stamped with when it was issued.

    Written here rather than adding a dependency for it: the payload is one id, and the whole
    contract is "reject anything altered or too old", which the standard library covers.
    """

    def __init__(self, secret: str, *, now: Callable[[], float] = time.time) -> None:
        if len(secret) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        self._key = secret.encode("utf-8")
        self._now = now

    def sign(self, purpose: str, payload: dict[str, str]) -> str:
        """Sign a payload for one purpose, so a cookie made for one use is refused for another."""

        stamped = {**payload, "purpose": purpose, "issued_at": int(self._now())}
        body = json.dumps(stamped, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
        return f"{encoded}.{self._signature(encoded)}"

    def verify(
        self, token: str | None, purpose: str, *, max_age_seconds: float
    ) -> dict[str, str] | None:
        """The payload, or None if missing, altered, malformed, expired or made for another use."""

        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._signature(encoded)):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        issued_at = payload.pop("issued_at", None)
        if not isinstance(issued_at, int) or self._now() - issued_at > max_age_seconds:
            return None
        if payload.pop("purpose", None) != purpose:
            return None
        if not all(isinstance(key, str) and isinstance(v, str) for key, v in payload.items()):
            return None
        return payload

    def _signature(self, encoded: str) -> str:
        return hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SignIn:
    """Everything sign-in needs, built only when all of it is configured."""

    identity: OfselfIdentity
    cookies: SignedCookies
    client_id: str
    authorize_url: str
    redirect_uri: str
    session_max_age_seconds: float
    secure_cookies: bool

    def signed_in_user(self, request: Request) -> str | None:
        """The Ofself user id this browser signed in as, or None."""

        session = self.cookies.verify(
            request.cookies.get(SESSION_COOKIE),
            SESSION_COOKIE,
            max_age_seconds=self.session_max_age_seconds,
        )
        return session.get("user_id") if session else None


def get_sign_in() -> SignIn | None:
    """Sign-in as configured, or None while any part of it is missing."""

    if (
        not settings.paradigm_client_id
        or settings.paradigm_api_key is None
        or settings.session_secret is None
    ):
        return None
    return SignIn(
        identity=OfselfIdentity(
            settings.paradigm_api_key.get_secret_value(),
            base_url=settings.paradigm_base_url,
            timeout_seconds=settings.paradigm_timeout_seconds,
        ),
        cookies=SignedCookies(settings.session_secret.get_secret_value()),
        client_id=settings.paradigm_client_id,
        authorize_url=settings.paradigm_authorize_url,
        redirect_uri=settings.paradigm_redirect_uri,
        session_max_age_seconds=settings.session_max_age_hours * 3600,
        secure_cookies=settings.session_cookie_secure,
    )


def require_sign_in(sign_in: Annotated[SignIn | None, Depends(get_sign_in)]) -> SignIn:
    if sign_in is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": (
                    "Sign-in with Ofself is not configured: PARADIGM_CLIENT_ID, PARADIGM_API_KEY "
                    "and SESSION_SECRET must all be set."
                )
            },
        )
    return sign_in


def require_signed_in_for_plans(
    request: Request, sign_in: Annotated[SignIn | None, Depends(get_sign_in)]
) -> str | None:
    """The Ofself user a plan is spent for, or None where `REQUIRE_SIGN_IN` is off.

    A plan costs searches and two model calls, so a public address without this is a public wallet
    (TODO item 7, step 5). It fails closed: required but not configured refuses every plan rather
    than quietly serving them anonymously (DECISIONS entry 191).
    """

    if not settings.require_sign_in:
        return None
    if sign_in is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": (
                    "Plans need sign-in with Ofself, which is not configured here: "
                    "PARADIGM_CLIENT_ID, PARADIGM_API_KEY and SESSION_SECRET must all be set, or "
                    "REQUIRE_SIGN_IN set to false where nobody else can reach this app."
                )
            },
        )
    user_id = sign_in.signed_in_user(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Sign in with Ofself to generate a plan.", "sign_in": True},
        )
    return user_id


def get_anonymous_allowance() -> AnonymousAllowance:
    return AnonymousAllowance(settings.allowance_file, settings.anonymous_plan_allowance)


def allowance_spent(limit: int, sign_in_available: bool) -> HTTPException:
    if sign_in_available:
        message = (
            f"You have used your {limit} free plans. Sign in with Ofself to keep generating plans."
        )
    else:
        message = f"The {limit} free plans for your connection are used up on this server."
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"message": message, "sign_in": sign_in_available, "allowance_spent": True},
    )


@dataclass
class PlanGate:
    """Who a plan is spent for: a signed-in Ofself user, or an address with free plans left."""

    user_id: str | None
    allowance: AnonymousAllowance | None = None
    address: str = ""
    sign_in_available: bool = False

    def spend(self) -> int | None:
        """Count this plan against the address and return how many are left; None if uncounted."""

        if self.allowance is None:
            return None
        try:
            return self.allowance.spend(self.address)
        except AllowanceSpent as spent:
            raise allowance_spent(spent.limit, self.sign_in_available) from spent
        except AllowanceStoreError as exc:
            # Uncounted would be unlimited: a plan that cannot be counted is not given.
            logger.error("anonymous plan allowance unavailable: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": (
                        "Free plans are unavailable just now. Sign in with Ofself, or try later."
                    ),
                    "sign_in": self.sign_in_available,
                },
            ) from exc


def plan_gate(
    request: Request,
    sign_in: Annotated[SignIn | None, Depends(get_sign_in)],
    allowance: Annotated[AnonymousAllowance, Depends(get_anonymous_allowance)],
) -> PlanGate:
    """Let a plan through for a signed-in user, or for an address with free plans left.

    Only checks: the route spends the plan once the request is valid (DECISIONS entry 262). With
    `REQUIRE_SIGN_IN` on there are no free plans, as entry 191 had it.
    """

    user_id = sign_in.signed_in_user(request) if sign_in is not None else None
    if user_id is not None:
        return PlanGate(user_id)
    if settings.require_sign_in:
        return PlanGate(require_signed_in_for_plans(request, sign_in))
    address = request.client.host if request.client else "unknown"
    try:
        left = allowance.remaining(address)
    except AllowanceStoreError:
        left = 1  # spend() refuses it, with its own message
    if left <= 0:
        raise allowance_spent(allowance.limit, sign_in is not None)
    return PlanGate(None, allowance, address, sign_in is not None)


router = APIRouter(prefix="/oauth", tags=["sign-in"])


@router.get("/login", include_in_schema=False)
async def login(sign_in: Annotated[SignIn, Depends(require_sign_in)]) -> RedirectResponse:
    query = urlencode({"client_id": sign_in.client_id, "redirect_uri": sign_in.redirect_uri})
    response = RedirectResponse(
        f"{sign_in.authorize_url}?{query}", status_code=status.HTTP_303_SEE_OTHER
    )
    response.set_cookie(
        PENDING_COOKIE,
        sign_in.cookies.sign(PENDING_COOKIE, {"nonce": secrets.token_urlsafe(16)}),
        max_age=PENDING_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=sign_in.secure_cookies,
    )
    return response


@router.get("/callback", include_in_schema=False)
async def callback(
    request: Request, sign_in: Annotated[SignIn, Depends(require_sign_in)]
) -> RedirectResponse:
    query = request.query_params
    if (
        sign_in.cookies.verify(
            request.cookies.get(PENDING_COOKIE),
            PENDING_COOKIE,
            max_age_seconds=PENDING_MAX_AGE_SECONDS,
        )
        is None
    ):
        raise _refused("Sign-in was not started from this browser, or took too long. Start again.")
    if query.get("error") or query.get("code") != "success":
        raise _refused("Ofself did not approve this sign-in.")
    if query.get("client_id") != sign_in.client_id:
        raise _refused("This sign-in was for a different app.")

    try:
        user_id = await sign_in.identity.exchange_session_code(query.get("sid_code") or "")
    except OfselfSignInRejected as exc:
        raise _refused(str(exc)) from exc
    except OfselfError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": str(exc)}
        ) from exc

    claimed = query.get("user_id")
    if claimed and claimed.lower() != user_id:
        raise _refused("The sign-in named a different user than Ofself confirmed.")

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        sign_in.cookies.sign(SESSION_COOKIE, {"user_id": user_id}),
        max_age=int(sign_in.session_max_age_seconds),
        httponly=True,
        samesite="lax",
        secure=sign_in.secure_cookies,
    )
    response.delete_cookie(PENDING_COOKIE)
    return response


@router.get("/session")
async def session(
    request: Request, sign_in: Annotated[SignIn | None, Depends(get_sign_in)]
) -> dict[str, object]:
    """Whether this browser is signed in with Ofself, and as whom."""

    user_id = sign_in.signed_in_user(request) if sign_in else None
    return {"configured": sign_in is not None, "signed_in": user_id is not None, "user_id": user_id}


@router.get("/traveller", response_model=TravellerDefaults)
async def traveller(
    request: Request, sign_in: Annotated[SignIn, Depends(require_sign_in)]
) -> TravellerDefaults | JSONResponse:
    """What the signed-in traveller's Ofself account holds that can start the form.

    Passports, a residence permit and journeys being considered — each offered, never decided. One
    passport fills the field in; several are offered with none chosen; none leaves it for the
    traveller (DECISIONS entries 180 and 181). Nothing read here is kept, and none of it reaches a
    plan except through the form the traveller submits.
    """

    user_id = sign_in.signed_in_user(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Sign in with Ofself first.", "sign_in": True},
        )
    try:
        return await sign_in.identity.traveller_defaults(user_id, today=date.today())
    except OfselfAuthorizationLost as exc:
        return grant_lost(exc)
    except OfselfError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": str(exc)}
        ) from exc


class SaveTripRequest(StrictModel):
    """The trip on the form, as the plan beside the button was researched for."""

    destination: str = Field(min_length=1, max_length=80)
    """A destination's slug as the page lists it, or a country code."""

    purpose: TravelPurpose
    trip: TripDates | None = None


@router.post("/trip", response_model=SavedTrip)
async def save_trip(
    body: SaveTripRequest,
    request: Request,
    sign_in: Annotated[SignIn, Depends(require_sign_in)],
) -> SavedTrip | JSONResponse:
    """Save the trip to the signed-in traveller's Ofself account (DECISIONS entry 283).

    Only on their click, and only the trip they chose: a `travel-plan` they are considering, with
    its destination, purpose and window, and nothing the plan concluded. The destination's
    `place` is minted only where they hold none.
    """

    user_id = sign_in.signed_in_user(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Sign in with Ofself first.", "sign_in": True},
        )
    try:
        by_slug = next(
            (c.code for c in get_country_registry().countries if c.slug == body.destination), None
        )
        destination = by_slug or normalise_country(body.destination)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"message": str(exc)}
        ) from exc
    trip = TripToSave(destination=destination, purpose=body.purpose, trip=body.trip)
    try:
        return await sign_in.identity.save_trip(user_id, trip)
    except OfselfAuthorizationLost as exc:
        return grant_lost(exc)
    except OfselfWriteRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": str(exc), "permission": True},
        ) from exc
    except OfselfError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": str(exc)}
        ) from exc


def grant_lost(exc: OfselfAuthorizationLost) -> JSONResponse:
    """Ask the traveller to reconnect, and end the session with the grant.

    The grant is gone, paused or expired: the one honest answer is to ask them to reconnect, never
    to carry on as if a traveller had been described (item 55, rule 4). And the session ends here
    too: a browser whose grant is gone is not signed in to anything this app can read, and leaving
    the cookie kept it looking signed in (pointed out by Ofself, 2026-09-24).
    """

    response = JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": {"message": str(exc), "code": exc.code, "reconnect": True}},
    )
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.post("/logout", include_in_schema=False)
async def logout() -> RedirectResponse:
    """Forget the session here. The user's grant to the app on Ofself is untouched."""

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


def _refused(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"message": message})
