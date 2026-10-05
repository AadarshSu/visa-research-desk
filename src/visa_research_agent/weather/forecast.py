"""A forecast for the days of the trip that fall inside MET Norway's window (DECISIONS entry 261).

MET Norway's Locationforecast is free under CC BY 4.0, answers our own identified user agent, and
its `robots.txt` disallows nothing to it. Its terms ask a client to identify itself, to send at most
four decimals of latitude and longitude, and to reuse a response until it expires; this does all
three, holding each place's forecast in memory until the `Expires` header it came with.

A day here is the destination city's own calendar day, in its time zone from GeoNames. A day's high
and low are the warmest and coldest of the forecast's own time steps — hourly for the first two or
three days, six-hourly after — so they are the forecast's, not a measured extreme.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from visa_research_agent.domain.models import StrictModel
from visa_research_agent.research.robots import RobotsCache, RobotsVerdict
from visa_research_agent.weather.places import City

FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
FORECAST_PAGE = "https://www.yr.no/en/forecast/daily-table/{latitude},{longitude}"
# The longest MET Norway forecasts reach; past it the panel shows averages.
FORECAST_DAYS = 9
DEFAULT_LIFETIME = timedelta(minutes=30)

# MET Norway's symbol codes, by their stem, in the words the panel uses.
SYMBOL_WORDS = {
    "clearsky": "Clear",
    "fair": "Mostly clear",
    "partlycloudy": "Partly cloudy",
    "cloudy": "Cloudy",
    "fog": "Fog",
    "lightrain": "Light rain",
    "rain": "Rain",
    "heavyrain": "Heavy rain",
    "lightrainshowers": "Light showers",
    "rainshowers": "Showers",
    "heavyrainshowers": "Heavy showers",
    "sleet": "Sleet",
    "lightsleet": "Light sleet",
    "heavysleet": "Heavy sleet",
    "snow": "Snow",
    "lightsnow": "Light snow",
    "heavysnow": "Heavy snow",
}


class DayForecast(StrictModel):
    day: date
    high_c: float
    low_c: float
    rain_mm: float
    summary: str


def describe(symbol: str) -> str:
    stem = symbol.split("_")[0]
    if "thunder" in stem:
        return "Thunderstorms"
    if stem in SYMBOL_WORDS:
        return SYMBOL_WORDS[stem]
    for key in sorted(SYMBOL_WORDS, key=len, reverse=True):
        if stem.startswith(key):
            return SYMBOL_WORDS[key]
    return stem.capitalize()


def daily_forecast(payload: dict[str, Any], timezone: str) -> list[DayForecast]:
    """MET Norway's time steps folded into the city's own calendar days."""

    zone = ZoneInfo(timezone)
    temperatures: dict[date, list[float]] = {}
    rain: dict[date, float] = {}
    midday_symbol: dict[date, tuple[int, str]] = {}
    hourly_until: datetime | None = None
    for step in payload["properties"]["timeseries"]:
        moment = datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        local = moment.astimezone(zone)
        data = step["data"]
        temperatures.setdefault(local.date(), []).append(
            float(data["instant"]["details"]["air_temperature"])
        )
        if "next_1_hours" in data:
            rain[local.date()] = rain.get(local.date(), 0.0) + float(
                data["next_1_hours"]["details"].get("precipitation_amount", 0.0)
            )
            hourly_until = moment + timedelta(hours=1)
        elif "next_6_hours" in data and (hourly_until is None or moment >= hourly_until):
            # Six-hour steps begin where the hourly ones end; counting both would double the rain.
            rain[local.date()] = rain.get(local.date(), 0.0) + float(
                data["next_6_hours"]["details"].get("precipitation_amount", 0.0)
            )
        summary = data.get("next_6_hours") or data.get("next_1_hours")
        if summary:
            # The symbol for the period nearest midday stands for the day.
            distance = abs(local.hour - 12)
            held = midday_symbol.get(local.date())
            if held is None or distance < held[0]:
                midday_symbol[local.date()] = (distance, summary["summary"]["symbol_code"])
    days = []
    for day, values in sorted(temperatures.items()):
        if len(values) < 3 or day not in midday_symbol:
            continue  # a sliver of a day at either end of the forecast says nothing about it
        days.append(
            DayForecast(
                day=day,
                high_c=round(max(values), 1),
                low_c=round(min(values), 1),
                rain_mm=round(rain.get(day, 0.0), 1),
                summary=describe(midday_symbol[day][1]),
            )
        )
    return days


@dataclass
class _Held:
    payload: dict[str, Any]
    expires: datetime


class ForecastClient:
    """MET Norway's forecast for a place, reused until it expires, as its terms ask."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        robots: RobotsCache,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.robots = robots
        self.now = now
        self._held: dict[tuple[float, float], _Held] = {}
        self._lock = asyncio.Lock()

    async def forecast(self, city: City) -> list[DayForecast] | None:
        """The city's forecast days, or None when MET Norway could not be read."""

        key = (round(city.latitude, 4), round(city.longitude, 4))
        async with self._lock:
            held = self._held.get(key)
            if held is None or held.expires <= self.now():
                # Read and obeyed as for every page the app fetches (entry 36).
                verdict = await self.robots.verdict(self.client, FORECAST_URL)
                if verdict is not RobotsVerdict.ALLOWED:
                    return None
                try:
                    response = await self.client.get(
                        FORECAST_URL, params={"lat": key[0], "lon": key[1]}
                    )
                except httpx.HTTPError:
                    return None
                if response.status_code != 200:
                    return None
                held = _Held(response.json(), self._expiry(response))
                self._held[key] = held
        return daily_forecast(held.payload, city.timezone)

    def _expiry(self, response: httpx.Response) -> datetime:
        header = response.headers.get("expires")
        if header:
            try:
                return parsedate_to_datetime(header)
            except (TypeError, ValueError):
                pass
        return self.now() + DEFAULT_LIFETIME
