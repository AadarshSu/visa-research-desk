"""Item 88's live save, with this app's key and the sandbox test user (DECISIONS entry 283).

Saves one trip through `OfselfIdentity.save_trip`, saves it again, then reads the form's defaults,
recording every request and answer. The sandbox user's grant is full access and ignores the DLR
(OFSELF_FEEDBACK 4.1), so this tests the write, not the grant.

    .venv/bin/python var/writeback-2026-10-08/probe.py
"""

import asyncio
import json
import os
from datetime import date
from pathlib import Path

import httpx

from visa_research_agent.api.ofself import OfselfIdentity, TripToSave
from visa_research_agent.config.settings import settings
from visa_research_agent.domain.models import TripDates

HERE = Path(__file__).resolve().parent
TEST_USER = os.environ.get("PROBE_USER", "66a3241b-5130-4ca9-9ce7-baaa69f84745")
LOG: list[dict[str, object]] = []


async def record(request: httpx.Request, response: httpx.Response) -> None:
    await response.aread()
    LOG.append(
        {
            "method": request.method,
            "path": request.url.path,
            "params": dict(request.url.params),
            "sent": json.loads(request.content) if request.content else None,
            "status": response.status_code,
            "answer": response.json() if response.content else None,
        }
    )


class Recording(httpx.AsyncBaseTransport):
    """A fresh connection per request, so no client closes another's, and every exchange logged."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with httpx.AsyncHTTPTransport() as inner:
            response = await inner.handle_async_request(request)
            await record(request, response)
            # The body is already decoded, so it goes back without its encoding headers.
            headers = {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in ("content-encoding", "content-length")
            }
            return httpx.Response(response.status_code, headers=headers, content=response.content)


async def main() -> None:
    assert settings.paradigm_api_key is not None
    transport = Recording()
    identity = OfselfIdentity(
        settings.paradigm_api_key.get_secret_value(),
        base_url=settings.paradigm_base_url,
        transport=transport,
    )
    trip = TripToSave(
        destination="TH",
        purpose="tourism",
        trip=TripDates(mode="exact", start=date(2026, 12, 1), end=date(2026, 12, 20)),
    )
    japan = TripToSave(
        destination="JP",
        purpose="business",
        trip=TripDates(mode="rough", start=date(2027, 3, 1), end=date(2027, 4, 30)),
    )
    outcome: dict[str, object] = {}
    try:
        outcome["thailand_again"] = (await identity.save_trip(TEST_USER, trip)).model_dump()
        outcome["japan"] = (await identity.save_trip(TEST_USER, japan)).model_dump()
        outcome["japan_again"] = (await identity.save_trip(TEST_USER, japan)).model_dump()
        defaults = await identity.traveller_defaults(TEST_USER, today=date.today())
        outcome["plans_offered"] = [plan.model_dump(mode="json") for plan in defaults.plans]
    finally:
        (HERE / "probe-log.json").write_text(json.dumps(LOG, indent=1, default=str))
    print(json.dumps(outcome, indent=1))
    (HERE / "probe-result.json").write_text(json.dumps(outcome, indent=1))


asyncio.run(main())
