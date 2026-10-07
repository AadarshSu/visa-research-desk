"""Loading the country reference data, scoring vocabulary and denylist.

All three are version-controlled YAML so they can be reviewed and tuned without a code change.
"""

import re
from functools import cached_property, lru_cache
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from visa_research_agent.config.loader import config_path
from visa_research_agent.domain.models import StrictModel
from visa_research_agent.domain.trust import host_is_within

_WORDS = re.compile(r"\w+")


class Country(StrictModel):
    """One country, with the several ways it is written in URLs and link text."""

    code: str = Field(pattern=r"^[A-Z]{2}$")
    alpha3: str = Field(pattern=r"^[A-Z]{3}$")
    """ISO 3166-1 alpha-3. Only the edge reads it: Ofself may record a citizenship as `IND` where
    everything in this program is keyed on `IN` (TODO item 55)."""
    name: str = Field(min_length=1)
    synonyms: list[str] = Field(default_factory=list)
    demonyms: list[str] = Field(default_factory=list)
    host_labels: list[str] = Field(default_factory=list)
    mission_labels: list[str] = Field(default_factory=list)
    """How a post serving this country is named, in any language the authority might use.

    Brazil calls its Edinburgh consulate `consulado-edimburgo` and its London one
    `consulado-londres`, so the labels are Portuguese. Like `host_labels`, this mismatch is data
    rather than a special case in code.
    """
    tlds: list[str] = Field(default_factory=list)

    @property
    def slug(self) -> str:
        """The country as a URL-safe identifier, matching `DestinationConfig.slug`.

        Derived rather than stored so a country cannot be listed under one slug and researched
        under another: "United Arab Emirates" is always "united-arab-emirates".
        """

        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")

    @property
    def text_tokens(self) -> list[str]:
        """Every phrase that means this country in prose or a URL path."""

        return sorted({token.lower() for token in [self.name, *self.synonyms, *self.demonyms]})


class CountryRegistry(StrictModel):
    schema_version: Literal[1]
    countries: list[Country] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_codes(self) -> "CountryRegistry":
        codes = [country.code for country in self.countries]
        if len(codes) != len(set(codes)):
            raise ValueError("country codes must be unique")
        alpha3s = [country.alpha3 for country in self.countries]
        if len(alpha3s) != len(set(alpha3s)):
            raise ValueError("alpha-3 codes must be unique")
        return self

    def by_slug(self, slug: str) -> "Country | None":
        return next((country for country in self.countries if country.slug == slug), None)

    def code_for_name(self, name: str) -> str | None:
        """The ISO code for a country written the way a person writes it.

        The traveller profile records "India" and "United Kingdom"; corridors are keyed by IN and
        GB. Synonyms are matched too, so "UK" and "Britain" resolve as well.
        """

        wanted = name.strip().lower()
        for country in self.countries:
            if country.name.lower() == wanted:
                return country.code
            if any(synonym.lower() == wanted for synonym in country.synonyms):
                return country.code
        return None

    def code_for_alpha3(self, alpha3: str) -> str | None:
        """The alpha-2 code for an ISO alpha-3 code, such as `IN` for `IND`."""

        wanted = alpha3.strip().upper()
        return next((c.code for c in self.countries if c.alpha3 == wanted), None)

    def get(self, code: str) -> Country | None:
        normalized = code.strip().upper()
        return next((c for c in self.countries if c.code == normalized), None)

    def require(self, code: str) -> Country:
        country = self.get(code)
        if country is None:
            raise ValueError(f"country {code} is not in countries.yaml; add it before using it")
        return country

    @cached_property
    def _by_word(self) -> dict[str, tuple[int, ...]]:
        """Which countries a single word could belong to, by position in `countries`.

        Built once and only ever read as a **prefilter**: it says which countries are worth the
        exact check, never which country a page is about. Over-inclusion is harmless and
        under-inclusion would be a silent behaviour change, so a country whose tokens contain no
        word characters at all is placed under the empty key and always considered.
        """

        index: dict[str, set[int]] = {}
        for position, country in enumerate(self.countries):
            words = {
                word for token in country.text_tokens for word in _WORDS.findall(token.lower())
            }
            words.update(label.lower() for label in country.host_labels)
            for word in words or {""}:
                index.setdefault(word, set()).add(position)
        return {word: tuple(sorted(positions)) for word, positions in index.items()}

    def possible_for(self, words: set[str]) -> list[Country]:
        """The countries any of these words could name, **in registry order**.

        A superset of what an exact match would find, which is the whole contract: the caller still
        runs the real check on every country this returns, so the order and the answer are
        unchanged and only the number of exact checks falls.

        It exists because the corpus made an old cost visible. `wrong_country` scanned all 198
        countries for every candidate, and a corridor scored 471 of them; against a 3,216-entry
        corpus the same scan cost **3.3 seconds** of a 54-second corridor. See DECISIONS entry 50.
        """

        positions: set[int] = set(self._by_word.get("", ()))
        for word in words:
            positions.update(self._by_word.get(word, ()))
        return [self.countries[position] for position in sorted(positions)]


class LexiconTerm(StrictModel):
    phrase: str = Field(min_length=1)
    weight: float


class RoleTerms(StrictModel):
    terms: list[LexiconTerm] = Field(default_factory=list)
    whole_words: bool = False
    """Match each phrase only as whole words. "eta" is a word in "gov.uk/eta" and "(ETA)", and a
    substring of "details" and "Beta"."""
    title_and_address_only: bool = False
    """Score from a page's title, link text and address, never its body, and leave the role out of
    the breadth count (entry 275). Every GOV.UK visa page carries the ETA notice, so a body match
    credited 100 pages and the ETA's own page still never reached the selector; a page *named* for
    the authorisation is the page about it."""


class PurposeTerms(StrictModel):
    terms: list[str] = Field(default_factory=list)
    weight: float = 20.0


class OffScopeTerms(StrictModel):
    weight: float = -30.0
    terms: list[str] = Field(default_factory=list)


class Lexicon(StrictModel):
    """The scoring vocabulary, loaded from `discovery_lexicon.yaml`."""

    schema_version: Literal[1]
    link_text_weight: float = 1.2
    heading_weight: float = 0.5
    context_weight: float = 0.5
    linking_title_weight: float = 0.25
    footer_factor: float = 0.5
    boilerplate_tokens: list[str] = Field(default_factory=list)
    """Path segments that mark site furniture — a legal notice is never visa guidance."""
    base_purpose_weight: float = 18.0
    breadth_threshold: int = 2
    index_page_penalty: float = -18.0
    index_page_stems: list[str] = Field(default_factory=list)
    roles: dict[str, RoleTerms] = Field(default_factory=dict)
    purposes: dict[str, PurposeTerms] = Field(default_factory=dict)
    off_scope: OffScopeTerms = Field(default_factory=OffScopeTerms)
    archive_tokens: list[str] = Field(default_factory=list)
    hard_off_scope: list[str] = Field(default_factory=list)
    hard_off_scope_beside_visa_question: list[str] = Field(default_factory=list)
    """`hard_off_scope` terms that do not veto a link which also asks whether a visa is needed
    (TODO item 70). Still penalised through `off_scope`; only the veto is lifted."""
    visa_question_terms: list[str] = Field(default_factory=list)
    form_penalty: float = -35.0
    form_terms: list[str] = Field(default_factory=list)
    language_penalty: float = -25.0
    language_terms: list[str] = Field(default_factory=list)
    base_visa_weight: float = 6.0
    nationality_weight: float = 40.0
    residence_weight: float = 40.0
    """What being about the country the traveller applies from is worth, on post-specific roles."""

    purpose_bonus_weight: float = 20.0
    shallow_path_weight: float = 8.0
    depth_penalty_weight: float = -10.0
    mission_host_bonus: float = 8.0
    other_mission_penalty: float = -45.0
    mission_path_markers: list[str] = Field(default_factory=list)
    document_nouns: list[str] = Field(default_factory=list)
    document_noun_weight: float = 9.0
    document_noun_cap: int = 5
    minimum_document_nouns: int = 3
    pdf_checklist_bonus: float = 10.0
    authority_kind_bonus: dict[str, float] = Field(default_factory=dict)

    def off_scope_terms_for(self, purpose: str) -> list[str]:
        """Off-scope terms, minus any that belong to the traveller's own purpose.

        Without this subtraction a study-purpose corridor would penalise the very pages it needs.
        """

        own = {term.lower() for term in self.purposes.get(purpose, PurposeTerms()).terms}
        return [term for term in self.off_scope.terms if term.lower() not in own]


class Denylist(StrictModel):
    """Domains that may never be proposed as authorities."""

    schema_version: Literal[1]
    commercial: list[str] = Field(default_factory=list)
    reference: list[str] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)

    @property
    def domains(self) -> list[str]:
        return [*self.commercial, *self.reference, *self.media]

    def blocks(self, host: str) -> bool:
        """True when a host is denied, including any subdomain of a denied domain."""

        return host_is_within(host, self.domains)


def _load_yaml(filename: str) -> Any:
    with config_path(filename).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache(maxsize=1)
def get_country_registry() -> CountryRegistry:
    return CountryRegistry.model_validate(_load_yaml("countries.yaml"))


@lru_cache(maxsize=1)
def get_lexicon() -> Lexicon:
    return Lexicon.model_validate(_load_yaml("discovery_lexicon.yaml"))


@lru_cache(maxsize=1)
def get_denylist() -> Denylist:
    return Denylist.model_validate(_load_yaml("discovery_denylist.yaml"))
