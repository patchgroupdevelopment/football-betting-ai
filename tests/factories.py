"""Builders for API-Football-shaped payloads used by the tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def envelope(endpoint: str, response: Any, *, errors: Any = None, paging: tuple[int, int] = (1, 1)) -> dict[str, Any]:
    return {
        "get": endpoint,
        "parameters": {},
        "errors": errors if errors is not None else [],
        "results": len(response) if isinstance(response, list) else 1,
        "paging": {"current": paging[0], "total": paging[1]},
        "response": response,
    }


def ts(year: int, month: int, day: int, hour: int = 15, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


def team_statistics(
    team_id: int, *, shots: int = 12, on_target: int = 5, corners: int = 6, yellow: int = 2, xg: str | None = "1.45"
) -> dict[str, Any]:
    stats = [
        {"type": "Shots on Goal", "value": on_target},
        {"type": "Shots off Goal", "value": shots - on_target},
        {"type": "Total Shots", "value": shots},
        {"type": "Blocked Shots", "value": 2},
        {"type": "Shots insidebox", "value": 8},
        {"type": "Shots outsidebox", "value": 4},
        {"type": "Fouls", "value": 10},
        {"type": "Corner Kicks", "value": corners},
        {"type": "Offsides", "value": 1},
        {"type": "Ball Possession", "value": "55%"},
        {"type": "Yellow Cards", "value": yellow},
        {"type": "Red Cards", "value": None},
        {"type": "Goalkeeper Saves", "value": 3},
        {"type": "Total passes", "value": 480},
        {"type": "Passes accurate", "value": 410},
        {"type": "Passes %", "value": "85%"},
    ]
    if xg is not None:
        stats.append({"type": "expected_goals", "value": xg})
    return {"team": {"id": team_id, "name": f"Team {team_id}", "logo": None}, "statistics": stats}


def lineup(team_id: int, players: list[int]) -> dict[str, Any]:
    return {
        "team": {"id": team_id, "name": f"Team {team_id}"},
        "formation": "4-3-3",
        "coach": {"id": 1, "name": f"Coach {team_id}"},
        "startXI": [
            {"player": {"id": pid, "name": f"Player {pid}", "number": n + 1, "pos": "M", "grid": None}}
            for n, pid in enumerate(players)
        ],
        "substitutes": [],
    }


def players_block(team_id: int, rows: list[tuple[int, str, int, int, int]]) -> dict[str, Any]:
    """rows: (player_id, position, minutes, goals, assists)"""
    return {
        "team": {"id": team_id, "name": f"Team {team_id}", "logo": None},
        "players": [
            {
                "player": {"id": pid, "name": f"Player {pid}", "photo": None},
                "statistics": [
                    {
                        "games": {"minutes": minutes, "number": n + 1, "position": pos, "rating": "7.1", "substitute": False},
                        "goals": {"total": goals, "conceded": 0, "assists": assists, "saves": None},
                        "cards": {"yellow": 0, "red": 0},
                    }
                ],
            }
            for n, (pid, pos, minutes, goals, assists) in enumerate(rows)
        ],
    }


def fixture(
    fixture_id: int,
    league_id: int,
    home_id: int,
    away_id: int,
    *,
    kickoff: int,
    status: str = "NS",
    season: int = 2026,
    league_name: str = "Premier League",
    country: str = "England",
    goals: tuple[int | None, int | None] = (None, None),
    halftime: tuple[int | None, int | None] = (None, None),
    home_name: str | None = None,
    away_name: str | None = None,
    statistics: list[dict[str, Any]] | None = None,
    lineups: list[dict[str, Any]] | None = None,
    players: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "fixture": {
            "id": fixture_id,
            "referee": "M. Oliver",
            "timezone": "UTC",
            "date": datetime.fromtimestamp(kickoff, UTC).isoformat(),
            "timestamp": kickoff,
            "venue": {"id": 1, "name": "Stadium", "city": "City"},
            "status": {"long": status, "short": status, "elapsed": None},
        },
        "league": {
            "id": league_id,
            "name": league_name,
            "country": country,
            "logo": None,
            "flag": None,
            "season": season,
            "round": "Regular Season - 7",
        },
        "teams": {
            "home": {"id": home_id, "name": home_name or f"Team {home_id}", "logo": None, "winner": None},
            "away": {"id": away_id, "name": away_name or f"Team {away_id}", "logo": None, "winner": None},
        },
        "goals": {"home": goals[0], "away": goals[1]},
        "score": {
            "halftime": {"home": halftime[0], "away": halftime[1]},
            "fulltime": {"home": goals[0], "away": goals[1]},
            "extratime": {"home": None, "away": None},
            "penalty": {"home": None, "away": None},
        },
    }
    if statistics is not None:
        item["statistics"] = statistics
    if lineups is not None:
        item["lineups"] = lineups
    if players is not None:
        item["players"] = players
    return item


def injury(fixture_id: int, player_id: int, team_id: int, *, kind: str = "Missing Fixture", reason: str = "Knee Injury") -> dict[str, Any]:
    return {
        "player": {"id": player_id, "name": f"Player {player_id}", "photo": None, "type": kind, "reason": reason},
        "team": {"id": team_id, "name": f"Team {team_id}", "logo": None},
        "fixture": {"id": fixture_id, "timezone": "UTC", "date": "2026-10-05T15:30:00+00:00", "timestamp": 0},
        "league": {"id": 39, "season": 2026, "name": "Premier League", "country": "England"},
    }


def standing_row(rank: int, team_id: int, points: int, *, group: str = "Premier League") -> dict[str, Any]:
    record = {"played": 7, "win": 4, "draw": 2, "lose": 1, "goals": {"for": 12, "against": 6}}
    return {
        "rank": rank,
        "team": {"id": team_id, "name": f"Team {team_id}", "logo": None},
        "points": points,
        "goalsDiff": 6,
        "group": group,
        "form": "WWDLW",
        "status": "same",
        "description": None,
        "all": record,
        "home": record,
        "away": record,
        "update": "2026-10-04T00:00:00+00:00",
    }


def standings(league_id: int, rows: list[dict[str, Any]], *, season: int = 2026) -> dict[str, Any]:
    return {"league": {"id": league_id, "name": "League", "country": "England", "season": season, "standings": [rows]}}


def odds(fixture_id: int, bets: list[dict[str, Any]], *, bookmaker_id: int = 8, bookmaker: str = "Bet365") -> dict[str, Any]:
    return {
        "league": {"id": 39, "season": 2026},
        "fixture": {"id": fixture_id, "timezone": "UTC", "date": "2026-10-05T15:30:00+00:00", "timestamp": 0},
        "update": "2026-10-05T06:00:00+00:00",
        "bookmakers": [{"id": bookmaker_id, "name": bookmaker, "bets": bets}],
    }


def bet(name: str, values: list[tuple[str, str]], bet_id: int = 1) -> dict[str, Any]:
    return {"id": bet_id, "name": name, "values": [{"value": v, "odd": o} for v, o in values]}
