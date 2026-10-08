"""Reading a traveller's details from their Ofself identity (TODO item 55, step 3).

Ofself's developer platform is Paradigm. This app's DLR asks for `work-authorization`'s
`citizenships`, and — since DECISIONS entry 181 — for named fields of Ofself's travel schemas. This
module is the one place that reads any of it.

**It reads only fields a built feature uses** (entry 181's third bound). The DLR asks ahead for
more — stays, obligations, a date of birth — and none of those is read here until a feature needs
it. What is read today: citizenships; a travel document's kind, code, nationality, issuing state,
expiry, status, holder, label, the class a permit grants, and where each date came from; an open
travel plan's label, window and candidates; and a place's kind, country code and parent, to turn a
candidate into a country. **For the plan call (entry 276):** the traveller's own documents with
their dates and what each grants, their stays, and what they applied for and how it ended — never a
number, a name, a scan, a note or a refusal's reason.

**What the form gets is a default for the traveller to confirm, never a corridor;** what the plan
call gets tailors a plan and never decides one. The node may have
been parsed out of a CV by another app rather than stated by the person, and someone with two
citizenships chooses which passport a trip is on, so nothing here picks one (item 55, rule 3). An
identity that yields no nationality means *ask the traveller* — never the default traveller, which
belongs to the anonymous form alone (rule 4).

**What it does not do, deliberately:**
- **Guess.** A value that is not a country this program holds reference data for is returned as
  unrecognised. It is not matched loosely, and it is not dropped silently.
- **Decrypt.** The app holds no keypair (entry 180). A value that arrives encrypted is counted and
  never read as a country.
- **Store.** Nothing read here is written anywhere.
- **Write back anything concluded.** The one write is the trip a traveller chooses to save, on their
  click (DECISIONS entry 283): a `travel-plan` they are considering, and the `place` of kind
  `country` its destination must point at when they hold none. Never the visa decision, a
  checklist, a fee, a deadline or a passport.
- **Retry.** A refused or lost authorisation is the user's decision, and is reported as such.
"""

import asyncio
import logging
import uuid
from datetime import date
from typing import Literal, cast, get_args

import httpx
from pydantic import Field

from visa_research_agent.api.countries import normalise_country
from visa_research_agent.discovery.lexicon import get_country_registry
from visa_research_agent.domain.models import (
    SharedApplication,
    SharedDetails,
    SharedDocument,
    SharedStay,
    StrictModel,
    TravelPurpose,
    TripDates,
)
from visa_research_agent.research.errors import VisaResearchError
from visa_research_agent.research.live_sources import transport_failure_reason

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.ofself.ai"
WORK_AUTHORIZATION_SCHEMA = "work-authorization"
TRAVEL_DOCUMENT_SCHEMA = "travel-document"
TRAVEL_PLAN_SCHEMA = "travel-plan"
TRAVEL_STAY_SCHEMA = "travel-stay"
TRAVEL_OBLIGATION_SCHEMA = "travel-obligation"
PLACE_SCHEMA = "place"

ORDINARY_PASSPORT_CODES = frozenset({"P", "P<"})
"""ICAO 9303 document codes read as an ordinary passport. `PD` is diplomatic and `PS` service,
and a state may use the second letter for other kinds, so any other code is refused rather than
assumed ordinary (item 55, rule 2). A passport recorded with no code at all is treated as the form
has always treated one: as ordinary, which is what the page says it researches."""

RETIRED_DOCUMENT_STATUSES = frozenset({"lost", "stolen", "cancelled", "replaced", "superseded"})
"""A document in one of these states is not one a trip could be made on, so it is not offered.
`expired` is not here: an expired passport is offered, marked, because saying so is the point."""

ATTESTED_VERIFICATIONS = frozenset({"cryptographic", "checksum"})
"""`travel-document.field_provenance` values that mean a date was read off the document itself.
Anything else — `none`, or no provenance at all — is a person's memory of it, which by the
schema's own rule may raise a question and may never close one."""

MAXIMUM_PLACE_DEPTH = 6
"""How far up `parent_ref` a place is walked to find its country — a home, its town, region and
country is four. A deeper or circular chain is left unresolved rather than followed."""

PAGE_SIZE = 100
"""`GET /nodes` caps a page at 100. `work-authorization` is one node per user, so one page is the
ordinary case, but more than one node is read correctly rather than assumed away.

Paging stops on a page that comes back short, not on `total`: live, on 2026-09-17, `total` was
`null` on every answer, though the developer guide shows a count."""

ENCRYPTED_VALUE_PREFIX = "paradigm_enc:"

AUTHORIZATION_CODES = frozenset(
    {"EP_NOT_FOUND", "EP_REVOKED", "EP_PAUSED", "EP_EXPIRED", "NO_AUTHORIZATION", "NO_PERMISSIONS"}
)
"""Every code Paradigm uses to say this user's grant to the app is gone or never existed. The
developer guide names two vocabularies — the `EP_*` codes and the older `NO_AUTHORIZATION` — so
both are handled."""


class OfselfError(VisaResearchError):
    """Raised when a traveller's Ofself identity could not be read."""


class OfselfUnavailable(OfselfError):
    """Paradigm could not be reached, refused the app's own credentials, or answered malformed."""


class OfselfAuthorizationLost(OfselfError):
    """The user has no active grant to this app: never given, revoked, paused or expired.

    The only honest response is to ask them to reconnect. It is never a reason to fall back to a
    default traveller.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OfselfWriteRefused(OfselfError):
    """Paradigm would not let this app write to the user's account: the grant has no `create` for
    the schema. Until the traveller re-approves the app's access, nothing can be saved."""


class OfselfSignInRejected(OfselfError):
    """Paradigm refused a sign-in's session code: invalid, expired or already used."""


SIGN_IN_REJECTION_CODES = frozenset({"INVALID_CODE", "VALIDATION_ERROR"})
"""What `POST /auth/session/exchange` answered to a made-up and to a missing code (2026-09-17)."""


class PassportNationalities(StrictModel):
    """What the traveller's identity says about their citizenships, for them to confirm."""

    nationalities: list[str] = Field(default_factory=list)
    """Alpha-2 codes, in the order recorded, without repeats. Empty means ask the traveller."""

    unrecognised: list[str] = Field(default_factory=list)
    """Recorded values that name no country this program holds reference data for."""

    encrypted_values: int = 0
    """Values that arrived encrypted, which this app cannot and does not read."""


class OfselfPassport(StrictModel):
    """A passport the traveller may be travelling on, offered for them to choose — never chosen."""

    nationality: str
    """Alpha-2."""

    label: str | None = None
    """What the person calls it — "my Indian passport". Absent for a bare citizenship."""

    expires_at: date | None = None
    expiry_attested: bool = False
    """True only when the expiry was read off the document (a chip, or an MRZ that passed its
    check digits). A typed-in date is shown as something to check, never as a fact."""

    expired: bool = False
    """Recorded as expired, or its expiry has passed."""

    from_document: bool = True
    """False when all Ofself holds is a citizenship: no document, so no expiry to show."""


class WithheldPassport(StrictModel):
    """A passport this app will not research, and says why rather than silently dropping it."""

    nationality: str | None = None
    label: str | None = None
    document_code: str


class OfselfResidence(StrictModel):
    """A residence permit, offered as the country applied from — for the traveller to confirm."""

    country: str
    """Alpha-2 of the state that issued the permit."""

    label: str | None = None
    permit_class: str | None = None
    """The coded class the permit grants — "Skilled Worker" — as recorded."""

    expires_at: date | None = None
    expiry_attested: bool = False
    expired: bool = False


class OfselfPlanCandidate(StrictModel):
    """One destination a traveller's plan is considering."""

    destination: str
    """Alpha-2."""

    destination_slug: str
    """How the page's destination list names the same country."""

    purpose: TravelPurpose | None = None
    """Only a purpose this app researches. Anything else is left for the traveller to choose."""

    recorded_purpose: str | None = None
    """The plan's own purpose when it is not one this app researches — "family", "medical"."""


class OfselfTravelPlan(StrictModel):
    """A journey the traveller is considering, as they labelled it."""

    label: str
    earliest: date | None = None
    latest: date | None = None
    candidates: list[OfselfPlanCandidate] = Field(default_factory=list)


class TravellerDefaults(StrictModel):
    """What Ofself holds that can start the form — every part for the traveller to confirm.

    **Empty means not shared, or not recorded, and the two cannot be told apart.** Paradigm
    answers a read of a schema outside the grant with `200` and no nodes (observed 2026-09-21), so
    nothing here may say that a traveller *has* no document or plan.
    """

    passports: list[OfselfPassport] = Field(default_factory=list)
    withheld_passports: list[WithheldPassport] = Field(default_factory=list)
    residences: list[OfselfResidence] = Field(default_factory=list)
    plans: list[OfselfTravelPlan] = Field(default_factory=list)

    other_holders: int = 0
    """Documents recorded for another person on the account, which are not offered as this
    traveller's."""

    unresolved_candidates: int = 0
    """Plan candidates whose place could not be turned into a country this program researches."""

    unrecognised: list[str] = Field(default_factory=list)
    encrypted_values: int = 0


class TripToSave(StrictModel):
    """The trip a traveller chose to save to their Ofself account, as the form described it."""

    destination: str
    """Alpha-2."""

    purpose: TravelPurpose
    trip: TripDates | None = None
    """None for "not sure yet": the plan is saved with no window."""


class SavedTrip(StrictModel):
    """What saving did. `already_saved` wrote nothing: an open plan for this trip was there."""

    status: Literal["saved", "already_saved"]
    plan_id: str | None = None
    place_created: bool = False


SOURCE_SYSTEM = "visa_research_agent"
"""How a `place` this app mints names its emitter (the schema's `source_system`)."""


Node = tuple[str | None, dict[str, object]]
"""A node as read: its id, and its `value_json` narrowed to the fields the grant allows."""


class OfselfIdentity:
    """Reads one Paradigm user's identity on this app's behalf, within the grant they gave it."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise OfselfUnavailable("A Paradigm API key is required to read an Ofself identity")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def passport_nationalities(self, user_id: str) -> PassportNationalities:
        """The citizenships recorded in the user's `work-authorization`, normalised, unconfirmed."""

        user = _user(user_id)
        values: list[object] = []
        for _, value_json in await self._read_all(user, WORK_AUTHORIZATION_SCHEMA):
            values.extend(_citizenships_of(value_json))
        return _normalise(values)

    async def traveller_defaults(self, user_id: str, *, today: date) -> TravellerDefaults:
        """What Ofself holds that can start the form: passports, residence and trips considered.

        Every part is a default the traveller confirms (item 55, rule 3), and nothing read here
        reaches a plan except through the form they submit. `today` decides what has expired.
        """

        user = _user(user_id)
        citizenships, documents, plans = await asyncio.gather(
            self.passport_nationalities(user),
            self._read_all(user, TRAVEL_DOCUMENT_SCHEMA),
            self._read_all(user, TRAVEL_PLAN_SCHEMA),
        )
        # Places are read only when a plan needs one resolved: every place the person holds
        # arrives, as a grain and a country code, so it is not asked for without a reason.
        places = await self._read_all(user, PLACE_SCHEMA) if plans else []
        return _defaults(citizenships, documents, plans, places, today=today)

    async def save_trip(self, user_id: str, trip: TripToSave) -> SavedTrip:
        """Write the trip into the traveller's account as a `travel-plan` (DECISIONS entry 283).

        Only on the traveller's click, for the one trip they chose. Its destination must be a
        `place` node, so their own `country` place is reused, or one is minted first. The plan
        holds the destination, the purpose, the window and that it is being considered — nothing
        the plan concluded. An open plan for the same trip is not written twice.
        """

        user = _user(user_id)
        plans, places = await asyncio.gather(
            self._read_all(user, TRAVEL_PLAN_SCHEMA), self._read_all(user, PLACE_SCHEMA)
        )
        existing = _same_trip(plans, _place_countries(places), trip)
        if existing is not None:
            return SavedTrip(status="already_saved", plan_id=existing)

        country = get_country_registry().get(trip.destination)
        name = country.name if country is not None else trip.destination
        place_id = _country_place(places, trip.destination)
        place_created = place_id is None
        if place_id is None:
            # Minted before the plan that needs it. Should the plan's write then fail, the place
            # stays, and the next save reuses it rather than minting a second.
            place_id = await self._create_node(
                user, PLACE_SCHEMA, name, _new_country_place(trip.destination, name)
            )
        label = f"{name}, {trip.purpose}"
        plan_id = await self._create_node(
            user, TRAVEL_PLAN_SCHEMA, label, _new_travel_plan(trip, place_id, label)
        )
        return SavedTrip(status="saved", plan_id=plan_id, place_created=place_created)

    async def exchange_session_code(self, code: str) -> str:
        """The user a sign-in's single-use `sid_code` belongs to, asked of Paradigm itself.

        **This is the only source of a signed-in user id.** Ofself's authorize page sends the
        browser back with `user_id` in the query string beside the code, where anyone can write any
        id; only this server-to-server exchange, made with this app's key, says who actually
        approved. Found by reading the authorize page's own code and probing with a dummy code
        (2026-09-17): the developer guide documents neither the parameter nor the endpoint.
        """

        if not code.strip():
            raise OfselfSignInRejected("The sign-in carried no session code to verify")
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout_seconds,
                headers={"Accept": "application/json", "X-API-Key": self.api_key},
            ) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/auth/session/exchange", json={"code": code}
                )
        except httpx.HTTPError as exc:
            raise OfselfUnavailable(
                f"Ofself could not be reached: {transport_failure_reason(exc)}"
            ) from exc

        if response.status_code != httpx.codes.OK:
            error_code, _ = _error_of(response)
            if error_code in SIGN_IN_REJECTION_CODES:
                raise OfselfSignInRejected(
                    "Ofself did not recognise this sign-in: the code is invalid, expired or used"
                )
            raise _refusal(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OfselfUnavailable("Ofself answered the sign-in with a non-JSON body") from exc
        user_id = _exchanged_user_id(payload)
        if user_id is None:
            # A success is single-use and its shape has not been seen, so say what it held —
            # field names only, never values — for the one chance there is to learn it.
            shape = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
            logger.warning("Ofself session exchange named no user id; response fields: %s", shape)
            raise OfselfUnavailable("Ofself confirmed the sign-in without saying which user it was")
        return user_id

    async def shared_details(self, user_id: str, *, today: date) -> SharedDetails:
        """What the traveller shared for the plan call to tailor to (entry 276).

        Their documents, stays and applications, as the grant lets this app see them: never a
        number, a name, a scan, a note or a refusal's reason, none of which is requested. Another
        person's records on the account are left out, and so is a passport this program would not
        research. Empty means nothing shared or nothing recorded, never that they hold nothing.
        """

        user = _user(user_id)
        documents, stays, obligations = await asyncio.gather(
            self._read_all(user, TRAVEL_DOCUMENT_SCHEMA),
            self._read_all(user, TRAVEL_STAY_SCHEMA),
            self._read_all(user, TRAVEL_OBLIGATION_SCHEMA),
        )
        # A stay names its country only through a place, so places are read only when one does.
        places = await self._read_all(user, PLACE_SCHEMA) if stays else []
        return _shared(documents, stays, obligations, places, today=today)

    async def _read_all(self, user_id: str, schema: str) -> list[Node]:
        """Every node of one schema the grant lets this app see, as its id and `value_json`.

        Paging stops on a short page, never on `total`, which Paradigm answers as `null`.
        """

        values: list[Node] = []
        offset = 0
        while True:
            page = await self._list_nodes(user_id, schema, offset)
            nodes = page["nodes"]
            for node in nodes:
                if isinstance(node, dict) and isinstance(node.get("value_json"), dict):
                    values.append((_text(node.get("id")), node["value_json"]))
            offset += len(nodes)
            if len(nodes) < PAGE_SIZE:
                return values

    async def _list_nodes(self, user_id: str, schema: str, offset: int) -> dict[str, list[object]]:
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            "X-User-ID": user_id,
        }
        params: dict[str, str | int] = {
            "schema_id": schema,
            "fields": "id,value_json",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds, headers=headers
            ) as client:
                response = await client.get(f"{self.base_url}/api/v1/nodes", params=params)
        except httpx.HTTPError as exc:
            raise OfselfUnavailable(
                f"Ofself could not be reached: {transport_failure_reason(exc)}"
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise _refusal(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OfselfUnavailable("Ofself answered with a body that is not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
            raise OfselfUnavailable("Ofself answered without a list of nodes")
        return payload

    async def _create_node(
        self, user_id: str, schema: str, title: str, value_json: dict[str, object]
    ) -> str:
        """Create one node in the user's account; return its id."""

        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            "X-User-ID": user_id,
        }
        body = {"schema_name": schema, "title": title, "value_json": value_json}
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds, headers=headers
            ) as client:
                response = await client.post(f"{self.base_url}/api/v1/nodes", json=body)
        except httpx.HTTPError as exc:
            raise OfselfUnavailable(
                f"Ofself could not be reached: {transport_failure_reason(exc)}"
            ) from exc

        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            code, _ = _error_of(response)
            if response.status_code == httpx.codes.FORBIDDEN and code not in AUTHORIZATION_CODES:
                raise OfselfWriteRefused(
                    "Ofself did not let this app save to your account"
                    f" ({code or 'HTTP 403'}): re-approve its access on Ofself to save trips"
                )
            raise _refusal(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OfselfUnavailable("Ofself answered the write with a non-JSON body") from exc
        # Live (2026-10-09) a write answers `201` with the node itself, `id` at the top; the guide
        # shows it under `node`. Both are accepted, and nothing looser.
        envelope = payload if isinstance(payload, dict) else {}
        node = envelope.get("node")
        identifier = _text((node if isinstance(node, dict) else envelope).get("id"))
        if identifier is None:
            raise OfselfUnavailable("Ofself confirmed the write without the new node's id")
        return identifier


def _same_trip(plans: list[Node], countries: dict[str, str], trip: TripToSave) -> str | None:
    """The id of a plan, not abandoned, already holding this destination, purpose and window."""

    earliest = trip.trip.start if trip.trip else None
    latest = trip.trip.end if trip.trip else None
    for identifier, plan in plans:
        if plan.get("status") == "abandoned" or plan.get("commitment") == "abandoned":
            continue
        raw_window = plan.get("window")
        window: dict[str, object] = raw_window if isinstance(raw_window, dict) else {}
        if (_date(window.get("earliest")), _date(window.get("latest"))) != (earliest, latest):
            continue
        raw_candidates = plan.get("candidates")
        for raw in raw_candidates if isinstance(raw_candidates, list) else []:
            if (
                isinstance(raw, dict)
                and not _text(raw.get("ruled_out_reason"))
                and countries.get(_text(raw.get("place_ref")) or "") == trip.destination
                and raw.get("purpose") == trip.purpose
            ):
                return identifier
    return None


def _country_place(places: list[Node], country: str) -> str | None:
    """The traveller's own `place` of kind `country` for this code, if they hold one."""

    for identifier, place in places:
        if not identifier or place.get("kind") != "country" or place.get("status") == "suspended":
            continue
        try:
            if normalise_country(_text(place.get("country_code")) or "") == country:
                return identifier
        except ValueError:
            continue
    return None


def _new_country_place(country: str, name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "country",
        "country_code": country,
        "status": "active",
        "source_system": SOURCE_SYSTEM,
        # Stable per country, so a retried create is recognisable as a retry (the schema's rule).
        "source_ref": f"{SOURCE_SYSTEM}:place:country:{country.lower()}",
    }


def _new_travel_plan(trip: TripToSave, place_id: str, label: str) -> dict[str, object]:
    """A plan being considered: no verdict, no `decide_by`, nothing the plan concluded."""

    value: dict[str, object] = {
        "label": label,
        "candidates": [{"place_ref": place_id, "purpose": trip.purpose}],
        # The traversal index sees only top-level ids, so the candidate's place is mirrored here.
        "linked_refs": [place_id],
        "commitment": "considering",
        "status": "open",
    }
    if trip.trip is not None:
        window: dict[str, object] = {
            "earliest": trip.trip.start.isoformat(),
            "latest": trip.trip.end.isoformat(),
            # A rough span stays soft; exact dates are the traveller's, never promoted from one.
            "flexible": trip.trip.mode == "rough",
        }
        if trip.trip.mode == "exact":
            window["nights"] = (trip.trip.end - trip.trip.start).days
        value["window"] = window
    return value


def _exchanged_user_id(payload: object) -> str | None:
    """The user id in a successful exchange, or None when it names none as a UUID.

    What a success looks like has not been seen yet: it needs a real sign-in. So this accepts the
    two shapes Paradigm uses elsewhere — `user_id` at the top, or a `user` object with an `id` —
    and nothing looser. Tighten it to the observed shape once one has been seen.
    """

    if not isinstance(payload, dict):
        return None
    candidate = payload.get("user_id")
    user = payload.get("user")
    if candidate is None and isinstance(user, dict):
        candidate = user.get("id")
    if not isinstance(candidate, str):
        return None
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return None


def _user(user_id: str) -> str:
    try:
        return str(uuid.UUID(user_id))
    except ValueError as exc:
        raise OfselfUnavailable("An Ofself user id must be a UUID") from exc


def _citizenships_of(value_json: dict[str, object]) -> list[object]:
    """A node's `citizenships`, or nothing where the field is absent, hidden or not a list."""

    citizenships = value_json.get("citizenships")
    return list(citizenships) if isinstance(citizenships, list) else []


def _defaults(
    citizenships: PassportNationalities,
    documents: list[Node],
    plans: list[Node],
    places: list[Node],
    *,
    today: date,
) -> TravellerDefaults:
    unrecognised = list(citizenships.unrecognised)
    encrypted = citizenships.encrypted_values
    passports: dict[str, OfselfPassport] = {}
    withheld: list[WithheldPassport] = []
    residences: list[OfselfResidence] = []
    other_holders = 0

    def country(value: object) -> str | None:
        nonlocal encrypted
        if not isinstance(value, str) or not value.strip():
            return None
        if value.startswith(ENCRYPTED_VALUE_PREFIX):
            encrypted += 1
            return None
        try:
            return normalise_country(value)
        except ValueError:
            if value not in unrecognised:
                unrecognised.append(value)
            return None

    for _, document in documents:
        if _text(document.get("status")) in RETIRED_DOCUMENT_STATUSES:
            continue
        # Absent means the account holder; anyone else's document is not this traveller's to use.
        if document.get("holder_ref"):
            other_holders += 1
            continue
        kind = _text(document.get("kind"))
        expires_at = _date(document.get("expires_at"))
        expired = _text(document.get("status")) == "expired" or (
            expires_at is not None and expires_at < today
        )
        attested = _attested(document, "expires_at")
        label = _text(document.get("label"))

        if kind == "passport":
            nationality = country(document.get("nationality"))
            code = (_text(document.get("document_code")) or "").upper()
            if code and code not in ORDINARY_PASSPORT_CODES:
                withheld.append(
                    WithheldPassport(nationality=nationality, label=label, document_code=code)
                )
                continue
            if nationality is None:
                continue
            candidate = OfselfPassport(
                nationality=nationality,
                label=label,
                expires_at=expires_at,
                expiry_attested=attested,
                expired=expired,
            )
            if _preferred(candidate, passports.get(nationality)):
                passports[nationality] = candidate
        elif kind == "residence_permit":
            issued_by = country(document.get("issuing_state"))
            if issued_by is None:
                continue
            grants = document.get("grants")
            permit_class = _text(grants.get("class")) if isinstance(grants, dict) else None
            residences.append(
                OfselfResidence(
                    country=issued_by,
                    label=label,
                    permit_class=permit_class,
                    expires_at=expires_at,
                    expiry_attested=attested,
                    expired=expired,
                )
            )

    # A citizenship with no passport recorded is still offered, as it was before the travel
    # schemas: the passport exists even where Ofself holds no record of the document.
    for nationality in citizenships.nationalities:
        passports.setdefault(
            nationality, OfselfPassport(nationality=nationality, from_document=False)
        )

    place_countries = _place_countries(places)
    travel_plans: list[OfselfTravelPlan] = []
    unresolved = 0
    for _, plan in plans:
        if _text(plan.get("status")) in {"committed", "abandoned"}:
            continue
        if _text(plan.get("commitment")) == "abandoned":
            continue
        label = _text(plan.get("label"))
        if label is None:
            continue
        candidates: list[OfselfPlanCandidate] = []
        raw_candidates = plan.get("candidates")
        for raw in raw_candidates if isinstance(raw_candidates, list) else []:
            if not isinstance(raw, dict) or _text(raw.get("ruled_out_reason")):
                continue
            destination = place_countries.get(_text(raw.get("place_ref")) or "")
            if destination is None:
                unresolved += 1
                continue
            recorded = _text(raw.get("purpose"))
            researched = (
                cast(TravelPurpose, recorded) if recorded in get_args(TravelPurpose) else None
            )
            candidates.append(
                OfselfPlanCandidate(
                    destination=destination,
                    destination_slug=_slug(destination),
                    purpose=researched,
                    recorded_purpose=recorded if researched is None else None,
                )
            )
        if not candidates:
            continue
        raw_window = plan.get("window")
        window: dict[str, object] = raw_window if isinstance(raw_window, dict) else {}
        travel_plans.append(
            OfselfTravelPlan(
                label=label,
                earliest=_date(window.get("earliest")),
                latest=_date(window.get("latest")),
                candidates=candidates,
            )
        )

    return TravellerDefaults(
        passports=list(passports.values()),
        withheld_passports=withheld,
        residences=residences,
        plans=travel_plans,
        other_holders=other_holders,
        unresolved_candidates=unresolved,
        unrecognised=unrecognised,
        encrypted_values=encrypted,
    )


def _shared(
    documents: list[Node],
    stays: list[Node],
    obligations: list[Node],
    places: list[Node],
    *,
    today: date,
) -> SharedDetails:
    def country(value: object) -> str | None:
        # Unreadable values are left out here: the form's defaults already report them.
        if not isinstance(value, str) or not value.strip():
            return None
        if value.startswith(ENCRYPTED_VALUE_PREFIX):
            return None
        try:
            return normalise_country(value)
        except ValueError:
            return None

    shared_documents: list[SharedDocument] = []
    date_of_birth: date | None = None
    date_of_birth_attested = False
    for _, document in documents:
        if document.get("holder_ref"):
            continue
        kind = _text(document.get("kind"))
        if kind is None:
            continue
        code = (_text(document.get("document_code")) or "").upper()
        if kind == "passport" and code and code not in ORDINARY_PASSPORT_CODES:
            continue
        status = _text(document.get("status"))
        if status in {"lost", "stolen"}:
            continue
        expires_at = _date(document.get("expires_at"))
        grants = document.get("grants")
        born = _date(document.get("date_of_birth"))
        if born is not None and (date_of_birth is None or _attested(document, "date_of_birth")):
            date_of_birth = born
            date_of_birth_attested = _attested(document, "date_of_birth")
        shared_documents.append(
            SharedDocument(
                kind=kind,
                nationality=country(document.get("nationality")),
                issuing_state=country(document.get("issuing_state")),
                grants=_text(grants.get("class")) if isinstance(grants, dict) else None,
                issued_at=_date(document.get("issued_at")),
                issued_at_read_off_document=_attested(document, "issued_at"),
                expires_at=expires_at,
                expires_at_read_off_document=_attested(document, "expires_at"),
                expired=status == "expired" or (expires_at is not None and expires_at < today),
                status=status,
            )
        )

    place_countries = _place_countries(places)
    shared_stays: list[SharedStay] = []
    for _, stay in stays:
        # Allowances are per person, so another traveller's stays are never this one's history.
        if stay.get("traveller_ref"):
            continue
        stayed_in = place_countries.get(_text(stay.get("place_ref")) or "")
        if stayed_in is None:
            continue
        exempt = stay.get("exempt")
        shared_stays.append(
            SharedStay(
                country=stayed_in,
                entered_at=_date(stay.get("entry_at")),
                exited_at=_date(stay.get("exit_at")),
                exempt=exempt if isinstance(exempt, bool) else None,
                purpose=_text(stay.get("purpose")),
                self_declared=_text(stay.get("provenance")) in {None, "self_declared"},
            )
        )

    applications = [
        SharedApplication(
            kind=_text(obligation.get("kind")),
            outcome=_text(obligation.get("state")),
            decided_at=_date(obligation.get("decided_at")),
        )
        for _, obligation in obligations
        if _text(obligation.get("kind")) or _text(obligation.get("state"))
    ]

    return SharedDetails(
        date_of_birth=date_of_birth,
        date_of_birth_read_off_document=date_of_birth_attested,
        documents=shared_documents,
        stays=sorted(shared_stays, key=lambda stay: stay.entered_at or date.min),
        applications=applications,
    )


def _preferred(candidate: OfselfPassport, current: OfselfPassport | None) -> bool:
    """Of two passports of one nationality, offer the one still valid, then the later expiry."""

    if current is None:
        return True
    if candidate.expired != current.expired:
        return not candidate.expired
    return (candidate.expires_at or date.min) > (current.expires_at or date.min)


def _place_countries(places: list[Node]) -> dict[str, str]:
    """Each place id the grant shows, mapped to the alpha-2 country it sits in, where it resolves.

    A place carries its own `country_code` when known; otherwise its `parent_ref` is walked upward
    — Catral, Alicante, Spain — to a place that does.
    """

    by_id = {identifier: place for identifier, place in places if identifier}

    resolved: dict[str, str] = {}
    for identifier in by_id:
        current: dict[str, object] | None = by_id[identifier]
        for _ in range(MAXIMUM_PLACE_DEPTH):
            if current is None:
                break
            code = _text(current.get("country_code"))
            if code:
                try:
                    resolved[identifier] = normalise_country(code)
                except ValueError:
                    pass
                break
            current = by_id.get(_text(current.get("parent_ref")) or "")
    return resolved


def _slug(code: str) -> str:
    country = get_country_registry().get(code)
    return country.slug if country is not None else code.lower()


def _attested(document: dict[str, object], field: str) -> bool:
    provenance = document.get("field_provenance")
    if not isinstance(provenance, dict):
        return False
    entry = provenance.get(field)
    return isinstance(entry, dict) and entry.get("verification") in ATTESTED_VERIFICATIONS


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _normalise(values: list[object]) -> PassportNationalities:
    nationalities: list[str] = []
    unrecognised: list[str] = []
    encrypted = 0
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        if value.startswith(ENCRYPTED_VALUE_PREFIX):
            encrypted += 1
            continue
        try:
            code = normalise_country(value)
        except ValueError:
            if value not in unrecognised:
                unrecognised.append(value)
            continue
        if code not in nationalities:
            nationalities.append(code)
    return PassportNationalities(
        nationalities=nationalities, unrecognised=unrecognised, encrypted_values=encrypted
    )


def _refusal(response: httpx.Response) -> OfselfError:
    """Name why Paradigm refused, from the error code in the body rather than the status alone."""

    code, message = _error_of(response)
    if code in AUTHORIZATION_CODES:
        return OfselfAuthorizationLost(
            code, message or "This Ofself account has no active authorisation for this app"
        )
    if response.status_code == httpx.codes.UNAUTHORIZED:
        return OfselfUnavailable(
            f"Ofself refused this app's API key ({code or 'HTTP 401'}); check PARADIGM_API_KEY"
        )
    detail = f"{code}: {message}" if code and message else code or f"HTTP {response.status_code}"
    return OfselfUnavailable(f"Ofself could not provide the identity ({detail})")


def _error_of(response: httpx.Response) -> tuple[str | None, str | None]:
    """The code and message from either envelope Paradigm documents: under `error`, or flat."""

    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    nested = body.get("error")
    envelope = nested if isinstance(nested, dict) else body
    code = envelope.get("code")
    message = envelope.get("message")
    return (
        code if isinstance(code, str) else None,
        message if isinstance(message, str) else None,
    )
