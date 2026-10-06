"""Pages a country's corridors read on every run, named by a person (DECISIONS entry 267).

The EU's regulation and ETIAS page already reach every Schengen corridor this way (entry 201). This
is the same path for one country's own pages: a page a person knows answers a role for every
traveller of a purpose, which the ranking keeps losing. Australia's ETA page is the first — it lists
the passports eligible for the ETA, and ranks 55th by stored text for `visa_decision` against about
twenty places a role (entry 266).

**It is read, never believed.** A page here is added to what a corridor fetches after selection,
fetched live like any other, and the role adjudicator decides what, if anything, it answers. A
traveller whose passport the page does not list gets nothing from it.

**It is an exceptions list, not a way of working.** Every entry says why it is here and which
ranking miss it covers; when the ranking finds the page by itself, the entry should come out.

**Trust is checked when the file loads.** A URL off its country's trusted domains is refused then,
as `authority_domains.yaml` is the only list a page may be read from.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field

from visa_research_agent.config.loader import config_path
from visa_research_agent.discovery.models import PageLink
from visa_research_agent.discovery.registry import AuthorityRegistry, get_authority_registry
from visa_research_agent.domain.models import StrictModel, TravelPurpose
from visa_research_agent.domain.trust import host_is_within

DISCOVERED_FROM = "always_read.yaml"


class AlwaysReadPage(StrictModel):
    """One page every corridor of the listed purposes reads, and why."""

    url: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=300)
    purposes: list[TravelPurpose] = Field(min_length=1)
    why: str = Field(min_length=1)
    """What the page answers, for whom, and the ranking miss it covers. Required: an entry nobody
    can explain cannot be removed when the ranking is fixed."""


class CountryAlwaysRead(StrictModel):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    pages: list[AlwaysReadPage] = Field(min_length=1)


class AlwaysReadRegistry(StrictModel):
    schema_version: Literal[1]
    countries: list[CountryAlwaysRead] = Field(default_factory=list)

    def pages_for(self, code: str, purpose: str) -> list[tuple[PageLink, str]]:
        """The pages a corridor to `code` for `purpose` reads on every run."""

        return [
            (
                PageLink(url=page.url, text=page.title, depth=0, discovered_from=DISCOVERED_FROM),
                page.title,
            )
            for country in self.countries
            if country.code == code
            for page in country.pages
            if purpose in page.purposes
        ]


def check_trusted(registry: AlwaysReadRegistry, authorities: AuthorityRegistry) -> None:
    """Refuse any page that is not on its country's trusted domains."""

    for country in registry.countries:
        entry = authorities.get(country.code)
        domains = entry.domains if entry is not None else []
        for page in country.pages:
            host = urlsplit(page.url).hostname or ""
            if urlsplit(page.url).scheme != "https" or not host_is_within(host, domains):
                raise ValueError(
                    f"always_read.yaml: {page.url} is not an https page on {country.code}'s "
                    f"trusted domains ({', '.join(domains) or 'none'}), so it may not be read"
                )


def load_always_read(
    path: Path | None = None, authorities: AuthorityRegistry | None = None
) -> AlwaysReadRegistry:
    with config_path("always_read.yaml", path).open(encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)
    registry = AlwaysReadRegistry.model_validate(raw)
    check_trusted(registry, authorities or get_authority_registry())
    return registry


@lru_cache(maxsize=1)
def get_always_read() -> AlwaysReadRegistry:
    return load_always_read()
