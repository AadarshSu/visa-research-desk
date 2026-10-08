"""A traveller's report of a corridor that did not work, kept for diagnosis only (entry 44).

A report also evicts the research it names (entry 282): the stored corridor, shared between
travellers for a week, and every plan draft written for it. The next request researches afresh.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from visa_research_agent.api.schemas import VisaPlanRequest
from visa_research_agent.config.traveller import DEFAULT_TRAVELLER_PROFILE
from visa_research_agent.discovery.automatic import find_country
from visa_research_agent.discovery.corridor_store import CorridorStoreError, FileCorridorStore
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.recall_log import FileRecallLog
from visa_research_agent.research.errors import VisaResearchError
from visa_research_agent.research.plan_store import FilePlanStore, PlanStoreError

MAXIMUM_REPORT_BYTES = 512_000
"""A plan is about 10 KB as JSON; this is fifty of them. Anything larger is not a page's output."""

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

Stage = Annotated[str, StringConstraints(min_length=1, max_length=40)]


class ReportStoreError(VisaResearchError):
    """Raised when a report cannot be written or read."""


class ReportRequest(BaseModel):
    """What the page sends: the request it made, what it showed, and the steps it saw."""

    model_config = ConfigDict(extra="forbid")

    request: VisaPlanRequest
    outcome: Literal["plan", "refusal"]
    shown: dict[str, Any]
    stages: list[Stage] = Field(default_factory=list, max_length=40)
    message: str | None = Field(default=None, max_length=2000)
    """What the traveller says is wrong with a plan. A refusal is reported without one."""


class ReportedCorridor(BaseModel):
    destination: str
    passport_nationality: str
    applying_from: str
    purpose: str
    key: str | None = None
    """None when the destination is not a country this agent knows, so no run was ever keyed."""


class ReportBuild(BaseModel):
    commit: str | None
    static_asset_version: str


class EvictedResearch(BaseModel):
    """What the report took out of the shared stores, so the next request researches afresh."""

    corridor: dict[str, Any] | None
    """The stored corridor as it was served — which pages filled which roles — or `None` when none
    was stored. Kept because the eviction removes the only other copy."""
    plan_drafts: int
    failed: bool = False
    """A store could not be read or written; the report is kept anyway."""


class ProblemReport(BaseModel):
    schema_version: Literal[1] = 1
    report_id: str
    received_at: datetime
    corridor: ReportedCorridor
    outcome: Literal["plan", "refusal"]
    cause: str | None
    """The refusal's `detail.cause`, copied up so a list of reports sorts without opening them."""
    shown: dict[str, Any]
    stages: list[str]
    message: str | None = None
    recall_log: dict[str, Any] | None
    # The corridor's last recorded run; a stored corridor writes none, so check its `recorded_at`.
    build: ReportBuild
    evicted: EvictedResearch | None = None
    """`None` for a destination with no corridor key, and on reports kept before entry 282."""

    def rerun_command(self) -> str | None:
        """The `visa-discover corridor` line that re-runs this corridor, cold."""

        if self.corridor.key is None:
            return None
        return (
            f"visa-discover corridor --destination {self.corridor.destination} "
            f"--nationality {self.corridor.passport_nationality} "
            f"--from {self.corridor.applying_from} --purpose {self.corridor.purpose}"
        )


def reported_corridor(request: VisaPlanRequest) -> tuple[ReportedCorridor, Corridor | None]:
    """The corridor's codes, and the `Corridor` its run was logged under when there is one."""

    traveller = request.traveller.to_profile() if request.traveller else DEFAULT_TRAVELLER_PROFILE
    country = find_country(request.destination, get_country_registry())
    corridor = (
        Corridor(
            destination_slug=country.slug,
            passport_nationality=traveller.passport_nationality,
            applying_from=traveller.country_of_residence,
            purpose=traveller.travel_purpose,
        )
        if country is not None
        else None
    )
    return (
        ReportedCorridor(
            destination=country.slug if country is not None else request.destination[:80],
            passport_nationality=traveller.passport_nationality,
            applying_from=traveller.country_of_residence,
            purpose=traveller.travel_purpose,
            key=corridor.key if corridor is not None else None,
        ),
        corridor,
    )


def copy_of_recall_log(directory: Path, corridor: Corridor | None) -> dict[str, Any] | None:
    """The run's log as it stands now. Missing or unreadable is recorded as missing, not fatal."""

    if corridor is None:
        return None
    try:
        loaded = json.loads(FileRecallLog(directory).path_for(corridor).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def evict_reported_research(
    corridor: Corridor | None, corridors: FileCorridorStore, plans: FilePlanStore
) -> EvictedResearch | None:
    """Take the reported corridor and its plan drafts out of the shared stores (entry 282).

    A failing store costs the eviction, never the report: the traveller has said something is
    wrong, and that is kept whatever else happens.
    """

    if corridor is None:
        return None
    try:
        raw = corridors.evict(corridor)
        stored = json.loads(raw) if raw is not None else None
        drafts = plans.evict_corridor(corridor.key)
    except (CorridorStoreError, PlanStoreError, ValueError):
        return EvictedResearch(corridor=None, plan_drafts=0, failed=True)
    return EvictedResearch(
        corridor=stored if isinstance(stored, dict) else None, plan_drafts=drafts
    )


def current_commit(root: Path = REPOSITORY_ROOT) -> str | None:
    """The checked-out commit, read from `.git` rather than by running git. None when unknown."""

    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head or None
        ref = head.removeprefix("ref: ")
        loose = root / ".git" / ref
        if loose.exists():
            return loose.read_text(encoding="utf-8").strip() or None
        for line in (root / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(f" {ref}"):
                return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


def build_report(
    received: ReportRequest,
    *,
    recall_directory: Path,
    static_asset_version: str,
    now: datetime,
    commit: str | None,
    corridors: FileCorridorStore,
    plans: FilePlanStore,
) -> ProblemReport:
    corridor, keyed = reported_corridor(received.request)
    cause = received.shown.get("cause") if received.outcome == "refusal" else None
    return ProblemReport(
        report_id=uuid4().hex[:12],
        received_at=now,
        corridor=corridor,
        outcome=received.outcome,
        cause=cause if isinstance(cause, str) else None,
        shown=received.shown,
        stages=list(received.stages),
        message=(received.message or "").strip() or None,
        recall_log=copy_of_recall_log(recall_directory, keyed),
        build=ReportBuild(commit=commit, static_asset_version=static_asset_version),
        evicted=evict_reported_research(keyed, corridors, plans),
    )


class FileReportStore:
    """One JSON file per report, named so a directory listing is already newest-last."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def store(self, report: ProblemReport) -> Path:
        stamp = report.received_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self.directory / f"{stamp}-{report.report_id}.json"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            raise ReportStoreError("The report could not be saved") from exc
        return path

    def load_all(self) -> list[ProblemReport]:
        """Every readable report, newest first. An unreadable file is skipped, never fatal."""

        if not self.directory.exists():
            return []
        reports: list[ProblemReport] = []
        for path in sorted(self.directory.glob("*.json"), reverse=True):
            try:
                reports.append(ProblemReport.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return reports
