from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from visa_research_agent.domain.models import (
    ApplicationStep,
    InteractiveTool,
    SourceReference,
    VisaPlan,
    VisaPlanDraft,
    VisaRequirement,
)


def application_steps(source_id: str = "official-source") -> list[ApplicationStep]:
    return [
        ApplicationStep(
            title=title,
            action=action,
            timing=timing,
            source_ids=[source_id],
            link_target="source",
            link_source_id=source_id,
        )
        for title, action, timing in [
            ("Prepare documents", "Prepare the documents.", "Before booking."),
            ("Book appointment", "Book the appointment.", "Before travel."),
            ("Attend centre", "Attend the visa centre.", "At the booked time."),
            ("Submit application", "Submit the application.", "At the appointment."),
        ]
    ]


def test_source_reference_requires_a_timezone() -> None:
    with pytest.raises(ValidationError, match="timestamp must include a timezone"):
        SourceReference.model_validate(
            {
                "source_id": "official-source",
                "title": "Official source",
                "url": "https://example.gov/visas",
                "authority": "Example authority",
                "retrieved_at": datetime(2026, 8, 5),
            }
        )


def test_requirement_rejects_an_unsupported_category() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VisaRequirement.model_validate(
            {
                "name": "Passport",
                "category": "sometimes",
                "description": "A passport is requested.",
                "reason_it_applies": "The traveller uses an ordinary passport.",
                "source_ids": ["official-source"],
            }
        )


def test_visa_plan_rejects_unknown_requirement_source_ids() -> None:
    checked_at = datetime(2026, 8, 5, tzinfo=UTC)

    with pytest.raises(ValidationError, match="unknown source IDs"):
        VisaPlan(
            destination="Singapore",
            visa_required=True,
            visa_type="Entry visa",
            explanation="The cited source states that a visa is required.",
            decision_source_ids=["official-source"],
            where_to_apply=None,
            requirements=[
                VisaRequirement(
                    name="Passport",
                    description="A passport is requested.",
                    reason_it_applies="The traveller uses an ordinary passport.",
                    source_ids=["missing-source"],
                )
            ],
            application_document_source_ids=["official-source"],
            application_steps=application_steps(),
            sources=[
                SourceReference.model_validate(
                    {
                        "source_id": "official-source",
                        "title": "Official source",
                        "url": "https://example.gov/visas",
                        "authority": "Example authority",
                        "retrieved_at": checked_at,
                    }
                )
            ],
            unresolved_questions=[],
            last_checked=checked_at,
            status="verified",
        )


def test_application_steps_are_structured_and_bounded() -> None:
    steps = [step.model_dump() for step in application_steps()]
    steps[0]["action"] = "A" * 321

    with pytest.raises(ValidationError):
        VisaPlan.model_validate(
            {
                "destination": "Singapore",
                "visa_required": True,
                "visa_type": "Entry visa",
                "explanation": "The cited source states that a visa is required.",
                "decision_source_ids": ["official-source"],
                "where_to_apply": None,
                "requirements": [],
                "application_document_source_ids": ["official-source"],
                "application_steps": steps,
                "sources": [
                    {
                        "source_id": "official-source",
                        "title": "Official source",
                        "url": "https://example.gov/visas",
                        "authority": "Example authority",
                        "retrieved_at": datetime(2026, 8, 5, tzinfo=UTC),
                    }
                ],
                "unresolved_questions": [],
                "last_checked": datetime(2026, 8, 5, tzinfo=UTC),
            }
        )


def test_a_step_link_to_a_page_that_is_not_a_source_fails_the_plan() -> None:
    """Since TODO item 70 an uncited link is cited rather than refused, so the containment is the
    plan's: a linked id that is not a packet source is an unknown source."""

    steps = [step.model_dump() for step in application_steps()]
    steps[0]["link_source_id"] = "uncited-source"

    with pytest.raises(ValidationError, match="unknown source IDs"):
        VisaPlan.model_validate(
            {
                "destination": "Singapore",
                "visa_required": True,
                "visa_type": "Entry visa",
                "explanation": "The cited source states that a visa is required.",
                "decision_source_ids": ["official-source"],
                "where_to_apply": None,
                "requirements": [],
                "application_document_source_ids": ["official-source"],
                "application_steps": steps,
                "sources": [
                    {
                        "source_id": "official-source",
                        "title": "Official source",
                        "url": "https://example.gov/visas",
                        "authority": "Example authority",
                        "retrieved_at": datetime(2026, 8, 5, tzinfo=UTC),
                    }
                ],
                "unresolved_questions": [],
                "last_checked": datetime(2026, 8, 5, tzinfo=UTC),
                "status": "verified",
            }
        )


def checklistless_plan(**overrides: object) -> dict[str, object]:
    """A plan for an authority that publishes no document checklist at all."""

    checked_at = datetime(2026, 8, 5, tzinfo=UTC)
    body: dict[str, object] = {
        "destination": "Vietnam",
        "visa_required": True,
        "visa_type": "E-visa",
        "explanation": "The cited source states that a visa is required.",
        "decision_source_ids": ["official-source"],
        "where_to_apply": None,
        "requirements": [],
        "application_document_source_ids": [],
        "application_steps": [step.model_dump() for step in application_steps()],
        "sources": [
            {
                "source_id": "official-source",
                "title": "Official source",
                "url": "https://example.gov/visas",
                "authority": "Example authority",
                "retrieved_at": checked_at,
            }
        ],
        "unresolved_questions": ["No official document checklist is published for this route."],
        "last_checked": checked_at,
        "status": "verified",
    }
    body.update(overrides)
    return body


def test_a_plan_may_have_no_document_checklist_when_it_says_so() -> None:
    """Some authorities publish none, so the plan states the gap rather than being refused."""

    plan = VisaPlan.model_validate(checklistless_plan())

    assert plan.application_document_source_ids == []
    assert plan.requirements == []


def test_a_plan_with_no_document_source_cannot_list_requirements() -> None:
    """The guard that makes a checklist-less corridor safe to serve.

    With no designated document source there is nothing a requirement could honestly cite, so
    listing one means it was inferred from an eligibility rule or an application form read as
    though it were guidance. Refused structurally rather than asked for in the prompt.
    """

    with pytest.raises(ValidationError, match="cannot list document requirements"):
        VisaPlan.model_validate(
            checklistless_plan(
                requirements=[
                    {
                        "name": "Passport",
                        "description": "A passport is requested.",
                        "reason_it_applies": "Inferred from the eligibility page.",
                        "source_ids": ["official-source"],
                    }
                ]
            )
        )


def test_a_plan_with_no_document_source_must_say_what_is_missing() -> None:
    # Silence would read as "this authority requires no documents", which is far worse.
    with pytest.raises(ValidationError, match="must record what could not be answered"):
        VisaPlan.model_validate(checklistless_plan(unresolved_questions=[]))


def test_a_step_title_that_stopped_mid_clause_is_tidied() -> None:
    """A model given eighty characters wrote to the limit and stopped mid-sentence — "…if the wizard
    says a visa," — which reads as a truncated interface. The title is a label, so trimming the
    trailing punctuation invents nothing; the substance was always in the action."""

    step = ApplicationStep.model_validate(
        {
            "title": "Create an account and complete the online application,",
            "action": "Create an account on the official portal and complete the form.",
            "timing": "After confirming a visa is needed",
            "source_ids": ["a_source"],
            "link_target": "none",
            "link_source_id": None,
        }
    )

    assert step.title == "Create an account and complete the online application"


def test_a_step_title_has_room_for_a_label_and_no_more() -> None:
    """Seventy characters is a label. A sentence belongs in the action, which has room for one."""

    with pytest.raises(ValidationError):
        ApplicationStep.model_validate(
            {
                "title": "Create an account and complete the online application if the wizard "
                "says a visa is needed for this trip",
                "action": "Create an account on the official portal.",
                "timing": "After confirming a visa is needed",
                "source_ids": ["a_source"],
                "link_target": "none",
                "link_source_id": None,
            }
        )


# --- a decision an official tool holds ---------------------------------------------------------


def plan_payload(**overrides: object) -> dict[str, object]:
    checked_at = datetime(2026, 8, 5, tzinfo=UTC)
    payload: dict[str, object] = {
        "destination": "United Kingdom",
        "visa_required": None,
        "visa_type": None,
        "explanation": "The Home Office decides this through its own official checker.",
        "decision_source_ids": ["official-source"],
        "where_to_apply": None,
        "requirements": [],
        "application_document_source_ids": [],
        "application_steps": [step.model_dump() for step in application_steps()],
        "sources": [
            {
                "source_id": "official-source",
                "title": "Official source",
                "url": "https://example.gov/visas",
                "authority": "Example authority",
                "retrieved_at": checked_at,
            }
        ],
        "unresolved_questions": ["Answer the official checker to get the decision."],
        "last_checked": checked_at,
        "status": "partial",
        "official_tools": [
            {
                "topic": "visa_decision",
                "url": "https://www.gov.uk/check-uk-visa",
                "authority": "United Kingdom authority (www.gov.uk)",
                "detail": "decides this by asking questions rather than stating an answer",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_a_plan_may_name_the_tool_that_holds_the_decision_it_could_not_state() -> None:
    plan = VisaPlan.model_validate(plan_payload())

    assert plan.visa_required is None
    assert [str(tool.url) for tool in plan.decision_tools] == ["https://www.gov.uk/check-uk-visa"]


def test_naming_the_tool_and_answering_it_are_mutually_exclusive() -> None:
    """A tool is named because no page stated the decision. Stating one anyway means it came from
    somewhere else — most likely the questionnaire's own prompts, which is reading an answer out of
    a question."""

    with pytest.raises(ValidationError, match="cannot also state whether a visa is required"):
        VisaPlan.model_validate(plan_payload(visa_required=True))


def test_a_plan_resting_on_an_unanswered_questionnaire_is_never_verified() -> None:
    """The decision is the one thing a traveller most needs right, and nobody read it off a page."""

    with pytest.raises(ValidationError, match="nobody read off a page"):
        VisaPlan.model_validate(plan_payload(status="verified"))


def test_a_decision_tool_carries_no_claim_about_what_it_would_answer() -> None:
    """Only where the decision is settled, never what it settles to."""

    tool = InteractiveTool.model_validate(
        {
            "topic": "visa_decision",
            "url": "https://www.gov.uk/check-uk-visa",
            "authority": "United Kingdom authority (www.gov.uk)",
            "detail": "decides this by asking questions rather than stating an answer",
        }
    )

    assert set(tool.model_dump()) == {"topic", "url", "authority", "detail"}


def test_a_checklist_tool_may_never_sit_beside_a_designated_checklist_source() -> None:
    """A tool is named because no page listed the documents. Designating a source anyway means the
    list came from somewhere that is not a checklist, which is the failure this project exists to
    prevent — `validate_absent_checklist` blocks it from the other side."""

    payload = plan_payload(
        official_tools=[
            {
                "topic": "document_checklist",
                "url": "https://www.gov.uk/check-uk-visa",
                "authority": "United Kingdom authority (www.gov.uk)",
                "detail": "lists the documents by asking questions",
            }
        ],
        application_document_source_ids=["official-source"],
    )

    with pytest.raises(ValidationError, match="cannot also designate a checklist source"):
        VisaPlan.model_validate(payload)


def test_a_tool_for_another_topic_leaves_the_visa_decision_alone() -> None:
    """Only `visa_decision` is load-bearing. A questionnaire holding the fees adds a link to a plan
    that stands on its own, and must not force the decision to unknown."""

    plan = VisaPlan.model_validate(
        plan_payload(
            visa_required=True,
            visa_type="Standard Visitor visa",
            status="partial",
            official_tools=[
                {
                    "topic": "fees",
                    "url": "https://www.gov.uk/check-uk-visa",
                    "authority": "United Kingdom authority (www.gov.uk)",
                    "detail": "works out the fee by asking questions",
                }
            ],
        )
    )

    assert plan.visa_required is True
    assert plan.decision_tools == []


# --- a decision that is a stated "no" ------------------------------------------------------------


def entry_payload(**overrides: object) -> dict[str, object]:
    """Singapore's shape for a Filipino traveller: no visa, and three duties that are not one.

    Three is not a chosen number. Curating the entry duties three visa-free corridors state gives
    three for Singapore, five for Japan and seven for the United Kingdom, whose visa-free visitors
    must still hold an ETA — so the honest list has no floor, and this fixture sits under the
    application's (DECISIONS entry 95).
    """

    checked_at = datetime(2026, 8, 5, tzinfo=UTC)
    duties = [
        ("Submit the arrival card", "Submit the SG Arrival Card.", "Within three days of arrival."),
        ("Check your passport", "Hold a passport valid past the stay.", "Before travel."),
        ("Carry onward travel", "Be able to show onward travel.", "At the border."),
    ]
    payload: dict[str, object] = {
        "destination": "Singapore",
        "visa_required": False,
        "visa_type": None,
        "explanation": "The authority lists the passports needing a visa and this is not one.",
        "decision_source_ids": ["official-source"],
        "where_to_apply": None,
        "requirements": [],
        "application_document_source_ids": [],
        "application_steps": [
            {
                "title": title,
                "action": action,
                "timing": timing,
                "source_ids": ["official-source"],
                "link_target": "source",
                "link_source_id": "official-source",
            }
            for title, action, timing in duties
        ],
        "sources": [
            {
                "source_id": "official-source",
                "title": "Official source",
                "url": "https://example.gov/visas",
                "authority": "Example authority",
                "retrieved_at": checked_at,
            }
        ],
        "unresolved_questions": [],
        "last_checked": checked_at,
        "status": "verified",
    }
    payload.update(overrides)
    return payload


def test_an_entry_plan_carries_the_duties_the_sources_state_and_no_filler() -> None:
    """Three entry steps, no checklist question, and still verified.

    Every relaxation here is one the application shape refuses, and each would otherwise force the
    model to write something it has no evidence for: a fourth duty, or a sentence saying no
    checklist was found when none was ever missing."""

    plan = VisaPlan.model_validate(entry_payload())

    assert plan.visa_required is False
    assert len(plan.application_steps) == 3
    assert plan.requirements == []
    assert plan.where_to_apply is None
    assert plan.unresolved_questions == []
    assert plan.status == "verified"


def test_an_entry_plan_may_state_the_decision_and_nothing_else() -> None:
    """No duty stated is an empty list, never an invented one. The decision still stands alone."""

    plan = VisaPlan.model_validate(entry_payload(application_steps=[]))

    assert plan.application_steps == []
    assert plan.decision_source_ids == ["official-source"]


@pytest.mark.parametrize("decision", [True, None])
def test_only_a_stated_no_may_use_fewer_than_two_steps(decision: bool | None) -> None:
    """The guard, read from the side a wrong answer would come in by.

    A plan that needs a visa is a process with a form and a fee, and one that could not confirm the
    decision has to leave every question visible. Neither may borrow the short shape — a three-line
    application would misdescribe the first, and suppressing the route would leave the second with
    nothing for the traveller to notice the error with."""

    payload = entry_payload(visa_required=decision, status="partial")
    payload["application_steps"] = cast(list[object], payload["application_steps"])[:1]
    payload["unresolved_questions"] = ["Which documents the application needs."]

    with pytest.raises(ValidationError, match="at least 2 steps"):
        VisaPlan.model_validate(payload)


def test_an_application_timeline_past_five_is_shown_not_refused() -> None:
    """Entry 255: the prompt asks for at most five, and a plan that writes six is still shown —
    a long timeline is what plans were before, and refusing a correct answer for its length is
    worse. The schema's eight still holds."""

    six = cast(list[object], entry_payload()["application_steps"]) * 2
    payload = entry_payload(visa_required=True, status="partial", application_steps=six)
    payload["unresolved_questions"] = ["Which documents the application needs."]

    assert len(VisaPlan.model_validate(payload).application_steps) == 6


def test_an_entry_plan_still_cannot_list_a_single_document() -> None:
    """`validate_absent_checklist` loses its unresolved-question clause and keeps the one that
    matters. With no designated document source there is still nothing a requirement could cite."""

    payload = entry_payload(
        requirements=[
            {
                "name": "Passport",
                "description": "A passport is requested.",
                "reason_it_applies": "Stated on the entry page.",
                "source_ids": ["official-source"],
            }
        ]
    )

    with pytest.raises(ValidationError, match="cannot list document requirements"):
        VisaPlan.model_validate(payload)


def test_a_plan_that_could_not_find_a_checklist_must_still_say_so() -> None:
    """The carve-out is for a question that does not arise, never for one that went unanswered."""

    payload = entry_payload(
        visa_required=True,
        status="partial",
        application_steps=[step.model_dump() for step in application_steps()],
    )

    with pytest.raises(ValidationError, match="must record what could not be answered"):
        VisaPlan.model_validate(payload)


def test_the_draft_holds_the_same_line_before_the_app_sees_it() -> None:
    """The floor is on `VisaPlanDraft` too, so a model that summarised a route away is refused at
    the point it answered rather than after the plan is assembled."""

    draft = {
        "destination": "Singapore",
        "visa_required": False,
        "visa_type": None,
        "explanation": "No visa is required for this passport.",
        "decision_source_ids": ["official-source"],
        "where_to_apply": None,
        "requirements": [],
        "application_steps": entry_payload()["application_steps"],
        "unresolved_questions": [],
    }

    assert len(VisaPlanDraft.model_validate(draft).application_steps) == 3

    with pytest.raises(ValidationError, match="at least 2 steps"):
        VisaPlanDraft.model_validate(
            {
                **draft,
                "visa_required": True,
                "application_steps": cast(list[object], draft["application_steps"])[:1],
            }
        )


def test_a_step_linking_a_page_it_did_not_cite_cites_it() -> None:
    """TODO item 70, and entry 197's fix should it recur — it did, on Germany `IN/GB`. The model
    linked the "where can I apply" page as `source` and cited only the pages stating the step, and
    the whole plan was refused. The linked page is now cited; it adds no page, because an id that
    is not a packet source still fails the plan."""

    step = ApplicationStep(
        title="Book the appointment",
        action="Book a C-visa appointment at the centre serving you.",
        timing="Before travel.",
        source_ids=["faq"],
        link_target="source",
        link_source_id="where-to-apply",
    )

    assert step.source_ids == ["faq", "where-to-apply"]
