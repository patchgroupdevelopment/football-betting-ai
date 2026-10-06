"""API-Football (api-sports.io, v3) client.

Responsibilities: authentication, quota tracking from response headers, mapping
API error payloads to typed errors, response caching, and typed endpoint
methods. API-Football reports most problems as HTTP 200 with a non-empty
``errors`` field, so that field is checked on every response.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from backend.config import ApiFootballConfig, CacheTtlConfig
from backend.schemas.api_football import (
    AccountStatus,
    FixtureDTO,
    FixtureOddsDTO,
    InjuryDTO,
    LeagueInfoDTO,
    OddsValueDTO,
    StandingDTO,
    parse_account_status,
    parse_fixtures,
    parse_injuries,
    parse_leagues,
    parse_odds,
    parse_standings,
)
from backend.services.cache import ResponseCache
from backend.services.errors import (
    MissingApiKeyError,
    PlanRestrictedError,
    ProviderAuthError,
    ProviderResponseError,
    QuotaExhaustedError,
    RateLimitedError,
)
from backend.services.http_client import HttpJsonClient, RateLimiter, RetryPolicy
from backend.utils.timeutils import utcnow

logger = logging.getLogger(__name__)

KEY_NAME = "FOOTBALL_API_KEY"
NO_CACHE = 0
IDS_BATCH_SIZE = 20  # documented maximum for the `ids` parameter
MAX_ODDS_PAGES = 5
LEAGUES_TTL = 86_400
STATUS_TTL = 60


@dataclass
class QuotaState:
    daily_limit: int | None = None
    daily_remaining: int | None = None
    minute_remaining: int | None = None
    requests_made: int = 0
    updated_at: datetime | None = None

    def remaining_today(self, now: datetime) -> int | None:
        """The daily quota resets at 00:00 UTC, so a reading from an earlier UTC day is stale."""
        if self.updated_at is None or self.updated_at.date() != now.date():
            return None
        return self.daily_remaining


def _chunks(items: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _join_ids(ids: Sequence[int]) -> str:
    return "-".join(str(i) for i in ids)


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, TypeError, ValueError):
        return None


def raise_for_api_errors(errors: Any) -> None:
    """Map API-Football's ``errors`` payload (dict or list) to a typed error."""
    if not errors:
        return
    items: dict[str, str] = {}
    if isinstance(errors, dict):
        items = {str(k).lower(): str(v) for k, v in errors.items()}
    elif isinstance(errors, list):
        for index, entry in enumerate(errors):
            if isinstance(entry, dict):
                items.update({str(k).lower(): str(v) for k, v in entry.items()})
            else:
                items[str(index)] = str(entry)
    else:
        items = {"error": str(errors)}

    detail = "; ".join(f"{key}: {value}" for key, value in items.items())
    if "token" in items or "access" in items:
        raise ProviderAuthError(detail, key_name=KEY_NAME)
    if "requests" in items:
        raise QuotaExhaustedError(detail)
    if "ratelimit" in items:
        raise RateLimitedError(detail)
    if "plan" in items:
        raise PlanRestrictedError(detail)
    raise ProviderResponseError(detail)


class ApiFootballClient:
    def __init__(
        self,
        api_key: str,
        config: ApiFootballConfig,
        ttl: CacheTtlConfig,
        cache: ResponseCache | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if not api_key:
            raise MissingApiKeyError(key_name=KEY_NAME)
        self._config = config
        self._ttl = ttl
        self._cache = cache
        self._clock = clock
        self._limiter = RateLimiter(config.requests_per_minute, sleep=sleep)
        self._http = HttpJsonClient(
            config.base_url,
            headers={"x-apisports-key": api_key},
            timeout=config.timeout_seconds,
            retry=RetryPolicy(max_retries=config.max_retries),
            rate_limiter=self._limiter,
            auth_key_name=KEY_NAME,
            transport=transport,
            sleep=sleep,
        )
        self.quota = QuotaState()
        self._quota_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------- transport

    def request(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        ttl: int = NO_CACHE,
        counts_quota: bool = True,
    ) -> dict[str, Any]:
        """GET an endpoint; ``ttl`` > 0 enables caching for that many seconds."""
        clean = {key: value for key, value in (params or {}).items() if value is not None}
        if ttl > 0 and self._cache is not None:
            cached = self._cache.get(endpoint, clean)
            if cached is not None:
                return cached
        if counts_quota:
            self._ensure_quota()
        payload = self._fetch(endpoint, clean, counts_quota=counts_quota)
        if ttl > 0 and self._cache is not None:
            self._cache.set(endpoint, clean, payload, ttl)
        return payload

    def _ensure_quota(self) -> None:
        remaining = self.quota.remaining_today(self._clock())
        if remaining is not None and remaining <= self._config.daily_reserve:
            raise QuotaExhaustedError(f"daily remaining {remaining} <= reserve {self._config.daily_reserve}")

    def _fetch(self, endpoint: str, params: dict[str, Any], *, counts_quota: bool) -> dict[str, Any]:
        for attempt in range(2):
            response = self._http.get_json(f"/{endpoint}", params)
            self._update_quota(response.headers, counted=counts_quota)
            payload = response.data
            if not isinstance(payload, dict):
                raise ProviderResponseError(f"/{endpoint}: payload is not a JSON object")
            try:
                raise_for_api_errors(payload.get("errors"))
            except RateLimitedError:
                if attempt == 0:
                    logger.warning("API-Football dəqiqəlik limitə çatdı — 60 san. gözlənilir")
                    self._limiter.block_for(60)
                    continue
                raise
            return payload
        raise RateLimitedError(f"/{endpoint}: still rate limited")  # pragma: no cover — loop always returns/raises

    def _update_quota(self, headers: httpx.Headers, *, counted: bool) -> None:
        with self._quota_lock:
            if counted:
                self.quota.requests_made += 1
            daily_limit = _header_int(headers, "x-ratelimit-requests-limit")
            daily_remaining = _header_int(headers, "x-ratelimit-requests-remaining")
            if daily_limit is not None:
                self.quota.daily_limit = daily_limit
            if daily_remaining is not None:
                self.quota.daily_remaining = daily_remaining
                self.quota.updated_at = self._clock()
            minute_remaining = _header_int(headers, "x-ratelimit-remaining")
            if minute_remaining is not None:
                self.quota.minute_remaining = minute_remaining
                if minute_remaining <= 0:
                    self._limiter.block_for(60)

    # ------------------------------------------------------------- endpoints

    def fixtures_by_date(self, day: date, timezone: str) -> list[FixtureDTO]:
        payload = self.request("fixtures", {"date": day.isoformat(), "timezone": timezone}, ttl=self._ttl.fixtures)
        return parse_fixtures(payload)

    def fixtures_by_ids(self, fixture_ids: Sequence[int]) -> list[FixtureDTO]:
        """Full fixture details (statistics, lineups) in batches of 20.

        Not cached: finished fixtures are persisted in the database and are not
        requested again, so caching these large payloads would only bloat it.
        """
        result: list[FixtureDTO] = []
        for chunk in _chunks(list(fixture_ids), IDS_BATCH_SIZE):
            result.extend(parse_fixtures(self.request("fixtures", {"ids": _join_ids(chunk)})))
        return result

    def team_last_fixtures(self, team_api_id: int, last: int) -> list[FixtureDTO]:
        payload = self.request("fixtures", {"team": team_api_id, "last": last}, ttl=self._ttl.team_history)
        return parse_fixtures(payload)

    def head_to_head(self, team_a: int, team_b: int, last: int = 10) -> list[FixtureDTO]:
        payload = self.request("fixtures/headtohead", {"h2h": f"{team_a}-{team_b}", "last": last}, ttl=self._ttl.h2h)
        return parse_fixtures(payload)

    def injuries_for_fixtures(self, fixture_ids: Sequence[int]) -> list[InjuryDTO]:
        result: list[InjuryDTO] = []
        for chunk in _chunks(list(fixture_ids), IDS_BATCH_SIZE):
            try:
                payload = self.request("injuries", {"ids": _join_ids(chunk)}, ttl=self._ttl.injuries)
            except (ProviderResponseError, PlanRestrictedError) as exc:
                # The free plan refuses the batch parameter; single fixtures are allowed.
                if "ids" not in exc.detail.lower():
                    raise
                # Older API versions/plans lack the batch parameter: fall back to one call per fixture.
                logger.info("injuries?ids dəstəklənmir — oyun-oyun sorğuya keçilir")
                for fixture_id in chunk:
                    single = self.request("injuries", {"fixture": fixture_id}, ttl=self._ttl.injuries)
                    result.extend(parse_injuries(single))
                continue
            result.extend(parse_injuries(payload))
        return result

    def standings(self, league_api_id: int, season: int) -> list[StandingDTO]:
        payload = self.request("standings", {"league": league_api_id, "season": season}, ttl=self._ttl.standings)
        return parse_standings(payload)

    def odds_for_fixture(self, fixture_api_id: int) -> FixtureOddsDTO | None:
        values: list[OddsValueDTO] = []
        updated_at: datetime | None = None
        page = 1
        while True:
            params: dict[str, Any] = {"fixture": fixture_api_id}
            if page > 1:
                params["page"] = page
            payload = self.request("odds", params, ttl=self._ttl.odds)
            for item in parse_odds(payload):
                values.extend(item.values)
                updated_at = updated_at or item.updated_at
            total_pages = (payload.get("paging") or {}).get("total") or 1
            if page >= total_pages or page >= MAX_ODDS_PAGES:
                break
            page += 1
        if not values:
            return None
        return FixtureOddsDTO(fixture_api_id=fixture_api_id, updated_at=updated_at, values=tuple(values))

    def leagues(
        self, *, league_id: int | None = None, country: str | None = None, search: str | None = None
    ) -> list[LeagueInfoDTO]:
        payload = self.request("leagues", {"id": league_id, "country": country, "search": search}, ttl=LEAGUES_TTL)
        return parse_leagues(payload)

    def account_status(self) -> AccountStatus:
        """Plan and today's usage. API-Football does not count this call against the quota."""
        status = parse_account_status(self.request("status", ttl=STATUS_TTL, counts_quota=False))
        if status.requests_current is not None and status.requests_limit_day is not None:
            with self._quota_lock:
                self.quota.daily_limit = status.requests_limit_day
                self.quota.daily_remaining = max(0, status.requests_limit_day - status.requests_current)
                self.quota.updated_at = self._clock()
        return status
