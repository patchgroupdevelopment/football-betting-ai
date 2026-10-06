from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.config import ApiFootballConfig
from backend.schemas.api_football import parse_fixtures, parse_stat_value
from backend.services.errors import (
    MissingApiKeyError,
    PlanRestrictedError,
    ProviderAuthError,
    QuotaExhaustedError,
)
from backend.services.providers.api_football import ApiFootballClient, raise_for_api_errors
from backend.services.providers.odds_markets import normalize_bet
from backend.config import CacheTtlConfig
from backend.utils.timeutils import utcnow
from tests.conftest import FakeApiFootball, make_client
from tests.factories import envelope, fixture, team_statistics, ts


def test_missing_key_is_rejected_with_azerbaijani_message():
    with pytest.raises(MissingApiKeyError) as info:
        ApiFootballClient("", ApiFootballConfig(), CacheTtlConfig())
    assert info.value.user_message == "⚠️ FOOTBALL_API_KEY təyin edilməyib. Açarı .env faylına əlavə edin."


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        ({"token": "Error/Missing application key."}, ProviderAuthError),
        ({"requests": "You have reached the request limit for the day."}, QuotaExhaustedError),
        ({"plan": "Free plans do not have access to this season."}, PlanRestrictedError),
        ([{"plan": "Free plans do not have access to this season."}], PlanRestrictedError),
    ],
)
def test_api_errors_are_mapped(errors, expected):
    with pytest.raises(expected) as info:
        raise_for_api_errors(errors)
    assert "Free plans" not in info.value.user_message


def test_empty_errors_are_ok():
    raise_for_api_errors([])
    raise_for_api_errors({})
    raise_for_api_errors(None)


def test_rate_limit_error_waits_and_retries_once(fake_api):
    responses = iter(
        [envelope("status", [], errors={"rateLimit": "Too many requests."}), envelope("leagues", [])]
    )
    fake_api.route("leagues", lambda params: next(responses))
    sleeps: list[float] = []
    client = make_client(fake_api, sleeps=sleeps)
    assert client.leagues(country="Azerbaijan") == []
    assert len(fake_api.calls) == 2 and sleeps and sleeps[0] == pytest.approx(60, abs=1)


def test_quota_headers_are_tracked_and_reserve_is_respected():
    fake = FakeApiFootball(daily_limit=8)
    client = make_client(fake, config=ApiFootballConfig(daily_reserve=5, requests_per_minute=10_000))
    client.request("leagues", {"id": 1})
    client.request("leagues", {"id": 2})
    assert client.quota.daily_remaining == 6 and client.quota.daily_limit == 8
    client.request("leagues", {"id": 3})  # remaining now 5 == reserve
    with pytest.raises(QuotaExhaustedError):
        client.request("leagues", {"id": 4})
    assert len(fake.calls) == 3  # the blocked request never reached the API


def test_stale_quota_from_previous_day_does_not_block(fake_api):
    client = make_client(fake_api, config=ApiFootballConfig(daily_reserve=5))
    client.quota.daily_remaining = 0
    client.quota.updated_at = utcnow() - timedelta(days=1)
    client.request("leagues", {"id": 1})
    assert len(fake_api.calls) == 1


def test_cache_avoids_second_request(db, fake_api):
    client = make_client(fake_api, db=db)
    client.request("leagues", {"country": "Spain"}, ttl=60)
    client.request("leagues", {"country": "Spain"}, ttl=60)
    assert len(fake_api.calls) == 1


def test_fixture_ids_are_batched_by_twenty(fake_api):
    client = make_client(fake_api)
    client.fixtures_by_ids(list(range(1, 26)))
    batches = [params["ids"].split("-") for path, params in fake_api.calls if path == "fixtures"]
    assert [len(batch) for batch in batches] == [20, 5]


def test_injuries_fall_back_to_per_fixture_when_ids_unsupported(fake_api):
    fake_api.route(
        "injuries", envelope("injuries", [], errors={"ids": "The Ids field is not supported."}), when=lambda p: "ids" in p
    )
    client = make_client(fake_api)
    client.injuries_for_fixtures([11, 12])
    per_fixture = [params for path, params in fake_api.calls if path == "injuries" and "fixture" in params]
    assert [p["fixture"] for p in per_fixture] == ["11", "12"]


def test_odds_pagination(fake_api):
    fake_api.route("odds", envelope("odds", [], paging=(1, 2)), when=lambda p: "page" not in p)
    make_client(fake_api).odds_for_fixture(1001)
    assert fake_api.count("odds") == 2


def test_account_status_updates_quota(fake_api):
    fake_api.route(
        "status",
        envelope(
            "status",
            {"subscription": {"plan": "Free", "active": True}, "requests": {"current": 30, "limit_day": 100}},
        ),
    )
    client = make_client(fake_api)
    status = client.account_status()
    assert (status.plan, status.requests_current, status.requests_limit_day) == ("Free", 30, 100)
    assert client.quota.daily_remaining == 70
    assert client.quota.requests_made == 0  # /status does not count against the quota


def test_parse_fixture_with_statistics():
    item = fixture(
        1, 39, 50, 42, kickoff=ts(2026, 10, 5, 15, 30), status="FT", goals=(2, 1),
        statistics=[team_statistics(50), team_statistics(42, xg=None)],
    )
    (dto,) = parse_fixtures(envelope("fixtures", [item, {"fixture": {}}]))  # malformed item skipped
    assert dto.is_finished and dto.kickoff_utc.hour == 15
    home, away = dto.statistics
    assert home.values["possession"] == 55.0 and home.values["xg"] == 1.45 and home.values["corners"] == 6
    assert isinstance(home.values["shots_total"], int) and home.values["red_cards"] is None
    assert "xg" not in away.values


@pytest.mark.parametrize(
    ("raw", "expected"), [("55%", 55.0), ("1.76", 1.76), (7, 7.0), (None, None), ("", None), ("n/a", None)]
)
def test_parse_stat_value(raw, expected):
    assert parse_stat_value(raw) == expected


@pytest.mark.parametrize(
    ("bet_name", "value", "expected"),
    [
        ("Match Winner", "Home", ("1X2", "1", None)),
        ("Match Winner", "Draw", ("1X2", "X", None)),
        ("Home/Away", "Away", ("DNB", "2", None)),
        ("Double Chance", "Draw/Away", ("DC", "X2", None)),
        ("Goals Over/Under", "Over 2.5", ("OU", "OVER", 2.5)),
        ("Goals Over/Under First Half", "Under 0.5", ("OU_1H", "UNDER", 0.5)),
        ("Both Teams Score", "No", ("BTTS", "NO", None)),
        ("Asian Handicap", "Home -1.5", ("AH", "1", -1.5)),
        ("Asian Handicap", "Away +0.25", ("AH", "2", 0.25)),
        ("Total - Home", "Over 1.5", ("TEAM_TOTAL_HOME", "OVER", 1.5)),
        ("Corners Over Under", "Over 9.5", ("CORNERS_OU", "OVER", 9.5)),
        ("Cards Over/Under", "Under 4.5", ("CARDS_OU", "UNDER", 4.5)),
        ("Exact Score", "1:0", None),
        ("Match Winner", "Nobody", None),
    ],
)
def test_normalize_bet(bet_name, value, expected):
    assert normalize_bet(bet_name, value) == expected


def test_fixtures_by_date_sends_timezone(fake_api):
    make_client(fake_api).fixtures_by_date(date(2026, 10, 5), "Asia/Baku")
    assert fake_api.calls == [("fixtures", {"date": "2026-10-05", "timezone": "Asia/Baku"})]
