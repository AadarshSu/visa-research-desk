"""Turning a corridor into a set of official sources, or refusing to.

The order is deliberate: search to arrive, crawl to pinpoint, then fetch the shortlist through the
ordinary retrieval path so that discovered pages are subject to exactly the same trust, PDF and
freshness rules as hand-configured ones.

If a load-bearing role cannot be filled confidently the corridor is refused. A plausible substitute
for a document checklist is worse than no answer, because the traveller would be told to bring the
wrong papers with full confidence.

A missing checklist is the one exception, and it is not a relaxation of that rule. Some authorities
publish no checklist anywhere — Vietnam states its e-visa requirements as upload fields inside the
application form — so the corridor resolves and the absence is reported instead. What must never
happen is a checklist appearing without a source behind it, which `VisaPlan` refuses structurally.
"""

import re
import time
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field

from visa_research_agent.discovery.adjudication import (
    MAXIMUM_BLOCKED_JUDGED,
    AdjudicationError,
    AdjudicationQuotaExhausted,
    RoleAdjudication,
    RoleAdjudicator,
    UsageRecorder,
    build_blocked_packet,
    build_candidate_packet,
    load_adjudication_prompt,
    load_blocked_prompt,
    role_verdicts,
    validated_blocked_choices,
    validated_choices,
    validated_confirmations,
    validated_delegates,
    validated_tools,
)
from visa_research_agent.discovery.corpus import CountryCorpus, canonical_key
from visa_research_agent.discovery.crawl import (
    DEFAULT_CRAWL_PAGES,
    CrawlFetcher,
    LinkCrawler,
)
from visa_research_agent.discovery.lexicon import (
    Country,
    CountryRegistry,
    Lexicon,
    get_country_registry,
    get_lexicon,
)
from visa_research_agent.discovery.models import (
    REPORTED_ROLES,
    ROLE_ORDER,
    CandidatePage,
    Corridor,
    Delegation,
    DiscoveryRole,
    PageLink,
    RefusalCause,
    ResolvedCorridor,
    ResolvedDelegate,
    ResolvedSource,
    ResolvedTool,
    RoleScores,
)
from visa_research_agent.discovery.page_text import PageTextStore
from visa_research_agent.discovery.recall_log import (
    ModelCall,
    RecallLog,
    RecallRecord,
    RoleVerdict,
    considered,
)
from visa_research_agent.discovery.scoring import (
    foreign_post_labels,
    is_archived,
    is_boilerplate,
    rank_for_role,
    score_body,
    score_link,
    wrong_audience,
    wrong_country,
)
from visa_research_agent.discovery.search import (
    SearchError,
    SearchProvider,
    corridor_queries,
    resolve_corridor_countries,
    search_all,
    usable_results,
)
from visa_research_agent.discovery.selection import (
    DEFAULT_SELECTION_BLIND,
    DEFAULT_SELECTION_SHOWN,
    CandidateSelector,
    SelectionError,
    admitted_on_text,
    build_selection_packet,
    load_selection_prompt,
    selected_candidates,
    shown_to_selector,
    validated_selection,
)
from visa_research_agent.discovery.urls import (
    canonicalise_url,
    is_crawlable,
    published_date_in_path,
)
from visa_research_agent.domain.models import (
    PERSISTENT_REFUSAL_STATUS_CODES,
    ConfiguredSource,
    DestinationConfig,
    DocumentLink,
    FailureOutcome,
    SourceFailure,
    SourceKind,
    StrictModel,
)
from visa_research_agent.domain.trust import host_is_within, host_of, registrable_domain
from visa_research_agent.research.errors import VisaResearchError
from visa_research_agent.research.live_sources import LiveSourceFetcher
from visa_research_agent.research.model_usage import ModelCallRecord, ModelUsageLog

DEFAULT_TEXT_COVERAGE_BAR = 0.5
"""What share of a corridor's candidates must have stored text before it may rank them.

A majority, and a statement rather than a tuned number — see `_text_scoring_is_fair`. Exposed as a
constructor argument only so the rule can be tested and measured against, never so a caller can
lower it to get more pages through.
"""

MINIMUM_ROLE_SCORE = 20.0
# How many pages the adjudicator gets to choose from. **This is a recall budget, not a precision
# one**, and reading it the other way is what kept it at ten for so long.
#
# The heuristic scorer does not decide anything: it decides what the model is allowed to see. So a
# page it ranks out of this window is one nothing downstream can recover, while a page it ranks in
# wrongly costs only an excerpt. The two errors are not symmetric, and the budget should reflect
# that.
#
# Measured 2026-08-18, changing only this number, live, on registry domains:
#
#   Canada       10 → refuses, no visa decision.  25 → every role filled.
#   Japan        10 → no visa decision.           25 → every role filled, same checklist.
#   Netherlands  10 → no checklist.               25 → checklist found.
#   Sweden       10 → two roles unfilled.         25 → unchanged; it fails for another reason.
#
# Two corridors that refused now resolve completely, and nothing regressed. **It bought more than
# any scoring rule in `scoring.py` does**, which is the finding, not the number.
#
# It is close to free. Fetching is concurrent, so the cost scales with batches rather than pages:
# Japan's corridor took 44.5s at ten and 39.3s at twenty-five, Canada's 45.2s and 41.7s — within
# noise both times, with no systematic penalty either way. Adjudication input roughly doubles, to
# about 19k tokens, which is small for one call.
#
# **Raised from 25 to 35 on 2026-08-24, together with the per-role depth below** (entry 61). The two
# move together and neither works alone: at depth 5 the reservation wants 30 places, so leaving the
# budget at 25 pushes the deepest reservations straight back out at truncation — measured, that
# dropped three previously-shortlisted pages per corridor and made the whole thing non-monotone,
# with depth 6 admitting *fewer* answers than depth 5. At 35 the truncation barely fires: replayed
# over 26 recorded corridors, **not one page that is shortlisted today is dropped.**
DEFAULT_SHORTLIST_SIZE = 35
# How many candidates each role reserves before the budget is filled best-first.
#
# **Three until 2026-08-24, and three was the reason the United Kingdom had no plan.** The page that
# decides a UK visa, `gov.uk/check-uk-visa`, scores 30.4 for `visa_decision` in every corridor —
# identically, because it names no country, which is what a page that *asks* your nationality does.
# What moves is the pages around it: for Nigeria and the Philippines it is 3rd for its role and got
# in; for India a ballot scheme on two paths and for China `ads-visa` and a translated-guidance page
# take the places, and at 5th it did not. Every one of those outranks it on `nationality:+40`, the
# scorer's largest term, awarded for a substring of the URL (TODO item 26).
#
# Five is the measured threshold rather than a guess, and the whole grid is in entry 61: depth 4
# admits nothing new, depth 5 admits the checker in all four UK corridors, and depths above it buy
# nothing until the budget grows to match. It is deliberately not a fix to the *scoring* — entries
# 56 and 57 both establish that "what does this page mean" is not a keyword question, and there is
# no honest keyword ranking an 804-character landing page above one that discusses visas at length.
DEFAULT_SHORTLIST_ROLE_DEPTH = 5
# Places held for each trusted domain before the rest are filled best-first. One is enough for what
# this protects against — an authority being shut out of the fetch entirely — and cheap: with the
# trusted set capped, the reserved places cannot crowd out the fill they exist to balance.
DEFAULT_SHORTLIST_DOMAIN_FLOOR = 1
# How much of each candidate the adjudicator is shown, per candidate rather than per packet. This
# is a **second recall gate** behind the shortlist, and entry 40's asymmetry applies to it
# unchanged: text the model never sees is text nothing downstream can recover.
#
# At 6,000, a flat head-of-page slice, it decided corridors on its own. `canada/GB/GB/tourism`
# ranked the right page first, fetched it, and refused: the sentence naming a "British citizen" as
# eTA-required sits at offset 8,947 of 16,465, because the page lists visa-required countries
# alphabetically and starts the eTA list only at 8,517. India at 5,325 was answered and every
# visa-exempt nationality was not, so whether a corridor resolved depended on where the traveller's
# nationality fell in an alphabet — and nothing in the output said so. See entry 42.
#
# Three numbers now, because widening alone scales badly across 25 candidates: the budget is the
# head plus a window centred on each later mention of the traveller's own country, and leftover
# budget reads straight on from the head.
#
# Measured 2026-08-21 over the 27 cached canada.ca/gc.ca pages in `var/cache`, page text across the
# packet goes from 84,704 characters to 153,862 (~+17k tokens for one call). **Almost all of that
# is the raise, not the anchoring**: a flat 20,000 costs 153,852 on the same pages, because 19 of
# the 27 are shorter than the head and only 2 exceed the budget at all. Anchoring is not what makes
# this affordable — it is what stops the raise from being another fixed offset. It changes nothing
# for a page under the budget and everything for one over it: on the 50,000-character visitor-visa
# PDF a US traveller's windows land at 19,452 and 24,449, and the second is text a flat 20,000
# cuts.
DEFAULT_EXCERPT_CHARACTERS = 20_000
# How many documents a corridor may open one hop below the pages it read (entry 210). Two, because
# a page usually lists one checklist per purpose and the label alone may not settle which applies.
MAXIMUM_FOLLOWED_DOCUMENTS = 2
# The head is kept whole because it carries the title, the "on this page" list and what the page is
# for. It is the old flat budget, so no page is now shown less of its head than before.
DEFAULT_EXCERPT_HEAD_CHARACTERS = 6_000
# Centred on the mention, not started at it: Canada's answering sentence — "You need an eTA … you
# don't need a visitor visa" — sits about 250 characters *before* the "British citizen" that
# anchors it, and a forward-only window would have cut exactly the sentence being looked for.
DEFAULT_EXCERPT_WINDOW_CHARACTERS = 3_000

# How many times the role adjudication may be asked before the corridor is refused. Two, so a
# momentary failure — a timeout, a rate limit, one malformed response — does not cost a corridor,
# while a real outage refuses instead of falling back to the heuristic. Retrying a *model provider*
# is not what DECISIONS entry 18 forbids; that is about an authority refusing to be read.
ADJUDICATION_ATTEMPTS = 2

# Why a likely checklist page may be named. `untrusted` landed off the approved domains, so its
# address is not one to send a traveller to. `blocked` is named here too, although the refusal is
# also reported: in the evidence banner it reads as a refusal, and the traveller looking for the
# documents looks in the documents panel. Switzerland's India embassy answered `403` for its
# "Checklist for Schengen Visa: Tourist" and the plan said no checklist was found (entry 213).
# Named, never read — entry 27's line.
NAMEABLE_CHECKLIST_OUTCOMES = frozenset(
    {"blocked", "challenged", "unreachable", "unusable", "disallowed"}
)
# How many a plan may name, most likely first. Australia named six on 2026-09-25, two of them help
# pages about evidence of funds, and a traveller shown six links cannot tell which is the list
# (entry 212).
MAXIMUM_NAMED_CHECKLISTS = 3
# A path segment a busy site redirects to instead of the page asked for. South Korea's ministry
# answers its checklist download with `/waitingroom/main.html` (entry 219). A queue is load, not a
# refusal: it is reported as such and never waited out or retried past.
WAITING_ROOM_MARKER = "waitingroom"


def says_it_is_this_trips_checklist(
    candidate: CandidatePage, corridor: Corridor, lexicon: Lexicon
) -> bool:
    """Whether the government page's own words around a link say it is this traveller's checklist.

    The bar for naming a page nobody read (entry 213, the owner): "only link it if the surrounding
    context makes us confident that it leads to the relevant checklist". A link score is not that —
    Australia named "Evidence of the financial status and funding for visit" on one. So:
    - the link's label or the page's recorded title contains a checklist phrase from the lexicon
      ("checklist", "documents required", "supporting documents", …); or
    - the heading it sits under does, **and** the label names this trip's purpose — Switzerland's
      "Tourist" under a checklists heading. A heading alone is not enough: under "Visa Application
      Documents" every form would qualify.
    - And neither label nor title names another purpose: "Checklist for Schengen business visa" is
      not a tourist's.
    """

    label = candidate.link.text.lower()
    title = (candidate.title or "").lower()
    heading = candidate.link.heading.lower()
    checklist_terms = lexicon.roles.get("document_checklist")
    phrases = [term.phrase.lower() for term in checklist_terms.terms] if checklist_terms else []
    own = lexicon.purposes.get(corridor.purpose)
    own_terms = [term.lower() for term in own.terms] if own else []
    other_terms = [
        term.lower()
        for purpose, terms in lexicon.purposes.items()
        if purpose != corridor.purpose
        for term in terms.terms
        if term.lower() not in own_terms
    ]
    if any(term in label or term in title for term in other_terms):
        return False
    if any(phrase in label or phrase in title for phrase in phrases):
        return True
    return any(phrase in heading for phrase in phrases) and any(term in label for term in own_terms)


def unread_checklist_pages(
    failures: list[SourceFailure],
    candidates: dict[str, CandidatePage],
    *,
    checklist_filled: bool,
    already_named: list[str],
    corridor: Corridor,
    lexicon: Lexicon,
) -> list[SourceFailure]:
    """Likely document-checklist pages this run tried and could not read, for a plan to name.

    TODO item 9. Nobody can show a checklist does not exist (entry 153), but a run can show it met a
    page that looked like one and could not open it — and the traveller can. "Likely" is the page's
    link score for the role, which is a reason to name it and never evidence of what it says.
    """

    if checklist_filled:
        return []
    named = set(already_named)
    pages: list[SourceFailure] = []

    def likelihood(failure: SourceFailure) -> tuple[float, str]:
        candidate = candidates.get(str(failure.attempted_url))
        score = candidate.link_scores.score_for("document_checklist") if candidate else 0.0
        return (-score, str(failure.attempted_url))

    for failure in sorted(failures, key=likelihood):
        url = str(failure.attempted_url)
        candidate = candidates.get(url)
        if (
            url in named
            or failure.outcome not in NAMEABLE_CHECKLIST_OUTCOMES
            or candidate is None
            or candidate.link_scores.score_for("document_checklist") <= 0
            or not says_it_is_this_trips_checklist(candidate, corridor, lexicon)
        ):
            continue
        named.add(url)
        pages.append(failure)
        if len(pages) >= MAXIMUM_NAMED_CHECKLISTS:
            break
    return pages


# At most this many pages for the traveller's own visa are named, most likely first (entry 219).
MAXIMUM_NAMED_VISA_PAGES = 2
VISA_WORD = "visa"
VISA_PAGE_ROLES: tuple[DiscoveryRole, ...] = ("application_route", "visa_decision")


def says_it_is_this_trips_visa_page(
    candidate: CandidatePage, corridor: Corridor, lexicon: Lexicon
) -> bool:
    """Whether the government's own words say a page is about the visa for this trip.

    Entry 213's bar for naming an unread checklist, applied to the traveller's visa (entry 219, the
    owner): Home Affairs' "Visitor visa (subclass 600) Tourist stream (apply outside Australia)" is
    chosen every run and its CDN refuses our browser. The label or recorded title must name a visa
    **and** this trip's purpose, and must name no other purpose and no audience a tourist is not
    ("working holiday", "student", "permanent" — the lexicon's `off_scope`). "Business visitor
    stream" and "Work and Holiday visa" fail it.
    """

    words = f"{candidate.link.text} {candidate.title or ''}".lower()
    own = lexicon.purposes.get(corridor.purpose)
    own_terms = [term.lower() for term in own.terms] if own else []
    excluded = [
        term.lower()
        for purpose, terms in lexicon.purposes.items()
        if purpose != corridor.purpose
        for term in terms.terms
    ] + [term.lower() for term in lexicon.off_scope.terms]
    excluded = [term for term in excluded if term not in own_terms]
    if VISA_WORD not in words or not any(term in words for term in own_terms):
        return False
    return not any(re.search(rf"\b{re.escape(term)}", words) for term in excluded)


def unread_visa_pages(
    failures: list[SourceFailure],
    candidates: dict[str, CandidatePage],
    *,
    read: list[str],
    already_named: list[str],
    corridor: Corridor,
    lexicon: Lexicon,
) -> list[SourceFailure]:
    """Pages about this traveller's visa that this run tried and could not read, for a plan to name.

    Named with their link and never read, exactly as an unread checklist is (entry 213): the
    traveller can open what this run could not. They fill no role, so a plan naming one still says
    whatever it would have said without it. **None is named once a page this run read passes the
    same test** (`read`, the addresses of the plan's sources): Australia's first run with Home
    Affairs readable read its Tourist stream page and still named two listing pages beside it.
    """

    if any(
        url in candidates and says_it_is_this_trips_visa_page(candidates[url], corridor, lexicon)
        for url in read
    ):
        return []
    named = set(already_named)
    pages: list[SourceFailure] = []

    def likelihood(failure: SourceFailure) -> tuple[float, str]:
        candidate = candidates.get(str(failure.attempted_url))
        score = max(candidate.combined(role) for role in VISA_PAGE_ROLES) if candidate else 0.0
        return (-score, str(failure.attempted_url))

    for failure in sorted(failures, key=likelihood):
        url = str(failure.attempted_url)
        candidate = candidates.get(url)
        if (
            url in named
            or failure.outcome not in NAMEABLE_CHECKLIST_OUTCOMES
            or candidate is None
            or not says_it_is_this_trips_visa_page(candidate, corridor, lexicon)
        ):
            continue
        named.add(url)
        pages.append(failure)
        if len(pages) >= MAXIMUM_NAMED_VISA_PAGES:
            break
    return pages


def failed_for_now(failures: Sequence[SourceFailure]) -> list[str]:
    """Pages that failed for a reason waiting could change: a `429`, a `5xx`, or no answer at all.

    Read from the recorded outcome and status, never the sentence (entry 36). A `401`, a bare `403`,
    a `404`, a `Disallow` or a challenge is a fact about the page and stays out of it.
    """

    pages: list[str] = []
    for failure in failures:
        status = failure.http_status
        temporary = status == 429 or (status is not None and 500 <= status <= 599)
        unanswered = failure.outcome == "unreachable" and status is None
        if failure.attempted_url is not None and (temporary or unanswered):
            pages.append(str(failure.attempted_url))
    return sorted(set(pages))


class SelectionRefusal(VisaResearchError):
    """The model chosen to pick what to read did not choose, so the corridor is refused.

    Until entry 258 the heuristic ranking chose instead, and the result was stored, then for three
    weeks. Australia `IN/SG` tourism, on 2026-10-05, had its selection call time out at Personas'
    gateway, the heuristic put the New Delhi High Commission's page in the application route for a
    traveller living in Singapore, and every later request was served that corridor. A worse
    chooser of what to read is not the worse *decider* entry 31 forbids, but it is a degraded answer
    that nobody downstream can see — so the run ends, says the check could not run, and stores
    nothing.
    `cause` is the recall log's: a failed call is `adjudication_failed`, a model that named no page
    is `no_candidates`.
    """

    def __init__(self, reason: str, cause: RefusalCause) -> None:
        super().__init__(reason)
        self.cause = cause


class AdjudicationRefusal(AdjudicationError):
    """Every adjudication attempt failed, so the corridor is refused rather than guessed at.

    Distinct from `AdjudicationError` so the retry loop can catch the ordinary failure without
    catching its own decision to give up, and so a caller can tell "this call failed" from "this
    corridor has no answer". Carries the attempt count because those calls were paid for.
    """

    def __init__(self, attempts: int, reason: str) -> None:
        super().__init__(f"role adjudication failed on all {attempts} attempts ({reason})")
        self.attempts = attempts


class FetchedShortlist(StrictModel):
    """The shortlisted pages that could actually be read, and the text they yielded.

    The ids and text are carried rather than discarded because adjudication needs to name pages
    back to the application, and the application must be able to check that what came back was a
    page it actually fetched.
    """

    candidates: list[CandidatePage] = Field(default_factory=list)
    by_id: dict[str, CandidatePage] = Field(default_factory=dict)
    contents: dict[str, str] = Field(default_factory=dict)
    links: dict[str, list[DocumentLink]] = Field(default_factory=dict)
    """The documents each read page links to, by source id (entry 210)."""
    documents: set[str] = Field(default_factory=set)
    """Source ids whose text came from a PDF rather than a web page (entry 212)."""
    landed: dict[str, str] = Field(default_factory=dict)
    """Where each read page's request landed after redirects, by source id (entry 219)."""
    followed: list[CandidatePage] = Field(default_factory=list)
    """Documents followed from those links, read or not, so a failed one can still be named."""
    failures: list[SourceFailure] = Field(default_factory=list)
    """Why the rest could not be read, carried out rather than dropped.

    This used to be discarded here, and the crawl was covering for it: every refusal a corridor
    reported came from `CrawlFetcher`, so a page refused at *retrieval* time reached
    `inaccessible_domains`, `inaccessible_urls` and the notes only if the crawl had happened to
    meet the same refusal first. With the crawl gone for a destination that has a corpus, this is
    the only place a refusal is observed at all — and a corridor that stops saying an authority
    refused it is the reporting discipline of DECISIONS entry 18 lost to a speed change.
    """


@dataclass
class ResolutionTrace:
    """What one run considered, filled in as it goes.

    A mutable scratch object rather than a return value, because the runs worth reading are the
    ones that end early: a corridor that refuses at "no candidate pages were found" still has
    queries and seeds worth seeing, and it never reaches a return that could carry them.
    """

    queries: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    candidates: dict[str, CandidatePage] = field(default_factory=dict)
    shortlisted: set[str] = field(default_factory=set)
    fetched: set[str] = field(default_factory=set)
    admitted_on_text: set[str] = field(default_factory=set)
    """Candidates offered to the model selector on their stored text alone (entry 158)."""
    withheld_from_selection: set[str] = field(default_factory=set)
    """Pooled candidates the model selector was not shown, because the pool was cut (entry 195)."""
    crawl_failures: dict[str, str] = field(default_factory=dict)
    fetch_failures: list[SourceFailure] = field(default_factory=list)
    """What reading the shortlist could not read, kept typed rather than flattened to a sentence.

    The crawl's failures were the only ones recorded until 2026-08-24, and once the crawl left the
    request path (entry 51) that meant none were. The shortlist fetch is the only stage that meets a
    refusal now.
    """

    role_verdicts: list[RoleVerdict] = field(default_factory=list)
    """What the role adjudicator said about each role, reasons included (TODO item 70)."""
    adjudicated_ids: dict[str, str] = field(default_factory=dict)
    """The adjudication packet's source ids and the pages they stood for."""

    selector: Literal["model", "heuristic"] = "heuristic"
    """Which selector **actually chose** the shortlist, not which one was configured.

    Set by `_choose_what_to_read` at the one point it can be known, and defaulting to the arm that
    always runs. Configuring a model selector is four steps away from a model having picked
    anything: the index may hold nothing for this destination, the pool may be empty, the call may
    fail, and the call may name no page this program recorded. Every one of those falls back to the
    heuristic ranking, honestly and by design (entry 83).

    It used to be derived at the write as `"model" if self.selector is not None else "heuristic"`,
    which is the *configuration*. On 2026-08-28 an OpenAI credit exhaustion took seven of twenty
    oracle corridors down that third path, and all seven wrote `model` — so `selection-recall`
    replayed the heuristic's own picks inside the model's arm and reported a number for an arm that
    had not run. That is entry 91's defect one level in: it recorded which selector was *available*
    and called it which selector *ran*. Entry 97.
    """

    refusal_cause: RefusalCause | None = None
    """Set only where a refusal is decided, for the two `ResolvedCorridor` cannot show.

    A run that found no candidates and a run whose adjudication failed both return a corridor with
    no sources, so `outcome_cause` cannot separate them from the result. Everything else is derived
    there rather than set here, because a value recorded twice drifts.
    """

    clock: Callable[[], float] = time.monotonic
    """Monotonic, and injected so a test can assert a duration without spending one.

    Deliberately not `CorridorResolver.now`: that is a wall clock for stamping *when* a run
    happened, and a wall clock can step backwards mid-run and report a negative phase."""

    phase_seconds: dict[str, float] = field(default_factory=dict)
    """Where this corridor's seconds went, by phase, accumulated rather than replaced.

    **The instrument entry 140 said was missing.** The goal has been stated in seconds since entry
    44 and nothing recorded any: `search_all` was found to be 19.0s of a 27.4s corridor only by
    timing it by hand outside the program, and the standing claim that adjudication dominated turned
    out to have been taken on a two-domain destination and never re-measured (entries 140, 141).

    Accumulated because a phase can be entered more than once, and a second visit replacing the
    first would under-report exactly the slow runs worth reading."""

    open_phase: tuple[str, float] | None = None
    """The phase running now. Not a timing itself — `begin` and `end` are the only readers."""

    on_phase: Callable[[str], None] | None = None
    """Told the name of each phase as it starts, so a waiting traveller can see how far the run has
    got (TODO item 57). It hears a phase's name and nothing the phase found."""

    def begin(self, phase: str) -> None:
        """Close whichever phase was running and start this one."""

        self.end()
        self.open_phase = (phase, self.clock())
        if self.on_phase is not None:
            self.on_phase(phase)

    def end(self) -> None:
        """Close the running phase, if any.

        Safe to call twice and called on every exit path, so a corridor that refuses early still
        records the phase it refused in — which is the run most worth reading, exactly as the class
        docstring says of the rest of this object.
        """

        if self.open_phase is None:
            return
        phase, started = self.open_phase
        self.phase_seconds[phase] = self.phase_seconds.get(phase, 0.0) + (self.clock() - started)
        self.open_phase = None


def _slugify(value: str, *, maximum: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned[:maximum].strip("_")


def build_source_id(destination_slug: str, url: str, taken: set[str]) -> str:
    """A stable identifier derived from the URL alone.

    Deriving it from the URL rather than the rank or role means the same page keeps the same id
    between runs, so two proposals can be compared.
    """

    prefix = _slugify(destination_slug, maximum=8) or "dst"
    host = host_of(url)
    host_label = _slugify(host.split(".")[0] if host else "src", maximum=16)
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    stem = _slugify(segments[-1].rsplit(".", 1)[0], maximum=24) if segments else "index"
    if not stem or stem.isdigit():
        stem = _slugify(segments[-2], maximum=24) if len(segments) > 1 else "page"

    base = "_".join(part for part in (prefix, host_label, stem) if part)[:48].strip("_")
    if not base or not base[0].isalnum():
        base = f"src_{base}".strip("_")

    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base[:44]}_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def confined_to_permitted_roles(
    decision: "RoleDecision", destination: DestinationConfig, notes: list[str]
) -> "RoleDecision":
    """Keep a supranational page to the roles its tier may answer, whoever assigned them.

    Applied to the decision as a whole, so it holds on the model's path and the heuristic's alike:
    the EU may answer a Schengen member's visa decision and nothing else (entry 201). A role taken
    from such a page is left unresolved unless another page answers it, and the notes say so. A
    tool on such a page is dropped the same way.
    """

    if not destination.supranational_domains:
        return decision
    permitted = set(destination.supranational_roles)
    sources: list[ResolvedSource] = []
    for source in decision.sources:
        if not destination.is_supranational(host_of(str(source.url))):
            sources.append(source)
            continue
        kept = [role for role in source.roles if role in permitted]
        for role in source.roles:
            if role not in permitted:
                notes.append(
                    f"{source.url} is {destination.supranational_authority}'s, which may answer "
                    f"only {', '.join(sorted(permitted))}, so it was not used for {role}"
                )
        if kept:
            sources.append(source.model_copy(update={"roles": kept}))
    filled = {role for source in sources for role in source.roles}
    unresolved = list(decision.unresolved) + [
        role
        for source in decision.sources
        for role in source.roles
        if role in REPORTED_ROLES and role not in filled and role not in decision.unresolved
    ]
    tools = [
        tool
        for tool in decision.tools
        if not destination.is_supranational(host_of(tool.url)) or tool.role in permitted
    ]
    return RoleDecision(
        sources=sources,
        unresolved=list(dict.fromkeys(unresolved)),
        model_calls=decision.model_calls,
        tools=tools,
        delegates=decision.delegates,
    )


def derive_authority(url: str, destination: DestinationConfig) -> tuple[str, SourceKind]:
    """Name the authority behind a URL, preferring what a human already wrote down."""

    host = host_of(url)
    # Before anything else: an EU page is the EU's, never the member state's (entry 201).
    if destination.is_supranational(host) and destination.supranational_authority:
        return f"{destination.supranational_authority} ({host})", "supranational_authority"
    for configured in destination.sources:
        if host_of(str(configured.url)) == host:
            return configured.authority, configured.kind
    for configured in destination.sources:
        if host_is_within(host, [host_of(str(configured.url))]):
            return configured.authority, configured.kind

    # An approved domain with no configured page yet: describe it plainly rather than guess.
    lowered = host.lower()
    if "emb" in lowered or "consul" in lowered:
        return f"{destination.display_name} mission ({host})", "embassy_or_high_commission"
    if "immi" in lowered or "ica" in lowered:
        return f"{destination.display_name} immigration authority", "immigration_authority"
    return f"{destination.display_name} authority ({host})", "foreign_ministry"


def _with_published_date(url: str, signals: list[str]) -> list[str]:
    """Prefix what the URL says about publication, so a reviewer sees it without digging."""

    published = published_date_in_path(url)
    return [f"published in path: {published}", *signals] if published else signals


def clean_title(raw: str | None, fallback: str) -> str:
    """Tidy a page title, dropping the trailing site name authorities append."""

    if not raw or not raw.strip():
        return fallback
    title = " ".join(raw.split())
    for separator in ("|", "—", " - "):
        if separator in title:
            head = title.split(separator)[0].strip()
            if len(head) >= 8:
                title = head
                break
    return title[:90] or fallback


@dataclass
class RoleDecision:
    """What the role step concluded: sources, and the two kinds of next step that fill nothing.

    A dataclass rather than a tuple because it has grown twice — questionnaires in entry 60,
    delegated services in entry 89 — and each time every caller had to be counted again to add a
    position. Both extra lists are deliberately *not* sources: naming one resolves nothing and
    cites nothing.
    """

    sources: list[ResolvedSource]
    unresolved: list[DiscoveryRole]
    model_calls: int = 0
    tools: list[ResolvedTool] = field(default_factory=list)
    delegates: list[ResolvedDelegate] = field(default_factory=list)


class CorridorResolver:
    """Find the official sources one traveller needs."""

    def __init__(
        self,
        provider: SearchProvider,
        crawl_fetcher: CrawlFetcher,
        live_fetcher: LiveSourceFetcher,
        *,
        lexicon: Lexicon | None = None,
        countries: CountryRegistry | None = None,
        shortlist_size: int = DEFAULT_SHORTLIST_SIZE,
        shortlist_role_depth: int = DEFAULT_SHORTLIST_ROLE_DEPTH,
        shortlist_domain_floor: int = DEFAULT_SHORTLIST_DOMAIN_FLOOR,
        minimum_role_score: float = MINIMUM_ROLE_SCORE,
        results_per_query: int = 8,
        adjudicator: RoleAdjudicator | None = None,
        excerpt_characters: int = DEFAULT_EXCERPT_CHARACTERS,
        excerpt_head_characters: int = DEFAULT_EXCERPT_HEAD_CHARACTERS,
        excerpt_window_characters: int = DEFAULT_EXCERPT_WINDOW_CHARACTERS,
        recall_log: RecallLog | None = None,
        usage_log: ModelUsageLog | None = None,
        corpus: CountryCorpus | None = None,
        page_text: PageTextStore | None = None,
        text_scoring_coverage_bar: float = DEFAULT_TEXT_COVERAGE_BAR,
        selection_shown: int = DEFAULT_SELECTION_SHOWN,
        selection_blind: int = DEFAULT_SELECTION_BLIND,
        selection_boost_searched: bool = False,
        selector: CandidateSelector | None = None,
        pinned: list[str] | None = None,
        always_read: list[tuple[PageLink, str]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.crawl_fetcher = crawl_fetcher
        self.live_fetcher = live_fetcher
        self.lexicon = lexicon or get_lexicon()
        self.countries = countries or get_country_registry()
        self.shortlist_size = shortlist_size
        self.shortlist_role_depth = shortlist_role_depth
        self.shortlist_domain_floor = shortlist_domain_floor
        self.minimum_role_score = minimum_role_score
        self.results_per_query = results_per_query
        # Optional on purpose. Without one the resolver behaves exactly as it did before
        # adjudication existed, which keeps the deterministic path as a regression baseline.
        self.adjudicator = adjudicator
        self.excerpt_characters = excerpt_characters
        self.excerpt_head_characters = excerpt_head_characters
        self.excerpt_window_characters = excerpt_window_characters
        # Optional, and nothing reads it back. A corridor behaves identically without one; what is
        # lost is the ability to answer "was that page ranked out, or never found" afterwards.
        self.recall_log = recall_log
        # Every model call, appended per day so a day's spend can be summed against the bill
        # (entry 166). Optional and read by nothing, like the recall log.
        self.usage_log = usage_log
        # Which corridor the calls being made belong to, set at the top of `resolve`.
        self.corridor_key: str | None = None
        # The country's known pages, seeded alongside search rather than instead of it. Optional, so
        # a resolver built without one behaves exactly as it did before the corpus existed.
        self.corpus = corpus
        # The body text of pages already fetched, read before the shortlist so a candidate can be
        # ranked by what its page says rather than only by the link pointing at it. Optional, and a
        # country with no index behaves exactly as it did before one existed (entry 78).
        self.page_text = page_text
        self.text_scoring_coverage_bar = text_scoring_coverage_bar
        # How much of a big pool the model selector sees: the best by `fusion_order`, plus pages
        # with no stored text on their links alone (entries 194, 195).
        self.selection_shown = selection_shown
        self.selection_blind = selection_blind
        # Whether pages live search returned are fused in as a third ranking (entries 243–245).
        # Off: adopting it, with a shorter list, is the owner's call (TODO item 78).
        self.selection_boost_searched = selection_boost_searched
        # Chooses what to fetch by reading stored page text, replacing `_shortlist` as the recall
        # gate. Optional and off unless one is supplied: without it the corridor behaves exactly as
        # it did before, which keeps the heuristic path as the regression baseline.
        self.selector = selector
        # Counted so a corridor's reported cost is its whole cost. A selection call that is not
        # counted is a call nobody is deciding whether to keep paying for.
        self.selection_calls = 0
        # URLs that already filled a role for this corridor. They keep their shortlist places
        # whatever the ranking says; see `_shortlist`.
        self.pinned = list(pinned or [])
        # Pages read on every corridor for this destination whatever the selector picks: the EU's
        # regulation and ETIAS page for a Schengen member, from the shared EU store (entry 201).
        # Traveller-neutral by construction — the same two pages for every nationality.
        self.always_read = list(always_read or [])
        # What this run considered, kept so the caller can fold it back into the corpus. A resolver
        # is built per corridor, so this is per-run state rather than something outliving a run —
        # the mistake entry 37 records about render budgets.
        self.discovered: list[PageLink] = []
        self.trace = ResolutionTrace()
        self.now = now
        # Separate from `now` on purpose: durations need a clock that cannot step backwards, and
        # `now` is a wall clock whose job is stamping when a run happened.
        self.monotonic = monotonic
        # Per-run like `selection_calls` beside it, and safe for the same reason: a resolver is
        # built per corridor. Cleared at the top of `resolve` anyway, so a caller that reuses one
        # gets this run's calls rather than every run's — entry 37 is the standing warning about
        # per-run state on an object that might outlive the run.
        self.model_call_timings: list[ModelCall] = []

    async def resolve(
        self,
        destination: DestinationConfig,
        corridor: Corridor,
        *,
        on_phase: Callable[[str], None] | None = None,
    ) -> ResolvedCorridor:
        """Resolve the corridor, and write down what it considered on the way.

        The trace is filled as the run proceeds rather than rebuilt at the end, so a corridor that
        refuses early still records how far it got — which is the run most worth reading.
        `on_phase` hears each phase's name as it starts.
        """

        trace = ResolutionTrace(clock=self.monotonic, on_phase=on_phase)
        self.model_call_timings = []
        self.corridor_key = corridor.key
        # Kept on the resolver so a caller can read what this run considered without re-reading the
        # recall log, which is overwritten per corridor and deliberately depended on by nothing.
        # Safe as per-run state because a resolver is built per corridor; the mistake entry 37
        # records is counting a *budget* on an object that outlives the run, not recording one.
        self.trace = trace
        resolved: ResolvedCorridor | None = None
        try:
            resolved = await self._resolve(destination, corridor, trace)
            return resolved
        finally:
            # Closes whichever phase was open, so a corridor that raised or refused early still
            # records where its seconds went rather than dropping the phase it died in.
            trace.end()
            self._write_recall_log(corridor, trace, resolved)

    async def _resolve(
        self, destination: DestinationConfig, corridor: Corridor, trace: "ResolutionTrace"
    ) -> ResolvedCorridor:
        nationality, residence = resolve_corridor_countries(corridor, self.countries)
        destination_code = self._destination_code(destination)
        notes: list[str] = []
        mission_domains = self._mission_domains(destination, residence)
        # Computed once per corridor rather than per link: it walks every country in the registry,
        # and the answer depends only on the corridor's two endpoints.
        other_posts = foreign_post_labels(self.countries, destination_code, residence)

        def score(link: PageLink) -> RoleScores:
            return score_link(
                link,
                corridor,
                self.lexicon,
                nationality,
                residence,
                mission_domains=mission_domains,
                other_posts=other_posts,
            )

        def reject(link: PageLink) -> str | None:
            if is_archived(link.url, self.lexicon):
                return "the path marks it as archived or superseded"
            if is_boilerplate(link.url, self.lexicon):
                return "the path marks it as site furniture rather than guidance"
            audience = wrong_audience(link, corridor, self.lexicon)
            if audience is not None:
                return f"the page is for {audience} holders, not this traveller"
            other = wrong_country(link, corridor, self.countries, destination_code)
            if other is not None:
                return f"the page is about {other}, which is not part of this corridor"
            return None

        # 1. Search to arrive.
        trace.begin("search")
        queries = corridor_queries(corridor, destination, nationality, residence)
        trace.queries = queries
        seeds: list[str] = []
        search_candidates: dict[str, CandidatePage] = {}
        # Every query at once, but the results walked in the order the queries were asked. Which
        # page a corridor resolves to depends on the order candidates arrive, so it must not depend
        # on which query the engine answered first.
        # A search outage does not have to end the corridor when the country's pages are already
        # on disk — but it must never be invisible, and it must never happen where there is nothing
        # to fall back to. DECISIONS entry 74.
        searched_without_error = True
        try:
            found = await search_all(self.provider, queries, count=self.results_per_query)
        except SearchError as exc:
            if not self._corpus_links(destination):
                # Nothing stored for this destination, so search was the only recall there was.
                # Falling through here would turn "we could not look" into "there is nothing".
                raise
            searched_without_error = False
            found = {query: [] for query in queries}
            notes.append(
                f"search was unavailable ({exc}), so this corridor was answered from "
                f"{destination.display_name}'s stored page corpus alone. Nothing was substituted "
                "for the pages search would have added, and this result is not kept for reuse."
            )
        for query in queries:
            results = usable_results(found[query], destination)
            for result in results:
                url = canonicalise_url(result.url)
                if not is_crawlable(url, destination):
                    continue
                # Truncated exactly as the crawler truncates anchor text. A search engine will
                # return a title far longer than any link on a page — China's ministry returned a
                # 300-plus character speech headline — and an over-long one raised rather than
                # being trimmed, taking the whole corridor down.
                link = PageLink(
                    url=url,
                    text=result.title[:300],
                    heading="",
                    depth=0,
                    discovered_from=query,
                )
                if reject(link) is not None:
                    continue
                if url not in search_candidates:
                    search_candidates[url] = CandidatePage(
                        link=link,
                        link_scores=score(link),
                        title=result.title,
                        found_by="search",
                        searched=True,
                    )
                if url not in seeds:
                    seeds.append(url)

        trace.seeds = seeds
        if not seeds and searched_without_error:
            # Guarded, because with search unavailable this sentence would be false of what was
            # seen: nothing was returned because nothing was asked. The note above already says so,
            # and two notes describing one event as two different failures is how a reader ends up
            # believing the corpus came up empty. Entries 33 and 36.
            notes.append("search returned nothing on an approved domain for this corridor")

        # 2. The country's known pages, which is what decides whether a crawl is worth running.
        trace.begin("corpus")
        #
        # Union with search rather than replacement: the corpus is not a superset of what a live run
        # finds — measured 2026-08-22, five of twenty-four pages a Canada run fetched were absent
        # from a 3,130-entry corpus, and one stayed absent when the exact query that had surfaced it
        # was re-run. Search is nondeterministic at the source, so no offline sweep can guarantee
        # the superset; the union is what closes the gap in both directions.
        #
        # Trust is applied here, at read time, against the domains in force now rather than those
        # in force when the corpus was built.
        corpus_links = self._corpus_links(destination)
        held = {canonical_key(link.url) for link, _ in corpus_links}
        candidates: dict[str, CandidatePage] = dict(search_candidates)
        from_corpus = 0
        for entry, title in corpus_links:
            if reject(entry) is not None:
                continue
            from_corpus += 1
            stored = CandidatePage(
                link=entry,
                link_scores=score(entry),
                # The corpus crawl recorded the page's own <title>; without this it would be
                # re-derived from the link text, which is what a crawl has to fall back on and
                # a store does not.
                title=title or None,
                found_by="corpus",
            )
            # **Best evidence about a page wins, exactly as it does for the crawl below** — this was
            # a `setdefault`, and it cost a live corridor its answer on 2026-08-23.
            #
            # Search and the corpus describe the same URL from different evidence. Search knows only
            # the engine's title; the corpus knows the anchor text and section heading an offline
            # crawl harvested from the page that links to it, and `link_text_weight` is why that is
            # worth far more. Measured on `canada/GB/GB/tourism`:
            # `entry-requirements-country.html` scores **63.4** from the corpus — "Entry
            # requirements by country or territory" under "Check if you need a visa or eTA" — and
            # **32.0** from
            # search's "What you need to enter Canada - Canada.ca". Seeded first, the search
            # candidate could not be displaced, and 32.0 missed the shortlist while 63.4 would have
            # been the second-best `visa_decision` candidate in it. The corridor refused.
            #
            # It was latent until entry 51 because the crawl re-found such pages with their real
            # anchor text and the loop below *does* compare scores, so the crawl was quietly
            # repairing what this line broke.
            existing = candidates.get(entry.url)
            if existing is None or stored.link_scores.best()[1] > existing.link_scores.best()[1]:
                # The corpus's evidence wins; the fact that search returned the page stays true.
                stored.searched = existing is not None and existing.searched
                candidates[entry.url] = stored

        # 3. Crawl to pinpoint — only when the corpus has not already out-covered it.
        trace.begin("crawl")
        crawled: list[CandidatePage] = []
        page_titles: dict[str, str] = {}
        if self._crawl_is_worth_running(from_corpus):
            crawler = LinkCrawler(self.crawl_fetcher, score, reject=reject)
            crawled = await crawler.crawl(destination, seeds)
            page_titles = crawler.titles
        else:
            notes.append(
                f"the crawl was skipped: {destination.display_name}'s stored page corpus already "
                f"offers {from_corpus} pages on currently trusted domains, more than a crawl could "
                "visit"
            )

        # Name the domains that could not be read at all. Without this a refusal reads as "nothing
        # scored well enough" when the real cause was an unreachable or client-rendered site, which
        # tells the reader to look in a completely different place.
        # A host's own crawl policy is reported separately, and always. Only the first failure per
        # host survives the loop below, so a `Disallow` folded into it could be masked by an
        # unrelated 404 elsewhere on the same site — and "we chose not to ask" is precisely the
        # fact that must never go missing, because it is the one a reader cannot infer from an
        # empty result. See DECISIONS entry 36.
        disallowed = self.crawl_fetcher.disallowed_urls()
        unreadable: dict[str, str] = {}
        for url, reason in self.crawl_fetcher.failures.items():
            if url in disallowed:
                continue
            unreadable.setdefault(host_of(url), reason)
        for host, reason in sorted(unreadable.items()):
            notes.append(f"{host} could not be read because {reason}")
        # Worded from the reason recorded per URL, never asserted. Written as "publishes a
        # robots.txt that does not permit this client", it claimed a policy nobody had read: China's
        # `avas.mfa.gov.cn` and `cova.mfa.gov.cn` answer `502` to every path including their own
        # policy, and the note called that a refusal. One note per host and distinct reason, so a
        # host with both kinds reports both rather than whichever came first.
        skipped: set[tuple[str, str]] = {
            (host_of(url), self.crawl_fetcher.failures[url]) for url in disallowed
        }
        for host, reason in sorted(skipped):
            notes.append(f"{host}: pages discovery reached were not fetched because {reason}")
        # An authority refusing this client is a different fact from a site being broken, and the
        # difference matters to a reader: one means "we were not allowed to check", not "no
        # guidance exists". Read from the recorded outcome rather than by matching the sentence,
        # so rewording a message cannot silently empty this list.
        # Domains cover every refusal including a rate limit, because reporting must not lose one.
        # The URL list is narrower: only a settled refusal may be handed to a traveller as a page
        # nobody was permitted to read, since a 429 might serve fine next week (entry 32).
        # **With no crawl these are empty, and the shortlist fetch is where refusals are seen** —
        # `_report_retrieval_refusals`, entry 49. Both are merged after the fetch.
        crawl_inaccessible = {host_of(url) for url in self.crawl_fetcher.blocked_urls()}
        crawl_refused = set(self.crawl_fetcher.persistent_refusals())

        for candidate in crawled:
            existing = candidates.get(candidate.link.url)
            if existing is None or candidate.link_scores.best()[1] > existing.link_scores.best()[1]:
                candidate.searched = existing is not None and existing.searched
                candidates[candidate.link.url] = candidate
        for url, candidate in candidates.items():
            if not candidate.title:
                candidate.title = page_titles.get(url) or candidate.link.text or None

        trace.candidates = candidates
        trace.crawl_failures = dict(self.crawl_fetcher.failures)
        # Only what *this run* found, never what the corpus already held: the caller folds this back
        # in, and a page arriving from the corpus has nothing to add to it.
        self.discovered = [
            candidate.link
            for url, candidate in candidates.items()
            if canonical_key(url) not in held
        ]
        if not candidates:
            trace.refusal_cause = "no_candidates"
            return self._refused(corridor, queries, notes, "no candidate pages were found")

        # 3b. Score whatever the text index already holds, before anything is ranked.
        #
        # **Order is the point.** `score_body` has always existed and has always run at step 5, on
        # pages that were already fetched — after the gate it should be part of. A page the anchor
        # scorer filed under the wrong role was never shortlisted, never fetched, and so never had
        # its text read at all. Measured on Japan: the page that fills `document_checklist` for
        # `japan/IN/GB` scores 22.0 as `visa_decision` from its anchor and is not a candidate for
        # the right role at any shortlist depth. Entry 78.
        #
        # The same scores decide what stored text may put back into the selector's pool at step 4
        # (entry 158), so they are computed once here and kept whether or not they may rank.
        stored_scores = self._stored_text_scores(
            destination, corridor, nationality, candidates, residence, other_posts
        )
        scored_from_text = self._score_from_text(stored_scores, candidates)
        if scored_from_text:
            notes.append(
                f"{scored_from_text} candidates were ranked on the text of the page as well as the "
                "link to it, from the stored page-text index"
            )

        # 4. Choose what to read, then fetch it through the ordinary retrieval path.
        # Timed apart because they fail and improve for different reasons: choosing is a model
        # call over stored text, fetching is the network and the render budget.
        trace.begin("select")
        try:
            shortlist = await self._choose_what_to_read(
                destination, corridor, candidates, stored_scores, notes, trace
            )
        except SelectionRefusal as exc:
            trace.refusal_cause = exc.cause
            notes.append(str(exc))
            return self._refused(corridor, queries, notes, str(exc))
        shortlist = self._with_always_read(shortlist, score, trace)
        trace.shortlisted = {candidate.link.url for candidate in shortlist}
        trace.begin("fetch")
        fetched = await self._fetch_bodies(destination, shortlist, corridor, nationality)
        # One hop further for the checklist: a document a page just read links to (entry 210).
        fetched = await self._follow_document_links(
            destination, corridor, nationality, fetched, score, reject, notes
        )
        # A followed document was never a candidate, so it is recorded here or the recall log
        # shows a page read that nothing chose.
        # The pool and the trace are one dict, so this also lets a followed document that could not
        # be read be named as a possible checklist (`unread_checklist_pages`, entry 211).
        for candidate in [*fetched.candidates, *fetched.followed]:
            trace.candidates.setdefault(candidate.link.url, candidate)
            trace.shortlisted.add(candidate.link.url)
        trace.fetched = {candidate.link.url for candidate in fetched.candidates}
        # Refusals met while reading the shortlist, folded in beside the crawl's. Both are
        # observations from this run; neither is complete on its own, and with no crawl the fetch
        # is the only one there is.
        # Kept on the trace as well as reported, because the recall log is the only place a refusal
        # can be counted across runs and it had been recording the crawl's alone.
        trace.fetch_failures = list(fetched.failures)
        fetch_inaccessible, fetch_refused = self._report_retrieval_refusals(fetched.failures, notes)
        inaccessible = sorted(crawl_inaccessible | fetch_inaccessible)
        refused = sorted(crawl_refused | fetch_refused)

        # 5. Assign roles from the combined evidence.
        trace.begin("adjudicate")
        try:
            decision = confined_to_permitted_roles(
                await self._decide_roles(destination, corridor, fetched, notes), destination, notes
            )
            sources = decision.sources
            unresolved = decision.unresolved
            tools = decision.tools
            delegates = decision.delegates
            model_calls = decision.model_calls
        except AdjudicationRefusal as exc:
            # Refuse rather than fall back to the heuristic. Degrading to the decider that named
            # Brazil's Riyadh page as a document checklist is not the conservative option — see
            # DECISIONS entry 31, which amends entry 16.
            trace.refusal_cause = "adjudication_failed"
            return self._refused(corridor, queries, notes, str(exc), model_calls=exc.attempts)

        # Which refused pages, if any, could have held the decision. Asked only when the decision is
        # actually missing and something was actually refused: `decision_blocking_urls` is read by
        # `is_usable` and `decision_is_unverified`, and both are inert once `visa_decision` is
        # filled. So the ordinary corridor makes no extra call at all. DECISIONS entry 57.
        decision_found = any("visa_decision" in source.roles for source in sources)
        # A tool is what to say when nothing stated the answer. Once a page did, it is at best a
        # second route to something already established, and offering it would tell the traveller to
        # go and work out what the plan already says. Applied per role, so a checklist found on a
        # page suppresses only the checklist tool.
        filled = {role for source in sources for role in source.roles}
        for tool in tools:
            if tool.role in filled:
                notes.append(
                    f"a {tool.role} tool was named at {tool.url} but a page answers it, "
                    "so it was not carried"
                )
        tools = [tool for tool in tools if tool.role not in filled]
        # The same suppression for a delegated service, and it must be the same: a role a page
        # answers does not also need the traveller sent to a contractor.
        delegates = [delegate for delegate in delegates if delegate.role not in filled]
        if decision_found or not refused:
            blocking = []
        elif self.adjudicator is None:
            # The deterministic path keeps the keyword test, exactly as `_decide_roles` does. This
            # is a configured mode, not entry 31's forbidden fallback from a failed call.
            blocking = self._decision_blocking(refused, candidates)
        else:
            blocking, blocked_calls = await self._decision_blocking_judged(
                corridor, refused, candidates, notes
            )
            model_calls += blocked_calls
        model_calls += self.selection_calls
        checklists = unread_checklist_pages(
            fetched.failures,
            candidates,
            checklist_filled="document_checklist" in filled,
            # Not `refused`: a refused checklist is named in both places (entry 213).
            already_named=[],
            corridor=corridor,
            lexicon=self.lexicon,
        )

        return ResolvedCorridor(
            corridor=corridor,
            resolved_at=self.now(),
            sources=sources,
            unresolved_roles=unresolved,
            notes=notes,
            inaccessible_domains=inaccessible,
            inaccessible_urls=refused,
            decision_blocking_urls=blocking,
            unread_checklist_pages=checklists,
            unread_visa_pages=unread_visa_pages(
                fetched.failures,
                candidates,
                read=[str(source.url) for source in sources],
                already_named=[str(page.attempted_url) for page in checklists],
                corridor=corridor,
                lexicon=self.lexicon,
            ),
            interactive_tools=tools,
            delegated_services=delegates,
            queries=queries,
            pages_fetched=len(shortlist),
            model_calls=model_calls,
            ran_without_search=not searched_without_error,
            unread_for_now=failed_for_now(fetched.failures),
        )

    def _with_always_read(
        self,
        shortlist: list[CandidatePage],
        score: Callable[[PageLink], RoleScores],
        trace: "ResolutionTrace",
    ) -> list[CandidatePage]:
        """The shortlist plus the pages read on every corridor, each once."""

        if not self.always_read:
            return shortlist
        held = {canonical_key(candidate.link.url) for candidate in shortlist}
        added: list[CandidatePage] = []
        for link, title in self.always_read:
            if canonical_key(link.url) in held:
                continue
            held.add(canonical_key(link.url))
            candidate = CandidatePage(
                link=link, link_scores=score(link), title=title or None, found_by="corpus"
            )
            trace.candidates.setdefault(link.url, candidate)
            added.append(candidate)
        return [*shortlist, *added]

    async def _choose_what_to_read(
        self,
        destination: DestinationConfig,
        corridor: Corridor,
        candidates: dict[str, CandidatePage],
        stored_scores: Mapping[str, RoleScores],
        notes: list[str],
        trace: ResolutionTrace,
    ) -> list[CandidatePage]:
        """Pick the pages to fetch — by model where one is configured, by the heuristic otherwise.

        The heuristic path is unchanged and is what runs today. The model path exists because the
        heuristic is the **recall gate**: measured over 135 recall logs it decides in 100 of them,
        with a median of 72 candidates in contention for 35 places, and a page it ranks out is never
        fetched and never judged (entry 40).

        Falling back is deliberate and **reported, never silent** — a country with no stored text
        would otherwise have its selection made from anchors alone, which is the weak version of
        this idea wearing the strong version's name.

        **The model is shown every candidate the link scorer rates above zero, plus the few their
        own stored text puts back** (entry 158). The link test alone showed it 6% of the candidate
        set and hid answers nothing in that 6% could replace (entries 123, 127, 128). The admission
        adds and never displaces, and is bounded per role so the packet stays bounded. The scores
        are the ones step 3b already computed for every candidate, so nothing is scored twice.
        """

        if self.selector is None or self.page_text is None:
            return self._shortlist(list(candidates.values()))
        admitted = admitted_on_text(
            [c for c in candidates.values() if c.best_combined()[1] <= 0], stored_scores
        )
        pool = [c for c in candidates.values() if c.best_combined()[1] > 0] + admitted
        if not pool:
            return self._shortlist(list(candidates.values()))

        code = self._destination_code(destination)
        held = (
            self.page_text.text_for_selection(code, [c.link.url for c in pool])
            if code is not None
            else {}
        )
        if not held:
            notes.append(
                "no stored page text was held for this destination, so the pages to read were "
                "chosen by the heuristic ranking rather than by reading them"
            )
            return self._shortlist(list(candidates.values()))

        offered, withheld = shown_to_selector(
            pool,
            stored_scores,
            lambda url: url in held,
            shown=self.selection_shown,
            blind=self.selection_blind,
            boost_searched=self.selection_boost_searched,
        )
        taken: set[str] = set()
        by_id: dict[str, CandidatePage] = {}
        text_by_id: dict[str, str] = {}
        for candidate in offered:
            source_id = build_source_id(destination.slug, candidate.link.url, taken)
            by_id[source_id] = candidate
            if candidate.link.url in held:
                text_by_id[source_id] = held[candidate.link.url]

        nationality, residence = resolve_corridor_countries(corridor, self.countries)
        packet = build_selection_packet(
            corridor,
            by_id,
            text_by_id,
            anchor_terms=sorted({*nationality.text_tokens, *residence.text_tokens}),
        )
        # Recorded once the packet exists, whatever the call then does: these pages were offered.
        trace.admitted_on_text = {candidate.link.url for candidate in admitted}
        trace.withheld_from_selection = {candidate.link.url for candidate in withheld}
        try:
            self.selection_calls += 1
            selection_prompt = load_selection_prompt()
            async with self._timed_model_call("select", selection_prompt, packet) as usage:
                selection = await self.selector.select(selection_prompt, packet, usage=usage)
        except SelectionError as exc:
            # Refused, not handed to the heuristic: see `SelectionRefusal` and entry 258.
            raise SelectionRefusal(
                f"candidate selection failed ({exc}), so nothing was chosen to read",
                "adjudication_failed",
            ) from exc

        chosen, discarded = validated_selection(selection, by_id)
        notes.extend(discarded)
        if not chosen:
            raise SelectionRefusal(
                "candidate selection named no page among those offered, so nothing was read",
                "no_candidates",
            )
        # Counted among what was shown, not what was pooled: the cut may withhold an admitted page.
        admitted_shown = sum(
            1 for candidate in admitted if candidate.link.url not in trace.withheld_from_selection
        )
        admitted_note = (
            f"; {admitted_shown} of those were offered on their stored text, the links to them "
            "having scored nothing for any role"
            if admitted_shown
            else ""
        )
        boosted = " and on search having returned them" if self.selection_boost_searched else ""
        withheld_note = (
            f"; {len(withheld)} of the {len(pool)} were not shown to it — it saw the "
            f"{self.selection_shown} ranked likeliest on their links and stored text{boosted}, and "
            f"{len(offered) - self.selection_shown} more with no stored text on their links "
            "alone, because a longer list chose worse (entry 194)"
            if withheld
            else ""
        )
        notes.append(
            f"{len(chosen)} of {len(offered)} candidates were chosen to read by a model shown what "
            f"{len(text_by_id)} of them say, rather than by ranking the links to them"
            f"{admitted_note}{withheld_note}"
        )
        # The only exit where a model picked the pages. Every `return self._shortlist(...)` above
        # is the heuristic doing the choosing, and each leaves the trace's default alone — which is
        # why this is set here rather than derived from `self.selector` at the write (entry 97).
        trace.selector = "model"
        return self._readable_only(selected_candidates(chosen, by_id))

    def _text_scoring_is_fair(self, scored: int, candidates: int) -> bool:
        """Whether the index covers enough of *this* candidate set to rank it.

        **A signal only some candidates carry cannot order them against each other**, and measured
        2026-08-26 that is not a theoretical worry — it cost `japan/IN/GB` two roles. The index held
        text for 115 of 860 candidates, spread by whatever hosts the crawl happened to reach: 90% of
        `evisa.mofa.go.jp`, 5% of `www.mofa.go.jp`, and **0% of `www.uk.emb-japan.go.jp`, the post
        that actually serves a traveller applying from Britain.** Every one of the eleven pages the
        lift added to the shortlist had index text; the eleven it displaced included the UK post's
        own fee and checklist pages. Corpus-only, three runs each way: with the lift Japan filled
        four roles every time and never `document_checklist` or `fees`; without it, four to six and
        always both.

        `combined` already refuses to let stored text *lower* a score. That protects the score and
        not the place — a shortlist is finite, so lifting some candidates displaces others, and the
        ones that cannot be lifted are the ones nobody crawled rather than the ones nobody needs.

        The bar is a majority, and it is a statement rather than a tuned number: below half,
        presence in the index predicts rank better than anything the page says, which is ranking by
        crawl coverage. Above it the minority is the exception rather than the rule. It is not a
        threshold to nudge — the fix for a country under it is to cover it ([TODO.md](TODO.md) item
        32), not to lower this.
        """

        return candidates > 0 and scored >= candidates * self.text_scoring_coverage_bar

    def _stored_text_scores(
        self,
        destination: DestinationConfig,
        corridor: Corridor,
        nationality: Country,
        candidates: dict[str, CandidatePage],
        residence: Country | None = None,
        other_posts: frozenset[str] = frozenset(),
    ) -> dict[str, RoleScores]:
        """Score, by stored text, every candidate whose page text the index holds.

        **Every candidate is offered, not a promising subset.** Narrowing first would put the link
        scorer back in front of the text scorer, which is the defect this exists to remove, and the
        same one `MAXIMUM_SCORED_MATCHES` records being made inside `rank` itself.

        Two things read the result, and their bars differ on purpose. `_score_from_text` may *rank*
        with it only where the index covers half the candidate set. `admitted_on_text` may put a
        page into the selector's pool on it anywhere, because a page scoring on its own text is
        worth showing whether or not its neighbours have text (entry 158).
        """

        if self.page_text is None:
            return {}
        code = self._destination_code(destination)
        if code is None:
            return {}
        return self.page_text.score_held(
            code,
            candidates.keys(),
            corridor=corridor,
            nationality=nationality,
            lexicon=self.lexicon,
            residence=residence,
            other_posts=other_posts,
        )

    def _score_from_text(
        self, scored: dict[str, RoleScores], candidates: dict[str, CandidatePage]
    ) -> int:
        """Attach `text_scores` to every candidate the index holds text for, where that is fair.

        Returns how many were scored, for the corridor's notes — a traveller-facing count of how
        much of this ranking rested on reading pages rather than reading links.
        """

        if not self._text_scoring_is_fair(len(scored), len(candidates)):
            return 0
        for url, scores in scored.items():
            candidate = candidates.get(url)
            if candidate is not None:
                candidate.text_scores = scores
        return len(scored)

    def _corpus_links(self, destination: DestinationConfig) -> list[tuple[PageLink, str]]:
        """The country's known pages and their titles, filtered by the domains trusted *now*.

        Read-time filtering is the point: a corpus outlives the registry row that produced it, so a
        domain a person later removes stops being offered without anyone rebuilding every corpus,
        and without deleting what was found.

        The title comes along because it is a thing the store knows and a crawl has to fetch a page
        to learn. Without it a corpus-sourced candidate falls back to its link text, which is the
        crawl's fallback rather than the store's.
        """

        if self.corpus is None:
            return []
        return [
            (entry.to_link(), entry.title)
            for entry in self.corpus.entries_within(destination.trusted_domains)
        ]

    def _crawl_is_worth_running(self, from_corpus: int) -> bool:
        """Whether walking the site adds anything the corpus has not already got.

        **Measured, and the answer for a built country is no** (DECISIONS entry 48): of the 25 pages
        that reached one Canada corridor's shortlist, 14 came from the crawl and **all 14 were
        already in the corpus**. The crawl contributed no unique shortlisted page while spending 62%
        of a 54-second corridor re-deriving a link graph the offline job had already mapped.

        The bound is derived rather than calibrated, which is why it is this and not a tuned number.
        A crawl visits at most `LinkCrawler.maximum_pages` pages — 40 — so a corpus already offering
        more candidate pages than that, on domains trusted right now, cannot be out-covered by one.
        Below it the corpus is not a map and the crawl is still the best thing available, which is
        the conditional entry 48 requires: **a country nobody has built must behave exactly as it
        does today.**

        What this deliberately does *not* claim is that the corpus is a superset. It is not
        (entry 47), which is why search still runs and why the write-back still folds what a live
        run found back in.
        """

        return from_corpus <= DEFAULT_CRAWL_PAGES

    def _report_retrieval_refusals(
        self, failures: list[SourceFailure], notes: list[str]
    ) -> tuple[set[str], set[str]]:
        """Report what reading the shortlist found out, in the shapes the crawl already reports in.

        Returns the hosts that refused this client and the URLs whose refusal was **settled**, so
        the caller can merge them with the crawl's. The notes are appended here because they read
        the same as the crawl's and a reader should not be able to tell which stage saw a refusal —
        only that one was seen.

        **This is the constraint DECISIONS entry 48 names, and it was already half-broken.**
        `_fetch_bodies` discarded `report.failures` entirely, so a page refused at retrieval time
        contributed nothing to `inaccessible_domains`, `inaccessible_urls` or the notes. The crawl
        covered for it by meeting the same refusals first. Remove the crawl and the cover goes with
        it, so this has to exist before that can happen.

        Every failure is noted, not only refusals. A shortlisted page that could not be read at all
        is the difference between "nothing scored well enough" and "the site would not give us the
        page", and that is precisely what a reader cannot infer from an empty result.

        `outcome` and `http_status` are read; `detail` is only ever repeated. Deciding from the
        sentence is what entry 36 forbids, because rewording a message would then silently empty a
        list something depends on.
        """

        blocked_hosts: set[str] = set()
        settled: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for failure in sorted(failures, key=lambda item: str(item.attempted_url)):
            url = str(failure.attempted_url)
            host = host_of(url)
            if failure.outcome == "blocked":
                blocked_hosts.add(host)
                # Only a settled refusal may be handed to a traveller as a page nobody was
                # permitted to read. A `429` might serve fine next week, and a refusal recorded
                # with no status is excluded, which fails toward *not* claiming we were blocked.
                # DECISIONS entry 32, the same rule `CrawlFetcher.persistent_refusals` applies.
                if failure.http_status in PERSISTENT_REFUSAL_STATUS_CODES:
                    settled.add(url)
            if (host, failure.detail) in seen:
                continue
            seen.add((host, failure.detail))
            if failure.outcome == "disallowed":
                note = f"{host}: pages discovery reached were not fetched because {failure.detail}"
            else:
                note = f"{host} could not be read because {failure.detail}"
            if note not in notes:
                notes.append(note)
        return blocked_hosts, settled

    def _decision_blocking(
        self, refused: list[str], candidates: dict[str, CandidatePage]
    ) -> list[str]:
        """Which refusals plausibly cost us the visa decision, rather than merely happening.

        A block only licenses saying the decision could not be verified if the page that refused
        could have answered the question. Otherwise the exception in `ResolvedCorridor.is_usable`
        stops being narrow: a `403` on a legal notice would resolve a corridor whose decision was
        simply never found, and WAF refusals on incidental pages are ordinary at scale.

        Credibility is read from the score the page already earned for `visa_decision` as a link.
        Anything above zero counts, and that is deliberately a low bar rather than a tuned one: the
        scorer has already **vetoed** site furniture, archived paths and wrong-audience pages
        outright, so a positive score means real visa-decision signal was seen rather than that a
        threshold was cleared. Nothing here is a judgement about what the page *says* — nobody read
        it, and nobody may.

        **Known limit, and it fails toward refusing.** Only pages the pipeline scored can be judged,
        and the crawl discards a page it could not fetch, so a refusal met for the first time at
        crawl depth is not in `candidates` and cannot qualify. In practice an authority's own visa
        portal is what search returns first — `france-visas.gouv.fr` is exactly that — so the case
        this exists for is covered. A corridor losing its answer this way refuses, which is the safe
        direction and the one this project prefers.
        """

        blocking: list[str] = []
        for url in refused:
            candidate = candidates.get(url)
            if candidate is not None and candidate.link_scores.score_for("visa_decision") > 0:
                blocking.append(url)
        return blocking

    async def _decision_blocking_judged(
        self,
        corridor: Corridor,
        refused: list[str],
        candidates: dict[str, CandidatePage],
        notes: list[str],
    ) -> tuple[list[str], int]:
        """Ask which refused pages could have held the decision, instead of keyword-matching them.

        The one place the heuristic was deciding what a page *means* rather than whether it was
        worth reading — and it was doing it on a page **nobody read**. DECISIONS entry 57; entry 56
        is what it cost, when Sweden's country list scored `visa_decision` 0.0 and an authority
        refusing the decision page could not make the decision unverifiable.

        **Fails closed.** Two attempts, then an empty list, which refuses the corridor exactly as
        nothing qualifying would. A model outage can never *create* a blocked-authority plan; it can
        only cost one, which is why it retries at all (entry 31's reasoning).
        """

        judged = {
            source_id: candidates[url]
            for source_id, url in (
                (build_source_id(corridor.destination_slug, url, set()), url)
                for url in sorted(refused)[:MAXIMUM_BLOCKED_JUDGED]
            )
            if url in candidates
        }
        if not judged or self.adjudicator is None:
            return [], 0

        by_id = {source_id: candidate.link.url for source_id, candidate in judged.items()}
        packet = build_blocked_packet(corridor, judged)
        prompt = load_blocked_prompt()
        calls = 0
        for attempt in range(1, ADJUDICATION_ATTEMPTS + 1):
            calls += 1
            try:
                async with self._timed_model_call("blocked", prompt, packet) as usage:
                    adjudication = await self.adjudicator.adjudicate(prompt, packet, usage=usage)
            except AdjudicationError as exc:
                if attempt < ADJUDICATION_ATTEMPTS:
                    notes.append(f"judging the refused pages failed ({exc}); retrying once")
                    continue
                # Not a fallback to the heuristic: this reports nothing rather than substituting a
                # decider whose keyword answer is the one entry 57 removed.
                notes.append(
                    f"the refused pages could not be judged after {ADJUDICATION_ATTEMPTS} "
                    "attempts, so none is treated as having held the visa decision"
                )
                return [], calls
            kept, discarded = validated_blocked_choices(adjudication, judged)
            for reason in discarded:
                notes.append(reason)
            return sorted(by_id[source_id] for source_id in kept), calls
        return [], calls

    def _mission_domains(self, destination: DestinationConfig, residence: object) -> list[str]:
        """Hosts that look like the post serving the traveller's residence.

        Missions sit on a per-country label of the umbrella domain, so a UK applicant is served by
        uk.emb-japan.go.jp. The label rarely matches the ISO code, which is why it comes from data.
        """

        labels = getattr(residence, "host_labels", [])
        found: list[str] = []
        for configured in destination.sources:
            host = host_of(str(configured.url))
            # Drop a leading "www." before reading the country label, or every host looks like
            # "www" and the mission is never recognised.
            bare = host[4:] if host.startswith("www.") else host
            first = bare.split(".")[0]
            if first and any(first == label for label in labels):
                found.append(bare)
        return found

    def _destination_code(self, destination: DestinationConfig) -> str | None:
        country = next(
            (
                country
                for country in self.countries.countries
                if country.name.lower() == destination.display_name.lower()
            ),
            None,
        )
        return country.code if country else None

    def _shortlist(self, candidates: list[CandidatePage]) -> list[CandidatePage]:
        """This resolver's settings applied to the module-level `shortlist`.

        The ranking is deliberately not a method any more: grading it against
        `oracle/selection_oracle.yaml` means replaying it from recorded scores at budgets nobody
        ran, and a resolver cannot be built for that without a fetcher, a search provider and a
        model client. What stays here is the one part that needs this run's state — dropping the
        candidates the crawl already proved unreadable.
        """

        return shortlist(
            self._readable_only(candidates),
            size=self.shortlist_size,
            role_depth=self.shortlist_role_depth,
            domain_floor=self.shortlist_domain_floor,
            pinned=self.pinned,
        )

    def _readable_only(self, candidates: list[CandidatePage]) -> list[CandidatePage]:
        """Drop candidates the crawl already found it could not read.

        Only two kinds are dropped, and only because each is a fact already established rather than
        a guess about what a fetch would do:

        * a host whose **name does not resolve** — no path under it can be read;
        * a URL an authority **refused this client** — asking again is a retry, which is exactly
          what must not be done, and it would answer the same way.

        Everything else stays. A page that was too large, was not HTML, or answered `502` is left in
        on purpose: retrieval is not the crawler. It reads PDFs, renders, and carries different
        limits, so a page the crawl could not use may still be readable evidence — and dropping
        those would trade a real answer for a tidier count.

        **With no crawl this does nothing, and the corpus's own `status` deliberately does not stand
        in for it** — which is what [TODO.md](TODO.md) item 22 proposed. Two reasons, and the second
        is the one that matters. First, there is nothing to stand in with: `corpus_build` writes
        `unreadable` or `unknown` and never `readable`, so Canada's 3,216 entries hold five
        unreadable and no readable ones. Second, this is a *fetch-budget* optimisation whose input
        today is an observation from **this run**. A stored refusal is an observation from another
        day, and skipping a page on one means the refusal is never seen live — so it can never reach
        `decision_blocking_urls`, and a France-shaped corridor, whose only settled `403` is on the
        page holding the decision, would stop resolving altogether (entries 27 and 32). The cost of
        not skipping is at most a few of twenty-five fetch places, on a step measured at 1.1s.
        """

        blocked = self.crawl_fetcher.blocked_urls()
        unresolvable = self.crawl_fetcher.unresolvable_hosts
        return [
            candidate
            for candidate in candidates
            if candidate.link.url not in blocked and host_of(candidate.link.url) not in unresolvable
        ]

    async def _fetch_bodies(
        self,
        destination: DestinationConfig,
        shortlist: list[CandidatePage],
        corridor: Corridor,
        nationality: object,
        *,
        taken: set[str] | None = None,
    ) -> "FetchedShortlist":
        """Read each shortlisted page and score its own text.

        The throwaway config is the point: building it re-runs `validate_route`, so a candidate
        that somehow left the approved domains cannot even be constructed, let alone requested.
        """

        shortlist = [
            candidate
            for candidate in shortlist
            # The country's own domains, or the union's for a member (entry 201). Not `trusts_host`,
            # which also admits a hand-configured appointed provider this path never reads.
            if host_is_within(host_of(candidate.link.url), destination.trusted_domains)
            or destination.is_supranational(host_of(candidate.link.url))
        ]
        if not shortlist:
            return FetchedShortlist()

        # Shared with an earlier read in the same run, so a later one cannot reuse an id.
        taken = taken if taken is not None else set()
        probe_sources: list[ConfiguredSource] = []
        by_id: dict[str, CandidatePage] = {}
        for candidate in shortlist:
            source_id = build_source_id(destination.slug, candidate.link.url, taken)
            authority, kind = derive_authority(candidate.link.url, destination)
            by_id[source_id] = candidate
            probe_sources.append(
                ConfiguredSource.model_validate(
                    {
                        "source_id": source_id,
                        "title": clean_title(candidate.title, candidate.link.url),
                        "url": candidate.link.url,
                        "authority": authority,
                        "kind": kind,
                        "research_pass": "primary",
                    }
                )
            )

        payload = destination.model_dump(mode="json")
        payload["sources"] = [source.model_dump(mode="json") for source in probe_sources]
        payload["application_document_source_ids"] = []
        payload["required_source_ids"] = []
        # An appointed provider is authorised by a named official page. Those pages are not in this
        # throwaway config, so the authorisation does not hold here and the provider is dropped
        # rather than assumed. Candidates were already restricted to trusted domains above.
        payload["appointed_providers"] = []
        probe = DestinationConfig.model_validate(payload)

        # Renders go to the pages most likely to answer, not to whichever needs one first (entry
        # 212). The roles a plan cannot do without count double.
        priority = {
            source_id: max(
                candidate.combined(role) * (2.0 if role in REPORTED_ROLES else 1.0)
                for role in ROLE_ORDER
            )
            for source_id, candidate in by_id.items()
        }
        report = await self.live_fetcher.fetch(probe, render_priority=priority)
        contents: dict[str, str] = {}
        links: dict[str, list[DocumentLink]] = {}
        documents: set[str] = set()
        landed: dict[str, str] = {}
        for item in report.fetched:
            contents[item.source.source_id] = item.content
            links[item.source.source_id] = list(item.document_links)
            if item.is_document:
                documents.add(item.source.source_id)
            if item.final_url:
                landed[item.source.source_id] = item.final_url
            fetched_candidate = by_id.get(item.source.source_id)
            if fetched_candidate is None:
                continue
            fetched_candidate.body_scores = score_body(
                item.content,
                item.source.title,
                corridor,
                self.lexicon,
                self.countries.require(corridor.passport_nationality),
                url=fetched_candidate.link.url,
            )
            fetched_candidate.content_hash = item.content_hash
        # Only pages that were actually readable can be proposed.
        readable = [source_id for source_id in contents if source_id in by_id]
        return FetchedShortlist(
            candidates=[by_id[source_id] for source_id in readable],
            by_id={source_id: by_id[source_id] for source_id in readable},
            contents=contents,
            links={source_id: links[source_id] for source_id in readable if source_id in links},
            documents={source_id for source_id in readable if source_id in documents},
            landed={source_id: landed[source_id] for source_id in readable if source_id in landed},
            failures=list(report.failures),
        )

    async def _follow_document_links(
        self,
        destination: DestinationConfig,
        corridor: Corridor,
        nationality: object,
        fetched: "FetchedShortlist",
        score: Callable[[PageLink], RoleScores],
        reject: Callable[[PageLink], str | None],
        notes: list[str],
    ) -> "FetchedShortlist":
        """Read the checklist a page this run read links to, when nothing chose it beforehand.

        Switzerland's India page lists its checklists as PDFs labelled "Business", "Tourist",
        "Personal visit"; the corridor read the page and never the PDF behind "Tourist", so the
        model saw the label and nothing under it (entry 210). The same gap a build closes one hop
        below a page it records (entry 88), closed here for the pages a corridor actually reads.

        Bounded three ways: only documents on the destination's own trusted domains, only ones
        whose label scores for `document_checklist` *for this traveller* after every veto a
        corridor applies to a link — another purpose, another passport type, an archived path —
        and at most `MAXIMUM_FOLLOWED_DOCUMENTS`. A followed document is fetched through the
        ordinary path, so trust, `robots.txt` and every refusal rule apply to it as to any page.
        """

        read = {candidate.link.url for candidate in fetched.candidates}
        offered: dict[str, CandidatePage] = {}
        for source_id, links in fetched.links.items():
            parent = fetched.by_id.get(source_id)
            if parent is None:
                continue
            for document in links:
                url = canonicalise_url(document.url)
                if url in read or url in offered:
                    continue
                if not host_is_within(host_of(url), destination.trusted_domains):
                    continue
                link = PageLink(
                    url=url,
                    text=document.text[:300],
                    heading=document.heading[:300],
                    depth=parent.link.depth + 1,
                    discovered_from=parent.link.url,
                )
                if reject(link) is not None:
                    continue
                scores = score(link)
                if scores.score_for("document_checklist") <= 0:
                    continue
                if not self._is_this_trips_checklist(link, corridor, scores):
                    continue
                offered[url] = CandidatePage(
                    link=link, link_scores=scores, title=document.text or None, found_by="crawl"
                )
        if not offered:
            return fetched

        chosen = sorted(
            offered.values(),
            key=lambda page: (-page.link_scores.score_for("document_checklist"), page.link.url),
        )[:MAXIMUM_FOLLOWED_DOCUMENTS]
        more = await self._fetch_bodies(
            destination, chosen, corridor, nationality, taken=set(fetched.by_id)
        )
        # A link labelled as a document must come back as one. South Korea's download address
        # answered with a page that read cleanly and held nothing (entry 212); it is unread, so it
        # is reported as such and may be named rather than quietly counted as read. That page was
        # the ministry's waiting room, and the reason says so where the landing address does
        # (entry 219): "returned a web page" was true and hid that the site was only busy.
        not_documents = [sid for sid in more.by_id if sid not in more.documents]
        for source_id in not_documents:
            candidate = more.by_id[source_id]
            authority, _kind = derive_authority(candidate.link.url, destination)
            queued = WAITING_ROOM_MARKER in urlsplit(more.landed.get(source_id, "")).path.lower()
            more.failures.append(
                SourceFailure(
                    source_id=source_id,
                    title=clean_title(candidate.title, candidate.link.url),
                    authority=authority,
                    outcome="unusable",
                    detail=(
                        "the site sent the request to its waiting room, a queue it uses when "
                        "busy, so the document was not opened here"
                        if queued
                        else "the link is labelled as a document and returned a web page instead"
                    ),
                    attempted_url=AnyHttpUrl(candidate.link.url),
                )
            )
        more = more.model_copy(
            update={
                "candidates": [
                    c
                    for c in more.candidates
                    if c.link.url not in {more.by_id[sid].link.url for sid in not_documents}
                ],
                "by_id": {sid: c for sid, c in more.by_id.items() if sid not in not_documents},
                "contents": {
                    sid: text for sid, text in more.contents.items() if sid not in not_documents
                },
            }
        )
        notes.append(
            f"{len(more.candidates)} of {len(chosen)} checklist documents linked from pages this "
            "run read were read as well"
        )
        return FetchedShortlist(
            candidates=[*fetched.candidates, *more.candidates],
            by_id={**fetched.by_id, **more.by_id},
            contents={**fetched.contents, **more.contents},
            links={**fetched.links, **more.links},
            failures=[*fetched.failures, *more.failures],
            followed=chosen,
        )

    def _is_this_trips_checklist(
        self, link: PageLink, corridor: Corridor, scores: RoleScores
    ) -> bool:
        """A document's own label names this trip's purpose, or calls itself a checklist and names
        no other purpose.

        The heading alone is not enough: under "Checklists" a PDF labelled "Business" scores for
        the checklist role on the heading, and is not this tourist's (entry 210)."""

        signals = scores.signals.get("document_checklist", [])
        if any(signal.startswith(("purpose:", "purpose-label:")) for signal in signals):
            return True
        label = link.text.lower()
        other_purposes = [
            term.lower()
            for purpose, terms in self.lexicon.purposes.items()
            if purpose != corridor.purpose
            for term in terms.terms
        ]
        if any(term in label for term in other_purposes):
            return False
        return "checklist" in label or "documents required" in label

    async def _decide_roles(
        self,
        destination: DestinationConfig,
        corridor: Corridor,
        fetched: "FetchedShortlist",
        notes: list[str],
    ) -> RoleDecision:
        """Choose the page for each role, by judgement when an adjudicator is configured.

        The fourth return is the interactive tools the model read and judged to hold a role's
        answer behind their questions. They are **only** ever produced here, on the path where the
        model was handed page text — the heuristic never produces one, because "is this page a
        questionnaire" is a question about meaning, and entry 57 is what keyword-matching meaning
        cost the last time. No adjudicator therefore means no tool, which is the deterministic
        baseline behaving exactly as it did before.

        The heuristic is not replaced. It produced the shortlist these candidates come from, and it
        is the answer when no adjudicator is configured — which keeps the deterministic path as the
        offline regression baseline.

        **What it is no longer is the fallback when a model call fails.** That read as the
        conservative choice and was the opposite: the heuristic is the decider entry 15 caught
        giving *confident wrong answers* — Brazil's Riyadh page named as the document checklist, at
        exit 0, with nothing in the output hinting the checklist came from a mission on another
        continent. So falling back turned a transient outage into exactly that, in production,
        visible only to someone who read `decided_by`. The call is retried once and then the
        corridor is refused, which every other layer of this project would do. Retrying is safe
        here because a model provider is not an authority refusing us — entry 18 does not apply.
        See entry 31.
        """

        if self.adjudicator is None or not fetched.candidates:
            sources, unresolved = self._assign_roles(destination, fetched.candidates, notes)
            return RoleDecision(sources=sources, unresolved=unresolved)

        # The traveller's own country words, so a long page is cut around them rather than at a
        # fixed offset. Nationality and residence only: the destination is named on every page it
        # publishes, so anchoring on it would anchor on nothing.
        nationality, residence = resolve_corridor_countries(corridor, self.countries)
        delegations = self._delegations_for(destination)
        packet = build_candidate_packet(
            corridor,
            fetched.by_id,
            fetched.contents,
            excerpt_characters=self.excerpt_characters,
            excerpt_head_characters=self.excerpt_head_characters,
            excerpt_window_characters=self.excerpt_window_characters,
            anchor_terms=sorted({*nationality.text_tokens, *residence.text_tokens}),
            delegations=delegations,
        )
        adjudication, model_calls = await self._adjudicate_with_one_retry(packet, notes)
        self.trace.role_verdicts = role_verdicts(adjudication, fetched.by_id, delegations)
        self.trace.adjudicated_ids = {
            source_id: candidate.link.url for source_id, candidate in fetched.by_id.items()
        }

        chosen, discarded = validated_choices(adjudication, fetched.by_id)
        confirming = validated_confirmations(adjudication, fetched.by_id, chosen)
        notes.extend(discarded)
        named, tool_discarded = validated_tools(adjudication, fetched.by_id)
        notes.extend(tool_discarded)
        sources, unresolved = self._sources_from_choices(
            destination, fetched, chosen, notes, confirming=confirming
        )
        tools: list[ResolvedTool] = []
        for role in ROLE_ORDER:
            tool = named.get(role)
            if tool is None or role == "irrelevant":
                continue
            source_id, reason = tool
            url = fetched.by_id[source_id].link.url
            tools.append(ResolvedTool(role=role, url=url))
            notes.append(f"{url} answers {role} interactively: {reason}")

        delegated, delegate_discarded = validated_delegates(adjudication, delegations)
        notes.extend(delegate_discarded)
        delegates: list[ResolvedDelegate] = []
        filled = {role for source in sources for role in source.roles}
        for role in ROLE_ORDER:
            chosen_delegate = delegated.get(role)
            if chosen_delegate is None or role == "irrelevant" or role in filled:
                continue
            delegate_id, reason = chosen_delegate
            record = delegations[delegate_id]
            delegates.append(ResolvedDelegate(role=role, url=record.url, named_on=record.named_on))
            notes.append(
                f"{record.url} is where {destination.display_name} sends this traveller for "
                f"{role}, according to {record.named_on}: {reason}"
            )
        return RoleDecision(
            sources=sources,
            unresolved=unresolved,
            model_calls=model_calls,
            tools=tools,
            delegates=delegates,
        )

    def _delegations_for(self, destination: DestinationConfig) -> dict[str, Delegation]:
        """The recorded places this country's own pages send a traveller, as selectable ids.

        Ids rather than URLs, and a separate namespace from `source_id`, so that a model can only
        ever *pick* one. Nothing here was fetched and nothing here has text, which is why they are
        not candidates and cannot be scored, cited or read.
        """

        if self.corpus is None:
            return {}
        corpus = self.corpus
        approved = destination.trusted_domains
        return {
            f"delegate-{index}": record
            for index, record in enumerate(
                sorted(corpus.delegations, key=lambda item: item.url), start=1
            )
            # The warrant is re-checked here, not assumed from the store: a domain narrowed since
            # the corpus was built must stop vouching for what it once linked.
            if host_is_within(host_of(record.named_on), approved)
        }

    async def _adjudicate_with_one_retry(
        self, packet: str, notes: list[str]
    ) -> tuple[RoleAdjudication, int]:
        """One retry, then let the failure through so the corridor refuses.

        A single retry, not a policy: the failures worth surviving are momentary ones — a timeout,
        a rate limit, one malformed response — and if the second attempt fails too, refusing is the
        honest answer rather than reaching for a decider known to be wrong with confidence. The
        attempt count is returned either way, because a refusal that cost two calls still cost
        them.

        **An empty account is not a momentary failure and is not retried.** The sentence above says
        what a retry is for, and "the account has no credit" is the one cause that fails that test
        outright: the second call cannot succeed, and it is billed the same as the first. This is
        entry 74's point about a `402` reaching the place where the retry decision is made — the
        classification is worth nothing if every caller retries anyway.
        """

        attempts = 0
        last: AdjudicationError | None = None
        while attempts < ADJUDICATION_ATTEMPTS:
            attempts += 1
            try:
                roles_prompt = load_adjudication_prompt()
                async with self._timed_model_call("roles", roles_prompt, packet) as usage:
                    adjudicated = await self.adjudicator.adjudicate(  # type: ignore[union-attr]
                        roles_prompt, packet, usage=usage
                    )
                return adjudicated, attempts
            except AdjudicationQuotaExhausted as exc:
                raise AdjudicationRefusal(attempts, str(exc)) from exc
            except AdjudicationError as exc:
                last = exc
                if attempts < ADJUDICATION_ATTEMPTS:
                    notes.append(f"role adjudication failed ({exc}); retrying once")
        raise AdjudicationRefusal(attempts, str(last))

    def _sources_from_choices(
        self,
        destination: DestinationConfig,
        fetched: "FetchedShortlist",
        chosen: dict[DiscoveryRole, tuple[str, str]],
        notes: list[str],
        *,
        confirming: dict[DiscoveryRole, tuple[str, str]] | None = None,
    ) -> tuple[list[ResolvedSource], list[DiscoveryRole]]:
        """Turn validated model choices into sources, honouring every refusal.

        A decision may carry a second page that brings the first into force (entry 209). It is added
        under the same role, so the plan sees and cites both.
        """

        by_url: dict[str, tuple[CandidatePage, list[DiscoveryRole], list[str]]] = {}
        unresolved: list[DiscoveryRole] = []

        for role in ROLE_ORDER:
            decision = chosen.get(role)
            if decision is None:
                if role in REPORTED_ROLES:
                    unresolved.append(role)
                    notes.append(f"no candidate was judged to be the {role.replace('_', ' ')}")
                continue
            source_id, reason = decision
            candidate = fetched.by_id[source_id]
            url = candidate.link.url
            if url in by_url:
                _, roles, reasons = by_url[url]
                roles.append(role)
                reasons.append(f"{role}: {reason}")
            else:
                by_url[url] = (candidate, [role], [f"{role}: {reason}"])

        for role, (source_id, reason) in (confirming or {}).items():
            candidate = fetched.by_id[source_id]
            url = candidate.link.url
            said = f"{role} (brings the announced revision into force): {reason}"
            if url in by_url:
                _, roles, reasons = by_url[url]
                if role not in roles:
                    roles.append(role)
                reasons.append(said)
            else:
                by_url[url] = (candidate, [role], [said])
            notes.append(
                f"the {role.replace('_', ' ')} rests on two pages read together: one naming the "
                "traveller's country, and a later one saying that revision took effect"
            )

        taken: set[str] = set()
        sources: list[ResolvedSource] = []
        for candidate, roles, reasons in sorted(
            by_url.values(), key=lambda item: (ROLE_ORDER.index(item[1][0]), item[0].link.url)
        ):
            authority, kind = derive_authority(candidate.link.url, destination)
            sources.append(
                ResolvedSource(
                    source_id=build_source_id(destination.slug, candidate.link.url, taken),
                    title=clean_title(candidate.title, candidate.link.url),
                    url=candidate.link.url,  # type: ignore[arg-type]
                    authority=authority,
                    kind=kind,
                    roles=roles,
                    # The heuristic score is still recorded, so a reviewer can see where the two
                    # deciders disagreed rather than only what the model concluded.
                    score=round(max(candidate.combined(role) for role in roles), 1),
                    decided_by="model",
                    signals=_with_published_date(
                        candidate.link.url, [reason[:160] for reason in reasons][:6]
                    ),
                )
            )
        return sources, unresolved

    def _assign_roles(
        self,
        destination: DestinationConfig,
        candidates: list[CandidatePage],
        notes: list[str],
    ) -> tuple[list[ResolvedSource], list[DiscoveryRole]]:
        """Pick the best page for each role independently.

        Roles are not exclusive. One page can be both the page that says a visa is needed and the
        page listing the documents, which is exactly how Singapore's per-nationality page is
        configured by hand, so forcing one role per page loses the right answer.
        """

        best_by_url: dict[str, tuple[CandidatePage, list[DiscoveryRole], float]] = {}
        unresolved: list[DiscoveryRole] = []

        for role in ROLE_ORDER:
            ranked = [
                (candidate, score)
                for candidate, score in rank_for_role(candidates, role)
                if score >= self.minimum_role_score
            ]
            if not ranked:
                # Reported, not only load-bearing: a missing checklist no longer refuses the
                # corridor, but leaving it unsaid would hide it from whoever reviews the result.
                if role in REPORTED_ROLES:
                    unresolved.append(role)
                    notes.append(f"no page scored high enough to be the {role.replace('_', ' ')}")
                continue

            candidate, score = ranked[0]
            url = candidate.link.url
            if url in best_by_url:
                existing_candidate, roles, best_score = best_by_url[url]
                roles.append(role)
                best_by_url[url] = (existing_candidate, roles, max(best_score, score))
            else:
                best_by_url[url] = (candidate, [role], score)

        taken: set[str] = set()
        sources: list[ResolvedSource] = []
        # Order by the most important role each page fills, so proposals read top-down.
        for candidate, roles, score in sorted(
            best_by_url.values(), key=lambda item: (ROLE_ORDER.index(item[1][0]), item[0].link.url)
        ):
            authority, kind = derive_authority(candidate.link.url, destination)
            signals: list[str] = []
            for role in roles:
                signals.extend(candidate.link_scores.signals.get(role, []))
            sources.append(
                ResolvedSource(
                    source_id=build_source_id(destination.slug, candidate.link.url, taken),
                    title=clean_title(candidate.title, candidate.link.url),
                    url=candidate.link.url,  # type: ignore[arg-type]
                    authority=authority,
                    kind=kind,
                    roles=roles,
                    score=round(score, 1),
                    decided_by="heuristic",
                    signals=list(dict.fromkeys(signals))[:6],
                )
            )
        return sources, unresolved

    @asynccontextmanager
    async def _timed_model_call(
        self, call: Literal["select", "roles", "blocked"], prompt: str, packet: str
    ) -> AsyncIterator[UsageRecorder]:
        """Time one model call and record what it was given and cost, whether or not it succeeds.

        Wrapped at the *call site* rather than inside the two providers on purpose: this measures
        what the resolver waits for, which is what a corridor's latency is made of, and it stays
        true of any provider without either implementation knowing it is being timed.

        **The recorder is yielded to the call site and handed to the provider, never read back off
        it** (entry 166). The web app shares one adjudicator between concurrent requests, so a
        `last_usage` field on it could hold another request's figures by the time this read it.
        """

        usage = UsageRecorder()
        started = self.monotonic()
        failed = False
        try:
            yield usage
        except BaseException:
            failed = True
            raise
        finally:
            # `None` throughout is "the provider said nothing", which is not zero — and a fake that
            # fills nothing in leaves every figure `None`.
            timing = ModelCall(
                call=call,
                prompt_characters=len(prompt),
                packet_characters=len(packet),
                seconds=round(self.monotonic() - started, 3),
                failed=failed,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_write_input_tokens=usage.cache_write_input_tokens,
                reasoning_output_tokens=usage.reasoning_output_tokens,
                http_requests=usage.http_requests,
            )
            self.model_call_timings.append(timing)
            self._record_usage(timing)

    def _record_usage(self, timing: ModelCall) -> None:
        """Append one call to the daily usage log, and never let doing so cost the corridor.

        The recall log keeps this run's calls too, but the next run of the same corridor overwrites
        it, so a day with repeat runs could not be summed from it — and a day's sum is what the
        provider's bill is checked against (entries 164 to 166).
        """

        if self.usage_log is None or self.corridor_key is None:
            return
        try:
            self.usage_log.write(
                ModelCallRecord(corridor_key=self.corridor_key, recorded_at=self.now(), call=timing)
            )
        except OSError:
            return

    def _write_recall_log(
        self,
        corridor: Corridor,
        trace: "ResolutionTrace",
        resolved: ResolvedCorridor | None,
    ) -> None:
        """Write the run down, and never let doing so cost the corridor an answer.

        An `OSError` here is swallowed deliberately: this is a diagnostic nothing reads back, so
        failing a resolution because a log file could not be written would trade an answer for a
        note about an answer. The failure is not silent in practice — the file is simply not there
        when someone goes looking, which is exactly what happened.
        """

        if self.recall_log is None:
            return
        outcome = "resolved"
        if resolved is None:
            outcome = "the run raised before it finished"
        elif not resolved.sources:
            outcome = resolved.notes[-1] if resolved.notes else "refused"
        elif resolved.unresolved_roles:
            unfilled = ", ".join(resolved.unresolved_roles)
            outcome = f"resolved, with no {unfilled}"
        # The sentence above and the value below say the same thing to different readers, and only
        # one of them can be counted. Where a refusal recorded its own cause that wins, because the
        # result cannot show it; everything else is derived from the result so the two cannot drift.
        cause: RefusalCause = "run_raised"
        if resolved is not None:
            cause = trace.refusal_cause or resolved.outcome_cause
        # Both stages, and the crawl's first so a page the fetch also met keeps the earlier reason.
        unreadable = dict(trace.crawl_failures)
        outcomes: dict[str, FailureOutcome] = {}
        for failure in trace.fetch_failures:
            url = str(failure.attempted_url)
            unreadable.setdefault(url, failure.detail)
            outcomes[url] = failure.outcome
        try:
            self.recall_log.write(
                RecallRecord(
                    corridor_key=corridor.key,
                    recorded_at=self.now(),
                    outcome=outcome,
                    cause=cause,
                    selector=trace.selector,
                    unresolved_roles=list(resolved.unresolved_roles) if resolved else [],
                    queries=trace.queries,
                    seeds=trace.seeds,
                    candidates=considered(
                        trace.candidates,
                        shortlisted=trace.shortlisted,
                        fetched=trace.fetched,
                        admitted_on_text=trace.admitted_on_text,
                        withheld_from_selection=trace.withheld_from_selection,
                    ),
                    unreadable=unreadable,
                    unreadable_outcomes=outcomes,
                    model_calls=list(self.model_call_timings),
                    role_verdicts=list(trace.role_verdicts),
                    adjudicated_ids=dict(trace.adjudicated_ids),
                    phase_seconds={
                        phase: round(seconds, 3) for phase, seconds in trace.phase_seconds.items()
                    },
                )
            )
        except OSError:
            return

    def _refused(
        self,
        corridor: Corridor,
        queries: list[str],
        notes: list[str],
        reason: str,
        *,
        model_calls: int = 0,
    ) -> ResolvedCorridor:
        """A corridor that produced no sources, with why, and what it cost getting there.

        `model_calls` is reported even though nothing was resolved: a refusal after two failed calls
        still spent money, and a cost that only appears on success is a cost nobody notices.
        """

        return ResolvedCorridor(
            corridor=corridor,
            resolved_at=self.now(),
            sources=[],
            unresolved_roles=list(REPORTED_ROLES),
            notes=[*notes, reason],
            queries=queries,
            model_calls=model_calls,
        )


def shortlist(
    candidates: list[CandidatePage],
    *,
    size: int = DEFAULT_SHORTLIST_SIZE,
    role_depth: int = DEFAULT_SHORTLIST_ROLE_DEPTH,
    domain_floor: int = DEFAULT_SHORTLIST_DOMAIN_FLOOR,
    pinned: Collection[str] = (),
) -> list[CandidatePage]:
    """The best few candidates per role, so only a handful of pages are ever fetched.

    Per-role first, so no role is crowded out by another's strong results, then the budget is
    filled with the next best overall. Filling it matters: taking three per role left four of
    Vietnam's ten places empty while every readable `evisa.gov.vn` page sat just outside the
    per-role cut, so the site that needed rendering most was never read.

    Then each domain's own best page is reserved a place, because the places are what decide
    what is read at all: an authority whose pages all fall below another's is never fetched, so
    it can never fill a role, so the corridor refuses with the answer sitting one place outside
    the cut. That is how a United States corridor refused while the mission serving the
    traveller went unread and eight federal domains competed for ten places.

    Pages the crawl already proved unreadable are dropped **by the caller**, before any of this.
    That step is `CorridorResolver._readable_only`, which stays a method because it asks the crawl
    fetcher what it observed this run. Everything below is pure, and that is what lets
    `discovery/selection_recall.py` replay the ranking from a recall log at budgets nobody ran.

    **Pinned pages take their places first, before any of the ranking runs.** A page that has
    already filled a role for *this* corridor should never have to win the ranking again — and
    it would increasingly have to, because seeding from the corpus grows the pool a great deal
    (Canada: 471 crawled candidates against roughly 3,000 held). Entry 40's asymmetry says a
    page ranked out is unrecoverable, so a larger pool means more pages lost to it. Pinning
    keeps the corpus from making the scorer *more* load-bearing rather than less.
    """

    chosen: dict[str, CandidatePage] = {}
    pinned_urls: set[str] = set()
    if pinned:
        wanted = {canonical_key(url) for url in pinned}
        for candidate in candidates:
            if canonical_key(candidate.link.url) in wanted:
                chosen.setdefault(candidate.link.url, candidate)
                pinned_urls.add(candidate.link.url)
    for role in ROLE_ORDER:
        for candidate, _ in rank_for_role(candidates, role)[:role_depth]:
            chosen.setdefault(candidate.link.url, candidate)

    by_score = sorted(candidates, key=lambda c: (-c.best_combined()[1], c.link.url))
    for candidate in by_score:
        if len(chosen) >= size:
            break
        # Only pages that scored for something. A candidate no role wants is not worth a fetch.
        if candidate.best_combined()[1] > 0:
            chosen.setdefault(candidate.link.url, candidate)

    # Reserved from every candidate rather than from those already chosen: a domain's best page
    # can be fourth for its role, which is exactly where the per-role cut leaves it.
    reserved = _reserved_per_domain(by_score, domain_floor)
    for candidate in reserved:
        chosen.setdefault(candidate.link.url, candidate)

    ordered = sorted(chosen.values(), key=lambda c: (-c.best_combined()[1], c.link.url))
    if len(ordered) <= size:
        return ordered

    # The truncation is where crowding out happens, so this is where both protections have to be
    # honoured. Anything held back earlier would simply be cut here instead.
    #
    # **Pins go first, and until 2026-08-23 they were not honoured here at all.** Entry 47 says
    # a page that already filled a role for this corridor "keeps its shortlist place regardless
    # of ranking", and it kept it only as far as `chosen`: `ordered` sorts by score and the tail
    # was cut without consulting `pinned_urls`, so a low-scoring pin was dropped — exactly
    # the pin that matters, since a high-scoring one never needed pinning. Measured on Canada,
    # `cbsa-asfc.gc.ca/travel-voyage/td-dv-eng.html` is `proven`, scores **0.0** on role
    # vocabulary, and was cut here with the pin naming it.
    reserved_urls = {candidate.link.url for candidate in reserved}
    # Pinned before reserved, and score order preserved inside each group because the sort is
    # stable: if the two protections together overflow the budget, a page that has answered this
    # corridor outranks a domain that merely has not been read yet.
    kept = sorted(
        (
            candidate
            for candidate in ordered
            if candidate.link.url in pinned_urls or candidate.link.url in reserved_urls
        ),
        key=lambda c: c.link.url not in pinned_urls,
    )
    del kept[size:]
    held = {candidate.link.url for candidate in kept}
    for candidate in ordered:
        if len(kept) >= size:
            break
        if candidate.link.url not in held:
            kept.append(candidate)
    return sorted(kept, key=lambda c: (-c.best_combined()[1], c.link.url))


def _reserved_per_domain(by_score: list[CandidatePage], floor: int) -> list[CandidatePage]:
    """Each trusted domain's best-scoring pages, up to the floor.

    Keyed on the registrable domain rather than the host, which is the unit trust is granted in.
    A mission network gives every post its own subdomain, so a per-host floor would let one
    authority reserve every place and recreate the crowding this exists to prevent. That is a
    deliberate difference from the crawl's per-host budget, which is about not hammering one
    site rather than about one site starving another.

    A page no role scored for is still not worth fetching, so the floor cannot admit one.
    """

    reserved: list[CandidatePage] = []
    per_domain: dict[str, int] = {}
    for candidate in by_score:
        if candidate.best_combined()[1] <= 0:
            continue
        domain = registrable_domain(host_of(candidate.link.url))
        if per_domain.get(domain, 0) >= floor:
            continue
        per_domain[domain] = per_domain.get(domain, 0) + 1
        reserved.append(candidate)
    return reserved
