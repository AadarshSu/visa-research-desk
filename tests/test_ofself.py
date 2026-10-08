"""Reading a passport nationality from an Ofself identity (TODO item 55, step 3).

Nothing here reaches Paradigm: a fake answers through `httpx.MockTransport`. Its shapes were
checked against the live API with a sandbox user on 2026-09-17, where one differs from the developer
guide: `total` is `null`, not a count.
"""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from visa_research_agent.api.ofself import (
    AUTHORIZATION_CODES,
    PAGE_SIZE,
    OfselfAuthorizationLost,
    OfselfIdentity,
    OfselfUnavailable,
    OfselfWriteRefused,
    SavedTrip,
    TripToSave,
)
from visa_research_agent.domain.models import TripDates

pytestmark = pytest.mark.anyio

USER = "2f0c9b8e-4d7a-4e61-9a1b-3c5d7e9f1a2b"
API_KEY = "ofs_tp_test.secret"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def identity(handler: Callable[[httpx.Request], httpx.Response]) -> OfselfIdentity:
    return OfselfIdentity(
        API_KEY, base_url="https://paradigm.test", transport=httpx.MockTransport(handler)
    )


def nodes(*value_jsons: dict[str, object]) -> httpx.Response:
    """A page as Paradigm answered live on 2026-09-17: `total` is `null`, not a count."""

    listed = [{"id": f"node-{i}", "value_json": value} for i, value in enumerate(value_jsons)]
    return httpx.Response(
        200, json={"nodes": listed, "total": None, "limit": PAGE_SIZE, "offset": 0}
    )


async def test_it_asks_paradigm_for_this_users_work_authorization_as_this_app() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return nodes({"citizenships": ["IN"]})

    await identity(handler).passport_nationalities(USER)

    (request,) = seen
    assert request.url.path == "/api/v1/nodes"
    assert request.url.params["schema_id"] == "work-authorization"
    assert request.headers["X-API-Key"] == API_KEY
    assert request.headers["X-User-ID"] == USER


async def test_citizenships_are_normalised_whether_recorded_as_alpha_2_or_alpha_3() -> None:
    """The schema allows either; everything past the edge is keyed on alpha-2."""

    found = await identity(lambda _: nodes({"citizenships": ["IND", "gb"]})).passport_nationalities(
        USER
    )

    assert found.nationalities == ["IN", "GB"]


async def test_every_citizenship_is_offered_and_none_is_chosen() -> None:
    """Rule 3: a traveller with two passports picks one; the adapter keeps both, in order."""

    found = await identity(
        lambda _: nodes({"citizenships": ["PH", "US"]}, {"citizenships": ["USA", "IN"]})
    ).passport_nationalities(USER)

    assert found.nationalities == ["PH", "US", "IN"]


@pytest.mark.parametrize(
    "value_json",
    [{}, {"citizenships": []}, {"citizenships": None}, {"citizenships": "IN"}],
    ids=["field hidden or unrecorded", "empty", "null", "not a list"],
)
async def test_an_identity_that_yields_no_nationality_means_ask(
    value_json: dict[str, object],
) -> None:
    """Rule 4: nothing usable is an empty answer, never the default traveller.

    The sandbox users have full access, so a field the user hid is only ever seen here.
    """

    found = await identity(lambda _: nodes(value_json)).passport_nationalities(USER)

    assert found.nationalities == []


async def test_no_work_authorization_at_all_means_ask() -> None:
    found = await identity(lambda _: nodes()).passport_nationalities(USER)

    assert found.nationalities == []


async def test_a_value_naming_no_known_country_is_reported_and_never_guessed() -> None:
    found = await identity(
        lambda _: nodes({"citizenships": ["Atlantis", "IN", "Atlantis", 42]})
    ).passport_nationalities(USER)

    assert found.nationalities == ["IN"]
    assert found.unrecognised == ["Atlantis"]


async def test_an_encrypted_value_is_counted_and_never_read_as_a_country() -> None:
    """The app holds no keypair, so ciphertext must not reach the country check at all."""

    found = await identity(
        lambda _: nodes({"citizenships": ["paradigm_enc:v2:AAAA", "GB"]})
    ).passport_nationalities(USER)

    assert found.nationalities == ["GB"]
    assert found.unrecognised == []
    assert found.encrypted_values == 1


async def test_more_nodes_than_one_page_are_all_read_without_a_total() -> None:
    """Live, `total` is always `null`, so a full page is what says another may follow."""

    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["offset"]
        offsets.append(offset)
        if offset == "0":
            full_page: list[dict[str, object]] = [{"citizenships": ["IN"]}] * PAGE_SIZE
            return nodes(*full_page)
        return nodes({"citizenships": ["GB"]})

    found = await identity(handler).passport_nationalities(USER)

    assert offsets == ["0", str(PAGE_SIZE)]
    assert found.nationalities == ["IN", "GB"]


@pytest.mark.parametrize("code", sorted(AUTHORIZATION_CODES))
@pytest.mark.parametrize("nested", [True, False], ids=["under error", "flat"])
async def test_a_lost_authorization_is_named_by_its_code(code: str, nested: bool) -> None:
    """The guide documents both envelopes, and both vocabularies of authorization code."""

    error = {"code": code, "message": "no active authorization"}
    body = {"error": error} if nested else error

    with pytest.raises(OfselfAuthorizationLost) as raised:
        await identity(lambda _: httpx.Response(403, json=body)).passport_nationalities(USER)

    assert raised.value.code == code


async def test_a_refused_api_key_is_the_apps_problem_not_the_users() -> None:
    body = {"error": {"code": "INVALID_API_KEY", "message": "Key not found"}}

    with pytest.raises(OfselfUnavailable, match="PARADIGM_API_KEY"):
        await identity(lambda _: httpx.Response(401, json=body)).passport_nationalities(USER)


async def test_any_other_refusal_is_reported_with_its_code() -> None:
    body = {"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Too many requests"}}

    with pytest.raises(OfselfUnavailable, match="RATE_LIMIT_EXCEEDED") as raised:
        await identity(lambda _: httpx.Response(429, json=body)).passport_nationalities(USER)

    assert not isinstance(raised.value, OfselfAuthorizationLost)


async def test_a_transport_failure_with_an_empty_message_still_says_what_happened() -> None:
    """Entry 122: `ConnectTimeout` carries no message, so the class name is the only fact."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("", request=request)

    with pytest.raises(OfselfUnavailable, match="ConnectTimeout"):
        await identity(handler).passport_nationalities(USER)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"results": []}),
    ],
    ids=["not JSON", "no node list"],
)
async def test_a_malformed_answer_is_unavailable_not_empty(response: httpx.Response) -> None:
    """An empty answer means "ask the traveller"; a broken one must not be mistaken for it."""

    with pytest.raises(OfselfUnavailable):
        await identity(lambda _: response).passport_nationalities(USER)


async def test_a_user_id_that_is_not_a_uuid_is_refused_before_anything_is_sent() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return nodes()

    with pytest.raises(OfselfUnavailable, match="UUID"):
        await identity(handler).passport_nationalities("alice")

    assert sent == []


def test_an_adapter_without_an_api_key_cannot_be_built() -> None:
    with pytest.raises(OfselfUnavailable, match="API key"):
        OfselfIdentity("  ")


# --- the travel schemas: passports, residence and plans (DECISIONS entry 181) ---------------

TODAY = date(2026, 9, 21)


def by_schema(**schemas: list[dict[str, object]]) -> Callable[[httpx.Request], httpx.Response]:
    """A fake Paradigm answering each schema with its own nodes, and any other with none — which
    is also how the live API answers a schema outside the grant (2026-09-21)."""

    def handler(request: httpx.Request) -> httpx.Response:
        schema = request.url.params["schema_id"].replace("-", "_")
        listed = [
            {"id": str(value.pop("_id", f"{schema}-{i}")), "value_json": value}
            for i, value in enumerate(dict(entry) for entry in schemas.get(schema, []))
        ]
        return httpx.Response(200, json={"nodes": listed, "total": None})

    return handler


def passport(**fields: object) -> dict[str, object]:
    return {"kind": "passport", "nationality": "IND", "status": "valid", **fields}


def typed(*fields: str) -> dict[str, object]:
    return {field: {"source": "manual", "verification": "none"} for field in fields}


def chip_read(*fields: str) -> dict[str, object]:
    return {field: {"source": "nfc_chip", "verification": "cryptographic"} for field in fields}


async def test_each_schema_is_asked_for_as_this_app_and_places_only_when_a_plan_needs_them() -> (
    None
):
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["schema_id"])
        assert request.headers["X-User-ID"] == USER
        return nodes()

    await identity(handler).traveller_defaults(USER, today=TODAY)

    assert sorted(asked) == ["travel-document", "travel-plan", "work-authorization"]


async def test_a_passport_is_offered_with_its_expiry_and_whether_it_was_read_off_the_document() -> (
    None
):
    """A typed-in date may raise a question and never close one, so the two are kept apart."""

    found = await identity(
        by_schema(
            travel_document=[
                passport(
                    label="my Indian passport",
                    expires_at="2031-03-02",
                    field_provenance=chip_read("expires_at"),
                ),
                passport(
                    nationality="GBR", expires_at="2029-01-01", field_provenance=typed("expires_at")
                ),
            ]
        )
    ).traveller_defaults(USER, today=TODAY)

    indian, british = found.passports
    assert (indian.nationality, indian.label, indian.expires_at) == (
        "IN",
        "my Indian passport",
        date(2031, 3, 2),
    )
    assert indian.expiry_attested is True and indian.expired is False
    assert british.nationality == "GB" and british.expiry_attested is False


async def test_a_diplomatic_passport_is_withheld_and_never_offered() -> None:
    """Rule 2: a passport this program cannot research is refused, never scored as ordinary."""

    found = await identity(
        by_schema(
            travel_document=[
                passport(document_code="PD", label="diplomatic"),
                passport(nationality="GBR", document_code="P"),
                passport(nationality="USA"),
            ]
        )
    ).traveller_defaults(USER, today=TODAY)

    assert [p.nationality for p in found.passports] == ["GB", "US"]
    (withheld,) = found.withheld_passports
    assert (withheld.nationality, withheld.document_code) == ("IN", "PD")


async def test_an_expired_passport_is_offered_marked_and_a_lost_one_not_at_all() -> None:
    found = await identity(
        by_schema(
            travel_document=[
                passport(expires_at="2026-01-01"),
                passport(nationality="GBR", status="expired"),
                passport(nationality="USA", status="lost"),
                passport(nationality="CAN", status="replaced"),
            ]
        )
    ).traveller_defaults(USER, today=TODAY)

    assert [(p.nationality, p.expired) for p in found.passports] == [("IN", True), ("GB", True)]


async def test_of_two_passports_of_one_nationality_the_valid_one_is_offered() -> None:
    found = await identity(
        by_schema(
            travel_document=[
                passport(label="the old one", expires_at="2025-05-01"),
                passport(label="the new one", expires_at="2035-05-01"),
            ]
        )
    ).traveller_defaults(USER, today=TODAY)

    (offered,) = found.passports
    assert offered.label == "the new one"


async def test_a_citizenship_with_no_document_is_still_offered_and_a_document_supersedes_it() -> (
    None
):
    found = await identity(
        by_schema(
            work_authorization=[{"citizenships": ["IND", "PH"]}],
            travel_document=[passport(expires_at="2031-03-02")],
        )
    ).traveller_defaults(USER, today=TODAY)

    by_nationality = {p.nationality: p for p in found.passports}
    assert by_nationality["IN"].from_document is True
    assert by_nationality["IN"].expires_at == date(2031, 3, 2)
    assert by_nationality["PH"].from_document is False


async def test_another_persons_document_is_counted_and_never_offered() -> None:
    found = await identity(
        by_schema(travel_document=[passport(holder_ref="a-child"), passport(nationality="GBR")])
    ).traveller_defaults(USER, today=TODAY)

    assert [p.nationality for p in found.passports] == ["GB"]
    assert found.other_holders == 1


async def test_a_residence_permit_offers_the_country_that_issued_it() -> None:
    found = await identity(
        by_schema(
            travel_document=[
                {
                    "kind": "residence_permit",
                    "issuing_state": "GBR",
                    "label": "BRP",
                    "grants": {"class": "Skilled Worker"},
                    "expires_at": "2027-06-30",
                    "field_provenance": typed("expires_at"),
                }
            ]
        )
    ).traveller_defaults(USER, today=TODAY)

    (residence,) = found.residences
    assert (residence.country, residence.permit_class, residence.expires_at) == (
        "GB",
        "Skilled Worker",
        date(2027, 6, 30),
    )
    assert residence.expiry_attested is False and found.passports == []


async def test_an_open_plan_offers_its_candidates_resolved_through_their_places() -> None:
    """A candidate's place may carry its own country or sit under one — Lisbon under Portugal."""

    found = await identity(
        by_schema(
            travel_plan=[
                {
                    "label": "Ana's wedding",
                    "status": "open",
                    "window": {"earliest": "2027-02-01", "latest": "2027-02-28"},
                    "candidates": [
                        {"place_ref": "lisbon", "purpose": "tourism"},
                        {"place_ref": "japan", "purpose": "family"},
                        {"place_ref": "brazil", "ruled_out_reason": "too far"},
                        {"place_ref": "nowhere"},
                    ],
                },
                {
                    "label": "given up",
                    "commitment": "abandoned",
                    "candidates": [{"place_ref": "japan"}],
                },
                {
                    "label": "booked already",
                    "status": "committed",
                    "candidates": [{"place_ref": "japan"}],
                },
            ],
            place=[
                {"_id": "portugal", "kind": "country", "country_code": "PT"},
                {"_id": "lisbon", "kind": "town", "parent_ref": "portugal"},
                {"_id": "japan", "kind": "country", "country_code": "JP"},
                {"_id": "brazil", "kind": "country", "country_code": "BR"},
            ],
        )
    ).traveller_defaults(USER, today=TODAY)

    (plan,) = found.plans
    assert (plan.label, plan.earliest, plan.latest) == (
        "Ana's wedding",
        date(2027, 2, 1),
        date(2027, 2, 28),
    )
    portugal, japan = plan.candidates
    assert (portugal.destination, portugal.destination_slug, portugal.purpose) == (
        "PT",
        "portugal",
        "tourism",
    )
    # "family" is not a purpose this app researches, so the traveller chooses one.
    assert (japan.destination, japan.purpose, japan.recorded_purpose) == ("JP", None, "family")
    assert found.unresolved_candidates == 1


async def test_nothing_shared_is_an_empty_answer_never_a_claim_that_nothing_exists() -> None:
    """Paradigm answers a schema outside the grant with `200` and no nodes."""

    found = await identity(lambda _: nodes()).traveller_defaults(USER, today=TODAY)

    assert found.passports == [] and found.residences == [] and found.plans == []


async def test_a_lost_grant_on_any_schema_asks_the_traveller_to_reconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["schema_id"] == "travel-plan":
            return httpx.Response(403, json={"error": {"code": "EP_PAUSED", "message": "paused"}})
        return nodes()

    with pytest.raises(OfselfAuthorizationLost):
        await identity(handler).traveller_defaults(USER, today=TODAY)


# --- what the plan call tailors to (DECISIONS entry 276) ------------------------------------------


async def test_shared_details_read_documents_stays_and_applications_for_this_traveller() -> None:
    found = await identity(
        by_schema(
            travel_document=[
                passport(
                    nationality="GBR",
                    issuing_state="GBR",
                    issued_at="2017-05-01",
                    expires_at="2027-03-03",
                    date_of_birth="1990-01-02",
                    field_provenance={**chip_read("expires_at"), **typed("issued_at")},
                ),
                {
                    "kind": "visa",
                    "issuing_state": "USA",
                    "grants": {"class": "B1/B2"},
                    "expires_at": "2025-01-01",
                },
                passport(nationality="FRA", holder_ref="person-2"),
                passport(nationality="IND", document_code="PD"),
            ],
            travel_stay=[
                {"place_ref": "spain", "entry_at": "2026-07-01", "exit_at": "2026-07-20"},
                {"place_ref": "france", "entry_at": "2026-05-01", "provenance": "border_record"},
                {"place_ref": "spain", "entry_at": "2026-08-01", "traveller_ref": "person-2"},
            ],
            travel_obligation=[{"kind": "visa", "state": "refused", "decided_at": "2024-02-10"}],
            place=[
                {"_id": "spain", "kind": "country", "country_code": "ESP"},
                {"_id": "france", "kind": "country", "country_code": "FR"},
            ],
        )
    ).shared_details(USER, today=TODAY)

    british, visa = found.documents
    assert (british.kind, british.nationality, british.issuing_state) == ("passport", "GB", "GB")
    assert british.expires_at == date(2027, 3, 3) and british.expires_at_read_off_document
    assert british.issued_at == date(2017, 5, 1) and not british.issued_at_read_off_document
    assert (visa.kind, visa.issuing_state, visa.grants, visa.expired) == (
        "visa",
        "US",
        "B1/B2",
        True,
    )
    assert found.date_of_birth == date(1990, 1, 2) and not found.date_of_birth_read_off_document
    # Ordered by entry; another person's stay is never this traveller's history.
    france, spain = found.stays
    assert (france.country, france.exited_at, france.self_declared) == ("FR", None, False)
    assert (spain.country, spain.exited_at, spain.self_declared) == ("ES", date(2026, 7, 20), True)
    (refusal,) = found.applications
    assert (refusal.kind, refusal.outcome, refusal.decided_at) == (
        "visa",
        "refused",
        date(2024, 2, 10),
    )


async def test_shared_details_read_places_only_when_a_stay_needs_one() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["schema_id"])
        return nodes()

    found = await identity(handler).shared_details(USER, today=TODAY)

    assert sorted(asked) == ["travel-document", "travel-obligation", "travel-stay"]
    assert found.is_empty()


# --- saving the trip a traveller chose (DECISIONS entry 283) ---------------------------------


def paradigm_with_writes(
    written: list[dict[str, object]], **schemas: list[dict[str, object]]
) -> Callable[[httpx.Request], httpx.Response]:
    """`by_schema` for reads; each write is recorded and answered as Paradigm did live on
    2026-10-09: `201` and the node itself, its `id` at the top."""

    read = by_schema(**schemas)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return read(request)
        assert request.url.path == "/api/v1/nodes"
        assert request.headers["X-User-ID"] == USER
        body = json.loads(request.content)
        written.append(body)
        return httpx.Response(201, json={"id": f"new-{len(written)}", **body})

    return handler


EXACT = TripDates(mode="exact", start=date(2026, 12, 1), end=date(2026, 12, 20))


async def test_a_trip_is_saved_as_a_plan_being_considered_with_its_country_minted() -> None:
    written: list[dict[str, object]] = []

    saved = await identity(paradigm_with_writes(written)).save_trip(
        USER, TripToSave(destination="TH", purpose="tourism", trip=EXACT)
    )

    place, plan = written
    assert place["schema_name"] == "place"
    assert place["value_json"] == {
        "name": "Thailand",
        "kind": "country",
        "country_code": "TH",
        "status": "active",
        "source_system": "visa_research_agent",
        "source_ref": "visa_research_agent:place:country:th",
    }
    assert plan["schema_name"] == "travel-plan"
    assert plan["title"] == "Thailand, tourism"
    assert plan["value_json"] == {
        "label": "Thailand, tourism",
        "candidates": [{"place_ref": "new-1", "purpose": "tourism"}],
        "linked_refs": ["new-1"],
        "commitment": "considering",
        "status": "open",
        "window": {
            "earliest": "2026-12-01",
            "latest": "2026-12-20",
            "flexible": False,
            "nights": 19,
        },
    }
    assert saved == SavedTrip(status="saved", plan_id="new-2", place_created=True)


async def test_the_travellers_own_country_place_is_reused_and_a_town_is_not_a_country() -> None:
    written: list[dict[str, object]] = []

    saved = await identity(
        paradigm_with_writes(
            written,
            place=[
                {"_id": "bangkok", "kind": "town", "country_code": "TH"},
                {
                    "_id": "old-thailand",
                    "kind": "country",
                    "country_code": "THA",
                    "status": "suspended",
                },
                {"_id": "thailand", "kind": "country", "country_code": "THA"},
            ],
        )
    ).save_trip(USER, TripToSave(destination="TH", purpose="business"))

    (plan,) = written
    value = plan["value_json"]
    assert isinstance(value, dict)
    assert value["candidates"] == [{"place_ref": "thailand", "purpose": "business"}]
    assert "window" not in value
    assert saved.place_created is False


async def test_a_rough_span_stays_soft() -> None:
    written: list[dict[str, object]] = []
    rough = TripDates(mode="rough", start=date(2027, 2, 1), end=date(2027, 3, 31))

    await identity(paradigm_with_writes(written)).save_trip(
        USER, TripToSave(destination="JP", purpose="study", trip=rough)
    )

    value = written[-1]["value_json"]
    assert isinstance(value, dict)
    assert value["window"] == {"earliest": "2027-02-01", "latest": "2027-03-31", "flexible": True}


async def test_nothing_the_plan_concluded_is_written() -> None:
    written: list[dict[str, object]] = []

    await identity(paradigm_with_writes(written)).save_trip(
        USER, TripToSave(destination="TH", purpose="tourism", trip=EXACT)
    )

    plan = written[-1]["value_json"]
    assert isinstance(plan, dict)
    assert not {"decide_by", "verdict_cached_at", "notes", "travellers"} & set(plan)
    assert [body["schema_name"] for body in written] == ["place", "travel-plan"]


async def test_the_same_trip_already_open_is_not_written_twice() -> None:
    written: list[dict[str, object]] = []
    window = {"earliest": "2026-12-01", "latest": "2026-12-20"}

    saved = await identity(
        paradigm_with_writes(
            written,
            travel_plan=[
                {
                    "_id": "mine",
                    "status": "open",
                    "window": window,
                    "candidates": [{"place_ref": "bangkok", "purpose": "tourism"}],
                }
            ],
            place=[
                {"_id": "thailand", "kind": "country", "country_code": "TH"},
                {"_id": "bangkok", "kind": "town", "parent_ref": "thailand"},
            ],
        )
    ).save_trip(USER, TripToSave(destination="TH", purpose="tourism", trip=EXACT))

    assert written == []
    assert saved == SavedTrip(status="already_saved", plan_id="mine")


@pytest.mark.parametrize(
    "plan",
    [
        {"commitment": "abandoned"},
        {"candidates": [{"place_ref": "thailand", "purpose": "business"}]},
        {"window": {"earliest": "2026-12-02", "latest": "2026-12-20"}},
        {"candidates": [{"place_ref": "thailand", "purpose": "tourism", "ruled_out_reason": "x"}]},
    ],
    ids=["abandoned", "another purpose", "other dates", "ruled out"],
)
async def test_a_plan_for_another_trip_does_not_stop_the_save(plan: dict[str, object]) -> None:
    written: list[dict[str, object]] = []
    existing = {
        "status": "open",
        "window": {"earliest": "2026-12-01", "latest": "2026-12-20"},
        "candidates": [{"place_ref": "thailand", "purpose": "tourism"}],
        **plan,
    }

    saved = await identity(
        paradigm_with_writes(
            written,
            travel_plan=[existing],
            place=[{"_id": "thailand", "kind": "country", "country_code": "TH"}],
        )
    ).save_trip(USER, TripToSave(destination="TH", purpose="tourism", trip=EXACT))

    assert saved.status == "saved"
    assert [body["schema_name"] for body in written] == ["travel-plan"]


async def test_a_grant_without_create_is_a_refused_write() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return nodes()
        return httpx.Response(403, json={"error": {"code": "PERMISSION_DENIED", "message": "no"}})

    with pytest.raises(OfselfWriteRefused, match="re-approve"):
        await identity(handler).save_trip(USER, TripToSave(destination="TH", purpose="tourism"))


async def test_a_lost_grant_while_saving_asks_the_traveller_to_reconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return nodes()
        return httpx.Response(403, json={"error": {"code": "EP_REVOKED", "message": "no"}})

    with pytest.raises(OfselfAuthorizationLost):
        await identity(handler).save_trip(USER, TripToSave(destination="TH", purpose="tourism"))


async def test_a_write_answered_as_the_guide_shows_it_is_read_too() -> None:
    """The developer guide wraps the new node in `node`; live it came back bare."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return nodes()
        return httpx.Response(201, json={"node": {"id": "wrapped"}})

    saved = await identity(handler).save_trip(USER, TripToSave(destination="TH", purpose="tourism"))

    assert saved.plan_id == "wrapped"


async def test_a_write_answered_without_an_id_is_not_a_save() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return nodes()
        return httpx.Response(201, json={"node": {}})

    with pytest.raises(OfselfUnavailable, match="id"):
        await identity(handler).save_trip(USER, TripToSave(destination="TH", purpose="tourism"))
