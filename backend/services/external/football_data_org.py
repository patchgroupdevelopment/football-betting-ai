"""football-data.org (v4) client — free tier: 12 competitions, 10 requests per minute.

Used for standings and top scorers (and results for competitions that
football-data.co.uk does not cover, e.g. the Champions League). Team ids are
global across competitions, so a club's league and cup matches link up.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from backend.config import FootballDataOrgConfig
from backend.services.cache import ResponseCache
from backend.services.errors import MissingApiKeyError, PlanRestrictedError, ProviderAuthError
from backend.services.http_client import HttpJsonClient, RateLimiter, RetryPolicy
from backend.utils.timeutils import parse_iso_to_utc

SOURCE = "football_data_org"
KEY_NAME = "FOOTBALL_DATA_ORG_KEY"


@dataclass(frozen=True)
class OrgTeam:
    id: int
    name: str
    short_name: str | None = None

    @property
    def names(self) -> list[str]:
        return [n for n in (self.name, self.short_name) if n]


@dataclass(frozen=True)
class OrgMatch:
    id: int
    kickoff_utc: datetime
    season: int
    home: OrgTeam
    away: OrgTeam
    home_goals: int
    away_goals: int
    ht_home_goals: int | None
    ht_away_goals: int | None
    referee: str | None
    stage: str | None


@dataclass(frozen=True)
class OrgStanding:
    team: OrgTeam
    group: str | None
    position: int
    played: int
    won: int
    draw: int
    lost: int
    points: int
    goals_for: int
    goals_against: int
    goal_diff: int
    form: str | None


@dataclass(frozen=True)
class OrgScorer:
    team: OrgTeam
    player_name: str
    position: str | None
    goals: int
    assists: int | None
    played: int | None


def _team(data: dict[str, Any] | None) -> OrgTeam | None:
    data = data or {}
    if data.get("id") is None:
        return None
    return OrgTeam(int(data["id"]), data.get("name") or data.get("shortName") or str(data["id"]), data.get("shortName"))


def _season(payload: dict[str, Any], fallback: datetime | None = None) -> int | None:
    start = ((payload.get("season") or {}).get("startDate") or "")[:4]
    if start.isdigit():
        return int(start)
    return fallback.year if fallback else None


class FootballDataOrgClient:
    def __init__(
        self,
        api_key: str,
        config: FootballDataOrgConfig,
        cache: ResponseCache | None = None,
        cache_hours: float = 12,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise MissingApiKeyError(key_name=KEY_NAME)
        self._cache = cache
        self._ttl = int(cache_hours * 3600)
        self._http = HttpJsonClient(
            config.base_url,
            headers={"X-Auth-Token": api_key},
            timeout=config.timeout_seconds,
            retry=RetryPolicy(max_retries=config.max_retries),
            rate_limiter=RateLimiter(config.requests_per_minute, sleep=sleep),
            auth_key_name=KEY_NAME,
            transport=transport,
            sleep=sleep,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_params = {"path": path, **params}
        if self._cache is not None and self._ttl > 0:
            cached = self._cache.get(SOURCE, cache_params)
            if cached is not None:
                return cached
        try:
            payload = self._http.get_json(path, params).data
        except ProviderAuthError as exc:
            if "403" in exc.detail:  # valid key, competition/resource outside the free tier
                raise PlanRestrictedError(exc.detail) from exc
            raise
        if self._cache is not None and self._ttl > 0:
            self._cache.set(SOURCE, cache_params, payload, self._ttl)
        return payload

    def finished_matches(self, code: str, season: int | None = None) -> list[OrgMatch]:
        payload = self._get(f"/competitions/{code}/matches", {"status": "FINISHED", "season": season})
        result: list[OrgMatch] = []
        for item in payload.get("matches") or []:
            home, away = _team(item.get("homeTeam")), _team(item.get("awayTeam"))
            kickoff = parse_iso_to_utc(item.get("utcDate"))
            score = item.get("score") or {}
            full, half = score.get("fullTime") or {}, score.get("halfTime") or {}
            if home is None or away is None or kickoff is None or full.get("home") is None or full.get("away") is None:
                continue
            referees = item.get("referees") or []
            main = next((r for r in referees if r.get("type") == "REFEREE"), referees[0] if referees else None)
            result.append(
                OrgMatch(
                    id=int(item["id"]),
                    kickoff_utc=kickoff,
                    season=_season(item, kickoff) or kickoff.year,
                    home=home,
                    away=away,
                    home_goals=int(full["home"]),
                    away_goals=int(full["away"]),
                    ht_home_goals=half.get("home"),
                    ht_away_goals=half.get("away"),
                    referee=(main or {}).get("name"),
                    stage=item.get("stage"),
                )
            )
        return result

    def standings(self, code: str) -> tuple[int | None, list[OrgStanding]]:
        payload = self._get(f"/competitions/{code}/standings")
        rows: list[OrgStanding] = []
        for table in payload.get("standings") or []:
            if table.get("type") not in (None, "TOTAL"):
                continue
            for row in table.get("table") or []:
                team = _team(row.get("team"))
                if team is None:
                    continue
                rows.append(
                    OrgStanding(
                        team=team,
                        group=table.get("group"),
                        position=int(row.get("position") or 0),
                        played=int(row.get("playedGames") or 0),
                        won=int(row.get("won") or 0),
                        draw=int(row.get("draw") or 0),
                        lost=int(row.get("lost") or 0),
                        points=int(row.get("points") or 0),
                        goals_for=int(row.get("goalsFor") or 0),
                        goals_against=int(row.get("goalsAgainst") or 0),
                        goal_diff=int(row.get("goalDifference") or 0),
                        form=row.get("form"),
                    )
                )
        return _season(payload), rows

    def scorers(self, code: str, limit: int = 50) -> tuple[int | None, list[OrgScorer]]:
        payload = self._get(f"/competitions/{code}/scorers", {"limit": limit})
        rows: list[OrgScorer] = []
        for item in payload.get("scorers") or []:
            team = _team(item.get("team"))
            player = item.get("player") or {}
            if team is None or not player.get("name"):
                continue
            rows.append(
                OrgScorer(
                    team=team,
                    player_name=player["name"],
                    position=player.get("position") or player.get("section"),
                    goals=int(item.get("goals") or 0),
                    assists=item.get("assists"),
                    played=item.get("playedMatches"),
                )
            )
        return _season(payload), rows
