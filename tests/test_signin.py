"""Signing in with Ofself (TODO item 55).

Nothing here reaches Paradigm. The exchange is faked through `httpx.MockTransport`; its refusals are
the ones the live endpoint gave a made-up and a missing code on 2026-09-17, and its success shape is
an assumption until a real sign-in has been seen.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from visa_research_agent.api.app import create_app
from visa_research_agent.api.dependencies import get_automatic_destinations, get_visa_plan_service
from visa_research_agent.api.ofself import OfselfIdentity, OfselfSignInRejected, OfselfUnavailable
from visa_research_agent.api.signin import (
    PENDING_COOKIE,
    SESSION_COOKIE,
    RedactSessionCodes,
    SignedCookies,
    SignIn,
    get_sign_in,
)
from visa_research_agent.config.settings import settings
from visa_research_agent.domain.models import VisaPlan
from visa_research_agent.research.errors import VisaResearchError

pytestmark = pytest.mark.anyio

SECRET = "a-test-session-secret-that-is-long-enough"
CLIENT_ID = "tp_test_client"
USER = "66a3241b-5130-4ca9-9ce7-baaa69f84745"
OTHER_USER = "2f0c9b8e-4d7a-4e61-9a1b-3c5d7e9f1a2b"
AUTHORIZE = "https://app.ofself.test/authorize"
REDIRECT = "http://localhost:8000/oauth/callback"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


Handler = Callable[[httpx.Request], httpx.Response]
Start = Callable[[Handler], Awaitable[httpx.AsyncClient]]


def exchanged(user_id: str = USER) -> Handler:
    return lambda _: httpx.Response(200, json={"user_id": user_id})


def refused(status: int, code: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(status, json={"error": {"code": code, "message": "no"}})


def sign_in(handler: Callable[[httpx.Request], httpx.Response]) -> SignIn:
    return SignIn(
        identity=OfselfIdentity(
            "ofs_tp_test.key",
            base_url="https://paradigm.test",
            transport=httpx.MockTransport(handler),
        ),
        cookies=SignedCookies(SECRET),
        client_id=CLIENT_ID,
        authorize_url=AUTHORIZE,
        redirect_uri=REDIRECT,
        session_max_age_seconds=3600,
        secure_cookies=False,
    )


def client_for(configured: SignIn | None) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[get_sign_in] = lambda: configured
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def started() -> AsyncIterator[Start]:
    """A client that has already been through `/oauth/login`, so it holds the pending cookie."""

    clients: list[httpx.AsyncClient] = []

    async def make(handler: Handler) -> httpx.AsyncClient:
        client = client_for(sign_in(handler))
        clients.append(client)
        response = await client.get("/oauth/login")
        assert response.status_code == 303
        return client

    yield make
    for client in clients:
        await client.aclose()


def callback_query(**overrides: str) -> dict[str, str]:
    query = {
        "code": "success",
        "client_id": CLIENT_ID,
        "user_id": USER,
        "username": "test-user",
        "sid_code": "single-use-code",
    }
    query.update(overrides)
    return {key: value for key, value in query.items() if value}


# --- the signed cookie ------------------------------------------------------------------------


def test_a_signed_cookie_reads_back_for_its_own_purpose_only() -> None:
    cookies = SignedCookies(SECRET, now=lambda: 1_000.0)
    token = cookies.sign("session", {"user_id": USER})

    assert cookies.verify(token, "session", max_age_seconds=60) == {"user_id": USER}
    assert cookies.verify(token, "pending", max_age_seconds=60) is None


def test_an_altered_cookie_is_refused() -> None:
    cookies = SignedCookies(SECRET)
    encoded, signature = cookies.sign("session", {"user_id": USER}).rsplit(".", 1)
    forged = SignedCookies("another-secret-that-is-also-long-enough-to-use").sign(
        "session", {"user_id": OTHER_USER}
    )

    assert cookies.verify(f"{encoded[:-2]}xx.{signature}", "session", max_age_seconds=60) is None
    assert cookies.verify(forged, "session", max_age_seconds=60) is None
    assert cookies.verify("not-a-token", "session", max_age_seconds=60) is None


def test_an_expired_cookie_is_refused() -> None:
    clock = [1_000.0]
    cookies = SignedCookies(SECRET, now=lambda: clock[0])
    token = cookies.sign("session", {"user_id": USER})

    clock[0] += 61

    assert cookies.verify(token, "session", max_age_seconds=60) is None


def test_a_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        SignedCookies("short")


# --- the exchange ----------------------------------------------------------------------------


async def test_the_exchange_sends_the_code_with_this_apps_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"user": {"id": USER.upper()}})

    user = await sign_in(handler).identity.exchange_session_code("single-use-code")

    (request,) = seen
    assert user == USER
    assert request.url.path == "/api/v1/auth/session/exchange"
    assert request.headers["X-API-Key"] == "ofs_tp_test.key"
    assert request.content == b'{"code":"single-use-code"}'


async def test_an_invalid_or_used_code_is_a_rejected_sign_in() -> None:
    with pytest.raises(OfselfSignInRejected):
        await sign_in(refused(404, "INVALID_CODE")).identity.exchange_session_code("used")


async def test_an_empty_code_is_rejected_before_anything_is_sent() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"user_id": USER})

    with pytest.raises(OfselfSignInRejected):
        await sign_in(handler).identity.exchange_session_code("  ")
    assert sent == []


@pytest.mark.parametrize(
    "body", [{}, {"user_id": "alice"}, {"user": {"name": "x"}}, ["not", "an", "object"]]
)
async def test_a_confirmation_that_names_no_user_is_not_a_sign_in(body: object) -> None:
    with pytest.raises(OfselfUnavailable, match="which user"):
        await sign_in(lambda _: httpx.Response(200, json=body)).identity.exchange_session_code("c")


# --- the routes ------------------------------------------------------------------------------


async def test_sign_in_is_off_until_it_is_configured() -> None:
    async with client_for(None) as client:
        login = await client.get("/oauth/login")
        session = await client.get("/oauth/session")

    assert login.status_code == 503
    assert session.json() == {"configured": False, "signed_in": False, "user_id": None}


async def test_login_sends_the_browser_to_ofself_for_this_app() -> None:
    async with client_for(sign_in(exchanged())) as client:
        response = await client.get("/oauth/login")

    location = urlsplit(response.headers["location"])
    assert response.status_code == 303
    assert f"{location.scheme}://{location.netloc}{location.path}" == AUTHORIZE
    assert parse_qs(location.query) == {"client_id": [CLIENT_ID], "redirect_uri": [REDIRECT]}
    assert PENDING_COOKIE in response.headers["set-cookie"]


async def test_a_confirmed_sign_in_starts_a_session_for_the_exchanged_user(
    started: Start,
) -> None:
    client = await started(exchanged())

    response = await client.get("/oauth/callback", params=callback_query())
    session = await client.get("/oauth/session")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert session.json() == {"configured": True, "signed_in": True, "user_id": USER}


async def test_the_user_id_in_the_address_is_never_believed(
    started: Start,
) -> None:
    """Anyone can write any `user_id` into a link; only the exchange says who approved."""

    client = await started(exchanged(OTHER_USER))

    response = await client.get("/oauth/callback", params=callback_query(user_id=USER))
    session = await client.get("/oauth/session")

    assert response.status_code == 400
    assert session.json()["signed_in"] is False


async def test_a_callback_without_a_session_code_is_refused(
    started: Start,
) -> None:
    client = await started(exchanged())

    response = await client.get("/oauth/callback", params=callback_query(sid_code=""))

    assert response.status_code == 400
    assert (await client.get("/oauth/session")).json()["signed_in"] is False


@pytest.mark.parametrize(
    "overrides",
    [{"client_id": "tp_another_app"}, {"code": "denied"}, {"error": "access_denied"}],
    ids=["another app", "not approved", "error"],
)
async def test_a_callback_that_is_not_an_approval_for_this_app_is_refused(
    started: Start, overrides: dict[str, str]
) -> None:
    client = await started(exchanged())

    response = await client.get("/oauth/callback", params=callback_query(**overrides))

    assert response.status_code == 400


async def test_a_callback_in_a_browser_that_never_started_signing_in_is_refused() -> None:
    async with client_for(sign_in(exchanged())) as client:
        response = await client.get("/oauth/callback", params=callback_query())

    assert response.status_code == 400


async def test_a_rejected_code_is_refused_and_an_unreachable_ofself_is_a_502(
    started: Start,
) -> None:
    rejected = await started(refused(404, "INVALID_CODE"))
    unreachable = await started(refused(503, "SERVICE_UNAVAILABLE"))

    assert (await rejected.get("/oauth/callback", params=callback_query())).status_code == 400
    assert (await unreachable.get("/oauth/callback", params=callback_query())).status_code == 502


async def test_a_session_cookie_signed_with_another_secret_is_not_a_session() -> None:
    forged = SignedCookies("another-secret-that-is-also-long-enough-to-use").sign(
        SESSION_COOKIE, {"user_id": OTHER_USER}
    )
    async with client_for(sign_in(exchanged())) as client:
        client.cookies.set(SESSION_COOKIE, forged)
        session = await client.get("/oauth/session")

    assert session.json()["signed_in"] is False


async def test_logout_ends_the_session(started: Start) -> None:
    client = await started(exchanged())
    await client.get("/oauth/callback", params=callback_query())

    response = await client.post("/oauth/logout")

    assert response.status_code == 303
    assert (await client.get("/oauth/session")).json()["signed_in"] is False


def test_a_session_code_never_reaches_the_access_log() -> None:
    """Seen live: uvicorn logged the whole callback address, unredeemed `sid_code` included."""

    path = f"/oauth/callback?code=success&user_id={USER}&sid_code=TW2dWyWr0djO4&username=x"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", path, "1.1", 400),
        None,
    )

    assert RedactSessionCodes().filter(record)

    line = record.getMessage()
    assert "TW2dWyWr0djO4" not in line
    assert "sid_code=[redacted]&username=x" in line
    assert f"user_id={USER}" in line


# --- the page and the traveller it is offered ------------------------------------------------


def paradigm(*, exchange_user: str = USER, nodes: Handler | None = None) -> Handler:
    """One fake Paradigm answering both the sign-in exchange and the node read."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/session/exchange":
            return httpx.Response(200, json={"user_id": exchange_user})
        if nodes is not None:
            return nodes(request)
        return httpx.Response(200, json={"nodes": [], "total": None})

    return handler


def recorded(*citizenships: str) -> Handler:
    """Citizenships in `work-authorization`, and nothing in any other schema."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("schema_id") != "work-authorization":
            return httpx.Response(200, json={"nodes": [], "total": None})
        node = {"id": "n", "value_json": {"citizenships": list(citizenships)}}
        return httpx.Response(200, json={"nodes": [node], "total": None})

    return handler


async def signed_in(started: Start, nodes: Handler) -> httpx.AsyncClient:
    client = await started(paradigm(nodes=nodes))
    response = await client.get("/oauth/callback", params=callback_query())
    assert response.status_code == 303
    return client


async def test_the_passport_needs_a_signed_in_traveller(started: Start) -> None:
    client = await started(paradigm())

    response = await client.get("/oauth/traveller")

    assert response.status_code == 401
    assert response.json()["detail"]["sign_in"] is True


async def test_a_signed_in_traveller_is_offered_their_recorded_citizenships(
    started: Start,
) -> None:
    client = await signed_in(started, recorded("IND", "GB"))

    response = await client.get("/oauth/traveller")

    assert response.status_code == 200
    body = response.json()
    assert [passport["nationality"] for passport in body["passports"]] == ["IN", "GB"]
    assert all(passport["from_document"] is False for passport in body["passports"])
    assert body["residences"] == [] and body["plans"] == []


async def test_a_lost_grant_asks_the_traveller_to_reconnect(started: Start) -> None:
    lost = refused(403, "EP_REVOKED")
    client = await signed_in(started, lost)

    response = await client.get("/oauth/traveller")

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "message": "no",
        "code": "EP_REVOKED",
        "reconnect": True,
    }
    # The session ends with the grant, so the page no longer presents the browser as signed in.
    assert (await client.get("/oauth/session")).json()["signed_in"] is False


async def test_an_unreachable_ofself_is_a_502_not_an_empty_passport(started: Start) -> None:
    client = await signed_in(started, refused(503, "SERVICE_UNAVAILABLE"))

    response = await client.get("/oauth/traveller")

    assert response.status_code == 502


async def test_the_page_asks_a_signed_in_traveller_rather_than_assuming_the_default(
    started: Start,
) -> None:
    """Rule 4: the default traveller belongs to the anonymous form alone."""

    client = await signed_in(started, recorded("IN"))

    page = (await client.get("/")).text

    assert 'data-signed-in="true"' in page
    assert '<option value="" selected>Choose a passport</option>' in page
    assert '<option value="" selected>Choose a country</option>' in page
    # Destination and purpose too: a list's first entry must not pass as the traveller's choice.
    assert '<option value="" selected>Choose a destination</option>' in page
    assert '<option value="" selected>Choose a purpose</option>' in page
    # What Ofself filled, or that it filled nothing, is said once above the form.
    assert 'id="ofself-note"' in page
    assert '<option value="IN" selected>' not in page
    assert '<option value="GB" selected>' not in page
    assert "Sign out" in page


async def test_the_anonymous_page_offers_sign_in_and_keeps_its_default() -> None:
    async with client_for(sign_in(paradigm())) as client:
        page = (await client.get("/")).text

    assert 'data-signed-in="false"' in page
    assert 'href="/oauth/login"' in page
    assert '<option value="IN" selected>' in page
    assert '<option value="" selected>Choose a destination</option>' not in page
    assert '<option value="" selected>Choose a purpose</option>' not in page
    assert 'id="ofself-note"' not in page


async def test_the_page_says_nothing_of_ofself_where_sign_in_is_neither_configured_nor_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_sign_in", False)
    async with client_for(None) as client:
        page = (await client.get("/")).text

    assert "Ofself" not in page
    assert '<option value="IN" selected>' in page
    assert 'id="sign-in-note"' not in page


# --- saving the trip to Ofself (DECISIONS entry 283) ------------------------------------------

TRIP = {
    "destination": "thailand",
    "purpose": "tourism",
    "trip": {"mode": "exact", "start": "2026-12-01", "end": "2026-12-20"},
}


def writable(written: list[dict[str, object]]) -> Handler:
    """No nodes to read; each write answered with the new node, as Paradigm did live."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"nodes": [], "total": None})
        written.append(json.loads(request.content))
        return httpx.Response(201, json={"id": f"new-{len(written)}", **written[-1]})

    return handler


async def test_saving_a_trip_needs_a_signed_in_traveller(started: Start) -> None:
    client = await started(paradigm())

    response = await client.post("/oauth/trip", json=TRIP)

    assert response.status_code == 401


async def test_a_signed_in_traveller_saves_the_trip_they_chose(started: Start) -> None:
    written: list[dict[str, object]] = []
    client = await signed_in(started, writable(written))

    response = await client.post("/oauth/trip", json=TRIP)

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "plan_id": "new-2", "place_created": True}
    assert [body["schema_name"] for body in written] == ["place", "travel-plan"]


async def test_a_destination_that_is_no_country_is_refused_before_anything_is_written(
    started: Start,
) -> None:
    written: list[dict[str, object]] = []
    client = await signed_in(started, writable(written))

    response = await client.post("/oauth/trip", json={**TRIP, "destination": "atlantis"})

    assert response.status_code == 422
    assert written == []


async def test_a_grant_without_create_asks_for_re_approval(started: Start) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"nodes": [], "total": None})
        return httpx.Response(403, json={"error": {"code": "PERMISSION_DENIED", "message": "no"}})

    client = await signed_in(started, handler)

    response = await client.post("/oauth/trip", json=TRIP)

    assert response.status_code == 403
    assert response.json()["detail"]["permission"] is True


async def test_a_lost_grant_while_saving_ends_the_session(started: Start) -> None:
    client = await signed_in(started, refused(403, "EP_REVOKED"))

    response = await client.post("/oauth/trip", json=TRIP)

    assert response.status_code == 403
    assert response.json()["detail"]["reconnect"] is True
    assert (await client.get("/oauth/session")).json()["signed_in"] is False


# --- a plan is spent only for a signed-in traveller (DECISIONS entry 191) --------------------


PLAN_REQUEST = {
    "destination": "singapore",
    "traveller": {
        "passport_nationality": "IN",
        "country_of_residence": "GB",
        "travel_purpose": "tourism",
    },
}


class SpendingNothing:
    """Stands in for every service a plan spends through, and records whether any was reached."""

    reached = False

    def __init__(self) -> None:
        SpendingNothing.reached = True

    async def generate(
        self, destination: object, traveller: object, *, on_stage: object = None
    ) -> VisaPlan:
        raise VisaResearchError("reached the plan")


def plan_client(configured: SignIn | None) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[get_sign_in] = lambda: configured
    app.dependency_overrides[get_visa_plan_service] = SpendingNothing
    app.dependency_overrides[get_automatic_destinations] = lambda: None
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def required(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pinned rather than read from a developer's `.env`, which may turn it off.
    monkeypatch.setattr(settings, "require_sign_in", True)
    SpendingNothing.reached = False


@pytest.mark.usefixtures("required")
async def test_a_plan_is_refused_to_a_browser_not_signed_in() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        response = await client.post("/visa-plans", json=PLAN_REQUEST)

    assert response.status_code == 401
    assert response.json()["detail"]["sign_in"] is True
    assert SpendingNothing.reached is False


@pytest.mark.usefixtures("required")
async def test_a_forged_session_is_not_signed_in() -> None:
    forged = SignedCookies("another-secret-that-is-also-long-enough-to-use").sign(
        SESSION_COOKIE, {"user_id": USER}
    )
    async with plan_client(sign_in(paradigm())) as client:
        client.cookies.set(SESSION_COOKIE, forged)
        response = await client.post("/visa-plans", json=PLAN_REQUEST)

    assert response.status_code == 401
    assert SpendingNothing.reached is False


@pytest.mark.usefixtures("required")
async def test_required_but_unconfigured_refuses_every_plan_rather_than_serving_anonymously() -> (
    None
):
    async with plan_client(None) as client:
        response = await client.post("/visa-plans", json=PLAN_REQUEST)
        page = (await client.get("/")).text

    assert response.status_code == 503
    assert "REQUIRE_SIGN_IN" in response.json()["detail"]["message"]
    assert SpendingNothing.reached is False
    assert "not configured on this server" in page
    assert 'id="generate-button" type="submit" disabled' in page


@pytest.mark.usefixtures("required")
async def test_a_signed_in_traveller_reaches_the_plan() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        assert (await client.get("/oauth/login")).status_code == 303
        assert (await client.get("/oauth/callback", params=callback_query())).status_code == 303

        response = await client.post("/visa-plans", json=PLAN_REQUEST)
        page = (await client.get("/")).text

    assert SpendingNothing.reached is True
    assert response.status_code == 503
    assert 'id="sign-in-note"' not in page


@pytest.mark.usefixtures("required")
async def test_the_anonymous_page_locks_the_form_behind_sign_in() -> None:
    async with client_for(sign_in(paradigm())) as client:
        page = (await client.get("/")).text

    assert 'id="sign-in-note"' in page
    assert 'id="generate-button" type="submit" disabled' in page


async def test_an_unrequired_plan_needs_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "require_sign_in", False)
    SpendingNothing.reached = False
    async with plan_client(sign_in(paradigm())) as client:
        response = await client.post("/visa-plans", json=PLAN_REQUEST)
        page = (await client.get("/")).text

    assert SpendingNothing.reached is True
    assert response.status_code == 503
    assert 'id="generate-button" type="submit" disabled' not in page


# --- free plans for a visitor who has not signed in (DECISIONS entry 262) --------------------


@pytest.fixture
def two_free_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "require_sign_in", False)
    monkeypatch.setattr(settings, "anonymous_plan_allowance", 2)
    SpendingNothing.reached = False


@pytest.mark.usefixtures("two_free_plans")
async def test_a_visitor_gets_the_free_plans_and_then_is_asked_to_sign_in() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        assert 'id="free-plans-left">2<' in (await client.get("/")).text
        for _ in range(2):
            SpendingNothing.reached = False
            assert (await client.post("/visa-plans", json=PLAN_REQUEST)).status_code == 503
            assert SpendingNothing.reached is True

        SpendingNothing.reached = False
        refused = await client.post("/visa-plans", json=PLAN_REQUEST)
        streamed = await client.post("/visa-plans/stream", json=PLAN_REQUEST)
        page = (await client.get("/")).text

    assert refused.status_code == 429
    assert refused.json()["detail"]["allowance_spent"] is True
    assert refused.json()["detail"]["sign_in"] is True
    assert streamed.status_code == 429
    assert SpendingNothing.reached is False
    assert "You have used your 2 free plans" in page
    assert 'id="generate-button" type="submit" disabled' in page


@pytest.mark.usefixtures("two_free_plans")
async def test_a_request_that_cannot_be_answered_costs_no_free_plan() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        for _ in range(3):
            assert (await client.post("/visa-plans", json={"traveller": {}})).status_code == 422
        page = (await client.get("/")).text

    assert 'id="free-plans-left">2<' in page


@pytest.mark.usefixtures("two_free_plans")
async def test_a_signed_in_traveller_is_never_counted() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        assert (await client.get("/oauth/login")).status_code == 303
        assert (await client.get("/oauth/callback", params=callback_query())).status_code == 303
        for _ in range(3):
            assert (await client.post("/visa-plans", json=PLAN_REQUEST)).status_code == 503
        page = (await client.get("/")).text

    assert 'id="free-plans-note"' not in page
    assert not settings.allowance_file.exists()


@pytest.mark.usefixtures("two_free_plans")
async def test_the_count_keeps_no_address() -> None:
    async with plan_client(sign_in(paradigm())) as client:
        await client.post("/visa-plans", json=PLAN_REQUEST)

    stored = settings.allowance_file.read_text(encoding="utf-8")
    assert "127.0.0.1" not in stored
    assert "testclient" not in stored


@pytest.mark.usefixtures("two_free_plans")
async def test_an_unreadable_count_refuses_rather_than_giving_plans_away() -> None:
    settings.allowance_file.parent.mkdir(parents=True)
    settings.allowance_file.write_text("not json", encoding="utf-8")
    async with plan_client(sign_in(paradigm())) as client:
        response = await client.post("/visa-plans", json=PLAN_REQUEST)

    assert response.status_code == 503
    assert "Free plans are unavailable" in response.json()["detail"]["message"]


async def test_with_sign_in_required_there_are_no_free_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "require_sign_in", True)
    async with client_for(sign_in(paradigm())) as client:
        page = (await client.get("/")).text

    assert 'id="free-plans-note"' not in page
