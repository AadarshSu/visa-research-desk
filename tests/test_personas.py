"""Model calls through Ofself Personas (TODO item 62).

Nothing here reaches Personas: a fake answers through `httpx.MockTransport`. The request and answer
shapes are the ones the live service used on 2026-09-24, when a `capabilities: []` run sent exactly
the app's system prompt and message, applied a strict JSON schema and a reasoning effort, and ran
the agent's stored model rather than `llm_config.model`.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.adjudication import (
    ROLE_REQUEST_PREFIX,
    AdjudicationError,
    RoleAdjudication,
    UsageRecorder,
)
from visa_research_agent.discovery.cli import build_candidate_selector, build_role_adjudicator
from visa_research_agent.discovery.selection import SELECTION_REQUEST_PREFIX, SelectionError
from visa_research_agent.domain.models import RuntimePolicy
from visa_research_agent.research.errors import LLMConfigurationError, LLMExtractionError
from visa_research_agent.research.openai_extraction import (
    PLAN_REQUEST_PREFIX,
    LangChainStructuredPlanGenerator,
)
from visa_research_agent.research.personas import (
    PLAN_AGENT,
    ROLES_AGENT,
    SELECTION_AGENT,
    PersonasCandidateSelector,
    PersonasConfig,
    PersonasError,
    PersonasModelClient,
    PersonasModelMismatch,
    PersonasPlanGenerator,
    PersonasRoleAdjudicator,
    personas_client_from_settings,
)

pytestmark = pytest.mark.anyio

USER = "43b82f83-66c4-449b-a9ce-eb1f690c433b"
KEY = "sk_hdls_test"
MODEL = "gpt-5.6-terra"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> PersonasModelClient:
    return PersonasModelClient(
        PersonasConfig(
            app_id="668f0915-09c1-438a-bd27-f881ae4f03b1",
            hmac_key=KEY,
            user_id=USER,
            model=MODEL,
            reasoning_effort="low",
            max_output_tokens=6_000,
            timeout_seconds=30,
            base_url="https://personas.test",
        ),
        transport=httpx.MockTransport(handler),
    )


def answered(text: str, *, model: str = MODEL) -> httpx.Response:
    """A `/run` reply as Personas gave it live: the text, and usage naming the model that ran."""

    return httpx.Response(
        200,
        json={
            "assistant_text": text,
            "conversation_id": "c",
            "usage": {"model": model, "total_input_tokens": 1_234, "total_output_tokens": 56},
        },
    )


ROLES = json.dumps({"choices": [], "delegates": [], "tools": []})


async def test_a_call_elects_nothing_and_carries_our_prompt_schema_effort_and_model() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answered(ROLES)

    await PersonasRoleAdjudicator(client(handler)).adjudicate("the prompt", '{"packet": 1}')

    (request,) = sent
    assert request.url.path == "/api/v1/internal/headless/run"
    body = json.loads(request.content)
    # No capabilities is what makes this one plain call: no platform text, no tools, no loop.
    assert body["capabilities"] == []
    assert body["system_prompt"] == "the prompt"
    assert body["message"] == f'{ROLE_REQUEST_PREFIX}{{"packet": 1}}'
    assert body["agent_name"] == ROLES_AGENT
    assert body["paradigm_user_id"] == USER
    # The agent-level model is what selects the model on Ofself's account (feedback 8.17).
    assert (body["llm_provider"], body["llm_model"]) == ("ofself", MODEL)
    config = body["llm_config"]
    assert config["reasoning"] == {"effort": "low"}
    assert config["max_tokens"] == 6_000
    assert "api_key" not in config, "no key is sent, so the call is billed to Ofself's account"
    schema = config["response_format"]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["name"] == "RoleAdjudication"


async def test_the_request_is_signed_over_the_exact_bytes_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        expected = "sha256=" + hmac.new(KEY.encode(), request.content, hashlib.sha256).hexdigest()
        assert request.headers["X-Internal-Signature"] == expected
        return answered(ROLES)

    await PersonasRoleAdjudicator(client(handler)).adjudicate("p", "{}")


async def test_the_answer_is_validated_and_what_was_billed_is_recorded() -> None:
    usage = UsageRecorder()
    selection = await PersonasCandidateSelector(
        client(lambda _: answered('{"source_ids": ["s1", "s2"]}'))
    ).select("p", "{}", usage=usage)

    assert selection.source_ids == ["s1", "s2"]
    assert (usage.input_tokens, usage.output_tokens, usage.http_requests) == (1_234, 56, 1)


async def test_an_answer_from_another_model_is_refused_not_used() -> None:
    """Without our key Personas ignores `llm_config.model`; a silent swap must not pass."""

    with pytest.raises(AdjudicationError, match="gpt-5.5"):
        await PersonasRoleAdjudicator(
            client(lambda _: answered(ROLES, model="gpt-5.5"))
        ).adjudicate("p", "{}")

    with pytest.raises(PersonasModelMismatch):
        await client(lambda _: answered(ROLES, model="gpt-5.5")).complete(
            agent_name="a", system_prompt="p", message="m", schema=RoleAdjudication
        )


@pytest.mark.parametrize(
    "response",
    [
        answered("not json at all"),
        answered('{"choices": "not a list"}'),
        answered(""),
        httpx.Response(200, json={"requires_realm_assignment": True, "redirect_url": "x"}),
        httpx.Response(200, text="<html>"),
    ],
    ids=["prose", "wrong shape", "empty", "needs authorisation", "not json"],
)
async def test_an_unusable_answer_is_a_failed_call(response: httpx.Response) -> None:
    with pytest.raises(PersonasError):
        await client(lambda _: response).complete(
            agent_name="a", system_prompt="p", message="m", schema=RoleAdjudication
        )


async def test_a_refusal_names_personas_own_error() -> None:
    refusal = httpx.Response(
        400, json={"error": {"code": "VALIDATION_ERROR", "message": "llm_config.x is not accepted"}}
    )
    with pytest.raises(SelectionError, match="VALIDATION_ERROR: llm_config.x is not accepted"):
        await PersonasCandidateSelector(client(lambda _: refusal)).select("p", "{}")


async def test_an_unreachable_personas_is_a_failed_call_with_its_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("", request=request)

    with pytest.raises(PersonasError, match="ConnectTimeout"):
        await client(handler).complete(
            agent_name="a", system_prompt="p", message="m", schema=RoleAdjudication
        )


async def test_each_call_sends_its_own_words_and_agent() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return answered('{"source_ids": []}')

    await PersonasCandidateSelector(client(handler)).select("p", "PACKET")
    with pytest.raises(LLMExtractionError):
        # The reply is not a plan, so the plan call fails — after sending its request.
        await PersonasPlanGenerator(client(handler)).generate("p", "PACKET")

    selection, plan = seen
    assert (selection["agent_name"], selection["message"]) == (
        SELECTION_AGENT,
        f"{SELECTION_REQUEST_PREFIX}PACKET",
    )
    assert (plan["agent_name"], plan["message"]) == (PLAN_AGENT, f"{PLAN_REQUEST_PREFIX}PACKET")
    assert plan["llm_config"]["response_format"]["json_schema"]["name"] == "VisaPlanDraft"


def test_a_plan_drafted_through_personas_is_never_reused_as_an_openai_one() -> None:
    """The reuse key names the route, so a draft is only served back on the route that wrote it."""

    personas = PersonasPlanGenerator(client(lambda _: answered("{}"))).fingerprint
    openai = LangChainStructuredPlanGenerator(
        api_key="k",
        model_name=MODEL,
        request_timeout_seconds=60,
        max_output_tokens=6_000,
        reasoning_effort="low",
    ).fingerprint

    assert json.loads(personas)["route"] == "personas"
    assert personas != openai


def test_a_client_refuses_a_user_id_that_is_not_a_uuid() -> None:
    with pytest.raises(PersonasError, match="UUID"):
        PersonasModelClient(
            PersonasConfig(
                app_id="a",
                hmac_key="k",
                user_id="alice",
                model=MODEL,
                reasoning_effort="low",
                max_output_tokens=1,
                timeout_seconds=1,
            )
        )


def policy(route: str) -> RuntimePolicy:
    return RuntimePolicy.model_validate(
        {
            "schema_version": 1,
            "source_mode": "live",
            "extraction_mode": "openai",
            "discovery_decider": "model",
            "discovery_selector": "model",
            "model_route": route,
            "source_cache_ttl_hours": 24,
            "source_maximum_stale_hours": 168,
        }
    )


def configure_personas(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    from pydantic import SecretStr

    values: dict[str, object] = {
        "personas_app_id": "668f0915-09c1-438a-bd27-f881ae4f03b1",
        "personas_hmac_key": SecretStr(KEY),
        "personas_user_id": USER,
        "openai_model": MODEL,
        "openai_api_key": None,
        **overrides,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def test_the_policy_routes_every_call_through_personas(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OpenAI key is needed on this route: that is the point of it."""

    configure_personas(monkeypatch)

    assert isinstance(build_role_adjudicator(policy("personas")), PersonasRoleAdjudicator)
    assert isinstance(build_candidate_selector(policy("personas")), PersonasCandidateSelector)


def test_a_missing_personas_setting_is_named_not_silently_rerouted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_personas(monkeypatch, personas_user_id=None, personas_hmac_key=None)

    with pytest.raises(LLMConfigurationError, match="PERSONAS_HMAC_KEY, PERSONAS_USER_ID"):
        personas_client_from_settings()
    with pytest.raises(LLMConfigurationError):
        build_role_adjudicator(policy("personas"))


def test_the_openai_route_is_one_policy_line_away(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from visa_research_agent.discovery.adjudication import LangChainRoleAdjudicator

    configure_personas(monkeypatch, openai_api_key=SecretStr("sk-test"))

    assert isinstance(build_role_adjudicator(policy("openai")), LangChainRoleAdjudicator)


async def test_the_quick_calls_carry_their_own_shorter_limit() -> None:
    """Entry 258: selection and role calls end at their own limit, not the general one, so a call
    stuck behind the gateway ends the run sooner. Anything not given a limit keeps the client's."""

    limits: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        limits.append(request.extensions["timeout"]["read"])
        if "selection" in request.content.decode().lower():
            return answered('{"source_ids": ["s1"]}')
        return answered(ROLES)

    await PersonasCandidateSelector(client(handler), timeout_seconds=12).select("p", "{}")
    await PersonasRoleAdjudicator(client(handler), timeout_seconds=14).adjudicate("p", "{}")
    await PersonasRoleAdjudicator(client(handler)).adjudicate("p", "{}")

    assert limits == [12, 14, 30]
