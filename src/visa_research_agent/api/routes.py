"""HTTP routing kept deliberately thin around the research workflow."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal, get_args

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import ValidationError

from visa_research_agent.api.allowance import AllowanceStoreError, AnonymousAllowance
from visa_research_agent.api.countries import normalise_country
from visa_research_agent.api.dependencies import (
    get_automatic_destinations,
    get_traveller_source,
    get_visa_plan_service,
)
from visa_research_agent.api.reports import (
    MAXIMUM_REPORT_BYTES,
    FileReportStore,
    ReportRequest,
    ReportStoreError,
    build_report,
    current_commit,
)
from visa_research_agent.api.schemas import (
    DestinationsResponse,
    DestinationSummary,
    HealthResponse,
    RegionsResponse,
    TravelAdviceResponse,
    VisaPlanRequest,
    WeatherResponse,
)
from visa_research_agent.api.signin import (
    PlanGate,
    SignIn,
    get_anonymous_allowance,
    get_sign_in,
    plan_gate,
    require_signed_in_for_plans,
)
from visa_research_agent.api.templates import static_asset_version, templates
from visa_research_agent.api.traveller import TravellerSource
from visa_research_agent.config.loader import get_destination_registry, get_runtime_policy
from visa_research_agent.config.regions import regions_for
from visa_research_agent.config.settings import settings
from visa_research_agent.config.traveller import DEFAULT_TRAVELLER_PROFILE
from visa_research_agent.discovery.adjudication import AdjudicationError
from visa_research_agent.discovery.advisories import (
    advice_link,
    get_advisory_links,
    get_advisory_publishers,
)
from visa_research_agent.discovery.automatic import (
    AutomaticDestinationService,
    AutomaticDiscoveryError,
)
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.registry import get_authority_registry
from visa_research_agent.discovery.search import SearchError
from visa_research_agent.discovery.selection import SelectionError
from visa_research_agent.domain.models import (
    DestinationConfig,
    TravellerProfile,
    TravelPurpose,
    VisaPlan,
)
from visa_research_agent.research.errors import (
    InsufficientEvidenceError,
    LLMExtractionError,
    VisaResearchError,
)
from visa_research_agent.research.personas import PersonasError
from visa_research_agent.research.robots import RobotsCache
from visa_research_agent.research.service import VisaPlanService
from visa_research_agent.research.tls import build_ssl_context
from visa_research_agent.weather.climate import normals_for
from visa_research_agent.weather.forecast import FORECAST_DAYS, ForecastClient
from visa_research_agent.weather.panel import weather_panel
from visa_research_agent.weather.places import cities_for, city_in

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(
    request: Request,
    sign_in: Annotated[SignIn | None, Depends(get_sign_in)],
    allowance: Annotated[AnonymousAllowance, Depends(get_anonymous_allowance)],
) -> HTMLResponse:
    policy = get_runtime_policy()
    signed_in = sign_in is not None and sign_in.signed_in_user(request) is not None
    # How many free plans this visitor has left, or None where plans are not counted for them.
    free_plans_left: int | None = None
    if not signed_in and not settings.require_sign_in:
        try:
            free_plans_left = allowance.remaining(
                request.client.host if request.client else "unknown"
            )
        except AllowanceStoreError:
            free_plans_left = None
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "destinations": researchable_destinations(),
            "countries": sorted(get_country_registry().countries, key=lambda c: c.name),
            "purposes": get_args(TravelPurpose),
            # A signed-in traveller is asked, never handed the default (TODO item 55, rule 4):
            # their passport comes from Ofself or from them, and where they apply from from them.
            "traveller": None if signed_in else DEFAULT_TRAVELLER_PROFILE,
            "sign_in_configured": sign_in is not None,
            "sign_in_required": settings.require_sign_in,
            "signed_in": signed_in,
            "free_plans_left": free_plans_left,
            "free_plan_allowance": settings.anonymous_plan_allowance,
            "source_mode": policy.source_mode,
            "extraction_mode": policy.extraction_mode,
            "static_asset_version": static_asset_version(),
        },
        headers={"Cache-Control": "no-store"},
    )


def researchable_destinations() -> list[DestinationSummary]:
    """Every destination a plan can be asked for.

    Under `destination_mode: automatic` that is every country with a usable row in
    `authority_domains.yaml`, not only the handful written into `destinations.yaml`. A country
    without one is refused before anything is fetched (`trusted_domains_for`), so offering it would
    only offer a refusal. A configured entry keeps its own route type, because Schengen membership
    is a fact about the destination rather than about how its sources were found.
    """

    registry = get_destination_registry()
    configured = {destination.slug: destination for destination in registry.destinations}
    if get_runtime_policy().destination_mode == "configured":
        return [
            DestinationSummary(
                slug=destination.slug,
                name=destination.display_name,
                route_type=destination.route_type,
                status=destination.implementation_status,
            )
            for destination in registry.destinations
        ]

    authorities = get_authority_registry()
    summaries: list[DestinationSummary] = []
    for country in sorted(get_country_registry().countries, key=lambda item: item.name):
        row = authorities.get(country.code)
        if row is None or not row.domains:
            continue
        entry = configured.get(country.slug)
        summaries.append(
            DestinationSummary(
                slug=country.slug,
                name=country.name,
                route_type=entry.route_type if entry is not None else "national",
                status="available",
            )
        )
    return summaries


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/regions/{country}", response_model=RegionsResponse, tags=["visa research"])
async def regions(country: str) -> RegionsResponse:
    """The regions a traveller in this country may choose from (TODO item 71, entry 252)."""

    try:
        code = normalise_country(country)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"message": str(exc)}) from exc
    return RegionsResponse(country=code, regions=list(regions_for(code)))


@router.get("/travel-advice", response_model=TravelAdviceResponse, tags=["visa research"])
async def travel_advice(passport: str, destination: str) -> TravelAdviceResponse:
    """A link to the passport's government's travel advice for this destination, or none.

    A lookup in committed data (entry 260): nothing is fetched, read or quoted, and nothing here
    reaches the visa answer.
    """

    try:
        passport_code = normalise_country(passport)
        by_slug = next(
            (c.code for c in get_country_registry().countries if c.slug == destination), None
        )
        destination_code = by_slug or normalise_country(destination)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"message": str(exc)}) from exc
    return TravelAdviceResponse(
        advice=advice_link(
            passport_code, destination_code, get_advisory_publishers(), get_advisory_links()
        )
    )


MAXIMUM_TRIP_DAYS = 731


@lru_cache(maxsize=1)
def get_forecast_client() -> ForecastClient:
    """One MET Norway client per process, so its forecasts are reused until they expire."""

    client = httpx.AsyncClient(
        headers={"User-Agent": settings.source_user_agent},
        verify=build_ssl_context(),
        timeout=15,
    )
    return ForecastClient(client, RobotsCache(user_agent=settings.source_user_agent))


@router.get("/weather", response_model=WeatherResponse, tags=["visa research"])
async def weather(
    destination: str,
    start: date,
    end: date,
    forecasts: Annotated[ForecastClient, Depends(get_forecast_client)],
    exact: bool = True,
    city: str | None = None,
) -> WeatherResponse:
    """Weather for the trip's dates, beside the plan and never an input to it (entry 261).

    The forecast where the dates fall inside MET Norway's window; past it, the city's committed
    monthly averages. `exact` is false for a rough span of months, which never gets a forecast.
    """

    if end < start or (end - start).days > MAXIMUM_TRIP_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "the dates must run forwards, over at most two years"},
        )
    try:
        by_slug = next(
            (c.code for c in get_country_registry().countries if c.slug == destination), None
        )
        code = by_slug or normalise_country(destination)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"message": str(exc)}) from exc
    place = city_in(code, city)
    if place is None:
        if city:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail={"message": f"{city} is not offered for {code}"}
            )
        return WeatherResponse(weather=None)
    now = datetime.now(UTC)
    window_end = now.date() + timedelta(days=FORECAST_DAYS)
    forecast = await forecasts.forecast(place) if exact and start <= window_end else None
    return WeatherResponse(
        weather=weather_panel(
            place,
            list(cities_for(code)),
            start,
            end,
            exact=exact,
            forecast=forecast,
            normals=normals_for(code, place.name),
            now=now,
        )
    )


@router.get("/destinations", response_model=DestinationsResponse, tags=["visa research"])
async def destinations() -> DestinationsResponse:
    return DestinationsResponse(destinations=researchable_destinations())


def corridor_for(destination_slug: str, traveller: TravellerProfile) -> Corridor:
    """The corridor a traveller profile describes.

    A straight mapping now that the profile holds ISO codes: the schema normalised whatever the
    caller wrote into the one form corridors, cache keys and the lexicon all use.
    """

    return Corridor(
        destination_slug=destination_slug,
        passport_nationality=traveller.passport_nationality,
        applying_from=traveller.country_of_residence,
        purpose=traveller.travel_purpose,
    )


def destination_country_code(requested: str) -> str | None:
    """The ISO code of the country a request names, however the caller wrote it."""

    registry = get_country_registry()
    wanted = requested.strip().lower()
    configured = get_destination_registry().get(wanted)
    if configured is not None:
        wanted = configured.display_name.lower()
    country = registry.by_slug(wanted) or next(
        (
            item
            for item in registry.countries
            if item.name.lower() == wanted
            or wanted in {synonym.lower() for synonym in item.synonyms}
        ),
        None,
    )
    return country.code if country else None


def refuse_impossible_corridors(requested: str, traveller: TravellerProfile) -> None:
    """Turn away a corridor that cannot have an answer, before anything is spent on it.

    A national of the destination does not apply to visit their own country, so there is no official
    visa guidance to find. Left alone it would search, crawl and spend two model calls to arrive at
    a refusal, which is slow, costs money, and reads as a fault rather than as the question being
    the wrong one.

    This is deliberately not a claim about entry rights — it says only that this agent researches
    visas for travellers who need one, which is a fact about the product.
    """

    code = destination_country_code(requested)
    if code is not None and code == traveller.passport_nationality:
        named = requested.replace("-", " ").title()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": (
                    f"This passport is issued by {named}, so there is no visa to research: a "
                    "country's own nationals do not apply for a visa to visit it. Choose a "
                    "different destination, or a different passport."
                ),
                "status": "not_applicable",
                "cause": "not_applicable",
            },
        )


async def resolve_destination(
    requested: str,
    traveller: TravellerProfile,
    automatic: AutomaticDestinationService | None,
    *,
    on_phase: Callable[[str], None] | None = None,
) -> DestinationConfig:
    """Research the destination, or use its hand-written entry when research is switched off.

    `on_phase` hears each research phase as it starts; a hand-written entry has none.
    """

    registry = get_destination_registry()
    destination = registry.get(requested)
    if automatic is None:
        # Only here may a hand-written entry answer. Under `automatic` it answered every traveller
        # from the pages written for one — Singapore's checklist is ICA's page for Indian travel
        # documents, Japan's the London embassy's — so a Filipino asking about Japan and a
        # Nigerian asking about Singapore were refused while discovery answers both from their own
        # post. DECISIONS entry 149.
        if destination is not None and destination.implementation_status == "available":
            return destination
        if destination is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": (
                        f"Visa-plan generation for {destination.display_name} is not available yet."
                    ),
                    "cause": "not_supported",
                },
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": f"Unsupported destination: {requested}",
                "cause": "not_supported",
                "supported_destinations": [item.slug for item in registry.destinations],
            },
        )

    name = destination.display_name if destination is not None else requested
    country = automatic.country_named(name)
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": (
                    f"{requested} is not a country this agent holds reference data for, so its "
                    "own government's domains cannot be told apart from other countries' pages "
                    "about it."
                ),
                "cause": "not_supported",
                "supported_destinations": [item.slug for item in researchable_destinations()],
            },
        )
    # Keyed on the country's own slug, never on the request as written. "united states" fails the
    # corridor's slug pattern, and "usa" fits it but would be stored as a corridor of its own beside
    # the `united-states` one the interface asks for. DECISIONS entry 168.
    corridor = corridor_for(country.slug, traveller)
    try:
        discovered = await automatic.destination_for(name, corridor, on_phase=on_phase)
    except AutomaticDiscoveryError as exc:
        # A refusal, not a fault. It names what could not be established rather than offering a
        # plan assembled from whatever happened to be readable.
        raise refusal(exc.cause, str(exc), unreadable_pages=exc.unreadable_urls) from exc
    except SearchError as exc:
        # Raised only where there is no stored corpus to answer from: "we could not look" must
        # never read as "there is nothing to find" (entry 74).
        raise refusal(
            "search_unavailable",
            f"{country.name}'s official sources could not be searched just now, and there are no "
            "stored pages to answer from. Nothing was concluded about this trip.",
        ) from exc
    except VisaResearchError as exc:
        raise research_fault(exc) from exc
    return discovered.config


RefusalReason = Literal[
    "not_supported",
    "not_applicable",
    "no_official_answer",
    "pages_unreadable",
    "check_failed",
    "search_unavailable",
    "internal_error",
]
"""What `detail.cause` may say, one per next step a traveller can take (TODO item 73)."""

# A model call that failed says nothing about the trip; anything else here is our own fault.
MODEL_CALL_ERRORS = (LLMExtractionError, PersonasError, AdjudicationError, SelectionError)


def refusal(
    cause: RefusalReason, message: str, *, unreadable_pages: list[str] | None = None, **extra: Any
) -> HTTPException:
    """A refusal both plan routes send, with the cause the page words its heading from."""

    detail: dict[str, Any] = {"message": message, "status": "unable_to_verify", "cause": cause}
    if unreadable_pages:
        detail["unreadable_pages"] = unreadable_pages
    detail.update(extra)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def research_fault(exc: VisaResearchError) -> HTTPException:
    """A research step that failed rather than refused. True of the cause, never of the trip."""

    if isinstance(exc, MODEL_CALL_ERRORS):
        return refusal(
            "check_failed",
            "A model call this research depends on did not complete, so nothing was concluded "
            "about this trip and nothing was saved. This is a fault on our side, not a finding "
            "about the trip: generate the plan again.",
        )
    return refusal(
        "internal_error",
        "The research could not be completed because of a fault on our side. Nothing was "
        "concluded about this trip.",
    )


@router.post(
    "/visa-plans",
    response_model=VisaPlan,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not signed in with Ofself"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Unsupported destination"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Could not be verified"},
    },
    tags=["visa research"],
)
async def create_visa_plan(
    request: VisaPlanRequest,
    # Checked before anything is spent; the plan is counted once it is known to be possible.
    gate: Annotated[PlanGate, Depends(plan_gate)],
    service: Annotated[VisaPlanService, Depends(get_visa_plan_service)],
    automatic: Annotated[AutomaticDestinationService | None, Depends(get_automatic_destinations)],
    travellers: Annotated[TravellerSource, Depends(get_traveller_source)],
) -> VisaPlan:
    traveller = await travellers.traveller_for(request)
    refuse_impossible_corridors(request.destination, traveller)
    gate.spend()
    return await research_plan(request.destination, traveller, service, automatic)


@router.post(
    "/visa-plans/stream",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Newline-delimited JSON: a `stage` event as each step starts, then exactly one "
                "`plan`, `refusal` or `error` event."
            ),
            "content": {"application/x-ndjson": {}},
        },
        status.HTTP_401_UNAUTHORIZED: {"description": "Not signed in with Ofself"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Unsupported destination"},
    },
    tags=["visa research"],
)
async def stream_visa_plan(
    request: VisaPlanRequest,
    gate: Annotated[PlanGate, Depends(plan_gate)],
    service: Annotated[VisaPlanService, Depends(get_visa_plan_service)],
    automatic: Annotated[AutomaticDestinationService | None, Depends(get_automatic_destinations)],
    travellers: Annotated[TravellerSource, Depends(get_traveller_source)],
) -> StreamingResponse:
    """The same plan as `POST /visa-plans`, with each step announced as it starts (TODO item 57).

    **Only progress streams, never content.** A stage event names which step has started — search,
    choosing pages, reading them, writing the plan — and carries nothing a step found. The plan
    arrives whole, after every validator has passed, exactly as the other route returns it: a
    half-written plan has passed none of them, and a claim it could still drop is the unverified,
    alarming answer entry 6 forbids.

    The checks that cost nothing run first, so a request that cannot be answered still gets its
    ordinary status code. From the first stage on the status is 200, and a refusal arrives as an
    event carrying the same `detail` the other route would have answered with.
    """

    traveller = await travellers.traveller_for(request)
    refuse_impossible_corridors(request.destination, traveller)
    gate.spend()

    async def work(report: Callable[[str], None]) -> VisaPlan:
        return await research_plan(
            request.destination, traveller, service, automatic, report=report
        )

    return StreamingResponse(
        plan_events(work),
        media_type="application/x-ndjson",
        # Without these a proxy may hold the stream back until it ends, which is the wait this
        # route exists to break up.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not signed in with Ofself"},
        status.HTTP_413_CONTENT_TOO_LARGE: {"description": "Larger than any page's output"},
    },
    tags=["visa research"],
    dependencies=[Depends(require_signed_in_for_plans)],
)
async def report_problem(http_request: Request) -> dict[str, str]:
    """Keep a traveller's report of a corridor that did not work, with its run (TODO item 74).

    The page sends the request it made, what it showed and the steps it saw; the server keeps only
    the corridor's codes from the request and attaches the run's log itself.
    """

    declared = http_request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAXIMUM_REPORT_BYTES:
        raise report_too_large()
    body = await http_request.body()
    if len(body) > MAXIMUM_REPORT_BYTES:
        raise report_too_large()
    try:
        received = ReportRequest.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "The report is not in the expected form."},
        ) from exc
    report = build_report(
        received,
        recall_directory=settings.recall_log_directory,
        static_asset_version=static_asset_version(),
        now=datetime.now(UTC),
        commit=current_commit(),
    )
    try:
        FileReportStore(settings.report_directory).store(report)
    except ReportStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "The report could not be saved. Try again later."},
        ) from exc
    return {"report_id": report.report_id}


def report_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail={"message": "The report is larger than any result this page produces."},
    )


async def research_plan(
    requested: str,
    traveller: TravellerProfile,
    service: VisaPlanService,
    automatic: AutomaticDestinationService | None,
    *,
    report: Callable[[str], None] | None = None,
) -> VisaPlan:
    """Resolve the destination and write its plan, turning a refusal into an HTTP answer.

    Shared by both plan routes so they cannot disagree about what is refused or how.
    """

    destination = await resolve_destination(requested, traveller, automatic, on_phase=report)
    try:
        return await service.generate(destination, traveller, on_stage=report)
    except InsufficientEvidenceError as exc:
        # A refusal explains which official evidence was missing, rather than failing opaquely.
        # With no failures on record the gap is the configuration's, which is ours to fix.
        raise refusal(
            "pages_unreadable" if exc.failures else "internal_error",
            f"A verified plan for {destination.display_name} could not be produced "
            "because required official evidence was unavailable.",
            # Only a stated refusal is handed over as a page to open (entries 27, 32); a timeout
            # or a stale page is explained in `reasons`, not named as one we were refused.
            unreadable_pages=[
                str(failure.attempted_url)
                for failure in exc.failures
                if failure.outcome == "blocked" and failure.http_status in (401, 403)
            ],
            reasons=exc.reasons,
            unavailable_sources=[failure.model_dump(mode="json") for failure in exc.failures],
        ) from exc
    except VisaResearchError as exc:
        raise research_fault(exc) from exc


async def plan_events(
    work: Callable[[Callable[[str], None]], Coroutine[Any, Any, VisaPlan]],
) -> AsyncIterator[str]:
    """Run `work`, yielding one JSON line per stage it reports and then one for its outcome.

    The work runs as its own task so a stage can be sent while it is still going. If the browser
    goes away the task is cancelled rather than left to spend searches and model calls on a plan
    nobody will read.
    """

    stages: asyncio.Queue[str | None] = asyncio.Queue()
    task = asyncio.create_task(work(stages.put_nowait))
    # The end of the work is one more item in the same queue, so every stage it reported is sent
    # before its outcome.
    task.add_done_callback(lambda _: stages.put_nowait(None))
    try:
        while (stage := await stages.get()) is not None:
            yield event_line({"event": "stage", "stage": stage})
        try:
            plan = task.result()
        except HTTPException as exc:
            yield event_line(
                {"event": "refusal", "status_code": exc.status_code, "detail": exc.detail}
            )
        except Exception:
            # Past the first line the status is already 200, so a fault has to be said in the
            # stream. It is still raised, so the server logs it as it would any other.
            yield event_line({"event": "error", "message": "The plan could not be generated."})
            raise
        else:
            yield event_line({"event": "plan", "plan": plan.model_dump(mode="json")})
    finally:
        if not task.done():
            task.cancel()


def event_line(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":")) + "\n"
