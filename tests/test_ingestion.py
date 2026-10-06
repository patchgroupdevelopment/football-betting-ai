"""End-to-end ingestion against a fake API-Football."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from backend.analyzers.data_quality import assess_match
from backend.config import ApiFootballConfig
from backend.models import (
    Injury,
    Lineup,
    Match,
    MatchStats,
    ModelRun,
    OddsSnapshot,
    Player,
    PlayerMatchStats,
    Result,
    Standing,
)
from backend.models.constants import InjuryKind, RunStatus
from backend.services.ingestion import IngestionService, classify_injury
from backend.services.overview import build_daily_overview
from tests.conftest import FakeApiFootball, make_client
from tests.factories import (
    bet,
    envelope,
    fixture,
    injury,
    lineup,
    odds,
    players_block,
    standing_row,
    standings,
    team_statistics,
    ts,
)

DAY = date(2026, 10, 5)
TZ = ZoneInfo("Asia/Baku")
TODAY = {
    1001: (39, 50, 42, ts(2026, 10, 5, 15, 30)),
    1002: (39, 33, 40, ts(2026, 10, 5, 18, 0)),
    1003: (140, 529, 541, ts(2026, 10, 5, 19, 0)),
    1004: (999, 1, 2, ts(2026, 10, 5, 12, 0)),
}
LEAGUE_META = {39: ("Premier League", "England"), 140: ("La Liga", "Spain"), 999: ("Other League", "Nowhere")}


def striker(team_id: int) -> int:
    return 1100 if team_id == 50 else team_id * 1000 + 9


class Scenario:
    """Registers routes for a realistic match day and keeps a registry of historical fixtures."""

    def __init__(self, fake: FakeApiFootball, *, odds_error_for: int | None = None) -> None:
        self.fake = fake
        self.history: dict[int, tuple[int, int, int, int]] = {}  # fixture id -> (league, home, away, kickoff)
        self.odds_error_for = odds_error_for
        fake.route("fixtures", self._fixtures)
        fake.route("fixtures/headtohead", self._h2h)
        fake.route("injuries", self._injuries)
        fake.route("standings", self._standings)
        fake.route("odds", self._odds)

    @staticmethod
    def _item(fixture_id: int, league: int, home: int, away: int, kickoff: int, **extra) -> dict:
        name, country = LEAGUE_META[league]
        return fixture(fixture_id, league, home, away, kickoff=kickoff, league_name=name, country=country, **extra)

    def _team_league(self, team_id: int) -> int:
        return 140 if team_id in (529, 541) else 39

    def _fixtures(self, params: dict[str, str]) -> dict:
        if "date" in params:
            return envelope("fixtures", [self._item(fid, *meta) for fid, meta in TODAY.items()])
        if "team" in params:
            team = int(params["team"])
            items = []
            for i in range(1, 7):
                fid = team * 100 + i
                meta = (self._team_league(team), team, 900 + i, ts(2026, 9, i * 4))
                self.history[fid] = meta
                items.append(self._item(fid, *meta, status="FT", goals=(2, 1), halftime=(1, 0)))
            return envelope("fixtures", items)
        if "ids" in params:
            items = []
            for fid in (int(x) for x in params["ids"].split("-")):
                league, home, away, kickoff = self.history[fid]
                items.append(
                    self._item(
                        fid, league, home, away, kickoff,
                        status="FT", goals=(2, 1), halftime=(1, 0),
                        statistics=[team_statistics(home), team_statistics(away, corners=4, xg="0.90")],
                        lineups=[lineup(home, [home * 1000 + 1, home * 1000 + 2])],
                        # Team 50's striker (player 1100) scores in every game; he is injured for fixture 1001.
                        players=[players_block(home, [(home * 1000 + 1, "G", 90, 0, 0), (striker(home), "F", 90, 1, 0)])],
                    )
                )
            return envelope("fixtures", items)
        return envelope("fixtures", [])

    def _h2h(self, params: dict[str, str]) -> dict:
        home, away = (int(x) for x in params["h2h"].split("-"))
        base = 70_000 + home * 10
        league = self._team_league(home)
        return envelope(
            "fixtures/headtohead",
            [self._item(base + k, league, home, away, ts(2025, 3, k + 1), status="FT", goals=(1, 1)) for k in (1, 2)],
        )

    def _injuries(self, params: dict[str, str]) -> dict:
        ids = {int(x) for x in params.get("ids", "").split("-") if x}
        items = []
        if 1001 in ids:
            items.append(injury(1001, 1100, 50, reason="Knee Injury"))
            items.append(injury(1001, 1101, 42, reason="Suspended"))
            items.append(injury(1001, 1102, 42, kind="Questionable", reason="Muscle Injury"))
        return envelope("injuries", items)

    def _standings(self, params: dict[str, str]) -> dict:
        league = int(params["league"])
        teams = (50, 42, 33, 40) if league == 39 else (529, 541)
        rows = [standing_row(rank, team, 20 - rank) for rank, team in enumerate(teams, start=1)]
        return envelope("standings", [standings(league, rows)])

    def _odds(self, params: dict[str, str]) -> dict | httpx.Response:
        fixture_id = int(params["fixture"])
        if fixture_id == self.odds_error_for:
            return httpx.Response(500)
        if fixture_id != 1001:
            return envelope("odds", [])
        bets = [
            bet("Match Winner", [("Home", "1.85"), ("Draw", "3.60"), ("Away", "4.20")]),
            bet("Goals Over/Under", [("Over 1.5", "1.25"), ("Under 1.5", "3.80"), ("Over 2.5", "1.80"), ("Under 2.5", "2.00")]),
            bet("Both Teams Score", [("Yes", "1.70"), ("No", "2.05")]),
            bet("Double Chance", [("Home/Draw", "1.22"), ("Home/Away", "1.30"), ("Draw/Away", "1.95")]),
            bet("Exotic Market", [("Something", "9.00")]),
        ]
        return envelope("odds", [odds(1001, bets)])


def _service(db, app_config, client) -> IngestionService:
    return IngestionService(db, client, app_config, "Asia/Baku")


def test_full_day_ingestion(db, app_config, fake_api):
    Scenario(fake_api)
    report = _service(db, app_config, make_client(fake_api, db=db)).run_daily(DAY)

    assert report.status == RunStatus.SUCCESS, report.warnings
    assert (report.total_fixtures, report.priority_fixtures, report.other_fixtures) == (4, 3, 1)
    assert (report.deep_planned, report.deep_completed) == (3, 3)
    assert report.requests_used == len(fake_api.calls)

    with db.session() as s:
        assert s.scalar(select(func.count(Match.id)).where(Match.is_priority.is_(True))) == 3
        assert s.scalar(select(Match).where(Match.api_id == 1004)) is None  # unconfigured league not stored
        # 6 teams × 6 historical fixtures × 2 teams' statistics
        assert s.scalar(select(func.count(MatchStats.id))) == 72
        assert s.scalar(select(func.count(Result.id))) == 36
        assert s.scalar(select(func.count(Lineup.id))) == 36
        assert s.scalar(select(func.count(Player.id))) >= 12
        assert s.scalar(select(func.count(PlayerMatchStats.id))) == 72  # 36 fixtures × 2 players
        assert s.scalar(select(func.count(Standing.id))) == 6

        stats = s.scalars(select(MatchStats).where(MatchStats.xg.is_not(None))).first()
        assert stats.possession == 55.0 and stats.pass_accuracy == 85.0 and stats.xg_source == "api"

        match_1001 = s.scalar(select(Match).where(Match.api_id == 1001))
        kinds = set(s.scalars(select(Injury.kind).where(Injury.match_id == match_1001.id)))
        assert kinds == {InjuryKind.INJURY, InjuryKind.SUSPENSION, InjuryKind.DOUBTFUL}

        markets = {(o.market, o.selection, o.line) for o in s.scalars(select(OddsSnapshot))}
        assert ("1X2", "X", None) in markets and ("OU", "OVER", 2.5) in markets
        assert ("BTTS", "YES", None) in markets and ("DC", "1X", None) in markets
        assert len(markets) == 12  # the unknown market is skipped

        quality_1001 = assess_match(s, match_1001)
        assert quality_1001.score == 95 and quality_1001.level == "full"
        match_1002 = s.scalar(select(Match).where(Match.api_id == 1002))
        quality_1002 = assess_match(s, match_1002)
        assert quality_1002.components["odds"] is False and quality_1002.level == "partial"

        run = s.scalar(select(ModelRun))
        assert run.status == RunStatus.SUCCESS and run.details["other_fixtures"] == 1


def test_second_run_is_idempotent(db, app_config, fake_api):
    Scenario(fake_api)
    service = _service(db, app_config, make_client(fake_api))  # no cache: every call hits the fake API
    service.run_daily(DAY)

    def counts():
        with db.session() as s:
            return tuple(
                s.scalar(select(func.count(model.id)))
                for model in (Match, MatchStats, OddsSnapshot, Injury, Standing, Player)
            )

    before = counts()
    calls_before = len(fake_api.calls)
    report = service.run_daily(DAY)

    assert report.status == RunStatus.SUCCESS
    assert counts() == before
    new_calls = fake_api.calls[calls_before:]
    assert not any("ids" in params for path, params in new_calls if path == "fixtures")  # details not re-fetched


def test_cache_prevents_refetching(db, app_config, fake_api):
    Scenario(fake_api)
    service = _service(db, app_config, make_client(fake_api, db=db))
    service.run_daily(DAY)
    calls_before = len(fake_api.calls)
    service.run_daily(DAY)
    assert len(fake_api.calls) == calls_before


def test_quota_exhaustion_is_partial_not_failed(db, app_config):
    fake = FakeApiFootball(daily_limit=12)
    Scenario(fake)
    report = _service(db, app_config, make_client(fake, config=ApiFootballConfig(daily_reserve=5, requests_per_minute=10_000, max_retries=1))).run_daily(DAY)

    assert report.status == RunStatus.PARTIAL
    assert report.deep_completed == 0 and report.skipped_due_to_quota == 3
    assert any("limiti bitib" in w for w in report.warnings)
    assert any("3 oyun API limiti" in w for w in report.warnings)
    assert report.daily_remaining == 5


def test_provider_error_in_one_step_does_not_stop_the_run(db, app_config, fake_api):
    Scenario(fake_api, odds_error_for=1002)
    report = _service(db, app_config, make_client(fake_api, db=db)).run_daily(DAY)

    assert report.status == RunStatus.PARTIAL
    assert report.deep_completed == 3
    assert report.warnings == ["Əmsallar (Team 33 – Team 40): ⚠️ Məlumat mənbəyindən cavab alınmadı."]


def test_plan_restriction_fails_with_azerbaijani_message(db, app_config, fake_api):
    fake_api.route(
        "fixtures",
        envelope("fixtures", [], errors={"plan": "Free plans do not have access to this date, try from 2026-10-04."}),
    )
    report = _service(db, app_config, make_client(fake_api)).run_daily(DAY)

    assert report.status == RunStatus.FAILED
    assert report.warnings == [
        "Oyunların yüklənməsi: ⚠️ Cari API planı bu məlumata giriş vermir (plan məhdudiyyəti)."
    ]
    assert not any("Free plans" in w for w in report.warnings)


def test_league_id_country_mismatch_is_reported(db, app_config, fake_api):
    app_config.leagues[1].country = "Portugal"  # La Liga configured with the wrong country
    Scenario(fake_api)
    report = _service(db, app_config, make_client(fake_api, db=db)).run_daily(DAY)
    assert any("Liqa ID 140" in w and "Portugal" in w for w in report.warnings)


def test_overview_after_ingestion(db, app_config, fake_api):
    Scenario(fake_api)
    _service(db, app_config, make_client(fake_api, db=db)).run_daily(DAY)
    overview = build_daily_overview(db, DAY, TZ)

    assert overview.loaded
    assert [block.title for block in overview.blocks] == ["İngiltərə — Premyer Liqa", "İspaniya — La Liqa"]
    assert (overview.priority_count, overview.ready_count, overview.incomplete_count) == (3, 1, 2)
    assert overview.other_leagues_count == 1
    first = overview.blocks[0].matches[0]
    assert first.kickoff_local.strftime("%H:%M") == "19:30" and first.has_odds


def test_classify_injury():
    assert classify_injury("Missing Fixture", "Knee Injury") == InjuryKind.INJURY
    assert classify_injury("Missing Fixture", "Suspended") == InjuryKind.SUSPENSION
    assert classify_injury("Missing Fixture", "Red Card") == InjuryKind.SUSPENSION
    assert classify_injury("Questionable", "Hamstring") == InjuryKind.DOUBTFUL
