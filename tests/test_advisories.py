"""The traveller's own government's travel advice: a link and nothing else (DECISIONS entry 260).

Nothing here reaches the network. The rules that matter: only a government's own domains are
linked, a refusing government's link is marked as never checked, a destination link is written only
after it opened, and a passport with no row gets no panel at all.
"""

from datetime import UTC, datetime

import httpx
import pytest

from visa_research_agent.discovery.advisories import (
    AdvisoryLinks,
    AdvisoryPublisher,
    AdvisoryPublishers,
    advice_link,
    load_advisory_links,
    load_advisory_publishers,
)
from visa_research_agent.discovery.advisory_links import (
    Destination,
    LinkChecker,
    build_publisher,
    index_matches,
    names_from_wikidata,
    normalise,
    pattern_candidates,
)
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.research.robots import RobotsCache

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def row(**overrides: object) -> AdvisoryPublisher:
    fields: dict[str, object] = {
        "code": "GB",
        "government": "UK Foreign, Commonwealth & Development Office",
        "written_for": "British nationals",
        "language": "en",
        "domains": ["www.gov.uk"],
        "access": "open",
        "page": "https://www.gov.uk/foreign-travel-advice",
    }
    fields.update(overrides)
    return AdvisoryPublisher.model_validate(fields)


def publishers(*rows: AdvisoryPublisher) -> AdvisoryPublishers:
    return AdvisoryPublishers(schema_version=1, publishers=list(rows))


def links(by_passport: dict[str, dict[str, str]]) -> AdvisoryLinks:
    return AdvisoryLinks(schema_version=1, generated_at=NOW, links=by_passport)


def destination(code: str, names: dict[str, list[str]] | None = None) -> Destination:
    country = next(c for c in get_country_registry().countries if c.code == code)
    return Destination(country, names or {})


# --- The reviewed file -------------------------------------------------------------------------


def test_the_committed_files_load() -> None:
    assert load_advisory_publishers().get("GB") is not None
    load_advisory_links()


def test_a_domain_the_rule_cannot_confirm_needs_evidence() -> None:
    """Germany's foreign office carries no governmental marker. Without a person's evidence it may
    not be linked — the same rule as `authority_domains.yaml` (entry 111)."""

    with pytest.raises(ValueError, match="needs reviewed evidence"):
        row(code="DE", language="de", domains=["auswaertiges-amt.de"],
            page="https://www.auswaertiges-amt.de/de/ReiseUndSicherheit")  # fmt: skip

    assert row(
        code="DE",
        language="de",
        domains=["auswaertiges-amt.de"],
        evidence={"auswaertiges-amt.de": "Wikidata P856/P17"},
        page="https://www.auswaertiges-amt.de/de/ReiseUndSicherheit",
    )


def test_another_governments_domain_is_never_this_ones() -> None:
    """The State Department's page about the UK is advice for Americans."""

    with pytest.raises(ValueError, match="not confirmed"):
        row(domains=["travel.state.gov"], page="https://travel.state.gov/x")


def test_every_address_must_be_on_the_rows_domains() -> None:
    with pytest.raises(ValueError, match="outside the row's domains"):
        row(page="https://www.example.com/advice")


# --- What the panel shows ----------------------------------------------------------------------


def test_no_row_means_no_panel() -> None:
    assert advice_link("IN", "JP", publishers(row()), links({})) is None


def test_no_panel_for_travel_to_ones_own_country() -> None:
    assert advice_link("GB", "GB", publishers(row()), links({})) is None


def test_the_destinations_own_page_when_one_was_checked() -> None:
    shown = advice_link(
        "GB",
        "JP",
        publishers(row()),
        links({"GB": {"JP": "https://www.gov.uk/foreign-travel-advice/japan"}}),
    )
    assert shown is not None
    assert shown.url == "https://www.gov.uk/foreign-travel-advice/japan"
    assert shown.about_destination
    assert shown.checked
    assert shown.language == "English"


def test_the_governments_advice_page_when_no_destination_page_is_known() -> None:
    shown = advice_link("GB", "JP", publishers(row()), links({}))
    assert shown is not None
    assert shown.url == "https://www.gov.uk/foreign-travel-advice"
    assert not shown.about_destination


def test_a_generated_link_off_the_rows_domains_is_never_shown() -> None:
    shown = advice_link(
        "GB", "JP", publishers(row()), links({"GB": {"JP": "https://evil.example/japan"}})
    )
    assert shown is not None
    assert shown.url == "https://www.gov.uk/foreign-travel-advice"


def test_a_refusing_government_is_linked_and_marked_unchecked() -> None:
    """The owner's call: their advice is still worth a link, and the panel says we could not open
    it to check."""

    shown = advice_link("GB", "JP", publishers(row(access="refused")), links({}))
    assert shown is not None
    assert not shown.checked


def test_advice_in_another_language_names_it_and_offers_english() -> None:
    german = row(
        code="DE",
        language="de",
        domains=["auswaertiges-amt.de"],
        evidence={"auswaertiges-amt.de": "Wikidata P856/P17"},
        page="https://www.auswaertiges-amt.de/de/reise",
        english="https://www.auswaertiges-amt.de/en/travel",
    )
    shown = advice_link("DE", "JP", publishers(german), links({}))
    assert shown is not None
    assert shown.language == "German"
    assert shown.english_url == "https://www.auswaertiges-amt.de/en/travel"


# --- Building the links offline ----------------------------------------------------------------


def test_names_are_kept_per_language_and_only_for_known_countries() -> None:
    payload = {
        "results": {
            "bindings": [
                {"iso": {"value": "JP"}, "label": {"xml:lang": "cs", "value": "Japonsko"}},
                {"iso": {"value": "JP"}, "label": {"xml:lang": "de", "value": "Japan"}},
                {"iso": {"value": "ZZ"}, "label": {"xml:lang": "de", "value": "Nirgendwo"}},
            ]
        }
    }
    assert names_from_wikidata(payload, ["JP"]) == {"JP": {"cs": ["Japonsko"], "de": ["Japan"]}}


def test_names_match_without_accents_or_punctuation() -> None:
    assert normalise("Brésil") == normalise("bresil")
    assert normalise("Côte d'Ivoire") == "cote d ivoire"


def test_a_pattern_fills_in_the_destination() -> None:
    japan = destination("JP", {"es": ["Japón"]})
    assert pattern_candidates("https://um.fi/c/{ISO2}", japan, "fi") == ["https://um.fi/c/JP"]
    assert pattern_candidates("https://x.gob.es/?trc={name}", japan, "es") == [
        "https://x.gob.es/?trc=Jap%C3%B3n"
    ]


def test_an_index_link_is_matched_by_the_destinations_name_in_the_governments_language() -> None:
    czech = row(
        code="CZ",
        language="cs",
        domains=["mzv.gov.cz"],
        page="https://mzv.gov.cz/index.html",
        destinations={"method": "index", "index": "https://mzv.gov.cz/i", "link_prefix": "/z/"},
    )
    html = (
        '<a href="/z/japonsko/">Japonsko</a>'
        '<a href="/z/brazilie/">Brazílie</a>'
        '<a href="https://evil.example/z/japonsko/">Japonsko</a>'
    )
    found = index_matches(
        html, "https://mzv.gov.cz/i", czech, destination("JP", {"cs": ["Japonsko"]})
    )
    assert found == ["https://mzv.gov.cz/z/japonsko/"]


def site(pages: dict[str, tuple[int, str]], redirects: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    redirects = redirects or {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url in redirects:
            return httpx.Response(302, headers={"location": redirects[url]})
        status, body = pages.get(url, (404, "<title>Not found</title>"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


async def no_sleep(_: float) -> None:
    return None


@pytest.mark.anyio
async def test_a_destination_link_is_written_only_after_it_opened() -> None:
    serbia = row(
        code="RS",
        domains=["mfa.gov.rs"],
        page="https://www.mfa.gov.rs/en/advice",
        destinations={"method": "pattern", "pattern": "https://www.mfa.gov.rs/en/advice/{slug}"},
    )
    transport = site(
        {"https://www.mfa.gov.rs/en/advice/japan": (200, "<title>Japan</title>")},
        redirects={"https://www.mfa.gov.rs/en/advice/brazil": "https://www.mfa.gov.rs/en/advice"},
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        checker = LinkChecker(client, RobotsCache(user_agent="test"), sleep=no_sleep)
        build = await build_publisher(
            serbia, [destination("JP"), destination("BR"), destination("FR")], checker
        )

    assert build.links == {"JP": "https://www.mfa.gov.rs/en/advice/japan"}
    # Brazil's page sent us back to the index, and France's does not exist: neither is linked.
    assert [code[:2] for code in build.missing] == ["BR", "FR"]


@pytest.mark.anyio
async def test_a_challenge_is_never_written_as_a_link() -> None:
    serbia = row(
        code="RS",
        domains=["mfa.gov.rs"],
        page="https://www.mfa.gov.rs/en/advice",
        destinations={"method": "pattern", "pattern": "https://www.mfa.gov.rs/en/advice/{slug}"},
    )
    transport = site(
        {"https://www.mfa.gov.rs/en/advice/japan": (403, "<title>Just a moment...</title>")}
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        checker = LinkChecker(client, RobotsCache(user_agent="test"), sleep=no_sleep)
        build = await build_publisher(serbia, [destination("JP")], checker)

    assert build.links == {}
