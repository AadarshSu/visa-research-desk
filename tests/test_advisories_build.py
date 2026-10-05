"""The offline survey of where each government publishes travel advice (DECISIONS entry 260).

Nothing here reaches the network. What matters is that only a country's own government is kept,
that another government's advice about it is dropped, and that a failed search is not written as
"publishes nothing".
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from visa_research_agent.discovery.advisories_build import (
    AdvisorySurvey,
    AdvisorySurveyRow,
    advisory_queries,
    build_advisory_survey,
    load_advisory_survey,
    write_advisory_survey,
)
from visa_research_agent.discovery.lexicon import Country, CountryRegistry, Denylist
from visa_research_agent.discovery.models import SearchResult
from visa_research_agent.discovery.search import SearchError

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def country(code: str, name: str, tlds: list[str]) -> Country:
    return Country(code=code, alpha3=f"{code}X", name=name, tlds=tlds)


def countries(*items: Country) -> CountryRegistry:
    return CountryRegistry(schema_version=1, countries=list(items))


def denylist() -> Denylist:
    return Denylist(schema_version=1, commercial=["traveladvisory-agency.com"])


class StubProvider:
    """Fixed URLs for any query containing a key like `site:uk`; fails for keys in `failing`."""

    def __init__(self, by_country: dict[str, list[str]], failing: set[str] | None = None) -> None:
        self.by_country = by_country
        self.failing = failing or set()
        self.queries: list[str] = []

    async def search(self, query: str, *, count: int) -> list[SearchResult]:
        self.queries.append(query)
        for name, urls in self.by_country.items():
            if name.lower() in query.lower():
                if name in self.failing:
                    raise SearchError("the search provider answered HTTP 429")
                return [
                    SearchResult(url=url, title="", snippet="", query=query, rank=rank)
                    for rank, url in enumerate(urls)
                ]
        return []


async def no_sleep(_: float) -> None:
    return None


async def test_only_the_countrys_own_government_is_kept() -> None:
    """The State Department's page about the UK is advice for Americans, never the UK's for its own
    citizens, so it is dropped however well it ranks."""

    provider = StubProvider(
        {
            "site:uk": [
                "https://travel.state.gov/content/travel/en/traveladvisories/united-kingdom.html",
                "https://www.gov.uk/foreign-travel-advice",
                "https://www.traveladvisory-agency.com/uk",
            ]
        }
    )
    survey, failures = await build_advisory_survey(
        countries(country("GB", "United Kingdom", ["uk"])),
        provider,
        denylist(),
        sleep=no_sleep,
        now=lambda: NOW,
    )

    assert not failures
    row = survey.get("GB")
    assert row is not None
    assert [c.domain for c in row.own_government] == ["www.gov.uk"]
    assert row.own_government[0].urls == ["https://www.gov.uk/foreign-travel-advice"]
    assert row.unconfirmable == []


async def test_a_ministry_with_no_marker_is_reported_never_kept() -> None:
    """Germany's foreign office carries no governmental marker. The rule cannot confirm it, so it is
    left for a reviewed row (entry 33), not quietly admitted."""

    provider = StubProvider(
        {
            "site:de": [
                "https://www.auswaertiges-amt.de/de/ReiseUndSicherheit/reise-und-sicherheitshinweise"
            ]
        }
    )
    survey, _ = await build_advisory_survey(
        countries(country("DE", "Germany", ["de"])),
        provider,
        denylist(),
        sleep=no_sleep,
        now=lambda: NOW,
    )

    row = survey.get("DE")
    assert row is not None
    assert row.own_government == []
    assert [c.domain for c in row.unconfirmable] == ["auswaertiges-amt.de"]


async def test_a_failed_search_is_left_out_rather_than_written_as_publishing_nothing() -> None:
    provider = StubProvider(
        {"site:jp": ["https://www.anzen.mofa.go.jp/"], "site:in": []}, failing={"site:in"}
    )
    survey, failures = await build_advisory_survey(
        countries(country("JP", "Japan", ["jp"]), country("IN", "India", ["in"])),
        provider,
        denylist(),
        sleep=no_sleep,
        now=lambda: NOW,
    )

    assert [row.code for row in survey.countries] == ["JP"]
    assert "429" in failures["IN"]


async def test_a_survey_resumes_rather_than_searching_again() -> None:
    provider = StubProvider({"site:in": []})
    existing = AdvisorySurvey(
        generated_at=NOW, countries=[AdvisorySurveyRow(code="JP", name="Japan")]
    )

    survey, _ = await build_advisory_survey(
        countries(country("JP", "Japan", ["jp"]), country("IN", "India", ["in"])),
        provider,
        denylist(),
        existing=existing,
        sleep=no_sleep,
        now=lambda: NOW,
    )

    assert [row.code for row in survey.countries] == ["IN", "JP"]
    assert all("site:jp" not in query for query in provider.queries)
    # Searched and nothing of its own found: a row, empty, which is a finding to check.
    assert survey.get("IN") == AdvisorySurveyRow(code="IN", name="India")


def test_every_query_is_confined_to_the_countrys_own_domains() -> None:
    """Unconfined, search reads the country as the destination and returns other governments'
    advice about it, which left 118 of 198 countries empty on the first full run."""

    queries = advisory_queries(Country(code="CZ", alpha3="CZE", name="Czechia", tlds=["cz"]))
    assert len(queries) == 2
    assert all(query.endswith("site:cz") for query in queries)


def test_a_survey_round_trips_through_its_file(tmp_path: Path) -> None:
    path = tmp_path / "advisories" / "survey.yaml"
    survey = AdvisorySurvey(
        generated_at=NOW, countries=[AdvisorySurveyRow(code="IN", name="India")]
    )

    write_advisory_survey(survey, path)

    assert path.read_text(encoding="utf-8").startswith("# Where each passport country")
    assert load_advisory_survey(path) == survey
