"""Keeping a resolved corridor on disk so a request does not re-search the web.

Resolving a corridor costs a crawl, twenty-five fetches and a model call, plus up to fifteen
searches — three per trusted domain. (It cost a bootstrap too until entry 38 moved that to a
committed registry, and ten fetches until entry 40 widened the shortlist.) That is fine as a
deliberate command and far too much for every request, so the result is cached.

A resolved corridor is **not** evidence and has a different lifetime from it. The evidence cache in
`research/source_cache.py` measures freshness in hours, because a government page can change any
day. Which *pages* answer a corridor changes on the timescale of site redesigns, so this measures
weeks. Both still expire: a corridor is re-resolved eventually, and its pages are re-fetched under
their own, much shorter, TTL every time a plan is produced.

Deliberately a file store rather than an `lru_cache`. A process-lifetime memo would serve a corridor
resolved weeks ago for as long as the server stayed up, with no way to notice.

**A corridor resolved under other discovery rules is a miss.** Each one records a fingerprint of
what discovery was asked — the roles, the selection and roles prompts, the scoring vocabulary and
the always-read pages. New Zealand `US/US` was served for a day after entry 275 from a corridor
resolved before the `travel_authorisation` role existed, so no page was ever looked for it.
"""

import json
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from visa_research_agent.config.loader import config_path
from visa_research_agent.discovery.models import ROLE_ORDER, Corridor, ResolvedCorridor
from visa_research_agent.domain.models import StrictModel
from visa_research_agent.research.errors import VisaResearchError


class CorridorStoreError(VisaResearchError):
    """Raised when the corridor store cannot be read or written safely."""


@lru_cache(maxsize=1)
def discovery_fingerprint() -> str:
    """A digest of what discovery is asked, so a corridor resolved under other rules is re-resolved.

    Read once per process: the files are package data and change only with a deploy.
    """

    prompts = files("visa_research_agent.prompts")
    material = json.dumps(
        {
            "roles": list(ROLE_ORDER),
            "select": prompts.joinpath("select_candidates.txt").read_text(encoding="utf-8"),
            "roles_prompt": prompts.joinpath("adjudicate_roles.txt").read_text(encoding="utf-8"),
            "lexicon": config_path("discovery_lexicon.yaml").read_text(encoding="utf-8"),
            "always_read": config_path("always_read.yaml").read_text(encoding="utf-8"),
        },
        sort_keys=True,
    )
    return sha256(material.encode("utf-8")).hexdigest()


class StoredCorridor(StrictModel):
    """One resolved corridor, with the domains that were trusted to produce it."""

    schema_version: Literal[1] = 1
    resolved: ResolvedCorridor
    trusted_domains: list[str] = Field(default_factory=list)
    withheld_domains: dict[str, str] = Field(default_factory=dict)
    stored_at: datetime
    discovery_fingerprint: str = ""
    """`discovery_fingerprint()` when it was resolved. Empty on a corridor stored before it was
    recorded, which therefore never matches and is resolved again."""

    @field_validator("stored_at")
    @classmethod
    def validate_stored_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stored_at must include a timezone")
        return value

    def age_hours(self, now: datetime) -> float:
        """Hours since this corridor was resolved, never negative."""

        return max((now - self.stored_at).total_seconds() / 3600, 0.0)


class FileCorridorStore:
    """One JSON document per corridor, written atomically."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, corridor: Corridor) -> Path:
        return self.directory / f"{sha256(corridor.key.encode()).hexdigest()}.json"

    def load(self, corridor: Corridor) -> StoredCorridor | None:
        """Return a stored corridor, treating anything unreadable or outdated as a miss."""

        try:
            raw = self._path(corridor).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CorridorStoreError("The corridor store could not be read") from exc

        try:
            stored = StoredCorridor.model_validate_json(raw)
        except ValidationError:
            # A store written by an older schema is a miss, not a crash: re-resolving is always
            # safe, and serving something whose shape is no longer understood is not.
            return None
        if stored.discovery_fingerprint != discovery_fingerprint():
            return None
        return stored

    def store(
        self,
        corridor: Corridor,
        resolved: ResolvedCorridor,
        trusted_domains: list[str],
        withheld_domains: dict[str, str],
        now: datetime,
    ) -> StoredCorridor:
        entry = StoredCorridor(
            resolved=resolved,
            trusted_domains=trusted_domains,
            withheld_domains=withheld_domains,
            stored_at=now,
            discovery_fingerprint=discovery_fingerprint(),
        )
        path = self._path(corridor)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written to a neighbouring temporary file and moved, so a crash mid-write cannot
            # leave a half-written corridor that later reads as valid.
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                json.dump(entry.model_dump(mode="json"), handle, ensure_ascii=False)
                temporary = Path(handle.name)
            temporary.replace(path)
        except OSError as exc:
            raise CorridorStoreError("The corridor store could not be written") from exc
        return entry

    def evict(self, corridor: Corridor) -> str | None:
        """Delete a stored corridor; return what it held, or `None` when there was none.

        A traveller's report evicts the corridor it names (entry 282): the store is shared for a
        week, and a resolution that chose the wrong pages is the one fault no plan call recovers
        from. The text is returned so the report keeps what was served.
        """

        path = self._path(corridor)
        try:
            raw = path.read_text(encoding="utf-8")
            path.unlink()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CorridorStoreError("The corridor store could not be evicted") from exc
        return raw
