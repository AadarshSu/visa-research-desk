import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from visa_research_agent.api.app import create_app
from visa_research_agent.api.dependencies import (
    get_automatic_destinations,
    get_traveller_source,
    get_visa_plan_service,
)
from visa_research_agent.api.routes import resolve_destination
from visa_research_agent.api.schemas import VisaPlanRequest
from visa_research_agent.api.signin import require_signed_in_for_plans
from visa_research_agent.discovery.automatic import (
    AutomaticDestinationService,
    AutomaticDiscoveryError,
    find_country,
)
from visa_research_agent.discovery.corpus import CorpusError
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.registry import get_authority_registry
from visa_research_agent.discovery.search import SearchQuotaExhausted
from visa_research_agent.domain.models import (
    DestinationConfig,
    RuntimePolicy,
    SourceFailure,
    TravellerProfile,
    VisaPlan,
)
from visa_research_agent.research.errors import (
    InsufficientEvidenceError,
    LLMExtractionError,
    VisaResearchError,
)

OFFLINE_POLICY = RuntimePolicy(
    schema_version=1,
    source_mode="fixtures",
    extraction_mode="fixture",
    source_cache_ttl_hours=24.0,
    source_maximum_stale_hours=168.0,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    # Pin the policy so these tests never depend on the committed runtime.yaml or reach a network.
    # Both modules import the loader by name, so each reference needs replacing.
    for module in ("dependencies", "routes"):
        monkeypatch.setattr(
            f"visa_research_agent.api.{module}.get_runtime_policy",
            lambda: OFFLINE_POLICY,
        )
    get_visa_plan_service.cache_clear()
    get_automatic_destinations.cache_clear()
    app = create_app()
    # These tests are about the plan, not who asked for it; test_signin covers the gate.
    app.dependency_overrides[require_signed_in_for_plans] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    get_visa_plan_service.cache_clear()
    get_automatic_destinations.cache_clear()


@pytest.mark.anyio
async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_a_countrys_regions_are_served_for_the_form(client: httpx.AsyncClient) -> None:
    """TODO item 71: the region field lists only the chosen country's regions."""

    response = await client.get("/regions/gb")

    assert response.status_code == 200
    assert response.json() == {
        "country": "GB",
        "regions": ["England", "Northern Ireland", "Scotland", "Wales"],
    }
    assert (await client.get("/regions/SG")).json()["regions"] == []
    assert (await client.get("/regions/Atlantis")).status_code == 404


@pytest.mark.anyio
async def test_destinations_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/destinations")

    assert response.status_code == 200
    # Under `configured` this is the hand-written registry, in its own order.
    assert [item["slug"] for item in response.json()["destinations"]] == [
        "singapore",
        "japan",
        "vietnam",
        "brazil",
        "china",
        "united-states",
        "france",
    ]
    assert response.json()["destinations"][0]["status"] == "available"


@pytest.mark.anyio
async def test_research_interface_is_available(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="plan-form"' in response.text
    assert "/static/styles.css?v=" in response.text
    assert "/static/app.js?v=" in response.text
    assert "Generate plan" in response.text
    assert "Singapore" in response.text


@pytest.mark.anyio
async def test_unsupported_destination_returns_helpful_error(client: httpx.AsyncClient) -> None:
    response = await client.post("/visa-plans", json={"destination": "canada"})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "message": "Unsupported destination: canada",
        "cause": "not_supported",
        "supported_destinations": [
            "singapore",
            "japan",
            "vietnam",
            "brazil",
            "china",
            "united-states",
            "france",
        ],
    }


@pytest.mark.anyio
async def test_singapore_fixture_plan_is_returned(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/visa-plans", json={"destination": " Singapore "})

    assert response.status_code == 200
    plan = response.json()
    assert plan["destination"] == "Singapore"
    assert plan["visa_required"] is True
    assert plan["visa_type"] == "Entry visa for a social visit (tourism)"
    assert plan["requirements"]
    assert plan["application_document_source_ids"] == ["sg_ica_india_visa_details"]
    assert all("category" not in requirement for requirement in plan["requirements"])
    assert all(
        "sg_ica_india_visa_details" in requirement["source_ids"]
        for requirement in plan["requirements"]
    )
    assert 2 <= len(plan["application_steps"]) <= 5
    assert all(
        {
            "title",
            "action",
            "timing",
            "source_ids",
            "link_target",
            "link_source_id",
        }
        == step.keys()
        for step in plan["application_steps"]
    )
    assert any(step["link_target"] == "application_route" for step in plan["application_steps"])
    assert len(plan["sources"]) == 5
    # And every source names the version of the page it was read from.
    assert all(len(source["content_hash"]) == 64 for source in plan["sources"])
    assert plan["last_checked"] == "2026-08-06T11:30:00Z"


@pytest.mark.anyio
async def test_supported_but_unimplemented_destination_is_explicit(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/visa-plans", json={"destination": "france"})

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == (
        "Visa-plan generation for France is not available yet."
    )


@pytest.mark.anyio
async def test_the_interface_lets_a_traveller_be_described(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    for field in ('id="nationality"', 'id="residence"', 'id="purpose"'):
        assert field in response.text
    # The default the interface opens on, so an unchanged form reproduces the fixture baseline.
    assert '<option value="IN" selected>' in response.text
    assert '<option value="GB" selected>' in response.text
    assert '<option value="business"' in response.text


class RecordingAutomatic:
    """Stands in for request-time discovery, and records what it was asked for."""

    def __init__(self, config: DestinationConfig) -> None:
        self.config = config
        self.asked: list[tuple[str, Corridor]] = []

    def country_named(self, name: str) -> object:
        # The real lookup, so a request written as a name or a synonym is found the way it is live.
        return find_country(name, get_country_registry())

    async def destination_for(
        self, name: str, corridor: Corridor, *, on_phase: Callable[[str], None] | None = None
    ) -> SimpleNamespace:
        self.asked.append((name, corridor))
        if on_phase is not None:
            on_phase("search")
        return SimpleNamespace(config=self.config)


@pytest.mark.anyio
@pytest.mark.parametrize("slug", ["singapore", "japan"])
async def test_a_configured_destination_is_researched_when_research_is_on(slug: str) -> None:
    """Entry 149: a hand-written entry answered every traveller from the pages written for one.

    Singapore's checklist is ICA's page for Indian travel documents and Japan's the London
    embassy's, so under `automatic` a Filipino asking about Japan and a Nigerian asking about
    Singapore were refused while discovery answers both from their own post.
    """

    discovered = DestinationConfig(
        slug=slug,
        display_name=slug.title(),
        route_type="national",
        implementation_status="available",
        trusted_domains=["example.gov.sg"],
    )
    automatic = RecordingAutomatic(discovered)
    traveller = TravellerProfile(
        passport_nationality="PH", passport_type="ordinary", country_of_residence="PH"
    )

    chosen = await resolve_destination(
        slug, traveller, cast(AutomaticDestinationService, automatic)
    )

    assert chosen is discovered
    assert [corridor.passport_nationality for _, corridor in automatic.asked] == ["PH"]


@pytest.mark.anyio
async def test_a_configured_destination_is_used_as_written_when_research_is_off() -> None:
    traveller = TravellerProfile(
        passport_nationality="IN", passport_type="ordinary", country_of_residence="GB"
    )

    chosen = await resolve_destination("japan", traveller, None)

    assert chosen.sources, "the hand-written entry's own pages were lost"


@pytest.mark.anyio
async def test_a_request_may_name_a_different_traveller(client: httpx.AsyncClient) -> None:
    """The fixture is recorded for one traveller, so a different one must be refused clearly
    rather than answered with the wrong person's plan."""

    response = await client.post(
        "/visa-plans",
        json={
            "destination": "singapore",
            "traveller": {"passport_nationality": "CN", "country_of_residence": "AE"},
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["cause"] == "internal_error"
    assert "Nothing was concluded" in response.json()["detail"]["message"]


@pytest.mark.anyio
async def test_a_country_with_no_reference_data_is_rejected_before_anything_runs(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/visa-plans",
        json={
            "destination": "singapore",
            "traveller": {"passport_nationality": "Atlantis", "country_of_residence": "GB"},
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_the_interface_does_not_describe_one_particular_traveller(
    client: httpx.AsyncClient,
) -> None:
    """The page used to announce it researched an Indian passport holder living in Edinburgh.

    Any traveller can be asked about now, so copy naming one is simply wrong.
    """

    response = await client.get("/")

    for hardcoded in ("Edinburgh", "Indian passport holder", "resident in the UK"):
        assert hardcoded not in response.text


@pytest.mark.anyio
async def test_every_built_country_and_no_other_is_offered_when_destinations_are_automatic(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Destinations stop being the handful in `destinations.yaml`, and stop at the registry.

    An unconfigured country is researched when it is asked for, so offering only the configured
    ones would hide most of what the agent can actually do. A country with no row in
    `authority_domains.yaml` is refused before anything is fetched, so it is not offered.
    """

    automatic = OFFLINE_POLICY.model_copy(update={"destination_mode": "automatic"})
    for module in ("dependencies", "routes"):
        monkeypatch.setattr(
            f"visa_research_agent.api.{module}.get_runtime_policy", lambda: automatic
        )

    slugs = [item["slug"] for item in (await client.get("/destinations")).json()["destinations"]]

    assert "united-arab-emirates" in slugs
    assert "thailand" in slugs
    assert "afghanistan" not in slugs
    authorities = get_authority_registry()
    assert len(slugs) == sum(1 for row in authorities.countries if row.domains)
    assert len(slugs) < len(get_country_registry().countries)
    # Nothing is offered that cannot be acted on.
    assert all(
        item["status"] == "available"
        for item in (await client.get("/destinations")).json()["destinations"]
    )


@pytest.mark.anyio
async def test_a_passport_of_the_destination_is_turned_away_before_anything_is_spent(
    client: httpx.AsyncClient,
) -> None:
    """A national of the destination has no visa to research, so there is no guidance to find.

    Left alone this searched, crawled and spent two model calls to arrive at a refusal — slow, paid
    for, and reading as a fault rather than as the question being the wrong one. It is not a claim
    about entry rights: it says only what this agent researches.
    """

    response = await client.post(
        "/visa-plans",
        json={
            "destination": "singapore",
            "traveller": {
                "passport_nationality": "SG",
                "country_of_residence": "GB",
                "travel_purpose": "tourism",
            },
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["status"] == "not_applicable"
    assert "own nationals do not apply" in detail["message"]


@pytest.mark.anyio
async def test_the_same_corridor_for_another_passport_is_still_researched(
    client: httpx.AsyncClient,
) -> None:
    """The guard must be narrow: only the passport matches the destination, never the residence.

    Applying from inside the destination is ordinary — in-country applications exist — so a resident
    of the destination holding another passport must still get a plan.
    """

    response = await client.post(
        "/visa-plans",
        json={
            "destination": "singapore",
            "traveller": {
                "passport_nationality": "IN",
                "country_of_residence": "SG",
                "travel_purpose": "tourism",
            },
        },
    )

    assert response.status_code != 422


UNITED_STATES = DestinationConfig(
    slug="united-states",
    display_name="United States",
    route_type="national",
    implementation_status="available",
    trusted_domains=["example.gov"],
)


class StoppingPlanService:
    """Stops once a destination is resolved: these tests are about the route, not the plan."""

    async def generate(
        self,
        destination: DestinationConfig,
        traveller: TravellerProfile,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> VisaPlan:
        raise VisaResearchError("the route is under test, not the plan")


@pytest.fixture
async def researching(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, RecordingAutomatic]]:
    """The app under `destination_mode: automatic`, with discovery and the plan stood in for."""

    policy = OFFLINE_POLICY.model_copy(update={"destination_mode": "automatic"})
    monkeypatch.setattr("visa_research_agent.api.routes.get_runtime_policy", lambda: policy)
    automatic = RecordingAutomatic(UNITED_STATES)
    app = create_app()
    app.dependency_overrides[get_automatic_destinations] = lambda: automatic
    app.dependency_overrides[get_visa_plan_service] = StoppingPlanService
    app.dependency_overrides[require_signed_in_for_plans] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client, automatic


@pytest.mark.anyio
async def test_a_destination_written_as_a_name_is_researched_not_a_500(
    researching: tuple[httpx.AsyncClient, RecordingAutomatic],
) -> None:
    """Entry 168: `"United States"` reached the corridor as written and crashed the request.

    The schema only lowercases, and `"united states"` fails `Corridor.destination_slug`'s pattern,
    so the unhandled validation error answered `HTTP 500`. The interface sends the slug and never
    met it; an API caller writing the country's name did.
    """

    client, automatic = researching

    response = await client.post(
        "/visa-plans",
        json={
            "destination": "United States",
            "traveller": {
                "passport_nationality": "IN",
                "country_of_residence": "GB",
                "travel_purpose": "tourism",
            },
        },
    )

    # 503 is the stand-in plan service stopping; what matters is that the route got that far.
    assert response.status_code == 503
    assert [corridor.key for _, corridor in automatic.asked] == ["united-states/IN/GB/tourism"]


class FixedTravellerSource:
    """Stands in for a traveller source other than the request body, such as an Ofself identity."""

    def __init__(self, traveller: TravellerProfile) -> None:
        self.traveller = traveller
        self.asked: list[VisaPlanRequest] = []

    async def traveller_for(self, request: VisaPlanRequest) -> TravellerProfile:
        self.asked.append(request)
        return self.traveller


@pytest.mark.anyio
async def test_the_plan_is_researched_for_the_traveller_the_injected_source_supplies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 55: the route asks a source for the traveller rather than reading the body itself, so
    an Ofself identity can replace the form without the route or anything past it changing."""

    policy = OFFLINE_POLICY.model_copy(update={"destination_mode": "automatic"})
    monkeypatch.setattr("visa_research_agent.api.routes.get_runtime_policy", lambda: policy)
    automatic = RecordingAutomatic(UNITED_STATES)
    source = FixedTravellerSource(
        TravellerProfile(
            passport_nationality="NG", passport_type="ordinary", country_of_residence="NG"
        )
    )
    app = create_app()
    app.dependency_overrides[get_automatic_destinations] = lambda: automatic
    app.dependency_overrides[get_visa_plan_service] = StoppingPlanService
    app.dependency_overrides[require_signed_in_for_plans] = lambda: None
    app.dependency_overrides[get_traveller_source] = lambda: source
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/visa-plans",
            json={
                "destination": "united-states",
                "traveller": {"passport_nationality": "IN", "country_of_residence": "GB"},
            },
        )

    # 503 is the stand-in plan service stopping; what matters is whose corridor was researched.
    assert response.status_code == 503
    assert [request.destination for request in source.asked] == ["united-states"]
    assert [corridor.key for _, corridor in automatic.asked] == ["united-states/NG/NG/tourism"]


@pytest.mark.anyio
@pytest.mark.parametrize("written", ["united states", "USA", "usa", "united-states"])
async def test_every_way_of_writing_a_destination_is_one_corridor(written: str) -> None:
    """A name, a synonym and the slug are one corridor, so one stored answer rather than several.

    `"usa"` fits the slug pattern, so before entry 168 it did not crash — it was researched and
    stored as a corridor of its own, beside the `united-states` one the interface asks for.
    """

    automatic = RecordingAutomatic(UNITED_STATES)
    traveller = TravellerProfile(
        passport_nationality="IN", passport_type="ordinary", country_of_residence="GB"
    )

    await resolve_destination(
        written.strip().lower(), traveller, cast(AutomaticDestinationService, automatic)
    )

    assert [corridor.destination_slug for _, corridor in automatic.asked] == ["united-states"]


@pytest.mark.anyio
@pytest.mark.parametrize("written", ["Narnia", "united_states", "United-States-of"])
async def test_a_destination_that_names_no_country_is_a_422_before_anything_runs(
    researching: tuple[httpx.AsyncClient, RecordingAutomatic], written: str
) -> None:
    client, automatic = researching

    response = await client.post("/visa-plans", json={"destination": written})

    assert response.status_code == 422
    message = response.json()["detail"]["message"]
    assert "not a country this agent holds reference data for" in message
    assert automatic.asked == []


def test_every_country_slug_can_key_a_corridor() -> None:
    """The route keys a corridor on `Country.slug`, so every slug must fit the corridor's pattern.

    One that did not would be entry 168's `HTTP 500` again, for that one country.
    """

    for country in get_country_registry().countries:
        Corridor(destination_slug=country.slug, passport_nationality="IN", applying_from="GB")


# --- the streamed plan (TODO item 57) --------------------------------------------------------


def stream_events(body: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in body.splitlines() if line]


@pytest.mark.anyio
async def test_the_streamed_plan_is_the_same_plan_after_its_stages(
    client: httpx.AsyncClient,
) -> None:
    """Only progress streams: the plan arrives whole, exactly as the other route returns it."""

    request = {"destination": "singapore"}

    streamed = await client.post("/visa-plans/stream", json=request)
    whole = await client.post("/visa-plans", json=request)

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("application/x-ndjson")
    events = stream_events(streamed.text)
    assert [event["stage"] for event in events[:-1]] == ["retrieve", "write"]
    assert events[-1] == {"event": "plan", "plan": whole.json()}


@pytest.mark.anyio
async def test_a_stage_carries_its_name_and_nothing_it_found(client: httpx.AsyncClient) -> None:
    streamed = await client.post("/visa-plans/stream", json={"destination": "singapore"})

    for event in stream_events(streamed.text)[:-1]:
        assert set(event) == {"event", "stage"}


@pytest.mark.anyio
async def test_a_streamed_refusal_carries_the_same_detail_as_the_other_route(
    researching: tuple[httpx.AsyncClient, RecordingAutomatic],
) -> None:
    client, _ = researching
    request = {
        "destination": "united-states",
        "traveller": {"passport_nationality": "IN", "country_of_residence": "GB"},
    }

    streamed = await client.post("/visa-plans/stream", json=request)
    whole = await client.post("/visa-plans", json=request)

    events = stream_events(streamed.text)
    # Forwarded from discovery itself; the stand-in plan service reports no stages of its own.
    assert [event["stage"] for event in events[:-1]] == ["search"]
    assert events[-1] == {
        "event": "refusal",
        "status_code": whole.status_code,
        "detail": whole.json()["detail"],
    }


@pytest.mark.anyio
async def test_a_request_that_cannot_be_answered_is_refused_before_the_stream_starts(
    client: httpx.AsyncClient,
) -> None:
    """The free checks keep their ordinary status code; nothing is streamed or spent."""

    response = await client.post(
        "/visa-plans/stream",
        json={
            "destination": "singapore",
            "traveller": {"passport_nationality": "SG", "country_of_residence": "SG"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["status"] == "not_applicable"


# --- every refusal says which kind it is (TODO item 73) ---------------------------------------


class RaisingAutomatic(RecordingAutomatic):
    """Discovery that fails the way a live run can."""

    def __init__(self, error: Exception) -> None:
        super().__init__(UNITED_STATES)
        self.error = error

    async def destination_for(
        self, name: str, corridor: Corridor, *, on_phase: Callable[[str], None] | None = None
    ) -> SimpleNamespace:
        raise self.error


class FailingPlanService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(
        self,
        destination: DestinationConfig,
        traveller: TravellerProfile,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> VisaPlan:
        raise self.error


REFUSED_PAGE = "https://travel.state.gov/content/travel/en/us-visas.html"
US_REQUEST = {
    "destination": "united-states",
    "traveller": {"passport_nationality": "IN", "country_of_residence": "GB"},
}


def app_failing_with(
    monkeypatch: pytest.MonkeyPatch,
    *,
    discovery: Exception | None = None,
    plan: Exception | None = None,
) -> httpx.AsyncClient:
    policy = OFFLINE_POLICY.model_copy(update={"destination_mode": "automatic"})
    monkeypatch.setattr("visa_research_agent.api.routes.get_runtime_policy", lambda: policy)
    app = create_app()
    automatic = RaisingAutomatic(discovery) if discovery else RecordingAutomatic(UNITED_STATES)
    app.dependency_overrides[get_automatic_destinations] = lambda: automatic
    app.dependency_overrides[get_visa_plan_service] = lambda: FailingPlanService(
        plan or VisaResearchError("unused")
    )
    app.dependency_overrides[require_signed_in_for_plans] = lambda: None
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("discovery", "plan", "cause"),
    [
        (
            AutomaticDiscoveryError(
                "refused", cause="pages_unreadable", unreadable_urls=[REFUSED_PAGE]
            ),
            None,
            "pages_unreadable",
        ),
        (AutomaticDiscoveryError("none", cause="no_official_answer"), None, "no_official_answer"),
        (AutomaticDiscoveryError("model", cause="check_failed"), None, "check_failed"),
        (SearchQuotaExhausted("out of credit"), None, "search_unavailable"),
        (CorpusError("unreadable store"), None, "internal_error"),
        (None, LLMExtractionError("The OpenAI extraction request failed"), "check_failed"),
        (None, VisaResearchError("anything else"), "internal_error"),
    ],
)
async def test_every_refusal_names_its_cause_on_both_routes(
    monkeypatch: pytest.MonkeyPatch,
    discovery: Exception | None,
    plan: Exception | None,
    cause: str,
) -> None:
    async with app_failing_with(monkeypatch, discovery=discovery, plan=plan) as client:
        plain = await client.post("/visa-plans", json=US_REQUEST)
        streamed = await client.post("/visa-plans/stream", json=US_REQUEST)

    assert plain.status_code == 503
    assert plain.json()["detail"]["cause"] == cause
    outcome = json.loads(streamed.text.strip().splitlines()[-1])
    assert outcome["event"] == "refusal"
    assert outcome["detail"] == plain.json()["detail"], "the two routes must say the same thing"
    if cause == "pages_unreadable":
        assert plain.json()["detail"]["unreadable_pages"] == [REFUSED_PAGE]
    else:
        assert "unreadable_pages" not in plain.json()["detail"]


@pytest.mark.anyio
async def test_a_model_fault_is_never_worded_as_a_missing_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = LLMExtractionError("The OpenAI extraction request failed")
    async with app_failing_with(monkeypatch, plan=error) as client:
        detail = (await client.post("/visa-plans", json=US_REQUEST)).json()["detail"]

    assert "not a finding about the trip" in detail["message"]
    assert "evidence" not in detail["message"]


@pytest.mark.anyio
async def test_only_a_stated_refusal_is_handed_over_as_a_page_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries 27 and 32: a 403 may be named; a timeout is explained, never named as a refusal."""

    def failure(url: str, outcome: str, http_status: int | None) -> SourceFailure:
        return SourceFailure(
            source_id="page",
            title="Visa page",
            authority="U.S. Department of State",
            outcome=outcome,  # type: ignore[arg-type]
            detail="could not be read",
            attempted_url=url,  # type: ignore[arg-type]
            http_status=http_status,
        )

    error = InsufficientEvidenceError(
        "missing",
        reasons=["Visa page could not be used"],
        failures=[
            failure(REFUSED_PAGE, "blocked", 403),
            failure("https://travel.state.gov/slow.html", "unreachable", None),
        ],
    )
    async with app_failing_with(monkeypatch, plan=error) as client:
        detail = (await client.post("/visa-plans", json=US_REQUEST)).json()["detail"]

    assert detail["cause"] == "pages_unreadable"
    assert detail["unreadable_pages"] == [REFUSED_PAGE]
    assert detail["reasons"] == ["Visa page could not be used"]


@pytest.mark.anyio
async def test_travel_advice_is_a_link_from_the_passports_government(
    client: httpx.AsyncClient,
) -> None:
    """Entry 260: a lookup in committed data — a link, whose it is, and nothing of the advice."""

    response = await client.get("/travel-advice", params={"passport": "GB", "destination": "japan"})

    assert response.status_code == 200
    advice = response.json()["advice"]
    assert advice["url"].startswith("https://www.gov.uk/foreign-travel-advice")
    assert advice["written_for"] == "British nationals"
    assert set(advice) == {
        "url", "government", "written_for", "language", "about_destination", "checked",
        "english_url",
    }  # fmt: skip


@pytest.mark.anyio
async def test_no_travel_advice_where_the_government_publishes_none(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/travel-advice", params={"passport": "PW", "destination": "japan"})

    assert response.status_code == 200
    assert response.json() == {"advice": None}
