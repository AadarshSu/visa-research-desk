"""A plan the model wrote is reused only for exactly the same inputs (DECISIONS entry 178)."""

from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml

from visa_research_agent.config.loader import load_destination_registry, load_runtime_policy
from visa_research_agent.config.traveller import DEFAULT_TRAVELLER_PROFILE
from visa_research_agent.discovery.adjudication import UsageRecorder
from visa_research_agent.domain.models import (
    DestinationConfig,
    RetrievalReport,
    RuntimePolicy,
    SharedDetails,
    SharedDocument,
    TravellerProfile,
    TripDates,
    VisaPlanDraft,
)
from visa_research_agent.research import openai_extraction
from visa_research_agent.research.errors import LLMExtractionError
from visa_research_agent.research.fixtures import FixtureSourceFetcher
from visa_research_agent.research.model_usage import FileModelUsageLog
from visa_research_agent.research.openai_extraction import (
    LangChainStructuredPlanGenerator,
    OpenAIVisaPlanExtractor,
)
from visa_research_agent.research.plan_store import FilePlanStore, PlanReuse

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


class CountingGenerator:
    def __init__(self, result: VisaPlanDraft) -> None:
        self.result = result
        self.calls = 0

    async def generate(
        self, system_prompt: str, research_packet: str, *, usage: UsageRecorder | None = None
    ) -> VisaPlanDraft:
        self.calls += 1
        return self.result


def singapore() -> DestinationConfig:
    destination = load_destination_registry().get("singapore")
    assert destination is not None
    return destination


def golden_draft() -> VisaPlanDraft:
    resource = files("visa_research_agent.fixtures.singapore").joinpath("plan.yaml")
    raw: Any = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return VisaPlanDraft.model_validate(raw)


async def singapore_report() -> RetrievalReport:
    return await FixtureSourceFetcher().fetch(singapore())


def extractor(
    generator: CountingGenerator,
    store: Path,
    *,
    now: datetime = NOW,
    fingerprint: str = "gpt-5.6-terra/low",
    hours: float = 24.0,
    usage_log: FileModelUsageLog | None = None,
) -> OpenAIVisaPlanExtractor:
    return OpenAIVisaPlanExtractor(
        generator,
        maximum_input_characters=80_000,
        usage_log=usage_log,
        now=lambda: now,
        reuse=PlanReuse(
            store=FilePlanStore(store), maximum_age_hours=hours, fingerprint=fingerprint
        ),
    )


def change_first_source(
    report: RetrievalReport,
    *,
    content: str | None = None,
    source: dict[str, Any] | None = None,
) -> RetrievalReport:
    first, *rest = report.fetched
    update: dict[str, Any] = {}
    if content is not None:
        update["content"] = content
    if source is not None:
        update["source"] = first.source.model_copy(update=source)
    return report.model_copy(update={"fetched": [first.model_copy(update=update), *rest]})


@pytest.mark.anyio
async def test_the_same_inputs_again_reuse_the_draft_and_make_no_model_call(tmp_path: Path) -> None:
    """A repeat was ~24s of asking the model the same question over the same evidence (entry 171).
    It is not asked again, and nothing is recorded as a model call because none was made."""

    generator = CountingGenerator(golden_draft())
    usage = FileModelUsageLog(tmp_path / "usage")
    plans = extractor(generator, tmp_path / "plans", usage_log=usage)
    report = await singapore_report()

    first = await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    second = await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)

    assert generator.calls == 1
    assert second == first
    recorded = sum(
        len(path.read_text().splitlines()) for path in (tmp_path / "usage").glob("*.jsonl")
    )
    assert recorded == 1


@pytest.mark.anyio
async def test_a_page_whose_text_changed_is_asked_about_again(tmp_path: Path) -> None:
    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()

    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    edited = change_first_source(report, content=report.fetched[0].content + " Updated guidance.")
    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, edited)

    assert generator.calls == 2


@pytest.mark.anyio
async def test_a_page_re_checked_since_is_asked_about_again(tmp_path: Path) -> None:
    """A `304` moves a page's retrieval time (entry 4), so a draft cannot outlive a re-check."""

    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()

    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    moved = report.fetched[0].source.retrieved_at + timedelta(hours=1)
    rechecked = change_first_source(report, source={"retrieved_at": moved})
    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, rechecked)

    assert generator.calls == 2


@pytest.mark.anyio
async def test_another_traveller_is_never_given_this_travellers_draft(tmp_path: Path) -> None:
    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()
    elsewhere: TravellerProfile = DEFAULT_TRAVELLER_PROFILE.model_copy(
        update={"country_of_residence": "IN"}
    )

    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    await plans.extract(singapore(), elsewhere, report)

    assert generator.calls == 2


@pytest.mark.anyio
async def test_another_prompt_or_model_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = await singapore_report()

    by_model = CountingGenerator(golden_draft())
    await extractor(by_model, tmp_path / "plans", fingerprint="terra").extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    await extractor(by_model, tmp_path / "plans", fingerprint="luna").extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    assert by_model.calls == 2

    by_prompt = CountingGenerator(golden_draft())
    prompt = openai_extraction.load_extraction_prompt()
    plans = extractor(by_prompt, tmp_path / "prompt-plans")
    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    monkeypatch.setattr(
        openai_extraction, "load_extraction_prompt", lambda: prompt + "\n14. A new rule."
    )
    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    assert by_prompt.calls == 2


@pytest.mark.anyio
async def test_a_draft_is_reused_only_inside_its_window(tmp_path: Path) -> None:
    generator = CountingGenerator(golden_draft())
    report = await singapore_report()

    await extractor(generator, tmp_path / "plans").extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    await extractor(generator, tmp_path / "plans", now=NOW + timedelta(hours=23)).extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    assert generator.calls == 1

    await extractor(generator, tmp_path / "plans", now=NOW + timedelta(hours=24)).extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    assert generator.calls == 2


@pytest.mark.anyio
async def test_a_refused_plan_is_never_kept_so_the_next_request_asks_again(
    tmp_path: Path,
) -> None:
    """A refusal was never stored (entry 151); a refused draft must not become a stored one."""

    generator = CountingGenerator(golden_draft().model_copy(update={"destination": "Elsewhere"}))
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()

    for _ in range(2):
        with pytest.raises(LLMExtractionError, match="does not match the configured destination"):
            await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)

    assert generator.calls == 2
    assert not list((tmp_path / "plans").glob("*.json"))


@pytest.mark.anyio
async def test_a_reused_draft_is_still_graded_on_this_requests_retrieval(tmp_path: Path) -> None:
    """Only the inference is reused. A page served stale this time keeps its text and retrieval
    time, so the key matches — and the plan must still say its evidence is stale."""

    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()

    first = await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    stale = change_first_source(report, source={"is_stale": True})
    second = await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, stale)

    assert generator.calls == 1
    assert first.status == "verified"
    assert second.status == "partial"
    assert any(source.is_stale for source in second.sources)


@pytest.mark.anyio
async def test_an_unreadable_stored_draft_costs_a_model_call_never_the_plan(
    tmp_path: Path,
) -> None:
    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()

    await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)
    for path in (tmp_path / "plans").glob("*.json"):
        path.write_text("not a stored draft", encoding="utf-8")
    plan = await plans.extract(singapore(), DEFAULT_TRAVELLER_PROFILE, report)

    assert generator.calls == 2
    assert plan.destination == "Singapore"


def test_a_plan_store_key_that_is_not_a_digest_never_reaches_the_filesystem(
    tmp_path: Path,
) -> None:
    from visa_research_agent.research.plan_store import PlanStoreError

    with pytest.raises(PlanStoreError):
        FilePlanStore(tmp_path).load("../../etc/passwd", now=NOW, maximum_age_hours=24)


def test_plan_reuse_may_not_outlast_the_cache_ttl_of_its_pages() -> None:
    with pytest.raises(ValueError, match="plan reuse cannot outlast"):
        RuntimePolicy(
            schema_version=1,
            source_mode="live",
            extraction_mode="openai",
            source_cache_ttl_hours=24.0,
            source_maximum_stale_hours=168.0,
            plan_reuse_hours=48.0,
        )


def test_the_committed_policy_reuses_plans_within_the_freshness_of_their_pages() -> None:
    policy = load_runtime_policy()

    assert 0 < policy.plan_reuse_hours <= policy.source_cache_ttl_hours


def test_the_generator_fingerprint_changes_with_the_model_and_its_settings() -> None:
    def fingerprint(model: str, effort: str) -> str:
        return LangChainStructuredPlanGenerator(
            api_key="test-key",
            model_name=model,
            request_timeout_seconds=60,
            max_output_tokens=6_000,
            reasoning_effort=effort,
        ).fingerprint

    assert fingerprint("gpt-5.6-terra", "low") == fingerprint("gpt-5.6-terra", "low")
    assert fingerprint("gpt-5.6-terra", "low") != fingerprint("gpt-5.6-luna", "low")
    assert fingerprint("gpt-5.6-terra", "low") != fingerprint("gpt-5.6-terra", "none")


def test_drafts_past_their_window_and_unreadable_ones_are_deleted(tmp_path: Path) -> None:
    """Entry 276: a draft past its window can never be reused, and its prose may name what the
    traveller shared, so it is not kept."""

    store = FilePlanStore(tmp_path)
    old, fresh = "a" * 64, "b" * 64
    store.store(old, golden_draft(), now=NOW)
    store.store(fresh, golden_draft(), now=NOW + timedelta(hours=20))
    (tmp_path / f"{'c' * 64}.json").write_text("not json", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")

    removed = store.prune(now=NOW + timedelta(hours=25), maximum_age_hours=24)

    assert removed == 2
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{fresh}.json", "notes.json"]


@pytest.mark.anyio
async def test_keeping_a_draft_prunes_the_expired_ones(tmp_path: Path) -> None:
    report = await singapore_report()
    await extractor(CountingGenerator(golden_draft()), tmp_path / "plans").extract(
        singapore(), DEFAULT_TRAVELLER_PROFILE, report
    )
    other = DEFAULT_TRAVELLER_PROFILE.model_copy(update={"region_of_residence": "England"})
    await extractor(
        CountingGenerator(golden_draft()), tmp_path / "plans", now=NOW + timedelta(hours=30)
    ).extract(singapore(), other, report)

    assert len(list((tmp_path / "plans").glob("*.json"))) == 1


TRIP = TripDates(mode="exact", start=date(2026, 12, 1), end=date(2026, 12, 20))
PASSPORT = SharedDetails(
    documents=[SharedDocument(kind="passport", nationality="IN", expires_at=date(2027, 2, 7))]
)


@pytest.mark.parametrize(
    "other",
    [
        pytest.param({"trip": None}, id="no-dates"),
        pytest.param({"trip": TRIP.model_copy(update={"end": date(2026, 12, 21)})}, id="a-day-on"),
        pytest.param({"shared_details": PASSPORT}, id="shared-passport"),
        pytest.param({"region_of_residence": "England"}, id="other-region"),
    ],
)
@pytest.mark.anyio
async def test_a_plan_written_for_one_traveller_is_never_served_to_another(
    tmp_path: Path, other: dict[str, object]
) -> None:
    """Entry 279: a plan is written for this traveller's details and dates, and a draft is reused
    only for byte-identical inputs — so a traveller differing in any of them is asked about anew."""

    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()
    first = DEFAULT_TRAVELLER_PROFILE.model_copy(update={"trip": TRIP})

    await plans.extract(singapore(), first, report)
    await plans.extract(singapore(), first.model_copy(update=other), report)

    assert generator.calls == 2


@pytest.mark.anyio
async def test_the_same_traveller_asking_again_about_the_same_trip_reuses_their_draft(
    tmp_path: Path,
) -> None:
    """The owner: so one traveller is not given two different plans for one trip."""

    generator = CountingGenerator(golden_draft())
    plans = extractor(generator, tmp_path / "plans")
    report = await singapore_report()
    traveller = DEFAULT_TRAVELLER_PROFILE.model_copy(
        update={"trip": TRIP, "shared_details": PASSPORT}
    )

    await plans.extract(singapore(), traveller, report)
    await plans.extract(singapore(), traveller.model_copy(), report)

    assert generator.calls == 1
