"""Surveying which governments publish travel advice for their citizens, offline (entry 260).

The first build step of the advice panel. For each passport country in `countries.yaml`, search
proposes where its government publishes travel advice, and **only that government's own domains are
kept** — governmental and under its own top-level domain, the rule `authority_domains.yaml` is built
by. A domain under its own top-level domain with no governmental marker (Germany's
`auswaertiges-amt.de`) is reported as unconfirmable, never kept: the same limit of the rule entry 33
describes, left for a person to resolve with a reviewed row.

**The output is a survey, not configuration.** Nothing here writes `advisory_publishers.yaml`; a
person reads each row, finds the advice index and the selectors, and only reviewed rows reach the
committed file. It is kept apart from `registry_build.py`, which it mirrors, because a passport
country's advice publisher is a different question from a destination's visa authority, asked of a
different set of countries.

Like the registry build it is resumable, paced, and leaves a country whose search failed out of the
file rather than writing it empty: "this government publishes nothing we found" is a finding, and
"we never asked" is not.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import Field

from visa_research_agent.discovery.bootstrap import DomainProposal, propose_domains
from visa_research_agent.discovery.lexicon import Country, CountryRegistry, Denylist
from visa_research_agent.discovery.models import SearchResult
from visa_research_agent.discovery.search import SearchError, SearchProvider, search_all
from visa_research_agent.domain.models import StrictModel

# Two queries a top-level domain; spaced by country as the registry build is.
DEFAULT_SECONDS_BETWEEN_COUNTRIES = 1.0
RESULTS_PER_QUERY = 10
# Most countries have one; a few list a second (`gov` for the US). Bounds the searches a country.
MAXIMUM_TLDS_SEARCHED = 2


def advisory_queries(country: Country) -> list[str]:
    """Queries for where a government advises its own citizens about travelling abroad.

    **Restricted to the country's own top-level domains with `site:`.** Asked unrestricted ("Czechia
    government travel advice"), search reads the country as the *destination* and returns the US,
    UK, Swiss and Canadian advice about it — all correctly dropped by the own-government rule, which
    left 118 of 198 countries with nothing on the first full run (entry 260). Two phrasings a
    domain, overlapping as `bootstrap_queries` are. English only: the reviewer reads the URLs, and a
    government publishing only in its own language may still need finding by hand.
    """

    return [
        query
        for tld in country.tlds[:MAXIMUM_TLDS_SEARCHED]
        for query in (
            f"travel advice for citizens abroad site:{tld}",
            f"ministry of foreign affairs travel warnings by country site:{tld}",
        )
    ]


class AdvisoryCandidate(StrictModel):
    """One domain search proposed for a country's travel advice, with what it was seen with."""

    domain: str
    queries: int = Field(ge=1)
    urls: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)


class AdvisorySurveyRow(StrictModel):
    """What the survey found for one passport country."""

    code: str
    name: str
    own_government: list[AdvisoryCandidate] = Field(default_factory=list)
    """Governmental and under the country's own top-level domain: candidates for its publisher."""

    unconfirmable: list[AdvisoryCandidate] = Field(default_factory=list)
    """Under its own top-level domain with no governmental marker. May be the real publisher; never
    kept without a person's review (entry 33)."""


class AdvisorySurvey(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    countries: list[AdvisorySurveyRow] = Field(default_factory=list)

    def get(self, code: str) -> AdvisorySurveyRow | None:
        return next((row for row in self.countries if row.code == code), None)


@dataclass(frozen=True)
class SurveyProgress:
    country: Country
    row: AdvisorySurveyRow | None
    error: str | None


def _candidate(proposal: DomainProposal) -> AdvisoryCandidate:
    return AdvisoryCandidate(
        domain=proposal.domain,
        queries=proposal.corroboration,
        # The same page found by several queries is one page to review.
        urls=list(dict.fromkeys(proposal.example_urls)),
        titles=list(dict.fromkeys(proposal.titles)),
    )


def survey_row(
    country: Country, results: dict[str, list[SearchResult]], denylist: Denylist
) -> AdvisorySurveyRow:
    """Sort one country's search results into its own government's domains and the unconfirmable.

    Another government's domain is dropped: the US State Department's page about Japan is advice
    for Americans, never Japan's advice for its own citizens.
    """

    report = propose_domains(country.name, results, denylist, country.tlds)
    return AdvisorySurveyRow(
        code=country.code,
        name=country.name,
        own_government=[_candidate(p) for p in report.proposals if p.is_own_government],
        unconfirmable=[
            _candidate(p)
            for p in report.proposals
            if p.belongs_to_destination and not p.looks_governmental
        ],
    )


async def build_advisory_survey(
    countries: CountryRegistry,
    provider: SearchProvider,
    denylist: Denylist,
    *,
    existing: AdvisorySurvey | None = None,
    on_progress: Callable[[SurveyProgress], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    seconds_between_countries: float = DEFAULT_SECONDS_BETWEEN_COUNTRIES,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    write: Callable[[AdvisorySurvey], None] | None = None,
) -> tuple[AdvisorySurvey, dict[str, str]]:
    """Survey every country not already in `existing`, returning the survey and what failed."""

    rows = {row.code: row for row in existing.countries} if existing else {}
    failures: dict[str, str] = {}
    pending = [country for country in countries.countries if country.code not in rows]

    for index, country in enumerate(pending):
        if index:
            await sleep(seconds_between_countries)
        try:
            results = await search_all(provider, advisory_queries(country), count=RESULTS_PER_QUERY)
        except SearchError as exc:
            failures[country.code] = str(exc)
            if on_progress is not None:
                on_progress(SurveyProgress(country, None, str(exc)))
            continue

        row = survey_row(country, results, denylist)
        rows[country.code] = row
        if on_progress is not None:
            on_progress(SurveyProgress(country, row, None))
        if write is not None:
            write(_assemble(rows, now()))

    return _assemble(rows, now()), failures


def _assemble(rows: dict[str, AdvisorySurveyRow], generated_at: datetime) -> AdvisorySurvey:
    return AdvisorySurvey(
        generated_at=generated_at, countries=sorted(rows.values(), key=lambda row: row.code)
    )


HEADER = """\
# Where each passport country's government publishes travel advice — a SURVEY, not configuration.
#
# Generated by `visa-discover advisories` (DECISIONS entry 260). Search proposed; the own-government
# rule kept a domain only if it is governmental and under that country's own top-level domain.
# A person reads each row and writes the reviewed ones into config/advisory_publishers.yaml.
#
#   own_government — candidates for the publisher. Not yet reviewed, not yet trusted.
#   unconfirmable  — under the country's own top-level domain with no governmental marker. May be
#                    the real publisher (Germany's auswaertiges-amt.de); needs a reviewed row.
#   both empty     — search found nothing of the government's own. A finding, to be checked.
#
# A country whose search failed is absent, and the next run retries it.
"""


def load_advisory_survey(path: Path) -> AdvisorySurvey | None:
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AdvisorySurvey.model_validate(payload)


def write_advisory_survey(survey: AdvisorySurvey, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = survey.model_dump(mode="json")
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + body, encoding="utf-8")
