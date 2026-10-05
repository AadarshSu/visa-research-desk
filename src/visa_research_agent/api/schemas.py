"""Request and lightweight response models specific to the HTTP API."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visa_research_agent.api.countries import normalise_country
from visa_research_agent.config.regions import normalise_region
from visa_research_agent.domain.models import TravellerProfile, TravelPurpose


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"


class DestinationSummary(ApiModel):
    slug: str
    name: str
    route_type: Literal["national", "schengen_member"]
    status: Literal["planned", "available"]


class DestinationsResponse(ApiModel):
    destinations: list[DestinationSummary]


class RegionsResponse(ApiModel):
    country: str
    regions: list[str]
    """Empty for a country the reference data divides into no regions; the form then hides the
    field."""


class TravellerRequest(ApiModel):
    """The traveller a request is asking about.

    Only the passport, the country applied from and the purpose are required: those three select
    the guidance. The rest is optional because a plan that does not use a detail should not ask
    for it.
    """

    passport_nationality: str = Field(min_length=1)
    country_of_residence: str = Field(min_length=1)
    travel_purpose: TravelPurpose = "tourism"
    region_of_residence: str | None = None
    residence_status: str | None = None
    residence_permission_expiry: date | None = None

    _normalise = field_validator("passport_nationality", "country_of_residence")(normalise_country)

    @model_validator(mode="after")
    def normalise_region(self) -> "TravellerRequest":
        """A region must be one the reference data holds for that country; never guessed."""

        if self.region_of_residence is not None and not self.region_of_residence.strip():
            self.region_of_residence = None
        if self.region_of_residence is not None:
            self.region_of_residence = normalise_region(
                self.country_of_residence, self.region_of_residence
            )
        return self

    def to_profile(self) -> TravellerProfile:
        return TravellerProfile(
            passport_nationality=self.passport_nationality,
            passport_type="ordinary",
            country_of_residence=self.country_of_residence,
            travel_purpose=self.travel_purpose,
            region_of_residence=self.region_of_residence,
            residence_status=self.residence_status,
            residence_permission_expiry=self.residence_permission_expiry,
        )


class VisaPlanRequest(ApiModel):
    destination: str = Field(min_length=1)
    traveller: TravellerRequest | None = None
    """Absent means the default profile. The interface opens on one, and the offline Singapore
    fixture was recorded against it."""

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        return value.strip().lower()


class ErrorDetail(ApiModel):
    message: str
    supported_destinations: list[str] | None = None
