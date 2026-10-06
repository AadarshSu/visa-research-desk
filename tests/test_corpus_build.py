"""Building a country's corpus: what it keeps that a corridor would drop.

Offline throughout, against the fake two-host government site. The rules worth pinning are the ones
that distinguish this from `resolver.py`, because a later "simplification" toward sharing that code
would break exactly them. See DECISIONS entry 44.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from discovery_site import (
    ARCHIVED,
    AUTHORITY,
    DETAIL_CHINA,
    DETAIL_INDIA,
    FULL_CHECKLIST,
    INDEX,
    MISSION,
    MISSION_INDEX,
    OFF_DOMAIN,
    TOURISM_CHECKLIST_PDF,
    handler,
)

from visa_research_agent.discovery.corpus import CorpusEntry, CountryCorpus
from visa_research_agent.discovery.corpus_build import (
    CORPUS_EXPANSION_THRESHOLD,
    CORPUS_FAMILY_PATTERN,
    DEFAULT_CORPUS_MISSION_SEEDS,
    add_named_pages,
    all_corpus_queries,
    build_country_corpus,
    is_transient_failure,
    mission_index_seeds,
)
from visa_research_agent.discovery.crawl import CrawlFetcher, LinkCrawler
from visa_research_agent.discovery.lexicon import Country, get_country_registry, get_lexicon
from visa_research_agent.discovery.models import Corridor, RoleScores, SearchResult
from visa_research_agent.discovery.page_text import PageTextStore
from visa_research_agent.discovery.search import SearchError, SearchQuotaExhausted
from visa_research_agent.domain.models import DestinationConfig

NOW = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
TRUSTED = ["immigration.gov.example", "uk.embassy.gov.example"]


def country() -> Country:
    return Country(
        code="XX",
        alpha3="XXX",
        name="Example",
        tlds=[".example"],
        synonyms=[],
        demonyms=[],
        host_labels=["xx"],
        mission_labels=["xx"],
    )


class FakeSearch:
    """Returns fixed results for every query, and records what was asked."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.queries: list[str] = []

    async def search(self, query: str, *, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult(url=url, title="", snippet="", query=query, rank=rank)
            for rank, url in enumerate(self.urls)
        ]


class FailingSearch(FakeSearch):
    """Fails every query containing `fail_on` with the error given, and answers the rest."""

    def __init__(self, urls: list[str], *, fail_on: str, error: SearchError) -> None:
        super().__init__(urls)
        self.fail_on = fail_on
        self.error = error

    async def search(self, query: str, *, count: int) -> list[SearchResult]:
        if self.fail_on in query:
            self.queries.append(query)
            raise self.error
        return await super().search(query, count=count)


async def sleep_none(_: float) -> None:
    return None


@pytest.mark.anyio
async def test_one_failed_search_query_does_not_cost_the_build() -> None:
    """A DNS blip on 2026-08-23 lost Japan's whole build: `search_all` raises on any failed query.

    Right for a corridor, which serves what it searched, and wrong for a corpus, which is additive
    and never claims to be complete. The build goes on without the failed queries and names them
    (entry 162). The reason is the exception's own text, or its type where a timeout left none.
    """

    search = FailingSearch(
        [INDEX], fail_on="business", error=SearchError("The search request failed ()")
    )

    corpus, report = await build_country_corpus(
        country(), TRUSTED, search, fetcher([]), existing=None, now=NOW, maximum_pages=60
    )

    assert corpus.entries, "the queries that answered still built a corpus"
    assert report.failed_queries, "and the ones that did not are named"
    assert all("business" in query for query in report.failed_queries)
    assert len(report.failed_queries) < report.queries


@pytest.mark.anyio
async def test_a_search_account_out_of_credit_still_stops_the_build() -> None:
    """No later query can succeed against an empty account, so going on would only crawl blind."""

    search = FailingSearch([INDEX], fail_on="business", error=SearchQuotaExhausted("out of credit"))

    with pytest.raises(SearchQuotaExhausted):
        await build_country_corpus(
            country(), TRUSTED, search, fetcher([]), existing=None, now=NOW, maximum_pages=60
        )


@pytest.mark.anyio
async def test_a_build_whose_every_query_failed_raises() -> None:
    """A build that searched nothing is not a partial build: "we could not look" stays an error."""

    search = FailingSearch([INDEX], fail_on="site:", error=SearchError("down"))

    with pytest.raises(SearchError, match="every one"):
        await build_country_corpus(
            country(), TRUSTED, search, fetcher([]), existing=None, now=NOW, maximum_pages=60
        )


def fetcher(requests: list[httpx.Request]) -> CrawlFetcher:
    return CrawlFetcher(
        transport=httpx.MockTransport(handler(requests)),  # type: ignore[arg-type]
        host_delay_seconds=0.0,
        sleep=sleep_none,
    )


async def build(
    urls: list[str], *, existing: CountryCorpus | None = None, pages: int = 60
) -> tuple[CountryCorpus, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    corpus, _report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch(urls),
        fetcher(requests),
        existing=existing,
        now=NOW,
        maximum_pages=pages,
    )
    return corpus, requests


def test_the_queries_name_no_nationality_or_residence() -> None:
    """198-valued dimensions stay out: a corpus built for one nationality is not a corpus."""

    queries = all_corpus_queries("Canada", ["canada.ca"])

    assert all(query.startswith("site:canada.ca ") for query in queries)
    for word in ("india", "indian", "british", "united kingdom", "resident"):
        assert not any(word in query.lower() for query in queries), word


def test_every_purpose_is_swept() -> None:
    """The measured gap, and why purpose is not treated like nationality.

    `.../visit-canada/supporting-documents` reached the live corridor run as a seed from
    `site:canada.ca tourism visa documents required`, and the corpus — which asked only
    `visa application documents required` — never saw it. Purpose has four values, so the dimension
    is swept exhaustively rather than dropped, and the corpus stays corridor-independent because it
    then holds every purpose's pages rather than one traveller's.
    """

    queries = all_corpus_queries("Canada", ["canada.ca"])

    for purpose in ("tourism", "business", "study", "transit"):
        assert any(purpose in query for query in queries), purpose
    # The exact phrasing the corridor uses, or the sweep would miss what it is meant to cover.
    assert "site:canada.ca tourism visa documents required" in queries


def test_the_query_list_is_stable_and_free_of_duplicates() -> None:
    """The corpus removes variance; it must not introduce a new source of it."""

    first = all_corpus_queries("Canada", ["canada.ca", "travel.gc.ca"])
    second = all_corpus_queries("Canada", ["canada.ca", "travel.gc.ca"])

    assert first == second
    assert len(first) == len(set(first))


@pytest.mark.anyio
async def test_a_build_records_the_pages_it_reached_with_how_it_got_there() -> None:
    corpus, _ = await build([INDEX])

    assert corpus.country_code == "XX"
    assert corpus.entries, "the crawl reached nothing"
    india = corpus.find("detail/india")
    assert india, "a page two hops in was not recorded"
    assert india[0].depth >= 1
    assert india[0].discovered_from, "how a page was reached is what a later crawl follows"


@pytest.mark.anyio
async def test_a_page_the_crawl_opened_is_recorded_readable_not_unknown() -> None:
    """`readable` was a documented retention tier that no build ever assigned (entry 92).

    It matters because `merge` moves a status up and never down and `unknown` ranks *below*
    `unreadable`, so a page that failed in one build and was read in the next kept the old failure's
    sentence for ever. Twelve France entries claimed a browser challenge "could not be answered
    here" while the page-text index held their bodies — a reason untrue of what was seen.
    """

    # A budget of two so the crawl opens some pages and only records the addresses of others.
    corpus, _ = await build([INDEX], pages=2)

    opened = [entry for entry in corpus.entries if entry.status == "readable"]
    assert opened, "a crawl that opened a page must record that it did"
    assert all(not entry.detail for entry in opened)
    # A page it only saw a link to still says so — an unopened address is a usable candidate and
    # must stay distinguishable from a page somebody read.
    assert any(entry.status == "unknown" for entry in corpus.entries)


@pytest.mark.anyio
async def test_a_later_build_clears_a_failure_the_page_no_longer_has() -> None:
    """The whole point of the tier. A corpus that could never record a success could never correct
    a failure either, because `merge` keeps the higher rank and `unknown` is not one."""

    from datetime import timedelta

    read_this_time = "https://immigration.gov.example/visa/detail/india.html"
    stale = CountryCorpus(
        country_code="XX",
        country_name="Xxland",
        built_at=NOW - timedelta(days=1),
        entries=[
            CorpusEntry(
                url=read_this_time,
                first_seen=NOW - timedelta(days=1),
                last_seen=NOW - timedelta(days=1),
                status="unreadable",
                detail="it asked this client to prove it is a browser (HTTP 403)",
            )
        ],
    )
    corpus, _ = await build([INDEX], existing=stale)

    entry = next(entry for entry in corpus.entries if entry.url == read_this_time)
    assert entry.status == "readable"
    assert entry.detail == ""


@pytest.mark.anyio
async def test_pages_about_other_countries_are_kept() -> None:
    """The sharpest difference from `resolver.py`, and it is deliberate.

    A corridor vetoes a page about another country, correctly — for *that* traveller it is noise.
    The corpus serves every corridor, so China's page is exactly what a later China corridor needs,
    and dropping it here would build a store that can only answer corridors it was not built for.
    """

    corpus, _ = await build([INDEX])

    assert corpus.find("detail/india"), "the India page should be held"
    assert corpus.find("detail/china"), "the China page must not be vetoed out of a corpus"


@pytest.mark.anyio
async def test_archived_paths_are_still_refused() -> None:
    """Not guidance for anybody, so unlike wrong-country it stays a rejection."""

    corpus, _ = await build([INDEX])

    assert not corpus.find("/2019/"), ARCHIVED


@pytest.mark.anyio
async def test_nothing_off_the_trusted_domains_is_ever_requested() -> None:
    corpus, requests = await build([INDEX, OFF_DOMAIN])

    assert all("cheap-visas" not in str(request.url) for request in requests)
    assert not corpus.find("cheap-visas")


@pytest.mark.anyio
async def test_a_second_build_adds_without_removing() -> None:
    """The whole point of the store, exercised end to end rather than only on the merge."""

    first, _ = await build([INDEX])
    held = {entry.url for entry in first.entries}
    assert held

    # A later crawl that only ever sees the mission host must not lose the authority's pages.
    second, _ = await build([MISSION_INDEX], existing=first)

    assert held.issubset({entry.url for entry in second.entries})
    assert len(second.entries) >= len(first.entries)


@pytest.mark.anyio
async def test_the_trusted_domains_are_refreshed_but_entries_are_not_filtered() -> None:
    """Trust is applied when the corpus is read, so a build records the set it used and no more."""

    first, _ = await build([INDEX])
    narrowed = first.model_copy(update={"trusted_domains": ["immigration.gov.example"]})

    allowed = narrowed.entries_within(["immigration.gov.example"])

    assert all(AUTHORITY in entry.url for entry in allowed)
    assert len(narrowed.entries) >= len(allowed), "narrowing must not delete what was found"


@pytest.mark.anyio
async def test_a_build_reports_what_it_did() -> None:
    requests: list[httpx.Request] = []
    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX]),
        fetcher(requests),
        existing=None,
        now=NOW,
        maximum_pages=60,
    )

    assert report.country_code == "XX"
    assert report.queries == len(all_corpus_queries("Example", TRUSTED))
    assert report.seeds >= 1
    assert report.added == report.total, "a first build adds everything it found"


@pytest.mark.anyio
async def test_the_corpus_stores_no_scores() -> None:
    """Scoring is corridor-dependent, so freezing it would freeze the half that must stay live."""

    corpus, _ = await build([INDEX])

    assert corpus.entries
    assert not any(hasattr(entry, "score") for entry in corpus.entries)
    # What it does keep is what a later corridor needs to score the page for itself.
    entry = next(item for item in corpus.entries if item.url == DETAIL_INDIA or item.link_text)
    link = entry.to_link()
    assert link.url == entry.url
    assert isinstance(link.text, str)


@pytest.mark.anyio
async def test_china_and_india_pages_both_survive_a_rebuild() -> None:
    first, _ = await build([INDEX])
    second, _ = await build([INDEX], existing=first)

    urls = {entry.url for entry in second.entries}
    assert DETAIL_INDIA in urls
    assert DETAIL_CHINA in urls
    assert second.find("detail/india")[0].times_seen == 2


@pytest.mark.anyio
async def test_a_host_that_gives_the_build_nothing_is_named() -> None:
    """The gap that was invisible, and stayed invisible because nothing could see it.

    A seeded host whose own fetch fails leaves at most an `unreadable` seed entry — before entry 161
    not even that — so without this report it leaves no trace a reader would notice.
    Japan's London embassy went missing through a transient `403` during a build, and the corpus has
    lacked the whole host ever since while live search returns it and a live corridor reads a
    document checklist from it. DECISIONS entry 77.
    """

    requests: list[httpx.Request] = []
    site = handler(requests)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == MISSION and request.url.path != "/robots.txt":
            return httpx.Response(403, text="Access Denied")
        response: httpx.Response = site(request)  # type: ignore[operator]
        return response

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, MISSION_INDEX]),
        CrawlFetcher(
            transport=httpx.MockTransport(handle),
            host_delay_seconds=0.0,
            sleep=sleep_none,
        ),
        existing=None,
        now=NOW,
        maximum_pages=60,
    )

    assert report.total, "the readable host still produced a corpus"
    assert MISSION in report.lost_hosts
    assert report.lost_host_outcomes[MISSION] == "blocked"
    # The typed outcome is what a count may rest on; the sentence is only ever repeated.
    assert "403" in report.lost_hosts[MISSION]
    # A host the build did read is not "lost", however many of its individual pages failed.
    assert AUTHORITY not in report.lost_hosts


@pytest.mark.anyio
async def test_a_search_seed_nothing_links_to_is_kept_as_an_entry() -> None:
    """A page the build's own search found is kept, not only the pages that link to it.

    `crawl` returns what it found on pages, never its seeds, so a seed nothing else linked to was
    dropped: 75% of Norway's seeds, 80% of Thailand's and 59% of Japan's were absent from their
    corpora, among them pages live corridors could get only from search (entry 161).
    `FULL_CHECKLIST` is linked from nothing in the fixture, which is exactly that shape.
    """

    corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, FULL_CHECKLIST, ARCHIVED]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
    )

    kept = corpus.find("tourism-checklist.html")
    assert [entry.url for entry in kept] == [FULL_CHECKLIST]
    assert kept[0].depth == 0
    assert kept[0].status == "readable"
    assert kept[0].discovered_from.startswith("site:"), "where it came from is the query"
    assert report.seeds_kept >= 1
    # A superseded page is refused as a seed exactly as it is as a link.
    assert ARCHIVED not in {entry.url for entry in corpus.entries}
    # Kept seeds are not crawl reach, so they must not dilute the shallow-crawl measure.
    assert 0 not in report.by_depth


@pytest.mark.anyio
async def test_a_pdf_seed_is_kept_and_read_for_its_text(tmp_path: Path) -> None:
    """A PDF seed was lost twice: the crawl refuses PDFs, and the PDF pass read only linked ones.

    Norway's January 2024 tourist checklist is a PDF that the build's own search returns and the
    corpus did not hold (entry 161).
    """

    index = PageTextStore(tmp_path)

    corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, TOURISM_CHECKLIST_PDF]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
        page_text=index,
    )

    assert [entry.depth for entry in corpus.find("tourism-checklist.pdf")] == [0]
    assert report.pdfs_read == 1
    matches = index.rank(
        "XX",
        role="document_checklist",
        corridor=Corridor(
            destination_slug="example",
            passport_nationality="IN",
            applying_from="GB",
            purpose="tourism",
        ),
        nationality=get_country_registry().require("IN"),
        lexicon=get_lexicon(),
    )
    assert TOURISM_CHECKLIST_PDF in [match.url for match in matches]


def test_only_a_failure_that_said_nothing_about_the_page_is_asked_again() -> None:
    """Entry 207. A busy or unreachable host said nothing; a refusal, a `Disallow`, a challenge, a
    missing page, a bad certificate and a name that does not resolve each did."""

    transient = [
        "it refused automated retrieval (HTTP 429), so its guidance could not be independently "
        "verified here",
        "it answered HTTP 502",
        "www.mfa.gov.cn had already failed to answer 6 times in a row in this run, so it was not "
        "asked again",
        "its robots.txt answered HTTP 503, so whether this client may fetch it is unknown",
        "the request failed (ReadTimeout)",
    ]
    final = [
        "it refused automated retrieval (HTTP 403), so its guidance could not be independently "
        "verified here",
        "it refused automated retrieval (HTTP 401), so its guidance could not be independently "
        "verified here",
        "its robots.txt does not permit this client to fetch it",
        "it asked this client to prove it is a browser (HTTP 403), and that challenge could not "
        "be answered here",
        "it answered HTTP 404",
        "its TLS certificate could not be verified",
        "the request failed ([Errno 8] nodename nor servname provided, or not known)",
        "its text layer is empty",
    ]

    assert all(is_transient_failure(detail) for detail in transient)
    assert not any(is_transient_failure(detail) for detail in final)


@pytest.mark.anyio
async def test_a_page_a_busy_host_refused_is_asked_again_and_a_refusal_is_not(
    tmp_path: Path,
) -> None:
    """Thailand's 2026 revision of its visa exemptions answered `429` in one build and stayed
    unreadable, with no text, because no later build's search happened to return it (entry 207).

    The PDF is linked only from a page this build does not reach, so only the retry can read it,
    and once read it must say so: `merge` never moves a status down, and a PDF the PDF pass read
    used to be recorded `unknown`, which ranks below the old failure."""

    refused = f"https://{AUTHORITY}/visa/refused.html"
    existing = CountryCorpus(
        country_code="XX",
        country_name="Example",
        trusted_domains=TRUSTED,
        built_at=NOW,
        entries=[
            CorpusEntry(
                url=TOURISM_CHECKLIST_PDF,
                title="Tourism visa checklist",
                link_text="Tourism visa checklist",
                depth=2,
                discovered_from=FULL_CHECKLIST,
                first_seen=NOW,
                last_seen=NOW,
                status="unreadable",
                detail="it refused automated retrieval (HTTP 429), so its guidance could not be "
                "independently verified here",
            ),
            CorpusEntry(
                url=refused,
                title="Visa checklist",
                link_text="Visa checklist",
                depth=1,
                discovered_from=INDEX,
                first_seen=NOW,
                last_seen=NOW,
                status="unreadable",
                detail="it refused automated retrieval (HTTP 403), so its guidance could not be "
                "independently verified here",
            ),
        ],
    )
    requests: list[httpx.Request] = []

    corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX]),
        fetcher(requests),
        existing=existing,
        now=NOW,
        maximum_pages=60,
        page_text=PageTextStore(tmp_path),
    )

    [pdf] = corpus.find("tourism-checklist.pdf")
    assert pdf.status == "readable" and pdf.detail == ""
    assert pdf.depth == 2, "asking again says nothing new about how the page is reached"
    assert report.retry_seeds == 1
    assert refused not in {str(request.url) for request in requests}
    [still_refused] = corpus.find("refused.html")
    assert still_refused.status == "unreadable"


@pytest.mark.anyio
async def test_a_build_keeps_the_text_of_the_pages_it_read(tmp_path: Path) -> None:
    """Two stores written from one crawl, and the text costs no extra request.

    The corpus answers *which pages exist*; the index answers *what they say*. The assertion that
    matters is the last one: no page was fetched twice to fill the second store.
    """

    requests: list[httpx.Request] = []
    index = PageTextStore(tmp_path)

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, FULL_CHECKLIST]),
        fetcher(requests),
        existing=None,
        now=NOW,
        maximum_pages=60,
        page_text=index,
    )

    assert report.indexed_text > 0
    assert index.count("XX") == report.indexed_text
    fetched = [str(request.url) for request in requests if "robots.txt" not in str(request.url)]
    assert len(fetched) == len(set(fetched))


@pytest.mark.anyio
async def test_a_build_given_no_index_writes_none(tmp_path: Path) -> None:
    """The corpus must still be buildable alone, and nothing may be created on the side."""

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
    )

    assert report.indexed_text == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_a_build_reads_the_pdfs_a_crawl_will_not_follow(tmp_path: Path) -> None:
    """A PDF is a destination, so the crawl skips it — and authorities publish checklists as PDFs.

    26% of Japan's corpus is PDFs, and the page that fills `document_checklist` for japan/IN/GB is
    one. The second pass reads them for text only; nothing follows a link out of one.
    """

    index = PageTextStore(tmp_path)

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, FULL_CHECKLIST]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
        page_text=index,
    )

    assert report.pdfs_read == 1
    matches = index.rank(
        "XX",
        role="document_checklist",
        corridor=Corridor(
            destination_slug="example",
            passport_nationality="IN",
            applying_from="GB",
            purpose="tourism",
        ),
        nationality=get_country_registry().require("IN"),
        lexicon=get_lexicon(),
    )
    assert TOURISM_CHECKLIST_PDF in [match.url for match in matches]


@pytest.mark.anyio
async def test_a_pdf_whose_text_layer_holds_nul_bytes_does_not_cost_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unstorable page must never discard a whole country's crawl.

    China, 2026-08-30: a PDF's text layer carried NUL bytes, `StoredPage` refused them, and the
    `ValidationError` came out of `_read_pdfs` — which runs *after* the crawl, so the corpus was
    never written and eighteen minutes of crawling went with it. The characters are dropped now,
    which changes no guidance: they carry no meaning and this text only ever ranks (entry 78).
    """

    index = PageTextStore(tmp_path)

    async def text_with_nuls(
        self: CrawlFetcher,
        client: httpx.AsyncClient,
        url: str,
        destination: object,
        *,
        maximum_characters: int,
    ) -> str:
        return "Checklist\x00\x00 of documents required for a tourism visa\ud800"

    monkeypatch.setattr(CrawlFetcher, "fetch_pdf_text", text_with_nuls)

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX, FULL_CHECKLIST]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
        page_text=index,
    )

    assert report.pdfs_read == 1


@pytest.mark.anyio
async def test_a_build_follows_links_the_request_path_would_not() -> None:
    """`expansion_threshold` is a latency compromise, and this job has no latency budget.

    At the request path's 10.0 it excluded 91% of Japan's corpus from ever being read, so their
    text could never enter the index. The budgets still bound the crawl and the frontier is still
    best-first; this only decides what fills the remainder.
    """

    assert (
        CORPUS_EXPANSION_THRESHOLD
        < LinkCrawler(fetcher([]), lambda _: RoleScores()).expansion_threshold
    )


# --- The mission index a build recorded and never opened (DECISIONS entry 137) ---------------


def entry(url: str, *, text: str = "", depth: int = 1, status: str = "unknown") -> CorpusEntry:
    return CorpusEntry(
        url=url,
        link_text=text,
        depth=depth,
        status=status,  # type: ignore[arg-type]
        first_seen=NOW,
        last_seen=NOW,
    )


def held(*entries: CorpusEntry) -> CountryCorpus:
    return CountryCorpus(
        country_code="XX",
        country_name="Example",
        trusted_domains=TRUSTED,
        built_at=NOW,
        entries=list(entries),
    )


def destination() -> DestinationConfig:
    return DestinationConfig(
        slug="example",
        display_name="Example",
        route_type="national",
        implementation_status="available",
        trusted_domains=TRUSTED,
    )


def seeds_from(corpus: CountryCorpus, *, limit: int = DEFAULT_CORPUS_MISSION_SEEDS) -> list[str]:
    slugs = frozenset(other.slug for other in get_country_registry().countries)
    return mission_index_seeds(corpus, destination(), slugs=slugs, limit=limit)


def test_the_index_of_a_countrys_own_posts_is_promoted_to_a_seed() -> None:
    """The measured case, in miniature.

    Australia's corpus records `…/our-embassies-and-consulates-overseas` at depth 1 and never opens
    it; behind it are 194 per-country mission pages and, one hop further, the post that serves the
    country a traveller applies from. Entry 137.
    """

    corpus = held(
        entry(f"https://{AUTHORITY}/about/our-embassies-and-consulates-overseas"),
        entry(f"https://{AUTHORITY}/visa/fees.html", text="Visa fees"),
    )

    assert seeds_from(corpus) == [
        f"https://{AUTHORITY}/about/our-embassies-and-consulates-overseas"
    ]


def test_a_member_of_the_family_is_not_mistaken_for_the_index_of_it() -> None:
    """`mfa.bg/en/embassyinfo/{country}` matches the words 62 times over and is the list's content.

    Seeding one of those spends a fetch on a page a corridor could already reach, and would crowd
    out the one page that names all of them. `country_family_keys` is what tells them apart, and it
    is the same function the family reservation groups on.
    """

    index = f"https://{AUTHORITY}/embassyinfo"
    corpus = held(
        entry(f"{index}/india"),
        entry(f"{index}/china"),
        entry(index),
    )

    assert seeds_from(corpus) == [index]


def test_a_page_the_last_build_never_opened_comes_first() -> None:
    """A page already read has contributed whatever it links to; a recorded one has not."""

    read = f"https://{AUTHORITY}/a/embassies-abroad"
    skipped = f"https://{AUTHORITY}/z/embassies-abroad"
    corpus = held(entry(read, status="readable"), entry(skipped))

    assert seeds_from(corpus) == [skipped, read]


def test_the_promotion_is_bounded_and_deterministic() -> None:
    """The pattern is noisy in the tail — Iceland matches 1,128 entries — so the cap does the work.

    Two builds of the same country must also ask the same things in the same order, which is why
    the ordering falls back to the address rather than to corpus order.
    """

    corpus = held(*(entry(f"https://{AUTHORITY}/{n}/missions-abroad") for n in range(9, 0, -1)))

    first = seeds_from(corpus, limit=3)
    assert first == seeds_from(corpus, limit=3)
    assert first == [f"https://{AUTHORITY}/{n}/missions-abroad" for n in (1, 2, 3)]


def test_a_country_with_no_corpus_gets_none_of_this() -> None:
    """Nine of the 53 record no recognisable index, and a first build records nothing at all.

    Both are ordinary and neither is a failure; `CorpusBuild.mission_seeds` is what tells the two
    apart from a build that simply found one.
    """

    assert seeds_from(held()) == []


def test_the_family_gate_admits_a_mission_family_and_still_refuses_travel_advice() -> None:
    """Entry 88's gate refused the largest per-traveller family Australia publishes.

    `…/missions/Pages/australian-embassy-{}` carries none of `visa`, `permit`, `immigrat`,
    `consular`, `checklist` or `entry`, so its 194 members went to the ordinary frontier where a
    bare country name scores at the floor. Widened for the mission words, and measured over all 53
    corpora before shipping: five families cross and nothing else, none of them travel advice.
    """

    assert CORPUS_FAMILY_PATTERN.search("/about-us/our-locations/missions/pages/embassy-{}")
    assert CORPUS_FAMILY_PATTERN.search("https://mfa.example/en/embassyinfo/{}")
    assert not CORPUS_FAMILY_PATTERN.search("https://travel.example/destinations/{}")
    assert not CORPUS_FAMILY_PATTERN.search("https://mofa.example/region/asia/{}")


@pytest.mark.anyio
async def test_a_build_seeds_what_the_last_one_recorded_and_skipped() -> None:
    """The whole point: the address is already in the store, so it costs no search to reach."""

    previous = held(entry(MISSION_INDEX.replace("index.html", "our-missions-abroad.html")))

    _corpus, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX]),
        fetcher([]),
        existing=previous,
        now=NOW,
        maximum_pages=60,
    )

    assert report.mission_seeds == 1
    assert report.seeds == 2


@pytest.mark.anyio
async def test_the_build_report_says_what_its_rules_threw_away() -> None:
    """Entry 200. Malta's visa lists were rejected as archived in every build and the report never
    said so; the rule's reason was recorded on the crawler and dropped. It is now counted per rule,
    with the addresses that say "visa" first."""

    import io

    from visa_research_agent.discovery.cli import print_corpus_build

    _, report = await build_country_corpus(
        country(),
        TRUSTED,
        FakeSearch([INDEX]),
        fetcher([]),
        existing=None,
        now=NOW,
        maximum_pages=60,
    )

    archived = "the path marks it as archived or superseded"
    assert report.rejected[archived] >= 1
    assert report.rejected_about_visas[archived] >= 1
    assert ARCHIVED in report.rejected_examples[archived]
    printed = io.StringIO()
    print_corpus_build(report, printed)
    assert archived in printed.getvalue() and ARCHIVED in printed.getvalue()


# --- adding a page a person named, with no crawl (DECISIONS entry 265) -----------------------


@pytest.mark.anyio
async def test_a_named_page_is_added_to_the_corpus_and_the_text_index(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    index = PageTextStore(tmp_path)
    existing, _ = await build([INDEX])
    before = {entry.url for entry in existing.entries}

    corpus, report = await add_named_pages(
        country(),
        TRUSTED,
        [FULL_CHECKLIST],
        fetcher(requests),
        existing=existing,
        page_text=index,
        now=NOW,
    )

    assert report.stored == [FULL_CHECKLIST] and not report.failed
    assert corpus is not None
    added = corpus.find(FULL_CHECKLIST)
    assert [entry.status for entry in added] == ["readable"]
    assert added[0].discovered_from.startswith("named by a person")
    # Additive, like any build: nothing the corpus held is lost.
    assert before <= {entry.url for entry in corpus.entries}
    assert index.count("XX") == 1
    # One page, no crawl: only the named page was requested.
    assert [str(request.url).rstrip("/") for request in requests] == [FULL_CHECKLIST]


@pytest.mark.anyio
async def test_a_named_page_off_the_trusted_domains_is_never_requested(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    index = PageTextStore(tmp_path)

    corpus, report = await add_named_pages(
        country(),
        TRUSTED,
        [OFF_DOMAIN],
        fetcher(requests),
        existing=None,
        page_text=index,
        now=NOW,
    )

    assert corpus is None
    assert "trusted domains" in next(iter(report.failed.values()))
    assert requests == []
    assert not index.has("XX")


@pytest.mark.anyio
async def test_a_named_page_robots_txt_disallows_is_not_stored(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    host = httpx.URL(FULL_CHECKLIST).host
    disallowing = CrawlFetcher(
        transport=httpx.MockTransport(
            handler(requests, robots={host: "User-agent: *\nDisallow: /"})  # type: ignore[arg-type]
        ),
        host_delay_seconds=0.0,
        sleep=sleep_none,
    )

    corpus, report = await add_named_pages(
        country(),
        TRUSTED,
        [FULL_CHECKLIST],
        disallowing,
        existing=None,
        page_text=PageTextStore(tmp_path),
        now=NOW,
    )

    assert corpus is None
    assert list(report.failed) == [FULL_CHECKLIST]
    assert requests == []
