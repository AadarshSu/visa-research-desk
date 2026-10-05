"""The traveller used when a request does not describe one.

The profile is no longer fixed: any corridor can be asked for. This one remains as the default the
interface opens on, and as the profile the Singapore offline fixture was recorded against, so the
deterministic regression baseline keeps working without every caller having to spell it out.
"""

from datetime import date

from visa_research_agent.domain.models import TravellerProfile

DEFAULT_TRAVELLER_PROFILE = TravellerProfile(
    passport_nationality="IN",
    passport_type="ordinary",
    country_of_residence="GB",
    region_of_residence="Scotland",
    residence_status="Graduate visa",
    residence_permission_expiry=date(2027, 12, 23),
    travel_purpose="tourism",
)
