"""Historical matches with odds from football-data.co.uk, held in memory.

Past seasons never change, so their CSV files are kept on disk; the current
season's file (and the multi-season extra-league files) are downloaded again
when older than a day.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.config import AppConfig
from backend.predictors.market_odds import OddsQuote
from backend.services.errors import ProviderError, ProviderResponseError
from backend.services.external.football_data_couk import (
    EXTRA_PREFIX,
    CsvMatch,
    FootballDataCoUk,
    parse_csv,
    season_code,
    season_start_year,
)

logger = logging.getLogger(__name__)

FRESH_SECONDS = 24 * 3600


@dataclass(frozen=True)
class LeagueSource:
    league_id: int  # API-Football id from config.yaml
    code: str
    title: str

    @property
    def is_extra(self) -> bool:
        return self.code.startswith(EXTRA_PREFIX)


@dataclass(frozen=True)
class HistoricalMatch:
    index: int
    league_id: int
    season: int
    kickoff_utc: datetime
    home_id: int
    away_id: int
    home: str
    away: str
    home_goals: int
    away_goals: int
    ht_home_goals: int | None
    ht_away_goals: int | None
    home_xg: float | None
    away_xg: float | None
    home_corners: int | None
    away_corners: int | None
    home_cards: int | None
    away_cards: int | None
    odds: tuple[OddsQuote, ...]
    closing_odds: tuple[OddsQuote, ...]


def league_sources(config: AppConfig, codes: set[str] | None = None) -> list[LeagueSource]:
    return [
        LeagueSource(league.api_id, league.fd_couk, league.name_az)
        for league in config.leagues
        if league.fd_couk and (codes is None or league.fd_couk in codes)
    ]


class CsvStore:
    """CSV files on disk in front of the football-data.co.uk client."""

    def __init__(
        self,
        directory: Path,
        client: FootballDataCoUk,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = directory
        self._client = client
        self._clock = clock

    def _cached(self, path: Path, fetch: Callable[[], str], *, permanent: bool) -> str:
        if path.exists() and (permanent or self._clock() - path.stat().st_mtime < FRESH_SECONDS):
            return path.read_text(encoding="utf-8")
        try:
            text = fetch()
        except ProviderError:
            if path.exists():  # a stale copy beats no data
                logger.warning("football-data.co.uk: köhnə nüsxə istifadə olunur — %s", path.name)
                return path.read_text(encoding="utf-8")
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    def season(self, code: str, start_year: int, current_season: int) -> str | None:
        path = self._dir / season_code(start_year) / f"{code}.csv"
        try:
            return self._cached(
                path, lambda: self._client.season_text(code, start_year), permanent=start_year < current_season
            )
        except ProviderResponseError as exc:
            if "404" in exc.detail:  # the season's file does not exist (yet)
                return None
            raise

    def extra(self, code: str) -> str:
        name = code[len(EXTRA_PREFIX):]
        return self._cached(self._dir / "new" / f"{name}.csv", lambda: self._client.extra_text(code), permanent=False)


def load_csv_matches(store: CsvStore, source: LeagueSource, since: date, today: date) -> list[CsvMatch]:
    if source.is_extra:
        matches = parse_csv(store.extra(source.code), source.code, with_odds=True)
    else:
        current = season_start_year(today)
        matches = []
        for start in range(season_start_year(since), current + 1):
            text = store.season(source.code, start, current)
            if text:
                matches.extend(parse_csv(text, source.code, season=start, with_odds=True))
    floor = datetime.combine(since, datetime.min.time())
    return [m for m in matches if m.kickoff_utc >= floor]


def _cards(stats: dict[str, float | int | None]) -> int | None:
    yellow = stats.get("yellow_cards")
    if yellow is None:
        return None
    return int(yellow) + int(stats.get("red_cards") or 0)


def build_matches(per_league: dict[LeagueSource, list[CsvMatch]]) -> list[HistoricalMatch]:
    """Chronological matches with integer team ids (team names are stable within the source)."""
    team_ids: dict[str, int] = {}

    def team(name: str) -> int:
        return team_ids.setdefault(name, len(team_ids) + 1)

    rows = [(source, m) for source, matches in per_league.items() for m in matches]
    rows.sort(key=lambda item: (item[1].kickoff_utc, item[1].home))
    result: list[HistoricalMatch] = []
    seen: set[tuple[str, str, date]] = set()
    for source, m in rows:
        key = (m.home, m.away, m.kickoff_utc.date())
        if key in seen:  # the same match listed twice
            continue
        seen.add(key)
        xg_home, xg_away = m.home_stats.get("xg"), m.away_stats.get("xg")
        corners_home, corners_away = m.home_stats.get("corners"), m.away_stats.get("corners")
        result.append(
            HistoricalMatch(
                index=len(result),
                league_id=source.league_id,
                season=m.season,
                kickoff_utc=m.kickoff_utc,
                home_id=team(m.home),
                away_id=team(m.away),
                home=m.home,
                away=m.away,
                home_goals=m.home_goals,
                away_goals=m.away_goals,
                ht_home_goals=m.ht_home_goals,
                ht_away_goals=m.ht_away_goals,
                home_xg=float(xg_home) if xg_home is not None else None,
                away_xg=float(xg_away) if xg_away is not None else None,
                home_corners=int(corners_home) if corners_home is not None else None,
                away_corners=int(corners_away) if corners_away is not None else None,
                home_cards=_cards(m.home_stats),
                away_cards=_cards(m.away_stats),
                odds=m.odds,
                closing_odds=m.closing_odds,
            )
        )
    return result


def load_history(
    config: AppConfig,
    cache_dir: Path,
    since: date,
    today: date,
    *,
    codes: set[str] | None = None,
    client: FootballDataCoUk | None = None,
) -> tuple[list[LeagueSource], list[HistoricalMatch], list[str]]:
    """(leagues loaded, matches, warnings)."""
    own_client = client is None
    client = client or FootballDataCoUk(config.external_data.football_data_couk, cache=None)
    store = CsvStore(cache_dir, client)
    warnings: list[str] = []
    per_league: dict[LeagueSource, list[CsvMatch]] = {}
    try:
        for source in league_sources(config, codes):
            try:
                per_league[source] = load_csv_matches(store, source, since, today)
            except ProviderError as exc:
                logger.warning("football-data.co.uk: %s yüklənmədi — %s", source.code, exc)
                warnings.append(source.title)
    finally:
        if own_client:
            client.close()
    loaded = [source for source, matches in per_league.items() if matches]
    return loaded, build_matches(per_league), warnings
