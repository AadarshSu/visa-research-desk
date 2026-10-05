"""Weather for the trip's dates, beside the plan and never an input to it (DECISIONS entry 261).

Nothing here reaches the network: MET Norway and NOAA are answered by `httpx.MockTransport`.
"""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from visa_research_agent.research.robots import RobotsCache
from visa_research_agent.weather.climate import (
    CityNormals,
    MonthNormal,
    monthly_normals,
    nearest_station,
)
from visa_research_agent.weather.forecast import DayForecast, ForecastClient, daily_forecast
from visa_research_agent.weather.panel import months_between, weather_panel
from visa_research_agent.weather.places import City, cities_from_geonames

TOKYO = City(
    name="Tokyo", latitude=35.6895, longitude=139.6917, timezone="Asia/Tokyo", capital=True
)
NOW = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)


def geonames_row(name: str, lat: float, lon: float, feature: str, population: int) -> list[str]:
    row = [""] * 19
    row[1], row[4], row[5], row[7], row[8] = name, str(lat), str(lon), feature, "US"
    row[14], row[17] = str(population), "America/New_York"
    return row


# --- Places ------------------------------------------------------------------------------------


def test_the_capital_comes_first_and_a_district_of_a_listed_city_is_dropped() -> None:
    rows = [
        geonames_row("New York City", 40.71, -74.01, "PPL", 8_000_000),
        geonames_row("Brooklyn", 40.65, -73.95, "PPLA2", 2_500_000),
        geonames_row("Washington", 38.90, -77.04, "PPLC", 700_000),
        geonames_row("Chicago", 41.85, -87.65, "PPLA2", 2_700_000),
        geonames_row("Midtown", 40.75, -73.98, "PPLX", 100_000),
    ]
    cities = cities_from_geonames(rows, ["US"])["US"]
    assert [city.name for city in cities] == ["Washington", "New York City", "Chicago"]
    assert cities[0].capital


# --- Averages ----------------------------------------------------------------------------------


def station(identifier: str, lon: float, lat: float, types: dict[str, tuple[str, str]]) -> dict:  # type: ignore[type-arg]
    return {
        "location": {"coordinates": [lon, lat]},
        "stations": [
            {
                "id": identifier,
                "name": identifier,
                "dataTypes": [
                    {"id": key, "startDate": f"{start}-01-01", "endDate": f"{end}-12-31"}
                    for key, (start, end) in types.items()
                ],
            }
        ],
    }


def test_a_station_is_judged_by_what_it_still_reports() -> None:
    """Tokyo's own station stopped reporting maxima in 2022 — still enough years — while one that
    stopped in 1999 is never chosen, however near."""

    search = {
        "results": [
            station("STOPPED", 139.69, 35.69, {"TMAX": ("1951", "1999"), "PRCP": ("1951", "2025")}),
            station("TOKYO", 139.77, 35.68, {"TMAX": ("1951", "2022"), "PRCP": ("1951", "2025")}),
            station("FAR", 141.0, 37.0, {"TMAX": ("1951", "2025"), "PRCP": ("1951", "2025")}),
        ]
    }
    chosen = nearest_station(TOKYO, search)
    assert chosen is not None
    assert chosen[0] == "TOKYO"


def test_a_month_with_too_few_years_is_left_empty_not_called_typical() -> None:
    records = [{"DATE": f"{year}-04", "TMAX": "19.0", "PRCP": "120"} for year in range(2011, 2021)]
    records += [{"DATE": f"{year}-05", "TMAX": "23.0"} for year in range(2011, 2014)]
    months = monthly_normals(records)
    assert months[4].high_c == 19.0 and months[4].rain_mm == 120.0 and months[4].years == 10
    assert months[5].high_c is None


# --- The forecast ------------------------------------------------------------------------------


def step(
    moment: datetime, temperature: float, *, hourly: float | None = None, six: float | None = None
) -> dict:  # type: ignore[type-arg]
    data: dict = {"instant": {"details": {"air_temperature": temperature}}}  # type: ignore[type-arg]
    if hourly is not None:
        data["next_1_hours"] = {
            "summary": {"symbol_code": "rain"},
            "details": {"precipitation_amount": hourly},
        }
    if six is not None:
        data["next_6_hours"] = {
            "summary": {"symbol_code": "clearsky_day"},
            "details": {"precipitation_amount": six},
        }
    return {"time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}


def test_days_are_the_citys_own_and_rain_is_never_counted_twice() -> None:
    """Hourly steps carry a six-hour total too; adding both would double the rain."""

    start = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)  # midnight in Tokyo
    steps = [step(start + timedelta(hours=h), 15 + h, hourly=0.5, six=3.0) for h in range(12)]
    steps += [step(start + timedelta(hours=h), 20, six=2.0) for h in (12, 18)]
    days = daily_forecast({"properties": {"timeseries": steps}}, "Asia/Tokyo")

    assert [day.day for day in days] == [date(2026, 10, 7)]
    assert days[0].high_c == 26 and days[0].low_c == 15
    assert days[0].rain_mm == 12 * 0.5 + 2.0 + 2.0


@pytest.mark.anyio
async def test_a_forecast_is_reused_until_it_expires_and_robots_txt_is_obeyed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        assert request.url.params["lat"] == "35.6895"
        return httpx.Response(
            200,
            json={"properties": {"timeseries": []}},
            headers={"expires": "Tue, 06 Oct 2026 04:00:00 GMT"},
        )

    clock = [NOW]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        forecasts = ForecastClient(client, RobotsCache(user_agent="test"), now=lambda: clock[0])
        assert await forecasts.forecast(TOKYO) == []
        assert await forecasts.forecast(TOKYO) == []
        clock[0] = NOW + timedelta(hours=2)
        await forecasts.forecast(TOKYO)

    assert [path for path in calls if path != "/robots.txt"] == [
        "/weatherapi/locationforecast/2.0/compact",
        "/weatherapi/locationforecast/2.0/compact",
    ]


@pytest.mark.anyio
async def test_a_disallowed_forecast_is_never_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError("fetched a page robots.txt disallows")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        forecasts = ForecastClient(client, RobotsCache(user_agent="test"), now=lambda: NOW)
        assert await forecasts.forecast(TOKYO) is None


# --- The panel ---------------------------------------------------------------------------------

NORMALS = CityNormals(
    city="Tokyo",
    station_id="JA000047662",
    station_name="TOKYO, JA",
    distance_km=7.2,
    first_year=2011,
    last_year=2025,
    months={
        10: MonthNormal(high_c=22.0, rain_mm=200.0, years=14),
        11: MonthNormal(high_c=17.0, low_c=9.0, rain_mm=90.0, years=14),
    },
)


def day(value: date) -> DayForecast:
    return DayForecast(day=value, high_c=22, low_c=15, rain_mm=0, summary="Clear")


def test_days_inside_the_window_are_forecast_and_the_rest_are_averages() -> None:
    forecast = [day(date(2026, 10, 6) + timedelta(days=n)) for n in range(9)]
    panel = weather_panel(
        TOKYO, [TOKYO], date(2026, 10, 10), date(2026, 11, 3),
        exact=True, forecast=forecast, normals=NORMALS, now=NOW,
    )  # fmt: skip
    assert panel is not None
    assert [d.day.day for d in panel.forecast] == [10, 11, 12, 13, 14]
    assert panel.trip_days == 25
    assert [m.name for m in panel.averages] == ["October", "November"]
    assert panel.averages_source is not None and panel.averages_source.distance_km == 7.2


def test_a_rough_span_never_gets_a_forecast() -> None:
    panel = weather_panel(
        TOKYO, [TOKYO], date(2026, 10, 1), date(2026, 11, 30),
        exact=False, forecast=[day(date(2026, 10, 7))], normals=NORMALS, now=NOW,
    )  # fmt: skip
    assert panel is not None
    assert panel.forecast == [] and panel.forecast_link is None
    assert [m.month for m in panel.averages] == [10, 11]


def test_nothing_to_show_is_no_panel() -> None:
    assert (
        weather_panel(
            TOKYO,
            [TOKYO],
            date(2027, 4, 1),
            date(2027, 4, 5),
            exact=True,
            forecast=None,
            normals=None,
            now=NOW,
        )  # fmt: skip
        is None
    )


def test_months_between_spans_a_year_end() -> None:
    assert months_between(date(2026, 12, 20), date(2027, 2, 2)) == [12, 1, 2]
