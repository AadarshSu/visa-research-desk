"""Resolving a destination nobody configured, without a person in the loop.

Until now a human approved every domain before anything was fetched. That gate is removed here,
and what replaces it is **not** trust in search — it is the rule the human was actually applying.
Reviewing six bootstraps, every accept and every reject reduced to one question: *is this domain
the destination country's own government?* Those are the two properties `bootstrap.py` already
computes, and the rule reproduces all 22 recorded human decisions with no disagreements.

**Where that rule runs changed on 2026-08-18 (DECISIONS entry 34).** It used to run inside every
cold request, cached per *corridor*, so a country's trusted set was re-derived from that day's
search rankings for every new nationality — entry 22's United States coin flip was this, diagnosed
at the time as ranking. The rule is unchanged and `auto_trusted_domains` below is still the whole of
it; what moved is *when*. It is now run offline for all 198 countries, read once by a person, and
committed as `authority_domains.yaml`. This service reads that file. Nothing here searches for a
domain any more, so two requests for the same country cannot disagree about whose government it is.

What the rule keeps out is the point of it. France's bootstrap surfaced `axa-schengen.com`, a
commercial travel insurer; Vietnam's ranked `usembassy.gov` first, which is a real government
describing the rules for *Americans*; Brazil's offered VFS, an appointed provider that by design
cannot pass domain trust. None of those is under the destination's own government, and none is
admitted.

The rule says which domains *may* be used. It does not say how many should be, and that turned out
to matter: it was calibrated against countries whose government is small enough that its own
top-level domain names only a handful of domains. A large government's whole namespace passes the
same rule, and the cost lands on everything downstream — three searches per trusted domain, a crawl
budget divided by the hosts seeded, and the shortlist's places. So the set is also capped, and
ordered by the authority hint the hostname carries, before anything is fetched.

Everything downstream is unchanged. Approved domains still pass `is_bare_public_suffix`, pages are
still fetched only from them, redirects and renders are still re-checked, and a corridor that
cannot fill a load-bearing role is still refused rather than filled in.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from visa_research_agent.discovery.bootstrap import BootstrapReport, DomainProposal
from visa_research_agent.discovery.corpus import (
    CorpusEntry,
    CorpusError,
    CountryCorpus,
    FileCorpusStore,
    canonical_key,
    merge,
)
from visa_research_agent.discovery.corridor_store import FileCorridorStore
from visa_research_agent.discovery.lexicon import (
    Country,
    CountryRegistry,
    Denylist,
    get_country_registry,
    get_denylist,
)
from visa_research_agent.discovery.models import Corridor, RefusalCause, ResolvedCorridor
from visa_research_agent.discovery.registry import (
    MAXIMUM_AUTO_TRUSTED_DOMAINS,
    AuthorityRegistry,
    get_authority_registry,
)
from visa_research_agent.discovery.resolver import CorridorResolver
from visa_research_agent.discovery.search import SearchProvider
from visa_research_agent.discovery.supranational import union_of, union_pages, with_union
from visa_research_agent.domain.models import DestinationConfig, StrictModel
from visa_research_agent.research.errors import VisaResearchError

DiscoveryRefusal = Literal[
    "not_supported", "no_official_answer", "pages_unreadable", "check_failed"
]
"""Why discovery refused, in the terms a traveller acts on (TODO item 73).

Coarser than `RefusalCause`, which counts runs: `decision_not_found` splits here by whether an
official page refused us, and a country nobody has built is a refusal no run ever records."""


class AutomaticDiscoveryError(VisaResearchError):
    """Raised when a destination cannot be resolved automatically, with a reason to show."""

    def __init__(
        self,
        message: str,
        *,
        cause: DiscoveryRefusal,
        unreadable_urls: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.cause: DiscoveryRefusal = cause
        # Named so the traveller can open them, never read (entry 27): 401/403 refusals only.
        self.unreadable_urls = unreadable_urls or []


# Re-exported: the cap now belongs to a registry row rather than to a live bootstrap, so it is
# defined beside the registry. Imported from here by callers and tests that named it first.
__all__ = [
    "MAXIMUM_AUTO_TRUSTED_DOMAINS",
    "AutomaticDestinationService",
    "AutomaticDiscoveryError",
    "DiscoveredDestination",
    "PreparedDestination",
    "auto_trusted_domains",
    "is_own_government",
    "unconfirmable_authorities",
]


def is_own_government(proposal: DomainProposal) -> bool:
    """The rule that replaces the human approval gate."""

    return proposal.is_own_government


def _trust_priority(proposal: DomainProposal) -> tuple[int, int, str]:
    """Order own-government domains by how likely they are to be a visa authority.

    This ranks *within* a set every member of which has already passed domain trust; it is not part
    of deciding whether a domain is official. `suggested_kind` is read from the hostname alone —
    `emb`, `consul`, `immi`, `mofa` — the same class of evidence as `looks_governmental` and the
    same inference `derive_authority` makes when naming an authority. Nothing about how a page reads
    or where search ranked it enters here, so two runs over the same proposals order them the same.

    Among domains with no hint and equal corroboration the order is alphabetical, which is
    arbitrary but stable: a hostname carries no further signal about whether that part of a
    government issues visas.
    """

    return (0 if proposal.suggested_kind else 1, -proposal.corroboration, proposal.domain)


def auto_trusted_domains(
    report: BootstrapReport,
    *,
    maximum_trusted: int = MAXIMUM_AUTO_TRUSTED_DOMAINS,
) -> tuple[list[str], dict[str, str]]:
    """Split a bootstrap into what may be trusted without a person, and what may not.

    The withheld list is returned rather than dropped: a corridor that then resolves nothing should
    be able to say which plausible-looking domains it declined to trust, and why. It carries what
    bootstrap rejected outright as well, so everything declined leaves one trace in one place.

    **Each reason has to be true, because this list is the only mitigation there is.** Known problem
    2 tells a reviewer to read it and look for domains declined that should not have been. Until
    2026-08-18 a domain under the destination's own top-level domain carrying no governmental
    marker fell through to "not a government domain for this destination" — which said something
    false about Italy's `esteri.it`, in wording identical to what a commercial visa agency got, so
    the one safeguard misled instead of warning. That case now says what is true: this rule cannot
    confirm it. See DECISIONS entry 33.
    """

    accepted: list[str] = []
    withheld: dict[str, str] = dict(report.rejected)
    destination = report.destination_name

    for proposal in sorted(report.proposals, key=_trust_priority):
        if not proposal.is_own_government:
            if proposal.looks_governmental:
                withheld[proposal.domain] = (
                    "governmental, but not under this destination's own government, so it "
                    "describes another country's rules"
                )
            elif proposal.belongs_to_destination:
                # Not "not a government domain": nothing here establishes that, and for 16 of 51
                # countries measured this is where the real immigration authority lands. The honest
                # statement is about the limit of the rule rather than about the domain.
                withheld[proposal.domain] = (
                    f"under {destination}'s own top-level domain, but its hostname carries no "
                    "marker this rule recognises as governmental, so it could not be confirmed as "
                    "an authority. It may be a real one: some governments use no such marker, and "
                    "for those the domain has to be named in reviewed data instead"
                )
            else:
                withheld[proposal.domain] = (
                    "neither governmental nor under this destination's own top-level domain"
                )
        elif len(accepted) < maximum_trusted:
            accepted.append(proposal.domain)
        else:
            # This one is the destination's own government. Saying so is the point: whoever reads
            # this must not be told it belongs to another country, which is a different problem
            # with a different fix.
            withheld[proposal.domain] = (
                "this destination's own government, but not among the "
                f"{maximum_trusted} best-evidenced visa authorities found, so it was not read"
            )
    return accepted, withheld


def unconfirmable_authorities(report: BootstrapReport) -> list[str]:
    """Domains under the destination's own top-level domain that carry no governmental marker.

    These are the candidates the trust rule can neither accept nor honestly dismiss, and naming them
    is what turns *"no domain belonging to Germany's own government could be identified"* — which is
    false, and the wrong place to go looking — into a description of the actual gap. Measured
    2026-08-18, remeasured 2026-08-25 after entry 65: 16 of 51 countries have their real immigration
    or foreign ministry here, because no `gov.de`, `gov.nl` or `gov.se` convention exists for one to
    be found under.

    Deliberately **not** a route to trusting any of them. The rule refuses "looks like an
    authority", and this only reports what it refused. See DECISIONS entry 33.
    """

    return sorted(
        proposal.domain
        for proposal in report.proposals
        if proposal.belongs_to_destination and not proposal.looks_governmental
    )


class PreparedDestination(StrictModel):
    """A destination ready to be resolved, before anything has been searched or fetched.

    Deliberately not a `DiscoveredDestination`: nothing has been discovered yet. This is only the
    answer to *whose pages may this corridor read*, which comes from committed data, so holding the
    two apart keeps "we know who to ask" from reading as "we found something".
    """

    config: DestinationConfig
    country_name: str
    trusted_domains: list[str]
    withheld_domains: dict[str, str]


class DiscoveredDestination(StrictModel):
    """A destination assembled entirely by machine, and the corridor that produced it."""

    config: DestinationConfig
    resolved: ResolvedCorridor
    trusted_domains: list[str]
    withheld_domains: dict[str, str]
    from_cache: bool = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def find_country(name: str, countries: CountryRegistry) -> Country | None:
    """Find a country however it was referred to: by name, synonym, or destination slug.

    The interface sends a slug, the registry stores display names, and a person may type either.
    All three have to land on the same country or a destination becomes unresearchable purely
    because of how it was spelled.
    """

    wanted = name.strip().lower()
    by_slug = countries.by_slug(wanted)
    if by_slug is not None:
        return by_slug
    return next(
        (
            country
            for country in countries.countries
            if country.name.lower() == wanted
            or wanted in {synonym.lower() for synonym in country.synonyms}
        ),
        None,
    )


def unusable_corridor(
    country_name: str, resolved: ResolvedCorridor, recorded_cause: RefusalCause | None
) -> AutomaticDiscoveryError:
    """The refusal for a corridor that ran and could not be answered, true to why.

    `recorded_cause` is what the run wrote down where the result cannot show it (a failed model
    call, no candidates); the recall log prefers it for the same reason.
    """

    missing = ", ".join(role.replace("_", " ") for role in resolved.unresolved_roles)
    cause = recorded_cause or resolved.outcome_cause
    if cause == "adjudication_failed":
        # A failed model call is not a missing page, and must never read as one.
        return AutomaticDiscoveryError(
            f"The check that decides which of {country_name}'s official pages answer this trip "
            "could not run, so nothing was concluded about it and nothing was saved. This is a "
            "fault on our side, not a finding about the trip: generate the plan again.",
            cause="check_failed",
        )
    if cause == "no_candidates":
        return AutomaticDiscoveryError(
            f"No page on {country_name}'s official sites came up for this trip, so there was "
            "nothing to confirm the visa decision with. Nothing was substituted in its place.",
            cause="no_official_answer",
        )
    if resolved.inaccessible_urls:
        return AutomaticDiscoveryError(
            f"Some of {country_name}'s official pages refused automated reading, and none of the "
            f"pages that could be read confirmed the {missing}. Nothing was substituted in its "
            "place, and nothing is said about what the refused pages contain.",
            cause="pages_unreadable",
            unreadable_urls=list(resolved.inaccessible_urls),
        )
    return AutomaticDiscoveryError(
        f"{country_name}'s official sources were searched, but no page could be confirmed "
        f"as the {missing}. Nothing was substituted in its place.",
        cause="no_official_answer",
    )


def unknown_country(name: str) -> AutomaticDiscoveryError:
    """The refusal for a destination no country in the registry answers to.

    One wording for the resolver and the `corridor` command, for the reason `prepare_destination`
    gives: a country one caller cannot research and a country another cannot are one fact.
    """

    return AutomaticDiscoveryError(
        f"{name} is not a country this agent knows how to research. Its own government "
        "domains cannot be told apart from other countries' pages about it.",
        cause="not_supported",
    )


def trusted_domains_for(
    country: Country, authorities: AuthorityRegistry
) -> tuple[list[str], dict[str, str]]:
    """Which of a country's domains may be read, and what was withheld, from committed data."""

    entry = authorities.get(country.code)
    if entry is None:
        # Not a refusal about the country — a refusal about this deployment. Searching for the
        # domains here instead would reintroduce the per-request variance entry 34 removed, and
        # would do it silently, on exactly the countries nobody had reviewed.
        raise AutomaticDiscoveryError(
            f"{country.name} is not in the reviewed authority registry, so there is no "
            "confirmed government domain to research it from. Nothing was fetched. Regenerate "
            "the registry with `visa-discover registry` and review the entry.",
            cause="not_supported",
        )

    trusted = entry.domains
    withheld = {
        domain: (
            f"under {country.name}'s own top-level domain, but its hostname carries no marker "
            "this rule recognises as governmental, so it could not be confirmed as an "
            "authority. It may be a real one: some governments use no such marker, and for "
            "those the domain has to be named in reviewed data instead"
        )
        for domain in entry.unconfirmable
    }
    if not trusted:
        # Name the candidates the rule could not confirm. Without this the message says no
        # government domain was *identified*, which for Germany or Italy is simply untrue and
        # sends whoever reads it to look at search or ranking rather than at the trust rule.
        detail = ""
        if entry.unconfirmable:
            detail = (
                f" Candidates under {country.name}'s own top-level domain were found — "
                f"{', '.join(entry.unconfirmable)} — but none of their hostnames carries a "
                "marker this agent recognises as governmental, so none could be confirmed as "
                "an authority. Some governments use no such marker; for those the domain has "
                "to be named in reviewed data."
            )
        raise AutomaticDiscoveryError(
            f"No domain belonging to {country.name}'s own government could be confirmed, so "
            f"there was nothing safe to read. Nothing was fetched.{detail}",
            cause="not_supported",
        )
    return trusted, withheld


def base_config_for(country: Country, corridor: Corridor, trusted: list[str]) -> DestinationConfig:
    # A Schengen member also carries the EU's reviewed domains, for the visa decision only
    # (entry 201). Attached here, the one place every destination is built from.
    return with_union(
        DestinationConfig(
            slug=corridor.destination_slug,
            display_name=country.name,
            route_type="national",
            implementation_status="available",
            trusted_domains=trusted,
        ),
        country.code,
    )


def prepare_destination(
    name: str,
    corridor: Corridor,
    *,
    countries: CountryRegistry | None = None,
    authorities: AuthorityRegistry | None = None,
) -> PreparedDestination:
    """The config a corridor is resolved against, or a refusal saying why there is none.

    Reads committed data and nothing else: **no store, no network, no model, and no search
    provider.** That last one is why this is a function rather than a method — building
    `AutomaticDestinationService` requires a `SearchProvider`, and a `BraveSearchProvider` raises
    without an API key, so a command that only wants to know *whose pages may be read* would have
    needed a key to ask a question answered entirely from a YAML file.

    Split out of `destination_for` so a command can resolve a registry destination **without** the
    corridor store in the way, which is what measuring run-to-run variance needs: a stored corridor
    would answer runs two and three from run one and hide the very thing being counted (TODO
    item 17).

    The refusals live here rather than in each caller on purpose. A country the CLI cannot research
    and a country the API cannot research are the same fact, and describing it twice is how the two
    drift into saying different things about one cause.
    """

    registry = countries or get_country_registry()
    country = find_country(name, registry)
    if country is None:
        raise unknown_country(name)
    trusted, withheld = trusted_domains_for(country, authorities or get_authority_registry())
    return PreparedDestination(
        config=base_config_for(country, corridor, trusted),
        country_name=country.name,
        trusted_domains=trusted,
        withheld_domains=withheld,
    )


class AutomaticDestinationService:
    """Turn a country name and a corridor into a destination the plan pipeline can use."""

    def __init__(
        self,
        provider: SearchProvider,
        build_resolver: Callable[..., CorridorResolver],
        store: FileCorridorStore,
        *,
        corpus: FileCorpusStore | None = None,
        countries: CountryRegistry | None = None,
        denylist: Denylist | None = None,
        authorities: AuthorityRegistry | None = None,
        maximum_age_hours: float = 24.0 * 7,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.provider = provider
        # A factory, not an instance: a resolver holds per-run state (render budgets, crawl
        # failures), so reusing one across requests would leak one corridor's limits into the next.
        self.build_resolver = build_resolver
        self.store = store
        # Optional, so a deployment without a corpus behaves exactly as it did before one existed.
        self.corpus = corpus
        self.countries = countries or get_country_registry()
        self.denylist = denylist or get_denylist()
        # Read once at construction. Which domains belong to a government is not a per-request
        # question, and making it one is exactly what entry 34 moved out of this path.
        self.authorities = authorities or get_authority_registry()
        self.maximum_age_hours = maximum_age_hours
        self.now = now

    def country_named(self, name: str) -> Country | None:
        """Find a country however it was referred to. See `find_country`."""

        return find_country(name, self.countries)

    def prepare(self, name: str, corridor: Corridor) -> PreparedDestination:
        """Whose pages this corridor may read, from committed data. See `prepare_destination`."""

        return prepare_destination(
            name, corridor, countries=self.countries, authorities=self.authorities
        )

    async def destination_for(
        self,
        name: str,
        corridor: Corridor,
        *,
        on_phase: Callable[[str], None] | None = None,
    ) -> DiscoveredDestination:
        """Resolve a destination from nothing, or refuse with a reason a traveller can read.

        `on_phase` hears each research phase as it starts. A stored corridor has none to report.
        """

        country = self.country_named(name)
        if country is None:
            raise unknown_country(name)

        cached = self.store.load(corridor)
        if cached is not None and cached.age_hours(self.now()) < self.maximum_age_hours:
            base = base_config_for(country, corridor, cached.trusted_domains)
            return DiscoveredDestination(
                config=cached.resolved.to_destination_config(base),
                resolved=cached.resolved,
                trusted_domains=cached.trusted_domains,
                withheld_domains=cached.withheld_domains,
                from_cache=True,
            )

        trusted, withheld = trusted_domains_for(country, self.authorities)
        base = base_config_for(country, corridor, trusted)
        # The country's known pages, and the pages that already answered *this* corridor. Both are
        # inputs to the resolver rather than things it discovers: the corpus is what search no
        # longer has to rediscover, and the pins are what the ranking no longer has to re-win.
        corpus = self.corpus.load(country.code) if self.corpus else None
        # A Schengen member also reads the EU's regulation and ETIAS page on every run, from the
        # shared EU store (entry 201).
        resolver = self.build_resolver(
            corpus=corpus,
            pinned=self._pinned(corridor),
            always_read=union_pages(union_of(base), self.corpus),
        )
        resolved = await resolver.resolve(base, corridor, on_phase=on_phase)
        if not resolved.is_usable:
            trace = getattr(resolver, "trace", None)
            raise unusable_corridor(country.name, resolved, getattr(trace, "refusal_cause", None))

        self._write_back(country, trusted, resolver, resolved)
        if not resolved.ran_without_search and not resolved.unread_for_now:
            # Only a resolution that ran as it should is kept. One answered from the corpus alone
            # is narrower than usual (entry 74), and one built while a page it chose to read was
            # down is that day's bad luck (entry 258): the store keeps what it is given for a week,
            # and keeping either would serve a degraded answer long after the cause had passed,
            # with nobody told — the shape entry 44 rejects, arriving by a different route.
            self.store.store(corridor, resolved, trusted, withheld, self.now())
        return DiscoveredDestination(
            config=resolved.to_destination_config(base),
            resolved=resolved,
            trusted_domains=trusted,
            withheld_domains=withheld,
        )

    def _pinned(self, corridor: Corridor) -> list[str]:
        """URLs that already filled a role for this corridor, from the last stored resolution.

        **No new store is needed for this.** `StoredCorridor.resolved.sources` is already exactly
        the proven set — today it is used only as a whole-answer cache, and this makes it also the
        thing that keeps a later run from losing what an earlier one found. A stored corridor may be
        too old to serve as an answer and still be perfectly good as a hint about which pages
        matter, so the age check deliberately does not apply here.
        """

        try:
            stored = self.store.load(corridor)
        except VisaResearchError:
            # A pin is an optimisation. An unreadable corridor store costs recall, never an answer.
            return []
        return [] if stored is None else [str(source.url) for source in stored.resolved.sources]

    def _write_back(
        self,
        country: Country,
        trusted: list[str],
        resolver: CorridorResolver,
        resolved: ResolvedCorridor,
    ) -> None:
        """Fold what this run discovered into the country's corpus, additively.

        **This is not the fallback entry 44 rejects, and the difference is the direction.** That was
        *deciding a corridor* from a live search after a corpus miss, so the answer depended on that
        day's search. This keeps what a run already found, so later runs start from more. It cannot
        change the current answer — the resolution is already made by the time this runs — and it
        widens no trust: every URL here passed `usable_results` and `is_crawlable`, and is filtered
        again against the live registry when it is read back.

        It is also what actually closes the gap. Measured 2026-08-22: five of twenty-four pages a
        Canada run fetched were absent from a 3,130-entry corpus, and one stayed absent when the
        exact query that had once surfaced it was re-run. Search is nondeterministic at the source,
        so no offline sweep can guarantee the superset — only keeping what a live run found can.
        """

        if self.corpus is None:
            return
        now = self.now()
        proven = {canonical_key(str(source.url)) for source in resolved.sources}
        found = [
            CorpusEntry(
                url=link.url,
                link_text=link.text,
                heading=link.heading,
                depth=link.depth,
                discovered_from=link.discovered_from,
                first_seen=now,
                last_seen=now,
                status="proven" if canonical_key(link.url) in proven else "unknown",
            )
            for link in resolver.discovered
        ]
        # **The pages that filled a role are written back whether or not this run discovered them**,
        # and getting that wrong made the strongest tier unreachable. `resolver.discovered` excludes
        # anything the corpus already held — correctly, since a page from the corpus is not a
        # discovery — but the common case is precisely that the *answering* page came from the
        # corpus. Marking proven only from `discovered` left a live Canada run writing 86 entries
        # and zero proven ones, so the never-evicted tier could never be entered at all.
        found.extend(
            CorpusEntry(
                url=str(source.url),
                title=source.title,
                first_seen=now,
                last_seen=now,
                status="proven",
            )
            for source in resolved.sources
        )
        if not found:
            return
        existing = self.corpus.load(country.code) or CountryCorpus(
            country_code=country.code,
            country_name=country.name,
            trusted_domains=trusted,
            built_at=now,
            entries=[],
        )
        try:
            self.corpus.store(merge(existing, found, now=now))
        except CorpusError:
            # Never at the cost of the answer: the corridor resolved, and a corpus that could not be
            # written costs the *next* run recall rather than this one its result.
            pass
