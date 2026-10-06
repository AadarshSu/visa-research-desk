"""Building a country's page corpus offline, with no traveller and no latency budget.

DECISIONS entry 44. The request path crawls under a stopwatch: forty pages, two hops, and a seed
frontier that in practice spends the whole allowance at depth 0. That is a compromise a sixty-second
request forces, not a judgement about how deep the answers are — Canada's answering page sits at
depth 1, and the ones still being lost are deeper. This job has no such bound, and that is the whole
of why it is expected to beat the request path rather than merely cache it.

Three things it does differently from `resolver.py`, each deliberate.

**No traveller.** Queries name the destination and nothing else, and links are scored with
`score_role_vocabulary` — the corridor-independent half — read with the link's surroundings
(`score_link_in_context`, entry 189). A corpus guided by one nationality's
vocabulary would be a corpus quietly built for that nationality.

**It keeps pages about other countries.** `resolver.py` vetoes those, correctly: for one corridor a
page about Brazil is noise. For a *corpus* it is the opposite — Canada's per-nationality pages are
exactly what a later India or Nigeria corridor needs, and vetoing them here would build a store that
can only answer the corridors nobody has a page for. Archived paths and site furniture are still
rejected, because those are not guidance for anybody.

**It never deletes.** The merge in `corpus.py` is additive; this job only ever hands it what it
found. A crawl that finds less than last time is ordinary, and treating that as a withdrawal would
rebuild the exact failure the corpus exists to prevent.
"""

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any, get_args

import httpx
from pydantic import Field

from visa_research_agent.config.loader import get_service_providers
from visa_research_agent.discovery.corpus import CorpusEntry, CountryCorpus, merge
from visa_research_agent.discovery.crawl import (
    DEFAULT_KEPT_TEXT_CHARACTERS,
    CrawlFetcher,
    LinkCrawler,
    page_title_of,
)
from visa_research_agent.discovery.lexicon import (
    Country,
    CountryRegistry,
    Lexicon,
    get_country_registry,
    get_lexicon,
)
from visa_research_agent.discovery.models import CandidatePage, PageLink, RoleScores, SearchResult
from visa_research_agent.discovery.page_text import PageTextStore, StoredPage
from visa_research_agent.discovery.scoring import (
    is_archived,
    is_boilerplate,
    score_link_in_context,
)
from visa_research_agent.discovery.search import (
    DEFAULT_SEARCH_CONCURRENCY,
    SearchError,
    SearchProvider,
    SearchQuotaExhausted,
    usable_results,
)
from visa_research_agent.discovery.urls import (
    canonicalise_url,
    country_family_keys,
    is_crawlable,
    is_pdf_url,
)
from visa_research_agent.domain.models import (
    DestinationConfig,
    FailureOutcome,
    ServiceProviderRegistry,
    StrictModel,
    TravelPurpose,
)
from visa_research_agent.domain.trust import host_of
from visa_research_agent.research.live_sources import clean_source_html
from visa_research_agent.research.tls import build_ssl_context

# Far above the request path's forty, and **it has to exceed the seed count or the crawl never
# crawls**. Measured 2026-08-22: Canada produced 203 seeds against a 200-page budget, so the whole
# allowance went on fetching seeds — 1,032 of 1,071 entries sat at depth 1 and only 39 deeper. The
# offline job's entire advantage over the request path is that it can go deeper, and at 200 it was
# not going anywhere. `depth_is_exercised` below exists so that failure is visible rather than
# inferred from a distribution nobody prints.
DEFAULT_CORPUS_PAGES = 1_200
# Three hops rather than two. Japan's checklist was found at depth 2 and the request path cannot
# reliably reach it; anything the corpus is *for* lives at least that far in.
DEFAULT_CORPUS_DEPTH = 3
# Per host, so one large ministry portal cannot spend the whole allowance before a mission site is
# reached. Same reasoning as the crawl's own budget, with more room.
DEFAULT_CORPUS_PAGES_PER_HOST = 400
# Below this share of pages beyond depth 1, the crawl did not really crawl: it fetched its seeds and
# stopped. Not a failure — the entries are still real — but it must be reported, because a build
# that quietly behaves like the request path has not done the thing it exists to do.
MINIMUM_DEEP_SHARE = 0.10
# The request path follows a link only when its anchor scores at least 10, because a sixty-second
# corridor cannot afford to read a page that probably says nothing. **This job has no such bound and
# the threshold was costing it most of the country.** Measured on Japan 2026-08-26: 2,834 of 3,103
# corpus entries — 91% — score below 10 from their anchor, so they were discovered and never read,
# and their text could never enter the index. The gate that decides what gets *read* was the same
# anchor scorer that cannot read, which is the defect one level up from the one the index fixes.
#
# Zero rather than a smaller number: the page budget and the per-host budget already bound the
# crawl, and the frontier is best-first, so the high-scoring links are still fetched *first*. This
# only decides what fills the remaining budget — noise, or nothing at all.
CORPUS_EXPANSION_THRESHOLD = 0.0
# PDFs are never followed by a crawl — `_expand` will not queue one, correctly, because a PDF holds
# no links worth walking. But authorities publish checklists as PDFs, which is what the lexicon's
# `pdf_checklist_bonus` exists for, and 26% of Japan's corpus is PDFs. So they are read in a second
# pass, for their text only, best-scoring first.
DEFAULT_CORPUS_PDFS = 400

# Characters a text index cannot hold: NUL and the other C0 controls, plus anything that survived
# extraction as an unpaired surrogate. Tab, newline and carriage return are deliberately kept —
# they are the only whitespace the body needs.
_UNSTORABLE_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def storable_text(text: str) -> str:
    """`text` with the characters no store can hold removed, and nothing else changed.

    Applied to every body on its way into the index. See `keep` in `build_country_corpus` for why
    this drops characters rather than dropping the page or failing the build.
    """

    repaired = text.encode("utf-8", "replace").decode("utf-8", "replace")
    return _UNSTORABLE_CHARACTERS.sub("", repaired)


# How many pages an offline build may render, against the request path's twelve.
#
# **The renderer was always passed to this job; the budget was the bug** (entry 92). Measured on the
# France corpus of 2026-08-28: 64 pages on `france-visas.gouv.fr` answered a Cloudflare challenge,
# twelve renders were available to answer them, and the rest were recorded "that challenge could not
# be answered here" — a sentence that is true of this crawl and false of the authority. France came
# out of that build with **18 readable candidates of 201**, the worst text coverage in the selection
# fixture, and the item that queued this work said the build did not render at all. It does.
#
# Entry 41 is what makes raising it legitimate rather than a relaxation: a challenge states no
# policy, so answering one by running the page's own scripts **under our own user agent** deceives
# nobody. A refusal is still a refusal — a bare `403`, a `401`, a `429` — and is never rendered
# past.
#
# Bounded on two sides, because the cost is time rather than quota: this ceiling, and
# `CHALLENGE_FAILURES_PER_HOST`, which stops a host we genuinely cannot answer from spending the
# whole budget proving it. A corridor keeps twelve because a traveller is waiting for it.
DEFAULT_CORPUS_RENDERS = 400
# **Zero, which means the even split stays — the mechanism below is built, tested and off.**
#
# Item 32 said the United Kingdom's fee tables stopped at 15 of ~198 nationalities because an even
# per-host share starved the host holding them, where Canada's equivalent `?country=XX` pages
# reached 213 on the same code. `HostBudget` was built to fix exactly that, and a rebuild at
# `--pages 3000` disproved the premise: `visa-fees.homeoffice.gov.uk` went **91 pages to 113, and
# 15 nationalities to 20**. It was never budget-limited.
#
# What it is limited by, measured: **zero** of its 86 pages were reached from a *different*
# nationality's page. The country selector is a form, so the space has no links to walk and a crawl
# only ever holds the nationalities search happened to seed. Canada's 425 pages came from a page
# that lists every country as a link — the difference is what the authority published, not what the
# crawler was allowed to spend. That is entry 59's wall, one layer down.
#
# And removing the cap cost something: the surplus goes to whichever host offers the most links,
# which for the United Kingdom is `www.gov.uk` — the whole government website. Its corpus went 922
# entries to 4,530 with **4,252 of them on gov.uk**, most about anything but visas.
#
# Raise this only with a measurement behind it. The floor half is the part worth revisiting: it
# guarantees a small mission host its pages, which is known problem 24's failure, and it is only
# the surplus half that inflated the corpus.
#
# **The even split now binds only links that scored nothing** (entry 186). A scored link may read
# past its host's share up to `maximum_pages_per_host`, which is what the surplus above got wrong:
# that surplus could be spent on any link, and `gov.uk` has endless ones. Limited to scored links,
# `gov.uk` stopped at 392 pages, most of them visa guidance.
DEFAULT_CORPUS_HOST_FLOOR = 0

# How much of an offline build may be spent opening per-traveller families — one page published once
# per country, `…/apply-{country}`. It is reserved rather than competed for, because the members
# cannot win a competition: their anchor text is a bare country name, so `score_role_vocabulary`
# gives every one of the Netherlands' 219 the same **8.0** while the index listing them scores 17.6
# and the checklist each one leads to would score 25.0. Entry 78's defect, in a new place.
#
# **This is not entry 82's proposal and must not be read as one.** That closed "raise the total"
# and "split the total unevenly between hosts", and measurement closed both. This changes neither
# the total nor the split: it changes what the budget is spent on, within one host.
#
# Measured before it was built: lifting the family's *score* to its index's 17.6 is not enough,
# because 764 unopened Dutch pages already score above that. Reservation is the only thing that
# reaches them.
#
# **Zero on the request path, and that is not an oversight.** A corridor has one traveller; opening
# 218 other countries' pages is the definition of a wasted fetch. Only this job serves everybody.
DEFAULT_CORPUS_FAMILY_SHARE = 0.4

# Which per-traveller families the share is spent on, matched against the family's shared address.
# A gate is needed because the members cannot be told apart by score — scoring at the floor is the
# defect being fixed — and because the largest country family on several sites is not guidance at
# all. Measured over the ten corpora: Canada's biggest is `travel.gc.ca/destinations/{}` at 176
# members and Japan's are `mofa.go.jp/region/{area}/{}` at 141, and reserving budget for those would
# spend 40% of a build on travel advisories. With this gate, six of the ten countries have no
# qualifying family and the reservation is inert for them, which is the intended outcome.
# **A visa-domain word is required, not merely a government one (entry 102).** The first version of
# this gate also accepted `apply`, `appointment` and `fees`, which every public service in the world
# uses, and it let three families into the Netherlands' verdict that no corridor could ever use:
# `passport-id-card/abroad/apply-{}` is Dutch citizens renewing a passport, and `making-appointment/
# {}` is booking, which is permanently out of scope. Both held the country at `incomplete` and both
# would have taken reserved crawl budget.
#
# Measured over all ten corpora before the change: it drops exactly those two and **keeps every
# other family in every country** — including `consular-fees/{}`, which survives on `consular`, and
# the United Kingdom's fee wall, which survives on `visa`.
#
# **It is still a keyword gate and it is still wrong in one known way.** `caribbean-visa/short-stay/
# apply-{}` matches on `visa` and is the Kingdom's *Caribbean* visa — Aruba, Curaçao, Bonaire, all
# outside Schengen — so it is the wrong answer for a `netherlands` corridor rather than merely a
# useless one. Excluding it needs a notion of territory this gate does not have. That is not fixed
# here and is written down rather than papered over, because `coverage` is offline with no model by
# design (entry 90) and entry 57's lesson is that a pattern cannot decide meaning.
#
# **The mission words were added on 2026-09-06, and they are the same shape one level up.** The
# post that serves the country a traveller applies from is published as a country family too —
# Australia's `…/missions/Pages/australian-embassy-{}` has 45 members, one per country, each linking
# on to that post's own site, with `…/australian-high-commission-{}` at 25 beside it — and not one
# of `visa`, `permit`, `immigrat`, `consular`, `checklist` or `entry` appears in either address. So
# the largest per-traveller family Australia publishes was refused by the gate built to find
# per-traveller families, and its members went to the ordinary frontier where a bare country name
# scores at the floor — measured on the real index, all 70 of them score exactly 0.0, which is
# entry 88's premise confirmed in a new place rather than assumed.
#
# Measured over all 53 corpora before the change, the way entry 88 measured its own: reconstructing
# each page's link set from `discovered_from` and grouping by `country_family_keys`, the wider
# pattern newly admits **five families and nothing else** — the Netherlands' 173 embassy pages,
# Bulgaria's 156, Greece's 14, Canada's 12 and Malta's 11, every one an index of that country's own
# posts abroad. Nothing resembling Canada's travel advisories or Japan's country-relations pages
# crosses, because neither carries a mission word.
CORPUS_FAMILY_PATTERN = re.compile(
    r"visa|permit|immigrat|consular|checklist|entry"
    r"|embass|consulate|ambassad|ambasad|botschaft|vertretung|mission",
    re.IGNORECASE,
)


# **An index of a country's own posts abroad, and why it is worth a seed of its own.**
#
# The post that serves the country a traveller applies from is missing from 24 of 27 corpora
# (entry 133), and the item that queued this work proposed finding it with a search query. It did
# not need one: measured 2026-09-06, **44 of the 53 corpora already record such a page**, and in 34
# of them the best one was never opened — 156 of the 199 addresses this selects have status
# `unknown`. Australia's is `www.dfat.gov.au/…/our-embassies-and-consulates-overseas`, sitting
# at depth 1 with status `unknown`; opening it by hand yields **194** per-country mission pages, of
# which the corpus holds **one**, and opening the United Arab Emirates member yields
# `uae.embassy.gov.au` — the host Australia's corpus has zero pages on while holding 35 on its
# Riyadh sibling. The family gate groups **70** of those 194; the other 124 name their country in
# the middle of the address and form no family at all.
#
# **So this is allocation, not discovery — and allocation alone cannot reach it.** That chain is
# three hops long. From depth 1 its far end lands at depth 4 and `maximum_depth` is 3, so merely
# *opening* the index would record the post's home page and never a single guidance page on it. A
# seed is depth 0, and depth 0 is what buys the three hops. That is the whole argument for promoting
# a recorded address to a seed rather than reserving budget for it where it sits.
#
# **It is a keyword gate and it misses.** Nine countries record no match at all, and China's index
# forwards with `window.location.href` rather than a link, so seeding it costs a fetch and yields
# nothing until something renders it. Both are written down rather than papered over. The gate is
# allowed to be a keyword one for the same reason `CORPUS_FAMILY_PATTERN` is: it decides which page
# a crawl opens, never what a traveller is told, so entry 57's rule about patterns deciding meaning
# does not reach it.
MISSION_INDEX_NOUNS = (
    r"(embassies|consulates|missions|representations|posts|embajadas|consulados|ambassades"
    r"|ambasciate|botschaften|vertretungen|representaciones|misiones|beskickningar)"
)
MISSION_INDEX_QUALIFIERS = (
    r"(abroad|overseas|worldwide|diplomatic|diplomatiques|diplomatique|diplomatiche"
    r"|our|list|network|directory|find|all)"
)
MISSION_INDEX_PATTERN = re.compile(
    rf"{MISSION_INDEX_NOUNS}[\W_]*(and|&|und|et|y)?[\W_]*{MISSION_INDEX_QUALIFIERS}"
    rf"|{MISSION_INDEX_QUALIFIERS}[\W_]*(and|&|und|et|y)?[\W_]*{MISSION_INDEX_NOUNS}"
    rf"|{MISSION_INDEX_NOUNS}[\W_]*(and|&)[\W_]*{MISSION_INDEX_NOUNS}"
    # Site-specific forms observed in the 53 corpora, where the phrase above is not in the address:
    # Germany, Iceland, Italy, Portugal, Japan, Bulgaria, Greece, Slovakia, Denmark and China.
    r"|auslandsvertretungen|sendiskrifstofur|laretediplomatica|rede[\W_]*consular"
    r"|emb[\W_]*cons|embassyinfo|missionsabroad|embassies-list|find[\W_]*us[\W_]*abroad"
    r"|/zwjg|驻外使馆|驻外总领馆|驻外机构|在外公館",
    re.IGNORECASE,
)

# How many recorded addresses one build may promote to seeds this way. Eight against a 1,200-page
# allowance, because the pattern is noisy in the tail — Iceland matches 1,128 entries, most of them
# a mission's own pages rather than the ministry's list of them — and the ordering below is what
# does the work: a page the last build never opened, shallowest first.
#
# It is also a bound on a second cost that is easy to miss: `_budget_for` divides the page allowance
# by the number of hosts **seeded**, so a seed on a host search did not reach makes every other
# host's share slightly smaller. Eight cannot move that much; a hundred could.
DEFAULT_CORPUS_MISSION_SEEDS = 8


# Addresses a previous build could not read for a reason that said nothing about the page — a
# `429`, a `5xx`, a connection that failed — asked again by the next build, best-scoring first.
# Bounded because nearly 4,200 such entries sat in the 55 stores on 2026-09-25, and a build's page
# budget is for finding what it lacks, not re-asking everything. Entry 207.
DEFAULT_CORPUS_RETRY_SEEDS = 100

# The failure sentences that are **not** a statement by the authority. A `401`, a bare `403`, a
# `Disallow`, an unanswerable challenge, a `404`, an unverifiable certificate and a name that does
# not resolve are facts about the page or the host, and asking again next week is not how any of
# them changes. Matched on the sentences `crawl.py` writes, which are the only record a corpus
# entry keeps of why it failed.
TRANSIENT_FAILURE = re.compile(
    r"\(HTTP 429\)"
    r"|^it answered HTTP 5\d\d"
    r"|had already failed to answer \d+ times in a row"
    r"|^its robots\.txt answered HTTP 5\d\d"
    r"|^the request failed(?!.*nodename nor servname)"
    r"|Server disconnected|peer closed connection|All connection attempts failed|Timeout"
)


def is_transient_failure(detail: str) -> bool:
    """True when a recorded failure says only that the host could not answer *then*.

    **Asking again in a later build is not the retry CLAUDE.md forbids.** That rule is about
    working around an authority that has stated something — re-requesting to get past a rate limit
    in the same run. A `429` on 14 September is a host that was busy on 14 September; a build ten
    days later asking once more, under the same user agent and the same per-host pacing, is an
    ordinary client. Thailand's 2026 revision of its visa exemptions was a `429` in one build and
    was never asked for again (entry 207).
    """

    return bool(TRANSIENT_FAILURE.search(detail))


def retry_candidates(
    corpus: CountryCorpus,
    destination: DestinationConfig,
    score: Callable[[PageLink], RoleScores],
    lexicon: Lexicon,
    *,
    limit: int = DEFAULT_CORPUS_RETRY_SEEDS,
) -> list[CandidatePage]:
    """Entries a previous build could not read for a transient reason, best-scoring first.

    Each keeps the depth and origin it was recorded with, because this build asking again says
    nothing new about how the page is reached; a retried page becomes a seed only so that it is
    fetched. Rejected on the same grounds as any link, in case the rules moved since.
    """

    matched: list[CandidatePage] = []
    for entry in corpus.entries:
        if entry.status != "unreadable" or not is_transient_failure(entry.detail):
            continue
        url = canonicalise_url(entry.url)
        if not is_crawlable(url, destination):
            continue
        link = PageLink(
            url=url,
            text=entry.link_text[:300],
            heading=entry.heading,
            depth=entry.depth,
            discovered_from=entry.discovered_from[:2000],
        )
        if _reject(link, lexicon) is not None:
            continue
        matched.append(
            CandidatePage(
                link=link, link_scores=score(link), title=entry.title or None, found_by="corpus"
            )
        )
    matched.sort(key=lambda page: (-page.link_scores.best()[1], page.link.depth, page.link.url))
    return matched[:limit]


def mission_index_seeds(
    corpus: CountryCorpus,
    destination: DestinationConfig,
    *,
    slugs: frozenset[str],
    limit: int = DEFAULT_CORPUS_MISSION_SEEDS,
) -> list[str]:
    """Addresses the corpus already holds that read as this country's own index of its missions.

    **Members of the family are excluded, not the index of it.** `mfa.bg/en/embassyinfo/{country}`
    matches the pattern 62 times over and is the list's *content*; seeding one of those spends a
    fetch on a page a corridor could already reach. `country_family_keys` is what tells them apart,
    and it is the same function the reservation groups on.

    Unopened first, then shallowest, then by address. Unopened first because a page the last build
    read has already contributed whatever it links to, and a page it recorded and skipped is the one
    this exists for; deterministic throughout, because two builds of the same country must ask the
    same things in the same order.
    """

    matched = [
        entry
        for entry in corpus.entries
        if MISSION_INDEX_PATTERN.search(f"{entry.url} {entry.link_text} {entry.title}")
        and not country_family_keys(entry.url, slugs)
        and is_crawlable(canonicalise_url(entry.url), destination)
    ]
    matched.sort(key=lambda entry: (entry.status != "unknown", entry.depth, entry.url))
    seeds: list[str] = []
    for entry in matched:
        url = canonicalise_url(entry.url)
        if url not in seeds:
            seeds.append(url)
        if len(seeds) >= limit:
            break
    return seeds


# How far a previous build got with an address, as the family queue's ordering key. Ordinary
# `unknown` is absent from the map and defaults to 0, so only the two states worth deferring are
# recorded and the map stays a fraction of the corpus.
_REVISIT_RANKS: dict[str, int] = {"unreadable": 1, "readable": 2, "proven": 2}


def family_revisit_ranks(corpus: CountryCorpus | None) -> dict[str, int]:
    """What the last build already got out of each address, for `FamilyQueues` to order on.

    **The only ordering a corpus is allowed to apply** (entry 139). This job has no traveller, so it
    may not prefer the countries some traveller came from — that is the tilt entry 44 exists to
    prevent, and it would make the store's contents a function of who happened to be asked about.
    What it may prefer is what it does not yet have: an address the last build never opened, then
    one it tried and failed, then one it read.

    Applied to the reserved family queues alone. Members tie there by construction — all 166 of
    Australia's mission pages score 0.0 — so a heap of ties falls through to the order the links sit
    on the index, which is the alphabet, in every build, for ever. The ordinary frontier is ranked
    by score and is deliberately untouched.
    """

    if corpus is None:
        return {}
    return {
        entry.url: _REVISIT_RANKS[entry.status]
        for entry in corpus.entries
        if entry.status in _REVISIT_RANKS
    }


def corpus_queries(
    country_name: str, domain: str, *, purpose: TravelPurpose | None = None
) -> list[str]:
    """What to ask about a country's visa pages when you do not know who is travelling.

    No nationality and no residence — those are 198-valued, and putting them here would tilt the
    corpus toward whoever it was built for.

    **Purpose is different, and treating it like the others cost the corpus a real page.** Measured
    2026-08-22: `.../visit-canada/supporting-documents` was fetched by a live corridor run and was
    absent from a 1,071-entry corpus. It had entered the corridor run as a search seed from
    `site:canada.ca tourism visa documents required` — `corridor_queries`' purpose template. The
    corpus asked `visa application documents required`, with no purpose word, and never saw it.

    So purpose is swept rather than omitted. There are **four** purposes against 198 nationalities,
    so covering the dimension exhaustively costs four passes and leaves the corpus still
    corridor-independent: a corpus containing every purpose's pages favours no traveller, where a
    corpus containing one purpose's would.

    Each is `site:`-restricted, exactly as `corridor_queries` is, so the engine is asked only about
    a domain the registry already confirmed — and the results are filtered again afterwards, because
    the restriction is a courtesy to the engine and not the safety mechanism.
    """

    if purpose is not None:
        # Deliberately the same phrasings `corridor_queries` uses, so the corpus sees what a
        # corridor would see. Divergent wording would reopen the gap this sweep exists to close.
        return [
            f"site:{domain} {purpose} visa documents required",
            f"site:{domain} {country_name} {purpose} visa requirements",
        ]
    return [
        f"site:{domain} {country_name} visa requirements",
        f"site:{domain} {country_name} who needs a visa",
        f"site:{domain} visa application documents required",
        f"site:{domain} visa types and fees",
        f"site:{domain} visa processing times",
        f"site:{domain} visa exempt countries",
    ]


def all_corpus_queries(country_name: str, domains: list[str]) -> list[str]:
    """Every query one build runs: the neutral pass, then one pass per purpose.

    Order is fixed and duplicates are dropped, so two builds of the same country ask the same things
    in the same order — the corpus is meant to remove variance, not add a new source of it.
    """

    queries: list[str] = []
    for domain in domains:
        queries.extend(corpus_queries(country_name, domain))
    for purpose in get_args(TravelPurpose):
        for domain in domains:
            queries.extend(corpus_queries(country_name, domain, purpose=purpose))
    return list(dict.fromkeys(queries))


class CorpusBuild(StrictModel):
    """What one build of a country's corpus did, for a person reading the command's output."""

    country_code: str
    queries: int = 0
    seeds: int = 0
    mission_seeds: int = 0
    """How many of those seeds were addresses the last build recorded and never opened.

    Reported rather than inferred, because a build whose corpus is empty gets none of them and a
    build whose country publishes no recognisable index gets none either — two very different
    reasons for the post being missing, and the count is what tells them apart."""

    retry_seeds: int = 0
    """How many seeds were addresses a previous build failed on for a transient reason (entry 207).

    Counted apart from `mission_seeds` because the two answer different gaps: a post nobody opened,
    and a page that was asked for once while its host was busy."""

    failed_queries: dict[str, str] = Field(default_factory=dict)
    """Search queries that failed, with the reason, which this build went on without (entry 162).

    Empty on a build where every query answered. A build with every query failed raises instead, and
    one whose account is out of credit raises on the first, so a non-empty value here always means
    a corpus that was searched in part and says which part."""

    seeds_kept: int = 0
    """Search seeds kept as depth-0 entries because nothing the crawl read linked to them.

    Until 2026-09-15 these were dropped: a seed became an entry only by being linked, which lost
    75% of Norway's, 80% of Thailand's and 59% of Japan's (entry 161). Not in `by_depth`, which
    measures how far the crawl reached."""

    crawled: int = 0
    found: int = 0
    """Entries this crawl produced, before the merge."""

    added: int = 0
    """Entries the corpus did not already hold."""

    total: int = 0
    """Entries the corpus holds afterwards, which is never fewer than before."""

    unreadable: int = 0
    page_budget: int = 0
    """The `--pages` allowance this build was given, so its own advice can tell two failures apart.

    Without it `depth_is_exercised` could say a crawl stayed shallow but not *why*, and the advice
    it printed — raise `--pages` — was wrong for half the cases: the Philippines stayed at depth 1
    having spent 425 of 1,200 pages, so more budget could not have helped it (entry 115)."""

    delegated: int = 0
    """How many places this country's own pages sent the traveller that we may name but not read."""
    by_depth: dict[int, int] = Field(default_factory=dict)
    """How far this crawl reached. A means rather than the point: the corpus exists so a live
    corridor does not re-fetch for 50+ seconds, and *which pages exist* does not vary by traveller
    (DECISIONS entry 44). Depth matters only where the pages a corridor needs lie deeper."""

    abandoned_hosts: dict[str, int] = Field(default_factory=dict)
    """Hosts this build stopped asking, and after how many consecutive unanswered requests.

    Separate from `lost_hosts`, which names hosts that contributed nothing: a host can be given up
    on and still be the largest in the corpus. `www.dfat.gov.au` is exactly that — 574 addresses
    recorded, and it timed out on 22 of the 25 mission pages this crawl tried to open (entry 138).
    Reported because giving up loses whatever that host still held, and a number nobody prints is a
    trade nobody can review."""

    lost_hosts: dict[str, str] = Field(default_factory=dict)
    """Hosts that failed and contributed **nothing**, with the reason, worst kind of gap first.

    A seeded host whose own fetch fails leaves at most an `unreadable` seed entry, which is not
    something the host gave, so it still counts as lost. Until entry 161 it left no trace whatever,
    because a seed never became an entry. Japan's London embassy went missing exactly that way,
    through a transient Akamai `403` during a build, and the corpus has lacked the host ever since
    while live search returns it and the live path reads a document checklist from it (entry 77).

    A corpus is additive and rebuilt rarely, so a hole opened by a moment's failure stays open. This
    is the field that makes it visible; retrying these on the next build is not yet built.
    """

    pdfs_read: int = 0
    """PDFs read in the second pass, for their text only. Counted apart from `crawled` because they
    were never crawled: `_expand` will not follow a PDF, so these are fetched deliberately."""

    indexed_text: int = 0
    """Pages whose readable text was kept, for the text index (`discovery/page_text.py`).

    Counted apart from `crawled` because the two differ and the difference is the interesting part:
    a page can be fetched and still contribute no text — too short to rank, or a render that came
    back empty. It is also **not** comparable to `found`, which counts discovered links rather than
    pages read. Zero here with a non-zero `crawled` means the build was asked to keep nothing."""

    opened_seeds: int = 0
    opened_scored: int = 0
    opened_unscored: int = 0
    """Pages opened by a link that scored nothing on role vocabulary. Entry 185 found 74% of
    Japan's link-opened pages were these, all on hosts with nothing scored left to read."""

    opened_family: int = 0
    opened_over_share: int = 0
    """Pages a host read past its even share because their links scored (`scored_host_ceiling`)."""

    dropped_scored: int = 0
    """Distinct scored links turned away at a host's cap and never opened. Entry 185: 149 in
    Japan's build under the even split, including the oracle's visa-decision page."""

    rejected: dict[str, int] = Field(default_factory=dict)
    """Links the crawl found and threw away, counted by the rule that rejected them (entry 200).

    Malta's two visa lists were rejected by `is_archived` in every build, and nothing in the report
    said so: the rule's reason was recorded on the crawler and dropped. A rejected link leaves no
    entry, so this is the only trace of what a veto costs."""

    rejected_about_visas: dict[str, int] = Field(default_factory=dict)
    """Of those, the ones whose address says "visa" — the count that would have flagged Malta."""

    rejected_examples: dict[str, list[str]] = Field(default_factory=dict)
    """Up to five addresses per rule, those saying "visa" first, so a person can judge the rule."""

    lost_host_outcomes: dict[str, FailureOutcome] = Field(default_factory=dict)
    """The same hosts keyed to the typed outcome, so a count never rests on parsing prose.

    DECISIONS entry 36's rule: `lost_hosts` carries what a person reads, this carries what may be
    counted, and rewording a message must never change what an audit reports."""

    @property
    def deep_share(self) -> float:
        """The share of this crawl's entries found beyond depth 1."""

        found = sum(self.by_depth.values())
        if not found:
            return 0.0
        return sum(count for depth, count in self.by_depth.items() if depth > 1) / found

    @property
    def budget_was_spent(self) -> bool:
        """Whether the crawl used what it was given, within a page.

        The question that separates "it ran out of allowance at depth 1" from "it ran out of links
        to follow". Only the first is fixed by a larger budget.
        """

        return bool(self.page_budget) and self.crawled >= self.page_budget - 1

    @property
    def depth_is_exercised(self) -> bool:
        """False when the crawl fetched its seeds and effectively stopped.

        Reported rather than raised: the entries are real either way. But a build that behaves like
        the request path has not done the one thing it exists to do, and on 2026-08-22 that was true
        and invisible — 1,032 of Canada's 1,071 entries sat at depth 1 and nothing said so.
        """

        return self.deep_share >= MINIMUM_DEEP_SHARE


def _reject(link: PageLink, lexicon: Lexicon) -> str | None:
    """What cannot be visa guidance for anybody.

    Only two, and both are corridor-independent facts about the page rather than about the reader.
    `wrong_audience` and `wrong_country` are deliberately **not** applied: a page about Brazil is
    noise for one corridor and the answer for another, and the corpus serves every corridor.
    """

    if is_archived(link.url, lexicon):
        return "the path marks it as archived or superseded"
    if is_boilerplate(link.url, lexicon):
        return "the path marks it as site furniture rather than guidance"
    return None


def _entry(
    candidate: CandidatePage,
    titles: dict[str, str],
    failures: dict[str, str],
    now: datetime,
    read: set[str] = frozenset(),  # type: ignore[assignment]
) -> CorpusEntry:
    """One corpus entry, and **whether the crawl actually opened the page**.

    `read` was added by entry 92 after a measurement nobody had made: this function wrote only
    `unreadable` or `unknown`, so `readable` was a documented retention tier that no build ever
    assigned. Two things followed, and the second is the one that matters.

    `merge` moves a status up and never down, and `unknown` ranks *below* `unreadable`. So a page
    that failed in one build and was read in the next kept the old failure and its sentence for
    ever. Twelve France entries said "it asked this client to prove it is a browser... and that
    challenge could not be answered here" while the page-text index held their bodies — a reason
    that is not true of what was seen, which is the one thing this project's failure text may never
    be.
    """

    url = candidate.link.url
    reason = failures.get(url)
    return CorpusEntry(
        url=url,
        title=titles.get(url) or candidate.title or "",
        link_text=candidate.link.text,
        heading=candidate.link.heading,
        depth=candidate.link.depth,
        discovered_from=candidate.link.discovered_from,
        first_seen=now,
        last_seen=now,
        status="unreadable" if reason else ("readable" if url in read else "unknown"),
        detail=reason or "",
    )


async def _search_tolerating_failures(
    provider: SearchProvider,
    queries: list[str],
    *,
    count: int,
    concurrency: int = DEFAULT_SEARCH_CONCURRENCY,
) -> tuple[dict[str, list[SearchResult]], dict[str, str]]:
    """Run a build's queries, going on without any that fail, and say which did.

    **`search_all` raises if any query fails, and that is right for a corridor and wrong here.** Its
    own docstring says tolerating a failure is a separate decision about serving partly-searched
    evidence, and a corridor serves evidence. A corpus does not: it is additive and never claims to
    be complete, so one failed query of up to 70 cost a whole build — Japan's, to a DNS blip on
    2026-08-23 — for nothing it was protecting. DECISIONS entry 162.

    Two failures still stop the build, because going on would not be a partial build but none:
    an account out of credit (`SearchQuotaExhausted`), which no later query can succeed against, and
    every query failing, where "we could not look" must not become a crawl of nothing but what the
    last build recorded.
    """

    limit = asyncio.Semaphore(max(1, concurrency))

    async def run(query: str) -> tuple[list[SearchResult], str | None]:
        async with limit:
            try:
                return await provider.search(query, count=count), None
            except SearchQuotaExhausted:
                raise
            except SearchError as exc:
                # A timeout can carry an empty message (entry 122), so the type stands in for it.
                return [], str(exc).strip() or type(exc).__name__

    completed = await asyncio.gather(*(run(query) for query in queries))
    found = {query: results for query, (results, _) in zip(queries, completed, strict=True)}
    failed = {
        query: reason
        for query, (_, reason) in zip(queries, completed, strict=True)
        if reason is not None
    }
    if queries and len(failed) == len(queries):
        first = next(iter(failed.values()))
        raise SearchError(
            f"every one of this build's {len(queries)} search queries failed, so nothing was "
            f"searched; the first said: {first}"
        )
    return found, failed


def _search_seed_candidates(
    searched: dict[str, tuple[str, str]],
    crawled: list[CandidatePage],
    score: Callable[[PageLink], RoleScores],
    lexicon: Lexicon,
) -> list[CandidatePage]:
    """The pages this build's own search pointed at that the crawl did not record by a link.

    **`LinkCrawler.crawl` returns only links found on pages it read, never the seeds it started
    from**, so until 2026-09-15 a seed became an entry only if some other crawled page happened to
    link to it. Search returns a page, not a site, and most of those pages are not linked from
    whatever else the crawl read. Measured by re-issuing today's corpus queries: **75% of Norway's
    seeds, 80% of Thailand's and 59% of Japan's were absent from the corpus**, 216 of them scoring
    for a role. Three were pages a live corridor could get only from search: Norway's January 2024
    tourist checklist, Thailand's arrival card, and the London embassy's tourism page for Japan.
    A PDF seed was lost twice over — the crawl refuses PDFs and `_read_pdfs` read only linked ones.
    DECISIONS entry 161.

    Recorded the way a live corridor records a search result (`resolver._resolve`): depth 0, the
    engine's title as the link text, and the query as where it came from. Rejected on the same
    corridor-independent grounds as a crawled link. The request path is not changed by this: it
    already turns every search result into a candidate itself.
    """

    recorded = {candidate.link.url for candidate in crawled}
    kept: list[CandidatePage] = []
    for url, (query, title) in searched.items():
        if url in recorded:
            continue
        link = PageLink(
            url=url, text=title[:300], heading="", depth=0, discovered_from=query[:2000]
        )
        if _reject(link, lexicon) is not None:
            continue
        kept.append(
            CandidatePage(
                link=link, link_scores=score(link), title=title or None, found_by="search"
            )
        )
    return kept


def _lost_hosts(
    entries: list[CorpusEntry], crawl_fetcher: CrawlFetcher
) -> tuple[dict[str, str], dict[str, FailureOutcome]]:
    """Hosts this build failed on and got *nothing* from, which is the gap nobody could see.

    A host with some pages read and some failed is not lost — the corpus holds it and a later build
    can deepen it. A host that contributed no entry at all is a hole, and because a corpus only ever
    grows, it is a permanent one until someone rebuilds and the same host happens to answer.
    """

    covered = {host_of(entry.url) for entry in entries}
    reasons: dict[str, str] = {}
    outcomes: dict[str, FailureOutcome] = {}
    for url, reason in crawl_fetcher.failures.items():
        host = host_of(url)
        if host in covered:
            continue
        reasons.setdefault(host, reason)
        outcome = crawl_fetcher.outcomes.get(url)
        if outcome is not None:
            outcomes.setdefault(host, outcome)
    return reasons, outcomes


async def _read_pdfs(
    crawled: list[CandidatePage],
    destination: DestinationConfig,
    crawl_fetcher: CrawlFetcher,
    keep: Callable[[str, str, str], None],
    *,
    maximum_pdfs: int,
) -> set[str]:
    """Read the PDFs a crawl found, for their text alone, best-scoring first; return those read.

    A second pass rather than part of the crawl, because a PDF is a destination and the crawl walks
    signposts — queuing one on the frontier would mean fetching it to look for links it cannot have.
    Ordered by the anchor score anyway: the budget is finite and a PDF whose link said "Checklist"
    is worth more than one whose link said "Form 12", even though the anchor is exactly the signal
    this whole exercise distrusts. It is what there is *before* the text is in hand.

    No title: a PDF has no `<title>`, and inventing one from the anchor would put the crawl's guess
    where `score_body` reads a page's own claim about itself.
    """

    unique = {page.link.url: page for page in crawled if is_pdf_url(page.link.url)}
    ordered = sorted(unique.values(), key=lambda page: -page.link_scores.best()[1])[:maximum_pdfs]
    if not ordered:
        return set()

    read: set[str] = set()
    async with httpx.AsyncClient(
        transport=crawl_fetcher.transport,
        timeout=crawl_fetcher.timeout_seconds,
        follow_redirects=True,
        verify=build_ssl_context(),
        headers={
            "User-Agent": crawl_fetcher.user_agent,
            "Accept": "application/pdf",
        },
    ) as client:
        for page in ordered:
            text = await crawl_fetcher.fetch_pdf_text(
                client,
                page.link.url,
                destination,
                maximum_characters=DEFAULT_KEPT_TEXT_CHARACTERS,
            )
            if text:
                keep(page.link.url, "", text)
                read.add(page.link.url)
    return read


async def build_country_corpus(
    country: Country,
    trusted: list[str],
    provider: SearchProvider,
    crawl_fetcher: CrawlFetcher,
    *,
    existing: CountryCorpus | None,
    now: datetime,
    lexicon: Lexicon | None = None,
    registry: CountryRegistry | None = None,
    providers: ServiceProviderRegistry | None = None,
    maximum_pages: int = DEFAULT_CORPUS_PAGES,
    maximum_depth: int = DEFAULT_CORPUS_DEPTH,
    maximum_pages_per_host: int = DEFAULT_CORPUS_PAGES_PER_HOST,
    host_floor: int = DEFAULT_CORPUS_HOST_FLOOR,
    results_per_query: int = 10,
    page_text: PageTextStore | None = None,
    maximum_pdfs: int = DEFAULT_CORPUS_PDFS,
    family_share: float = DEFAULT_CORPUS_FAMILY_SHARE,
    maximum_mission_seeds: int = DEFAULT_CORPUS_MISSION_SEEDS,
    maximum_retry_seeds: int = DEFAULT_CORPUS_RETRY_SEEDS,
) -> tuple[CountryCorpus, CorpusBuild]:
    """Search, crawl and fold the result into the country's corpus, adding but never removing.

    With `page_text`, the readable text of every page the crawl reads is kept as well, and this is
    the only place that ever costs nothing extra to do: the bytes are already in hand, already
    parsed for links, and were previously dropped on the floor at `crawl._expand`. No additional
    fetch, no additional search, no additional politeness delay.

    Two stores, written together and deliberately not merged. The corpus answers *which pages
    exist* and is read whole on every request; the index answers *what they say* and is queried
    without being loaded. Putting the text in the corpus file would take Japan's from 1.4MB to
    roughly 35MB and spend about a second of pydantic validation per corridor.
    """

    words = lexicon or get_lexicon()
    # Every country's slug, so a run of sibling links that differ only by which country they
    # are about can be recognised as one page published per traveller.
    every_country = registry or get_country_registry()
    destination = DestinationConfig(
        slug=country.slug,
        display_name=country.name,
        route_type="national",
        implementation_status="available",
        trusted_domains=trusted,
    )

    queries = all_corpus_queries(country.name, trusted)
    found, failed_queries = await _search_tolerating_failures(
        provider, queries, count=results_per_query
    )

    seeds: list[str] = []
    # Which query first returned each seed, and the title it came back under: kept so a seed the
    # crawl does not reach again by a link can still become an entry. See `_search_seed_candidates`.
    searched: dict[str, tuple[str, str]] = {}
    for query in queries:
        for result in usable_results(found[query], destination):
            url = canonicalise_url(result.url)
            if not is_crawlable(url, destination):
                continue
            if url not in seeds:
                seeds.append(url)
                searched[url] = (query, result.title)

    # Seeds the last build already found and did not open. A search seed lands wherever the engine
    # surfaced a mission — which is how 24 of 27 corpora came to hold no post for the country a
    # traveller applies from (entry 133) — while the authority's own index of its posts names every
    # one of them. Promoting it to depth 0 is what puts its far end inside `maximum_depth`; see
    # `mission_index_seeds`.
    mission_seeds = 0
    if existing is not None:
        for url in mission_index_seeds(
            existing,
            destination,
            slugs=frozenset(other.slug for other in every_country.countries),
            limit=maximum_mission_seeds,
        ):
            if url not in seeds:
                seeds.append(url)
                mission_seeds += 1

    def score(link: PageLink) -> RoleScores:
        # With the link's surroundings, which only a build reads (entry 189).
        return score_link_in_context(link, words)

    # Addresses a previous build could not read because the host was busy or down at the time.
    # A web page is fetched as a seed; a PDF goes to the PDF pass, which is where any PDF is read.
    retried = (
        retry_candidates(existing, destination, score, words, limit=maximum_retry_seeds)
        if existing is not None
        else []
    )
    for candidate in retried:
        if not is_pdf_url(candidate.link.url) and candidate.link.url not in seeds:
            seeds.append(candidate.link.url)

    # Buffered rather than written page by page: one transaction at the end of a crawl, against
    # thousands mid-crawl. The cost is that a build killed halfway keeps no text, which is the
    # corpus file's own behaviour and the same remedy — run it again.
    kept: list[StoredPage] = []

    def keep(url: str, title: str, text: str) -> None:
        # Sanitised on the way in, because one page must never cost a build. A PDF's text layer can
        # carry NUL bytes, and truncating at `DEFAULT_KEPT_TEXT_CHARACTERS` can cut a surrogate
        # pair in half; pydantic refuses either as a string and SQLite cannot hold a NUL, so a
        # single such page raised `ValidationError` out of `_read_pdfs` — *after* the crawl, so the
        # corpus was never written and 18 minutes of China went with it (entry 114).
        #
        # Dropping these characters is not editing guidance. They carry no meaning, this text is
        # ranking input that never reaches a traveller (entry 78), and the alternative on offer was
        # losing every page rather than none.
        body = storable_text(text)
        if body:
            kept.append(StoredPage(url=url, fetched_at=now, body=body, title=storable_text(title)))

    crawler = LinkCrawler(
        crawl_fetcher,
        score,
        reject=lambda link: _reject(link, words),
        maximum_depth=maximum_depth,
        maximum_pages=maximum_pages,
        maximum_pages_per_host=maximum_pages_per_host,
        host_floor=host_floor,
        expansion_threshold=CORPUS_EXPANSION_THRESHOLD,
        on_page=keep if page_text is not None else None,
        family_slugs=frozenset(other.slug for other in every_country.countries),
        family_share=family_share,
        family_pattern=CORPUS_FAMILY_PATTERN,
        # Where the last build stopped, so this one does not start at the alphabet again.
        family_revisit=family_revisit_ranks(existing),
        provider_domains=(providers or get_service_providers()).domains,
        # Scored links may read past an even share, up to the per-host cap; links that scored
        # nothing may not. Entry 185.
        scored_host_ceiling=maximum_pages_per_host,
    )
    crawled = await crawler.crawl(destination, seeds)
    ledger = crawler.ledger
    seeded = _search_seed_candidates(searched, crawled, score, words)
    recorded = {candidate.link.url for candidate in [*crawled, *seeded]}
    retried = [candidate for candidate in retried if candidate.link.url not in recorded]
    pdfs_read: set[str] = set()
    if page_text is not None:
        # The seeds go in too: a PDF seed is never fetched by the crawl, which refuses PDFs, so this
        # pass is the only place its text can be read. So do PDFs a previous build failed on.
        pdfs_read = await _read_pdfs(
            [*crawled, *seeded, *retried],
            destination,
            crawl_fetcher,
            keep,
            maximum_pdfs=maximum_pdfs,
        )
    # A page is read if the crawl read it or the PDF pass did. Without the second, a PDF that failed
    # in one build and was read in the next kept its old failure sentence, since `merge` never moves
    # a status down and `unknown` ranks below `unreadable` — entry 92's defect, for PDFs.
    read = crawler.read | pdfs_read
    indexed_text = page_text.write(country.code, kept) if page_text is not None else 0

    crawl_entries = [
        _entry(candidate, crawler.titles, crawl_fetcher.failures, now, read)
        for candidate in crawled
    ]
    seed_entries = [
        _entry(candidate, crawler.titles, crawl_fetcher.failures, now, read)
        for candidate in [*seeded, *retried]
    ]
    entries = [*crawl_entries, *seed_entries]
    before = existing or CountryCorpus(
        country_code=country.code,
        country_name=country.name,
        trusted_domains=trusted,
        built_at=now,
        entries=[],
    )
    # A seed that could not be fetched is kept as an `unreadable` entry, which is not something the
    # host gave this build: counting it would hide exactly the hole `lost_hosts` exists to name.
    lost_reasons, lost_outcomes = _lost_hosts(
        [*crawl_entries, *(entry for entry in seed_entries if entry.status != "unreadable")],
        crawl_fetcher,
    )
    known = {entry.url for entry in before.entries}
    # The domains are refreshed to what the registry says now, so the file records the set actually
    # used. The entries are not filtered by it: `entries_within` applies trust when the corpus is
    # *read*, which is what lets a later narrowing take effect without deleting what was found.
    after = merge(
        before.model_copy(update={"trusted_domains": trusted}),
        entries,
        now=now,
        delegations=crawler.delegations.values(),
    )
    return after, CorpusBuild(
        country_code=country.code,
        queries=len(queries),
        seeds=len(seeds),
        mission_seeds=mission_seeds,
        retry_seeds=len(retried),
        failed_queries=failed_queries,
        seeds_kept=len(seed_entries),
        crawled=len(crawled),
        page_budget=maximum_pages,
        found=len(entries),
        added=sum(1 for entry in entries if entry.url not in known),
        total=len(after.entries),
        unreadable=sum(1 for entry in entries if entry.status == "unreadable"),
        delegated=len(after.delegations),
        # How far the *crawl* reached. Kept seeds sit at depth 0 and are not reach, so counting them
        # here would lower `deep_share` and warn of a shallow crawl that did not happen.
        by_depth=Counter(entry.depth for entry in crawl_entries),
        abandoned_hosts=dict(crawl_fetcher.abandoned_hosts),
        lost_hosts=lost_reasons,
        lost_host_outcomes=lost_outcomes,
        indexed_text=indexed_text,
        pdfs_read=len(pdfs_read),
        opened_seeds=ledger.seeds,
        opened_scored=ledger.scored,
        opened_unscored=ledger.unscored,
        opened_family=ledger.family,
        opened_over_share=ledger.over_share,
        dropped_scored=len(ledger.dropped_scored - crawler.read),
        **_rejections(crawler.rejected),
    )


REJECTED_EXAMPLES = 5


def _rejections(rejected: dict[str, str]) -> dict[str, Any]:
    """What the crawl threw away, per rule: a count, the part about visas, and a few addresses."""

    by_reason: dict[str, list[str]] = {}
    for url, reason in rejected.items():
        by_reason.setdefault(reason, []).append(url)
    about_visas = {
        reason: [url for url in urls if "visa" in url.lower()] for reason, urls in by_reason.items()
    }
    return {
        "rejected": {reason: len(urls) for reason, urls in by_reason.items()},
        "rejected_about_visas": {reason: len(urls) for reason, urls in about_visas.items() if urls},
        "rejected_examples": {
            reason: (
                sorted(about_visas[reason])
                + sorted(url for url in urls if url not in set(about_visas[reason]))
            )[:REJECTED_EXAMPLES]
            for reason, urls in by_reason.items()
        },
    }


BuildReporter = Callable[[CorpusBuild], None]


# Below this a "page" is an error page or a shell, not what a person asked to add.
MINIMUM_ADDED_PAGE_CHARACTERS = 500


class AddedPages(StrictModel):
    """What `add_named_pages` stored, and why each page it did not store was left out."""

    country_code: str
    stored: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)


async def add_named_pages(
    country: Country,
    trusted: list[str],
    urls: list[str],
    fetcher: CrawlFetcher,
    *,
    existing: CountryCorpus | None,
    page_text: PageTextStore,
    now: datetime,
) -> tuple[CountryCorpus | None, AddedPages]:
    """Add pages a person named to a country's corpus and text index, with no search and no crawl.

    For a page the owner has read and wants a corridor able to find (entry 265), without a rebuild
    that would crawl its whole site. Nothing about trust is different: each URL is read through
    `CrawlFetcher`, so it must sit on one of the country's trusted domains after every redirect,
    and robots.txt and the challenge rules apply as in any build. Like every corpus page, it ranks
    and never speaks — a corridor that picks it reads it live before a word reaches a plan.
    """

    destination = DestinationConfig(
        slug=country.slug,
        display_name=country.name,
        route_type="national",
        implementation_status="available",
        trusted_domains=trusted,
    )
    report = AddedPages(country_code=country.code)
    entries: list[CorpusEntry] = []
    pages: list[StoredPage] = []
    async with httpx.AsyncClient(
        transport=fetcher.transport,
        timeout=fetcher.timeout_seconds,
        follow_redirects=True,
        verify=build_ssl_context(),
        headers={"User-Agent": fetcher.user_agent, "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        for raw in urls:
            url = canonicalise_url(raw)
            if not is_crawlable(url, destination):
                report.failed[url] = (
                    f"it is not on one of {country.name}'s trusted domains "
                    f"({', '.join(trusted)}), so it may not be read"
                )
                continue
            html = await fetcher.fetch_html(client, url, destination)
            text = (
                storable_text(
                    clean_source_html(html, maximum_characters=DEFAULT_KEPT_TEXT_CHARACTERS)
                )
                if html
                else ""
            )
            if len(text.strip()) < MINIMUM_ADDED_PAGE_CHARACTERS:
                report.failed[url] = fetcher.failures.get(
                    url, f"it returned {len(text.strip())} characters, too little to be the page"
                )
                continue
            title = storable_text(page_title_of(html or ""))
            entries.append(
                CorpusEntry(
                    url=url,
                    title=title,
                    link_text=title[:300],
                    discovered_from="named by a person (visa-discover corpus-add)",
                    first_seen=now,
                    last_seen=now,
                    status="readable",
                )
            )
            pages.append(StoredPage(url=url, fetched_at=now, body=text, title=title))
            report.stored.append(url)

    if not entries:
        return None, report
    before = existing or CountryCorpus(
        country_code=country.code,
        country_name=country.name,
        trusted_domains=trusted,
        built_at=now,
        entries=[],
    )
    page_text.write(country.code, pages)
    return merge(before, entries, now=now), report
