"""Free sources: name matching, CSV / football-data.org parsing, linking with API-Football, analysis on top."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import permutations
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from backend.analyzers.motivation import computed_standing
from backend.config import (
    ApiFootballConfig,
    AppConfig,
    ExternalDataConfig,
    FootballDataCoUkConfig,
    FootballDataOrgConfig,
    LeagueConfig,
)
from backend.models import League, Match, MatchStats, ScorerStat, Standing, Team, TeamAlias
from backend.services.analysis import AnalysisService
from backend.services.errors import PlanRestrictedError
from backend.services.external.football_data_couk import FootballDataCoUk, parse_csv, parse_kickoff, season_code, season_start_year
from backend.services.external.football_data_org import FootballDataOrgClient
from backend.services.external.sync import ExternalDataSync
from backend.services.external.teamnames import best_match, similarity
from backend.services.ingestion import IngestionService
from backend.services.picks import get_match_analysis
from tests.conftest import FakeApiFootball, make_client
from tests.factories import bet, envelope, fixture, injury, odds, ts

TODAY = date(2026, 10, 5)
TZ = ZoneInfo("Asia/Baku")
COUK_TEAMS = ["Man United", "Man City", "Arsenal", "Liverpool", "Nott'm Forest", "Wolves"]
ORG_TEAMS = {
    66: "Manchester United FC", 65: "Manchester City FC", 57: "Arsenal FC",
    64: "Liverpool FC", 351: "Nottingham Forest FC", 76: "Wolverhampton Wanderers FC",
}
HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HxG,AxG,"
    "HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A"
)


def season_csv() -> str:
    """A double round robin of six clubs in August 2026 (all before the test day)."""
    rows = [HEADER]
    for i, (home, away) in enumerate(permutations(COUK_TEAMS, 2)):
        day = date(2026, 8, 1) + timedelta(days=i)
        hg, ag = (i * 7) % 4, (i * 3) % 3
        rows.append(
            f"E0,{day:%d/%m/%Y},16:30,{home},{away},{hg},{ag},H,{min(hg, 1)},0,H,M Oliver,1.60,1.10,"
            f"14,9,6,3,10,12,7,4,2,3,0,0,2.1,3.4,3.5"
        )
    return "\n".join(rows) + "\n"


def couk_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/mmz4281/2627/E0.csv":
        return httpx.Response(200, content=season_csv().encode("utf-8"))
    return httpx.Response(404)


def org_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/competitions/PL/standings"):
        table = [
            {"position": n, "team": {"id": tid, "name": name}, "playedGames": 10, "won": 5, "draw": 2, "lost": 3,
             "points": 30 - n, "goalsFor": 12, "goalsAgainst": 9, "goalDifference": 3, "form": None}
            for n, (tid, name) in enumerate(ORG_TEAMS.items(), start=1)
        ]
        return httpx.Response(200, json={"season": {"startDate": "2026-08-21"}, "standings": [{"type": "TOTAL", "group": None, "table": table}]})
    if path.endswith("/competitions/PL/scorers"):
        return httpx.Response(200, json={
            "season": {"startDate": "2026-08-21"},
            "scorers": [{"player": {"name": "Erling Haaland", "position": "Offence"}, "team": {"id": 65, "name": "Manchester City FC"},
                         "playedMatches": 10, "goals": 5, "assists": 1}],
        })
    if path.endswith("/competitions/CL/standings"):
        return httpx.Response(403, json={"message": "The resource you are looking for is restricted."})
    return httpx.Response(200, json={"matches": []})


def free_config() -> AppConfig:
    return AppConfig(
        leagues=[LeagueConfig(api_id=39, name_az="İngiltərə — Premyer Liqa", country="England", fd_couk="E0", fd_org="PL")],
        external_data=ExternalDataConfig(football_data_couk=FootballDataCoUkConfig(seasons_back=1)),
        api_football=ApiFootballConfig(requests_per_minute=10_000, max_retries=1),
    )


def sync(db, config: AppConfig):
    couk = FootballDataCoUk(config.external_data.football_data_couk, transport=httpx.MockTransport(couk_handler), sleep=lambda s: None)
    org = FootballDataOrgClient("key", FootballDataOrgConfig(requests_per_minute=10_000), transport=httpx.MockTransport(org_handler), sleep=lambda s: None)
    return ExternalDataSync(db, config, couk, org).run(TODAY)


# ----------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Man United", "Manchester United FC", True),
        ("Nott'm Forest", "Nottingham Forest FC", True),
        ("Bayern Munich", "FC Bayern München", True),
        ("Ath Madrid", "Club Atlético de Madrid", True),
        ("Estudiantes L.P.", "Estudiantes de La Plata", True),
        ("Leeds", "Leeds United FC", True),
        ("Inter", "FC Internazionale Milano", True),
        ("Brighton", "Brighton & Hove Albion FC", True),
        ("Man City", "Man United", False),
        ("Atletico Madrid", "Real Madrid", False),
        ("Gimnasia L.P.", "Gimnasia Mendoza", False),
        # Real mistakes seen on the first live import:
        ("RCD Espanyol de Barcelona", "FC Barcelona", False),
        ("Paris Saint-Germain FC", "Paris FC", False),
        ("Los Angeles Galaxy", "Los Angeles FC", False),
        ("Cercle Brugge", "Club Brugge", False),
        ("Wisla", "Wisla Plock", False),
        ("Deportivo", "Deportivo Alavés", False),
    ],
)
def test_team_name_similarity(a, b, same):
    assert (similarity(a, b) >= 0.84) is same


def test_one_name_per_source_keeps_similar_clubs_apart(db):
    from backend.services.external.linking import TeamResolver

    with db.session() as s:
        league = League(api_id=179, name="Premiership")
        s.add(league)
        s.flush()
        resolver = TeamResolver(s, 0.84)
        dundee = resolver.resolve("football_data_couk", "Scotland:Dundee", ["Dundee"], league.id)
        united = resolver.resolve("football_data_couk", "Scotland:Dundee United", ["Dundee United"], league.id)
        assert dundee.id != united.id  # "Dundee United" ⊃ "Dundee", but the file names two clubs


def test_ambiguous_names_do_not_match():
    assert best_match("United", {1: ["Manchester United"], 2: ["Newcastle United"]}, 0.84)[0] is None


# ------------------------------------------------------------------ parsing


def test_csv_parsing_and_uk_time():
    (first, *_rest) = parse_csv(season_csv(), "E0", season=2026)
    assert first.kickoff_utc == datetime(2026, 8, 1, 15, 30)  # 16:30 BST
    assert (first.home, first.away, first.season) == ("Man United", "Man City", 2026)
    assert first.home_stats["xg"] == 1.60 and first.home_stats["corners"] == 7 and first.away_stats["yellow_cards"] == 3
    assert first.has_xg and first.referee == "M Oliver"
    assert parse_kickoff("05/01/27", "19:45") == datetime(2027, 1, 5, 19, 45)  # GMT in winter


def test_extra_league_layout():
    text = "Country,League,Season,Date,Time,Home,Away,HG,AG,Res\nArgentina,Liga Profesional,2026,22/09/2026,01:15,Lanus,Estudiantes L.P.,2,1,H\n"
    (match,) = parse_csv(text, "new:ARG")
    assert (match.home, match.home_goals, match.season) == ("Lanus", 2, 2026) and not match.home_stats


def test_season_helpers():
    assert season_start_year(date(2026, 10, 5)) == 2026 and season_start_year(date(2027, 3, 1)) == 2026
    assert season_code(2026) == "2627"


def test_redirects_are_followed_and_empty_files_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mmz4281/2627/E0.csv":
            return httpx.Response(301, headers={"Location": "https://www.football-data.co.uk/files/E0.csv"})
        if request.url.path == "/files/E0.csv":
            return httpx.Response(200, content=season_csv().encode())
        if request.url.path == "/mmz4281/2627/E1.csv":
            return httpx.Response(200, content=b"")
        return httpx.Response(404)

    couk = FootballDataCoUk(FootballDataCoUkConfig(seasons_back=0), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert len(couk.division("E0", TODAY)) == 30
    from backend.services.errors import ProviderResponseError
    with pytest.raises(ProviderResponseError):
        couk.division("E1", TODAY)


def test_missing_season_file_is_skipped():
    couk = FootballDataCoUk(FootballDataCoUkConfig(seasons_back=1), transport=httpx.MockTransport(couk_handler), sleep=lambda s: None)
    assert len(couk.division("E0", TODAY)) == 30  # 2025/26 file returns 404 and is skipped


def test_football_data_org_parsing_and_restriction():
    org = FootballDataOrgClient("key", FootballDataOrgConfig(requests_per_minute=10_000), transport=httpx.MockTransport(org_handler), sleep=lambda s: None)
    season, rows = org.standings("PL")
    assert season == 2026 and rows[0].team.name == "Manchester United FC" and rows[0].goals_for == 12
    _, scorers = org.scorers("PL")
    assert scorers[0].player_name == "Erling Haaland" and scorers[0].goals == 5
    with pytest.raises(PlanRestrictedError):
        org.standings("CL")


# -------------------------------------------------------------------- sync


def test_sync_links_both_sources_to_one_set_of_teams(db):
    report = sync(db, free_config())
    assert report.matches_added == 30 and report.with_xg == 30
    assert report.standings_leagues == 1 and report.scorer_leagues == 1
    with db.session() as s:
        assert s.scalar(select(func.count(Team.id))) == 6  # couk and football-data.org names merged
        assert s.scalar(select(func.count(TeamAlias.id))) == 12
        assert s.scalar(select(func.count(MatchStats.id))) == 60
        assert s.scalar(select(func.count(Standing.id))) == 6
        league = s.scalar(select(League).where(League.api_id == 39))
        assert league.full_results
        haaland = s.scalar(select(ScorerStat))
        city = s.get(Team, haaland.team_id)
        assert city.name == "Man City"


def test_teams_with_unrelated_names_are_linked_through_their_games(db):
    """football-data.org calls the clubs "Klub 1..6": only the shared fixtures can link them."""
    org_ids = {name: 1000 + i for i, name in enumerate(COUK_TEAMS)}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/competitions/PL/matches"):
            matches = []
            for i, (home, away) in enumerate(permutations(COUK_TEAMS, 2)):
                day = date(2026, 8, 1) + timedelta(days=i)
                matches.append({
                    "id": 9000 + i, "utcDate": f"{day.isoformat()}T15:30:00Z", "season": {"startDate": "2026-08-01"},
                    "homeTeam": {"id": org_ids[home], "name": f"Klub {org_ids[home]}"},
                    "awayTeam": {"id": org_ids[away], "name": f"Klub {org_ids[away]}"},
                    "score": {"fullTime": {"home": (i * 7) % 4, "away": (i * 3) % 3}, "halfTime": {"home": 0, "away": 0}},
                })
            return httpx.Response(200, json={"matches": matches})
        if path.endswith("/competitions/PL/standings"):
            table = [{"position": n, "team": {"id": tid, "name": f"Klub {tid}"}, "playedGames": 10, "won": 5, "draw": 2,
                      "lost": 3, "points": 20, "goalsFor": 12, "goalsAgainst": 9, "goalDifference": 3}
                     for n, tid in enumerate(org_ids.values(), start=1)]
            return httpx.Response(200, json={"season": {"startDate": "2026-08-01"}, "standings": [{"type": "TOTAL", "table": table}]})
        return httpx.Response(200, json={"scorers": []})

    config = free_config()
    couk = FootballDataCoUk(config.external_data.football_data_couk, transport=httpx.MockTransport(couk_handler), sleep=lambda s: None)
    org = FootballDataOrgClient("key", FootballDataOrgConfig(requests_per_minute=10_000), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    ExternalDataSync(db, config, couk, org).run(TODAY)
    with db.session() as s:
        assert s.scalar(select(func.count(Team.id))) == 6  # no "Klub" duplicates
        arsenal = s.scalar(select(Team).where(Team.name == "Arsenal"))
        alias = s.scalar(select(TeamAlias).where(TeamAlias.team_id == arsenal.id, TeamAlias.source == "football_data_org"))
        assert alias.key == str(org_ids["Arsenal"])
        assert s.scalar(select(func.count(Standing.id)).where(Standing.team_id == arsenal.id)) == 1


def test_sync_is_idempotent(db):
    config = free_config()
    sync(db, config)
    report = sync(db, config)
    assert report.matches_added == 0 and report.matches_updated == 30
    with db.session() as s:
        assert s.scalar(select(func.count(Match.id))) == 30 and s.scalar(select(func.count(Team.id))) == 6


def test_computed_standing_from_results(db):
    sync(db, free_config())
    with db.session() as s:
        league = s.scalar(select(League).where(League.api_id == 39))
        s.execute(Standing.__table__.delete())
        team = s.scalar(select(Team).where(Team.name == "Arsenal"))
        standing = computed_standing(s, league.id, 2026, team.id)
    assert standing is not None and standing.played == 10 and standing.group_size == 6


# ------------------------------------------------- API-Football on top of it

PLAN = {"plan": "Free plans do not have access to the Last parameter."}


def free_plan_api(fake: FakeApiFootball) -> None:
    kickoff = ts(2026, 10, 5, 15, 30)
    today = [fixture(5001, 39, 33, 50, kickoff=kickoff, home_name="Manchester United", away_name="Manchester City")]
    fake.route("fixtures", lambda p: envelope("fixtures", today if p.get("date") == "2026-10-05" else []), when=lambda p: "date" in p)
    fake.route("fixtures", envelope("fixtures", [], errors=PLAN), when=lambda p: "team" in p)
    fake.route("fixtures/headtohead", envelope("fixtures", [], errors=PLAN))
    fake.route("standings", envelope("standings", [], errors={"plan": "Free plans do not have access to this season."}))
    fake.route("injuries", envelope("injuries", [], errors={"plan": "Free plans do not have access to the Ids parameter."}), when=lambda p: "ids" in p)
    # API-Football names the player "E. Haaland"; football-data.org's scorer list says "Erling Haaland".
    haaland = injury(5001, 1100, 50, reason="Hamstring")
    haaland["player"]["name"] = "E. Haaland"
    haaland["team"]["name"] = "Manchester City"
    fake.route("injuries", envelope("injuries", [haaland]), when=lambda p: "fixture" in p)
    bets = [bet("Goals Over/Under", [("Over 2.5", "1.62"), ("Under 2.5", "2.30")])]
    fake.route("odds", envelope("odds", [odds(5001, bets)]))


def _ingest_on_free_plan(db, fake):
    config = free_config()
    sync(db, config)
    free_plan_api(fake)
    report = IngestionService(db, make_client(fake, db=db), config, "Asia/Baku").run_daily(TODAY)
    return config, report


def test_api_teams_adopt_free_source_teams(db, fake_api):
    _, report = _ingest_on_free_plan(db, fake_api)
    with db.session() as s:
        assert s.scalar(select(func.count(Team.id))) == 6  # no duplicates for the API's names
        united = s.scalar(select(Team).where(Team.api_id == 33))
        assert united is not None and s.scalar(select(func.count(TeamAlias.id)).where(TeamAlias.team_id == united.id)) == 2
    assert report.status == "success", report.warnings


def test_plan_refusals_are_noted_once_and_not_repeated(db, fake_api):
    _, report = _ingest_on_free_plan(db, fake_api)
    assert len(report.notes) == 3 and all("pulsuz mənbələrdən" in note for note in report.notes)
    assert fake_api.count("fixtures", lambda p: "team" in p) == 1  # stopped asking after the first refusal
    assert fake_api.count("injuries", lambda p: "fixture" in p) == 1  # per-fixture fallback for injuries


def test_analysis_runs_on_free_history(db, fake_api):
    config, _ = _ingest_on_free_plan(db, fake_api)
    AnalysisService(db, config, TZ, clock=lambda: datetime(2026, 10, 5, 4, 0)).run_daily(TODAY)
    with db.session() as s:
        match_id = s.scalar(select(Match.id).where(Match.api_id == 5001))
    view = get_match_analysis(db, match_id, TZ)
    assert view is not None and view.reasons["quality"]["score"] >= 90
    components = view.reasons["quality"]["components"]
    assert components["home_history"] and components["xg"] and components["standings"] and components["h2h"]
    analysis = "\n".join(view.reasons["analysis"])
    assert "E. Haaland (bombardir, 5 qol)" in analysis  # matched to the scorer list by name
    assert "xG göstəricisi (son 10, oyun başına)" in analysis
