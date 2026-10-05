"""Strict domain models shared by the API and future research workflow."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from visa_research_agent.domain.trust import host_is_within, host_of, is_bare_public_suffix

RouteType = Literal["national", "schengen_member"]
ImplementationStatus = Literal["planned", "available"]
SourceMode = Literal["fixtures", "live"]
ExtractionMode = Literal["fixture", "openai"]
# Whether a real browser may be started for pages that return nothing readable. Kept as policy
# rather than tuning because it changes how government sites are contacted.
RenderMode = Literal["never", "on_demand"]
# Which decider assigns roles during discovery. `heuristic` is deterministic, free and offline;
# `model` asks one bounded question over pages already fetched and trusted.
DiscoveryDecider = Literal["heuristic", "model"]
DiscoverySelector = Literal["heuristic", "model"]
# Where a destination's sources come from.
#   configured — only destinations hand-written in destinations.yaml can produce a plan.
#   automatic  — an unconfigured destination is resolved by discovery at request time, trusting
#                only domains belonging to that country's own government.
DestinationMode = Literal["configured", "automatic"]
ModelRoute = Literal["openai", "personas"]
# Why a source produced no usable evidence. These are kept apart because they need different
# remedies and, more importantly, they support different statements to a traveller.
#
# `blocked` is the subtle one. A site refusing automated clients is not saying its guidance is
# wrong or missing — it is saying this program may not read it. The only honest claim is then
# "we could not independently retrieve and verify this here", which is a much narrower statement
# than "unreachable" and must never be softened into an inference from some other page.
#
# `disallowed` is the same fact arrived at without a request: the host published a crawl policy
# that excludes this client, and it was obeyed (DECISIONS entry 36). Kept apart from `blocked`
# because the two license different things. A `403` was observed on the page itself; a `Disallow`
# is a rule about a path we never asked for, so it may be reported but must never resolve a
# corridor the way a settled refusal can.
#
# `challenged` is the third thing a `403` can be, and it is not a refusal. Cloudflare and Azure both
# answer one when they want to know whether the client is a browser: `france-visas.gouv.fr` serves a
# Cloudflare interstitial saying "enable JavaScript and cookies to continue" — for `/robots.txt` as
# well — and `www.gov.cy` serves an Azure WAF JS Challenge. **The authority stated nothing** in
# either case; it asked a question. So a challenge sits outside `blocked_urls()` and
# `persistent_refusals()`
# exactly as `disallowed` does, may never resolve a corridor, and may be answered by running the
# page's own scripts in our own renderer under our own user agent, which misrepresents us to nobody.
# DECISIONS entries 41 and 73.
#
# A challenge we cannot answer stays `challenged` rather than becoming `blocked`. "We could not
# prove we were a browser" and "the authority refused us" are different statements, and only one of
# them is about the authority.
FailureOutcome = Literal[
    "untrusted", "unreachable", "unusable", "blocked", "disallowed", "challenged"
]


# How a challenge announces itself. Cloudflare sets a header; **Azure declares it only in the
# body**,
# which is why a header-only test called Cyprus a refusal for half a day (DECISIONS entry 73).
#
# Deliberately narrow, and matched against markers rather than judged: Greece's `www.mfa.gr` answers
# an Akamai "Access Denied" with no script to run and no question asked, and it must keep being read
# as the refusal it is. Widening these strings until a refusal matches would quietly convert entry
# 18's rule into its opposite.
CHALLENGE_HEADER = "cf-mitigated"
AWS_CHALLENGE_HEADER = "x-amzn-waf-action"
"""AWS WAF's header. Its challenge arrives as `202` with the value `challenge` — not a refusal's
status at all, so without this it read as an empty success (EUR-Lex, entry 201). The value
`captcha` is not a challenge this program may answer, and is left to read as what it is."""
CHALLENGE_BODY_MARKERS = (
    "azure waf js challenge",
    "_cf_chl_opt",
    # AWS WAF's interstitial, which loads its token script and reloads the page (entry 201).
    "awswafintegration",
)
"""Markers that appear on a page **asking** a question, and not on one stating a refusal.

`cdn-cgi/challenge-platform` used to be here and was removed on 2026-08-29 (entry 109). It is
Cloudflare scaffolding shared by the challenge page *and* the block page, so it read
`travel.state.gov` — which answers **"Sorry, you have been blocked. You are unable to access
travel.state.gov"**, with no script to run — as a capability test. Measured side by side:
`france-visas.gouv.fr` carries `cf-mitigated: challenge`, `_cf_chl_opt` and "Enable JavaScript and
cookies to continue"; `travel.state.gov` carries none of them and only the shared marker.
"""

REFUSAL_BODY_MARKERS = (
    "you have been blocked",
    "attention required",
)
"""Cloudflare's block language, which settles the question before any challenge marker is consulted.

A negative test rather than a stricter positive one, because the cost of being wrong is asymmetric:
entry 18 forbids pointing the renderer at a page an authority **refused**, and a marker that starts
appearing on block pages would otherwise silently reopen exactly that. A page that says a client is
blocked has stated something, and no amount of Cloudflare scaffolding around it turns that into a
question.
"""


def is_challenge(status_code: int, headers: Mapping[str, str], body: str) -> bool:
    """Whether a response is a capability test rather than a refusal.

    Only `401`/`403`/`503` are considered at all — a challenge arrives wearing a refusal's status,
    and looking for these markers on a `200` would let a page that merely mentions Cloudflare be
    thrown away.
    """

    if status_code == 202:
        # AWS WAF's challenge is the only `202` that is one, and it says so in its own header.
        return headers.get(AWS_CHALLENGE_HEADER, "").strip().lower() == "challenge"
    if status_code not in {401, 403, 503}:
        return False
    # The **whole** body, not a prefix. It was capped at 20,000 characters, and Cloudflare puts
    # `_cf_chl_opt` *after* the interstitial's inline CSS: measured on `www.llv.li`, the marker sits
    # at index 24,915 of a 29,336-character rendered page, so the cap hid it by 4,915 characters.
    # The cost of that was silent — the renderer handed back the interstitial, this said "not a
    # challenge", and the crawl stored Cloudflare's "Performing security verification" page as
    # Liechtenstein's visa guidance for 29 pages (entry 117).
    #
    # Scanning it all is what a marker test has to do to mean anything: a prefix window makes
    # detection depend on how much CSS a vendor inlines above its own marker, which is not a
    # property of whether the page is a challenge. It is also cheap — 6.8ms on a 1.2MB body,
    # against a network fetch — and it strengthens the refusal check below in the same stroke,
    # which is the direction entry 18 wants to be wrong in.
    haystack = body.lower()
    # Checked first and deliberately: a page that states a client is blocked is a refusal whatever
    # else it carries, and entry 18 forbids rendering past one. Ordering it ahead of the header is
    # what makes this a guard rather than a tie-breaker.
    if any(marker in haystack for marker in REFUSAL_BODY_MARKERS):
        return False
    if headers.get(CHALLENGE_HEADER, "").strip().lower() == "challenge":
        return True
    return any(marker in haystack for marker in CHALLENGE_BODY_MARKERS)


# Statuses an authority returns to refuse an automated client rather than to report a fault.
BLOCKING_STATUS_CODES = frozenset({401, 403, 429})

# The subset whose refusal is **settled** rather than momentary, and so the only ones a page may be
# handed to a traveller on. `401` and `403` say "you may not read this", and waiting changes
# nothing. `429` says "not right now" — it is a rate limit, and the honest advice there is to try
# again later, exactly as DECISIONS entry 27 reasons about a `502`. Both are still reported as
# `blocked`, because entry 18 requires a refusal never to read as "nothing found"; the distinction
# governs only what may resolve a corridor or be named as guidance nobody was allowed to read.
PERSISTENT_REFUSAL_STATUS_CODES = frozenset({401, 403})

# The widest status a server can put on the wire, not the widest the standard defines. A status
# line is three digits (RFC 9112), so anything from 100 to 999 arrives parsed and has to be
# *recordable*; refusing to record one turns a strange server into a crashed corridor, which is
# what `mofa.gov.sa` answering **HTTP 990** did to both Saudi Arabia corridors on 2026-08-25
# (DECISIONS entry 71). Widening this cannot widen what a refusal may claim: everything that acts
# on a status tests membership of `PERSISTENT_REFUSAL_STATUS_CODES` or `BLOCKING_STATUS_CODES`
# above, and 990 is in neither.
MINIMUM_HTTP_STATUS = 100
MAXIMUM_HTTP_STATUS = 999
PlanStatus = Literal["verified", "partial"]
SourceKind = Literal[
    "immigration_authority",
    "foreign_ministry",
    "embassy_or_high_commission",
    "official_application_provider",
    # An authority above the country — the EU for a Schengen member (DECISIONS entry 201).
    "supranational_authority",
]
SourcePass = Literal["primary", "follow_up"]

# Why someone is travelling. Shared with discovery, which scores pages against it, so the two can
# never drift apart into a corridor nobody can research.
TravelPurpose = Literal["tourism", "business", "study", "transit"]

# ISO 3166-1 alpha-2. Countries are held as codes rather than names because a name has many
# spellings and a code has one, and every corridor, cache key and lexicon lookup is keyed by code.
COUNTRY_CODE_PATTERN = r"^[A-Z]{2}$"


class StrictModel(BaseModel):
    """Base model that rejects unexpected data instead of silently discarding it."""

    model_config = ConfigDict(extra="forbid")


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class TravellerProfile(StrictModel):
    """Who is travelling, in the terms that change which official pages apply.

    Only four things actually select the guidance: the passport, where the traveller applies from,
    why they are going, and — where they are not a citizen of the country they live in — what
    permission they hold there. Everything else is optional, because asking for detail a plan does
    not use is asking for personal data with no purpose.
    """

    passport_nationality: str = Field(pattern=COUNTRY_CODE_PATTERN)
    passport_type: Literal["ordinary"]
    """Ordinary only, deliberately. Diplomatic and official passport pages are a hard veto in
    discovery's scoring, so a plan for one of those travellers could not be researched and must
    not be silently approximated with the ordinary-passport rules."""

    country_of_residence: str = Field(pattern=COUNTRY_CODE_PATTERN)
    travel_purpose: TravelPurpose = "tourism"

    region_of_residence: str | None = None
    """The state, province or region they live in, chosen from committed reference data — the unit
    missions draw jurisdiction in (TODO item 71, DECISIONS entry 252). It replaced a city, which no
    form ever asked for: a region is matched against a page's own list, where a city would need
    geography inferred. None when the traveller did not say."""
    residence_status: str | None = None
    """The permission a traveller holds where they live, when they are not a citizen of it — a UK
    Graduate visa, a US green card. Frequently decisive: Brazil and China both require proof of
    regular status from a non-citizen resident. None when they are a citizen, or did not say."""

    residence_permission_expiry: date | None = None


class ConfiguredSource(StrictModel):
    """An approved official starting point for one destination."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    url: AnyHttpUrl
    authority: str = Field(min_length=1)
    kind: SourceKind
    research_pass: SourcePass = "primary"
    selection: "SourceSelection | None" = None
    """Why discovery chose this page. `None` for a hand-written source, which a person chose."""


class AppointedProvider(StrictModel):
    """A non-government domain authorised by a named official page.

    Appointed providers cannot pass domain trust by design, so their authority comes only from an
    official source that names them for this destination.
    """

    domain: str = Field(min_length=1)
    appointed_by: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")


class UnreadableAuthority(StrictModel):
    """One of the destination's own authorities that would not let this program read it.

    Carried so a plan can name it and hand the traveller the URL, which is a next step they can act
    on: they can open it in their own browser. It is **never evidence**. Nothing here says what the
    page contains — only that it exists, whose it is, and that we were not permitted to read it from
    here. That is the one claim a block licenses (DECISIONS entry 18), and it is the whole reason
    this is a separate type from `ConfiguredSource` rather than a source with empty content.
    """

    url: AnyHttpUrl
    authority: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    """A safe summary of what happened. Never carries retrieved page text."""

    may_hold_decision: bool = False
    """True when the refused-page judgement named this page as one that could have stated whether a
    visa is needed (entry 57). Set by the application from `decision_blocking_urls`, never by the
    extraction model, so a plan can point the traveller at it first (TODO item 54)."""


# What an official page can answer for a traveller. The same vocabulary discovery assigns to pages,
# minus `irrelevant`, which is a verdict rather than a topic. `DiscoveryRole` is built from this so
# the two can never drift apart — a tool named for a role the plan has no place for would be a link
# nobody ever sees.
GuidanceTopic = Literal[
    "visa_decision",
    "document_checklist",
    "application_route",
    "fees",
    "processing_times",
    "general_entry",
]


class DelegatedService(StrictModel):
    """A commercial company the destination's own page sends the traveller to for its guidance.

    The Netherlands is why this exists. For most residences its application page says, in as many
    words, "on the VFS Global website you'll find a checklist with the documents you need" — so the
    guidance is official, is current, and is not on any government domain. Measured 2026-08-27: of
    185 Dutch pages published one per residence, 113 link no checklist of their own (DECISIONS
    entries 88 and 89).

    **It is not a source and there is nowhere here to make it one.** No `content`, no excerpt, no
    `source_id`: the page is never fetched, so a claim about what it says could not be grounded in
    anything. It is the third member of a family — `UnreadableAuthority` for a page an authority
    refused (entry 27) and `InteractiveTool` for one that asks questions instead of answering
    (entries 59, 60) — and the family's rule is the same: **a next step the traveller can take and
    this program may not.**

    `appointed_by` is the warrant, and the reason it is required rather than optional. Without it
    this is a commercial URL; with it, it is a record that the destination's own government said
    this is where its guidance lives. A traveller shown one must be able to see who sent them.
    """

    topic: GuidanceTopic
    url: AnyHttpUrl
    provider: str = Field(min_length=1)
    appointed_by: str = Field(min_length=1)
    """The approved government page that linked it."""


class InteractiveTool(StrictModel):
    """An official page that *works out* an answer by questioning the traveller, not stating it.

    `gov.uk/check-uk-visa` is why this exists. It is served willingly, fetched cleanly and read in
    full, and it does not state whether a visa is needed: it is a questionnaire that computes an
    answer from replies this program does not have. Measured over twenty corridors, that cost every
    United Kingdom corridor its entire plan — the checklist, the route, the times and the
    per-nationality fees were all found, and all discarded (DECISIONS entry 58).

    It is deliberately **not** an `UnreadableAuthority`: nothing refused us, and saying so would be
    false. It is equally **not** a `ConfiguredSource` for its topic: the page states no answer, so
    citing it as evidence of one would be reading an answer out of a question. What it is, is a next
    step the traveller can take and this program cannot — they can answer the questions themselves —
    which is the same thing entry 27 offers for a refused page, and for the same reason.

    **It is not only the visa decision** (entry 60). An authority that puts its document checklist,
    its fees or its entry requirements behind a questionnaire has published that guidance; a plan
    that stayed silent about it would be withholding the one thing the traveller could act on. The
    topic says which question the tool settles, so the plan can offer it in the right place.

    Driving the questionnaire is not the alternative. That is an application flow, permanently out
    of scope in CLAUDE.md, and it would mean supplying traveller answers nobody gave us.
    """

    topic: GuidanceTopic
    url: AnyHttpUrl
    authority: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    """A safe summary of why the page did not answer. Never a guess at what answering would say."""


class DestinationConfig(StrictModel):
    """Country-specific research configuration."""

    slug: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(min_length=1)
    route_type: RouteType
    schengen_member: str | None = None
    implementation_status: ImplementationStatus = "planned"
    sources: list[ConfiguredSource] = Field(default_factory=list)
    application_document_source_ids: list[str] = Field(default_factory=list)
    trusted_domains: list[str] = Field(default_factory=list)
    appointed_providers: list[AppointedProvider] = Field(default_factory=list)
    required_source_ids: list[str] = Field(default_factory=list)

    supranational_authority: str | None = None
    """An authority above this country that may answer part of its question — the European Union,
    for a Schengen member (DECISIONS entry 201). `None` for every other destination."""

    supranational_domains: list[str] = Field(default_factory=list)
    """That authority's reviewed domains. Fetched under every rule a trusted domain is, and kept
    apart from `trusted_domains` so they never pass as this country's own government."""

    supranational_roles: list[str] = Field(default_factory=list)
    """The only roles a page from those domains may fill. The EU answers the visa decision."""

    challenge_script_hosts: dict[str, list[str]] = Field(default_factory=dict)
    """Per page host, the other hosts a render may reach to answer that page's challenge — only
    EUR-Lex's AWS token host, a narrow ruling on TODO item 61 (entry 201)."""

    unreadable_authorities: list[UnreadableAuthority] = Field(default_factory=list)
    """The destination's own authorities that refused this program, for the plan to point at."""

    unread_checklist_pages: list["SourceFailure"] = Field(default_factory=list)
    """Likely document-checklist pages discovery tried and could not read, for the plan to name.
    Never evidence of what they say — see `ResolvedCorridor.unread_checklist_pages`."""

    unread_visa_pages: list["SourceFailure"] = Field(default_factory=list)
    """Pages about this trip's visa discovery tried and could not read, for the plan to name. Never
    evidence of what they say — see `ResolvedCorridor.unread_visa_pages`."""

    official_tools: list[InteractiveTool] = Field(default_factory=list)
    """Official questionnaires that answer a question this destination publishes no page for."""

    delegated_services: list[DelegatedService] = Field(default_factory=list)
    """Companies this destination's own pages send the traveller to for a role it publishes no page
    for. Named, never read, never cited — see `DelegatedService`."""

    decision_is_unverified: bool = False
    """True when no page could be confirmed as saying whether a visa is needed, *and* the reason is
    one the traveller can act on rather than simply that nothing was found.

    There are two such reasons and they are different facts. An authority refused automated
    retrieval, so the page exists and we were not permitted to read it — `unreadable_authorities`.
    Or the authority publishes the answer only inside an interactive tool, which was read and asks
    questions rather than stating anything — an `official_tools` entry whose topic is
    `visa_decision`. Both leave the traveller one page to open; neither licenses an inference about
    what it would say.

    It is what lets a plan be produced at all in either case, so it must never be set without one of
    those — otherwise "we could not read it" and "the answer is behind a form" would both cover for
    "we did not find it", which needs a different remedy and must still refuse.

    A tool for any **other** topic does not set this and never could: only `visa_decision` is
    load-bearing, so a questionnaire holding the fees or the checklist adds a link to a plan that
    stands on its own."""

    @property
    def decision_tools(self) -> list[InteractiveTool]:
        """The tools settling the visa decision, which is the only load-bearing topic."""

        return [tool for tool in self.official_tools if tool.topic == "visa_decision"]

    @property
    def load_bearing_source_ids(self) -> list[str]:
        """Sources the plan cannot be produced without: the checklist, plus anything else required.

        A union, not a fallback. This used to be `required_source_ids or
        application_document_source_ids`, so naming any required source silently discarded the
        checklist requirement — a destination could declare a checklist and still produce a plan
        without it. Order is preserved so the reported reason names the checklist first.
        """

        combined = [*self.application_document_source_ids, *self.required_source_ids]
        return list(dict.fromkeys(combined))

    def trusts_host(self, host: str) -> bool:
        """True when a host is an approved authority domain or an appointed provider domain."""

        return (
            host_is_within(host, self.trusted_domains)
            or host_is_within(host, [provider.domain for provider in self.appointed_providers])
            or host_is_within(host, self.supranational_domains)
        )

    def is_supranational(self, host: str) -> bool:
        """True when a host belongs to the authority above this country rather than to it."""

        return (
            bool(self.supranational_domains)
            and host_is_within(host, self.supranational_domains)
            and not host_is_within(host, self.trusted_domains)
        )

    @model_validator(mode="after")
    def validate_route(self) -> "DestinationConfig":
        if self.route_type == "schengen_member" and not self.schengen_member:
            raise ValueError("a Schengen member route must identify its member country")
        if self.route_type == "national" and self.schengen_member is not None:
            raise ValueError("a national route cannot set schengen_member")

        source_ids = {source.source_id for source in self.sources}
        if len(self.application_document_source_ids) != len(
            set(self.application_document_source_ids)
        ):
            raise ValueError("application document source IDs must be unique")

        unknown_source_ids = set(self.application_document_source_ids).difference(source_ids)
        if unknown_source_ids:
            unknown = ", ".join(sorted(unknown_source_ids))
            raise ValueError(f"application document sources contain unknown IDs: {unknown}")

        unknown_required_ids = set(self.required_source_ids).difference(source_ids)
        if unknown_required_ids:
            unknown = ", ".join(sorted(unknown_required_ids))
            raise ValueError(f"required sources contain unknown IDs: {unknown}")

        if self.decision_is_unverified and not (self.unreadable_authorities or self.decision_tools):
            raise ValueError(
                "an unverified visa decision must name the authority that could not be read, or "
                "the official tool that decides it"
            )

        # These are presented to a traveller as this destination's own guidance, so they are held to
        # the same rule as evidence: officialness is a property of the domain. A page we could not
        # read is exactly the case where nothing else could vouch for it.
        for authority in self.unreadable_authorities:
            if not self.trusts_host(host_of(str(authority.url))):
                raise ValueError(
                    f"unreadable authority {authority.url} is not on an approved domain"
                )

        # The same rule, for the same reason. A tool's page *was* read, but nothing in its text is
        # what makes it official — entry 2 — and a traveller is being sent there to get an answer
        # this plan could not state, which is the last place to start trusting prose.
        for page in self.unread_checklist_pages:
            if not self.trusts_host(host_of(str(page.attempted_url))):
                raise ValueError(
                    f"unread checklist page {page.attempted_url} is not on an approved domain"
                )
        for page in self.unread_visa_pages:
            if not self.trusts_host(host_of(str(page.attempted_url))):
                raise ValueError(
                    f"unread visa page {page.attempted_url} is not on an approved domain"
                )

        for tool in self.official_tools:
            if not self.trusts_host(host_of(str(tool.url))):
                raise ValueError(f"official tool {tool.url} is not on an approved domain")

        for domain in [*self.trusted_domains, *self.supranational_domains]:
            if is_bare_public_suffix(domain):
                raise ValueError(
                    f"trusted domain {domain} is a public suffix and would trust every site "
                    "beneath it"
                )

        for provider in self.appointed_providers:
            if provider.appointed_by not in source_ids:
                raise ValueError(
                    f"appointed provider {provider.domain} names an unknown appointing source: "
                    f"{provider.appointed_by}"
                )

        # Every hand-configured URL must already satisfy the destination's own trust rules, so a
        # mistake in review fails at load time rather than during a research run.
        if self.sources and not self.trusted_domains:
            raise ValueError("a destination with sources must declare its trusted domains")
        untrusted = sorted(
            {
                str(source.url)
                for source in self.sources
                if not self.trusts_host(host_of(str(source.url)))
            }
        )
        if untrusted:
            listed = ", ".join(untrusted)
            raise ValueError(f"configured sources are not on a trusted domain: {listed}")
        return self


class ServiceProvider(StrictModel):
    """A commercial company a government delegates visa services to.

    **It is not an authority and this type must never be mistaken for one.** Nothing here may be
    fetched, read, quoted, cited, or counted as a source. The single thing a provider may do is be
    *named* to a traveller as a next step, and only when a page on the destination's own approved
    government domain linked it — which is DECISIONS entry 27's rule for a refused page and entries
    59 and 60's for a questionnaire, applied to guidance an authority publishes by delegation.
    """

    domain: str = Field(min_length=3)
    name: str = Field(min_length=1)
    note: str = ""


class ServiceProviderRegistry(StrictModel):
    """The reviewed list of delegates, from `config/service_providers.yaml`."""

    schema_version: Literal[1]
    providers: list[ServiceProvider] = Field(default_factory=list)

    @model_validator(mode="after")
    def _domains_are_registrable_and_unique(self) -> "ServiceProviderRegistry":
        seen: set[str] = set()
        for provider in self.providers:
            domain = provider.domain.lower().strip(".")
            if domain != provider.domain:
                raise ValueError(f"{provider.domain!r} must be written lowercase and unpadded")
            if "/" in domain or ":" in domain:
                raise ValueError(f"{provider.domain!r} must be a bare domain, not a URL")
            if domain in seen:
                raise ValueError(f"{provider.domain!r} is listed twice")
            seen.add(domain)
        return self

    @property
    def domains(self) -> frozenset[str]:
        return frozenset(provider.domain for provider in self.providers)

    def named(self, domain: str) -> ServiceProvider | None:
        return next((p for p in self.providers if p.domain == domain.lower()), None)


class DestinationRegistry(StrictModel):
    """Validated top-level destination configuration file."""

    schema_version: Literal[1]
    destinations: list[DestinationConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> "DestinationRegistry":
        slugs = [destination.slug for destination in self.destinations]
        if len(slugs) != len(set(slugs)):
            raise ValueError("destination slugs must be unique")

        source_ids = [
            source.source_id for destination in self.destinations for source in destination.sources
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique across the registry")
        return self

    def get(self, slug: str) -> DestinationConfig | None:
        normalized_slug = slug.strip().lower()
        return next(
            (
                destination
                for destination in self.destinations
                if destination.slug == normalized_slug
            ),
            None,
        )


class RuntimePolicy(StrictModel):
    """Version-controlled policy for what the agent may contact and how old evidence may be.

    These choices are reviewable rather than environment-local: they decide whether government
    websites are contacted, whether a paid model is called, and when stale guidance is refused.
    """

    schema_version: Literal[1]
    source_mode: SourceMode
    extraction_mode: ExtractionMode
    render_mode: RenderMode = "never"
    discovery_decider: DiscoveryDecider = "heuristic"
    discovery_selector: DiscoverySelector = "heuristic"
    """Who chooses which pages get fetched: the heuristic shortlist, or a model reading stored text.

    Default `heuristic`, which is the behaviour every measurement so far describes. `model` is
    DECISIONS entry 83's prototype, and it changes the *recall gate* rather than the decider — the
    adjudicator still decides, on text fetched in this run.
    """
    destination_mode: DestinationMode = "configured"
    model_route: ModelRoute = "openai"
    """How the model calls reach the model: OpenAI directly, on this project's key, or Ofself
    Personas as a plain call on Ofself's account (TODO item 62). The same prompts, packets, schemas
    and reasoning effort either way; one line of `runtime.yaml` switches them all."""
    source_cache_ttl_hours: float = Field(gt=0)
    source_maximum_stale_hours: float = Field(gt=0)
    plan_reuse_hours: float = Field(default=0, ge=0)
    """How long the model's draft of a plan may be reused for the same inputs; 0 turns reuse off.

    DECISIONS entry 178, amending entry 44. Only the draft is reused — every request still checks
    its quotes, runs every validator and grades its status on its own retrieval — and never for
    longer than the cache TTL, so a plan cannot outlive the freshness window of the pages it was
    written from.
    """

    @model_validator(mode="after")
    def validate_freshness_window(self) -> "RuntimePolicy":
        if self.source_maximum_stale_hours < self.source_cache_ttl_hours:
            raise ValueError("the stale ceiling cannot be shorter than the cache TTL")
        if self.plan_reuse_hours > self.source_cache_ttl_hours:
            raise ValueError("plan reuse cannot outlast the cache TTL of the pages behind it")
        return self


class SourceSelection(StrictModel):
    """Why discovery chose a page, carried to the plan so a reader can see it (TODO item 21).

    It stopped at the resolved corridor before, so *why was this page used for the checklist?* was
    answerable on disk and nowhere in a response (known problem 22). A diagnostic for whoever checks
    a plan, not a claim to a traveller: the score and signals come from links and anchors, never
    from a page's stored body text (entry 78).
    """

    roles: list[GuidanceTopic] = Field(min_length=1)
    decided_by: Literal["heuristic", "model"]
    score: float
    signals: list[str] = Field(default_factory=list)


class SourceReference(StrictModel):
    """A source actually consulted during a research run."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    url: AnyHttpUrl
    authority: str = Field(min_length=1)
    retrieved_at: datetime
    supporting_excerpt: str | None = None
    is_stale: bool = False
    """True when a refresh failed and cached text was served past its freshness window."""

    content_hash: str | None = None
    """The SHA-256 of the cleaned text this plan was read from, so a plan can be tied to the exact
    version of a page behind a claim (TODO item 21, known problem 21). Set when the plan is built,
    from this run's retrieval; `None` only on a reference that never became part of a plan."""

    selection: SourceSelection | None = None
    """Why discovery chose this page, from the destination the plan was built against. Set when the
    plan is built, never inside the retrieval cache, which corridors that chose differently
    share."""

    _validate_retrieved_at = field_validator("retrieved_at")(_require_aware_datetime)


class DocumentLink(StrictModel):
    """A document a fetched page links to: where, and what the page calls it.

    Read off the page's own markup by our code — never written by a model — so that a corridor can
    open the checklist a page it read points at (entry 210). It carries no content: until it is
    fetched through the ordinary path, nobody has read it.
    """

    url: str = Field(min_length=1)
    text: str = ""
    heading: str = ""


class FetchedSource(StrictModel):
    """Cleaned source material and retrieval metadata passed to extraction."""

    source: SourceReference
    content: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    from_cache: bool = False
    document_links: list[DocumentLink] = Field(default_factory=list)
    """Documents the page links to (entry 210). Addresses and labels only; never evidence."""
    is_document: bool = False
    """The text came from a PDF rather than a web page (entry 212)."""
    final_url: str | None = None
    """Where the request landed after redirects, when it was recorded. A followed document that
    lands in a site's waiting room says so from this (entry 219)."""


class SourceFailure(StrictModel):
    """A configured source that produced no usable evidence, and why."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    outcome: FailureOutcome
    detail: str = Field(min_length=1)
    """A safe summary of the cause. Never carries retrieved page text."""

    attempted_url: AnyHttpUrl
    final_url: AnyHttpUrl | None = None
    """Where the request actually landed, recorded when a redirect left the trusted domains."""

    may_hold_decision: bool = False
    """Only on a named refusal: the page could have stated whether a visa is needed. Copied from
    `UnreadableAuthority.may_hold_decision`, so the interface can lead with it (TODO item 54)."""

    http_status: int | None = Field(default=None, ge=MINIMUM_HTTP_STATUS, le=MAXIMUM_HTTP_STATUS)
    """The status the authority answered with, when there was one.

    `outcome` says a page was refused; this says whether waiting could change that. A `429` is a
    rate limit and a `403` is a settled refusal, and only the second supports telling a traveller
    that an authority would not permit this program to read a page — the distinction DECISIONS
    entry 32 draws, and `PERSISTENT_REFUSAL_STATUS_CODES` is what reads it.

    Carried structurally rather than left in `detail`, because entry 36's rule applies here too:
    what acts on a refusal must read a recorded outcome, never parse the sentence describing it, or
    rewording a message silently empties the list something else depends on. `CrawlFetcher` has
    kept the same fact in `refusal_statuses` since entry 32; this is the retrieval path catching up.

    None when no response was received at all — a DNS failure, a timeout, a `robots.txt`
    `Disallow` — which fails toward *not* claiming an authority refused us.
    """


class RetrievalReport(StrictModel):
    """Everything one retrieval pass produced: usable evidence and explained gaps."""

    fetched: list[FetchedSource] = Field(default_factory=list)
    failures: list[SourceFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> "RetrievalReport":
        source_ids = [item.source.source_id for item in self.fetched] + [
            failure.source_id for failure in self.failures
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("a retrieval report cannot report the same source twice")
        return self


class VisaRequirement(StrictModel):
    """One evidence-backed document requirement."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reason_it_applies: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


InPerson = Literal["required", "not_required"]
"""`required`: a page says the traveller must attend — to submit, give biometrics or be interviewed.
`not_required`: a page says the whole application is made without attending, online or by post."""


class ApplicationLocation(StrictModel):
    """Where and how the traveller should apply."""

    authority: str = Field(min_length=1)
    application_method: str = Field(min_length=1)
    location: str | None = None
    application_url: AnyHttpUrl
    source_ids: list[str] = Field(min_length=1)
    in_person: InPerson | None = None
    """Whether the traveller must go somewhere in person, only where a cited page says so (TODO item
    71, DECISIONS entry 252). None is "not stated", never "no"."""


class ApplicationLocationDraft(StrictModel):
    """Model-facing application details before the URL is validated by the app."""

    authority: str = Field(min_length=1)
    application_method: str = Field(min_length=1)
    location: str | None
    application_url: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    in_person: InPerson | None


class ApplicationStep(StrictModel):
    """One evidence-backed action in the traveller's ordered application timeline."""

    title: str = Field(min_length=3, max_length=70)
    """A short label. The step's substance belongs in `action`, not here."""
    action: str = Field(min_length=3, max_length=320)
    timing: str = Field(min_length=3, max_length=160)
    source_ids: list[str] = Field(min_length=1)
    link_target: Literal["application_route", "source", "none"]
    link_source_id: str | None

    @field_validator("title")
    @classmethod
    def tidy_title(cls, value: str) -> str:
        """Drop punctuation that means a sentence was still going.

        A model given 80 characters wrote to the limit and stopped mid-clause — "…if the wizard says
        a visa," — which reads as a truncation bug in the interface. The length is now tighter and
        the prompt asks for a label, but the trailing comma is trimmed here as well: a heading is
        derived text rather than a claim, so tidying it invents nothing.
        """

        return value.strip().rstrip(",;:").strip() or value.strip()

    @model_validator(mode="before")
    @classmethod
    def cite_the_linked_page(cls, data: Any) -> Any:
        """A step linking a page cites it, when the model left it out of `source_ids`.

        The model links the application page as `source` and cites the pages that state the step,
        and the check below then refused the whole plan — Germany `IN/GB` in 4 of 79 calls by
        entry 197, and again on 2026-09-24 after a verified answer was in hand (TODO item 70). Entry
        197 named this as one of two fixes should it recur. It adds no page: the id must still be a
        packet source, which `VisaPlan` checks for every step, and the page is where the step sends
        the traveller.
        """

        if (
            isinstance(data, dict)
            and data.get("link_target") == "source"
            and isinstance(data.get("link_source_id"), str)
            and isinstance(data.get("source_ids"), list)
            and data["link_source_id"] not in data["source_ids"]
        ):
            return {**data, "source_ids": [*data["source_ids"], data["link_source_id"]]}
        return data

    @model_validator(mode="after")
    def validate_link_target(self) -> "ApplicationStep":
        if self.link_target == "source":
            if self.link_source_id is None:
                raise ValueError("a source step link requires link_source_id")
            if self.link_source_id not in self.source_ids:
                raise ValueError("link_source_id must also appear in source_ids")
        elif self.link_source_id is not None:
            raise ValueError("link_source_id is only valid for a source step link")
        return self


APPLICATION_STEP_FLOOR = 2
"""How many steps an *application* must be described in before it is worth serving.

One line is not a timeline for a process with a submission and a wait, so a model that summarised
the route away is refused rather than rendered. It was four until entry 255, when the steps became a
timeline of what has to happen by when rather than a walk through the route: the linked official
pages carry the mechanics. See `_check_step_count` for why an entry plan is not held to it.
"""

# Five is what the prompt asks an application timeline for (entry 255), and deliberately not checked
# here: a plan with six to eight steps is a long timeline, which is what plans were before, and the
# owner chose to show it rather than refuse a correct answer for its length. The schema's eight
# holds.


def _check_step_count(visa_required: bool | None, steps: Sequence[ApplicationStep]) -> None:
    """The two-step floor holds for an application and is withheld from an entry plan.

    A traveller who needs no visa still has duties — Singapore asks for the SG Arrival Card, a
    passport valid past the stay and evidence of onward travel — and none of them is an application,
    so those duties are `application_steps` in this vocabulary and *entry* steps in the traveller's
    (DECISIONS entry 95). They are also however many the authority happens to state: three for
    Singapore, five for Japan, seven for the United Kingdom, whose visa-free visitors must still
    hold an ETA. The range has no natural floor and its low end is already under four, so a floor
    here would be a quota, and a quota on a list with no evidence left to draw from is an invitation
    to invent an entry duty. Inventing one is the alarming-wrong-answer class this project refuses
    outright, so the entry shape carries whatever the sources state and no minimum at all.

    **The short shape is therefore available only to a plan whose decision a page stated.** That is
    this check read from the other side, and it is the guard entry 95 is a decision for: a wrong
    "no visa required" that then suppresses the four remaining questions is worse than a wrong one
    that leaves them visible, because the traveller has nothing left to notice the error with.
    `visa_required` can only be `False` on a final plan when a page said so — extraction overrides
    it to `None` whenever `decision_is_unverified`, and `validate_tools_leave_their_questions_open`
    refuses a stated decision beside a questionnaire that settles it — so a blocked page and a tool
    are both already excluded, and neither can reach this shape.
    """

    if visa_required is False:
        return
    if len(steps) < APPLICATION_STEP_FLOOR:
        raise ValueError(
            f"a plan describing an application needs at least {APPLICATION_STEP_FLOOR} steps; "
            "only a plan stating that no visa is required may carry fewer"
        )


class VisaPlanDraft(StrictModel):
    """Structured extraction result before trusted source metadata is attached."""

    destination: str = Field(min_length=1)
    visa_required: bool | None
    decision_condition: str | None = None
    """The fact about this trip the decision holds on, where the profile does not record it — see
    `VisaPlan.decision_condition`."""
    visa_type: str | None
    explanation: str = Field(min_length=1)
    decision_source_ids: list[str] = Field(min_length=1)
    where_to_apply: ApplicationLocationDraft | None
    requirements: list[VisaRequirement]
    application_steps: list[ApplicationStep] = Field(max_length=8)
    unresolved_questions: list[str]

    @model_validator(mode="after")
    def validate_step_count(self) -> "VisaPlanDraft":
        _check_step_count(self.visa_required, self.application_steps)
        return self


class VisaPlan(StrictModel):
    """Final source-backed output returned by the workflow and API."""

    destination: str = Field(min_length=1)
    visa_required: bool | None
    decision_condition: str | None = None
    """The fact about this trip that `visa_required` holds on, where the traveller profile does not
    record it — "you stay in the airport's transit area and connect within 24 hours" — or `None`
    when the decision holds for this traveller as described.

    The owner's decision, entry 250: a plan may state a conditional answer. A transit answer turns
    on the layover, whether the traveller leaves the airport and the onward country, and a study
    answer on the length of stay; the profile holds none of them, so a correct answer the sources
    state for one side of such a fact used to come out as an open decision. The interface shows the
    condition beside the decision, and a conditional plan is never `verified`, because the
    traveller has not confirmed the fact it rests on."""
    visa_type: str | None
    explanation: str = Field(min_length=1)
    decision_source_ids: list[str] = Field(min_length=1)
    where_to_apply: ApplicationLocation | None
    requirements: list[VisaRequirement]
    application_document_source_ids: list[str]
    """May be empty: some authorities publish no checklist. See `validate_absent_checklist`."""
    application_steps: list[ApplicationStep] = Field(max_length=8)
    """The route, or — where no visa is required — the entry steps. See `_check_step_count`."""

    sources: list[SourceReference]
    unresolved_questions: list[str]
    """Also where a disagreement between official sources goes.

    There was a `conflicts` field beside this one, and it was deleted rather than improved. Entry 6
    built a *deterministic* conflict detector, found a real discrepancy with it, and deleted it
    anyway, because nothing recorded who a claim applied to: a page listing visa-free nationalities
    and a nationality-specific page requiring a visa compared as a contradiction. The lesson it
    recorded — a feature whose wrong answers are alarming needs a near-zero false-positive rate or
    it should not ship — condemns unverified model prose more strongly than the checked version it
    replaced. So a disagreement is reported here, as something we could not resolve, which is what
    it honestly is. See DECISIONS entry 30, and entry 6 before rebuilding it.
    """

    last_checked: datetime
    status: PlanStatus
    unavailable_sources: list[SourceFailure] = Field(default_factory=list)

    official_tools: list[InteractiveTool] = Field(default_factory=list)
    """Official questionnaires that answer a question no page in this plan could.

    Deliberately not folded into `unavailable_sources`: these pages were read, and reporting them
    as evidence that could not be used would be false about what happened. They are set by the
    application from the resolved corridor, never by the model, so the URL handed to a traveller is
    one an authority published rather than one that was generated.

    Each carries the topic it settles, so the interface can offer it beside the question it answers
    rather than in a footnote. A tool is never a substitute for the evidence it stands in for: one
    for `document_checklist` still leaves `application_document_source_ids` empty, so
    `validate_absent_checklist` still forbids listing any requirement (entry 60).
    """

    delegated_services: list[DelegatedService] = Field(default_factory=list)
    """Companies the destination's own pages send the traveller to for a question no page answered.

    Set by the application from the resolved corridor, never by the model, and the URL is one this
    program's crawler read out of an approved government page's markup — never a string a model
    produced from page text. That distinction is the safety argument: this is a link a traveller
    will follow, for the checklist.

    Like a tool, it fills nothing. One for `document_checklist` still leaves
    `application_document_source_ids` empty, so `validate_absent_checklist` still forbids the plan
    from listing a single requirement.
    """

    @property
    def decision_tools(self) -> list[InteractiveTool]:
        """The tools settling the visa decision, which is the only load-bearing topic."""

        return [tool for tool in self.official_tools if tool.topic == "visa_decision"]

    _validate_last_checked = field_validator("last_checked")(_require_aware_datetime)

    @model_validator(mode="after")
    def validate_status_matches_evidence(self) -> "VisaPlan":
        """A verified plan must have complete, current evidence behind every source."""

        if self.status == "verified":
            if self.unavailable_sources:
                raise ValueError("a verified plan cannot report unavailable sources")
            if any(source.is_stale for source in self.sources):
                raise ValueError("a verified plan cannot rest on stale evidence")
            if self.decision_tools:
                raise ValueError("a verified plan cannot rest on a decision nobody read off a page")
            if self.visa_required is None:
                # Held here as well as in extraction, which missed it: it downgraded only a
                # decision a block or a questionnaire stood in for (TODO item 53).
                raise ValueError("a verified plan cannot leave the visa decision unconfirmed")
            if self.decision_condition is not None:
                raise ValueError(
                    "a verified plan cannot rest on a condition the traveller has not confirmed"
                )
        return self

    @model_validator(mode="after")
    def validate_condition_has_a_decision(self) -> "VisaPlan":
        """A condition qualifies a stated decision; beside an open one it would qualify nothing."""

        if self.decision_condition is not None:
            if self.visa_required is None:
                raise ValueError("a decision condition needs a stated decision to qualify")
            if not self.decision_condition.strip():
                raise ValueError("a decision condition cannot be blank")
        return self

    @model_validator(mode="after")
    def validate_tools_leave_their_questions_open(self) -> "VisaPlan":
        """Naming a questionnaire and answering its question are mutually exclusive.

        A tool is named precisely because no page stated the answer. Stating one anyway would mean
        it came from somewhere else — the questionnaire's own prompts, most likely, which is reading
        an answer out of a question. Enforced here rather than asked for in the prompt, for the same
        reason `decision_is_unverified` is: a model asked for null returned `true`.

        Checked for the two topics a plan has a field to contradict itself in. The visa decision is
        the load-bearing one; the checklist is the one whose wrong answer this project exists to
        prevent, and `validate_absent_checklist` already blocks it from the other direction, so this
        is the same rule reached from the side where a tool exists.
        """

        if self.decision_tools and self.visa_required is not None:
            raise ValueError(
                "a plan naming an interactive decision tool cannot also state whether a visa is "
                "required"
            )
        if any(tool.topic == "document_checklist" for tool in self.official_tools) and (
            self.application_document_source_ids
        ):
            raise ValueError(
                "a plan naming an interactive document-checklist tool cannot also designate a "
                "checklist source"
            )
        return self

    @model_validator(mode="after")
    def validate_absent_checklist(self) -> "VisaPlan":
        """With no document source, a plan may state the gap but never fill it.

        This is the guard that makes a checklist-less corridor safe to serve. Without a designated
        document source there is nothing a requirement could honestly cite, so listing one means it
        was inferred from a page that is not a checklist — an eligibility rule or an application
        form read as though it were guidance. That is the single most damaging output this project
        can produce, so it is refused structurally rather than asked for politely in the prompt.
        """

        if self.application_document_source_ids:
            return self
        if self.requirements:
            raise ValueError(
                "a plan with no document checklist source cannot list document requirements"
            )
        if self.visa_required is False:
            # Nothing failed to be answered here: with no visa there is no application, so there
            # are no application documents to look for and no gap to report. Demanding a question
            # anyway is what made Singapore's corridor read as two of six while resolving perfectly
            # (DECISIONS entries 94 and 95) — the sentence a model would write to satisfy it, "no
            # official checklist was published", describes a search that failed rather than a
            # question that does not arise. The clause above is untouched and is the one that
            # matters: a plan with no document source still may not list a single requirement.
            return self
        if not self.unresolved_questions:
            raise ValueError(
                "a plan with no document checklist source must record what could not be answered"
            )
        return self

    @model_validator(mode="after")
    def validate_step_count(self) -> "VisaPlan":
        _check_step_count(self.visa_required, self.application_steps)
        return self

    @model_validator(mode="after")
    def validate_requirement_sources(self) -> "VisaPlan":
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs in a visa plan must be unique")

        cited_source_ids = set(self.decision_source_ids)
        cited_source_ids.update(
            source_id for requirement in self.requirements for source_id in requirement.source_ids
        )
        cited_source_ids.update(self.application_document_source_ids)
        cited_source_ids.update(
            source_id for step in self.application_steps for source_id in step.source_ids
        )
        if self.where_to_apply is not None:
            cited_source_ids.update(self.where_to_apply.source_ids)

        if self.where_to_apply is None and any(
            step.link_target == "application_route" for step in self.application_steps
        ):
            raise ValueError("application-route step links require an application location")

        unknown_ids = cited_source_ids.difference(source_ids)
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise ValueError(f"visa plan cites unknown source IDs: {unknown}")
        return self
