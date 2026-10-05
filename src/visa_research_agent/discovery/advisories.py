"""The link to the traveller's own government's travel advice a plan may show (entry 260).

A third trust tier, beside "the destination's own government" and the EU's, and narrower than both:
the government of the traveller's **passport**, for one panel, as **a link and nothing else**. No
page is fetched, read, quoted or summarised at request time — the answer is a lookup in two
committed files:

* `advisory_publishers.yaml` — reviewed by a person: one row per government that publishes advice,
  with its domains and the evidence for any that carries no governmental marker.
* `advisory_links.yaml` — generated offline by `visa-discover advisory-links`: each destination's
  page, written only after it opened for us (or, for a government that refuses us, only when its own
  published data names it).

Nothing here may reach the visa answer. The module has no route into a research packet, and the
plan call never sees it.
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from visa_research_agent.config.loader import config_path
from visa_research_agent.discovery.bootstrap import belongs_to_destination, looks_governmental
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.domain.models import StrictModel
from visa_research_agent.domain.trust import host_is_within, host_of, registrable_domain

PUBLISHERS_FILENAME = "advisory_publishers.yaml"
LINKS_FILENAME = "advisory_links.yaml"

# The languages a publisher writes in, named the way the panel says them. A code missing here is a
# configuration error rather than a blank label.
LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nb": "Norwegian",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "th": "Thai",
    "zh": "Chinese",
}


class DestinationPages(StrictModel):
    """How the offline build finds this government's page for one destination."""

    method: Literal["pattern", "index", "us_feed", "de_feed", "nl_feed"]
    pattern: str | None = None
    """An address with `{ISO2}`, `{iso2}`, `{ISO3}`, `{slug}`, `{name}` or `{name-slug}`, the last
    two in the row's language."""

    index: str | None = None
    """A page listing every destination, read offline and matched by name."""

    link_prefix: str | None = None
    """Only links whose path starts with this count as destination pages on the index."""

    depth: Literal[1, 2] = 1
    """2 for an index of regions whose pages list the countries."""

    @model_validator(mode="after")
    def method_has_what_it_needs(self) -> "DestinationPages":
        if self.method == "pattern" and not self.pattern:
            raise ValueError("a pattern method needs a pattern")
        if self.method == "index" and not (self.index and self.link_prefix):
            raise ValueError("an index method needs an index and a link prefix")
        return self


class AdvisoryPublisher(StrictModel):
    """One government that publishes travel advice for its own citizens."""

    code: str = Field(pattern=r"^[A-Z]{2}$")
    government: str = Field(min_length=1)
    written_for: str = Field(min_length=1)
    language: str
    domains: list[str] = Field(min_length=1)
    evidence: dict[str, str] = Field(default_factory=dict)
    """Why a domain carrying no governmental marker is this government's — entry 111's tiers."""

    access: Literal["open", "refused"]
    """`refused`: it blocked, challenged or `Disallow`ed us, so a link to it was never checked."""

    access_note: str = ""
    page: str
    """The government's advice page or index — shown when no destination page is known."""

    english: str | None = None
    """The same advice in English, where the government publishes it."""

    destinations: DestinationPages | None = None

    @model_validator(mode="after")
    def every_address_is_this_governments(self) -> "AdvisoryPublisher":
        if self.language not in LANGUAGE_NAMES:
            raise ValueError(f"{self.code}: no name for language {self.language!r}")
        country = next((c for c in get_country_registry().countries if c.code == self.code), None)
        if country is None:
            raise ValueError(f"{self.code} is not in countries.yaml")
        for domain in self.domains:
            # The own-government rule: governmental **and** under the country's own top-level
            # domain — or a person's reviewed evidence for a government that marks no hostname.
            own = looks_governmental(registrable_domain(domain)) and belongs_to_destination(
                domain, country.tlds
            )
            if not own and domain not in self.evidence:
                raise ValueError(
                    f"{self.code}: {domain} is not confirmed as this government's own; a domain "
                    "the rule cannot confirm needs reviewed evidence (entry 111)"
                )
        addresses = [self.page, self.english]
        if self.destinations is not None:
            addresses += [self.destinations.index, self.destinations.pattern]
        for address in filter(None, addresses):
            host = host_of(address.replace("{", "x").replace("}", "x"))
            if not host_is_within(host, self.domains):
                raise ValueError(f"{self.code}: {address} is outside the row's domains")
        return self


class AdvisoryPublishers(StrictModel):
    schema_version: Literal[1]
    publishers: list[AdvisoryPublisher]

    @model_validator(mode="after")
    def one_row_per_government(self) -> "AdvisoryPublishers":
        codes = [row.code for row in self.publishers]
        if len(codes) != len(set(codes)):
            raise ValueError("one row per government")
        return self

    def get(self, code: str) -> AdvisoryPublisher | None:
        return next((row for row in self.publishers if row.code == code), None)


class AdvisoryLinks(StrictModel):
    """Each government's checked page per destination, generated offline."""

    schema_version: Literal[1]
    generated_at: datetime
    links: dict[str, dict[str, str]] = Field(default_factory=dict)
    """Passport code → destination code → the government's page for that destination."""


class AdviceLink(StrictModel):
    """What the panel shows: a link, with whose it is. Never a word of the advice itself."""

    url: str
    government: str
    written_for: str
    language: str
    """The language's English name, e.g. "German"."""

    about_destination: bool
    """True for the destination's own page; false for the government's advice page or index."""

    checked: bool
    """False where the government refuses us, so the link was never opened from here."""

    english_url: str | None = None


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_advisory_publishers(path: Path | None = None) -> AdvisoryPublishers:
    return AdvisoryPublishers.model_validate(_load(config_path(PUBLISHERS_FILENAME, path)))


def load_advisory_links(path: Path | None = None) -> AdvisoryLinks:
    return AdvisoryLinks.model_validate(_load(config_path(LINKS_FILENAME, path)))


@lru_cache(maxsize=1)
def get_advisory_publishers() -> AdvisoryPublishers:
    return load_advisory_publishers()


@lru_cache(maxsize=1)
def get_advisory_links() -> AdvisoryLinks:
    return load_advisory_links()


def advice_link(
    passport: str,
    destination: str,
    publishers: AdvisoryPublishers,
    links: AdvisoryLinks,
) -> AdviceLink | None:
    """The link the panel shows for this passport and destination, or None for no panel.

    None where the passport's government publishes no advice we found (the owner, entry 260), and
    for travel to one's own country.
    """

    row = publishers.get(passport)
    if row is None or passport == destination:
        return None
    destination_page = links.links.get(passport, {}).get(destination)
    if destination_page is not None and not host_is_within(host_of(destination_page), row.domains):
        # A generated link off the row's domains is a build fault, never something to show.
        destination_page = None
    return AdviceLink(
        url=destination_page or row.page,
        government=row.government,
        written_for=row.written_for,
        language=LANGUAGE_NAMES[row.language],
        about_destination=destination_page is not None,
        checked=row.access == "open",
        english_url=row.english if row.language != "en" else None,
    )
