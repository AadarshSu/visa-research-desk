"""Writing down what a corridor considered, so a miss can be told from a mis-ranking.

Every test here is offline. The point of the file under test is a question asked *after* a run is
over — "was that page ranked out, or never found?" — so these run a whole corridor against the fake
site and then ask the record.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import httpx2
import pytest
from discovery_site import (
    AUTHORITY,
    DETAIL_INDIA,
    INDEX,
    MISSION_INDEX,
    OFF_DOMAIN,
    destination,
    handler,
)
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from visa_research_agent.discovery.adjudication import (
    AdjudicationError,
    LangChainRoleAdjudicator,
    UsageRecorder,
)
from visa_research_agent.discovery.crawl import CrawlFetcher
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.page_text import PageTextStore, StoredPage
from visa_research_agent.discovery.recall_log import (
    ConsideredCandidate,
    FileRecallLog,
    ModelCall,
    RecallRecord,
    RoleVerdict,
    compare_runs,
)
from visa_research_agent.discovery.resolver import CorridorResolver, ResolutionTrace
from visa_research_agent.discovery.selection import (
    LangChainCandidateSelector,
    Selection,
    SelectionQuotaExhausted,
)
from visa_research_agent.domain.models import DestinationConfig
from visa_research_agent.research.errors import LLMExtractionError
from visa_research_agent.research.live_sources import LiveSourceFetcher
from visa_research_agent.research.model_usage import FileModelUsageLog
from visa_research_agent.research.openai_extraction import LangChainStructuredPlanGenerator
from visa_research_agent.research.source_cache import FileSourceCache

pytestmark = pytest.mark.anyio

RESOLVED_AT = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)


def corridor() -> Corridor:
    return Corridor(
        destination_slug="testland",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )


class StubSearchProvider:
    def __init__(self, urls: list[str]) -> None:
        self.urls = urls

    async def search(self, query: str, *, count: int) -> list[object]:
        from visa_research_agent.discovery.models import SearchResult

        return [
            SearchResult(url=url, title="", snippet="", query=query, rank=rank)
            for rank, url in enumerate(self.urls[:count])
        ]


class RecordingLog:
    """Keeps every record in memory, so a test can see the run that was overwritten."""

    def __init__(self) -> None:
        self.records: list[RecallRecord] = []

    def write(self, record: RecallRecord) -> None:
        self.records.append(record)


class UnwritableLog:
    def write(self, record: RecallRecord) -> None:
        raise OSError("the disk is full")


async def sleep_none(_: float) -> None:
    return None


def build_resolver(
    tmp_path: Path,
    search_urls: list[str],
    recall_log: object | None,
) -> CorridorResolver:
    transport = httpx.MockTransport(handler([]))  # type: ignore[arg-type]
    return CorridorResolver(
        StubSearchProvider(search_urls),  # type: ignore[arg-type]
        CrawlFetcher(transport=transport, sleep=sleep_none, host_delay_seconds=0.0),
        LiveSourceFetcher(
            FileSourceCache(tmp_path / "cache"),
            ttl_hours=24.0,
            maximum_stale_hours=168.0,
            timeout_seconds=5.0,
            concurrency=2,
            maximum_characters=50_000,
            minimum_characters=40,
            user_agent="test-agent",
            transport=transport,
            now=lambda: RESOLVED_AT,
        ),
        minimum_role_score=10.0,
        recall_log=recall_log,  # type: ignore[arg-type]
        now=lambda: RESOLVED_AT,
    )


async def test_a_resolved_run_records_every_candidate_with_its_score(tmp_path: Path) -> None:
    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved.is_usable
    record = log.records[-1]
    assert record.corridor_key == "testland/IN/GB/tourism"
    assert record.outcome == "resolved"
    assert record.queries, "the queries are half of why a page was never found"
    assert len(record.candidates) > len(record.shortlisted)
    assert all(row.best_score >= 0 for row in record.candidates)
    assert [row.best_score for row in record.candidates] == sorted(
        (row.best_score for row in record.candidates), reverse=True
    ), "recorded in the order the shortlist was cut in"


async def test_the_record_answers_which_kind_of_miss_it_was(tmp_path: Path) -> None:
    """The whole reason the file exists: never seen and seen-but-not-used must look different."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)

    await resolver.resolve(destination(), corridor())
    record = log.records[-1]

    seen = record.find("/detail/india.html")
    assert seen, "this page is on the fake site and must be recorded as considered"
    assert seen[0].url == DETAIL_INDIA
    assert record.find("entry-requirements-country") == [], (
        "a page nobody ever discovered must come back empty, not with a zero score"
    )


async def test_a_refused_run_is_recorded_too(tmp_path: Path) -> None:
    """The runs worth reading are the ones that failed, so a refusal must not skip the log."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [OFF_DOMAIN], log)

    resolved = await resolver.resolve(destination(), corridor())

    assert not resolved.is_usable
    record = log.records[-1]
    assert record.outcome != "resolved"
    assert record.outcome in resolved.notes


async def test_shortlisted_and_fetched_are_recorded_apart(tmp_path: Path) -> None:
    """A shortlisted page that could not be read is a third answer, lost if the two are merged."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)

    await resolver.resolve(destination(), corridor())
    record = log.records[-1]

    assert all(row.shortlisted for row in record.candidates if row.fetched)
    assert any(not row.shortlisted for row in record.candidates)


async def test_a_log_that_cannot_be_written_never_costs_the_corridor_its_answer(
    tmp_path: Path,
) -> None:
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], UnwritableLog())

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved.is_usable


async def test_without_a_log_a_corridor_behaves_exactly_as_before(tmp_path: Path) -> None:
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], None)

    resolved = await resolver.resolve(destination(), corridor())

    assert resolved.is_usable


async def test_the_file_log_round_trips_and_keeps_only_the_newest_run(tmp_path: Path) -> None:
    log = FileRecallLog(tmp_path / "recall")
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)

    await resolver.resolve(destination(), corridor())
    first = log.read(corridor())
    await resolver.resolve(destination(), corridor())
    second = log.read(corridor())

    assert first is not None and second is not None
    assert first.corridor_key == second.corridor_key
    assert len(list((tmp_path / "recall").glob("*.json"))) == 1


def test_a_record_read_back_from_disk_is_the_one_written(tmp_path: Path) -> None:
    log = FileRecallLog(tmp_path / "recall")
    record = RecallRecord(
        corridor_key="canada/GB/GB/tourism",
        recorded_at=RESOLVED_AT,
        outcome="no candidate shows the result for a GB passport holder",
        queries=["canada visa british citizen"],
    )

    log.write(record)

    assert (
        log.read(Corridor(destination_slug="canada", passport_nationality="GB", applying_from="GB"))
        == record
    )


def record(outcome: str, candidates: list[tuple[str, bool, bool]]) -> RecallRecord:
    """One run's record, written as (url, shortlisted, fetched) so a case reads in one line."""

    return RecallRecord(
        corridor_key="canada/GB/GB/tourism",
        recorded_at=datetime(2026, 8, 22, 9, 0, tzinfo=UTC),
        outcome=outcome,
        candidates=[
            ConsideredCandidate(
                url=url,
                depth=0,
                best_score=50.0,
                shortlisted=shortlisted,
                fetched=fetched,
            )
            for url, shortlisted, fetched in candidates
        ],
    )


def test_a_page_one_run_read_and_another_never_saw_is_reported_first() -> None:
    """The Canada case: same corridor, same code, and one run simply never got the page.

    Ordering is the assertion, not a nicety. A person reading this is asking "what could that have
    cost me", so a candidate that reached the decider in one run and was absent in another has to
    come above the noise of pages that flickered without ever being read.
    """

    answering = "https://www.canada.ca/entry-requirements-country.html"
    noise = "https://www.canada.ca/newsroom.html"
    report = compare_runs(
        ["resolved", "refused"],
        [
            record("resolved", [(answering, True, True), (noise, False, False)]),
            record("refused", []),
        ],
    )

    assert report.flipped
    assert report.resolved_runs == 1
    assert [item.url for item in report.unstable][0] == answering
    assert report.unstable[0].reached_the_model
    assert report.unstable[0].runs_fetched == [1]


def test_runs_that_agree_are_not_reported_as_variance() -> None:
    same = [("https://www.canada.ca/a.html", True, True)]
    report = compare_runs(["resolved", "resolved"], [record("resolved", same)] * 2)

    assert not report.flipped
    assert report.stable == 1
    assert report.unstable == []


def test_the_run_count_comes_from_the_runs_not_from_the_records() -> None:
    """A recall-log write may fail silently (entry 43), and that must not erase a run.

    Written because the first version of this counted `len(records)`, so two runs where one failed
    to leave a record described themselves as one run — a diagnostic quietly understating how much
    evidence it had, which is the exact failure this file exists to prevent.
    """

    report = compare_runs(
        ["resolved", "refused"],
        [record("resolved", [("https://www.canada.ca/a.html", True, True)])],
    )

    assert report.runs == 2
    assert report.records_read == 1
    assert not report.comparison_is_complete
    assert report.flipped, "the outcomes still differ even though only one record survived"


# --- which selector actually chose, which is not which one was configured -----------------------


class FailingSelector:
    """A configured model selector whose call cannot succeed.

    This is the shape of what happened on 2026-08-28: the OpenAI account ran out of credit part-way
    through the twenty oracle corridors, so `SelectionQuotaExhausted` came back for the last seven
    and the heuristic ranking chose instead — honestly, and by design (entry 83).
    """

    def __init__(self) -> None:
        self.calls = 0

    async def select(self, system_prompt: str, packet: str, *, usage: object = None) -> Selection:
        self.calls += 1
        raise SelectionQuotaExhausted("The OpenAI account is out of credit")


class PickingSelector:
    """A selector that names the first candidate it is offered, by reading the packet's ids."""

    def __init__(self) -> None:
        self.calls = 0

    async def select(self, system_prompt: str, packet: str, *, usage: object = None) -> Selection:
        self.calls += 1
        entries = json.loads(packet)["candidates"]
        return Selection(source_ids=[entries[0]["source_id"]])


def indexed_destination() -> DestinationConfig:
    """The fake site under a real country's display name, so a page-text index can be found.

    `_destination_code` matches the display name against the country registry, so the ordinary
    "testland" fixture can never reach the selector at all — its index is looked up under a code
    that does not exist.
    """

    return destination().model_copy(update={"display_name": "Japan"})


def text_store(tmp_path: Path, urls: list[str]) -> PageTextStore:
    store = PageTextStore(tmp_path / "pagetext")
    store.write(
        "JP",
        [
            StoredPage(
                url=url,
                fetched_at=RESOLVED_AT,
                body="Visa requirements for Indian nationals applying from the United Kingdom. "
                * 20,
            )
            for url in urls
        ],
    )
    return store


async def test_a_configured_selector_that_never_chose_is_not_recorded_as_the_model(
    tmp_path: Path,
) -> None:
    """The defect, as one assertion.

    `selector` was derived at the write as `"model" if self.selector is not None else "heuristic"`,
    which records the *configuration*. A destination whose text index holds nothing never reaches
    the model at all — the heuristic ranks, exactly as it would with no selector configured — and
    calling that a model run puts the heuristic's own picks inside the model's arm when
    `selection-recall` replays them. Entries 91 and 97.
    """

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)
    selector = FailingSelector()
    resolver.selector = selector

    await resolver.resolve(destination(), corridor())

    assert log.records, "a run must be recorded"
    assert log.records[-1].selector == "heuristic"
    assert selector.calls == 0, "no index, so the model was never even asked"


async def test_a_failed_selection_refuses_the_corridor(tmp_path: Path) -> None:
    """Entry 258: the model was asked and could not answer, so nothing is read and nothing resolves.

    Until then the heuristic ranking chose instead, and on Australia `IN/SG` it chose another
    country's post and the result was stored. A failed call now ends the run as a failed check."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)
    selector = FailingSelector()
    resolver.selector = selector
    resolver.page_text = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])

    resolved = await resolver.resolve(indexed_destination(), corridor())

    assert selector.calls == 1, "the model was asked"
    assert not resolved.sources, "and nothing was read in its place"
    assert not resolved.is_usable
    assert resolver.trace.refusal_cause == "adjudication_failed"
    assert any("candidate selection failed" in note for note in resolved.notes)


class EmptySelector:
    """A selector that answers and names no page at all."""

    def __init__(self) -> None:
        self.calls = 0

    async def select(self, system_prompt: str, packet: str, *, usage: object = None) -> Selection:
        self.calls += 1
        return Selection(source_ids=[])


async def test_a_selection_that_named_no_page_refuses_too(tmp_path: Path) -> None:
    """Entry 258: an answer that chose nothing is not handed to the heuristic either."""

    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], RecordingLog())
    resolver.selector = EmptySelector()
    resolver.page_text = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])

    resolved = await resolver.resolve(indexed_destination(), corridor())

    assert not resolved.sources
    assert resolver.trace.refusal_cause == "no_candidates"


async def test_a_selection_that_chose_is_recorded_as_the_model(tmp_path: Path) -> None:
    """The positive control, so the fix is not just 'always heuristic'."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], log)
    selector = PickingSelector()
    resolver.selector = selector
    resolver.page_text = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])

    await resolver.resolve(indexed_destination(), corridor())

    assert selector.calls == 1
    assert log.records[-1].selector == "model"


class OfferRecordingSelector:
    """Names the first candidate offered, and keeps the addresses of every page it was shown."""

    def __init__(self) -> None:
        self.offered: list[set[str]] = []

    async def select(self, system_prompt: str, packet: str, *, usage: object = None) -> Selection:
        entries = json.loads(packet)["candidates"]
        self.offered.append({entry["url"] for entry in entries})
        return Selection(source_ids=[entries[0]["source_id"]])


ZERO_LABEL_CHECKLIST = f"https://{AUTHORITY}/notice/a1.html"
ZERO_LABEL_NOTICE = f"https://{AUTHORITY}/notice/a2.html"


async def test_a_page_its_link_scores_zero_for_is_shown_to_the_selector_on_its_stored_text(
    tmp_path: Path,
) -> None:
    """TODO item 31's defect and its fix, through a whole corridor (entries 123, 127, 158).

    Neither address says anything a link scorer can use, so both score zero for every role, and the
    pool used to exclude both however good the page. The first one's stored text is a checklist;
    the second's is office hours. Only the first is shown — admission reads what a page says, it
    does not open the gate to everything — and the recall log says why a zero-scoring page was,
    because until now a `best_score` of 0.0 meant the selector never saw it.
    """

    log = RecordingLog()
    resolver = build_resolver(
        tmp_path, [INDEX, MISSION_INDEX, ZERO_LABEL_CHECKLIST, ZERO_LABEL_NOTICE], log
    )
    selector = OfferRecordingSelector()
    resolver.selector = selector
    store = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])
    store.write(
        "JP",
        [
            StoredPage(
                url=ZERO_LABEL_CHECKLIST,
                fetched_at=RESOLVED_AT,
                body="Checklist of documents required from every applicant: a passport, a bank "
                "statement, proof of accommodation and a return ticket. " * 5,
            ),
            StoredPage(
                url=ZERO_LABEL_NOTICE,
                fetched_at=RESOLVED_AT,
                body="The office is open from nine until five on weekdays. " * 10,
            ),
        ],
    )
    resolver.page_text = store

    resolved = await resolver.resolve(indexed_destination(), corridor())

    assert ZERO_LABEL_CHECKLIST in selector.offered[0]
    assert ZERO_LABEL_NOTICE not in selector.offered[0]
    rows = {row.url: row for row in log.records[-1].candidates}
    assert rows[ZERO_LABEL_CHECKLIST].best_score == 0.0
    assert rows[ZERO_LABEL_CHECKLIST].admitted_on_text
    assert not rows[ZERO_LABEL_NOTICE].admitted_on_text
    assert any("on their stored text" in note for note in resolved.notes)


# --- Where a corridor's seconds went (DECISIONS entry 142) -----------------------------------


def test_a_phase_records_what_it_spent_and_the_clock_is_injected() -> None:
    """Asserted without spending the seconds, which is why the clock is a seam."""

    ticks = iter([0.0, 2.5, 2.5, 9.0])
    trace = ResolutionTrace(clock=lambda: next(ticks))

    trace.begin("search")
    trace.begin("fetch")
    trace.end()

    assert trace.phase_seconds == {"search": 2.5, "fetch": 6.5}


def test_a_phase_entered_twice_accumulates() -> None:
    """A second visit replacing the first would under-report the slow runs worth reading."""

    ticks = iter([0.0, 1.0, 10.0, 13.0])
    trace = ResolutionTrace(clock=lambda: next(ticks))

    trace.begin("fetch")
    trace.end()
    trace.begin("fetch")
    trace.end()

    assert trace.phase_seconds == {"fetch": 4.0}


def test_closing_twice_is_safe_and_records_once() -> None:
    """`end` runs in a `finally` on every exit path, including ones that already closed."""

    ticks = iter([0.0, 3.0])
    trace = ResolutionTrace(clock=lambda: next(ticks))

    trace.begin("adjudicate")
    trace.end()
    trace.end()

    assert trace.phase_seconds == {"adjudicate": 3.0}


def test_each_phase_is_announced_as_it_starts() -> None:
    """What a waiting traveller is shown (TODO item 57): the phase's name, in order."""

    heard: list[str] = []
    trace = ResolutionTrace(clock=lambda: 0.0, on_phase=heard.append)

    trace.begin("search")
    trace.begin("select")
    trace.end()

    assert heard == ["search", "select"]


def test_a_run_that_never_started_a_phase_records_none() -> None:
    """An empty map means unrecorded, never a run that spent nothing — the field is graded that
    way because every log written before 2026-09-07 has one."""

    trace = ResolutionTrace(clock=lambda: 0.0)
    trace.end()

    assert trace.phase_seconds == {}


def test_the_record_carries_the_phases_and_an_older_log_reads_as_unrecorded(
    tmp_path: Path,
) -> None:
    """The field is optional, so a log written before it existed still loads."""

    log = FileRecallLog(tmp_path)
    corridor = Corridor(
        destination_slug="australia",
        passport_nationality="BD",
        applying_from="AE",
        purpose="tourism",
    )
    log.write(
        RecallRecord(
            corridor_key=corridor.key,
            recorded_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            outcome="resolved",
            phase_seconds={"search": 2.6, "fetch": 18.4},
        )
    )

    read = log.read(corridor)
    assert read is not None
    assert read.phase_seconds == {"search": 2.6, "fetch": 18.4}
    assert (
        RecallRecord(
            corridor_key=corridor.key,
            recorded_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            outcome="resolved",
        ).phase_seconds
        == {}
    )


# --- What a model call was given, and what it cost (DECISIONS entry 144) ---------------------


@pytest.mark.anyio
async def test_a_model_call_records_its_input_size_and_what_it_cost(tmp_path: Path) -> None:
    """The 4× adjudication spread of entry 143 had no handle because nothing recorded the input."""

    clock = [0.0]
    resolver = CorridorResolver(
        StubSearchProvider([]),  # type: ignore[arg-type]
        CrawlFetcher(host_delay_seconds=0.0),
        LiveSourceFetcher(
            FileSourceCache(tmp_path),
            ttl_hours=24.0,
            maximum_stale_hours=168.0,
            timeout_seconds=5.0,
            concurrency=2,
            maximum_characters=20_000,
            minimum_characters=10,
            user_agent="VisaResearchAgent/test",
        ),
        monotonic=lambda: clock[0],
    )

    async with resolver._timed_model_call("select", "PROMPT", "PACKET-LONGER"):
        clock[0] += 4.0

    assert resolver.model_call_timings == [
        ModelCall(
            call="select",
            prompt_characters=6,
            packet_characters=13,
            seconds=4.0,
            failed=False,
        )
    ]


@pytest.mark.anyio
async def test_a_failed_model_call_is_timed_and_kept(tmp_path: Path) -> None:
    """A slow failure costs the corridor exactly as much as a slow success, and a retry loop that
    dropped them would under-report the runs worth reading."""

    clock = [0.0]
    resolver = CorridorResolver(
        StubSearchProvider([]),  # type: ignore[arg-type]
        CrawlFetcher(host_delay_seconds=0.0),
        LiveSourceFetcher(
            FileSourceCache(tmp_path),
            ttl_hours=24.0,
            maximum_stale_hours=168.0,
            timeout_seconds=5.0,
            concurrency=2,
            maximum_characters=20_000,
            minimum_characters=10,
            user_agent="VisaResearchAgent/test",
        ),
        monotonic=lambda: clock[0],
    )

    with pytest.raises(SelectionQuotaExhausted):
        async with resolver._timed_model_call("roles", "P", "K"):
            clock[0] += 9.0
            raise SelectionQuotaExhausted("no credit")

    assert len(resolver.model_call_timings) == 1
    assert resolver.model_call_timings[0].failed is True
    assert resolver.model_call_timings[0].seconds == 9.0


async def test_the_usage_recorder_reads_cache_writes_beside_cache_reads() -> None:
    """OpenAI bills a cache write at 1.25× on GPT-5.6 and later, and only reads were being kept
    (entry 164). LangChain reports the Responses API's `cache_write_tokens` as `cache_creation`."""

    recorder = UsageRecorder()
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 110_205,
            "output_tokens": 350,
            "total_tokens": 110_555,
            "input_token_details": {"cache_read": 2_029, "cache_creation": 108_000},
            "output_token_details": {"reasoning": 131},
        },
    )

    await recorder.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

    assert recorder.input_tokens == 110_205
    assert recorder.cached_input_tokens == 2_029
    assert recorder.cache_write_input_tokens == 108_000
    assert recorder.output_tokens == 350
    assert recorder.reasoning_output_tokens == 131


async def test_a_model_call_keeps_what_its_recorder_was_told(tmp_path: Path) -> None:
    resolver = CorridorResolver(
        StubSearchProvider([]),  # type: ignore[arg-type]
        CrawlFetcher(host_delay_seconds=0.0),
        LiveSourceFetcher(
            FileSourceCache(tmp_path),
            ttl_hours=24.0,
            maximum_stale_hours=168.0,
            timeout_seconds=5.0,
            concurrency=2,
            maximum_characters=20_000,
            minimum_characters=10,
            user_agent="VisaResearchAgent/test",
        ),
        monotonic=lambda: 0.0,
    )
    async with resolver._timed_model_call("select", "PROMPT", "PACKET") as usage:
        # What the provider's callback and the client's request hook fill in for a real call.
        usage.input_tokens = 54_667
        usage.cache_write_input_tokens = 52_000
        usage.http_requests = 3

    [call] = resolver.model_call_timings
    assert call.input_tokens == 54_667
    assert call.cache_write_input_tokens == 52_000
    assert call.http_requests == 3


async def test_every_model_call_a_run_makes_is_appended_to_the_daily_usage_log(
    tmp_path: Path,
) -> None:
    """The recall log keeps a run's calls, but the next run of the corridor overwrites them, so a
    day's spend could not be summed from it (entry 166)."""

    recall = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX], recall)
    resolver.selector = PickingSelector()
    resolver.page_text = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])
    resolver.usage_log = FileModelUsageLog(tmp_path / "usage")
    resolver.now = lambda: RESOLVED_AT

    for _ in range(2):
        await resolver.resolve(indexed_destination(), corridor())

    records = FileModelUsageLog(tmp_path / "usage").read(RESOLVED_AT.date())
    assert [record.call.call for record in records] == ["select", "select"]
    assert {record.corridor_key for record in records} == {corridor().key}
    assert len(recall.records[-1].model_calls) == 1, "the recall log holds only the latest run"


# --- The providers themselves, against a mocked Responses API (entry 166) --------------------


def responses_api_reply(text: str, *, input_tokens: int) -> dict[str, object]:
    """The smallest Responses API body the OpenAI client and LangChain accept, reporting usage the
    way a GPT-5.6 call does — cache writes included."""

    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "test-model",
        "output": [
            {
                "type": "message",
                "id": "msg_test",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1_100},
            "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": input_tokens + 20,
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


class ProviderEndpoint:
    """A Responses API that fails the first request carrying each marker, then answers.

    A mock transport, so nothing reaches the network. The input tokens it reports depend on the
    packet, so two concurrent calls can each be checked for their own figures.
    """

    def __init__(self, text: str, *, fail_first: tuple[str, ...] = ()) -> None:
        self.text = text
        self.failing = set(fail_first)
        self.requests = 0
        self.bodies: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests += 1
        body = request.content.decode()
        self.bodies.append(body)
        for marker in sorted(self.failing):
            if marker in body:
                self.failing.discard(marker)
                return httpx2.Response(
                    500, headers={"retry-after-ms": "1"}, json={"error": {"message": "overloaded"}}
                )
        tokens = 2_400 if "PACKET-B" in body else 1_200
        return httpx2.Response(200, json=responses_api_reply(self.text, input_tokens=tokens))


def selector_against(endpoint: ProviderEndpoint) -> LangChainCandidateSelector:
    return LangChainCandidateSelector(
        api_key="test-key",
        model_name="test-model",
        request_timeout_seconds=5.0,
        max_output_tokens=100,
        reasoning_effort="low",
        transport=httpx2.MockTransport(endpoint),
    )


async def test_every_call_caches_only_its_instructions_and_never_writes_its_packet() -> None:
    """Left implicit, every call wrote its whole prompt to the cache at 1.25× the input rate, and
    nothing ever read a packet back (entries 167 and 169)."""

    selection = ProviderEndpoint(json.dumps({"source_ids": []}))
    await selector_against(selection).select("SELECT-PROMPT", "PACKET-SELECT")
    roles = ProviderEndpoint(json.dumps({"choices": [], "delegates": [], "tools": []}))
    await LangChainRoleAdjudicator(
        api_key="test-key",
        model_name="test-model",
        request_timeout_seconds=5.0,
        max_output_tokens=100,
        reasoning_effort="low",
        transport=httpx2.MockTransport(roles),
    ).adjudicate("ROLES-PROMPT", "PACKET-ROLES")
    plan = ProviderEndpoint("{}")
    with pytest.raises(LLMExtractionError):
        # The reply is not a plan, so the call refuses; what it sent is the part under test.
        await LangChainStructuredPlanGenerator(
            api_key="test-key",
            model_name="test-model",
            request_timeout_seconds=5.0,
            max_output_tokens=100,
            reasoning_effort="low",
            transport=httpx2.MockTransport(plan),
        ).generate("PLAN-PROMPT", "PACKET-PLAN")

    for endpoint in (selection, roles, plan):
        body = json.loads(endpoint.bodies[0])
        assert body["prompt_cache_options"] == {"mode": "explicit"}
        instructions, packet = body["input"]
        assert instructions["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert "prompt_cache_breakpoint" not in json.dumps(packet), "a packet is never written"


async def test_a_retried_selection_counts_every_request_and_keeps_cache_writes() -> None:
    """The OpenAI client retried a failed selection by itself, and that retry was invisible."""

    endpoint = ProviderEndpoint(json.dumps({"source_ids": ["japan_a"]}), fail_first=("PACKET",))
    usage = UsageRecorder()

    selection = await selector_against(endpoint).select("PROMPT", "PACKET", usage=usage)

    assert selection.source_ids == ["japan_a"]
    assert endpoint.requests == 2
    assert usage.http_requests == 2
    assert usage.input_tokens == 1_200
    assert usage.cache_write_input_tokens == 1_100
    assert usage.reasoning_output_tokens == 5


async def test_role_adjudication_sends_one_request_and_a_failure_reports_no_usage() -> None:
    endpoint = ProviderEndpoint("{}", fail_first=("PACKET",))
    adjudicator = LangChainRoleAdjudicator(
        api_key="test-key",
        model_name="test-model",
        request_timeout_seconds=5.0,
        max_output_tokens=100,
        reasoning_effort="low",
        transport=httpx2.MockTransport(endpoint),
    )
    usage = UsageRecorder()

    with pytest.raises(AdjudicationError):
        await adjudicator.adjudicate("PROMPT", "PACKET", usage=usage)

    assert endpoint.requests == 1, "role adjudication keeps max_retries=0"
    assert usage.http_requests == 1
    assert usage.input_tokens is None, "the provider reported nothing, which is not zero"


async def test_concurrent_calls_on_one_provider_each_record_their_own_usage() -> None:
    """One provider serves every concurrent web request, which is why usage is handed in."""

    endpoint = ProviderEndpoint(json.dumps({"source_ids": ["japan_a"]}), fail_first=("PACKET-B",))
    selector = selector_against(endpoint)
    first, second = UsageRecorder(), UsageRecorder()

    await asyncio.gather(
        selector.select("PROMPT", "PACKET-A", usage=first),
        selector.select("PROMPT", "PACKET-B", usage=second),
    )

    assert (first.http_requests, first.input_tokens) == (1, 1_200)
    assert (second.http_requests, second.input_tokens) == (2, 2_400)


def test_the_record_keeps_model_calls_and_an_older_log_reads_as_unrecorded(
    tmp_path: Path,
) -> None:
    """Empty means the log predates the field, not a run that made no calls — which is a real
    state a corridor reaches when it refuses before selection."""

    log = FileRecallLog(tmp_path)
    corridor = Corridor(
        destination_slug="japan",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )
    log.write(
        RecallRecord(
            corridor_key=corridor.key,
            recorded_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            outcome="resolved",
            model_calls=[
                ModelCall(
                    call="roles", prompt_characters=100, packet_characters=48000, seconds=22.9
                )
            ],
        )
    )

    read = log.read(corridor)
    assert read is not None
    assert read.model_calls[0].packet_characters == 48000
    assert read.model_calls[0].seconds == 22.9
    assert (
        RecallRecord(
            corridor_key=corridor.key,
            recorded_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            outcome="resolved",
        ).model_calls
        == []
    )


async def test_a_pool_cut_for_the_selector_says_so_and_the_log_marks_what_was_withheld(
    tmp_path: Path,
) -> None:
    """Entry 195: a big pool is cut before the model sees it, and nothing is dropped silently.

    The fixture's pool is small, so the cut is set to one to exercise it. The corridor's notes say
    how many were not shown and why, and each withheld row in the recall log says so — which is how
    a later reader tells "never offered" from "offered and not picked".
    """

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA], log)
    selector = OfferRecordingSelector()
    resolver.selector = selector
    resolver.page_text = text_store(tmp_path, [INDEX, MISSION_INDEX, DETAIL_INDIA])
    resolver.selection_shown = 1
    resolver.selection_blind = 0

    resolved = await resolver.resolve(indexed_destination(), corridor())

    assert len(selector.offered[0]) == 1
    rows = log.records[-1].candidates
    withheld = [row for row in rows if row.withheld_from_selection]
    assert withheld, "the pool held more than one candidate, so something was withheld"
    assert not any(row.url in selector.offered[0] for row in withheld)
    assert any(
        f"{len(withheld)} of the" in note and "were not shown to it" in note
        for note in resolved.notes
    )


def test_the_adjudicators_reasons_reach_the_record_and_an_older_log_reads_empty(
    tmp_path: Path,
) -> None:
    """TODO item 70. Empty means no role adjudication answered — including every log written
    before the field existed, which is true of them."""

    log = RecordingLog()
    resolver = build_resolver(tmp_path, [], log)
    trace = ResolutionTrace()
    trace.role_verdicts = [
        RoleVerdict(role="visa_decision", reason="No candidate names Bangladesh."),
    ]
    trace.adjudicated_ids = {"tl_1": AUTHORITY}

    resolver._write_recall_log(corridor(), trace, None)

    record = log.records[-1]
    assert record.role_verdicts[0].reason == "No candidate names Bangladesh."
    assert record.role_verdicts[0].source_id is None
    assert record.adjudicated_ids == {"tl_1": AUTHORITY}
    older = RecallRecord.model_validate(
        {"corridor_key": "k", "recorded_at": RESOLVED_AT.isoformat(), "outcome": "refused"}
    )
    assert older.role_verdicts == [] and older.adjudicated_ids == {}
