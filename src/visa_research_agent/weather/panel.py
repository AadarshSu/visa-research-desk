"""What the weather panel shows for a city and the traveller's dates (DECISIONS entry 261).

The days of the trip inside MET Norway's window get its forecast; every month of the trip beyond
it gets the city's averages, labelled as averages with their station and years. With no dates there
is no panel — weather is for the trip, not for a country in general.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from visa_research_agent.domain.models import StrictModel
from visa_research_agent.weather.climate import CityNormals
from visa_research_agent.weather.forecast import FORECAST_PAGE, DayForecast
from visa_research_agent.weather.places import City

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
MAXIMUM_MONTHS = 6


class MonthAverage(StrictModel):
    month: int
    name: str
    high_c: float | None
    low_c: float | None
    rain_mm: float | None


class AveragesSource(StrictModel):
    station: str
    distance_km: float
    first_year: int
    last_year: int


class WeatherPanel(StrictModel):
    city: str
    capital: bool
    cities: list[str]
    capital_city: str | None = None
    """The country's capital, where GeoNames marks one, so the picker can say which it is."""

    forecast: list[DayForecast]
    forecast_link: str | None = None
    """MET Norway's own forecast page for the place, credited on the panel."""

    trip_days: int | None = None
    """For exact dates: how many days the trip has, so the panel can say how many are forecast."""

    averages: list[MonthAverage]
    averages_source: AveragesSource | None = None


def months_between(first: date, last: date) -> list[int]:
    """The calendar months a span touches, in order, each once."""

    months: list[int] = []
    cursor = first.replace(day=1)
    while cursor <= last and len(months) < 12:
        if cursor.month not in months:
            months.append(cursor.month)
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months


def forecast_page(city: City) -> str:
    return FORECAST_PAGE.format(
        latitude=round(city.latitude, 4), longitude=round(city.longitude, 4)
    )


def weather_panel(
    city: City,
    cities: list[City],
    start: date,
    end: date,
    *,
    exact: bool,
    forecast: list[DayForecast] | None,
    normals: CityNormals | None,
    now: datetime,
) -> WeatherPanel | None:
    """The panel for a trip from `start` to `end`, or None when there is nothing to show."""

    today = now.astimezone(ZoneInfo(city.timezone)).date()
    in_trip = [day for day in forecast or [] if start <= day.day <= end] if exact else []
    covered_until = in_trip[-1].day if in_trip else None
    beyond = (covered_until + timedelta(days=1)) if covered_until else max(start, today)
    averages: list[MonthAverage] = []
    if normals is not None and beyond <= end:
        for month in months_between(beyond, end)[:MAXIMUM_MONTHS]:
            normal = normals.months.get(month)
            if normal is None or normal.high_c is None:
                continue
            averages.append(
                MonthAverage(
                    month=month,
                    name=MONTH_NAMES[month - 1],
                    high_c=normal.high_c,
                    low_c=normal.low_c,
                    rain_mm=normal.rain_mm,
                )
            )
    if not in_trip and not averages:
        return None
    return WeatherPanel(
        city=city.name,
        capital=city.capital,
        cities=[place.name for place in cities],
        capital_city=next((place.name for place in cities if place.capital), None),
        forecast=in_trip,
        forecast_link=forecast_page(city) if in_trip else None,
        trip_days=(end - start).days + 1 if exact else None,
        averages=averages,
        averages_source=(
            AveragesSource(
                station=normals.station_name,
                distance_km=normals.distance_km,
                first_year=normals.first_year,
                last_year=normals.last_year,
            )
            if averages and normals is not None
            else None
        ),
    )
