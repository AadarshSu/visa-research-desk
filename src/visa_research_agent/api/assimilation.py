"""A destination briefing from Ofself's Assimilation plugin, for the news panel (TODO item 84).

Assimilation is another app's plugin, called through Paradigm with this app's key and the signed-in
traveller's user id; Paradigm signs the call on to Assimilation. Its usage guide (read live from
`GET /plugins/<id>` on 2026-10-08) shows the Paradigm SDK's `execute_plugin`, but that SDK comes
from a private repository this project cannot install (OFSELF_FEEDBACK 12.1), so this is the REST
call the SDK makes, with `httpx` as `api/ofself.py` already uses it.

**What is sent:** the destination, the trip's dates and the passport typed on the form — nothing
from the traveller's graph, and nothing from `shared_details` (the owner, item 84).

**What comes back is Assimilation's, not this program's.** Its advisories and entry requirements
are another app's claims that have passed none of the domain-trust rules (entry 181, rule 2), so a
briefing is handed to the page as Assimilation's answer, labelled as such, and is **never an input
to the visa answer** (entry 251). Nothing here is stored, and nothing is written to Paradigm.

**What it does not do:**
- **Retry.** A refusal is reported as it came; a setup or consent the traveller has not given is
  theirs to give, through the link Paradigm names.
- **Follow a link it was not sure of.** A setup or consent address is offered only where it is
  https on Ofself's own hosts.
"""

import json
import time
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from visa_research_agent.api.ofself import AUTHORIZATION_CODES, DEFAULT_BASE_URL
from visa_research_agent.domain.models import StrictModel
from visa_research_agent.research.live_sources import transport_failure_reason

ASSIMILATION_PLUGIN_ID = "912d0476-5ea8-4ae0-991f-2a242979ea16"
BRIEFING_MODE = "destination-briefing"

NOT_SET_UP_CODES = frozenset({"PLUGIN_NOT_ENABLED", "PLUGIN_NOT_AUTHORIZED"})
"""Paradigm's answer when the traveller has not set Assimilation up. `PLUGIN_NOT_ENABLED` was seen
live on 2026-10-08; `PLUGIN_NOT_AUTHORIZED` is the developer guide's name for the same state
(§31.4)."""

CONSENT_CODES = frozenset({"CONSENT_REQUIRED"})
"""A run waiting on the traveller's approval. The usage guide names the SDK's `ConsentRequired`;
the wire code is assumed from it until one is seen, and a body carrying a `consent_url` is read as
this whatever its code."""

OFSELF_HOSTS = ("ofself.com", "ofself.ai")

BriefingStatus = Literal["ok", "setup", "consent", "reconnect", "refused", "unavailable"]


class Briefing(StrictModel):
    """What the panel is told: the briefing, or why there is none and what the traveller can do."""

    status: BriefingStatus
    answer: dict[str, Any] | None = None
    """Assimilation's decoded answer, exactly as it came. Shown as Assimilation's; never read by
    the plan."""
    action_url: str | None = None
    """For `setup` and `consent`: where the traveller gives it, on Ofself's own hosts."""
    message: str | None = None
    seconds: float
    """How long the call took — item 84 asks for it to be measured."""


class AssimilationClient:
    """Asks Assimilation, through Paradigm, for one trip's destination briefing."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def destination_briefing(
        self,
        user_id: str,
        *,
        place: str,
        passport: str | None,
        start: date | None,
        end: date | None,
    ) -> Briefing:
        parameters: dict[str, str] = {"mode": BRIEFING_MODE, "place": place}
        if passport:
            parameters["passport"] = passport
        if start and end:
            parameters["startDate"] = start.isoformat()
            parameters["endDate"] = end.isoformat()
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds
            ) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/plugins/{ASSIMILATION_PLUGIN_ID}/execute",
                    headers={
                        "Accept": "application/json",
                        "X-API-Key": self.api_key,
                        "X-User-ID": user_id,
                    },
                    json={"input_parameters": parameters},
                )
        except httpx.HTTPError as exc:
            return Briefing(
                status="unavailable",
                message=f"Assimilation could not be reached: {transport_failure_reason(exc)}",
                seconds=_since(started),
            )
        return read_briefing(response, seconds=_since(started))


def read_briefing(response: httpx.Response, *, seconds: float) -> Briefing:
    """Paradigm's answer, read as the usage guide describes it, checked for failure first."""

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != httpx.codes.OK:
        return _paradigm_refusal(response.status_code, body, seconds)
    execution = body.get("execution") if isinstance(body, dict) else None
    if not isinstance(execution, dict) or not isinstance(execution.get("message"), str):
        return Briefing(
            status="unavailable", message="Paradigm answered without an execution", seconds=seconds
        )
    try:
        answer = json.loads(execution["message"])
    except ValueError:
        return Briefing(
            status="unavailable",
            message="Assimilation's answer is not JSON",
            seconds=seconds,
        )
    if execution.get("status") == "failed" or not isinstance(answer, dict):
        error = answer.get("error") if isinstance(answer, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        return Briefing(
            status="refused",
            message=detail if isinstance(detail, str) else "Assimilation refused the request",
            seconds=seconds,
        )
    return Briefing(status="ok", answer=answer, seconds=seconds)


def _paradigm_refusal(status_code: int, body: object, seconds: float) -> Briefing:
    error = body.get("error") if isinstance(body, dict) else None
    envelope = error if isinstance(error, dict) else body if isinstance(body, dict) else {}
    code = envelope.get("code")
    message = envelope.get("message")
    text = message if isinstance(message, str) else f"Paradigm answered HTTP {status_code}"
    consent_url = _ofself_url(envelope.get("consent_url"))
    if code in CONSENT_CODES or consent_url:
        return Briefing(status="consent", action_url=consent_url, message=text, seconds=seconds)
    if code in NOT_SET_UP_CODES:
        return Briefing(
            status="setup",
            action_url=_ofself_url(envelope.get("setup_url")),
            message=text,
            seconds=seconds,
        )
    if code in AUTHORIZATION_CODES:
        return Briefing(status="reconnect", message=text, seconds=seconds)
    detail = f"{code}: {text}" if isinstance(code, str) else text
    return Briefing(status="unavailable", message=detail, seconds=seconds)


def _ofself_url(value: object) -> str | None:
    """An https address on Ofself's own hosts, or None."""

    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(
        host == suffix or host.endswith(f".{suffix}") for suffix in OFSELF_HOSTS
    ):
        return None
    return value


def _since(started: float) -> float:
    return round(time.monotonic() - started, 2)
