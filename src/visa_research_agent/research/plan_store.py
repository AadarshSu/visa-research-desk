"""Reusing a plan the model wrote minutes earlier from exactly the same inputs.

DECISIONS entry 178, the owner's decision, amending entry 44. Writing the plan is the longest wait
in a request, and a repeat is nearly all of it (entry 171): about 24 seconds spent asking the model
the same question over the same evidence. So the model's *draft* is kept, never the plan. A reused
draft goes back through the whole construction on every request — every validator,
and a status graded on that request's own retrieval — so what is reused is the inference, and only
for inputs that are byte-identical.

**The key is everything the model is shown**: its instructions, the request around the packet, the
packet itself — the traveller, the destination's policy, every page's text and when it was
retrieved — and the model's settings and output schema. A page whose text changes, or which is
re-checked, moves its retrieval time and so misses: a `304` moves `fetched_at` (entry 4). The reuse
window is reviewable policy and may not outlast the page cache's own freshness window.

**A refusal is never kept.** Only a draft that became a plan is stored, so a request that refused
asks the model again next time, as it always has (entry 151). **Nor is a draft past its window:**
each new draft kept prunes the ones that can no longer be reused (TODO item 55, entry 276).
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import ValidationError, field_validator

from visa_research_agent.domain.models import StrictModel, VisaPlanDraft
from visa_research_agent.research.errors import VisaResearchError

PLAN_REUSE_VERSION = 1
"""Bump when the plan call changes in a way the key cannot see — how its messages are assembled or
sent — so every draft written under the old call is a miss."""

_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PlanStoreError(VisaResearchError):
    """Raised when the plan store cannot be read or written safely."""


class StoredPlanDraft(StrictModel):
    """One model draft, with when it was written."""

    schema_version: Literal[1] = 1
    draft: VisaPlanDraft
    stored_at: datetime

    @field_validator("stored_at")
    @classmethod
    def validate_stored_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("stored_at must include a timezone")
        return value

    def age_hours(self, now: datetime) -> float:
        """Hours since the draft was written, never negative."""

        return max((now - self.stored_at).total_seconds() / 3600, 0.0)


def plan_key(*, fingerprint: str, system_prompt: str, research_packet: str) -> str:
    """The identity of one plan call: everything the model is shown, and how it is asked."""

    material = json.dumps(
        {
            "version": PLAN_REUSE_VERSION,
            "fingerprint": fingerprint,
            "schema": VisaPlanDraft.model_json_schema(),
            "system_prompt": system_prompt,
            "research_packet": research_packet,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return sha256(material.encode("utf-8")).hexdigest()


class FilePlanStore:
    """One JSON document per plan call, written atomically."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        # A key is only ever `plan_key`'s digest, so anything else never reaches the filesystem.
        if not _KEY_PATTERN.match(key):
            raise PlanStoreError("A plan store key must be a SHA-256 digest")
        return self.directory / f"{key}.json"

    def load(self, key: str, *, now: datetime, maximum_age_hours: float) -> VisaPlanDraft | None:
        """A draft written for this key inside the reuse window, or `None` for anything else.

        Unreadable, outdated in shape, or older than the window are all a miss: asking the model
        again is always safe, and reusing something not understood is not.
        """

        try:
            raw = self._path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PlanStoreError("The plan store could not be read") from exc

        try:
            stored = StoredPlanDraft.model_validate_json(raw)
        except ValidationError:
            return None
        if stored.age_hours(now) >= maximum_age_hours:
            return None
        return stored.draft

    def store(self, key: str, draft: VisaPlanDraft, *, now: datetime) -> None:
        path = self._path(key)
        entry = StoredPlanDraft(draft=draft, stored_at=now)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written to a neighbouring temporary file and moved, so a crash mid-write cannot leave
            # a half-written draft that later reads as valid.
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                json.dump(entry.model_dump(mode="json"), handle, ensure_ascii=False)
                temporary = Path(handle.name)
            temporary.replace(path)
        except OSError as exc:
            raise PlanStoreError("The plan store could not be written") from exc

    def prune(self, *, now: datetime, maximum_age_hours: float) -> int:
        """Delete every draft past the reuse window, and any that cannot be read; return how many.

        A draft past its window can never be reused, so keeping it bought nothing, and since entry
        276 its prose may name what the traveller shared — a passport's expiry, a past refusal.
        """

        removed = 0
        for path in self.directory.glob("*.json"):
            if not _KEY_PATTERN.match(path.stem):
                continue
            try:
                stored = StoredPlanDraft.model_validate_json(path.read_text(encoding="utf-8"))
                expired = stored.age_hours(now) >= maximum_age_hours
            except (OSError, ValidationError):
                expired = True
            if expired:
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise PlanStoreError("The plan store could not be pruned") from exc
                removed += 1
        return removed


@dataclass(frozen=True)
class PlanReuse:
    """What the extractor needs to reuse a draft: where they are kept, for how long, and the model.

    `fingerprint` describes the call beyond its prompt and packet — the model, its reasoning effort
    and output ceiling — because a draft another model wrote is not the same answer.
    """

    store: FilePlanStore
    maximum_age_hours: float
    fingerprint: str
