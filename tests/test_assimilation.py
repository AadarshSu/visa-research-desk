"""The news panel's call to Assimilation through Paradigm (TODO item 84).

Nothing here reaches Paradigm: a fake answers through `httpx.MockTransport`. The refusal shape is
the one Paradigm sent live on 2026-10-08 (`var/news-2026-10-08/taxonomy.json`); the answer shape is
the usage guide's, until a real briefing has been seen.
"""

import json
from collections.abc import AsyncIterator, Callable
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from visa_research_agent.api import routes
from visa_research_agent.api.app import create_app
from visa_research_agent.api.assimilation import (
    ASSIMILATION_PLUGIN_ID,
    AssimilationClient,
    Briefing,
)
from visa_research_agent.api.signin import get_sign_in

API_KEY = "ofs_tp_test.secret"
USER = "66a3241b-5130-4ca9-9ce7-baaa69f84745"
SETUP_URL = f"https://nucleus.ofself.com/plugins/{ASSIMILATION_PLUGIN_ID}/setup"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def assimilation(handler: Callable[[httpx.Request], httpx.Response]) -> AssimilationClient:
    return AssimilationClient(
        API_KEY, base_url="https://paradigm.test", transport=httpx.MockTransport(handler)
    )


def executed(answer: object, status: str = "completed") -> httpx.Response:
    return httpx.Response(
        200, json={"execution": {"status": status, "message": json.dumps(answer)}}
    )


async def brief(handler: Callable[[httpx.Request], httpx.Response]) -> Briefing:
    return await assimilation(handler).destination_briefing(
        USER, place="JP", passport="GB", start=date(2026, 11, 2), end=date(2026, 11, 9)
    )


@pytest.mark.anyio
async def test_it_sends_the_trip_and_nothing_else_as_this_app_for_this_user() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return executed({"news": []})

    briefing = await brief(handler)

    assert briefing.status == "ok"
    assert briefing.answer == {"news": []}
    (request,) = seen
    assert request.url == f"https://paradigm.test/api/v1/plugins/{ASSIMILATION_PLUGIN_ID}/execute"
    assert request.headers["X-API-Key"] == API_KEY
    assert request.headers["X-User-ID"] == USER
    assert json.loads(request.content) == {
        "input_parameters": {
            "mode": "destination-briefing",
            "place": "JP",
            "passport": "GB",
            "startDate": "2026-11-02",
            "endDate": "2026-11-09",
        }
    }


@pytest.mark.anyio
async def test_without_dates_none_are_sent() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["input_parameters"])
        return executed({})

    await assimilation(handler).destination_briefing(
        USER, place="JP", passport=None, start=None, end=None
    )
    assert sent == [{"mode": "destination-briefing", "place": "JP"}]


@pytest.mark.anyio
async def test_a_plugin_not_set_up_offers_paradigms_setup_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "PLUGIN_NOT_ENABLED",
                    "message": "Set up and approve Assimilation on Paradigm first",
                    "setup_url": SETUP_URL,
                }
            },
        )

    briefing = await brief(handler)
    assert briefing.status == "setup"
    assert briefing.action_url == SETUP_URL


@pytest.mark.anyio
async def test_a_run_waiting_on_approval_offers_its_consent_link() -> None:
    consent = "https://nucleus.ofself.com/consent/abc"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "CONSENT_REQUIRED", "consent_url": consent}}
        )

    briefing = await brief(handler)
    assert briefing.status == "consent"
    assert briefing.action_url == consent


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://nucleus.ofself.com/setup",
        "https://ofself.com.example.net/setup",
        "https://evil.test/setup",
        "javascript:alert(1)",
    ],
)
async def test_a_link_off_ofselfs_own_hosts_is_never_offered(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "PLUGIN_NOT_ENABLED", "setup_url": url}})

    briefing = await brief(handler)
    assert briefing.status == "setup"
    assert briefing.action_url is None


@pytest.mark.anyio
async def test_a_failed_execution_is_a_refusal_and_never_shown_as_a_briefing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return executed(
            {"error": {"status": 400, "code": "BAD_REQUEST", "message": "unknown field: plaec"}},
            status="failed",
        )

    briefing = await brief(handler)
    assert briefing.status == "refused"
    assert briefing.answer is None
    assert briefing.message == "unknown field: plaec"


@pytest.mark.anyio
async def test_a_lost_grant_asks_the_traveller_to_reconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "EP_REVOKED", "message": "revoked"}})

    assert (await brief(handler)).status == "reconnect"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(504, text="Gateway Timeout"),
        httpx.Response(200, json={"execution": {"status": "completed"}}),
        httpx.Response(200, json={"execution": {"status": "completed", "message": "not json"}}),
    ],
)
async def test_a_gateway_failure_or_malformed_answer_is_unavailable(
    response: httpx.Response,
) -> None:
    briefing = await brief(lambda request: response)
    assert briefing.status == "unavailable"
    assert briefing.answer is None


@pytest.mark.anyio
async def test_an_unreachable_paradigm_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert (await brief(handler)).status == "unavailable"


@pytest.fixture
async def app_client() -> AsyncIterator[tuple[httpx.AsyncClient, list[dict[str, object]]]]:
    app = create_app()
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "user": request.headers["X-User-ID"],
                **json.loads(request.content)["input_parameters"],
            }
        )
        return executed({"news": []})

    app.dependency_overrides[routes.get_assimilation_client] = lambda: assimilation(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sent


def signed_in_as(client: httpx.AsyncClient, user: str | None) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_sign_in] = lambda: SimpleNamespace(
        signed_in_user=lambda request: user
    )


@pytest.mark.anyio
async def test_the_anonymous_page_gets_no_briefing(
    app_client: tuple[httpx.AsyncClient, list[dict[str, object]]],
) -> None:
    client, sent = app_client
    signed_in_as(client, None)
    response = await client.get("/news-briefing", params={"destination": "japan"})
    assert response.status_code == 401
    assert sent == []


@pytest.mark.anyio
async def test_a_signed_in_traveller_is_briefed_on_the_destination_dates_and_typed_passport(
    app_client: tuple[httpx.AsyncClient, list[dict[str, object]]],
) -> None:
    client, sent = app_client
    signed_in_as(client, USER)
    response = await client.get(
        "/news-briefing",
        params={
            "destination": "japan",
            "passport": "gb",
            "start": "2026-11-02",
            "end": "2026-11-09",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert sent == [
        {
            "user": USER,
            "mode": "destination-briefing",
            "place": "JP",
            "passport": "GB",
            "startDate": "2026-11-02",
            "endDate": "2026-11-09",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-02"},
        {"start": "2026-11-09", "end": "2026-11-02"},
    ],
)
async def test_dates_must_come_as_a_forward_pair(
    app_client: tuple[httpx.AsyncClient, list[dict[str, object]]], params: dict[str, str]
) -> None:
    client, sent = app_client
    signed_in_as(client, USER)
    response = await client.get("/news-briefing", params={"destination": "japan", **params})
    assert response.status_code == 422
    assert sent == []
