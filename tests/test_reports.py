"""A traveller's report of a corridor that did not work (TODO item 74)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from visa_research_agent.api.app import create_app
from visa_research_agent.api.reports import (
    MAXIMUM_REPORT_BYTES,
    FileReportStore,
    current_commit,
)
from visa_research_agent.config.settings import settings
from visa_research_agent.discovery.cli import main
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.recall_log import FileRecallLog

pytestmark = pytest.mark.anyio

REFUSAL = {
    "message": "Some of France's official pages refused automated reading.",
    "status": "unable_to_verify",
    "cause": "pages_unreadable",
    "unreadable_pages": ["https://france-visas.gouv.fr/en/web/france-visas/visa-wizard"],
}
REPORT: dict[str, Any] = {
    "request": {
        "destination": "france",
        "traveller": {
            "passport_nationality": "IN",
            "country_of_residence": "GB",
            "travel_purpose": "tourism",
            "region_of_residence": "England",
            "residence_status": "skilled worker",
        },
    },
    "outcome": "refusal",
    "shown": REFUSAL,
    "stages": ["search", "gather", "choose", "read", "check"],
}
FRANCE_IN_GB = Corridor(
    destination_slug="france", passport_nationality="IN", applying_from="GB", purpose="tourism"
)


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(settings, "require_sign_in", False)
    monkeypatch.setattr(settings, "report_directory", tmp_path / "reports")
    monkeypatch.setattr(settings, "recall_log_directory", tmp_path / "recall")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


def write_recall_log(directory: Path) -> None:
    path = FileRecallLog(directory).path_for(FRANCE_IN_GB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"corridor_key": FRANCE_IN_GB.key, "cause": "decision_not_found"}))


async def test_a_report_keeps_the_corridor_what_was_shown_and_a_copy_of_the_run(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    write_recall_log(tmp_path / "recall")

    response = await client.post("/reports", json=REPORT)

    assert response.status_code == 201
    [report] = FileReportStore(tmp_path / "reports").load_all()
    assert report.report_id == response.json()["report_id"]
    assert report.corridor.key == FRANCE_IN_GB.key
    assert report.cause == "pages_unreadable"
    assert report.shown == REFUSAL
    assert report.stages == REPORT["stages"]
    assert report.recall_log == {"corridor_key": FRANCE_IN_GB.key, "cause": "decision_not_found"}
    assert report.rerun_command() == (
        "visa-discover corridor --destination france --nationality IN --from GB --purpose tourism"
    )


async def test_nothing_about_the_traveller_beyond_the_corridor_is_kept(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await client.post("/reports", json=REPORT)

    [stored] = (tmp_path / "reports").glob("*.json")
    text = stored.read_text()
    assert "Leeds" not in text
    assert "skilled worker" not in text


async def test_the_log_is_a_copy_so_a_later_run_cannot_change_the_report(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Entry 118: the next run of the same corridor overwrites its log."""

    write_recall_log(tmp_path / "recall")
    await client.post("/reports", json=REPORT)
    FileRecallLog(tmp_path / "recall").path_for(FRANCE_IN_GB).write_text('{"cause": "resolved"}')

    [report] = FileReportStore(tmp_path / "reports").load_all()
    assert report.recall_log is not None
    assert report.recall_log["cause"] == "decision_not_found"


async def test_a_corridor_with_no_run_is_reported_without_a_log(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    unknown = {**REPORT, "request": {**REPORT["request"], "destination": "atlantis"}}

    response = await client.post("/reports", json=unknown)

    assert response.status_code == 201
    [report] = FileReportStore(tmp_path / "reports").load_all()
    assert report.corridor.key is None
    assert report.recall_log is None
    assert report.rerun_command() is None


async def test_the_page_cannot_supply_the_log_or_anything_else_unasked_for(
    client: httpx.AsyncClient,
) -> None:
    forged = {**REPORT, "recall_log": {"cause": "resolved"}}

    response = await client.post("/reports", json=forged)

    assert response.status_code == 422


async def test_a_report_larger_than_any_result_is_refused(client: httpx.AsyncClient) -> None:
    padded = {**REPORT, "shown": {**REFUSAL, "message": "x" * MAXIMUM_REPORT_BYTES}}

    response = await client.post("/reports", json=padded)

    assert response.status_code == 413


async def test_reporting_needs_sign_in_as_a_plan_does(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "require_sign_in", True)

    response = await client.post("/reports", json=REPORT)

    assert response.status_code in (401, 503)
    assert not (tmp_path / "reports").exists()


async def test_the_command_lists_each_report_with_its_rerun_line(
    client: httpx.AsyncClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await client.post("/reports", json=REPORT)

    assert main(["reports", str(tmp_path / "reports")]) == 0

    printed = capsys.readouterr().out
    assert "france IN/GB tourism  pages_unreadable  (no run log)" in printed
    assert "visa-discover corridor --destination france --nationality IN --from GB" in printed


async def test_the_command_prints_one_report_in_full(
    client: httpx.AsyncClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_id = (await client.post("/reports", json=REPORT)).json()["report_id"]

    assert main(["reports", str(tmp_path / "reports"), "--show", report_id]) == 0

    assert json.loads(capsys.readouterr().out)["shown"] == REFUSAL


def test_the_build_is_read_from_git_without_running_it(tmp_path: Path) -> None:
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("abc123\n")

    assert current_commit(tmp_path) == "abc123"
    assert current_commit(tmp_path / "missing") is None


def test_reports_are_listed_newest_first(tmp_path: Path) -> None:
    from visa_research_agent.api.reports import ProblemReport, ReportBuild, ReportedCorridor

    store = FileReportStore(tmp_path)
    for day in (1, 3, 2):
        store.store(
            ProblemReport(
                report_id=f"r{day}",
                received_at=datetime(2026, 9, day, tzinfo=UTC),
                corridor=ReportedCorridor(
                    destination="france",
                    passport_nationality="IN",
                    applying_from="GB",
                    purpose="tourism",
                ),
                outcome="plan",
                cause=None,
                shown={},
                stages=[],
                recall_log=None,
                build=ReportBuild(commit=None, static_asset_version="x"),
            )
        )

    assert [report.report_id for report in store.load_all()] == ["r3", "r2", "r1"]


async def test_a_plan_report_keeps_what_the_traveller_says_is_wrong(
    client: httpx.AsyncClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_report = {**REPORT, "outcome": "plan", "shown": {"destination": "France"}}
    plan_report["message"] = "  The checklist link opens the wrong page.\nIt goes to Schengen.  "

    response = await client.post("/reports", json=plan_report)

    assert response.status_code == 201
    [report] = FileReportStore(tmp_path / "reports").load_all()
    assert report.message == "The checklist link opens the wrong page.\nIt goes to Schengen."
    assert report.cause is None
    assert main(["reports", str(tmp_path / "reports")]) == 0
    assert '"The checklist link opens the wrong page."' in capsys.readouterr().out


async def test_a_refusal_is_reported_without_a_message(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    await client.post("/reports", json=REPORT)

    [report] = FileReportStore(tmp_path / "reports").load_all()
    assert report.message is None


async def test_a_message_longer_than_the_box_allows_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/reports", json={**REPORT, "message": "x" * 2001})

    assert response.status_code == 422
