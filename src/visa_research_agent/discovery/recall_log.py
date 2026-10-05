"""Recording what a corridor considered, so a miss can be told apart from a mis-ranking.

Every recall failure this project has found was invisible in the same way. A corridor refuses; the
refusal is accurate about what the decider was shown; and nothing in the output says whether the
page that would have answered it was **ranked out** or **never found**. Those have different fixes —
one is scoring, one is search or crawl — and on 2026-08-21 `canada/GB/GB/tourism` refused without
anybody being able to say which it was, on a page the same corridor had retrieved two days earlier.

So this writes the run down. One record per corridor, overwritten by the newest run, holding every
candidate the corridor considered with its score and whether it was shortlisted and fetched. It is
a diagnostic, not evidence: nothing reads it back, no decision depends on it, and losing it costs a
question rather than an answer.

Deliberately separate from `corridor_store.py`, which keeps *resolved* corridors for a week and
never sees a refusal. A refusal is exactly the run worth reading here.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Protocol

from pydantic import Field, field_validator

from visa_research_agent.discovery.models import (
    CandidatePage,
    Corridor,
    DiscoveryRole,
    RefusalCause,
)
from visa_research_agent.domain.models import FailureOutcome, StrictModel


class ConsideredCandidate(StrictModel):
    """One page the corridor knew about, and how far it got."""

    url: str = Field(min_length=1)
    title: str = ""
    found_by: Literal["search", "crawl", "corpus"] = "crawl"
    depth: int = Field(ge=0)
    discovered_from: str = ""
    best_role: str = "irrelevant"
    best_score: float = 0.0
    scores: dict[str, float] = Field(default_factory=dict)
    shortlisted: bool = False
    fetched: bool = False
    """Shortlisted *and* readable. A shortlisted page that could not be read is the third answer to
    "why was this page not used", and it is invisible unless the two are recorded apart."""
    admitted_on_text: bool = False
    """Shown to the model selector on the strength of its stored text, the link to it having scored
    nothing for any role (entry 158).

    Until then a `best_score` of 0.0 meant the selector never saw the page; on a row with this set
    it did. False on every log written before the field existed, which is true of them — nothing was
    admitted that way."""
    withheld_from_selection: bool = False
    """In the pool, and **not shown** to the model selector, because the pool was cut to the ranked
    top plus pages with no stored text (entry 195). Tells "never offered" from "offered and not
    picked", which the recall log could not say once a pool member could be withheld. False on every
    log written before the field existed, which is true of them — nothing was withheld then."""


class RoleVerdict(StrictModel):
    """What the role adjudicator said about one role, and why, in its own words (TODO item 70).

    Rule 10 of `adjudicate_roles.txt` makes the model give a reason for every choice and say what
    was missing for a role it leaves null, and until 2026-09-24 nothing kept a null's reason:
    `validated_choices` skips it, and only a *kept* choice's reason reaches the notes. So a corridor
    that read its authority's pages and credited no decision could only be diagnosed by guessing
    what the model saw. This is the model's answer **as it gave it**, before validation — an
    invented id is kept with `url` left empty, so "named a page we never fetched" stays visible.

    A diagnostic like the rest of this record: nothing reads it back and nothing decides from it.
    """

    role: DiscoveryRole
    kind: Literal["choice", "tool", "delegate"] = "choice"
    """`choice` fills the role; `tool` and `delegate` name a next step that fills nothing."""
    source_id: str | None = None
    """The id the model named, or `None` where it named nothing — a refusal of the role."""
    url: str | None = None
    """The page that id stood for in this run's packet. `None` for a refusal, and for an id that
    was not in the packet, which the kept `source_id` tells apart."""
    reason: str = ""


class ModelCall(StrictModel):
    """One call to a model, with the input size that is the only handle on why it took as long.

    **Added 2026-09-07 because a 4× spread had no explanation** (entry 143): `japan/IN/GB` spent
    22.9s adjudicating where `australia/BD/AE` spent 5.4s for the same two calls, and nothing
    recorded what either was given. Characters rather than tokens: tokens are the provider's unit
    and are not knowable here without asking it, and the question is which *input* grows, for which
    characters are an honest proxy this program can compute.

    Prompt and packet are kept apart because only one of them varies with the corridor — the system
    prompt is loaded from a file and is the same every run, so a total would hide the thing being
    measured behind a constant.
    """

    call: Literal["select", "roles", "blocked", "plan"]
    """`plan` never appears in a `RecallRecord`. The plan is written on every web request — after a
    resolution, or with none because the corridor came from the store — so it is not part of any
    run this record describes. Every call, `plan` included, is also appended to the daily log in
    `research/model_usage.py` (entries 165 and 166)."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    """What the call wrote to the provider's prompt cache, which OpenAI bills at 1.25× the input
    rate on GPT-5.6 and later (entry 164). Counted inside `input_tokens`, as `cached_input_tokens`
    is: on 24 live calls the input was always cached plus written plus 3 (entry 167)."""
    reasoning_output_tokens: int | None = None
    """What the provider said it billed, or `None` where it did not say.

    **Tokens rather than the characters beside them, because only tokens are what is charged**, and
    the two do not stand in for each other: this model reasons, and reasoning tokens are billed as
    output while appearing nowhere in the input. `None` is kept distinct from `0` — a call whose
    provider reported nothing is not a free call, and averaging it as one would understate a
    corridor."""
    prompt_characters: int = Field(ge=0)
    packet_characters: int = Field(ge=0)
    seconds: float = Field(ge=0)
    failed: bool = False
    """A call that raised. Timed and kept, because a slow failure is a cost like any other and a
    retry loop that hides them would under-report the runs worth reading."""
    http_requests: int | None = None
    """How many HTTP requests the provider's client sent for this call; above one means it retried
    (entry 166). `None` where nothing counted, which includes every log written before the field
    existed. Usage is recorded from the attempt that answered, and whether an attempt that failed
    was billed depends on how it failed, which is not known here."""


class RecallRecord(StrictModel):
    """Everything one resolution considered, in the order a reader asks about it."""

    schema_version: Literal[1] = 1
    corridor_key: str = Field(min_length=1)
    recorded_at: datetime
    outcome: str = Field(min_length=1)
    """"resolved", or the reason it refused. So a reader knows which run they are looking at."""

    cause: RefusalCause | None = None
    """The same thing as a value, so a set of runs can be counted rather than read.

    `None` means *this log predates the field*, and it is deliberately not a default that guesses.
    The 27 logs from the twenty-corridor measurement are all `None`, and they cannot be repaired by
    reading `outcome`: "resolved, with no visa_decision" is written both by a corridor that refused
    and by one that resolved on a questionnaire, and nothing else in the record separates them. An
    audit reports those as unrecorded rather than bucketing them, because inferring the cause from
    the sentence is the habit that produced two wrong entries in `CLAUDE.md`'s corrections table.
    """

    selector: Literal["model", "heuristic"] | None = None
    """Which selector chose what to read, recorded because nothing else in the file can say.

    `None` means *this log predates the field*, exactly as it does for `cause`, and it is graded as
    unattributable rather than assumed. `selection_recall.arms_from_logs` reads a run's fetched URLs
    as "the model's picks", so a heuristic run graded that way puts the heuristic into the arm
    labelled model and compares it with itself. That went unnoticed while the only oracle corridors
    with logs happened to be model runs; the second traveller added in entry 91 brought in six logs
    from before entry 85 turned the model selector on, and the mixed number came out six points
    lower without anything saying why.
    """

    unresolved_roles: list[DiscoveryRole] = Field(default_factory=list)
    """Which reported roles went unfilled, as values rather than inside the `outcome` sentence.

    Separate from `cause` because they answer different questions and only one of them refuses a
    corridor: a run missing only its `document_checklist` is `resolved` (entry 14) and still worth
    counting, since it is the number entry 58's second bar was measured against.
    """

    queries: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)
    candidates: list[ConsideredCandidate] = Field(default_factory=list)
    unreadable: dict[str, str] = Field(default_factory=dict)
    """Per URL, not per host. The notes on a resolved corridor collapse these to one line per host,
    which answers "was this site readable" but not "was this page".

    **Both stages, since 2026-08-24.** This was filled from the crawl's failures alone, which was
    complete only while the crawl ran. It stopped crawling (entry 51) and the field silently went
    empty: all 27 logs from the twenty-corridor measurement record nothing unreadable, on runs whose
    `ResolvedCorridor` named three authorities that refused us. The shortlist fetch is where a
    refusal is met now, and it is merged in here.
    """

    phase_seconds: dict[str, float] = Field(default_factory=dict)
    """Where this run's seconds went — `search`, `corpus`, `crawl`, `select`, `fetch`, `adjudicate`.

    **Empty means the log predates the field**, exactly as `None` does for `cause` and `selector`,
    and it is graded as unrecorded rather than as a fast run. Added 2026-09-07 because the project's
    goal has been stated in seconds since entry 44 and nothing in the program recorded one: entry
    140 had to time `search_all` by hand from outside to find it was 19.0s of a 27.4s corridor, and
    the standing belief that adjudication dominated turned out to date from a two-domain destination
    and to have been false ever since the five-domain cap tripled the query count.

    **A phase is a stage of `_resolve`, not the act it is named after**, and the difference shows on
    the first run that used this: `australia/BD/AE` recorded `crawl 2.0s` on a run whose own notes
    say *"the crawl was skipped"*. The 2.0s is the span between step 3 and step 4 — deciding the
    corpus out-covers a crawl, and merging and scoring what that leaves. True and easy to misread,
    so it is said here rather than left for someone to rediscover.

    A diagnostic like the rest of this record: nothing reads it back, and a phase missing from the
    map is one this run never reached."""

    model_calls: list[ModelCall] = Field(default_factory=list)
    """Every model call this run made, in order, with what it was given and what it cost.

    Empty means the log predates the field, exactly as it does for `phase_seconds` — not a run that
    made no calls, which is a real state a corridor reaches when it refuses before selection."""

    unreadable_outcomes: dict[str, FailureOutcome] = Field(default_factory=dict)
    """Per URL, why it could not be read, as the typed outcome rather than the sentence.

    `unreadable` carries the detail a person reads; this carries the thing a count may be taken
    from. Keeping them apart is DECISIONS entry 36's rule — a `Disallow` and a `403` license
    different statements, and deciding which one happened by matching words in `detail` would make
    rewording a message silently change what an audit reports.
    """

    role_verdicts: list[RoleVerdict] = Field(default_factory=list)
    """Every role the adjudicator answered on the call that counted, with its reason.

    Empty means no role adjudication answered — the log predates the field, no adjudicator was
    configured, the run refused before it, or both attempts failed. Only the call that answered is
    kept: a failed first attempt produced no verdict to keep."""

    adjudicated_ids: dict[str, str] = Field(default_factory=dict)
    """Each source id in the adjudication packet and the page it stood for, because a reason names
    pages by id ("tl_7 lists nationalities…") and the id means nothing once the run is over."""

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value

    @property
    def shortlisted(self) -> list[ConsideredCandidate]:
        return [candidate for candidate in self.candidates if candidate.shortlisted]

    def find(self, fragment: str) -> list[ConsideredCandidate]:
        """Every candidate whose URL contains `fragment` — the question this file exists to answer.

        An empty list means the corridor never saw the page, which is a different problem from a
        page it saw and ranked out.
        """

        return [candidate for candidate in self.candidates if fragment in candidate.url]


def considered(
    candidates: dict[str, CandidatePage],
    *,
    shortlisted: set[str],
    fetched: set[str],
    admitted_on_text: set[str] | frozenset[str] = frozenset(),
    withheld_from_selection: set[str] | frozenset[str] = frozenset(),
) -> list[ConsideredCandidate]:
    """Flatten the candidate set, best-scoring first, which is the order it was cut in."""

    rows = [
        ConsideredCandidate(
            url=url,
            title=candidate.title or candidate.link.text or "",
            found_by=candidate.found_by,
            depth=candidate.link.depth,
            discovered_from=candidate.link.discovered_from,
            best_role=candidate.link_scores.best()[0],
            best_score=candidate.link_scores.best()[1],
            scores={role: score for role, score in candidate.link_scores.scores.items() if score},
            shortlisted=url in shortlisted,
            fetched=url in fetched,
            admitted_on_text=url in admitted_on_text,
            withheld_from_selection=url in withheld_from_selection,
        )
        for url, candidate in candidates.items()
    ]
    return sorted(rows, key=lambda row: (-row.best_score, row.url))


class RecallLog(Protocol):
    def write(self, record: RecallRecord) -> None:
        """Keep this run's record, replacing any earlier one for the same corridor."""
        ...


class FileRecallLog:
    """One JSON document per corridor, written atomically, overwritten by the newest run.

    Overwritten rather than accumulated because the question is almost always about the run that
    just happened. Comparing two runs — which is how the Canada variance was found — means keeping
    a copy of the file, and that is a deliberate act rather than a directory that grows forever.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, corridor: Corridor) -> Path:
        return self.directory / f"{sha256(corridor.key.encode()).hexdigest()}.json"

    def write(self, record: RecallRecord) -> None:
        path = self.directory / f"{sha256(record.corridor_key.encode()).hexdigest()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(record.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        temporary.replace(path)

    def read(self, corridor: Corridor) -> RecallRecord | None:
        """Read a record back, for a person or a script asking about the last run."""

        try:
            raw = self.path_for(corridor).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return RecallRecord.model_validate_json(raw)


class CandidateVariance(StrictModel):
    """One page that some runs of a corridor saw and others did not."""

    url: str
    title: str = ""
    runs_seen: list[int] = Field(default_factory=list)
    runs_shortlisted: list[int] = Field(default_factory=list)
    runs_fetched: list[int] = Field(default_factory=list)
    best_score: float = 0.0

    @property
    def reached_the_model(self) -> bool:
        """True when at least one run got this page as far as being readable evidence.

        The line that matters. A page that flickers in and out of the *candidate* set but never
        reaches a shortlist place changes nothing; a page that was fetched in one run and absent in
        another is a page that could have changed the answer.
        """

        return bool(self.runs_fetched)


class VarianceReport(StrictModel):
    """What several runs of one corridor did and did not agree about.

    The point of counting is in `unstable`: TODO item 17 asks for the flip *rate* rather than the
    anecdote, and a rate over outcomes alone would not say which page moved. Two runs that both
    resolve are not necessarily agreeing — they can resolve on different pages — so outcomes and
    candidates are both reported.
    """

    runs: int
    """Runs actually performed — **not** the number of records read.

    Kept apart from `records_read` because the two came out different the first time this was
    written: the count was taken from the recall records, so a run whose record could not be read
    simply vanished from the report and two runs described themselves as one. A diagnostic that
    quietly understates how much evidence it had is the failure mode this whole file exists to
    prevent.
    """

    records_read: int = 0
    """How many runs left a readable recall record. Below `runs`, the candidate comparison is
    incomplete and the report says so rather than presenting a partial comparison as a whole one."""

    outcomes: list[str] = Field(default_factory=list)
    stable: int = 0
    """Candidates every run *that left a record* saw."""

    unstable: list[CandidateVariance] = Field(default_factory=list)
    """Candidates only some runs saw, worst first. Ordered by how far they got, because a page one
    run *fetched* and another never saw is the case that decides corridors."""

    @property
    def resolved_runs(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome == "resolved")

    @property
    def comparison_is_complete(self) -> bool:
        """True when every run left a record, so an absence really means the run did not see it."""

        return self.records_read == self.runs

    @property
    def flipped(self) -> bool:
        """True when the runs did not all reach the same outcome."""

        return len(set(self.outcomes)) > 1


def compare_runs(outcomes: Sequence[str], records: Sequence[RecallRecord]) -> VarianceReport:
    """Summarise several runs of the same corridor, in the order they were run.

    **Outcomes and records are passed separately, and that is the point.** An outcome is known from
    the resolution itself and always exists; a record is a file that may be missing, stale, or
    unwritable — entry 43 deliberately lets a recall-log write fail silently rather than cost a
    corridor its answer. Deriving the run count from the records would therefore make a failed
    *write* look like a run that never happened.

    Runs are numbered from 1. A candidate is keyed by URL, because that is what a later run would
    have to find again.
    """

    by_url: dict[str, CandidateVariance] = {}
    for index, record in enumerate(records, start=1):
        for candidate in record.candidates:
            entry = by_url.get(candidate.url)
            if entry is None:
                entry = CandidateVariance(url=candidate.url, title=candidate.title)
                by_url[candidate.url] = entry
            entry.runs_seen.append(index)
            if candidate.shortlisted:
                entry.runs_shortlisted.append(index)
            if candidate.fetched:
                entry.runs_fetched.append(index)
            entry.best_score = max(entry.best_score, candidate.best_score)
            if not entry.title and candidate.title:
                entry.title = candidate.title

    total = len(records)
    stable = [item for item in by_url.values() if len(item.runs_seen) == total]
    unstable = [item for item in by_url.values() if len(item.runs_seen) != total]
    # Fetched-somewhere first, then shortlisted, then by score: the ordering is the reading order a
    # person wants, which is "what could this have cost me" rather than alphabetical.
    unstable.sort(
        key=lambda item: (
            not item.runs_fetched,
            not item.runs_shortlisted,
            -item.best_score,
            item.url,
        )
    )
    return VarianceReport(
        runs=len(outcomes),
        records_read=total,
        outcomes=list(outcomes),
        stable=len(stable),
        unstable=unstable,
    )
