"""FastAPI dependency factories for independently testable business services."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from visa_research_agent.api.signin import SignIn, get_sign_in
from visa_research_agent.api.traveller import (
    RequestBodyTravellerSource,
    SharedDetailsTravellerSource,
    TravellerSource,
)
from visa_research_agent.config.loader import get_runtime_policy
from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.automatic import AutomaticDestinationService
from visa_research_agent.discovery.corpus import FileCorpusStore
from visa_research_agent.discovery.corridor_store import FileCorridorStore
from visa_research_agent.domain.models import RuntimePolicy
from visa_research_agent.research.errors import LLMConfigurationError
from visa_research_agent.research.fixtures import FixtureSourceFetcher, FixtureVisaPlanExtractor
from visa_research_agent.research.interfaces import SourceFetcher
from visa_research_agent.research.live_sources import LiveSourceFetcher
from visa_research_agent.research.model_usage import FileModelUsageLog
from visa_research_agent.research.openai_extraction import (
    LangChainStructuredPlanGenerator,
    OpenAIVisaPlanExtractor,
)
from visa_research_agent.research.personas import (
    PersonasPlanGenerator,
    personas_client_from_settings,
)
from visa_research_agent.research.plan_store import FilePlanStore, PlanReuse
from visa_research_agent.research.rendering import build_page_renderer
from visa_research_agent.research.service import VisaPlanService
from visa_research_agent.research.source_cache import FileSourceCache


def build_source_fetcher(policy: RuntimePolicy) -> SourceFetcher:
    """Select offline fixture evidence or live retrieval from the reviewed runtime policy."""

    if policy.source_mode == "fixtures":
        return FixtureSourceFetcher()

    return LiveSourceFetcher(
        FileSourceCache(settings.cache_directory),
        ttl_hours=policy.source_cache_ttl_hours,
        maximum_stale_hours=policy.source_maximum_stale_hours,
        timeout_seconds=settings.source_fetch_timeout_seconds,
        concurrency=settings.source_fetch_concurrency,
        maximum_characters=settings.maximum_source_characters,
        minimum_characters=settings.minimum_source_characters,
        user_agent=settings.source_user_agent,
        maximum_bytes=settings.maximum_source_bytes,
        renderer=build_page_renderer(policy),
        maximum_renders=settings.maximum_source_renders,
    )


def build_visa_plan_service(policy: RuntimePolicy) -> VisaPlanService:
    """Assemble the retrieval and extraction pipeline described by the runtime policy."""

    source_fetcher = build_source_fetcher(policy)
    if policy.extraction_mode == "fixture":
        return VisaPlanService(source_fetcher, FixtureVisaPlanExtractor())

    generator: LangChainStructuredPlanGenerator | PersonasPlanGenerator
    if policy.model_route == "personas":
        generator = PersonasPlanGenerator(personas_client_from_settings())
    else:
        if (
            settings.openai_api_key is None
            or not settings.openai_api_key.get_secret_value().strip()
        ):
            raise LLMConfigurationError("OPENAI_API_KEY is required for OpenAI extraction")
        if settings.openai_model is None or not settings.openai_model.strip():
            raise LLMConfigurationError("OPENAI_MODEL is required for OpenAI extraction")
        generator = LangChainStructuredPlanGenerator(
            api_key=settings.openai_api_key.get_secret_value(),
            model_name=settings.openai_model,
            request_timeout_seconds=settings.openai_request_timeout_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
            reasoning_effort=settings.openai_reasoning_effort,
        )
    extractor = OpenAIVisaPlanExtractor(
        generator,
        maximum_input_characters=settings.maximum_model_input_characters,
        usage_log=FileModelUsageLog(settings.model_usage_directory),
        reuse=(
            PlanReuse(
                store=FilePlanStore(settings.plan_directory),
                maximum_age_hours=policy.plan_reuse_hours,
                fingerprint=generator.fingerprint,
            )
            if policy.plan_reuse_hours > 0
            else None
        ),
    )
    return VisaPlanService(source_fetcher, extractor)


def build_automatic_destinations(policy: RuntimePolicy) -> AutomaticDestinationService | None:
    """Build request-time discovery when the policy asks for it, or none when it does not."""

    if policy.destination_mode == "configured":
        return None

    # Imported here: the CLI owns how a resolver is assembled, and importing it at module scope
    # would make the API depend on the command line rather than the other way round.
    from visa_research_agent.discovery.cli import (
        build_resolver,
        build_role_adjudicator,
        build_search_provider,
    )
    from visa_research_agent.research.rendering import build_page_renderer

    renderer = build_page_renderer(policy)
    adjudicator = build_role_adjudicator(policy)
    return AutomaticDestinationService(
        build_search_provider(),
        # Keyword arguments are forwarded, so the service can hand the resolver the country's corpus
        # and this corridor's proven pages without the API knowing how a resolver is assembled.
        lambda **kwargs: build_resolver(renderer, adjudicator, **kwargs),
        FileCorridorStore(settings.corridor_directory),
        corpus=FileCorpusStore(settings.corpus_directory),
        maximum_age_hours=settings.corridor_maximum_age_hours,
    )


@lru_cache(maxsize=1)
def get_visa_plan_service() -> VisaPlanService:
    return build_visa_plan_service(get_runtime_policy())


@lru_cache(maxsize=1)
def get_automatic_destinations() -> AutomaticDestinationService | None:
    """The service itself is cached; **resolved corridors are not.**

    Caching the service is safe because it holds no corridor state. Corridors expire, so they live
    in a file store with an age check rather than in a process-lifetime memo that could serve a
    weeks-old answer for as long as the server stays up.
    """

    return build_automatic_destinations(get_runtime_policy())


def get_traveller_source(
    request: Request, sign_in: Annotated[SignIn | None, Depends(get_sign_in)]
) -> TravellerSource:
    """The request body; for a signed-in traveller, with what they shared through Ofself too
    (entry 276)."""

    user_id = sign_in.signed_in_user(request) if sign_in is not None else None
    if sign_in is None or user_id is None:
        return RequestBodyTravellerSource()
    return SharedDetailsTravellerSource(sign_in.identity, user_id)
