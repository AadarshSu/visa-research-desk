"""Model calls through Ofself Personas, as one plain call rather than an agent (TODO item 62).

Personas runs agents, but at `capabilities: []` a run is a single model call: the system prompt and
one message, no platform text and no tools. Probed live on 2026-09-24 (DECISIONS entry 188):

- **Nothing is added to the prompt.** The `debug` request event held exactly our system prompt and
  our message.
- **A strict JSON schema is applied** through `llm_config.response_format`, on Ofself's account.
- **Reasoning effort is applied** through `llm_config.reasoning.effort`.
- **The model is not, through `llm_config`, on Ofself's account.** Without the app's own key its
  `model` is ignored and the agent's stored model is used. So every run also sends the agent-level
  `llm_model`, which is stored on the agent and does select the model — and every reply's reported
  model is checked against the one asked for (`PersonasModelMismatch`). A different model answering
  a visa question is exactly the change entry 177 found can flip a decision, and here it would
  arrive silently.

**What this module does not change.** The prompts, the packets, the schemas and the reasoning effort
are the ones the OpenAI route sends; only the transport differs. The schema is built by the OpenAI
SDK's own converter, the same one LangChain's strict structured output uses, so both routes send
one schema. A failed call raises, and each caller turns that into its own refusal — never a fallback
to a worse decider (entry 31).

**What Personas keeps.** Each run is stored as a conversation in the run user's Personas history,
one agent per kind of call. Deleting them is the owner's decision, not this module's.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import TypeVar

import httpx
from openai.lib._parsing._completions import type_to_response_format_param
from pydantic import BaseModel, ValidationError

from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.adjudication import (
    ROLE_REQUEST_PREFIX,
    AdjudicationError,
    RoleAdjudication,
    UsageRecorder,
)
from visa_research_agent.discovery.selection import (
    SELECTION_REQUEST_PREFIX,
    Selection,
    SelectionError,
)
from visa_research_agent.domain.models import VisaPlanDraft
from visa_research_agent.research.errors import (
    LLMConfigurationError,
    LLMExtractionError,
    VisaResearchError,
)
from visa_research_agent.research.live_sources import transport_failure_reason
from visa_research_agent.research.openai_extraction import PLAN_REQUEST_PREFIX

T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URL = "https://personas.ofself.com"
RUN_PATH = "/api/v1/internal/headless/run"


class PersonasError(VisaResearchError):
    """A model call through Personas did not produce a usable, validated answer."""


class PersonasModelMismatch(PersonasError):
    """Personas answered with a model other than the one asked for, and the answer is not used."""


@dataclass(frozen=True)
class PersonasConfig:
    app_id: str
    hmac_key: str
    user_id: str
    """The Paradigm user every run is made as. These calls serve no particular traveller, so this is
    one fixed identity, the owner's, until Ofself offers calls that belong to the app itself
    (OFSELF_FEEDBACK 8.16)."""

    model: str
    reasoning_effort: str
    max_output_tokens: int
    timeout_seconds: float
    base_url: str = DEFAULT_BASE_URL
    app_name: str = "Visa Research Desk"


def personas_client_from_settings() -> "PersonasModelClient":
    """The client `model_route: personas` asks for, or a loud error naming what is missing.

    Missing configuration raises rather than falling back to the OpenAI route: the route is a
    committed, reviewed line, so a machine that cannot honour it should say so.
    """

    missing = [
        name
        for name, value in (
            ("PERSONAS_APP_ID", settings.personas_app_id),
            (
                "PERSONAS_HMAC_KEY",
                settings.personas_hmac_key.get_secret_value() if settings.personas_hmac_key else "",
            ),
            ("PERSONAS_USER_ID", settings.personas_user_id),
            ("OPENAI_MODEL", settings.openai_model),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise LLMConfigurationError(
            f"model_route: personas needs {', '.join(missing)} to make a model call"
        )
    assert settings.personas_hmac_key is not None and settings.openai_model is not None
    try:
        return PersonasModelClient(
            PersonasConfig(
                app_id=str(settings.personas_app_id),
                hmac_key=settings.personas_hmac_key.get_secret_value(),
                user_id=str(settings.personas_user_id),
                model=settings.openai_model,
                reasoning_effort=settings.openai_reasoning_effort,
                max_output_tokens=settings.openai_max_output_tokens,
                timeout_seconds=settings.personas_timeout_seconds,
                base_url=settings.personas_base_url,
            )
        )
    except PersonasError as exc:
        raise LLMConfigurationError(str(exc)) from exc


def signature(raw_body: bytes, hmac_key: str) -> str:
    """Personas' request signature: HMAC-SHA256 over the exact bytes sent."""

    return "sha256=" + hmac.new(hmac_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


class PersonasModelClient:
    """One structured model call per `complete`, made through Personas on Ofself's account."""

    def __init__(
        self, config: PersonasConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        for name in ("app_id", "hmac_key", "user_id", "model"):
            if not str(getattr(config, name)).strip():
                raise PersonasError(f"Personas needs {name} to make a model call")
        try:
            uuid.UUID(config.user_id)
        except ValueError as exc:
            raise PersonasError("The Personas user id must be a UUID") from exc
        self.config = config
        self.transport = transport

    async def complete(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        message: str,
        schema: type[T],
        usage: UsageRecorder | None = None,
        timeout_seconds: float | None = None,
    ) -> T:
        config = self.config
        body = {
            "app_id": config.app_id,
            "hmac_key": config.hmac_key,
            "paradigm_user_id": config.user_id,
            "app_name": config.app_name,
            "agent_name": agent_name,
            "system_prompt": system_prompt,
            "message": message,
            # Elect nothing: no platform prompt, no tools, so no agent loop (entry 188).
            "capabilities": [],
            # The only way the model takes effect on Ofself's account; stored on this call's agent.
            "llm_provider": "ofself",
            "llm_model": config.model,
            "llm_config": {
                "provider": "openai",
                "model": config.model,
                "reasoning": {"effort": config.reasoning_effort},
                "response_format": type_to_response_format_param(schema),
                "max_tokens": config.max_output_tokens,
            },
            "conversation_title": agent_name,
        }
        raw = json.dumps(body).encode("utf-8")  # signed and sent as these exact bytes
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Internal-Signature": signature(raw, config.hmac_key),
        }
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=timeout_seconds or config.timeout_seconds
            ) as client:
                response = await client.post(
                    config.base_url.rstrip("/") + RUN_PATH, content=raw, headers=headers
                )
        except httpx.HTTPError as exc:
            raise PersonasError(
                f"Personas could not be reached: {transport_failure_reason(exc)}"
            ) from exc
        if usage is not None:
            usage.http_requests = (usage.http_requests or 0) + 1

        payload = _json_of(response)
        if response.status_code != httpx.codes.OK:
            raise PersonasError(f"Personas refused the model call ({_error_of(response, payload)})")
        if not isinstance(payload, dict):
            raise PersonasError("Personas answered with a body that is not a JSON object")
        if payload.get("requires_realm_assignment"):
            raise PersonasError(
                "Personas asks the run user to authorise the agent first; no call was made"
            )

        reported = payload.get("usage")
        reported = reported if isinstance(reported, dict) else {}
        if usage is not None:
            usage.input_tokens = _int(reported.get("total_input_tokens"))
            usage.output_tokens = _int(reported.get("total_output_tokens"))
        answered_by = reported.get("model")
        if answered_by != config.model:
            raise PersonasModelMismatch(
                f"Personas answered with {answered_by!r}, not {config.model!r}; the answer is not "
                "used"
            )

        text = payload.get("assistant_text")
        if not isinstance(text, str) or not text.strip():
            raise PersonasError("Personas answered without any text")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise PersonasError("The model returned invalid structured output") from exc


def _json_of(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _error_of(response: httpx.Response, payload: object) -> str:
    """The code and message from Personas' `{"error": {"code", "message"}}` envelope."""

    envelope = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(envelope, dict):
        code, message = envelope.get("code"), envelope.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return f"{code}: {message[:200]}"
        if isinstance(code, str):
            return code
    return f"HTTP {response.status_code}"


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- the three calls, each behind the interface its OpenAI class already implements -------------
#
# One agent per kind of call, so each keeps its own stored model and its own history. Roles and the
# refused-page judgement share an adjudicator, and so share an agent, as they share a class today.

ROLES_AGENT = "visa-roles"
SELECTION_AGENT = "visa-selection"
PLAN_AGENT = "visa-plan"


class PersonasRoleAdjudicator:
    """`RoleAdjudicator` through Personas: the OpenAI class's prompt, packet and schema."""

    def __init__(
        self, client: PersonasModelClient, *, timeout_seconds: float | None = None
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def adjudicate(
        self, system_prompt: str, packet: str, *, usage: UsageRecorder | None = None
    ) -> RoleAdjudication:
        try:
            return await self.client.complete(
                agent_name=ROLES_AGENT,
                system_prompt=system_prompt,
                message=f"{ROLE_REQUEST_PREFIX}{packet}",
                schema=RoleAdjudication,
                usage=usage,
                timeout_seconds=self.timeout_seconds,
            )
        except PersonasError as exc:
            raise AdjudicationError(f"The role adjudication request failed: {exc}") from exc


class PersonasCandidateSelector:
    """`CandidateSelector` through Personas. A failure refuses the corridor (entry 258)."""

    def __init__(
        self, client: PersonasModelClient, *, timeout_seconds: float | None = None
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def select(
        self, system_prompt: str, packet: str, *, usage: UsageRecorder | None = None
    ) -> Selection:
        try:
            return await self.client.complete(
                agent_name=SELECTION_AGENT,
                system_prompt=system_prompt,
                message=f"{SELECTION_REQUEST_PREFIX}{packet}",
                schema=Selection,
                usage=usage,
                timeout_seconds=self.timeout_seconds,
            )
        except PersonasError as exc:
            raise SelectionError(f"The candidate selection request failed: {exc}") from exc


class PersonasPlanGenerator:
    """`StructuredPlanGenerator` through Personas: the plan call, validated afterwards as always."""

    def __init__(self, client: PersonasModelClient) -> None:
        self.client = client
        config = client.config
        # The plan reuse key's view of this call. It names the route, so a draft written through
        # one route is never served as the other's (entry 178).
        self.fingerprint = json.dumps(
            {
                "route": "personas",
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "max_output_tokens": config.max_output_tokens,
                "request_prefix": PLAN_REQUEST_PREFIX,
            },
            sort_keys=True,
        )

    async def generate(
        self, system_prompt: str, research_packet: str, *, usage: UsageRecorder | None = None
    ) -> VisaPlanDraft:
        try:
            return await self.client.complete(
                agent_name=PLAN_AGENT,
                system_prompt=system_prompt,
                message=f"{PLAN_REQUEST_PREFIX}{research_packet}",
                schema=VisaPlanDraft,
                usage=usage,
            )
        except PersonasError as exc:
            raise LLMExtractionError(f"The plan request through Personas failed: {exc}") from exc
