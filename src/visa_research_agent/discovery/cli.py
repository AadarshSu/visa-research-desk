"""Command line entry points for discovery.

Discovery is a separate command rather than part of a research run. It contacts many pages and its
output changes what the application would treat as official, so it stays a deliberate act with a
person looking at the result.

Exit codes are meaningful, so this can be used in a script:
    0  resolved, every load-bearing role filled
    1  resolved with a non-essential role missing
    2  refused, a load-bearing role could not be filled
    3  configuration error
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from visa_research_agent.config.loader import (
    config_path,
    get_destination_registry,
    get_runtime_policy,
)
from visa_research_agent.config.regions import (
    REGIONS_FILENAME,
    regions_from_geonames,
    render_regions_yaml,
)
from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.adjudication import (
    LangChainRoleAdjudicator,
    RoleAdjudicator,
)
from visa_research_agent.discovery.advisories_build import (
    SurveyProgress,
    build_advisory_survey,
    load_advisory_survey,
    write_advisory_survey,
)
from visa_research_agent.discovery.audit import (
    CAUSE_LABELS,
    CAUSE_ORDER,
    POSTURE_COST,
    Reachability,
    RecallAudit,
    audit_records,
    counted,
    reachability,
    read_records,
)
from visa_research_agent.discovery.automatic import (
    AutomaticDiscoveryError,
    find_country,
    prepare_destination,
    trusted_domains_for,
    unknown_country,
)
from visa_research_agent.discovery.bootstrap import (
    BootstrapReport,
    bootstrap_destination,
    entry_point_for,
)
from visa_research_agent.discovery.contention import (
    Contention,
    contention_for,
    ranked_for_role,
    unpooled_by_text,
)
from visa_research_agent.discovery.corpus import CountryCorpus, FileCorpusStore
from visa_research_agent.discovery.corpus_build import (
    DEFAULT_CORPUS_DEPTH,
    DEFAULT_CORPUS_PAGES,
    DEFAULT_CORPUS_RENDERS,
    CorpusBuild,
    build_country_corpus,
)
from visa_research_agent.discovery.coverage import (
    VERDICT_MEANING,
    CountryCoverage,
    CoverageReport,
    KnownAnswer,
    coverage,
)
from visa_research_agent.discovery.coverage import (
    report as coverage_report,
)
from visa_research_agent.discovery.crawl import CrawlFetcher
from visa_research_agent.discovery.lexicon import (
    Country,
    CountryRegistry,
    get_country_registry,
    get_denylist,
    get_lexicon,
)
from visa_research_agent.discovery.models import (
    CandidatePage,
    Corridor,
    PageLink,
    ResolvedCorridor,
)
from visa_research_agent.discovery.page_text import (
    BackfillReport,
    PageTextStore,
    TextMatch,
    backfill_from_cache,
)
from visa_research_agent.discovery.proposal import render_corridor_yaml
from visa_research_agent.discovery.recall_log import (
    FileRecallLog,
    ModelCall,
    RecallRecord,
    VarianceReport,
    compare_runs,
)
from visa_research_agent.discovery.registry import (
    REGISTRY_FILENAME,
    get_authority_registry,
    load_authority_registry,
)
from visa_research_agent.discovery.registry_build import (
    BuildProgress,
    build_authority_registry,
    write_registry,
)
from visa_research_agent.discovery.resolver import DEFAULT_SHORTLIST_SIZE, CorridorResolver
from visa_research_agent.discovery.search import (
    BraveSearchProvider,
    SearchError,
    resolve_corridor_countries,
)
from visa_research_agent.discovery.selection import (
    CandidateSelector,
    LangChainCandidateSelector,
)
from visa_research_agent.discovery.selection_recall import (
    DEFAULT_ORACLE_PATH,
    Arm,
    Grading,
    PoolAudit,
    SelectionOracle,
    arms_from_logs,
    grade,
    load_oracle,
    pool_audit,
    read_recall_logs,
    same_pages,
    unattributed_logs,
)
from visa_research_agent.discovery.supranational import (
    get_supranational_registry,
    refresh_union_store,
    union_of,
    union_pages,
)
from visa_research_agent.domain.models import DestinationConfig, RuntimePolicy
from visa_research_agent.domain.trust import host_is_within
from visa_research_agent.research.errors import LLMConfigurationError, VisaResearchError
from visa_research_agent.research.live_sources import LiveSourceFetcher
from visa_research_agent.research.model_usage import FileModelUsageLog
from visa_research_agent.research.personas import (
    PersonasCandidateSelector,
    PersonasRoleAdjudicator,
    personas_client_from_settings,
)
from visa_research_agent.research.rendering import (
    PageRenderer,
    PlaywrightPageRenderer,
    build_page_renderer,
)
from visa_research_agent.research.source_cache import FileSourceCache


def build_search_provider() -> BraveSearchProvider:
    key = settings.search_api_key.get_secret_value() if settings.search_api_key else ""
    return BraveSearchProvider(key, timeout_seconds=settings.search_timeout_seconds)


def build_role_adjudicator(policy: RuntimePolicy) -> RoleAdjudicator | None:
    """Build the decider the policy asks for, or none when it asks for the heuristic.

    Missing credentials raise rather than silently falling back: `discovery_decider` is a
    committed, reviewed line, so a machine that cannot honour it should say so.
    """

    if policy.discovery_decider == "heuristic":
        return None
    if policy.model_route == "personas":
        return PersonasRoleAdjudicator(
            personas_client_from_settings(),
            timeout_seconds=settings.personas_roles_timeout_seconds,
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise LLMConfigurationError("OPENAI_API_KEY is required for model role adjudication")
    if settings.openai_model is None or not settings.openai_model.strip():
        raise LLMConfigurationError("OPENAI_MODEL is required for model role adjudication")
    return LangChainRoleAdjudicator(
        api_key=settings.openai_api_key.get_secret_value(),
        model_name=settings.openai_model,
        request_timeout_seconds=settings.openai_request_timeout_seconds,
        max_output_tokens=settings.openai_max_output_tokens,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def build_candidate_selector(policy: RuntimePolicy) -> CandidateSelector | None:
    """Build the model that chooses what to read, or none when nothing asks for it.

    Off unless `discovery_selector` says otherwise, so the heuristic shortlist stays the default and
    the regression baseline. DECISIONS entry 83.
    """

    if policy.discovery_selector != "model":
        return None
    if policy.model_route == "personas":
        return PersonasCandidateSelector(
            personas_client_from_settings(),
            timeout_seconds=settings.personas_selection_timeout_seconds,
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise LLMConfigurationError("OPENAI_API_KEY is required for model candidate selection")
    if settings.openai_model is None or not settings.openai_model.strip():
        raise LLMConfigurationError("OPENAI_MODEL is required for model candidate selection")
    return LangChainCandidateSelector(
        api_key=settings.openai_api_key.get_secret_value(),
        model_name=settings.openai_model,
        request_timeout_seconds=settings.openai_request_timeout_seconds,
        max_output_tokens=settings.openai_max_output_tokens,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def build_resolver(
    renderer: PageRenderer | None = None,
    adjudicator: RoleAdjudicator | None = None,
    *,
    corpus: CountryCorpus | None = None,
    pinned: list[str] | None = None,
    always_read: list[tuple[PageLink, str]] | None = None,
) -> CorridorResolver:
    # One renderer for both fetchers, so a corridor starts at most one browser and its render
    # budget is shared between finding pages and reading them.
    crawl_fetcher = CrawlFetcher(
        timeout_seconds=settings.source_fetch_timeout_seconds,
        user_agent=settings.source_user_agent,
        host_delay_seconds=settings.discovery_host_delay_seconds,
        renderer=renderer,
        maximum_renders=settings.maximum_crawl_renders,
    )
    # The reviewed policy's window, as the API's fetcher uses: a corridor run from the command
    # line must serve and refuse the same stale pages a traveller's request would (entry 249).
    policy = get_runtime_policy()
    live_fetcher = LiveSourceFetcher(
        FileSourceCache(settings.cache_directory),
        ttl_hours=policy.source_cache_ttl_hours,
        maximum_stale_hours=policy.source_maximum_stale_hours,
        timeout_seconds=settings.source_fetch_timeout_seconds,
        concurrency=settings.source_fetch_concurrency,
        maximum_characters=settings.maximum_source_characters,
        minimum_characters=settings.minimum_source_characters,
        user_agent=settings.source_user_agent,
        maximum_bytes=settings.maximum_source_bytes,
        renderer=renderer,
        maximum_renders=settings.maximum_source_renders,
    )
    return CorridorResolver(
        build_search_provider(),
        crawl_fetcher,
        live_fetcher,
        adjudicator=adjudicator,
        # On by default in both the command and the API. A recall failure is diagnosable only from
        # the run that had it, and the run that had it is over by the time anyone asks.
        recall_log=FileRecallLog(settings.recall_log_directory),
        # Both the command and the API, like the recall log: a sweep and a web request spend from
        # the same account, and the bill is checked against the sum (entry 166).
        usage_log=FileModelUsageLog(settings.model_usage_directory),
        corpus=corpus,
        # Passed unconditionally, like the recall log and unlike the corpus: this is a directory
        # rather than one country's data, and a country with nothing in it is silently the old
        # behaviour. One line wires both the command and the API, which reaches a resolver only
        # through this function.
        page_text=PageTextStore(settings.page_text_directory),
        selector=build_candidate_selector(get_runtime_policy()),
        pinned=pinned,
        always_read=always_read,
    )


def print_bootstrap(report: BootstrapReport, stream: TextIO) -> None:
    print(f"\nCandidate authority domains for {report.destination_name}\n", file=stream)
    if not report.proposals:
        print("  none survived the checks.", file=stream)
    for proposal in report.proposals:
        if proposal.is_own_government:
            marker = "OWN GOV"
        elif proposal.looks_governmental:
            marker = "gov    "
        else:
            marker = "       "
        print(f"  [{marker}] {proposal.domain}", file=stream)
        print(f"        seen in {proposal.corroboration} queries", file=stream)
        if proposal.looks_governmental and not proposal.belongs_to_destination:
            print(
                f"        WARNING: governmental, but not under {report.destination_name}'s own "
                "domain — this may be another country's advice about this destination",
                file=stream,
            )
        if proposal.suggested_kind:
            print(f"        looks like: {proposal.suggested_kind}", file=stream)
        entry = entry_point_for(proposal)
        if entry:
            print(f"        entry point: {entry}", file=stream)
    if report.rejected:
        print("\n  rejected:", file=stream)
        for domain, reason in sorted(report.rejected.items()):
            print(f"    {domain}: {reason}", file=stream)
    print(
        "\n  Nothing is approved automatically. Add the domains you accept to "
        "trusted_domains in destinations.yaml.\n",
        file=stream,
    )


def print_phase_timings(phases: dict[str, float], stream: TextIO) -> None:
    """Where the seconds went, in the order the pipeline spends them.

    Printed rather than left in the recall log because the log is overwritten per corridor and read
    by almost nobody, and a goal stated in seconds went weeks without a number for exactly that
    reason (entry 142). A phase absent from the map was never entered — a corridor whose corpus
    out-covered a crawl shows no `crawl` line — so nothing is printed as a zero it did not spend.
    """

    if not phases:
        return
    order = ["search", "corpus", "crawl", "select", "fetch", "adjudicate"]
    known = [(phase, phases[phase]) for phase in order if phase in phases]
    known += [(phase, seconds) for phase, seconds in sorted(phases.items()) if phase not in order]
    total = sum(seconds for _, seconds in known)
    print(f"\n  {total:.1f}s in the resolver, by stage:", file=stream)
    for phase, seconds in known:
        share = f"{seconds / total:.0%}" if total else "--"
        print(f"    {phase:<12} {seconds:6.1f}s  {share:>4}", file=stream)


def print_model_calls(calls: list[ModelCall], stream: TextIO) -> None:
    """What each model call was given and what it cost.

    The packet is printed apart from the prompt because only the packet varies with the corridor —
    the system prompt is a file — so a combined figure would hide the variable behind a constant
    (entry 144).
    """

    if not calls:
        return
    print("\n  model calls:", file=stream)
    for call in calls:
        packet = f"{call.packet_characters / 1000:.1f}k"
        prompt = f"{call.prompt_characters / 1000:.1f}k"
        failed = "  FAILED" if call.failed else ""
        tokens = ""
        if call.input_tokens is not None or call.output_tokens is not None:
            cached = f" ({call.cached_input_tokens} cached)" if call.cached_input_tokens else ""
            thinking = (
                f" ({call.reasoning_output_tokens} reasoning)"
                if call.reasoning_output_tokens
                else ""
            )
            tokens = f"   in {call.input_tokens}{cached}  out {call.output_tokens}{thinking}"
        print(
            f"    {call.call:<9} {call.seconds:6.1f}s   packet {packet:>7}"
            f"   prompt {prompt:>6}{tokens}{failed}",
            file=stream,
        )


def print_corridor(resolved: ResolvedCorridor, stream: TextIO) -> None:
    print(f"\nCorridor {resolved.corridor.key}\n", file=stream)
    for source in resolved.sources:
        roles = ", ".join(source.roles)
        print(f"  {roles:<34} {source.score:>6.1f}  {source.url}", file=stream)
        print(f"  {'':<34} {'':>6}  {source.title}", file=stream)
        if source.signals:
            print(f"  {'':<34} {'':>6}  why: {', '.join(source.signals[:4])}", file=stream)
    if resolved.inaccessible_domains:
        print("\n  refused automated retrieval:", file=stream)
        for domain in resolved.inaccessible_domains:
            print(f"    {domain}", file=stream)
        print(
            "    These authorities were not read, so nothing here rests on them. That is not a\n"
            "    judgement about their guidance: it could not be verified from here.",
            file=stream,
        )
    if resolved.interactive_tools:
        print("\n  answered only by an official tool:", file=stream)
        for tool in resolved.interactive_tools:
            print(f"    {tool.role:<22} {tool.url}", file=stream)
        print(
            "    These pages were read. They ask the traveller questions and work the answer out\n"
            "    from them, so nothing here states what they would say.",
            file=stream,
        )
    if resolved.delegated_services:
        print("\n  published by a company the authority contracts with:", file=stream)
        for service in resolved.delegated_services:
            print(f"    {service.role:<22} {service.url}", file=stream)
            print(f"    {'':<22} named on {service.named_on}", file=stream)
        print(
            "    These were not read and nothing here rests on them: they are not government\n"
            "    domains, so this program may name them and may not believe them.",
            file=stream,
        )
    if resolved.unresolved_roles:
        print("\n  could not be identified:", file=stream)
        for role in resolved.unresolved_roles:
            print(f"    {role}", file=stream)
    for note in resolved.notes:
        print(f"  note: {note}", file=stream)
    print(
        f"\n  {resolved.pages_fetched} pages read, {resolved.model_calls} model calls\n",
        file=stream,
    )


async def run_bootstrap(args: argparse.Namespace, stream: TextIO) -> int:
    # Knowing the destination's own top-level domains is what separates its authorities from
    # another government's pages about it.
    country = next(
        (
            item
            for item in get_country_registry().countries
            if item.name.lower() == args.destination_name.strip().lower()
        ),
        None,
    )
    if country is None:
        print(
            f"{args.destination_name} is not in countries.yaml. Add it so its own domains can be "
            "told apart from other governments' pages about it.",
            file=stream,
        )
        return 3

    report = await bootstrap_destination(
        args.destination_name,
        build_search_provider(),
        get_denylist(),
        destination_tlds=country.tlds,
    )
    print_bootstrap(report, stream)
    return 0 if report.proposals else 2


async def run_registry(args: argparse.Namespace, stream: TextIO) -> int:
    """Generate the committed authority registry — the one command that costs search quota.

    Deliberately not something a request can trigger. Four queries per country times 198 countries
    is most of an hour and a real bill, and the whole point of DECISIONS entry 34 is that this
    happens once and is reviewed, rather than on every cold request for a nationality nobody has
    asked about before.
    """

    destination = Path(args.output)
    existing = None
    if destination.exists():
        # Loaded even when rebuilding: `--rebuild` replaces search output, never a person's
        # reviewed domains, and those live in the same rows.
        existing = load_authority_registry(str(destination))
        if args.rebuild:
            print("rebuilding all countries; reviewed domains kept", file=stream)
        else:
            print(
                f"resuming: {len(existing.countries)} countries already in {destination}",
                file=stream,
            )

    countries = get_country_registry()
    if args.only:
        # A subset is a first-class operation, not a shortcut: after a review finds a country's
        # trusted set wrong, rebuilding that one country must not cost 792 searches. It also lets
        # the file be grown deliberately rather than all at once.
        wanted = {code.strip().upper() for code in args.only.split(",") if code.strip()}
        unknown = wanted - {country.code for country in countries.countries}
        if unknown:
            print(f"not in countries.yaml: {', '.join(sorted(unknown))}", file=stream)
            return 3
        countries = CountryRegistry(
            schema_version=1,
            countries=[c for c in countries.countries if c.code in wanted],
        )

    remaining = len(countries.countries)
    if existing and not args.rebuild:
        remaining -= sum(1 for c in countries.countries if existing.get(c.code))
    print(f"{remaining} countries to build, about {remaining * 4} searches\n", file=stream)

    def report(progress: BuildProgress) -> None:
        if progress.error is not None:
            print(f"  {progress.country.code}  FAILED  {progress.error}", file=stream)
            return
        row = progress.row
        assert row is not None
        trusted = ", ".join(row.trusted) if row.trusted else "(none — refused)"
        print(f"  {row.code}  {trusted}", file=stream)
        if row.unconfirmable:
            print(f"      unconfirmable: {', '.join(row.unconfirmable)}", file=stream)

    registry, failures = await build_authority_registry(
        countries,
        build_search_provider(),
        get_denylist(),
        existing=existing,
        rebuild=args.rebuild,
        on_progress=report,
        write=lambda current: write_registry(current, destination),
    )
    write_registry(registry, destination)

    # `domains`, not `trusted` — the same property the resolver reads, so this line reports what a
    # traveller would actually get. Counting `trusted` alone called Belgium, Germany, Denmark and
    # Sweden "refused" while every one of them was researchable on a reviewed domain, and Germany
    # had confirmed the visa decision on 8 of 8 corridors (entry 58). A reviewed row is the whole
    # mechanism for a government that marks no hostname, so the one command that writes those rows
    # was the worst possible place to report them as failures.
    confirmed = sum(1 for row in registry.countries if row.domains)
    print(
        f"\n{len(registry.countries)} countries written to {destination}; "
        f"{confirmed} have a confirmed domain, {len(registry.countries) - confirmed} are refused.",
        file=stream,
    )
    if failures:
        # Named rather than counted: these are not in the file at all, so a later run will retry
        # exactly these, and a reader has to be able to tell them from a country that was refused.
        print(f"{len(failures)} could not be searched and were left out:", file=stream)
        for code, reason in sorted(failures.items()):
            print(f"  {code}  {reason}", file=stream)
        return 2
    return 0


async def run_advisories(args: argparse.Namespace, stream: TextIO) -> int:
    """Survey where each passport country's government publishes travel advice (entry 260).

    Writes a survey for a person to review, never `advisory_publishers.yaml`. Costs three searches a
    country and resumes from what the file already holds.
    """

    destination = Path(args.output)
    existing = load_advisory_survey(destination)
    countries = get_country_registry()
    if args.only:
        wanted = {code.strip().upper() for code in args.only.split(",") if code.strip()}
        unknown = wanted - {country.code for country in countries.countries}
        if unknown:
            print(f"not in countries.yaml: {', '.join(sorted(unknown))}", file=stream)
            return 3
        countries = CountryRegistry(
            schema_version=1, countries=[c for c in countries.countries if c.code in wanted]
        )
    remaining = sum(1 for c in countries.countries if not (existing and existing.get(c.code)))
    print(f"{remaining} countries to survey, about {remaining * 3} searches\n", file=stream)

    def report(progress: SurveyProgress) -> None:
        if progress.error is not None:
            print(f"  {progress.country.code}  FAILED  {progress.error}", file=stream)
            return
        row = progress.row
        assert row is not None
        own = ", ".join(c.domain for c in row.own_government) or "(none of its own)"
        print(f"  {row.code}  {own}", file=stream)
        if row.unconfirmable:
            print(
                f"      unconfirmable: {', '.join(c.domain for c in row.unconfirmable)}",
                file=stream,
            )

    survey, failures = await build_advisory_survey(
        countries,
        build_search_provider(),
        get_denylist(),
        existing=existing,
        on_progress=report,
        write=lambda current: write_advisory_survey(current, destination),
    )
    write_advisory_survey(survey, destination)
    found = sum(1 for row in survey.countries if row.own_government or row.unconfirmable)
    print(
        f"\n{len(survey.countries)} countries in {destination}; {found} with a candidate of their "
        f"own government's, {len(survey.countries) - found} with none found.",
        file=stream,
    )
    if failures:
        print(f"{len(failures)} could not be searched and were left out:", file=stream)
        for code, reason in sorted(failures.items()):
            print(f"  {code}  {reason}", file=stream)
        return 2
    return 0


def corridor_destination(slug: str, corridor: Corridor, stream: TextIO) -> DestinationConfig | None:
    """Which config a corridor resolves against: the configured one, else the committed registry.

    **Falls back rather than refusing**, which it did not until 2026-08-22. `destinations.yaml`
    holds seven destinations and `authority_domains.yaml` holds forty, so `--destination canada`
    answered *"Unknown destination: canada"* while the API resolved that same corridor perfectly
    well through `AutomaticDestinationService`. Every live check of a registry corridor had
    therefore to be run from a throwaway script — which is why nobody had a candidate list until
    entry 43, and why nobody has yet counted how often a corridor flips (TODO item 17).

    **A configured destination wins only under `destination_mode: configured`**, because that is
    the only mode in which the web app serves one (entry 149). Under `automatic` the API researches
    Singapore and Japan like any other country, so resolving them here against their hand-written
    domains measured a path no traveller was served — which every Singapore and Japan corridor run
    before 2026-09-14 did. Singapore's VFS provider, named only in `destinations.yaml`, goes with
    it; entry 89's delegate naming is the automatic path's equivalent, and it names, never reads.

    Deliberately **not** the automatic service, and so deliberately not the corridor store either.
    A stored corridor would answer the second and third runs from the first, which is precisely the
    variance being measured.
    """

    configured = get_destination_registry().get(slug)
    if (
        get_runtime_policy().destination_mode == "configured"
        and configured is not None
        and configured.trusted_domains
    ):
        return configured
    try:
        return prepare_destination(slug, corridor).config
    except AutomaticDiscoveryError as exc:
        print(str(exc), file=stream)
        return None


def print_variance(report: VarianceReport, stream: TextIO) -> None:
    """What several runs of one corridor disagreed about, worst first."""

    print(f"\n{report.runs} runs: {', '.join(report.outcomes)}", file=stream)
    if not report.comparison_is_complete:
        # Said before the numbers, not after: without a record from every run, an absence below
        # means "no record" rather than "this run did not find it", and those read identically.
        print(
            f"  only {report.records_read} of {report.runs} runs left a recall record, so the "
            "candidate comparison below is incomplete.",
            file=stream,
        )
    if not report.flipped:
        print("  every run reached the same outcome.", file=stream)
    else:
        print(
            f"  **the corridor flipped** — {report.resolved_runs} of {report.runs} resolved. "
            "A stored corridor would have kept whichever came first for a week.",
            file=stream,
        )
    print(
        f"  {report.stable} candidates seen by every run, {len(report.unstable)} by only some.",
        file=stream,
    )
    changed = [item for item in report.unstable if item.reached_the_model]
    if not report.unstable:
        # Kept apart from the branch below, because the two are different facts and the first
        # printing of this conflated them: with nothing unstable at all it announced "the variance
        # is real but did not reach the decider", which was false of a run where every candidate set
        # was identical. A reason has to be true of what was seen — entries 33 and 36.
        print(
            "  every run saw exactly the same candidate set, so nothing varied at this level.\n"
            "  Note what this does and does not cover: it is search and crawl recall only. The\n"
            "  adjudication is a model call, so identical candidates can still be judged\n"
            "  differently between runs (known problem 10), and that is not measured here.",
            file=stream,
        )
        return
    if not changed:
        print(
            "  none of the varying candidates was ever fetched, so none of them could have\n"
            "  changed the answer. The variance is real but did not reach the decider.",
            file=stream,
        )
        return
    print("\n  varying candidates that at least one run actually read:", file=stream)
    for item in changed:
        seen = ",".join(str(run) for run in item.runs_seen)
        fetched = ",".join(str(run) for run in item.runs_fetched)
        print(f"    {item.best_score:>6.1f}  {item.url}", file=stream)
        print(f"            seen in runs {seen}; read in runs {fetched}", file=stream)


def print_reachability(report: Reachability, stream: TextIO) -> None:
    """How much of the world can be researched, and what stands in the way of the rest."""

    print(
        f"\nReachability, from committed data — {report.countries} countries offered", file=stream
    )
    # Each row names the key its cost is looked up under, rather than the cost itself, so the
    # answer to "is this the price of rigor" has one home and cannot drift between the two halves
    # of this report. `researchable` has no key because it cost nothing — it is the successes.
    rows = (
        ("researchable", len(report.researchable), ""),
        ("row, no confirmable domain", len(report.row_without_domain), "row_without_domain"),
        ("no registry row at all", len(report.no_row), "no_row"),
    )
    for label, count, key in rows:
        cost = POSTURE_COST.get(key, "—")
        share = 100.0 * count / report.countries if report.countries else 0.0
        print(f"  {label:<28} {count:>4}  {share:>5.1f}%  {cost}", file=stream)
    print(
        f"  {report.refused} of {report.countries} are refused before any page is fetched. "
        "None of them leaves a recall log,\n  so they cannot appear in the causes below — which is "
        "why the two halves are counted apart.",
        file=stream,
    )
    if report.unconfirmable_candidates:
        total = sum(report.unconfirmable_candidates.values())
        named = ", ".join(
            f"{code} ({count})" for code, count in report.unconfirmable_candidates.items()
        )
        plural = "domain" if total == 1 else "domains"
        print(
            f"\n  {len(report.unconfirmable_candidates)} of the refused countries had "
            f"{total} candidate {plural} the rule declined: {named}.\n"
            "  Those are the ones with something a reviewer could promote by hand; the rest "
            "found nothing at all.",
            file=stream,
        )


def print_recall_audit(report: RecallAudit, stream: TextIO) -> None:
    """What the runs on disk actually did, bucketed by a cause each of them recorded."""

    print(f"\nOutcomes, over {report.records} recorded runs", file=stream)
    if not report.records:
        print("  no recall logs found. Run a corridor first.", file=stream)
        return
    for cause, count in counted(report.causes, CAUSE_ORDER):
        if cause == "not recorded":
            continue
        label = CAUSE_LABELS.get(cause, cause)
        cost = POSTURE_COST.get(cause, "")
        print(f"  {label:<52} {count:>4}  {cost}", file=stream)
    if report.unrecorded:
        # Said as its own paragraph rather than as a row, because it is not a bucket — it is the
        # absence of bucketing, and a reader skimming a column of counts would read it as one.
        print(
            f"\n  {report.unrecorded} of {report.records} runs predate the cause field and are "
            "not bucketed above.\n  They cannot be repaired by reading their outcome line: a "
            "corridor that refused for want of\n  a visa decision and one that resolved by handing "
            'over the questionnaire stating it both\n  wrote "resolved, with no visa_decision", '
            "and nothing else in the record separates them.\n  Re-run them to fill this in.",
            file=stream,
        )
    if report.unresolved_roles:
        print("\n  roles left unfilled, counted across the same runs:", file=stream)
        for role, count in sorted(report.unresolved_roles.items(), key=lambda i: (-i[1], i[0])):
            print(f"    {role:<50} {count:>4}", file=stream)
    print("\n  pages that could not be read:", file=stream)
    if not report.unreadable:
        print(
            "    none recorded. Note what that does and does not mean: until 2026-08-24 this\n"
            "    field was filled from the crawl alone, and the crawl left the request path\n"
            "    (entry 51), so a run older than that records nothing here however many\n"
            "    authorities refused it.",
            file=stream,
        )
        return
    for outcome, count in sorted(report.unreadable.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {outcome:<50} {count:>4}  {POSTURE_COST.get(outcome, '')}", file=stream)
    for host, count in sorted(report.unreadable_hosts.items(), key=lambda i: (-i[1], i[0]))[:8]:
        print(f"      {host:<48} {count:>4}", file=stream)


def run_audit(args: argparse.Namespace, stream: TextIO) -> int:
    """Both halves of the question, always. Exit code reports whether anything went unanswered.

    The reachability half is printed even when there are no logs, because it is the larger number
    and it needs no runs — a reader who has never run a corridor should still learn that most of
    the world is refused before a page is fetched.
    """

    print_reachability(reachability(get_authority_registry(), get_country_registry()), stream)
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"\nNo recall logs at {directory}; the outcome half needs runs.", file=stream)
        return 1
    report = audit_records(read_records(directory))
    print_recall_audit(report, stream)
    refused = sum(
        count
        for cause, count in report.causes.items()
        if cause in {"decision_not_found", "no_candidates", "adjudication_failed", "run_raised"}
    )
    return 1 if refused or report.unrecorded else 0


def run_regions(args: argparse.Namespace, stream: TextIO) -> int:
    """Write the committed region list from a downloaded GeoNames file (TODO item 71, entry 252)."""

    codes = [country.code for country in get_country_registry().countries]
    with Path(args.geonames).open(encoding="utf-8") as handle:
        regions = regions_from_geonames(handle, codes)
    Path(args.output).write_text(render_regions_yaml(regions), encoding="utf-8")
    without = sorted(set(codes) - set(regions))
    print(
        f"{sum(map(len, regions.values()))} regions for {len(regions)} of {len(codes)} countries "
        f"written to {args.output}; none for {', '.join(without) or 'none'}.",
        file=stream,
    )
    return 0


def run_reports(args: argparse.Namespace, stream: TextIO) -> int:
    """List travellers' reports newest first, each with the line that re-runs it (TODO item 74)."""

    # Imported here: the API depends on this module, never the other way round.
    from visa_research_agent.api.reports import FileReportStore

    reports = FileReportStore(Path(args.directory)).load_all()
    if args.show:
        chosen = [report for report in reports if report.report_id == args.show]
        if not chosen:
            print(f"No report {args.show} in {args.directory}.", file=stream)
            return 1
        print(chosen[0].model_dump_json(indent=2), file=stream)
        return 0
    if not reports:
        print(f"No reports in {args.directory}.", file=stream)
        return 0
    for report in reports:
        corridor = report.corridor
        what = report.cause or report.outcome
        print(
            f"{report.received_at:%Y-%m-%d %H:%M}Z  {report.report_id}  {corridor.destination} "
            f"{corridor.passport_nationality}/{corridor.applying_from} {corridor.purpose}  {what}"
            f"{'' if report.recall_log else '  (no run log)'}",
            file=stream,
        )
        if report.message:
            first_line = report.message.splitlines()[0]
            print(f'    "{first_line[:100]}"', file=stream)
        rerun = report.rerun_command()
        if rerun:
            print(f"    {rerun}", file=stream)
    return 0


def print_selection_recall(grading: Grading, stream: TextIO) -> None:
    """Both numbers, always, and the independent one first.

    Entry 86 acknowledged that its oracle was built by the two arms it was grading and could not do
    anything about it from its own data. Printing the joint column beside the independent one is how
    that acknowledgement stops being a sentence: the gap between the two columns is the bias, in
    points, on every run of this command.
    """

    print(f"\nSelection recall over {len(grading.graded)} corridors", file=stream)
    if grading.unattributed:
        print(
            f"\n  {len(grading.unattributed)} corridor(s) have a log that does not say which\n"
            "  selector fetched its pages, so no arm can be replayed. A log written before\n"
            "  `RecallRecord.selector` existed cannot be told from a heuristic run, and\n"
            "  grading one as the model puts the heuristic in the model's own arm (entry 91).\n"
            "  Re-run these corridors to grade them:\n"
            f"  {', '.join(grading.unattributed)}",
            file=stream,
        )
    if not grading.graded:
        print(
            "\n  Nothing could be graded. Every arm here is replayed from a recall log, so a\n"
            "  corridor has to have been run by a model-selector build before it can be scored.",
            file=stream,
        )
        return
    print(
        f"\n  {'arm':<26} {'roles':>9} {'':>5}   {'same page':>9} {'':>5}   {'joint':>7} {'':>5}"
        f"   {'read':>5}  {'tools':>6}",
        file=stream,
    )
    for arm in grading.arms:
        print(
            f"  {arm.name:<26} {f'{arm.roles_hit}/{arm.roles_total}':>9} "
            f"{arm.role_recall:>5.0%}   {f'{arm.roles_hit_same_page}/{arm.roles_total}':>9} "
            f"{arm.same_page_recall:>5.0%}   {f'{arm.joint_hit}/{arm.joint_total}':>7} "
            f"{arm.joint_recall:>5.0%}   {arm.pages_read:>5}  "
            f"{f'{arm.tools_hit}/{arm.tools_total}':>6}",
            file=stream,
        )
    print(
        "\n  roles: pages that answer a role, in an oracle neither selector helped build.\n"
        "  same page: the same, also crediting an answering page picked at another address —\n"
        "  identical stored text, or a link whose `redirect` names it (entry 194).\n"
        "  joint: the pages entries 85 and 86 graded against, which both arms did help build.\n"
        "  tools: an official questionnaire holding a role's answer. Naming one never fills it.\n"
        "\n  This grades a selector against a selector: it counts agreement with the pages a\n"
        "  person named, NOT whether the corridor got its answer. The two come apart in both\n"
        "  directions — `united-kingdom/PH/PH` reads 2 of 5 here and filled every role, while\n"
        "  `united-arab-emirates/PH/PH` reads 6 of 6 and left the checklist unfilled. For what a\n"
        "  corridor actually lacks, read `unresolved_roles` in its recall log (entry 99).",
        file=stream,
    )
    if any(arm.pooled_total or arm.outside_total for arm in grading.arms):
        print(
            f"\n  Role recall split by whether the gate admits the answer at all:\n"
            f"\n  {'arm':<26} {'answer pooled':>16} {'':>5}   {'answer outside':>16} {'':>5}",
            file=stream,
        )
        for arm in grading.arms:
            outside = f"{arm.outside_hit}/{arm.outside_total}" if arm.outside_total else "—"
            share = f"{arm.outside_recall:.0%}" if arm.outside_total else ""
            print(
                f"  {arm.name:<26} {f'{arm.pooled_hit}/{arm.pooled_total}':>16} "
                f"{arm.pooled_recall:>5.0%}   {outside:>16} {share:>5}",
                file=stream,
            )
        first = grading.arms[0]
        print(
            "\n  A role whose every answer is outside the pool cannot be filled by ANY arm,\n"
            "  because all of them pick out of the pool. Rolling the two columns together\n"
            "  reports a gate failure as a selector failure, which is what this command did\n"
            f"  until entry 127. {first.absent_total} role(s) are held out of both columns: "
            "their answers are not\n  in the corpus at all, which is item 35's gap and says "
            "nothing about the gate.",
            file=stream,
        )
    for arm in grading.arms:
        if arm.unverifiable_read:
            print(
                f"  {arm.name} also read {arm.unverifiable_read} page(s) nobody could judge; "
                "they are neither credited nor counted against it.",
                file=stream,
            )
    for name, rows in grading.rows.items():
        print(f"\n  {name}, by corridor:", file=stream)
        for row in rows:
            print(
                f"    {row.corridor:<38} {row.roles_hit}/{row.roles_total} roles   "
                f"{row.joint_hit}/{row.joint_total} joint   {row.picks} read",
                file=stream,
            )
    if grading.skipped:
        print(
            f"\n  {len(grading.skipped)} corridor(s) in the oracle had no recall log written by a "
            "model-selector\n  run, so no arm could be replayed for them: "
            f"{', '.join(grading.skipped)}",
            file=stream,
        )


def print_pool_audit(audits: list[PoolAudit], stream: TextIO) -> None:
    """How many of the pages a curator named the selector is never shown.

    **The one number in this command that grades the gate rather than the selector.** Every arm
    replays what a run chose *out of the pool*, so a page the gate excluded is invisible to all of
    them at once and reads as though no selector could have found it. The pool here is the one the
    resolver shows: the link test plus what stored text puts back (entry 158). A non-zero `outside`
    is an answer the selector cannot be shown even so.
    """

    outside = sum(len(audit.outside) for audit in audits)
    absent = sum(len(audit.absent) for audit in audits)
    answers = sum(audit.answers for audit in audits)
    whole = [audit for audit in audits if audit.curated_from == "whole_corpus"]
    print(
        f"\n  Pool audit over {len(audits)} corridor(s): of {answers} answering pages, "
        f"{outside} are outside\n  the pool the selector is shown and {absent} are not in the "
        f"corpus at all.",
        file=stream,
    )
    for audit in audits:
        if audit.outside or audit.absent:
            print(
                f"    {audit.corridor:<38} {len(audit.outside)} outside, "
                f"{len(audit.absent)} absent  [{audit.curated_from}]",
                file=stream,
            )
            for url in (*audit.outside, *audit.absent):
                print(f"      {url}", file=stream)
    if not whole:
        print(
            "\n  Every row here was curated from the pool itself (`curated_from: pool`), so a\n"
            "  zero above is a **tautology, not a result** — the fixture cannot name a page the\n"
            "  filter it shares had already removed (entry 123). Curate a row with\n"
            "  `contention --outside-pool` and mark it `curated_from: whole_corpus` before\n"
            "  reading anything into this line.",
            file=stream,
        )
    elif not outside:
        print(
            f"\n  {len(whole)} row(s) were curated from the whole corpus and still name no page\n"
            "  outside the pool. That is a real answer: the gate, with what stored text puts\n"
            "  back, hides none of their answers. It is bounded by what the text index holds.",
            file=stream,
        )


def run_selection_recall(args: argparse.Namespace, stream: TextIO) -> int:
    """Grade what was chosen to read. Reads two directories and calls nothing."""

    oracle = load_oracle(Path(args.oracle))
    directory = Path(args.recall)
    if not directory.is_dir():
        print(f"No recall logs at {directory}; there is nothing to grade.", file=stream)
        return 1
    logs = read_recall_logs(directory)
    arms = arms_from_logs(oracle, logs, full_size=args.shipped_size)
    contentions = contentions_for(oracle, stream)
    grading = grade(
        oracle,
        arms,
        unattributed=unattributed_logs(oracle, logs),
        contentions=contentions,
        same_page=same_page_for(oracle, arms),
    )
    print_selection_recall(grading, stream)
    audits = [
        pool_audit(row, contentions[row.corridor])
        for row in oracle.corridors
        if row.corridor in contentions
    ]
    if audits:
        print_pool_audit(audits, stream)
    return 0 if grading.graded else 1


def same_page_for(
    oracle: SelectionOracle, arms: Mapping[str, Sequence[Arm]]
) -> dict[str, dict[str, frozenset[str]]]:
    """Per graded corridor, which of its picks and answers are one page at several addresses.

    Read from the page-text index, offline like the rest of the command. A corridor whose country
    has no stored text still gets the redirect test, which needs none.
    """

    countries = get_country_registry()
    page_text = PageTextStore(settings.page_text_directory)
    found: dict[str, dict[str, frozenset[str]]] = {}
    for row in oracle.corridors:
        corridor_arms = arms.get(row.corridor)
        if not corridor_arms:
            continue
        urls = {url for arm in corridor_arms for url in arm.picks}
        urls |= {url for role in row.answers for url in row.answering_urls(role)}
        country = countries.by_slug(row.slug)
        texts = page_text.text_for_selection(country.code, urls) if country is not None else {}
        found[row.corridor] = same_pages(urls, texts, oracle.mirrors)
    return found


def contentions_for(oracle: SelectionOracle, stream: TextIO) -> dict[str, Contention]:
    """Rebuild each row's contention set from the store, to split its answers by the pool gate.

    Offline like the rest of the command: `contention_for` takes no fetcher and no search provider.
    A corridor whose country has no corpus is left out rather than guessed at, because "not in the
    corpus" and "no corpus to look in" are the two things `PoolAudit.absent` exists to keep apart.
    """

    countries = get_country_registry()
    corpora = FileCorpusStore(settings.corpus_directory)
    lexicon = get_lexicon()
    # The same admission the resolver applies, so the audit grades the gate the product has rather
    # than the link-only one it replaced (entry 158).
    page_text = PageTextStore(settings.page_text_directory)
    built: dict[str, Contention] = {}
    for row in oracle.corridors:
        slug, nationality, residence, purpose = row.corridor.split("/")
        country = countries.by_slug(slug)
        if country is None:
            continue
        corpus = corpora.load(country.code)
        if corpus is None:
            continue
        corridor = Corridor(
            destination_slug=slug,
            passport_nationality=nationality,
            applying_from=residence,
            purpose=purpose,  # type: ignore[arg-type]
        )
        config = corridor_destination(slug, corridor, stream)
        if config is None:
            continue
        built[row.corridor] = contention_for(
            corpus,
            config,
            corridor,
            countries=countries,
            lexicon=lexicon,
            destination_code=country.code,
            page_text=page_text,
        )
    return built


# One cold resolution of a corridor. Named so `run_corridor` can take a fake in tests.
Resolve = Callable[[DestinationConfig, Corridor, RuntimePolicy], Awaitable[ResolvedCorridor]]


def corpus_for(destination: DestinationConfig) -> CountryCorpus | None:
    """The stored page corpus for whatever country this destination is, or none.

    The command did not read one until 2026-08-23, so every measurement taken through it described
    a pipeline the product had already stopped being — no corpus meant no corpus candidates and,
    now, an unconditional crawl. [TODO.md](TODO.md) item 22.

    A corpus that cannot be read **raises**, exactly as it does in the request path: it is the
    candidate source, and treating an unreadable file as "this country has no pages" would turn a
    corrupt corpus into a quietly worse run.
    """

    country = get_country_registry().by_slug(destination.slug)
    if country is None:
        return None
    return FileCorpusStore(settings.corpus_directory).load(country.code)


async def resolve_once(
    destination: DestinationConfig, corridor: Corridor, policy: RuntimePolicy
) -> ResolvedCorridor:
    """One cold resolution, with the browser closed afterwards whatever happened.

    The corpus is passed and the **pins are not**, and the difference is what each one depends on.
    A corpus is corridor-independent, so reading it makes this command behave as the product does.
    Pins come from the stored resolution of *this* corridor, so passing them would let run one
    decide part of run two's shortlist — which is the variance `--runs` exists to measure.
    """

    renderer = build_page_renderer(policy)
    adjudicator = build_role_adjudicator(policy)
    try:
        resolver = build_resolver(
            renderer,
            adjudicator,
            corpus=corpus_for(destination),
            always_read=union_pages(
                union_of(destination), FileCorpusStore(settings.corpus_directory)
            ),
        )
        return await resolver.resolve(destination, corridor)
    finally:
        # The browser outlives the resolver, so closing it is this function's job.
        if isinstance(renderer, PlaywrightPageRenderer):
            await renderer.aclose()


async def run_corridor(
    args: argparse.Namespace,
    stream: TextIO,
    *,
    resolve: Resolve = resolve_once,
) -> int:
    """Resolve one corridor, or the same corridor several times to see what varies.

    `resolve` is a seam, in the sense `AGENTS.md` means it — the same role `transport=` and `now=`
    play elsewhere. It exists because this function had none: it built its own resolver from global
    settings and went straight to the network, so a test could only avoid contacting real
    authorities by relying on the command bailing out early for some other reason. One did, and when
    the early exit went away on 2026-08-22 the test suite spent 21 seconds making live Brave
    searches and a live model call. `tests/conftest.py` now blocks the socket as well, so the
    convention and the seam back each other up.
    """

    # Every argument is mapped to a country before a corridor is built, and the corridor is keyed on
    # the destination's slug rather than on the argument as written, as the API is (entry 168).
    # "united states" and a passport given as "India" raised a validation error; "usa" fitted the
    # pattern but ran under a slug `corpus_for` finds no country for, so without its corpus.
    countries = get_country_registry()
    country = find_country(args.destination, countries)
    if country is None:
        print(str(unknown_country(args.destination)), file=stream)
        return 3
    nationality = _country(countries, args.nationality)
    if nationality is None:
        print(f"--nationality {args.nationality} is not a known ISO code, e.g. IN.", file=stream)
        return 3
    residence = _country(countries, getattr(args, "from"))
    if residence is None:
        print(f"--from {getattr(args, 'from')} is not a known ISO code, e.g. GB.", file=stream)
        return 3
    corridor = Corridor(
        destination_slug=country.slug,
        passport_nationality=nationality.code,
        applying_from=residence.code,
        purpose=args.purpose,
    )
    destination = corridor_destination(country.slug, corridor, stream)
    if destination is None:
        return 3

    policy = get_runtime_policy()
    runs = max(1, int(getattr(args, "runs", 1)))
    log = FileRecallLog(settings.recall_log_directory)
    records: list[RecallRecord] = []
    outcomes: list[str] = []
    resolved: ResolvedCorridor | None = None

    timings: dict[str, float] = {}
    model_calls: list[ModelCall] = []
    for attempt in range(1, runs + 1):
        started = datetime.now(UTC)
        resolved = await resolve(destination, corridor, policy)
        if runs == 1:
            # Read back from the record this run just wrote, rather than from the resolver: the
            # resolver is built inside `resolve` and never returned, and the log is the thing that
            # has to be right anyway.
            written = log.read(corridor)
            if written is not None and written.recorded_at >= started:
                timings = written.phase_seconds
                model_calls = written.model_calls
            continue
        outcomes.append("resolved" if resolved.is_usable else "refused")
        filled = ", ".join(sorted({role for s in resolved.sources for role in s.roles})) or "—"
        print(
            f"  run {attempt}: {outcomes[-1]:<8}  {resolved.pages_fetched:>3} read  "
            f"roles: {filled}",
            file=stream,
        )
        # Read back before the next run overwrites it. The log keeps only the newest run per
        # corridor (entry 43), so comparing runs means holding each record as it is produced.
        #
        # `recorded_at` is checked because the file may predate this command entirely: running two
        # corridors a week apart leaves a week-old record sitting exactly where this looks, and
        # comparing run 2 against last week's run 1 would invent variance that never happened.
        record = log.read(corridor)
        if record is not None and record.recorded_at >= started:
            records.append(record)

    assert resolved is not None
    if runs > 1:
        print_variance(compare_runs(outcomes, records), stream)
    else:
        print_corridor(resolved, stream)
        print_phase_timings(timings, stream)
        print_model_calls(model_calls, stream)

    if args.format == "yaml":
        print(render_corridor_yaml(resolved))

    if not resolved.is_usable:
        return 2
    return 1 if resolved.unresolved_roles else 0


def print_corpus_build(build: CorpusBuild, stream: TextIO) -> None:
    print(
        f"  {build.country_code}  {build.queries} queries, {build.seeds} seeds"
        + (f" ({build.mission_seeds} from its own mission index)" if build.mission_seeds else "")
        + (
            f", {build.retry_seeds} asked again after a transient failure"
            if build.retry_seeds
            else ""
        )
        + (f", {build.seeds_kept} kept that nothing linked to" if build.seeds_kept else "")
        + f", {build.crawled} crawled  ->  {build.added} new, {build.total} held"
        + (f", {build.unreadable} unreadable" if build.unreadable else ""),
        file=stream,
    )
    if build.failed_queries:
        # Named, because a partly-searched build is still a good build and a person reading it
        # should know which part is missing rather than find out from a gap weeks later.
        print(
            f"      {len(build.failed_queries)} of {build.queries} search queries failed and the "
            "build went on without them:",
            file=stream,
        )
        for query, reason in sorted(build.failed_queries.items()):
            print(f"        {query}: {reason}", file=stream)
    depths = ", ".join(f"depth {d}: {n}" for d, n in sorted(build.by_depth.items()))
    print(f"      {depths}", file=stream)
    # Where the page allowance went. Entry 185 needed a patched build to learn this.
    print(
        f"      opened {build.opened_seeds} seeds, {build.opened_scored} scored links, "
        f"{build.opened_unscored} links that scored nothing, {build.opened_family} family members"
        + (
            f"; {build.opened_over_share} past a host's even share"
            if build.opened_over_share
            else ""
        )
        + (
            f"; {build.dropped_scored} scored links turned away at a host's cap"
            if build.dropped_scored
            else ""
        ),
        file=stream,
    )
    if build.indexed_text:
        print(
            f"      {build.indexed_text} pages kept their text for the index"
            + (f", including {build.pdfs_read} PDFs read for text only" if build.pdfs_read else ""),
            file=stream,
        )
    if build.delegated:
        print(
            f"      {build.delegated} places its own pages send travellers, on companies we may "
            "name but never read",
            file=stream,
        )
    if build.rejected:
        # What the rules threw away, and how much of it mentions visas — the line that would have
        # shown Malta's lists being vetoed as archived (entry 200).
        stream.write(f"      {sum(build.rejected.values())} links rejected by rule:\n")
        for reason, count in sorted(build.rejected.items(), key=lambda item: -item[1]):
            visas = build.rejected_about_visas.get(reason, 0)
            stream.write(
                f"        {count:>6}  {reason}"
                + (f"  ({visas} with 'visa' in the address)" if visas else "")
                + "\n"
            )
            for url in build.rejected_examples.get(reason, []):
                stream.write(f"                  {url}\n")
    if build.abandoned_hosts:
        # Named rather than counted, and separately from `lost_hosts`: a host given up on may still
        # be the biggest in the corpus, and what it was still holding is what this costs.
        print(
            f"      {len(build.abandoned_hosts)} hosts stopped answering and were not asked again:",
            file=stream,
        )
        for host, failures in sorted(build.abandoned_hosts.items()):
            print(f"        {host:<42} after {failures} unanswered requests in a row", file=stream)
    if build.lost_hosts:
        # Named, not counted. A host that contributed nothing leaves at most an unreadable seed —
        # before entry 161 not even that — so without this the gap was invisible, and a corpus only
        # ever grows, which makes it permanent. DECISIONS entry 77.
        print(
            f"      {len(build.lost_hosts)} hosts gave this build nothing and are absent from the "
            "corpus:",
            file=stream,
        )
        for host, reason in sorted(build.lost_hosts.items()):
            outcome = build.lost_host_outcomes.get(host, "unknown")
            print(f"        {host:<42} {outcome:<12} {reason}", file=stream)
    if not build.depth_is_exercised:
        # The one thing this job exists to do better than the request path. Said out loud, because
        # on 2026-08-22 it silently did not happen: 203 seeds against a 200-page budget meant the
        # whole allowance went on seeds, and nothing in the output showed it.
        # Two different failures wear the same shallow shape, and only one is a budget. The
        # Philippines stopped at depth 1 having spent 425 of 1,200 pages: telling anyone to raise
        # --pages there sends them to change the one number that was never the constraint
        # (entry 115).
        remedy = (
            "Raise --pages well above the seed count."
            if build.budget_was_spent
            else (
                f"It spent {build.crawled} of {build.page_budget} pages, so the budget was not "
                "the limit — the frontier ran dry. Read the lost hosts and the unreadable count "
                "above."
            )
        )
        print(
            f"      only {build.deep_share:.0%} of what it found lies beyond depth 1 — this crawl "
            "fetched its seeds and stopped, which is the request path's behaviour, not this job's. "
            + remedy,
            file=stream,
        )


def country_of_host_from_registry() -> Callable[[str], str | None]:
    """Which country's authority a host belongs to, or none.

    The live registry rather than whatever was true when the page was cached, exactly as
    `CountryCorpus.entries_within` applies trust at read time. A host under no country's approved
    domains maps to nothing and is skipped: guessing here would put a page into a candidate set
    that the trust rules had already refused.
    """

    registry = get_authority_registry()
    pairs = [(domain, country.code) for country in registry.countries for domain in country.domains]

    def country_of(host: str) -> str | None:
        return next((code for domain, code in pairs if host_is_within(host, [domain])), None)

    return country_of


def print_backfill(report: BackfillReport, stream: TextIO) -> None:
    print(f"  indexed {report.total} pages across {len(report.indexed)} countries", file=stream)
    for code, count in sorted(report.indexed.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {code}  {count}", file=stream)
    # Said even when zero. An empty index and an empty cache are different situations with
    # different fixes, and a bare page count cannot tell them apart.
    print(
        f"  skipped: {report.skipped_unmapped} on no trusted domain, "
        f"{report.skipped_short} too short to rank, {report.unreadable} unreadable",
        file=stream,
    )


def _country(registry: CountryRegistry, code: str) -> Country | None:
    wanted = code.strip().upper()
    return next((c for c in registry.countries if c.code == wanted), None) if wanted else None


def run_page_text(args: argparse.Namespace, stream: TextIO) -> int:
    """Backfill or query the page-text index (`discovery/page_text.py`).

    Backfilling costs no fetch and no search: the retrieval cache already holds the text of every
    page a corridor has read, and this only stops throwing it away.
    """

    store = PageTextStore(settings.page_text_directory)
    if args.purge_interstitials:
        # Repairs what entry 117's detector fix can only stop happening again: `write` replaces a
        # URL it re-reads, and a page now correctly recorded `challenged` is never written, so
        # every body stored before the fix stays until something removes it.
        codes = [args.country.upper()] if args.country else store.countries()
        total = 0
        for code in codes:
            removed = store.purge_retrieval_interstitials(code)
            total += removed
            if removed:
                print(f"  {code}: removed {removed}", file=stream)
        print(
            f"\n{total} stored bodies were a bot-check page rather than the authority's own, "
            "and are gone. The pages stay in the corpus as candidates; only their ranking text "
            "was wrong.",
            file=stream,
        )
        return 0
    if args.backfill:
        report = backfill_from_cache(
            store, settings.cache_directory, country_of_host=country_of_host_from_registry()
        )
        print_backfill(report, stream)
        return 0

    countries = get_country_registry()
    code = args.country.upper()
    if not store.has(code):
        print(f"There is no page-text index for {code}. Run --backfill first.", file=stream)
        return 3
    nationality = _country(countries, args.nationality)
    destination = _country(countries, code)
    if nationality is None or destination is None:
        print("--nationality and --country must both be known ISO codes.", file=stream)
        return 3
    corridor = Corridor(
        destination_slug=destination.slug,
        passport_nationality=nationality.code,
        applying_from=(_country(countries, getattr(args, "from")) or nationality).code,
        purpose=args.purpose,
    )
    matches = store.rank(
        code,
        role=args.role,
        corridor=corridor,
        nationality=nationality,
        lexicon=get_lexicon(),
        limit=args.limit,
    )
    print(
        f"  {code}: {store.count(code)} pages indexed, {len(matches)} matched {args.role}",
        file=stream,
    )
    for position, match in enumerate(matches, start=1):
        print(f"  {position:>2}. {match.score:>7.1f}  {match.url}", file=stream)
        print(f"        {' '.join(match.signals)}", file=stream)
    return 0


def print_coverage(report: CoverageReport, stream: TextIO) -> None:
    """Both halves, always, and never added together.

    The known-answer half is printed first and labelled for what it is — a regression check over one
    traveller. Printing it alone is how "the corpus is ready" got claimed on a 100% that could not
    see the dimension it was being claimed about (known problem 33), so it is never printed without
    the family half beside it, even for a country that has no families.
    """

    print("\nCorpus coverage, half 1: the answers a human named, per traveller", file=stream)
    known = [row for country in report.countries for row in country.known]
    ungraded = [country.code for country in report.countries if country.verdict == "ungraded"]
    if not known:
        print(
            "  no country asked about appears in the oracle, so this half graded nothing.",
            file=stream,
        )
    else:
        by_traveller: dict[str, list[KnownAnswer]] = {}
        for row in known:
            by_traveller.setdefault(row.traveller, []).append(row)
        for traveller, rows in sorted(by_traveller.items()):
            held = sum(row.held for row in rows)
            answerable = sum(row.answerable for row in rows)
            settled = sum(len(row.settled) for row in rows)
            absent = sum(len(row.not_applicable) for row in rows)
            roles = sum(row.roles for row in rows)
            print(
                f"\n  {traveller:<18} {held}/{answerable} of its answers held "
                f"({held / answerable if answerable else 0:.0%})",
                file=stream,
            )
            done = answerable + settled + absent
            print(
                f"  {'':<18} {answerable} answered by a page, {settled} settled by an official "
                f"tool, {absent} do not arise, {roles - done} open\n"
                f"  {'':<18} -> {done}/{roles} of the traveller's questions accounted for "
                f"({done / roles if roles else 0:.0%})",
                file=stream,
            )
            for row in rows:
                flag = "" if row.held == row.answerable else "   <-- MISS"
                alias = f"   ({len(row.aliased)} under a host alias)" if row.aliased else ""
                tool = f", {len(row.settled)} by tool" if row.settled else ""
                absent_here = f", {len(row.not_applicable)} n/a" if row.not_applicable else ""
                print(
                    f"      {row.corridor:<40} {row.held}/{row.answerable} held, "
                    f"{row.answerable}/{row.roles} by a page{tool}{absent_here}{alias}{flag}",
                    file=stream,
                )
                for role, urls in sorted(row.missing.items()):
                    print(f"          {role:<22} {urls[0]}", file=stream)
        print(
            "\n  held: of the roles a page answers, how many the corpus holds that page for.\n"
            "        This is the regression half and should stay at 100% for every traveller.\n"
            "  by a page vs by an official tool: **counted apart and never added into `held`.**\n"
            "        A questionnaire the authority publishes *is* an answer — the plan names it\n"
            "        beside the question and the traveller acts on it — but nothing about it is\n"
            "        citable, and a tool-settled checklist lists no requirement (entry 60).\n"
            "  n/a: the question does not arise — Singapore asks a visa-free traveller for no\n"
            "        checklist, route, fee or processing time, and counting those as gaps\n"
            "        scored a corridor that resolved correctly as a thin one (entry 94).\n"
            "  open: no page, no tool, and the question does arise. This is the column that moves\n"
            "        with the traveller, and the one a single-traveller oracle could not show.",
            file=stream,
        )

    print("\nCorpus coverage, half 2: the dimension that varies, per traveller", file=stream)
    for country in report.countries:
        print(
            f"\n  {country.code}  {country.entries} entries, {country.pages_opened} opened, "
            f"{country.delegations} delegated  ->  {country.verdict}",
            file=stream,
        )
        print(f"      {VERDICT_MEANING[country.verdict]}", file=stream)
        if not country.families:
            continue
        print(
            f"      {'held':>9}  {'read':>10}  {'led on':>7}  {'shape':<9} {'crawl':<5}  family",
            file=stream,
        )
        for family in country.families:
            print(
                f"      {f'{family.held}/{family.countries}':>9} {family.completeness:>4.0%}  "
                f"{f'{family.read}':>4} {family.read_share:>4.0%}  "
                f"{f'{family.opened}':>7}  {family.shape:<9} "
                f"{'listed' if family.reservable else 'spread':<5}  {family.key}",
                file=stream,
            )
    if ungraded:
        # Said once, plainly, and with the countries named. The per-country lines each carry the
        # meaning string, but a reader scanning 53 of them sees a column of identical sentences and
        # takes the shape of a pass from it — which is what happened to the 43 built on 2026-08-30
        # (TODO item 43).
        print(
            f"\n  UNGRADED — {len(ungraded)} of {len(report.countries)}: "
            f"{', '.join(ungraded)}.\n"
            "  They have no per-traveller family and no oracle row, so neither half said anything\n"
            "  about them. That is a fixture nobody has curated, not a corpus that passed.",
            file=stream,
        )
    if report.unbuilt:
        print(
            f"\n  no corpus at all for {', '.join(report.unbuilt)} — a job nobody has run, "
            "not a coverage failure.",
            file=stream,
        )
    print(
        "\n  held: members the corpus knows the address of. An unread member is still a usable\n"
        "        candidate; what does not exist is the child of a member nobody read.\n"
        "  read: members the crawl actually fetched, and what the verdict is computed from.\n"
        "  led on: of those, how many led to a page the corpus recorded. **The gap between\n"
        "        these two is not a crawl gap** — it is members read that link nothing. On\n"
        "        the Netherlands that gap is 113 pages whose checklist is on VFS Global, and the\n"
        "        gate used to read it as work a rebuild could do (entry 101).\n"
        "  shape: gateway means opening a member yields that traveller's own page; leaf means the\n"
        "        member is the answer, so opening it buys nothing. Counted, never guessed, and\n"
        "        counted from `led on` so both sides of the ratio agree.\n"
        "  crawl: listed means one page names enough siblings for the crawl's reservation to see\n"
        "        the family; spread means it exists in the store and no page lists it.\n"
        "  Whether a corridor then *finds* what is held is a different question: selection-recall.",
        file=stream,
    )


def run_coverage(args: argparse.Namespace, stream: TextIO) -> int:
    """Is a country's corpus good enough to serve a corridor? Reads three stores, calls nothing.

    Exit code 1 when any country is short of a verdict somebody can promote on — a missing
    known answer, or a gateway family the crawl has not walked. `bounded by the authority` is a
    **pass**: the family cannot be crawled at any budget, so saying so and stopping is the correct
    outcome rather than a shortfall (entry 82).
    """

    oracle = load_oracle(Path(args.oracle))
    registry = get_country_registry()
    slugs = frozenset(country.slug for country in registry.countries)
    store = FileCorpusStore(settings.corpus_directory)
    text = PageTextStore(settings.page_text_directory)

    wanted = [c.strip().upper() for c in args.country.split(",") if c.strip()] or store.countries()
    rows: list[CountryCoverage] = []
    unbuilt: list[str] = []
    for code in wanted:
        corpus = store.load(code)
        country = registry.get(code)
        if corpus is None or country is None:
            unbuilt.append(code)
            continue
        rows.append(
            coverage(
                corpus,
                oracle,
                slug=country.slug,
                slugs=slugs,
                countries=len(registry.countries),
                indexed=lambda urls, code=code: text.indexed(code, urls),  # type: ignore[misc]
            )
        )
    if not rows:
        print(f"No corpus to measure in {settings.corpus_directory}.", file=stream)
        return 1
    built = coverage_report(rows, unbuilt)
    print_coverage(built, stream)
    short = any(
        row.verdict == "incomplete" or any(k.missing for k in row.known) for row in built.countries
    )
    return 1 if short else 0


def print_contention(
    contention: Contention, role: str, limit: int, indexed: set[str], stream: TextIO
) -> None:
    """One role's ranked candidates, with whether anybody could read each one.

    Built for the person curating `oracle/selection_oracle.yaml`, so it prints the two things a
    curation decision needs and nothing else: what the ranking thinks, and whether there is a body
    to check it against. `text` is the bound on the row — a role can only be curated from a page
    somebody could read.
    """

    print(
        f"\n{contention.key}: {len(contention.candidates)} in contention, "
        f"{contention.text_held} with stored text ({contention.rejected} rejected before scoring)",
        file=stream,
    )
    ranked = ranked_for_role(contention, role, limit=limit)  # type: ignore[arg-type]
    print(f"\n  best {len(ranked)} for {role}:", file=stream)
    for position, (candidate, score) in enumerate(ranked, start=1):
        readable = "text" if candidate.link.url in indexed else "  --"
        print(f"  {position:>3}. {score:>7.1f}  {readable}  {candidate.link.url}", file=stream)
        label = candidate.title or candidate.link.text
        if label:
            print(f"           {label[:96]}", file=stream)
    print(
        "\n  --show <url> prints that page's stored text, which is how a role is judged.\n"
        "  A page named in the oracle is still fetched live before a word of it is ever quoted.",
        file=stream,
    )


# How far down `PageTextStore.rank` to look before intersecting with the unpooled set. Generous on
# purpose: the pool holds most of what scores well on text, so the first matches are usually pages
# the selector already sees, and a small limit would report "nothing outside the pool" for the
# uninteresting reason that it never got past them.
MAXIMUM_UNPOOLED_MATCHES = 400


def print_outside_pool(
    contention: Contention,
    role: str,
    found: list[tuple[TextMatch, CandidatePage]],
    stream: TextIO,
) -> None:
    """The candidates the selector is never shown, ranked by what their own text says.

    **This is the only view in the project that can produce evidence against the pool gate.** Every
    page here scores zero for every role on the anchor scorer, so `_choose_what_to_read` excludes it
    and no arm of `selection-recall` can reach it; a row curated from this set is marked
    `curated_from: whole_corpus` so the grader can tell it apart from the twenty rows built inside
    the gate. TODO item 31.
    """

    total = len(contention.candidates) + len(contention.unpooled)
    print(
        f"\n{contention.key}: {len(contention.unpooled)} of {total} candidates score zero for "
        f"every role,\n  so the selector is never shown them. Ranked here by their own stored "
        f"text, for {role}:",
        file=stream,
    )
    if not found:
        print(
            "\n  None of them hold text that scores for this role. That is a real answer and it\n"
            "  is the one this item expects most often: the discarded set is largely chaff.\n"
            "  It is bounded by what the index holds — 23% of the corpus — so a role answered\n"
            "  only by an address nobody opened cannot appear here (TODO item 35).",
            file=stream,
        )
        return
    for position, (match, candidate) in enumerate(found, start=1):
        print(f"  {position:>3}. {match.score:>7.1f}  {match.url}", file=stream)
        label = candidate.title or candidate.link.text
        if label:
            print(f"           {label[:96]}", file=stream)
        if match.signals:
            print(f"           {' '.join(match.signals)[:96]}", file=stream)
    print(
        "\n  --show <url> prints a page's stored text, which is how a role is judged.\n"
        "  A row curated from this list MUST record `curated_from: whole_corpus`, or the grader\n"
        "  will read it as one more row that agrees with the gate by construction.",
        file=stream,
    )


def run_contention(args: argparse.Namespace, stream: TextIO) -> int:
    """Rebuild a corridor's contention set from the store, for curating an oracle row.

    No search, no model, no fetch — the whole point is a set the next session can reproduce. See
    `discovery/contention.py` for why it is corpus-only and what that costs.
    """

    countries = get_country_registry()
    destination = countries.by_slug(args.destination)
    if destination is None:
        print(f"Unknown destination slug: {args.destination}", file=stream)
        return 3
    corridor = Corridor(
        destination_slug=args.destination,
        passport_nationality=args.nationality.upper(),
        applying_from=getattr(args, "from").upper(),
        purpose=args.purpose,
    )
    config = corridor_destination(args.destination, corridor, stream)
    if config is None:
        return 3
    corpus = FileCorpusStore(settings.corpus_directory).load(destination.code)
    if corpus is None:
        print(f"There is no corpus for {destination.code}; build one first.", file=stream)
        return 3

    text = PageTextStore(settings.page_text_directory)
    if args.show:
        body = text.body_for_review(destination.code, args.show)
        if body is None:
            print(f"The index holds no text for {args.show}", file=stream)
            return 1
        print(body, file=stream)
        return 0

    held = text.indexed(
        destination.code, [entry.url for entry in corpus.entries_within(config.trusted_domains)]
    )
    contention = contention_for(
        corpus,
        config,
        corridor,
        countries=countries,
        lexicon=get_lexicon(),
        destination_code=destination.code,
        indexed=frozenset(held),
        page_text=text,
    )
    if args.outside_pool:
        nationality, _ = resolve_corridor_countries(corridor, countries)
        matches = text.rank(
            destination.code,
            role=args.role,
            corridor=corridor,
            nationality=nationality,
            lexicon=get_lexicon(),
            limit=MAXIMUM_UNPOOLED_MATCHES,
        )
        print_outside_pool(
            contention,
            args.role,
            unpooled_by_text(contention, matches, limit=args.limit),
            stream,
        )
        return 0
    print_contention(contention, args.role, args.limit, held, stream)
    return 0


async def run_eu_store(args: argparse.Namespace, stream: TextIO) -> int:
    """Refresh a union's shared store: its pages and the newest version they link (entry 201).

    Offline, like `corpus`, and small: no search, only the pages `supranational_authorities.yaml`
    lists. Run it after the regulation is amended, and before a rebuild; a corridor reads the store
    as it was last refreshed, and fetches the page itself live before a word of it is used.
    """

    union = get_supranational_registry().by_code(args.union.strip().upper())
    if union is None:
        print(f"{args.union} is not a union in supranational_authorities.yaml.", file=stream)
        return 2
    policy = get_runtime_policy()
    renderer = build_page_renderer(policy)
    fetcher = CrawlFetcher(
        timeout_seconds=settings.source_fetch_timeout_seconds,
        user_agent=settings.source_user_agent,
        host_delay_seconds=settings.discovery_host_delay_seconds,
        renderer=renderer,
        maximum_renders=args.renders,
    )
    try:
        report = await refresh_union_store(
            union,
            fetcher,
            FileCorpusStore(settings.corpus_directory),
            PageTextStore(settings.page_text_directory),
            now=datetime.now(UTC),
        )
    finally:
        if isinstance(renderer, PlaywrightPageRenderer):
            await renderer.aclose()
    print(f"{union.name}: {len(report.stored)} pages stored", file=stream)
    for url in report.stored:
        print(f"  stored  {url}", file=stream)
    for url, reason in report.failed.items():
        print(f"  failed  {url}: {reason}", file=stream)
    return 0 if report.stored and not report.failed else 1


async def run_corpus(args: argparse.Namespace, stream: TextIO) -> int:
    """Build one country's page corpus, offline and deliberately (DECISIONS entry 44).

    Not something a request can trigger, for the same reason `registry` is not: it crawls hundreds
    of pages and spends search quota, and the whole point of the corpus is that this happens on a
    schedule rather than while a traveller waits.
    """

    countries = get_country_registry()
    # By ISO code first, because everything downstream is keyed by one: the authority registry,
    # the corpus filename, the corridor. `find_country` matches slugs, names and synonyms and is
    # what the API uses, so it is left alone rather than widened — a request asking for the
    # destination "in" should not quietly become India.
    wanted = args.country.strip()
    country = countries.get(wanted.upper()) if len(wanted) == 2 else None
    country = country or find_country(wanted, countries)
    if country is None:
        print(
            f"{args.country} is not a country in countries.yaml. Give an ISO code (CA) or a name "
            "(Canada).",
            file=stream,
        )
        return 3
    try:
        trusted, _ = trusted_domains_for(country, get_authority_registry())
    except AutomaticDiscoveryError as exc:
        # The same refusal the API gives, for the same reason: a country whose government cannot be
        # identified has nothing safe to crawl, and guessing here would put unreviewed domains into
        # a store every later corridor reads.
        print(str(exc), file=stream)
        return 3

    store = FileCorpusStore(settings.corpus_directory)
    existing = store.load(country.code)
    print(
        f"building {country.name} from {len(trusted)} domains "
        f"({'existing corpus: ' + str(len(existing.entries)) + ' entries' if existing else 'new'})",
        file=stream,
    )

    policy = get_runtime_policy()
    renderer = build_page_renderer(policy)
    fetcher = CrawlFetcher(
        timeout_seconds=settings.source_fetch_timeout_seconds,
        user_agent=settings.source_user_agent,
        host_delay_seconds=settings.discovery_host_delay_seconds,
        renderer=renderer,
        # Not `settings.maximum_crawl_renders`, which is the request path's twelve. An offline
        # build has no traveller waiting and a challenged authority costs it nothing but time.
        maximum_renders=args.renders,
    )
    try:
        corpus, build = await build_country_corpus(
            country,
            trusted,
            build_search_provider(),
            fetcher,
            existing=existing,
            now=datetime.now(UTC),
            maximum_pages=args.pages,
            maximum_depth=args.depth,
            # On by default. The text is already fetched and already parsed, so keeping it costs a
            # write and nothing on the network; `--no-text` exists for rebuilding the corpus alone.
            page_text=None if args.no_text else PageTextStore(settings.page_text_directory),
        )
    finally:
        if isinstance(renderer, PlaywrightPageRenderer):
            await renderer.aclose()

    store.store(corpus)
    print_corpus_build(build, stream)
    # Nothing found is not an error to a script — the corpus is unchanged and still readable — but
    # it is worth an exit code, because a country that crawls to nothing needs a person.
    return 0 if build.total else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="visa-discover",
        description="Find official visa sources for a traveller, from official domains only.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser(
        "bootstrap", help="propose the official domains for a country, for human approval"
    )
    bootstrap.add_argument("--destination-name", required=True, help='e.g. "Brazil"')

    registry = commands.add_parser(
        "registry", help="generate the reviewed authority-domain registry for every country"
    )
    registry.add_argument(
        "--output",
        default=str(config_path(REGISTRY_FILENAME)),
        help="where to write the registry",
    )
    registry.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild every country instead of resuming; costs the full search quota again",
    )
    registry.add_argument(
        "--only",
        default="",
        help="comma-separated ISO codes to build, e.g. FR,DE,JP; the rest of the file is kept",
    )

    advisories = commands.add_parser(
        "advisories",
        help="survey where each passport country's government publishes travel advice, entry 260",
    )
    advisories.add_argument(
        "--output",
        default="var/advisories/survey.yaml",
        help="where to write the survey; resumed from if it exists",
    )
    advisories.add_argument(
        "--only", default="", help="comma-separated ISO codes to survey, e.g. GB,US,IN"
    )

    eu_store = commands.add_parser(
        "eu-store",
        help="refresh a union's shared store (the EU's regulation and ETIAS page), entry 201",
    )
    eu_store.add_argument("--union", default="EU", help="the union's code in the YAML file")
    eu_store.add_argument(
        "--renders", type=int, default=10, help="renders for the pages' challenges"
    )

    corpus = commands.add_parser(
        "corpus",
        help="crawl one country's official sites and record what pages exist, with no traveller",
    )
    corpus.add_argument("--country", required=True, help="ISO code or name, e.g. CA or Canada")
    corpus.add_argument(
        "--pages",
        type=int,
        default=DEFAULT_CORPUS_PAGES,
        help="how many pages the crawl may read; far above the request path's forty on purpose",
    )
    corpus.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_CORPUS_DEPTH,
        help="how many hops from a seed; the request path affords two, this is not that path",
    )
    corpus.add_argument(
        "--renders",
        type=int,
        default=DEFAULT_CORPUS_RENDERS,
        help="how many pages may be rendered to answer a browser challenge; twelve on the "
        "request path, and the budget is what left France unreadable (entry 92)",
    )
    corpus.add_argument(
        "--no-text",
        action="store_true",
        help="do not keep the readable text of pages read; the corpus alone is rebuilt",
    )

    page_text = commands.add_parser(
        "pagetext",
        help="index the body text of pages already fetched, and rank by it",
    )
    page_text.add_argument(
        "--backfill",
        action="store_true",
        help="index every body the retrieval cache already holds; no fetch, no search",
    )
    page_text.add_argument(
        "--purge-interstitials",
        action="store_true",
        help="delete stored bodies that are a bot-check page rather than the authority's own",
    )
    page_text.add_argument("--country", default="", help="ISO code to rank within, e.g. JP")
    page_text.add_argument("--role", default="document_checklist", help="which role to rank for")
    page_text.add_argument("--nationality", default="", help="ISO code, e.g. IN")
    page_text.add_argument("--from", default="", help="ISO code of where they apply, e.g. GB")
    page_text.add_argument(
        "--purpose", default="tourism", choices=["tourism", "business", "study", "transit"]
    )
    page_text.add_argument("--limit", type=int, default=10)

    regions = commands.add_parser(
        "regions", help="regenerate the region list a traveller chooses from, from GeoNames"
    )
    regions.add_argument(
        "--geonames", required=True, help="a downloaded GeoNames admin1CodesASCII.txt"
    )
    regions.add_argument("--output", default=str(config_path(REGIONS_FILENAME)))

    reports = commands.add_parser(
        "reports", help="list travellers' problem reports, each with the line that re-runs it"
    )
    reports.add_argument("directory", nargs="?", default="var/reports")
    reports.add_argument("--show", default="", help="print one report in full, by its id")

    audit = commands.add_parser(
        "audit",
        help="count why travellers go unanswered: reachability from data, causes from runs",
    )
    audit.add_argument(
        "directory",
        nargs="?",
        default="var/recall",
        help="a directory of recall logs; the reachability half needs no runs and is always shown",
    )

    selection = commands.add_parser(
        "selection-recall",
        help="grade what a selector chose to read against ground truth it did not help build",
    )
    selection.add_argument(
        "--oracle",
        default=str(DEFAULT_ORACLE_PATH),
        help="the hand-curated fixture naming the page that answers each role",
    )
    selection.add_argument(
        "--recall",
        default="var/recall",
        help="a directory of recall logs; only corridors named in the oracle are graded",
    )
    selection.add_argument(
        "--shipped-size",
        type=int,
        default=DEFAULT_SHORTLIST_SIZE,
        help="the second heuristic budget, for reference; the first is always the model's own",
    )

    cover = commands.add_parser(
        "coverage",
        help="is a country's stored corpus good enough to serve a corridor? offline, no model",
    )
    cover.add_argument(
        "--country",
        default="",
        help="comma-separated ISO codes, e.g. NL,SG; every country with a corpus by default",
    )
    cover.add_argument(
        "--oracle",
        default=str(DEFAULT_ORACLE_PATH),
        help="the hand-curated fixture, for the regression half only",
    )

    contend = commands.add_parser(
        "contention",
        help="rebuild a corridor's candidate set from the store, for curating an oracle row",
    )
    contend.add_argument("--destination", required=True, help="destination slug, e.g. netherlands")
    contend.add_argument("--nationality", required=True, help="ISO code, e.g. PH")
    contend.add_argument("--from", required=True, help="ISO code of where they apply, e.g. PH")
    contend.add_argument(
        "--purpose", default="tourism", choices=["tourism", "business", "study", "transit"]
    )
    contend.add_argument("--role", default="visa_decision", help="which role to rank for")
    contend.add_argument("--limit", type=int, default=20)
    contend.add_argument(
        "--show",
        default="",
        help="print this candidate's stored text instead, which is how a role is judged",
    )
    contend.add_argument(
        "--outside-pool",
        action="store_true",
        help="rank the candidates scoring zero for every role, by their own stored text, so an "
        "oracle row can name a page the selector is never shown (TODO item 31)",
    )

    corridor = commands.add_parser(
        "corridor", help="find the pages one traveller needs within approved domains"
    )
    corridor.add_argument(
        "--destination", required=True, help='destination slug or name, e.g. japan or "Japan"'
    )
    corridor.add_argument("--nationality", required=True, help="ISO code, e.g. IN")
    corridor.add_argument("--from", required=True, help="ISO code of where they apply, e.g. GB")
    corridor.add_argument(
        "--purpose", default="tourism", choices=["tourism", "business", "study", "transit"]
    )
    corridor.add_argument("--format", default="table", choices=["table", "yaml"])
    corridor.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "resolve the corridor this many times and report what varied between runs. "
            "Costs the full search and model quota each time; TODO item 17"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bootstrap":
            return asyncio.run(run_bootstrap(args, sys.stderr))
        if args.command == "registry":
            return asyncio.run(run_registry(args, sys.stderr))
        if args.command == "advisories":
            return asyncio.run(run_advisories(args, sys.stderr))
        if args.command == "corpus":
            return asyncio.run(run_corpus(args, sys.stderr))
        if args.command == "eu-store":
            return asyncio.run(run_eu_store(args, sys.stderr))
        if args.command == "pagetext":
            return run_page_text(args, sys.stderr)
        if args.command == "audit":
            return run_audit(args, sys.stderr)
        if args.command == "reports":
            return run_reports(args, sys.stdout)
        if args.command == "regions":
            return run_regions(args, sys.stderr)
        if args.command == "selection-recall":
            return run_selection_recall(args, sys.stderr)
        if args.command == "coverage":
            return run_coverage(args, sys.stderr)
        if args.command == "contention":
            return run_contention(args, sys.stderr)
        return asyncio.run(run_corridor(args, sys.stderr))
    except SearchError as exc:
        print(f"Search is unavailable: {exc}", file=sys.stderr)
        return 3
    except VisaResearchError as exc:
        print(f"Discovery could not complete: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
