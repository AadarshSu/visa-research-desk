"""Resolving a whole corridor: search, crawl, fetch, assign roles, or refuse."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from discovery_site import (
    ARCHIVED,
    AUTHORITY,
    BUSINESS_PDF,
    CHECKLIST_HUB,
    DETAIL_CHINA,
    DETAIL_INDIA,
    EXEMPTIONS,
    INDEX,
    MISSION,
    MISSION_CHECKLIST,
    MISSION_INDEX,
    MISSION_QUEUED_DOWNLOAD,
    MISSION_SPOUSE,
    OFF_DOMAIN,
    TOURIST_PDF,
    destination,
    handler,
    site_pages,
)
from pydantic import AnyHttpUrl

from visa_research_agent.discovery.adjudication import (
    AdjudicationError,
    AdjudicationQuotaExhausted,
    RoleAdjudication,
    RoleChoice,
    RoleTool,
)
from visa_research_agent.discovery.corpus import CorpusEntry, CountryCorpus
from visa_research_agent.discovery.crawl import DEFAULT_CRAWL_PAGES, CrawlFetcher
from visa_research_agent.discovery.lexicon import get_country_registry, get_lexicon
from visa_research_agent.discovery.models import (
    CandidatePage,
    Corridor,
    PageLink,
    ResolvedCorridor,
    RoleScores,
    SearchResult,
)
from visa_research_agent.discovery.resolver import (
    DEFAULT_SHORTLIST_SIZE,
    CorridorResolver,
    FetchedShortlist,
    build_source_id,
    clean_title,
    derive_authority,
    failed_for_now,
    unread_checklist_pages,
)
from visa_research_agent.discovery.scoring import score_link, wrong_audience
from visa_research_agent.discovery.search import SearchError, SearchQuotaExhausted
from visa_research_agent.domain.models import DocumentLink, SourceFailure
from visa_research_agent.domain.trust import host_of
from visa_research_agent.research.live_sources import LiveSourceFetcher, extract_document_links
from visa_research_agent.research.source_cache import FileSourceCache

RESOLVED_AT = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)


def corridor() -> Corridor:
    return Corridor(
        destination_slug="testland",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )


class StubSearchProvider:
    """Returns the same ranked URLs for every query, including deliberate spam."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.queries: list[str] = []
        self.title = ""
        self.error: SearchError | None = None

    title: str = ""

    async def search(self, query: str, *, count: int) -> list[SearchResult]:
        if self.error is not None:
            raise self.error
        self.queries.append(query)
        return [
            SearchResult(url=url, title=self.title, snippet="", query=query, rank=rank)
            for rank, url in enumerate(self.urls[:count])
        ]


async def sleep_none(_: float) -> None:
    return None


def build_resolver(
    tmp_path: Path,
    requests: list[httpx.Request],
    search_urls: list[str],
    *,
    robots: dict[str, str] | None = None,
) -> tuple[CorridorResolver, StubSearchProvider]:
    transport = httpx.MockTransport(handler(requests, robots=robots))  # type: ignore[arg-type]
    provider = StubSearchProvider(search_urls)
    crawl_fetcher = CrawlFetcher(transport=transport, sleep=sleep_none, host_delay_seconds=0.0)
    live_fetcher = LiveSourceFetcher(
        FileSourceCache(tmp_path / "cache"),
        ttl_hours=24.0,
        maximum_stale_hours=168.0,
        timeout_seconds=5.0,
        concurrency=2,
        maximum_characters=50_000,
        minimum_characters=40,
        user_agent="test-agent",
        transport=transport,
        now=lambda: RESOLVED_AT,
    )
    resolver = CorridorResolver(
        provider,
        crawl_fetcher,
        live_fetcher,
        minimum_role_score=10.0,
        now=lambda: RESOLVED_AT,
    )
    return resolver, provider


def test_source_ids_are_derived_from_the_url_so_they_are_stable() -> None:
    taken: set[str] = set()

    first = build_source_id("testland", DETAIL_INDIA, taken)
    second = build_source_id("testland", DETAIL_INDIA, set())

    assert first == second, "the same page must keep the same id between runs"
    assert first.startswith("testland_")
    assert "india" in first


def test_colliding_source_ids_are_disambiguated() -> None:
    taken: set[str] = set()

    first = build_source_id("testland", "https://immigration.gov.example/a/index.html", taken)
    second = build_source_id("testland", "https://immigration.gov.example/b/index.html", taken)

    assert first != second


def test_a_title_loses_the_site_name_authorities_append() -> None:
    assert clean_title("Visa: Temporary Visitor Visa | Embassy of Japan", "x") == (
        "Visa: Temporary Visitor Visa"
    )
    assert clean_title(None, "fallback") == "fallback"


def test_an_authority_is_named_from_the_host() -> None:
    authority, kind = derive_authority(MISSION_INDEX, destination())

    assert "mission" in authority.lower()
    assert kind == "embassy_or_high_commission"


@pytest.mark.anyio
async def test_a_corridor_resolves_to_the_right_pages(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolver, provider = build_resolver(tmp_path, requests, [INDEX, MISSION_INDEX])

    resolved = await resolver.resolve(destination(), corridor())

    assert provider.queries, "search should have been consulted"
    assert resolved.is_usable, f"unresolved: {resolved.unresolved_roles} notes={resolved.notes}"

    by_role = {role: source for source in resolved.sources for role in source.roles}
    assert "visa_decision" in by_role
    assert "document_checklist" in by_role

    # Either genuine decision page is acceptable: the exemptions list establishes the rule and the
    # per-nationality page confirms it. Japan's hand-curated config uses the exemptions page and
    # Singapore's uses the per-nationality one, so both are right answers.
    assert str(by_role["visa_decision"].url) in {EXEMPTIONS, DETAIL_INDIA}
    # What must never happen is a page about a different nationality being chosen.
    assert all(str(source.url) != DETAIL_CHINA for source in resolved.sources)
    assert by_role["visa_decision"].signals, "a selection must record why it was made"


@pytest.mark.anyio
async def test_resolution_never_touches_a_host_outside_the_approved_domains(
    tmp_path: Path,
) -> None:
    """Search returns spam; none of it may ever be requested."""

    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(
        tmp_path, requests, [OFF_DOMAIN, "https://ivisa.com/testland", INDEX]
    )

    await resolver.resolve(destination(), corridor())

    assert requests
    for request in requests:
        assert destination().trusts_host(host_of(str(request.url))), request.url


@pytest.mark.anyio
async def test_a_page_for_another_nationality_is_never_selected(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [DETAIL_CHINA, INDEX])

    resolved = await resolver.resolve(destination(), corridor())

    assert all(str(source.url) != DETAIL_CHINA for source in resolved.sources)


@pytest.mark.anyio
async def test_an_archived_page_is_never_selected(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [ARCHIVED, INDEX])

    resolved = await resolver.resolve(destination(), corridor())

    assert all(str(source.url) != ARCHIVED for source in resolved.sources)


@pytest.mark.anyio
async def test_a_wrong_audience_page_does_not_become_the_checklist(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [MISSION_SPOUSE, MISSION_INDEX, INDEX])

    resolved = await resolver.resolve(destination(), corridor())

    checklist = [s for s in resolved.sources if "document_checklist" in s.roles]
    assert all(str(source.url) != MISSION_SPOUSE for source in checklist)


@pytest.mark.anyio
async def test_a_corridor_with_nothing_usable_is_refused_rather_than_guessed(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [OFF_DOMAIN])

    resolved = await resolver.resolve(destination(), corridor())

    assert not resolved.is_usable
    assert resolved.sources == []
    assert set(resolved.unresolved_roles) >= {"visa_decision", "document_checklist"}
    assert resolved.notes, "a refusal must explain itself"


@pytest.mark.anyio
async def test_a_resolved_corridor_converts_into_a_loadable_destination(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [INDEX, MISSION_INDEX])

    resolved = await resolver.resolve(destination(), corridor())
    config = resolved.to_destination_config(destination())

    assert config.application_document_source_ids
    assert config.load_bearing_source_ids
    # Everything downstream now treats this exactly like hand-written configuration.
    assert all(config.trusts_host(host_of(str(source.url))) for source in config.sources)


@pytest.mark.anyio
async def test_resolution_costs_no_model_calls(tmp_path: Path) -> None:
    """The cost guarantee: the heuristics decide, so a corridor is free to resolve."""

    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [INDEX, MISSION_INDEX])

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved.model_calls == 0


def test_the_shortlist_budget_is_filled_rather_than_left_part_used(tmp_path: Path) -> None:
    """Taking only the top few per role left most of the fetch budget unspent.

    Vietnam used six of ten places while every readable `evisa.gov.vn` page sat just outside the
    per-role cut, so the one site that needed rendering was never read at all.
    """

    resolver, _ = build_resolver(tmp_path, [], [])
    # Pinned: this is about *filling* the budget, whatever the budget is. Asserting against the
    # production default made it a test of that number instead, and it broke when the number moved.
    resolver.shortlist_size = 10
    candidates = [
        CandidatePage(
            link=PageLink(
                url=f"https://immigration.gov.example/visa/p{index}",
                text="Visa documents required",
                heading="",
                depth=1,
                discovered_from="seed",
            ),
            link_scores=RoleScores(scores={"document_checklist": 50.0 - index}),
            found_by="crawl",
        )
        for index in range(12)
    ]

    shortlist = resolver._shortlist(candidates)

    assert len(shortlist) == resolver.shortlist_size
    # Still best-first: filling spare places must not promote a weak page over a strong one.
    assert shortlist[0].link.url.endswith("p0")
    # One domain, so reserving a place per domain reserves one and changes nothing else.
    assert [page.link.url for page in shortlist] == [
        candidate.link.url for candidate in candidates[: resolver.shortlist_size]
    ]


def page(url: str, score: float, *, role: str = "document_checklist") -> CandidatePage:
    return CandidatePage(
        link=PageLink(
            url=url,
            text="Visa documents required",
            heading="",
            depth=1,
            discovered_from="seed",
        ),
        link_scores=RoleScores(scores={role: score}),
        found_by="crawl",
    )


def test_one_authority_cannot_take_every_place_in_the_shortlist(tmp_path: Path) -> None:
    """How the United States refused a corridor it had the evidence for.

    Eight federal domains were trusted, because the country's own top-level domain is `gov`. The
    ten fetch places went to whichever of them scored loudest, and the mission serving the
    traveller was never read — so it could not fill a role, so the corridor refused.

    The quiet page here is outside the per-role cut *and* outside the ten best by score, so it is
    reached only by looking at every candidate. A reservation drawn from the pages already chosen
    would find exactly the pages that did not need reserving.
    """

    resolver, _ = build_resolver(tmp_path, [], [])
    # Pinned, so the quiet page stays genuinely outside the window. With a wider one it would be
    # admitted on score alone and the reservation this tests would never be exercised.
    resolver.shortlist_size = 10
    loud = [page(f"https://interior.gov.example/p{index}", 50.0 - index) for index in range(12)]
    quiet = page("https://in.mission.gov.example/visas/", 5.0)

    shortlist = resolver._shortlist([*loud, quiet])

    assert quiet.link.url in [candidate.link.url for candidate in shortlist]
    assert len(shortlist) == resolver.shortlist_size


def test_a_mission_network_does_not_reserve_a_place_for_every_post(tmp_path: Path) -> None:
    """Keyed on the registrable domain, which is the unit trust is granted in.

    Per host, one authority's posts would reserve every place and recreate the crowding.
    """

    resolver, _ = build_resolver(tmp_path, [], [])
    # Pinned: twelve posts must exceed the window for the crowding to exist at all.
    resolver.shortlist_size = 10
    posts = [
        page(f"https://{label}.mission.gov.example/visas/", 80.0)
        for label in ("in", "de", "uk", "fr", "jp", "br", "cn", "sg", "za", "mx", "ke", "pe")
    ]
    other = page("https://immigration.gov.example/checklist", 1.0)

    shortlist = resolver._shortlist([*posts, other])
    urls = [candidate.link.url for candidate in shortlist]

    assert other.link.url in urls
    assert len(shortlist) == resolver.shortlist_size


def test_reserving_a_place_never_admits_a_page_no_role_wants(tmp_path: Path) -> None:
    """A domain being trusted is not a reason to spend a fetch on a page nothing scored."""

    resolver, _ = build_resolver(tmp_path, [], [])
    wanted = [page(f"https://immigration.gov.example/p{index}", 40.0) for index in range(3)]
    unwanted = CandidatePage(
        link=PageLink(
            url="https://interior.gov.example/press-release",
            text="",
            heading="",
            depth=1,
            discovered_from="seed",
        ),
        link_scores=RoleScores(scores={}),
        found_by="crawl",
    )

    shortlist = resolver._shortlist([*wanted, unwanted])

    assert unwanted.link.url not in [candidate.link.url for candidate in shortlist]
    assert len(shortlist) == 3


def test_a_candidate_no_role_wants_is_not_worth_fetching(tmp_path: Path) -> None:
    resolver, _ = build_resolver(tmp_path, [], [])
    candidates = [
        CandidatePage(
            link=PageLink(
                url=f"https://immigration.gov.example/visa/p{index}",
                text="",
                heading="",
                depth=1,
                discovered_from="seed",
            ),
            link_scores=RoleScores(scores={}),
            found_by="crawl",
        )
        for index in range(12)
    ]

    assert resolver._shortlist(candidates) == []


@pytest.mark.anyio
async def test_a_very_long_search_title_does_not_take_down_the_corridor(tmp_path: Path) -> None:
    """Search returns titles far longer than any anchor text on a page.

    China's foreign ministry returned a 300-plus character speech headline, and building the link
    raised instead of trimming, so one long title failed the whole corridor.
    """

    resolver, _ = build_resolver(tmp_path, [], [INDEX])
    resolver.provider.urls = [INDEX]  # type: ignore[attr-defined]
    resolver.provider.title = "Remarks of the Vice Foreign Minister " * 20  # type: ignore[attr-defined]

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved is not None


def test_a_host_whose_name_does_not_resolve_takes_no_fetch_place(tmp_path: Path) -> None:
    """A place spent on an unreachable host buys nothing, and the US was spending half of them.

    `sample2.usembassy.gov` — a sample host that does not exist — scored highest of all its
    candidates and took the place reserved for the mission network, while `go.usa.gov`, a
    decommissioned shortener, took two more.
    """

    resolver, _ = build_resolver(tmp_path, [], [])
    resolver.crawl_fetcher.unresolvable_hosts = {"sample.embassy.gov.example"}
    dead = [page(f"https://sample.embassy.gov.example/visa/p{index}", 90.0) for index in range(3)]
    alive = [page(f"https://immigration.gov.example/visa/p{index}", 20.0) for index in range(3)]

    shortlist = resolver._shortlist([*dead, *alive])
    urls = [candidate.link.url for candidate in shortlist]

    assert urls == [candidate.link.url for candidate in alive]


def test_a_page_the_crawl_could_not_use_is_still_worth_fetching(tmp_path: Path) -> None:
    """The exclusion must stay narrow. Retrieval is not the crawler: it reads PDFs, renders, and
    carries different limits, so a page that was too large, was not HTML, or answered `502` may
    still be readable evidence. Dropping those would trade real answers for a tidier count."""

    resolver, _ = build_resolver(tmp_path, [], [])
    unusable = "https://immigration.gov.example/visa/big.html"
    resolver.crawl_fetcher._record_failure(unusable, "unusable", "it is larger than the size limit")

    shortlist = resolver._shortlist([page(unusable, 40.0)])

    assert [candidate.link.url for candidate in shortlist] == [unusable]


@pytest.mark.anyio
async def test_a_url_an_authority_refused_is_not_asked_for_a_second_time(tmp_path: Path) -> None:
    """Asking again is a retry, which is the one thing a block must never provoke — and it would
    answer the same way. The refusal is still reported: `inaccessible_domains` is read from what the
    crawl recorded, not from whether the page was later fetched."""

    requests: list[httpx.Request] = []
    site = handler(requests)

    def refusing(request: httpx.Request) -> httpx.Response:
        if str(request.url).rstrip("/") == MISSION_CHECKLIST:
            requests.append(request)
            return httpx.Response(403, text="Access denied")
        served: httpx.Response = site(request)  # type: ignore[operator]
        return served

    transport = httpx.MockTransport(refusing)
    resolver, _ = build_resolver(tmp_path, [], [INDEX, MISSION_INDEX])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport

    resolved = await resolver.resolve(destination(), corridor())

    assert host_of(MISSION_CHECKLIST) in resolved.inaccessible_domains
    # Once, by the crawl. The shortlist must not spend a place asking a second time.
    assert [str(request.url) for request in requests].count(MISSION_CHECKLIST) == 1


async def resolve_with_status(
    tmp_path: Path, refused_url: str, status: int, *, also_refuse: tuple[str, ...] = ()
) -> ResolvedCorridor:
    """Resolve the corridor with one URL answering `status`, reached as a search result.

    Via search deliberately: the crawl discards a page it could not fetch, so a refusal only has a
    score to be judged on when something else scored it first.

    `also_refuse` exists because `decision_blocking_urls` is only populated when the visa decision
    is genuinely **missing** — if another page answered it, nothing blocked it. Refusing every page
    that could fill the role is what actually reproduces the France shape.
    """

    requests: list[httpx.Request] = []
    site = handler(requests)
    refused_urls = {refused_url, *also_refuse}

    def refusing(request: httpx.Request) -> httpx.Response:
        if str(request.url).rstrip("/") in refused_urls:
            return httpx.Response(status, text="no")
        served: httpx.Response = site(request)  # type: ignore[operator]
        return served

    transport = httpx.MockTransport(refusing)
    # The mission is seeded too, so that refusing every visa-decision page still leaves
    # something readable to cite. Entry 27 requires that: with nothing at all to cite there is
    # no plan, only a link.
    resolver, _ = build_resolver(tmp_path, [], [INDEX, DETAIL_INDIA, MISSION_INDEX])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport
    return await resolver.resolve(destination(), corridor())


@pytest.mark.anyio
async def test_a_rate_limit_is_reported_but_can_never_resolve_a_corridor(tmp_path: Path) -> None:
    """A `429` says "not right now", which is a different fact from "you may not read this".

    Entry 27 already reasoned this way about a `502` — "try again later" is the honest advice — but
    `BLOCKING_STATUS_CODES` lumped all three together, so a momentary rate limit could force a visa
    decision to *unknown* and hand a traveller a page as one an authority refused them. It stays
    reported, because entry 18 requires a refusal never to read as "nothing found". DECISIONS 32.
    """

    resolved = await resolve_with_status(tmp_path, DETAIL_INDIA, 429)

    assert host_of(DETAIL_INDIA) in resolved.inaccessible_domains
    assert any("429" in note for note in resolved.notes), resolved.notes
    # Reported, and nothing more: not handed over, and not grounds to resolve.
    assert resolved.inaccessible_urls == []
    assert resolved.decision_blocking_urls == []
    assert not resolved.decision_is_unverified


@pytest.mark.anyio
async def test_a_settled_refusal_of_the_decision_page_is_what_may_resolve_one(
    tmp_path: Path,
) -> None:
    """The France shape, which entry 27 exists for and this must not break.

    `france-visas.gouv.fr` answers `403` and is the only place the decision lives, so naming it and
    handing over the URL is the one useful thing left to say.
    """

    resolved = await resolve_with_status(
        tmp_path,
        DETAIL_INDIA,
        403,
        also_refuse=(EXEMPTIONS, f"https://{AUTHORITY}/visa/detail"),
    )

    assert DETAIL_INDIA in resolved.inaccessible_urls
    assert "visa_decision" not in {role for source in resolved.sources for role in source.roles}, (
        "this shape only exists when the decision was not found elsewhere"
    )
    assert DETAIL_INDIA in resolved.decision_blocking_urls, (
        "the per-nationality decision page refused us, so it is why the decision is unverifiable"
    )
    assert resolved.decision_is_unverified
    assert resolved.is_usable, "a corridor whose only gap is behind a block still produces a plan"


@pytest.mark.anyio
async def test_a_refusal_is_not_decision_blocking_when_the_decision_was_found(
    tmp_path: Path,
) -> None:
    """Nothing blocked the decision if we got the decision.

    `decision_blocking_urls` used to be populated from the keyword score whether or not
    `visa_decision` was filled, so it listed pages that had blocked nothing. Both its consumers —
    `is_usable` and `decision_is_unverified` — short-circuit once the role is filled, so this was
    never read; it was a field describing something that had not happened. DECISIONS entry 57.
    """

    resolved = await resolve_with_status(tmp_path, DETAIL_INDIA, 403)

    assert "visa_decision" in {role for source in resolved.sources for role in source.roles}
    assert DETAIL_INDIA in resolved.inaccessible_urls, "the refusal is still reported"
    assert resolved.decision_blocking_urls == []
    assert not resolved.decision_is_unverified


@pytest.mark.anyio
async def test_a_corridor_refuses_when_adjudication_cannot_answer(tmp_path: Path) -> None:
    """End to end, because the unit test only proves the exception is raised, not acted on.

    An OpenAI outage must not quietly hand the corridor to the heuristic — the decider entry 15
    caught naming a Riyadh page as a UK applicant's checklist at full confidence. It refuses
    instead, says so, and still reports the two calls it paid for. DECISIONS entry 31.
    """

    class AlwaysFails:
        async def adjudicate(
            self, system_prompt: str, packet: str, *, usage: object = None
        ) -> object:
            raise AdjudicationError("the request failed")

    resolver, _ = build_resolver(tmp_path, [], [INDEX, MISSION_INDEX])
    resolver.adjudicator = AlwaysFails()  # type: ignore[assignment]

    resolved = await resolver.resolve(destination(), corridor())

    assert not resolved.is_usable
    assert resolved.sources == []
    assert resolved.model_calls == 2, "a refusal still spent what it spent"
    assert any("failed on all 2 attempts" in note for note in resolved.notes), resolved.notes
    # And nothing was decided by the ranking that entry 15 caught being confidently wrong.
    assert not any("heuristic ranking was used" in note for note in resolved.notes)


def test_only_a_refusal_of_a_plausible_decision_page_counts(tmp_path: Path) -> None:
    """The credibility half, which is what keeps the exception from swallowing the rule.

    A page carrying no visa-decision signal cannot have cost us the decision, so its refusal is
    reported and nothing more. Scored above zero is a deliberately low bar rather than a tuned one:
    the scorer already vetoes site furniture and wrong-audience pages outright, so any positive
    score means real signal was seen.
    """

    resolver, _ = build_resolver(tmp_path, [], [])
    decision_page = page(DETAIL_INDIA, 31.0, role="visa_decision")
    footer = page(EXEMPTIONS, 0.0, role="visa_decision")
    candidates = {DETAIL_INDIA: decision_page, EXEMPTIONS: footer}

    blocking = resolver._decision_blocking([DETAIL_INDIA, EXEMPTIONS], candidates)

    assert blocking == [DETAIL_INDIA]
    # A refusal nothing ever scored cannot qualify either, which fails toward refusing.
    assert resolver._decision_blocking(["https://immigration.gov.example/never-seen"], {}) == []


def test_the_shortlist_is_a_recall_budget_and_is_wide_enough_to_be_one() -> None:
    """The scorer decides what the model is *allowed to see*, not what is chosen.

    So the two errors it can make are not symmetric: a page ranked out of this window is one
    nothing downstream can recover, while a page ranked in wrongly costs an excerpt. Ten places
    made the heuristic the effective decider for every corridor whose right page sat eleventh —
    measured live on 2026-08-18, Canada and Japan both refused at ten and filled every role at
    twenty-five, having changed nothing else.

    Pinned because the cost of narrowing it again is invisible: corridors do not fail loudly, they
    quietly refuse, and the page that would have answered is never fetched to be missed.
    """

    assert DEFAULT_SHORTLIST_SIZE >= 25


def test_a_wider_window_still_refuses_a_page_no_role_wants(tmp_path: Path) -> None:
    """Widening must buy recall, never admit noise. A page nothing scored is still not fetched,
    however much room there is — otherwise the extra places fill with whatever the crawl saw."""

    resolver, _ = build_resolver(tmp_path, [], [])
    wanted = [page(f"https://immigration.gov.example/p{index}", 40.0) for index in range(3)]
    unwanted = [
        CandidatePage(
            link=PageLink(
                url=f"https://immigration.gov.example/noise{index}",
                text="",
                heading="",
                depth=1,
                discovered_from="seed",
            ),
            link_scores=RoleScores(scores={}),
            found_by="crawl",
        )
        for index in range(30)
    ]

    shortlist = resolver._shortlist([*wanted, *unwanted])

    assert len(shortlist) == 3, "spare places are left empty rather than filled with noise"


async def resolve_with_retrieval_status(
    tmp_path: Path, refused_url: str, status: int
) -> ResolvedCorridor:
    """Resolve with one URL refusing **the shortlist fetch only**, which the crawl never meets.

    The two stages announce different user agents, which is what separates them here. It reproduces
    the case the crawl was silently covering for: a page served happily for its links and refused
    when it is asked for as evidence. Before `_report_retrieval_refusals`, `_fetch_bodies` dropped
    `report.failures` on the floor, so that refusal reached neither the notes nor
    `inaccessible_domains` — and with the crawl gone for a destination that has a corpus, it is the
    only place a refusal is seen at all. DECISIONS entry 48.
    """

    requests: list[httpx.Request] = []
    site = handler(requests)

    def refusing(request: httpx.Request) -> httpx.Response:
        retrieval = request.headers.get("user-agent") == "test-agent"
        if retrieval and str(request.url).rstrip("/") == refused_url:
            return httpx.Response(status, text="no")
        served: httpx.Response = site(request)  # type: ignore[operator]
        return served

    transport = httpx.MockTransport(refusing)
    resolver, _ = build_resolver(tmp_path, [], [INDEX, DETAIL_INDIA])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport
    return await resolver.resolve(destination(), corridor())


@pytest.mark.anyio
async def test_a_refusal_met_only_while_reading_the_shortlist_is_still_reported(
    tmp_path: Path,
) -> None:
    """The reporting discipline must not depend on which stage happened to meet the refusal."""

    resolved = await resolve_with_retrieval_status(tmp_path, DETAIL_INDIA, 403)

    assert host_of(DETAIL_INDIA) in resolved.inaccessible_domains
    assert resolved.inaccessible_urls == [DETAIL_INDIA]
    assert any(DETAIL_INDIA.split("//")[1].split("/")[0] in note for note in resolved.notes), (
        resolved.notes
    )
    # Not asserted here: `decision_blocking_urls`. The decision *was* found in this corridor, so
    # nothing blocked it — see the "not decision blocking when the decision was found" test. What
    # this one is about is that the refusal is reported at all.


@pytest.mark.anyio
async def test_a_rate_limit_while_reading_the_shortlist_is_reported_and_nothing_more(
    tmp_path: Path,
) -> None:
    """Entry 32's line, applied to the stage that had no line at all.

    `CrawlFetcher.persistent_refusals` has told a `429` from a `403` since entry 32; retrieval kept
    the status only inside a sentence, so anything acting on it would have had to read prose —
    which entry 36 forbids for exactly this reason. `SourceFailure.http_status` is what makes the
    same rule enforceable here.
    """

    resolved = await resolve_with_retrieval_status(tmp_path, DETAIL_INDIA, 429)

    assert host_of(DETAIL_INDIA) in resolved.inaccessible_domains
    assert any("429" in note for note in resolved.notes), resolved.notes
    assert resolved.inaccessible_urls == []
    assert resolved.decision_blocking_urls == []
    assert not resolved.decision_is_unverified


def corpus_of(*, padding: int) -> CountryCorpus:
    """A corpus holding the whole fake site, plus filler pages that score for no role.

    The filler is what makes the corpus *large*, which is the only thing the crawl decision reads.
    It scores zero, so it can never be shortlisted — a corpus is a page inventory, not a shortlist,
    and most of a real one is noise: measured 2026-08-22, 723 of Canada's first 1,071 entries scored
    nothing at all.
    """

    seen = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    urls = [*site_pages(), *(f"https://{AUTHORITY}/about/page-{n}.html" for n in range(padding))]
    return CountryCorpus(
        country_code="TL",
        country_name="Testland",
        trusted_domains=[AUTHORITY, "embassy.gov.example"],
        built_at=seen,
        entries=[CorpusEntry(url=url, first_seen=seen, last_seen=seen) for url in urls],
    )


@pytest.mark.anyio
async def test_a_corpus_that_out_covers_a_crawl_replaces_it(tmp_path: Path) -> None:
    """The crawl is 62% of a cold corridor and contributed no unique shortlisted page (entry 48).

    The bound is derived, not tuned: a crawl visits at most `DEFAULT_CRAWL_PAGES`, so a corpus
    already offering more pages than that on trusted domains cannot be out-covered by one.
    """

    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [INDEX])
    resolver.corpus = corpus_of(padding=DEFAULT_CRAWL_PAGES + 1)

    resolved = await resolver.resolve(destination(), corridor())

    assert resolver.crawl_fetcher.requested == [], "no page may be walked for its links"
    assert any("the crawl was skipped" in note for note in resolved.notes), resolved.notes
    assert resolved.is_usable, f"unresolved: {resolved.unresolved_roles} notes={resolved.notes}"
    by_role = {role: source for source in resolved.sources for role in source.roles}
    assert "visa_decision" in by_role
    assert "document_checklist" in by_role


class _Captured(Exception):
    pass


@pytest.mark.anyio
async def test_a_page_search_returned_stays_marked_searched_when_the_corpus_version_wins(
    tmp_path: Path,
) -> None:
    """Search and the corpus describe the same page; the corpus's anchor text outscores search's
    title, so its version is kept — and `found_by` says `corpus`. Whether search returned the page
    is a separate fact, and the one entry 243 found predicts an answer, so it must survive."""

    requests: list[httpx.Request] = []
    search_only = f"https://{AUTHORITY}/visa/search-only.html"
    resolver, provider = build_resolver(tmp_path, requests, [EXEMPTIONS, search_only])
    provider.title = ""
    seen = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    corpus = corpus_of(padding=DEFAULT_CRAWL_PAGES + 1)
    corpus.entries = [
        CorpusEntry(
            url=EXEMPTIONS,
            link_text="Visa Exemption Countries and Regions",
            first_seen=seen,
            last_seen=seen,
        )
        if entry.url == EXEMPTIONS
        else entry
        for entry in corpus.entries
    ]
    resolver.corpus = corpus
    offered: dict[str, CandidatePage] = {}

    async def capture(destination, corridor, candidates, stored_scores, notes, trace):  # type: ignore[no-untyped-def]
        offered.update(candidates)
        raise _Captured

    resolver._choose_what_to_read = capture  # type: ignore[method-assign]
    with pytest.raises(_Captured):
        await resolver.resolve(destination(), corridor())

    assert offered[EXEMPTIONS].found_by == "corpus", "the corpus's anchor text should have won"
    assert offered[EXEMPTIONS].searched
    assert offered[search_only].found_by == "search" and offered[search_only].searched
    corpus_only = next(u for u, c in offered.items() if u not in {EXEMPTIONS, search_only})
    assert not offered[corpus_only].searched


@pytest.mark.anyio
async def test_a_destination_nobody_has_built_still_crawls(tmp_path: Path) -> None:
    """The conditional entry 48 requires. Removing the crawl is conditional on having a map."""

    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [INDEX])
    resolver.corpus = None

    resolved = await resolver.resolve(destination(), corridor())

    assert resolver.crawl_fetcher.requested, "with no corpus there is nothing else to go on"
    assert not any("the crawl was skipped" in note for note in resolved.notes), resolved.notes
    assert resolved.is_usable, f"unresolved: {resolved.unresolved_roles}"


@pytest.mark.anyio
async def test_a_search_outage_is_survived_by_a_country_whose_pages_are_stored(
    tmp_path: Path,
) -> None:
    """Canada holds a 1.7 MB corpus and still died with `Search is unavailable: HTTP 402`.

    Search left the request path for *recall* (entry 48); it was never meant to be the one thing a
    fully-built country could not do without. The fallback is loud on purpose. DECISIONS entry 74.
    """

    requests: list[httpx.Request] = []
    resolver, provider = build_resolver(tmp_path, requests, [INDEX])
    resolver.corpus = corpus_of(padding=DEFAULT_CRAWL_PAGES + 1)
    provider.error = SearchQuotaExhausted("the search account has spent 25.01 against its 25.0")

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved.is_usable, f"unresolved: {resolved.unresolved_roles} notes={resolved.notes}"
    assert resolved.ran_without_search
    assert any("search was unavailable" in note for note in resolved.notes), resolved.notes
    assert any("25.01" in note for note in resolved.notes), resolved.notes


@pytest.mark.anyio
async def test_a_search_outage_still_refuses_a_country_with_nothing_stored(
    tmp_path: Path,
) -> None:
    """The half that must not be relaxed: with no corpus, search was the only recall there was.

    Falling through here would turn *we could not look* into *there is nothing to find*, which is
    the statement DECISIONS entry 18 exists to forbid.
    """

    requests: list[httpx.Request] = []
    resolver, provider = build_resolver(tmp_path, requests, [INDEX])
    resolver.corpus = None
    provider.error = SearchQuotaExhausted("the search account has spent 25.01 against its 25.0")

    with pytest.raises(SearchError):
        await resolver.resolve(destination(), corridor())


@pytest.mark.anyio
async def test_a_thin_corpus_does_not_stop_the_crawl(tmp_path: Path) -> None:
    """A handful of pages is not a map, so the crawl is still the best thing available."""

    requests: list[httpx.Request] = []
    resolver, _ = build_resolver(tmp_path, requests, [INDEX])
    resolver.corpus = corpus_of(padding=0)

    await resolver.resolve(destination(), corridor())

    assert resolver.crawl_fetcher.requested


@pytest.mark.anyio
async def test_a_corridor_that_does_not_crawl_still_reports_a_refusal(tmp_path: Path) -> None:
    """The thing entry 48 says must not be lost as a side effect of a speed change.

    With no crawl, `CrawlFetcher` sees nothing, so every refusal a corridor reports has to come from
    reading the shortlist. Before entry 49 that path reported none at all, and this corridor would
    have gone quiet about an authority refusing it while still answering the traveller.
    """

    requests: list[httpx.Request] = []
    site = handler(requests)

    def refusing(request: httpx.Request) -> httpx.Response:
        if str(request.url).rstrip("/") == DETAIL_INDIA:
            return httpx.Response(403, text="no")
        served: httpx.Response = site(request)  # type: ignore[operator]
        return served

    transport = httpx.MockTransport(refusing)
    resolver, _ = build_resolver(tmp_path, [], [INDEX])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport
    resolver.corpus = corpus_of(padding=DEFAULT_CRAWL_PAGES + 1)

    resolved = await resolver.resolve(destination(), corridor())

    assert resolver.crawl_fetcher.requested == []
    assert host_of(DETAIL_INDIA) in resolved.inaccessible_domains
    assert resolved.inaccessible_urls == [DETAIL_INDIA]


class StubBlockedJudge:
    """An adjudicator that fills no role, then answers the blocked-page question as told."""

    def __init__(self, *, blocked_reply: object) -> None:
        self.blocked_reply = blocked_reply
        self.packets: list[str] = []
        self.prompts: list[str] = []

    async def adjudicate(
        self, system_prompt: str, packet: str, *, usage: object = None
    ) -> RoleAdjudication:
        self.prompts.append(system_prompt)
        self.packets.append(packet)
        if "refused_pages" not in packet:
            # The role call. Fill the checklist from a real candidate and nothing else, so the visa
            # decision is genuinely missing *and* something readable remains to cite — entry 27
            # requires both, and without the second the corridor refuses for a different reason.
            first = json.loads(packet)["candidates"][0]["source_id"]
            return RoleAdjudication(
                choices=[
                    RoleChoice(role="document_checklist", source_id=first, reason="the checklist")
                ]
            )
        if isinstance(self.blocked_reply, Exception):
            raise self.blocked_reply
        assert isinstance(self.blocked_reply, RoleAdjudication)
        return self.blocked_reply


async def resolve_with_judge(tmp_path: Path, judge: StubBlockedJudge) -> ResolvedCorridor:
    """Refuse every visa-decision page, so the blocked-page question is actually asked."""

    site = handler([])
    refused = {DETAIL_INDIA, EXEMPTIONS, f"https://{AUTHORITY}/visa/detail"}

    def refusing(request: httpx.Request) -> httpx.Response:
        if str(request.url).rstrip("/") in refused:
            return httpx.Response(403, text="no")
        served: httpx.Response = site(request)  # type: ignore[operator]
        return served

    transport = httpx.MockTransport(refusing)
    resolver, _ = build_resolver(tmp_path, [], [INDEX, DETAIL_INDIA, MISSION_INDEX])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport
    resolver.adjudicator = judge
    return await resolver.resolve(destination(), corridor())


def blocked_id(url: str) -> str:
    return build_source_id("testland", url, set())


@pytest.mark.anyio
async def test_a_refused_page_is_judged_rather_than_keyword_matched(tmp_path: Path) -> None:
    """DECISIONS entry 57: the one place the scorer was deciding what a page *means*.

    The judged page is asserted to carry no text, because there is none — the authority refused it.
    A packet that ever grew an excerpt field would be inferring content about a page nobody read,
    which is the thing DECISIONS entry 18 forbids outright.
    """

    chosen = blocked_id(DETAIL_INDIA)
    judge = StubBlockedJudge(
        blocked_reply=RoleAdjudication(
            choices=[RoleChoice(role="visa_decision", source_id=chosen, reason="per-nationality")]
        )
    )

    resolved = await resolve_with_judge(tmp_path, judge)

    assert resolved.decision_blocking_urls == [DETAIL_INDIA], (
        "the judged page, and only it — the other two refusals were not chosen"
    )
    assert resolved.decision_is_unverified
    assert resolved.is_usable
    packet = next(p for p in judge.packets if "refused_pages" in p)
    assert "untrusted_content" not in packet, "a refused page has no text and must carry none"
    assert "excerpt" not in packet


@pytest.mark.anyio
async def test_judging_the_refused_pages_fails_closed(tmp_path: Path) -> None:
    """A model outage may cost a blocked-authority plan; it may never invent one.

    Empty is the same outcome as nothing qualifying, so the corridor refuses — which is what this
    project does when it cannot tell. It retries first, for entry 31's reason: a momentary failure
    should not cost a corridor its answer.
    """

    judge = StubBlockedJudge(blocked_reply=AdjudicationError("the request failed"))

    resolved = await resolve_with_judge(tmp_path, judge)

    assert resolved.decision_blocking_urls == []
    assert not resolved.decision_is_unverified
    assert not resolved.is_usable
    assert any("could not be judged" in note for note in resolved.notes), resolved.notes
    assert sum(1 for p in judge.packets if "refused_pages" in p) == 2, "one retry, then give up"


@pytest.mark.anyio
async def test_a_judged_page_the_model_invented_is_discarded(tmp_path: Path) -> None:
    """The application decides what is real, exactly as `validated_choices` does for roles."""

    judge = StubBlockedJudge(
        blocked_reply=RoleAdjudication(
            choices=[RoleChoice(role="visa_decision", source_id="not_a_real_id", reason="made up")]
        )
    )

    resolved = await resolve_with_judge(tmp_path, judge)

    assert resolved.decision_blocking_urls == []
    assert any("which was not one of the refused pages" in note for note in resolved.notes)


# --- the decision behind an official tool -------------------------------------------------------


class StubToolJudge:
    """Fills the checklist, refuses the decision, and names one candidate as the tool that holds it.

    `also_fill_decision` is the case where the model contradicts itself: it names a page that states
    the decision *and* a tool. The tool is then pointless, and carrying it would send a traveller
    off to work out something the plan already says.
    """

    def __init__(self, *, also_fill_decision: bool = False) -> None:
        self.also_fill_decision = also_fill_decision
        self.packets: list[str] = []

    async def adjudicate(
        self, system_prompt: str, packet: str, *, usage: object = None
    ) -> RoleAdjudication:
        self.packets.append(packet)
        candidates = json.loads(packet)["candidates"]
        first = candidates[0]["source_id"]
        choices = [RoleChoice(role="document_checklist", source_id=first, reason="the checklist")]
        if self.also_fill_decision:
            choices.append(
                RoleChoice(role="visa_decision", source_id=first, reason="it states it plainly")
            )
        return RoleAdjudication(
            choices=choices,
            tools=[
                RoleTool(
                    role="visa_decision",
                    source_id=candidates[-1]["source_id"],
                    reason="a step-by-step checker that asks nationality and purpose",
                )
            ],
        )


async def resolve_with_tool_judge(tmp_path: Path, judge: StubToolJudge) -> ResolvedCorridor:
    """Nothing is refused here — that is the whole point. Every page is served and read."""

    transport = httpx.MockTransport(handler([]))  # type: ignore[arg-type]
    resolver, _ = build_resolver(tmp_path, [], [INDEX, DETAIL_INDIA, MISSION_INDEX])
    resolver.crawl_fetcher.transport = transport
    resolver.live_fetcher.transport = transport
    resolver.adjudicator = judge
    return await resolver.resolve(destination(), corridor())


@pytest.mark.anyio
async def test_a_decision_held_by_a_tool_resolves_without_anything_being_blocked(
    tmp_path: Path,
) -> None:
    """The United Kingdom shape from entry 58, end to end. Nothing refused us, so none of the block
    machinery fires — and before entry 59 that meant the corridor refused and discarded a checklist
    it had resolved correctly."""

    resolved = await resolve_with_tool_judge(tmp_path, StubToolJudge())

    assert resolved.inaccessible_urls == [], "nothing was blocked; that is the difference"
    assert resolved.decision_blocking_urls == []
    assert len(resolved.decision_tool_urls) == 1
    assert resolved.decision_is_unverified
    assert resolved.is_usable, "the checklist survives instead of being thrown away with the plan"
    assert "visa_decision" in resolved.unresolved_roles


@pytest.mark.anyio
async def test_a_tool_is_dropped_when_a_page_states_the_decision(tmp_path: Path) -> None:
    """Once a page answers the question, a checker is at best a second route to the same answer.

    The same short-circuit `decision_blocking_urls` gets, for the same reason: a field describing
    something that did not happen is one a later reader will believe.
    """

    resolved = await resolve_with_tool_judge(tmp_path, StubToolJudge(also_fill_decision=True))

    assert "visa_decision" in {role for source in resolved.sources for role in source.roles}
    assert resolved.interactive_tools == []
    assert not resolved.decision_is_unverified
    assert any("was not carried" in note for note in resolved.notes), resolved.notes


def _shortlist_only_resolver(*, shortlist_size: int, shortlist_role_depth: int) -> CorridorResolver:
    """A resolver built only far enough to call `_shortlist`, which asks the crawl what it already
    proved unreadable."""

    return CorridorResolver(
        None,  # type: ignore[arg-type]
        CrawlFetcher(transport=httpx.MockTransport(lambda _: httpx.Response(404))),
        None,  # type: ignore[arg-type]
        shortlist_size=shortlist_size,
        shortlist_role_depth=shortlist_role_depth,
    )


def _candidate(
    url: str, link: dict[str, float], text: dict[str, float] | None = None
) -> CandidatePage:
    return CandidatePage(
        link=PageLink(url=url, text="", heading="", depth=1, discovered_from="seed"),
        link_scores=RoleScores(scores=link),
        text_scores=RoleScores(scores=text) if text is not None else None,
    )


def test_stored_text_wins_a_shortlist_place_the_link_alone_would_not() -> None:
    """The point of the index, as a shortlist assertion.

    The checklist page's anchor scores for the wrong role — which is the real shape, measured on
    `mofa.go.jp/files/000121327.pdf`: 22.0 as `visa_decision`, nothing at all for
    `document_checklist`. With only its link to go on it loses every place to pages the anchor
    scorer likes better, and a page never shortlisted is never fetched and never recovers
    (entry 40).
    """

    resolver = _shortlist_only_resolver(shortlist_size=2, shortlist_role_depth=1)
    loud = _candidate("https://a.gov.example/1", {"visa_decision": 50.0})
    also_loud = _candidate("https://a.gov.example/2", {"visa_decision": 40.0})
    checklist = _candidate("https://a.gov.example/checklist.pdf", {"visa_decision": 5.0})

    without_text = {c.link.url for c in resolver._shortlist([loud, also_loud, checklist])}
    assert checklist.link.url not in without_text

    checklist.text_scores = RoleScores(scores={"document_checklist": 80.0})
    with_text = {c.link.url for c in resolver._shortlist([loud, also_loud, checklist])}
    assert checklist.link.url in with_text


def test_a_page_the_index_is_silent_about_keeps_its_place() -> None:
    """Holding a page's text must never cost it a place — `combined` lifts and never sinks."""

    resolver = _shortlist_only_resolver(shortlist_size=1, shortlist_role_depth=1)
    strong = _candidate("https://a.gov.example/1", {"visa_decision": 50.0})
    rival = _candidate("https://a.gov.example/2", {"visa_decision": 40.0})

    strong.text_scores = RoleScores(scores={})
    chosen = {c.link.url for c in resolver._shortlist([strong, rival])}

    assert chosen == {strong.link.url}


@pytest.mark.anyio
async def test_an_empty_account_is_not_retried(tmp_path: Path) -> None:
    """A retry exists for momentary failures. A second billed call cannot fix an empty account."""

    class OutOfCredit:
        def __init__(self) -> None:
            self.calls = 0

        async def adjudicate(
            self, system_prompt: str, packet: str, *, usage: object = None
        ) -> RoleAdjudication:
            self.calls += 1
            raise AdjudicationQuotaExhausted("The OpenAI account is out of credit")

    adjudicator = OutOfCredit()
    resolver, _ = build_resolver(tmp_path, [], [INDEX])
    resolver.adjudicator = adjudicator

    resolved = await resolver.resolve(destination(), corridor())

    assert adjudicator.calls == 1, "the second call is billed and cannot succeed"
    assert not resolved.is_usable


def test_stored_text_may_not_rank_a_set_it_barely_covers() -> None:
    """A signal only some candidates carry orders them by who has it, not by what it says.

    Measured on `japan/IN/GB`: the index held text for 115 of 860 candidates, spread by whatever
    hosts the crawl reached — 90% of `evisa.mofa.go.jp`, 5% of `www.mofa.go.jp`, and **0% of
    `www.uk.emb-japan.go.jp`, the post serving a traveller applying from Britain**. All eleven
    pages the lift added to the shortlist had index text; the eleven displaced included that post's
    own fee and checklist pages. Corpus-only, three runs each way, it cost two roles every time.

    `combined` already refuses to let stored text lower a score. That protects the score and not the
    place: a shortlist is finite, so lifting some candidates displaces others.
    """

    resolver = _shortlist_only_resolver(shortlist_size=2, shortlist_role_depth=1)

    assert not resolver._text_scoring_is_fair(115, 860)
    assert resolver._text_scoring_is_fair(500, 860)
    assert not resolver._text_scoring_is_fair(0, 0), "an empty candidate set is not full coverage"


def test_stored_text_may_refile_a_page_but_never_rescue_an_irrelevant_one() -> None:
    """Text re-files a page across roles; it does not create a candidate from one the link rejected.

    The case this exists for is Japan's checklist PDF: 22.0 as `visa_decision`, nothing at all for
    `document_checklist`, and its body says "Checklist ... Documents to be submitted". That is a
    re-filing, and the link scorer had already judged the page relevant.

    A page scoring zero for *every* role is a different claim, and `0.6 * text` overrides it
    outright when the link score is zero. Measured on `japan/IN/GB`, that lifted MOFA's long-stay
    category pages — Professor, Student, Cultural activities — into a tourism shortlist at +39,
    because a long-stay page also says "necessary documents".
    """

    misfiled = _candidate(
        "https://a.gov.example/checklist.pdf",
        {"visa_decision": 22.0},
        {"document_checklist": 73.5},
    )
    assert misfiled.combined("document_checklist") > 0

    irrelevant = _candidate(
        "https://a.gov.example/long/professor.html", {}, {"document_checklist": 65.0}
    )
    assert irrelevant.combined("document_checklist") == 0.0


def test_a_link_stating_who_must_hold_a_visa_scores_for_the_decision_two_hops_deep() -> None:
    """TODO item 70, entry 200. Belgium links its list as "List of third countries that are required
    to hold a visa"; the lexicon read only the word "visa" (+6), so two hops from a build seed the
    link scored −14 and the list never reached the selector. It states the answer, so it scores as
    one — for a page or a document alike."""

    from visa_research_agent.discovery.lexicon import get_country_registry, get_lexicon
    from visa_research_agent.discovery.scoring import score_link
    from visa_research_agent.discovery.search import resolve_corridor_countries

    link = PageLink(
        url="https://dofi.example.be/files/2024-12/list-third-countries.pdf",
        text="List of third countries that are required to hold a visa",
        heading="",
        depth=2,
        discovered_from="https://dofi.example.be/visa/list",
    )
    corridor = Corridor(
        destination_slug="belgium", passport_nationality="BD", applying_from="AE", purpose="tourism"
    )
    nationality, residence = resolve_corridor_countries(corridor, get_country_registry())
    scores = score_link(link, corridor, get_lexicon(), nationality, residence)

    assert scores.score_for("visa_decision") > 0


@pytest.mark.anyio
async def test_the_checklist_a_read_page_links_to_is_read_as_well(tmp_path: Path) -> None:
    """Switzerland's India page lists its checklists as PDFs labelled "Business" and "Tourist",
    and a corridor answering from the store read the page and never the PDF (entry 210). The one
    for this traveller's purpose is now read; another purpose's, an off-domain one and one for a
    diplomatic passport are not."""

    requests: list[httpx.Request] = []
    resolver, _provider = build_resolver(tmp_path, requests, [])
    hub = CandidatePage(
        link=PageLink(url=CHECKLIST_HUB, text="Procedure", heading="", depth=0, discovered_from=""),
        link_scores=RoleScores(),
    )
    heading = "Checklists"
    fetched = FetchedShortlist(
        candidates=[hub],
        by_id={"hub": hub},
        contents={"hub": "Application procedure"},
        links={
            "hub": [
                DocumentLink(url=BUSINESS_PDF, text="Business", heading=heading),
                DocumentLink(url=TOURIST_PDF, text="Tourist", heading=heading),
                DocumentLink(url=f"{OFF_DOMAIN}/tourist.pdf", text="Tourist", heading=heading),
                DocumentLink(
                    url=f"https://{AUTHORITY}/files/official.pdf",
                    text="Official passports checklist",
                    heading=heading,
                ),
            ]
        },
    )
    lexicon = get_lexicon()
    registry = get_country_registry()

    def score(link: PageLink) -> RoleScores:
        return score_link(link, corridor(), lexicon, registry.require("IN"), registry.require("GB"))

    def reject(link: PageLink) -> str | None:
        return wrong_audience(link, corridor(), lexicon)

    notes: list[str] = []
    merged = await resolver._follow_document_links(
        destination(), corridor(), registry.require("IN"), fetched, score, reject, notes
    )

    asked = {str(request.url) for request in requests}
    assert asked == {TOURIST_PDF}
    assert {candidate.link.url for candidate in merged.candidates} == {CHECKLIST_HUB, TOURIST_PDF}
    assert "hub" in merged.by_id and len(merged.by_id) == 2, "ids from the two reads never collide"
    assert any("checklist documents linked from pages" in note for note in notes)


def test_a_page_s_document_links_are_read_off_its_markup() -> None:
    """An ordinary link to a document, and a script link labelled with a file name — South Korea's
    consulate attachments (entry 211) — whose address is read from the call, never run."""

    html = (
        "<h2>Checklists</h2>"
        '<a href="/files/tourist.pdf">Tourist</a>'
        "<a href=\"javascript:f_down('./down.do?id=1')\">Korean Visa checklist.pdf</a>"
        "<a href=\"javascript:showMenu('./menu.do')\">Menu</a>"
        '<a href="/visa/other.html">Other page</a>'
        '<a href="/files/tourist.pdf#page=2">Tourist again</a>'
    )

    links = extract_document_links(html, "https://uk.embassy.gov.example/visa/procedure.html")

    assert links == [
        DocumentLink(
            url="https://uk.embassy.gov.example/files/tourist.pdf",
            text="Tourist",
            heading="Checklists",
        ),
        DocumentLink(
            url="https://uk.embassy.gov.example/visa/down.do?id=1",
            text="Korean Visa checklist.pdf",
            heading="Checklists",
        ),
    ]


@pytest.mark.anyio
async def test_a_followed_checklist_that_cannot_be_read_is_named_with_its_link(
    tmp_path: Path,
) -> None:
    """Entry 211, the owner's decision: a checklist we believe exists and could not read is named
    so the traveller can open it — South Korea's sits behind a waiting room. Nothing is read from
    it; the plan only carries its address, which our code read off the government page."""

    requests: list[httpx.Request] = []
    resolver, _provider = build_resolver(tmp_path, requests, [])
    hub = CandidatePage(
        link=PageLink(url=CHECKLIST_HUB, text="Notice", heading="", depth=0, discovered_from=""),
        link_scores=RoleScores(),
    )
    missing = f"https://{MISSION}/files/visa-checklist.pdf"
    fetched = FetchedShortlist(
        candidates=[hub],
        by_id={"hub": hub},
        contents={"hub": "Notice"},
        links={"hub": [DocumentLink(url=missing, text="Korean Visa checklist.pdf")]},
    )
    lexicon = get_lexicon()
    registry = get_country_registry()

    def score(link: PageLink) -> RoleScores:
        return score_link(link, corridor(), lexicon, registry.require("IN"), registry.require("GB"))

    merged = await resolver._follow_document_links(
        destination(), corridor(), registry.require("IN"), fetched, score, lambda _: None, []
    )

    assert [candidate.link.url for candidate in merged.followed] == [missing]
    assert missing not in {candidate.link.url for candidate in merged.candidates}
    named = unread_checklist_pages(
        merged.failures,
        {candidate.link.url: candidate for candidate in merged.followed},
        checklist_filled=False,
        already_named=[],
        corridor=corridor(),
        lexicon=get_lexicon(),
    )
    assert [str(page.attempted_url) for page in named] == [missing]


@pytest.mark.anyio
async def test_a_document_link_sent_to_a_waiting_room_says_the_site_was_busy(
    tmp_path: Path,
) -> None:
    """Entry 219. South Korea's checklist download sends our client to the ministry's waiting room,
    and "returned a web page instead" hid that the site was only busy. Said as what it is, and
    never waited out or retried past."""

    requests: list[httpx.Request] = []
    resolver, _provider = build_resolver(tmp_path, requests, [])
    hub = CandidatePage(
        link=PageLink(url=CHECKLIST_HUB, text="Notice", heading="", depth=0, discovered_from=""),
        link_scores=RoleScores(),
    )
    fetched = FetchedShortlist(
        candidates=[hub],
        by_id={"hub": hub},
        contents={"hub": "Notice"},
        links={
            "hub": [
                DocumentLink(
                    url=MISSION_QUEUED_DOWNLOAD,
                    text="Korean Visa checklist(w.e.f. 24.08.2026).pdf",
                )
            ]
        },
    )
    lexicon = get_lexicon()
    registry = get_country_registry()

    def score(link: PageLink) -> RoleScores:
        return score_link(link, corridor(), lexicon, registry.require("IN"), registry.require("GB"))

    merged = await resolver._follow_document_links(
        destination(), corridor(), registry.require("IN"), fetched, score, lambda _: None, []
    )

    [failure] = [f for f in merged.failures if str(f.attempted_url) == MISSION_QUEUED_DOWNLOAD]
    assert "waiting room" in failure.detail
    assert [str(r.url) for r in requests].count(MISSION_QUEUED_DOWNLOAD) == 1, "never retried"


@pytest.mark.anyio
async def test_a_document_link_that_returns_a_web_page_is_unread_and_named(tmp_path: Path) -> None:
    """South Korea's checklist address answered with the site's front page, which read cleanly and
    held nothing (entry 212). A link labelled as a document must come back as one; otherwise it
    is unread, says why, and can be named with its link."""

    requests: list[httpx.Request] = []
    resolver, _provider = build_resolver(tmp_path, requests, [])
    hub = CandidatePage(
        link=PageLink(url=CHECKLIST_HUB, text="Notice", heading="", depth=0, discovered_from=""),
        link_scores=RoleScores(),
    )
    fetched = FetchedShortlist(
        candidates=[hub],
        by_id={"hub": hub},
        contents={"hub": "Notice"},
        links={"hub": [DocumentLink(url=MISSION_SPOUSE, text="Tourist visa checklist.pdf")]},
    )
    lexicon = get_lexicon()
    registry = get_country_registry()

    def score(link: PageLink) -> RoleScores:
        return score_link(link, corridor(), lexicon, registry.require("IN"), registry.require("GB"))

    merged = await resolver._follow_document_links(
        destination(), corridor(), registry.require("IN"), fetched, score, lambda _: None, []
    )

    assert MISSION_SPOUSE not in {candidate.link.url for candidate in merged.candidates}
    [failure] = [f for f in merged.failures if str(f.attempted_url) == MISSION_SPOUSE]
    assert "returned a web page" in failure.detail
    named = unread_checklist_pages(
        merged.failures,
        {candidate.link.url: candidate for candidate in merged.followed},
        checklist_filled=False,
        already_named=[],
        corridor=corridor(),
        lexicon=get_lexicon(),
    )
    assert [str(page.attempted_url) for page in named] == [MISSION_SPOUSE]


def test_at_most_three_likely_checklists_are_named_best_first() -> None:
    """Australia named six on 2026-09-25, two of them help pages (entry 212). A traveller shown six
    links cannot tell which is the list, so a plan names the three most likely."""

    def unread(index: int) -> tuple[SourceFailure, CandidatePage]:
        url = f"https://{AUTHORITY}/visa/page-{index}.html"
        failure = SourceFailure(
            source_id=f"page_{index}",
            title=f"Page {index}",
            authority="Testland",
            outcome="unusable",
            detail="the page returned too little readable text to trust",
            attempted_url=AnyHttpUrl(url),
        )
        candidate = CandidatePage(
            link=PageLink(url=url, text="Visa checklist", heading="", depth=1, discovered_from=""),
            link_scores=RoleScores(scores={"document_checklist": float(index)}),
        )
        return failure, candidate

    pairs = [unread(index) for index in range(1, 7)]
    named = unread_checklist_pages(
        [failure for failure, _ in pairs],
        {candidate.link.url: candidate for _, candidate in pairs},
        checklist_filled=False,
        already_named=[],
        corridor=corridor(),
        lexicon=get_lexicon(),
    )

    assert [failure.source_id for failure in named] == ["page_6", "page_5", "page_4"]


def test_only_a_failure_waiting_could_change_counts_as_failed_for_now() -> None:
    """Entry 258. Read from the outcome and status, never the sentence (entry 36): a `5xx`, a
    `429` and no answer at all are "not now"; a `403`, a `404` and a `Disallow` are facts about
    the page."""

    def failure(url: str, outcome: str, status: int | None) -> SourceFailure:
        return SourceFailure.model_validate(
            {
                "source_id": url.rsplit("/", 1)[-1],
                "title": "Page",
                "authority": "Authority",
                "outcome": outcome,
                "detail": "It failed.",
                "attempted_url": url,
                "http_status": status,
            }
        )

    failures = [
        failure("https://gov.example/a", "unreachable", 500),
        failure("https://gov.example/b", "unreachable", 520),
        failure("https://gov.example/c", "blocked", 429),
        failure("https://gov.example/d", "unreachable", None),
        failure("https://gov.example/e", "blocked", 403),
        failure("https://gov.example/f", "unusable", 404),
        failure("https://gov.example/g", "disallowed", None),
    ]

    assert failed_for_now(failures) == [
        "https://gov.example/a",
        "https://gov.example/b",
        "https://gov.example/c",
        "https://gov.example/d",
    ]
