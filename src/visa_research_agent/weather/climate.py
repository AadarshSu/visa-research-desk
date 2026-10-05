"""What a month is usually like in a city: averages from NOAA station records, built offline.

A forecast exists only about nine days ahead; past that the panel shows the averages for the
months of the trip, and says so (DECISIONS entry 261). They come from NOAA's Global Summary of the
Month — public domain, and `robots.txt` permits its access service, where Open-Meteo's and NASA
POWER's APIs are disallowed to every client and so may not be used (entry 36).

For each city in `destination_cities.yaml` the build picks the nearest station with temperature
and rainfall records running to recent years, averages each calendar month over
`FIRST_YEAR`–`LAST_YEAR`, and commits the result with the station, its distance and the years
used. A month with fewer than `MINIMUM_YEARS` observations is left empty rather than averaged from
two or three years and shown as typical.
"""

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import Field

from visa_research_agent.config.loader import config_path
from visa_research_agent.domain.models import StrictModel
from visa_research_agent.weather.places import City

NORMALS_FILENAME = "climate_normals.yaml"
SEARCH_URL = "https://www.ncei.noaa.gov/access/services/search/v1/data"
DATA_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
STATION_URL = "https://www.ncei.noaa.gov/access/past-weather/{latitude},{longitude}"
DATASET = "global-summary-of-the-month"
# Thirty years, about the span of a WMO standard normal. 2011–2025 left Beijing, Singapore and Oslo
# with too few years of monthly maxima in NOAA's summaries (2026-10-06).
FIRST_YEAR = 1996
LAST_YEAR = 2025
MINIMUM_YEARS = 5
# The station listing overstates what the summaries hold, so the nearest few are tried in turn.
STATIONS_TRIED = 3
# How far from the city a station may be. Half a degree is about 55 km of latitude: far enough to
# reach a city's airport, near enough that it is still that city's weather.
SEARCH_DEGREES = 0.5
MAXIMUM_DISTANCE_KM = 60.0


class MonthNormal(StrictModel):
    high_c: float | None = None
    """Mean of the month's average daily maximum temperature, °C."""

    low_c: float | None = None
    rain_mm: float | None = None
    """Mean monthly precipitation total, mm."""

    years: int = 0
    """How many years' records the temperatures were averaged over."""


class CityNormals(StrictModel):
    city: str
    station_id: str
    station_name: str
    distance_km: float
    first_year: int
    last_year: int
    months: dict[int, MonthNormal] = Field(default_factory=dict)


class ClimateNormals(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    countries: dict[str, list[CityNormals]] = Field(default_factory=dict)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = rlat2 - rlat1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _usable_types(station: dict[str, Any]) -> set[str]:
    """The data types a station reported for at least `MINIMUM_YEARS` of the averaging window."""

    usable = set()
    for item in station.get("dataTypes") or []:
        first = max(int(str(item.get("startDate", "0"))[:4] or 0), FIRST_YEAR)
        last = min(int(str(item.get("endDate", "0"))[:4] or 0), LAST_YEAR)
        if last - first + 1 >= MINIMUM_YEARS:
            usable.add(item["id"])
    return usable


def ranked_stations(city: City, search: dict[str, Any]) -> list[tuple[str, str, float]]:
    """Stations with maximum temperatures for enough of the averaging window, best first.

    Each data type has its own dates: Abu Dhabi's airport reports temperatures to 2025 and stopped
    reporting rain in 1998. So a station is judged type by type: maxima are required, and rain is
    preferred — a station without it is taken only when it is much nearer than one with it.
    Minima are not weighed: the monthly summaries often lack them where the station listing says
    otherwise (Yokohama, 2026-10-06), so the panel shows a low only where one was recorded.
    """

    ranked: list[tuple[float, str, str, float]] = []
    for result in search.get("results", []):
        stations = result.get("stations") or []
        location = (result.get("location") or {}).get("coordinates")
        if not stations or not location:
            continue
        usable = _usable_types(stations[0])
        if "TMAX" not in usable:
            continue
        longitude, latitude = location
        km = distance_km(city.latitude, city.longitude, latitude, longitude)
        if km > MAXIMUM_DISTANCE_KM:
            continue
        score = km + (0 if "PRCP" in usable else 30)
        ranked.append((score, stations[0]["id"], stations[0].get("name", ""), round(km, 1)))
    return [entry[1:] for entry in sorted(ranked)]


def nearest_station(city: City, search: dict[str, Any]) -> tuple[str, str, float] | None:
    ranked = ranked_stations(city, search)
    return ranked[0] if ranked else None


def monthly_normals(records: list[dict[str, str]]) -> dict[int, MonthNormal]:
    """Each calendar month's average over the years with a record for it."""

    by_month: dict[int, dict[str, list[float]]] = {}
    for record in records:
        month = int(record["DATE"][5:7])
        bucket = by_month.setdefault(month, {"TMAX": [], "TMIN": [], "PRCP": []})
        for key in bucket:
            value = record.get(key)
            if value:
                bucket[key].append(float(value))

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if len(values) >= MINIMUM_YEARS else None

    return {
        month: MonthNormal(
            high_c=mean(values["TMAX"]),
            low_c=mean(values["TMIN"]),
            rain_mm=mean(values["PRCP"]),
            years=len(values["TMAX"]),
        )
        for month, values in sorted(by_month.items())
    }


async def city_normals(
    client: httpx.AsyncClient, city: City, *, pause: Callable[[], Any]
) -> CityNormals | str:
    """One city's averages, or why there are none."""

    box = (
        f"{city.latitude + SEARCH_DEGREES},{city.longitude - SEARCH_DEGREES},"
        f"{city.latitude - SEARCH_DEGREES},{city.longitude + SEARCH_DEGREES}"
    )
    search = await client.get(
        SEARCH_URL,
        params={
            "dataset": DATASET,
            "bbox": box,
            "dataTypes": "TMAX",
            "startDate": f"{FIRST_YEAR}-01-01T00:00:00",
            "endDate": f"{LAST_YEAR}-12-31T23:59:59",
            "limit": 50,
        },
    )
    await pause()
    if search.status_code != 200:
        return f"station search answered HTTP {search.status_code}"
    candidates = ranked_stations(city, search.json())[:STATIONS_TRIED]
    if not candidates:
        return f"no station within {MAXIMUM_DISTANCE_KM:.0f} km has {MINIMUM_YEARS} years of maxima"
    reasons = []
    for station_id, station_name, km in candidates:
        data = await client.get(
            DATA_URL,
            params={
                "dataset": DATASET,
                "stations": station_id,
                "dataTypes": "TMAX,TMIN,PRCP",
                "startDate": f"{FIRST_YEAR}-01-01",
                "endDate": f"{LAST_YEAR}-12-31",
                "format": "json",
                "units": "metric",
            },
        )
        await pause()
        if data.status_code != 200:
            reasons.append(f"{station_name} answered HTTP {data.status_code}")
            continue
        records = data.json()
        months = monthly_normals(records)
        if not any(month.high_c is not None for month in months.values()):
            reasons.append(f"{station_name} has under {MINIMUM_YEARS} years of maxima")
            continue
        # The years the averages actually rest on, not the window asked for.
        years = sorted({int(r["DATE"][:4]) for r in records if r.get("TMAX")})
        return CityNormals(
            city=city.name,
            station_id=station_id,
            station_name=station_name,
            distance_km=km,
            first_year=years[0],
            last_year=years[-1],
            months=months,
        )
    return "; ".join(reasons)


async def build_normals(
    client: httpx.AsyncClient,
    cities: dict[str, list[City]],
    *,
    existing: ClimateNormals | None = None,
    rebuild: bool = False,
    concurrency: int = 4,
    pause_seconds: float = 1.0,
    sleep: Callable[[float], Any] = asyncio.sleep,
    on_city: Callable[[str, City, CityNormals | str], None] | None = None,
    write: Callable[[ClimateNormals], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ClimateNormals:
    """Every city's averages. A country already in `existing` is kept, not asked again, unless
    `rebuild`; each country is written as it completes, so an interruption keeps what was done."""

    countries = dict(existing.countries) if existing else {}
    limit = asyncio.Semaphore(max(1, concurrency))

    async def pause() -> None:
        await sleep(pause_seconds)

    async def one(code: str, city: City) -> CityNormals | str:
        async with limit:
            try:
                result: CityNormals | str = await city_normals(client, city, pause=pause)
            except httpx.HTTPError as exc:
                result = f"transport error ({type(exc).__name__})"
        if on_city is not None:
            on_city(code, city, result)
        return result

    for code, places in sorted(cities.items()):
        if code in countries and not rebuild:
            continue
        results = await asyncio.gather(*(one(code, city) for city in places))
        countries[code] = [result for result in results if isinstance(result, CityNormals)]
        if write is not None:
            write(ClimateNormals(generated_at=now(), countries=dict(sorted(countries.items()))))
    return ClimateNormals(generated_at=now(), countries=dict(sorted(countries.items())))


_HEADER = """\
# Each destination city's monthly averages, for the weather panel (DECISIONS entry 261).
#
# GENERATED by `visa-discover climate` — do not edit by hand. From NOAA NCEI's Global Summary of
# the Month: per city, the nearest station reporting temperature and rainfall, averaged per
# calendar month over the years shown. A month with under five years of records is left empty.
#
# Source: NOAA National Centers for Environmental Information, https://www.ncei.noaa.gov/ —
# U.S. Government work, public domain.
"""


def write_normals(normals: ClimateNormals, path: Path) -> None:
    payload = normals.model_dump(mode="json")
    path.write_text(
        _HEADER + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def load_normals(path: Path | None = None) -> ClimateNormals:
    with config_path(NORMALS_FILENAME, path).open(encoding="utf-8") as handle:
        return ClimateNormals.model_validate(yaml.safe_load(handle))


@lru_cache(maxsize=1)
def get_normals() -> ClimateNormals:
    return load_normals()


def normals_for(country: str, city: str) -> CityNormals | None:
    return next((row for row in get_normals().countries.get(country, []) if row.city == city), None)
