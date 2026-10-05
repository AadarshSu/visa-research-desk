"""Resolving a destination nobody configured, with no human approving a domain.

No test here reaches the network or a model. The search provider and the resolver are both
injected, exactly as elsewhere.

The load-bearing assertions are about what the machine refuses to trust. Removing the human gate
only stays safe because the rule that replaces it is the one the human was applying: a domain is
used only when it is the destination country's **own** government. France's real bootstrap
surfaced a commercial travel insurer, and Vietnam's ranked the US embassy first.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from visa_research_agent.discovery.automatic import (
    MAXIMUM_AUTO_TRUSTED_DOMAINS,
    AutomaticDestinationService,
    AutomaticDiscoveryError,
    auto_trusted_domains,
    is_own_government,
    unconfirmable_authorities,
)
from visa_research_agent.discovery.bootstrap import (
    BootstrapReport,
    DomainProposal,
    suggest_kind,
)
from visa_research_agent.discovery.corridor_store import FileCorridorStore
from visa_research_agent.discovery.models import (
    Corridor,
    ResolvedCorridor,
    ResolvedSource,
    ResolvedTool,
    SearchResult,
)
from visa_research_agent.discovery.registry import AuthorityRegistry, CountryAuthorities
from visa_research_agent.domain.models import DestinationConfig

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def proposal(domain: str, *, governmental: bool, own: bool) -> DomainProposal:
    return DomainProposal(
        domain=domain,
        looks_governmental=governmental,
        belongs_to_destination=own,
        queries=["q1", "q2"],
    )


def corridor(slug: str = "france") -> Corridor:
    return Corridor(
        destination_slug=slug,
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )


def resolved(slug: str = "france") -> ResolvedCorridor:
    return ResolvedCorridor(
        corridor=corridor(slug),
        resolved_at=NOW,
        sources=[
            ResolvedSource(
                source_id="fr_visa_decision",
                title="Applying for a visa",
                url="https://france-visas.gouv.fr/en/applying",  # type: ignore[arg-type]
                authority="France Visas",
                kind="foreign_ministry",
                roles=["visa_decision", "document_checklist"],
                score=60.0,
                decided_by="model",
            )
        ],
    )


class StubProvider:
    """Returns one fixed set of results, whatever is asked."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.queries: list[str] = []

    async def search(self, query: str, *, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult(url=url, title="", snippet="", query=query, rank=rank)
            for rank, url in enumerate(self.urls)
        ]


class StubResolver:
    """Stands in for the corridor resolver, recording the config it was handed."""

    def __init__(self, outcome: ResolvedCorridor) -> None:
        self.outcome = outcome
        self.trusted_seen: list[list[str]] = []

    async def resolve(
        self,
        destination: object,
        traveller: Corridor,
        *,
        on_phase: Callable[[str], None] | None = None,
    ) -> ResolvedCorridor:
        self.trusted_seen.append(list(getattr(destination, "trusted_domains", [])))
        if on_phase is not None:
            on_phase("search")
        return self.outcome


def registry(*rows: CountryAuthorities) -> AuthorityRegistry:
    return AuthorityRegistry(schema_version=1, generated_at=NOW, countries=list(rows))


def france(trusted: list[str] | None = None) -> CountryAuthorities:
    return CountryAuthorities(
        code="FR",
        name="France",
        trusted=["france-visas.gouv.fr"] if trusted is None else trusted,
    )


def build_service(
    tmp_path: Path,
    provider: StubProvider,
    resolver: StubResolver,
    *,
    now: datetime = NOW,
    authorities: AuthorityRegistry | None = None,
) -> AutomaticDestinationService:
    return AutomaticDestinationService(
        provider,
        lambda **_: resolver,  # type: ignore[arg-type]
        FileCorridorStore(tmp_path / "corridors"),
        authorities=authorities if authorities is not None else registry(france()),
        now=lambda: now,
    )


# --- the rule that replaces the human gate ---------------------------------------------------


def test_only_the_destinations_own_government_is_trusted() -> None:
    assert is_own_government(proposal("diplomatie.gouv.fr", governmental=True, own=True))
    # A real government, describing its own citizens' rules rather than this destination's.
    assert not is_own_government(proposal("usembassy.gov", governmental=True, own=False))
    # Under the country's TLD, but a commercial insurer. France's real bootstrap surfaced this.
    assert not is_own_government(proposal("axa-schengen.com", governmental=False, own=False))
    # An appointed provider cannot pass domain trust by design.
    assert not is_own_government(proposal("vfsglobal.com", governmental=False, own=False))


def test_what_is_withheld_is_reported_with_a_reason() -> None:
    report = BootstrapReport(
        destination_name="France",
        proposals=[
            proposal("diplomatie.gouv.fr", governmental=True, own=True),
            proposal("europa.eu", governmental=True, own=False),
            proposal("axa-schengen.com", governmental=False, own=False),
        ],
    )

    accepted, withheld = auto_trusted_domains(report)

    assert accepted == ["diplomatie.gouv.fr"]
    assert "another country's rules" in withheld["europa.eu"]
    assert "neither governmental nor under" in withheld["axa-schengen.com"]


def test_a_ministry_with_no_marker_is_not_called_a_non_government_domain() -> None:
    """The one mitigation known problem 2 offers is reading these reasons, and it used to lie.

    Italy's real foreign ministry has no governmental marker in its hostname — there is no `gov.it`
    convention for one to be found under — so it fell through to "not a government domain for this
    destination", which is false, and in wording identical to what a commercial visa agency got. A
    reviewer doing exactly what they were told would have believed it. Measured for 16 of 51
    countries; DECISIONS entry 33.
    """

    report = BootstrapReport(
        destination_name="Italy",
        proposals=[
            proposal("esteri.it", governmental=False, own=True),
            proposal("italy-visa-help.com", governmental=False, own=False),
        ],
    )

    accepted, withheld = auto_trusted_domains(report)

    # Still not trusted: "looks like an authority" is exactly what the rule refuses.
    assert accepted == []
    ministry, agency = withheld["esteri.it"], withheld["italy-visa-help.com"]
    assert ministry != agency, "a real ministry must not read identically to a visa agency"
    assert "could not be confirmed" in ministry
    assert "may be a real one" in ministry
    assert "not a government domain" not in ministry
    assert "own top-level domain" in ministry


def test_the_candidates_the_rule_cannot_confirm_are_named() -> None:
    """So a refusal can describe the actual gap instead of sending a reader to the wrong place."""

    report = BootstrapReport(
        destination_name="Germany",
        proposals=[
            proposal("auswaertiges-amt.de", governmental=False, own=True),
            proposal("bamf.de", governmental=False, own=True),
            proposal("usembassy.gov", governmental=True, own=False),
            proposal("germany-visa-agency.com", governmental=False, own=False),
        ],
    )

    assert unconfirmable_authorities(report) == ["auswaertiges-amt.de", "bamf.de"]


def test_what_bootstrap_rejected_outright_is_reported_too() -> None:
    """Everything declined leaves one trace in one place.

    A domain dropped for too little corroboration was previously visible only in the bootstrap
    report, which the request path never prints, so a corridor could not say it had seen it.
    """

    report = BootstrapReport(
        destination_name="Wonderland",
        proposals=[proposal("mfa.gov.wl", governmental=True, own=True)],
        rejected={"agency.example": "on the denylist of agencies and non-authoritative sites"},
    )

    accepted, withheld = auto_trusted_domains(report)

    assert accepted == ["mfa.gov.wl"]
    assert "denylist" in withheld["agency.example"]


# --- how many of its own government's domains one bootstrap may put into use ------------------


def own_government(domain: str, *, corroboration: int = 1) -> DomainProposal:
    return DomainProposal(
        domain=domain,
        looks_governmental=True,
        belongs_to_destination=True,
        matched_tlds=["gov"],
        suggested_kind=suggest_kind(domain),
        queries=[f"q{index}" for index in range(corroboration)],
    )


def test_only_the_best_evidenced_own_government_domains_are_put_to_use() -> None:
    """A government large enough to have many domains passes the rule with all of them.

    Every domain here is genuinely this destination's own government, so nothing is being called
    unofficial. They are competing for a crawl budget and ten fetch places, and an authority whose
    hostname says it serves travellers should not lose them to one that says nothing.
    """

    report = BootstrapReport(
        destination_name="Wonderland",
        proposals=[
            own_government("statedept.gov", corroboration=4),
            own_government("portal.gov", corroboration=4),
            own_government("homeland.gov", corroboration=3),
            own_government("wlembassy.gov", corroboration=2),
            own_government("interior.gov"),
            own_government("registry.gov"),
            own_government("enforcement.gov"),
            own_government("services.gov"),
        ],
    )

    accepted, withheld = auto_trusted_domains(report)

    assert len(accepted) == MAXIMUM_AUTO_TRUSTED_DOMAINS
    # The mission network is the one page in this list that serves a traveller abroad, and it is
    # the least corroborated of the four that matter. Its hostname is what keeps it in.
    assert accepted[0] == "wlembassy.gov"
    assert set(accepted) >= {"wlembassy.gov", "statedept.gov", "portal.gov", "homeland.gov"}
    assert set(withheld) and set(withheld).isdisjoint(accepted)


def test_a_domain_left_out_is_not_described_as_another_countrys() -> None:
    """The reason has to be true. These are this destination's own government, and saying otherwise
    would send whoever reads `withheld_domains` after a different problem entirely."""

    report = BootstrapReport(
        destination_name="Wonderland",
        proposals=[own_government(f"agency{index}.gov", corroboration=2) for index in range(7)],
    )

    _, withheld = auto_trusted_domains(report)

    reasons = " ".join(withheld.values())
    assert "another country" not in reasons
    assert "not a government domain" not in reasons
    assert "own government" in reasons


def test_the_cap_does_not_reach_an_ordinary_corridor() -> None:
    """Brazil put one domain to use, France two, China four. None of them changes."""

    report = BootstrapReport(
        destination_name="China",
        proposals=[
            proposal("china-embassy.gov.cn", governmental=True, own=True),
            proposal("china-consulate.gov.cn", governmental=True, own=True),
            proposal("mfa.gov.cn", governmental=True, own=True),
            proposal("nia.gov.cn", governmental=True, own=True),
        ],
    )

    accepted, withheld = auto_trusted_domains(report)

    assert len(accepted) == 4
    assert withheld == {}


def test_which_domains_are_used_does_not_depend_on_the_order_search_returned_them() -> None:
    """The failure this fixes was a coin flip, so the same evidence must give the same set."""

    proposals = [
        own_government("statedept.gov", corroboration=4),
        own_government("portal.gov", corroboration=4),
        own_government("homeland.gov", corroboration=3),
        own_government("wlembassy.gov", corroboration=2),
        own_government("interior.gov"),
        own_government("registry.gov"),
    ]

    forwards, _ = auto_trusted_domains(
        BootstrapReport(destination_name="Wonderland", proposals=list(proposals))
    )
    backwards, _ = auto_trusted_domains(
        BootstrapReport(destination_name="Wonderland", proposals=list(reversed(proposals)))
    )

    assert forwards == backwards


# --- resolving a destination from nothing ----------------------------------------------------


async def test_a_destination_nobody_configured_is_researched(tmp_path: Path) -> None:
    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())

    discovered = await build_service(tmp_path, provider, resolver).destination_for(
        "France", corridor()
    )

    assert discovered.trusted_domains == ["france-visas.gouv.fr"]
    assert [str(source.url) for source in discovered.config.sources] == [
        "https://france-visas.gouv.fr/en/applying"
    ]
    # The config must be usable by the ordinary pipeline, with its trust rules re-run.
    assert discovered.config.application_document_source_ids == ["fr_visa_decision"]


async def test_nothing_is_fetched_when_no_own_government_domain_is_confirmed(
    tmp_path: Path,
) -> None:
    """The refusal that keeps the gate's removal honest.

    The rule confirmed nothing for this country when the registry was generated — search had
    offered only a commercial agency and another country's government. There is nothing safe to
    read, so nothing is read, rather than falling back to the best available.
    """

    provider = StubProvider([])
    resolver = StubResolver(resolved())
    service = build_service(tmp_path, provider, resolver, authorities=registry(france(trusted=[])))

    with pytest.raises(AutomaticDiscoveryError, match="own government"):
        await service.destination_for("France", corridor())

    assert resolver.trusted_seen == [], "nothing may be crawled without an approved domain"


async def test_a_country_absent_from_the_registry_is_refused_not_searched(
    tmp_path: Path,
) -> None:
    """The failure mode the registry introduces, and it must not be papered over.

    Falling back to a live bootstrap here would reintroduce exactly the per-request variance
    DECISIONS entry 34 removed, and would do it silently, on the countries nobody had reviewed.
    """

    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())
    service = build_service(tmp_path, provider, resolver, authorities=registry())

    with pytest.raises(AutomaticDiscoveryError, match="reviewed authority registry"):
        await service.destination_for("France", corridor())

    assert provider.queries == [], "a missing row must never fall back to searching"
    assert resolver.trusted_seen == []


async def test_a_refusal_names_the_candidates_the_rule_could_not_confirm(tmp_path: Path) -> None:
    """The sentence a reader acts on, and it used to point at the wrong thing.

    "No domain belonging to Germany's own government could be identified" is false — two were, and
    the rule simply cannot confirm either, because no `gov.de` convention exists. A reader believing
    that message goes looking at search or at ranking, when the gap is the trust rule itself.
    """

    provider = StubProvider([])
    resolver = StubResolver(resolved())
    service = build_service(
        tmp_path,
        provider,
        resolver,
        authorities=registry(
            CountryAuthorities(
                code="DE",
                name="Germany",
                trusted=[],
                unconfirmable=["auswaertiges-amt.de"],
            )
        ),
    )

    with pytest.raises(AutomaticDiscoveryError) as raised:
        await service.destination_for("Germany", corridor())

    message = str(raised.value)
    assert "auswaertiges-amt.de" in message, "name what was found rather than implying nothing was"
    assert "none could be confirmed as an authority" in message
    assert "named in reviewed data" in message
    # Not "could not be identified": they were, and saying otherwise is what sent readers astray.
    assert "could not be identified" not in message
    # Only candidates under Germany's own TLD are named; a commercial agency never reaches the
    # registry's `unconfirmable` list, so it cannot be offered here as a candidate authority.
    assert "germany-visa-agency.com" not in message
    assert resolver.trusted_seen == [], "still nothing crawled — this changes wording, not trust"


async def test_an_unknown_country_is_refused_rather_than_searched(tmp_path: Path) -> None:
    provider = StubProvider(["https://example.gov.zz/visa"])
    resolver = StubResolver(resolved())

    with pytest.raises(AutomaticDiscoveryError, match="not a country"):
        await build_service(tmp_path, provider, resolver).destination_for(
            "Atlantis", corridor("atlantis")
        )

    assert provider.queries == [], "an unknown country must not even be searched"


async def test_a_corridor_missing_a_load_bearing_role_is_refused(tmp_path: Path) -> None:
    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    empty = ResolvedCorridor(
        corridor=corridor(), resolved_at=NOW, sources=[], unresolved_roles=["visa_decision"]
    )

    with pytest.raises(AutomaticDiscoveryError, match="visa decision"):
        await build_service(tmp_path, provider, StubResolver(empty)).destination_for(
            "France", corridor()
        )


async def test_only_approved_domains_reach_the_resolver(tmp_path: Path) -> None:
    provider = StubProvider(
        [
            "https://france-visas.gouv.fr/en/applying",
            "https://axa-schengen.com/visa",
            "https://travel.state.gov/france",
        ]
    )
    resolver = StubResolver(resolved())

    await build_service(tmp_path, provider, resolver).destination_for("France", corridor())

    assert resolver.trusted_seen == [["france-visas.gouv.fr"]]


# --- caching ---------------------------------------------------------------------------------


async def test_a_second_request_reuses_the_stored_corridor(tmp_path: Path) -> None:
    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())
    service = build_service(tmp_path, provider, resolver)

    await service.destination_for("France", corridor())
    again = await service.destination_for("France", corridor())

    assert again.from_cache
    assert len(resolver.trusted_seen) == 1, "a cached corridor must not be re-crawled"


async def test_a_corridor_past_its_age_is_resolved_again(tmp_path: Path) -> None:
    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())

    await build_service(tmp_path, provider, resolver).destination_for("France", corridor())
    later = build_service(tmp_path, provider, resolver, now=NOW + timedelta(days=30))
    again = await later.destination_for("France", corridor())

    assert not again.from_cache
    assert len(resolver.trusted_seen) == 2


async def test_a_corridor_is_kept_for_a_week_and_no_longer(tmp_path: Path) -> None:
    """Entry 258: one week, down from three, so a resolution gone wrong is not served for long."""

    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())

    await build_service(tmp_path, provider, resolver).destination_for("France", corridor())
    six_days = build_service(tmp_path, provider, resolver, now=NOW + timedelta(days=6))
    assert (await six_days.destination_for("France", corridor())).from_cache
    eight_days = build_service(tmp_path, provider, resolver, now=NOW + timedelta(days=8))
    assert not (await eight_days.destination_for("France", corridor())).from_cache


async def test_a_corridor_resolved_while_a_chosen_page_was_down_is_not_stored(
    tmp_path: Path,
) -> None:
    """Entry 258: Australia `IN/SG` was resolved while both embassy sites answered `5xx`, stored,
    and served to every later request. A resolution built around a page that failed only "for now"
    is used for the request that made it and never kept, so the next request looks again."""

    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    unlucky = resolved().model_copy(
        update={"unread_for_now": ["https://france-visas.gouv.fr/en/where-to-apply"]}
    )
    resolver = StubResolver(unlucky)
    service = build_service(tmp_path, provider, resolver)

    first = await service.destination_for("France", corridor())
    again = await service.destination_for("France", corridor())

    assert first.config.sources, "the request that ran still gets its answer"
    assert not again.from_cache
    assert len(resolver.trusted_seen) == 2


async def test_every_corridor_is_resolved_through_a_freshly_built_resolver(
    tmp_path: Path,
) -> None:
    """The factory is what keeps the crawl's render allowance and failure records per corridor.

    `CrawlFetcher` holds both as instance state, and that is only safe while an instance never
    outlives one corridor. If this service ever kept a resolver instead of building one, the second
    corridor would start with the first's render budget already spent and its failures already
    recorded — the same defect DECISIONS entry 37 removed from retrieval, where the fetcher really
    is process-lifetime.
    """

    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    built: list[StubResolver] = []

    def build(**_: object) -> StubResolver:
        built.append(StubResolver(resolved()))
        return built[-1]

    service = AutomaticDestinationService(
        provider,
        build,  # type: ignore[arg-type]
        FileCorridorStore(tmp_path / "corridors"),
        authorities=registry(france()),
        now=lambda: NOW,
    )

    second = Corridor(
        destination_slug="france",
        passport_nationality="IN",
        applying_from="GB",
        purpose="study",
    )
    await service.destination_for("France", corridor())
    await service.destination_for("France", second)

    assert len(built) == 2, "a resolver must not be carried from one corridor to the next"


async def test_a_stored_corridor_is_keyed_by_the_whole_corridor(tmp_path: Path) -> None:
    """Nationality and purpose change the answer, so they must change the key."""

    provider = StubProvider(["https://france-visas.gouv.fr/en/applying"])
    resolver = StubResolver(resolved())
    service = build_service(tmp_path, provider, resolver)

    await service.destination_for("France", corridor())
    other = corridor().model_copy(update={"passport_nationality": "CN"})
    await service.destination_for("France", other)

    assert len(resolver.trusted_seen) == 2


# --- a gap an authority imposed, rather than a gap we could not close ---------------------------


BLOCKED_PAGE = "https://france-visas.gouv.fr/en/web/france-visas"


def partly_resolved(
    *, blocked: bool, with_sources: bool = True, held_the_decision: bool = True
) -> ResolvedCorridor:
    """France as it actually resolves: the UK post's route page, and no confirmed decision.

    `held_the_decision` is what separates France from an incidental refusal. France-Visas is the
    page the decision actually lives on, so its `403` is why the decision is unverifiable. A `403`
    on a legal notice is not, and must not resolve anything.
    """

    sources = (
        [
            ResolvedSource(
                source_id="fr_route",
                title="Applying for a visa",
                url="https://uk.diplomatie.gouv.fr/en/applying-for-a-visa",  # type: ignore[arg-type]
                authority="France in the United Kingdom",
                kind="embassy_or_high_commission",
                roles=["application_route"],
                score=42.8,
                decided_by="model",
            )
        ]
        if with_sources
        else []
    )
    return ResolvedCorridor(
        corridor=corridor("france"),
        resolved_at=NOW,
        sources=sources,
        unresolved_roles=["visa_decision", "document_checklist"],
        inaccessible_domains=["france-visas.gouv.fr"] if blocked else [],
        inaccessible_urls=[BLOCKED_PAGE] if blocked else [],
        decision_blocking_urls=[BLOCKED_PAGE] if blocked and held_the_decision else [],
    )


def test_a_decision_missing_because_an_authority_refused_us_still_yields_a_plan() -> None:
    """France states the visa decision only on a portal that refuses this program, and every
    readable page delegates to it. Silence is not the honest answer there: naming the page and
    saying we were not permitted to read it is something the traveller can act on."""

    resolved = partly_resolved(blocked=True)

    assert resolved.is_usable
    assert resolved.decision_is_unverified


def test_a_decision_simply_not_found_still_refuses() -> None:
    """The narrow exception must stay narrow. Without an authority refusing us, an unfilled visa
    decision means we did not find it — and a plan built on that would be guessing."""

    resolved = partly_resolved(blocked=False)

    assert not resolved.is_usable
    assert not resolved.decision_is_unverified


def test_a_blocked_authority_alone_is_not_a_plan() -> None:
    """With nothing readable to cite there is no plan, only a link."""

    assert not partly_resolved(blocked=True, with_sources=False).is_usable


def test_a_block_that_could_not_have_held_the_decision_resolves_nothing() -> None:
    """The exception has to be narrow or it swallows the rule it is an exception to.

    A WAF refusing an incidental page is ordinary at scale, so if any block anywhere qualified,
    every corridor whose decision was merely *not found* would come to present as one an authority
    refused us — and that resolves where the truth must refuse. The refusal is still reported; it
    simply cannot license a claim about a question it never touched. DECISIONS entry 32.
    """

    resolved = partly_resolved(blocked=True, held_the_decision=False)

    assert not resolved.is_usable
    assert not resolved.decision_is_unverified
    # Reported, though: entry 18 requires that a refusal never read as "nothing was found".
    assert resolved.inaccessible_urls == [BLOCKED_PAGE]
    assert resolved.inaccessible_domains == ["france-visas.gouv.fr"]


def test_the_refused_page_reaches_the_destination_it_will_be_planned_from() -> None:
    """The URL is the point: a plan can hand it over, and a domain alone could not be opened."""

    base = DestinationConfig(
        slug="france",
        display_name="France",
        route_type="national",
        implementation_status="available",
        trusted_domains=["diplomatie.gouv.fr", "france-visas.gouv.fr"],
    )

    config = partly_resolved(blocked=True).to_destination_config(base)

    assert config.decision_is_unverified
    assert [str(authority.url) for authority in config.unreadable_authorities] == [BLOCKED_PAGE]
    assert "france-visas.gouv.fr" in config.unreadable_authorities[0].authority
    # It is not a source: there is no content behind it, so nothing may cite it as evidence.
    assert [source.source_id for source in config.sources] == ["fr_route"]
    # Judged able to hold the decision, so a plan can lead with it (TODO item 54).
    assert config.unreadable_authorities[0].may_hold_decision
    # And why the readable page was chosen travels with it, instead of stopping at the corridor
    # (TODO item 21, known problem 22).
    selection = config.sources[0].selection
    assert selection is not None
    assert (selection.roles, selection.decided_by, selection.score) == (
        ["application_route"],
        "model",
        42.8,
    )


def test_a_refusal_that_could_not_hold_the_decision_is_still_named_and_not_marked() -> None:
    """Item 54 changes what leads, never what is shown: every refusal is still reported."""

    base = DestinationConfig(
        slug="france",
        display_name="France",
        route_type="national",
        implementation_status="available",
        trusted_domains=["diplomatie.gouv.fr", "france-visas.gouv.fr"],
    )

    config = partly_resolved(blocked=True, held_the_decision=False).to_destination_config(base)

    assert [str(authority.url) for authority in config.unreadable_authorities] == [BLOCKED_PAGE]
    assert not config.unreadable_authorities[0].may_hold_decision


# --- the decision behind an official tool -----------------------------------------------------

WIZARD_PAGE = "https://www.gov.uk/check-uk-visa"


def behind_a_tool(*, with_sources: bool = True) -> ResolvedCorridor:
    """The United Kingdom as it actually resolves: a checklist found, and a checker for the rest.

    Nothing refused us here, which is the whole difference from France. `gov.uk/check-uk-visa` was
    ranked, shortlisted, fetched and read; it asks the traveller questions rather than answering
    them. Before entry 59 that fell into *not found* and threw the checklist away with everything
    else (entry 58).
    """

    sources = (
        [
            ResolvedSource(
                source_id="gb_checklist",
                title="Standard Visitor visa: documents you must provide",
                url="https://www.gov.uk/standard-visitor/documents-you-must-provide",  # type: ignore[arg-type]
                authority="UK Visas and Immigration",
                kind="immigration_authority",
                roles=["document_checklist"],
                score=61.2,
                decided_by="model",
            )
        ]
        if with_sources
        else []
    )
    return ResolvedCorridor(
        corridor=corridor("united-kingdom"),
        resolved_at=NOW,
        sources=sources,
        unresolved_roles=["visa_decision"],
        interactive_tools=[ResolvedTool(role="visa_decision", url=WIZARD_PAGE)],
    )


def uk_base() -> DestinationConfig:
    return DestinationConfig(
        slug="united-kingdom",
        display_name="United Kingdom",
        route_type="national",
        implementation_status="available",
        trusted_domains=["www.gov.uk"],
    )


def test_a_decision_only_an_official_tool_can_give_still_yields_a_plan() -> None:
    """Entry 58's largest coverage limit. The checklist, route, times and fees were all correct;
    discarding them because a questionnaire holds the decision helped nobody."""

    resolved = behind_a_tool()

    assert resolved.is_usable
    assert resolved.decision_is_unverified


def test_a_tool_alone_is_not_a_plan_either() -> None:
    """The same bound as a block: with nothing readable to cite there is no plan, only a link."""

    assert not behind_a_tool(with_sources=False).is_usable


def test_the_tool_reaches_the_destination_as_a_tool_and_not_as_a_refusal() -> None:
    """The distinction is the point. This page was served, fetched and read, so reporting it as an
    authority that would not permit retrieval would be false about what happened."""

    config = behind_a_tool().to_destination_config(uk_base())

    assert config.decision_is_unverified
    assert [str(tool.url) for tool in config.decision_tools] == [WIZARD_PAGE]
    assert config.unreadable_authorities == []
    assert "asking questions" in config.decision_tools[0].detail
    # Not a source either: it states no decision, so nothing may cite it as evidence of one.
    assert [source.source_id for source in config.sources] == ["gb_checklist"]


def test_a_tool_off_the_approved_domains_is_refused() -> None:
    """A traveller is being sent to this URL to settle the question the plan could not. Officialness
    is still a property of the domain, and reading the page changes nothing about that."""

    resolved = behind_a_tool().model_copy(
        update={
            "interactive_tools": [
                ResolvedTool(role="visa_decision", url="https://uk-visa-help.example.com/checker")
            ]
        }
    )

    with pytest.raises(ValueError, match="not on an approved domain"):
        resolved.to_destination_config(uk_base())


def test_an_unverified_decision_cannot_be_claimed_without_naming_the_authority() -> None:
    """Otherwise "we were not allowed to read it" would quietly cover for "we did not find it"."""

    with pytest.raises(ValueError, match="must name the authority"):
        DestinationConfig(
            slug="france",
            display_name="France",
            route_type="national",
            implementation_status="available",
            trusted_domains=["diplomatie.gouv.fr"],
            decision_is_unverified=True,
        )


def test_one_page_answering_two_questions_is_offered_for_both() -> None:
    """The same page under two questions is two answers, not one link twice.

    France's `uk.diplomatie.gouv.fr/en/applying-for-a-visa` settles whether a visa is needed, which
    documents to bring and the fee. Collapsing it to one entry — which this did for an hour on
    2026-08-24 — hides the checklist tool from the documents panel, which is where a reader looking
    for documents goes. Repetition is suppressed in the interface, and only in the catch-all panel
    that carries several topics together.
    """

    resolved = behind_a_tool().model_copy(
        update={
            "interactive_tools": [
                ResolvedTool(role="visa_decision", url=WIZARD_PAGE),
                ResolvedTool(role="general_entry", url=WIZARD_PAGE),
            ]
        }
    )

    config = resolved.to_destination_config(uk_base())

    assert [(tool.topic, str(tool.url)) for tool in config.official_tools] == [
        ("visa_decision", WIZARD_PAGE),
        ("general_entry", WIZARD_PAGE),
    ]


def test_a_tool_is_dropped_where_a_source_already_answers_that_role() -> None:
    """Per role, not per corridor: a checklist found on a page suppresses only its own tool."""

    resolved = behind_a_tool().model_copy(
        update={
            "interactive_tools": [
                ResolvedTool(role="visa_decision", url=WIZARD_PAGE),
                ResolvedTool(role="document_checklist", url=WIZARD_PAGE + "/y"),
            ]
        }
    )

    config = resolved.to_destination_config(uk_base())

    # `behind_a_tool` resolves a real checklist source, so only the decision tool survives.
    assert [tool.topic for tool in config.official_tools] == ["visa_decision"]


def test_a_tool_for_a_role_no_source_fills_reaches_the_destination() -> None:
    """Entry 60: only `visa_decision` is load-bearing, so a fees questionnaire adds a link to a plan
    that already stands rather than deciding whether it exists."""

    resolved = behind_a_tool().model_copy(
        update={
            "interactive_tools": [
                ResolvedTool(role="visa_decision", url=WIZARD_PAGE),
                ResolvedTool(role="fees", url="https://www.gov.uk/visa-fees-checker"),
            ]
        }
    )

    config = resolved.to_destination_config(uk_base())

    assert [tool.topic for tool in config.official_tools] == ["visa_decision", "fees"]
    assert config.decision_is_unverified


# --- progress (TODO item 57) -----------------------------------------------------------------


async def test_the_resolvers_phases_reach_the_caller(tmp_path: Path) -> None:
    service = build_service(
        tmp_path,
        StubProvider(["https://france-visas.gouv.fr/en/applying"]),
        StubResolver(resolved()),
    )
    heard: list[str] = []

    await service.destination_for("France", corridor(), on_phase=heard.append)

    assert heard == ["search"]


async def test_a_stored_corridor_reports_no_phases(tmp_path: Path) -> None:
    """Nothing was researched, so nothing may be announced as running."""

    service = build_service(
        tmp_path,
        StubProvider(["https://france-visas.gouv.fr/en/applying"]),
        StubResolver(resolved()),
    )
    await service.destination_for("France", corridor())
    heard: list[str] = []

    again = await service.destination_for("France", corridor(), on_phase=heard.append)

    assert again.from_cache
    assert heard == []


# --- why a refusal happened, in the traveller's terms (TODO item 73) --------------------------


class TracedStubResolver(StubResolver):
    """A resolver whose run recorded its own refusal cause, as the real one does on its trace."""

    def __init__(self, outcome: ResolvedCorridor, refusal_cause: str) -> None:
        super().__init__(outcome)
        self.trace = SimpleNamespace(refusal_cause=refusal_cause)


def unanswered(**fields: object) -> ResolvedCorridor:
    return ResolvedCorridor(
        corridor=corridor(),
        resolved_at=NOW,
        sources=[],
        unresolved_roles=["visa_decision"],
        **fields,  # type: ignore[arg-type]
    )


async def test_a_failed_model_call_is_refused_as_a_check_that_could_not_run(
    tmp_path: Path,
) -> None:
    """The Smaller-things defect item 73 absorbs: this read as "no page could be confirmed"."""

    resolver = TracedStubResolver(unanswered(), "adjudication_failed")

    with pytest.raises(AutomaticDiscoveryError) as raised:
        await build_service(tmp_path, StubProvider([]), resolver).destination_for(
            "France", corridor()
        )

    assert raised.value.cause == "check_failed"
    assert "could not run" in str(raised.value)
    assert "no page could be confirmed" not in str(raised.value)


async def test_a_corridor_with_no_candidates_says_nothing_came_up(tmp_path: Path) -> None:
    resolver = TracedStubResolver(unanswered(), "no_candidates")

    with pytest.raises(AutomaticDiscoveryError) as raised:
        await build_service(tmp_path, StubProvider([]), resolver).destination_for(
            "France", corridor()
        )

    assert raised.value.cause == "no_official_answer"
    assert "No page" in str(raised.value)


async def test_pages_an_authority_refused_are_named_never_described(tmp_path: Path) -> None:
    refused = "https://france-visas.gouv.fr/en/web/france-visas/visa-wizard"
    resolver = StubResolver(unanswered(inaccessible_urls=[refused]))

    with pytest.raises(AutomaticDiscoveryError) as raised:
        await build_service(tmp_path, StubProvider([]), resolver).destination_for(
            "France", corridor()
        )

    assert raised.value.cause == "pages_unreadable"
    assert raised.value.unreadable_urls == [refused]
    assert "nothing is said about what the refused pages contain" in str(raised.value)


async def test_a_corridor_that_read_pages_and_found_no_decision_says_so(tmp_path: Path) -> None:
    with pytest.raises(AutomaticDiscoveryError) as raised:
        await build_service(tmp_path, StubProvider([]), StubResolver(unanswered())).destination_for(
            "France", corridor()
        )

    assert raised.value.cause == "no_official_answer"
    assert raised.value.unreadable_urls == []


async def test_a_country_nobody_has_built_is_refused_as_not_supported(tmp_path: Path) -> None:
    with pytest.raises(AutomaticDiscoveryError) as raised:
        await build_service(
            tmp_path, StubProvider([]), StubResolver(unanswered()), authorities=registry()
        ).destination_for("France", corridor())

    assert raised.value.cause == "not_supported"
