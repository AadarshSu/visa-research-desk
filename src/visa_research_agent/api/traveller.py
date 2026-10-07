"""Where the traveller a plan is researched for comes from.

Today there is one source, the request body the form posts. TODO item 55 adds a second, the
traveller's Ofself identity, and this seam exists so that adapter is one new module rather than
edits across the route, extraction and the page. Everything past the route depends on
`TravellerProfile` alone and never learns which source produced it.

The two sources differ in one way worth stating now, because it is easy to copy across by
mistake: **only the anonymous request body may fall back to the default traveller.** A request
naming nobody on the form gets the profile the interface opens on. An Ofself identity lacking a
deciding field must ask the traveller instead, or it would research someone else's corridor for
them without saying so (item 55, rule 4).
"""

import logging
from datetime import date
from typing import Protocol

from visa_research_agent.api.ofself import OfselfIdentity, OfselfUnavailable
from visa_research_agent.api.schemas import VisaPlanRequest
from visa_research_agent.config.traveller import DEFAULT_TRAVELLER_PROFILE
from visa_research_agent.domain.models import TravellerProfile


class TravellerSource(Protocol):
    """Supplies the traveller for one plan request."""

    async def traveller_for(self, request: VisaPlanRequest) -> TravellerProfile: ...


class RequestBodyTravellerSource:
    """The traveller as the request describes it, or the default when it describes nobody."""

    async def traveller_for(self, request: VisaPlanRequest) -> TravellerProfile:
        # The default is what the interface opens on and what the offline Singapore fixture was
        # recorded against.
        if request.traveller is None:
            return DEFAULT_TRAVELLER_PROFILE
        return request.traveller.to_profile()


logger = logging.getLogger(__name__)


class SharedDetailsTravellerSource:
    """The traveller the form describes, with what they shared through Ofself (entry 276).

    The corridor still comes from the form the traveller confirmed; Ofself adds only the details
    the plan call tailors to. **Only this source fills them** — a request body has no field for
    them, so nobody can supply another person's documents by posting them.

    A lost grant refuses the plan, as it does everywhere (TODO item 55): a traveller who withdrew
    access is never quietly served. Ofself being unreachable does not: the corridor-level plan is
    still correct for them, only less tailored, and that is logged rather than hidden.
    """

    def __init__(self, identity: OfselfIdentity, user_id: str) -> None:
        self.identity = identity
        self.user_id = user_id
        self.body = RequestBodyTravellerSource()

    async def traveller_for(self, request: VisaPlanRequest) -> TravellerProfile:
        profile = await self.body.traveller_for(request)
        try:
            shared = await self.identity.shared_details(self.user_id, today=date.today())
        except OfselfUnavailable as exc:
            logger.warning("Planning without the traveller's shared details: %s", exc)
            return profile
        if shared.is_empty():
            return profile
        return profile.model_copy(update={"shared_details": shared})
