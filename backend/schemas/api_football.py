"""Typed views of API-Football v3 responses.

Parsers are defensive: the API often returns ``null`` for fields, statistics as
strings ("55%", "1.76"), and lower leagues may miss whole blocks. A malformed
item is skipped (and logged) instead of failing the whole response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.models.constants import FINISHED_STATUSES
from backend.utils.timeutils import from_timestamp, parse_iso_to_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamRef:
    api_id: int
    name: str
    logo: str | None = None


@dataclass(frozen=True)
class LeagueRef:
    api_id: int
    name: str
    country: str | None = None
    logo: str | None = None
    season: int | None = None
    round: str | None = None


@dataclass(frozen=True)
class TeamStatistics:
    team_api_id: int
    team_name: str
    values: dict[str, float | int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamLineup:
    team_api_id: int
    team_name: str
    formation: str | None
    coach: str | None
    starters: tuple[dict[str, Any], ...] = ()
    substitutes: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PlayerMatchStatsDTO:
    team_api_id: int
    team_name: str
    player_api_id: int
    player_name: str
    minutes: int | None
    position: str | None
    rating: float | None
    substitute: bool
    goals: int | None
    assists: int | None
    yellow_cards: int | None
    red_cards: int | None


@dataclass(frozen=True)
class FixtureDTO:
    api_id: int
    kickoff_utc: datetime
    status: str
    league: LeagueRef
    home: TeamRef
    away: TeamRef
    home_goals: int | None = None
    away_goals: int | None = None
    ht_home_goals: int | None = None
    ht_away_goals: int | None = None
    referee: str | None = None
    venue: str | None = None
    statistics: tuple[TeamStatistics, ...] = ()
    lineups: tuple[TeamLineup, ...] = ()
    players: tuple[PlayerMatchStatsDTO, ...] = ()

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES

    @property
    def label(self) -> str:
        return f"{self.home.name} – {self.away.name}"


@dataclass(frozen=True)
class InjuryDTO:
    fixture_api_id: int
    player_api_id: int
    player_name: str
    team: TeamRef
    api_type: str | None
    reason: str | None


@dataclass(frozen=True)
class StandingDTO:
    team: TeamRef
    group: str | None
    rank: int | None
    points: int | None
    played: int | None
    won: int | None
    drawn: int | None
    lost: int | None
    goals_for: int | None
    goals_against: int | None
    goal_diff: int | None
    form: str | None
    description: str | None
    home: dict[str, int | None]
    away: dict[str, int | None]


@dataclass(frozen=True)
class OddsValueDTO:
    bookmaker_api_id: int | None
    bookmaker: str
    bet_name: str
    value: str
    price: float | None


@dataclass(frozen=True)
class FixtureOddsDTO:
    fixture_api_id: int
    updated_at: datetime | None
    values: tuple[OddsValueDTO, ...]


@dataclass(frozen=True)
class LeagueInfoDTO:
    api_id: int
    name: str
    league_type: str | None
    country: str | None
    current_season: int | None


@dataclass(frozen=True)
class AccountStatus:
    plan: str | None
    active: bool
    requests_current: int | None
    requests_limit_day: int | None


# ------------------------------------------------------------------ helpers

# Statistic "type" (lower-cased) -> MatchStats column.
STAT_FIELDS: dict[str, str] = {
    "shots on goal": "shots_on_target",
    "shots off goal": "shots_off_target",
    "total shots": "shots_total",
    "blocked shots": "shots_blocked",
    "shots insidebox": "shots_inside_box",
    "shots outsidebox": "shots_outside_box",
    "fouls": "fouls",
    "corner kicks": "corners",
    "offsides": "offsides",
    "ball possession": "possession",
    "yellow cards": "yellow_cards",
    "red cards": "red_cards",
    "goalkeeper saves": "saves",
    "total passes": "passes_total",
    "passes accurate": "passes_accurate",
    "passes %": "pass_accuracy",
    "expected_goals": "xg",
}
FLOAT_STAT_FIELDS = frozenset({"possession", "pass_accuracy", "xg"})


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_stat_value(raw: Any) -> float | None:
    """'55%' -> 55.0, '1.76' -> 1.76, 7 -> 7.0, None/'' -> None."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip().rstrip("%").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def response_items(payload: dict[str, Any]) -> list[Any]:
    items = payload.get("response")
    return items if isinstance(items, list) else []


# ------------------------------------------------------------------ fixtures


def parse_team_statistics(entry: dict[str, Any]) -> TeamStatistics | None:
    team = entry.get("team") or {}
    team_id = _as_int(team.get("id"))
    if team_id is None:
        return None
    values: dict[str, float | int | None] = {}
    for stat in entry.get("statistics") or []:
        column = STAT_FIELDS.get(str(stat.get("type") or "").strip().lower())
        if column is None:
            continue
        value = parse_stat_value(stat.get("value"))
        if value is not None and column not in FLOAT_STAT_FIELDS:
            values[column] = int(value)
        else:
            values[column] = value
    return TeamStatistics(team_api_id=team_id, team_name=team.get("name") or "", values=values)


def _lineup_player(entry: dict[str, Any]) -> dict[str, Any]:
    player = entry.get("player") or {}
    return {
        "api_id": _as_int(player.get("id")),
        "name": player.get("name"),
        "number": _as_int(player.get("number")),
        "pos": player.get("pos"),
        "grid": player.get("grid"),
    }


def parse_lineup(entry: dict[str, Any]) -> TeamLineup | None:
    team = entry.get("team") or {}
    team_id = _as_int(team.get("id"))
    if team_id is None:
        return None
    return TeamLineup(
        team_api_id=team_id,
        team_name=team.get("name") or "",
        formation=entry.get("formation"),
        coach=(entry.get("coach") or {}).get("name"),
        starters=tuple(_lineup_player(p) for p in entry.get("startXI") or []),
        substitutes=tuple(_lineup_player(p) for p in entry.get("substitutes") or []),
    )


def parse_players(entries: list[dict[str, Any]]) -> list[PlayerMatchStatsDTO]:
    """The per-player block of a detailed fixture (minutes, position, goals, assists)."""
    result: list[PlayerMatchStatsDTO] = []
    for team_entry in entries or []:
        team = team_entry.get("team") or {}
        team_id = _as_int(team.get("id"))
        if team_id is None:
            continue
        for entry in team_entry.get("players") or []:
            player = entry.get("player") or {}
            player_id = _as_int(player.get("id"))
            if player_id is None:
                continue
            stats = (entry.get("statistics") or [{}])[0] or {}
            games = stats.get("games") or {}
            goals = stats.get("goals") or {}
            cards = stats.get("cards") or {}
            result.append(
                PlayerMatchStatsDTO(
                    team_api_id=team_id,
                    team_name=team.get("name") or "",
                    player_api_id=player_id,
                    player_name=player.get("name") or "",
                    minutes=_as_int(games.get("minutes")),
                    position=games.get("position"),
                    rating=_as_float(games.get("rating")),
                    substitute=bool(games.get("substitute")),
                    goals=_as_int(goals.get("total")),
                    assists=_as_int(goals.get("assists")),
                    yellow_cards=_as_int(cards.get("yellow")),
                    red_cards=_as_int(cards.get("red")),
                )
            )
    return result


def parse_fixture(item: dict[str, Any]) -> FixtureDTO | None:
    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}

    fixture_id = _as_int(fixture.get("id"))
    league_id = _as_int(league.get("id"))
    home_id = _as_int(home.get("id"))
    away_id = _as_int(away.get("id"))
    if None in (fixture_id, league_id, home_id, away_id):
        logger.debug("Natamam oyun qeydi buraxıldı: %s", fixture_id)
        return None

    timestamp = fixture.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        kickoff = from_timestamp(timestamp)
    else:
        kickoff = parse_iso_to_utc(fixture.get("date"))
    if kickoff is None:
        logger.debug("Başlama vaxtı olmayan oyun buraxıldı: %s", fixture_id)
        return None

    goals = item.get("goals") or {}
    halftime = (item.get("score") or {}).get("halftime") or {}
    statistics = (parse_team_statistics(s) for s in item.get("statistics") or [])
    lineups = (parse_lineup(entry) for entry in item.get("lineups") or [])

    return FixtureDTO(
        api_id=fixture_id,
        kickoff_utc=kickoff,
        status=(fixture.get("status") or {}).get("short") or "NS",
        league=LeagueRef(
            api_id=league_id,
            name=league.get("name") or "",
            country=league.get("country"),
            logo=league.get("logo"),
            season=_as_int(league.get("season")),
            round=league.get("round"),
        ),
        home=TeamRef(home_id, home.get("name") or "", home.get("logo")),
        away=TeamRef(away_id, away.get("name") or "", away.get("logo")),
        home_goals=_as_int(goals.get("home")),
        away_goals=_as_int(goals.get("away")),
        ht_home_goals=_as_int(halftime.get("home")),
        ht_away_goals=_as_int(halftime.get("away")),
        referee=fixture.get("referee"),
        venue=(fixture.get("venue") or {}).get("name"),
        statistics=tuple(s for s in statistics if s is not None),
        lineups=tuple(lu for lu in lineups if lu is not None),
        players=tuple(parse_players(item.get("players") or [])),
    )


def parse_fixtures(payload: dict[str, Any]) -> list[FixtureDTO]:
    parsed = (parse_fixture(item) for item in response_items(payload) if isinstance(item, dict))
    return [fixture for fixture in parsed if fixture is not None]


# ------------------------------------------------------------------ injuries


def parse_injuries(payload: dict[str, Any]) -> list[InjuryDTO]:
    result: list[InjuryDTO] = []
    for item in response_items(payload):
        player = item.get("player") or {}
        team = item.get("team") or {}
        fixture_id = _as_int((item.get("fixture") or {}).get("id"))
        player_id = _as_int(player.get("id"))
        team_id = _as_int(team.get("id"))
        if None in (fixture_id, player_id, team_id):
            continue
        result.append(
            InjuryDTO(
                fixture_api_id=fixture_id,
                player_api_id=player_id,
                player_name=player.get("name") or "",
                team=TeamRef(team_id, team.get("name") or "", team.get("logo")),
                api_type=player.get("type"),
                reason=player.get("reason"),
            )
        )
    return result


# ------------------------------------------------------------------ standings


def _split_record(record: dict[str, Any] | None) -> dict[str, int | None]:
    record = record or {}
    goals = record.get("goals") or {}
    return {
        "played": _as_int(record.get("played")),
        "won": _as_int(record.get("win")),
        "drawn": _as_int(record.get("draw")),
        "lost": _as_int(record.get("lose")),
        "goals_for": _as_int(goals.get("for")),
        "goals_against": _as_int(goals.get("against")),
    }


def parse_standings(payload: dict[str, Any]) -> list[StandingDTO]:
    result: list[StandingDTO] = []
    for item in response_items(payload):
        groups = (item.get("league") or {}).get("standings") or []
        for group in groups:
            for row in group or []:
                team = row.get("team") or {}
                team_id = _as_int(team.get("id"))
                if team_id is None:
                    continue
                overall = _split_record(row.get("all"))
                result.append(
                    StandingDTO(
                        team=TeamRef(team_id, team.get("name") or "", team.get("logo")),
                        group=row.get("group"),
                        rank=_as_int(row.get("rank")),
                        points=_as_int(row.get("points")),
                        played=overall["played"],
                        won=overall["won"],
                        drawn=overall["drawn"],
                        lost=overall["lost"],
                        goals_for=overall["goals_for"],
                        goals_against=overall["goals_against"],
                        goal_diff=_as_int(row.get("goalsDiff")),
                        form=row.get("form"),
                        description=row.get("description"),
                        home=_split_record(row.get("home")),
                        away=_split_record(row.get("away")),
                    )
                )
    return result


# ------------------------------------------------------------------ odds


def parse_odds(payload: dict[str, Any]) -> list[FixtureOddsDTO]:
    result: list[FixtureOddsDTO] = []
    for item in response_items(payload):
        fixture_id = _as_int((item.get("fixture") or {}).get("id"))
        if fixture_id is None:
            continue
        values: list[OddsValueDTO] = []
        for bookmaker in item.get("bookmakers") or []:
            for bet in bookmaker.get("bets") or []:
                for value in bet.get("values") or []:
                    values.append(
                        OddsValueDTO(
                            bookmaker_api_id=_as_int(bookmaker.get("id")),
                            bookmaker=bookmaker.get("name") or "",
                            bet_name=bet.get("name") or "",
                            value=str(value.get("value") or ""),
                            price=_as_float(value.get("odd")),
                        )
                    )
        result.append(
            FixtureOddsDTO(
                fixture_api_id=fixture_id,
                updated_at=parse_iso_to_utc(item.get("update")),
                values=tuple(values),
            )
        )
    return result


# ------------------------------------------------------------------ misc


def parse_leagues(payload: dict[str, Any]) -> list[LeagueInfoDTO]:
    result: list[LeagueInfoDTO] = []
    for item in response_items(payload):
        league = item.get("league") or {}
        league_id = _as_int(league.get("id"))
        if league_id is None:
            continue
        seasons = item.get("seasons") or []
        current = next((s for s in seasons if s.get("current")), None)
        result.append(
            LeagueInfoDTO(
                api_id=league_id,
                name=league.get("name") or "",
                league_type=league.get("type"),
                country=(item.get("country") or {}).get("name"),
                current_season=_as_int((current or {}).get("year")),
            )
        )
    return result


def parse_account_status(payload: dict[str, Any]) -> AccountStatus:
    response = payload.get("response") or {}
    if isinstance(response, list):
        response = response[0] if response else {}
    subscription = response.get("subscription") or {}
    requests = response.get("requests") or {}
    return AccountStatus(
        plan=subscription.get("plan"),
        active=bool(subscription.get("active")),
        requests_current=_as_int(requests.get("current")),
        requests_limit_day=_as_int(requests.get("limit_day")),
    )
