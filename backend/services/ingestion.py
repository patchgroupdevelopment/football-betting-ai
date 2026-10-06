"""Daily data ingestion: API-Football -> local database.

Order of work for a day:
1. all fixtures of the day (one request) -> keep the configured leagues;
2. standings per league and injuries for all selected fixtures (batched);
3. per fixture, in league-priority order: both teams' recent matches with
   statistics, head-to-head, and bookmaker odds.

Every step is isolated: a provider error is recorded as an Azerbaijani warning
and the run continues. When the daily quota runs out, the remaining fixtures
are reported as skipped instead of failing the run.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config import AppConfig, LeagueConfig
from backend.database.session import Database
from backend.i18n import t
from backend.models import (
    Injury,
    League,
    Lineup,
    Match,
    MatchStats,
    ModelRun,
    OddsSnapshot,
    Player,
    PlayerMatchStats,
    Result,
    Standing,
    Team,
)
from backend.models.constants import NOT_STARTED_STATUSES, InjuryKind, RunStatus
from backend.schemas.api_football import FixtureDTO, InjuryDTO, LeagueRef, TeamRef
from backend.services.errors import PlanRestrictedError, ProviderError, QuotaExhaustedError
from backend.services.external.linking import adopt_unlinked_team, find_same_match
from backend.services.providers.api_football import ApiFootballClient
from backend.services.providers.odds_markets import normalize_bet
from backend.services.runs import RUN_KIND_DAILY
from backend.utils.timeutils import analysis_window, utcnow

logger = logging.getLogger(__name__)

T = TypeVar("T")
MAX_STORED_WARNINGS = 30
H2H_DEPTH = 10
UNCONFIGURED_PRIORITY = 10_000
ODDS_SOURCE = "api_football"
# Steps whose data the free sources (football-data.co.uk / .org) provide when the API plan refuses them.
COVERED_BY_FREE_SOURCES = frozenset({"history", "h2h", "standings"})

LeagueIndex = dict[int, tuple[int, LeagueConfig]]


@dataclass
class IngestionReport:
    target_date: date
    started_at: datetime
    finished_at: datetime | None = None
    status: str = RunStatus.RUNNING
    total_fixtures: int = 0
    priority_fixtures: int = 0
    other_fixtures: int = 0
    deep_planned: int = 0
    deep_completed: int = 0
    skipped_due_to_quota: int = 0
    requests_used: int = 0
    daily_remaining: int | None = None
    daily_limit: int | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # informational, e.g. data taken from free sources
    external: Any = None  # ExternalSyncReport of the free-source import that ran before

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def to_details(self) -> dict[str, Any]:
        return {
            "total_fixtures": self.total_fixtures,
            "priority_fixtures": self.priority_fixtures,
            "other_fixtures": self.other_fixtures,
            "deep_planned": self.deep_planned,
            "deep_completed": self.deep_completed,
            "skipped_due_to_quota": self.skipped_due_to_quota,
            "requests_used": self.requests_used,
            "daily_remaining": self.daily_remaining,
            "daily_limit": self.daily_limit,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class MatchRef:
    """Plain identifiers of a stored match, safe to pass between sessions."""

    id: int
    api_id: int
    league_api_id: int
    season: int
    home_api_id: int
    away_api_id: int
    label: str
    sort_key: tuple[int, datetime]
    not_started: bool = True


def classify_injury(api_type: str | None, reason: str | None) -> InjuryKind:
    text = (reason or "").lower()
    if "suspend" in text or "card" in text:
        return InjuryKind.SUSPENSION
    if (api_type or "").lower() == "questionable":
        return InjuryKind.DOUBTFUL
    return InjuryKind.INJURY


class _Upserter:
    """Get-or-create helpers bound to one session, with per-session identity maps."""

    def __init__(
        self, session: Session, leagues: LeagueIndex, clock: Callable[[], datetime], name_threshold: float = 0.84
    ) -> None:
        self.s = session
        self._league_index = leagues
        self._clock = clock
        self._name_threshold = name_threshold
        self._leagues: dict[int, League] = {}
        self._teams: dict[int, Team] = {}
        self._players: dict[int, Player] = {}

    def league(self, ref: LeagueRef) -> League:
        league = self._leagues.get(ref.api_id) or self.s.scalar(select(League).where(League.api_id == ref.api_id))
        if league is None:
            league = League(api_id=ref.api_id, name=ref.name or str(ref.api_id))
            self.s.add(league)
        if ref.name:
            league.name = ref.name
        league.country = ref.country or league.country
        league.logo_url = ref.logo or league.logo_url
        configured = self._league_index.get(ref.api_id)
        if configured is not None:
            priority, cfg = configured
            league.priority, league.tier, league.name_az = priority, cfg.tier, cfg.name_az
        else:
            league.priority = league.tier = league.name_az = None
        if league.id is None:
            self.s.flush()
        self._leagues[ref.api_id] = league
        return league

    def team(self, ref: TeamRef, league_id: int | None = None) -> Team:
        team = self._teams.get(ref.api_id) or self.s.scalar(select(Team).where(Team.api_id == ref.api_id))
        if team is None and ref.name:
            # Already known from a free source under a slightly different name? Link instead of duplicating.
            team = adopt_unlinked_team(self.s, ref.name, league_id, self._name_threshold)
            if team is not None:
                team.api_id = ref.api_id
        if team is None:
            team = Team(api_id=ref.api_id, name=ref.name or str(ref.api_id))
            self.s.add(team)
        if ref.name:
            team.name = ref.name
        team.logo_url = ref.logo or team.logo_url
        if team.id is None:
            self.s.flush()
        self._teams[ref.api_id] = team
        return team

    def player(
        self, api_id: int, name: str | None, team_id: int, position: str | None = None, number: int | None = None
    ) -> Player:
        player = self._players.get(api_id) or self.s.scalar(select(Player).where(Player.api_id == api_id))
        if player is None:
            player = Player(api_id=api_id, name=name or str(api_id), role_tags=[])
            self.s.add(player)
        if name:
            player.name = name
        player.team_id = team_id
        player.position = position or player.position
        player.number = number if number is not None else player.number
        if player.id is None:
            self.s.flush()
        self._players[api_id] = player
        return player

    def match(self, dto: FixtureDTO, *, priority: bool = False) -> Match:
        league = self.league(dto.league)
        home = self.team(dto.home, league.id)
        away = self.team(dto.away, league.id)
        match = self.s.scalar(select(Match).where(Match.api_id == dto.api_id))
        if match is None:
            # The same game may already be stored from a free source: adopt it.
            match = find_same_match(self.s, home.id, away.id, dto.kickoff_utc)
            if match is not None and match.api_id is None:
                match.api_id = dto.api_id
            elif match is not None:
                match = None
        if match is None:
            match = Match(api_id=dto.api_id, is_priority=False)
            self.s.add(match)
        match.league_id = league.id
        match.season = dto.league.season or match.season or dto.kickoff_utc.year
        match.round_name = dto.league.round or match.round_name
        match.kickoff_utc = dto.kickoff_utc
        match.home_team_id = home.id
        match.away_team_id = away.id
        match.status = dto.status
        match.home_goals = dto.home_goals
        match.away_goals = dto.away_goals
        match.ht_home_goals = dto.ht_home_goals
        match.ht_away_goals = dto.ht_away_goals
        match.referee = dto.referee or match.referee
        match.venue = dto.venue or match.venue
        if priority:
            match.is_priority = True
        self.s.flush()
        return match

    def match_details(self, match: Match, dto: FixtureDTO) -> None:
        """Store statistics, line-ups and (for finished games) the result."""
        for stats in dto.statistics:
            team = self.team(TeamRef(stats.team_api_id, stats.team_name))
            row = self.s.scalar(
                select(MatchStats).where(MatchStats.match_id == match.id, MatchStats.team_id == team.id)
            )
            if row is None:
                row = MatchStats(match_id=match.id, team_id=team.id, is_home=False)
                self.s.add(row)
            row.is_home = stats.team_api_id == dto.home.api_id
            for column, value in stats.values.items():
                setattr(row, column, value)
            row.xg_source = "api" if stats.values.get("xg") is not None else None

        for lineup in dto.lineups:
            team = self.team(TeamRef(lineup.team_api_id, lineup.team_name))
            row = self.s.scalar(select(Lineup).where(Lineup.match_id == match.id, Lineup.team_id == team.id))
            if row is None:
                row = Lineup(match_id=match.id, team_id=team.id)
                self.s.add(row)
            row.formation = lineup.formation
            row.coach = lineup.coach
            row.starters = list(lineup.starters)
            row.substitutes = list(lineup.substitutes)
            row.confirmed_at = self._clock()
            for entry in (*lineup.starters, *lineup.substitutes):
                if entry.get("api_id"):
                    self.player(entry["api_id"], entry.get("name"), team.id, entry.get("pos"), entry.get("number"))

        for stats in dto.players:
            team = self.team(TeamRef(stats.team_api_id, stats.team_name))
            player = self.player(stats.player_api_id, stats.player_name, team.id, stats.position)
            row = self.s.scalar(
                select(PlayerMatchStats).where(
                    PlayerMatchStats.match_id == match.id, PlayerMatchStats.player_id == player.id
                )
            )
            if row is None:
                row = PlayerMatchStats(match_id=match.id, player_id=player.id, team_id=team.id)
                self.s.add(row)
            row.team_id = team.id
            row.minutes = stats.minutes
            row.position = stats.position
            row.rating = stats.rating
            row.substitute = stats.substitute
            row.goals = stats.goals
            row.assists = stats.assists
            row.yellow_cards = stats.yellow_cards
            row.red_cards = stats.red_cards

        if dto.is_finished:
            self._result(match, dto)
        match.details_fetched_at = self._clock()

    def _result(self, match: Match, dto: FixtureDTO) -> None:
        corners = [s.values.get("corners") for s in dto.statistics]
        cards = [(s.values.get("yellow_cards") or 0) + (s.values.get("red_cards") or 0) for s in dto.statistics]
        result = self.s.scalar(select(Result).where(Result.match_id == match.id))
        if result is None:
            result = Result(match_id=match.id)
            self.s.add(result)
        result.home_goals = dto.home_goals
        result.away_goals = dto.away_goals
        result.ht_home_goals = dto.ht_home_goals
        result.ht_away_goals = dto.ht_away_goals
        complete_stats = len(dto.statistics) == 2
        result.total_corners = sum(corners) if complete_stats and None not in corners else None
        result.total_cards = sum(cards) if complete_stats else None
        result.settled_at = self._clock()


class IngestionService:
    def __init__(
        self,
        db: Database,
        client: ApiFootballClient,
        config: AppConfig,
        tz_name: str,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._client = client
        self._config = config
        self._tz_name = tz_name
        self._tz = ZoneInfo(tz_name)
        self._clock = clock
        self._leagues: LeagueIndex = {cfg.api_id: (index, cfg) for index, cfg in enumerate(config.leagues)}
        self._quota_exhausted = False
        self._restricted: set[str] = set()  # steps the API plan does not allow (skipped after the first refusal)

    def _upserter(self, session: Session) -> _Upserter:
        return _Upserter(session, self._leagues, self._clock, self._config.external_data.name_match_threshold)

    # ----------------------------------------------------------------- run

    def run_daily(self, target_date: date) -> IngestionReport:
        report = IngestionReport(target_date=target_date, started_at=self._clock())
        requests_before = self._client.quota.requests_made
        self._quota_exhausted = False
        self._restricted = set()
        run_id = self._start_run(target_date)
        try:
            ok, today = self._step(
                report, "fixtures", lambda: self._client.fixtures_by_date(target_date, self._tz_name)
            )
            if not ok or today is None:
                report.status = RunStatus.FAILED
            else:
                # The analysis window reaches into the next morning; that day's list is optional.
                _, tomorrow = self._step(
                    report,
                    "fixtures_next",
                    lambda: self._client.fixtures_by_date(target_date + timedelta(days=1), self._tz_name),
                )
                # Yesterday's results (inside the free plan's date window) build history day by day,
                # also for leagues the free sources do not cover.
                _, yesterday = self._step(
                    report,
                    "fixtures_prev",
                    lambda: self._client.fixtures_by_date(target_date - timedelta(days=1), self._tz_name),
                )
                self._store_results(yesterday or [])
                fixtures = self._within_window([*today, *(tomorrow or [])], target_date)
                refs = self._store_daily_fixtures(fixtures, report)
                if refs:
                    upcoming = [ref for ref in refs if ref.not_started]
                    self._ingest_standings(refs, report)
                    if upcoming:
                        self._step(report, "injuries", lambda: self._ingest_injuries(upcoming))
                    self._ingest_deep(refs, report)
                report.status = RunStatus.PARTIAL if report.warnings else RunStatus.SUCCESS
        except Exception:
            logger.exception("Gündəlik yükləmədə gözlənilməz xəta")
            self._add_warning(report, t("error.unexpected"))
            report.status = RunStatus.FAILED
        finally:
            report.finished_at = self._clock()
            report.requests_used = self._client.quota.requests_made - requests_before
            report.daily_remaining = self._client.quota.daily_remaining
            report.daily_limit = self._client.quota.daily_limit
            self._finish_run(run_id, report)
        logger.info(
            "Yükləmə bitdi: %s, tarix %s, oyun %d (prioritet %d), ətraflı %d/%d, sorğu %d",
            report.status, target_date, report.total_fixtures, report.priority_fixtures,
            report.deep_completed, report.deep_planned, report.requests_used,
        )
        return report

    # --------------------------------------------------------------- steps

    def _store_results(self, fixtures: list[FixtureDTO]) -> None:
        finished = [f for f in fixtures if f.is_finished and f.league.api_id in self._leagues]
        if not finished:
            return
        with self._db.session() as session:
            upsert = self._upserter(session)
            for dto in finished:
                upsert.match(dto)

    def _within_window(self, fixtures: list[FixtureDTO], target_date: date) -> list[FixtureDTO]:
        start, end = analysis_window(target_date, self._tz, self._config.schedule.daily_pipeline)
        unique = {dto.api_id: dto for dto in fixtures if start <= dto.kickoff_utc < end}
        return list(unique.values())

    def _store_daily_fixtures(self, fixtures: list[FixtureDTO], report: IngestionReport) -> list[MatchRef]:
        configured = [f for f in fixtures if f.league.api_id in self._leagues]
        others = [f for f in fixtures if f.league.api_id not in self._leagues]
        report.total_fixtures = len(fixtures)
        report.priority_fixtures = len(configured)
        report.other_fixtures = len(others)
        self._check_league_countries(configured, report)

        selected = configured + (others if self._config.ingestion.include_other_leagues else [])
        refs: list[MatchRef] = []
        with self._db.session() as session:
            upsert = self._upserter(session)
            for dto in selected:
                match = upsert.match(dto, priority=True)
                refs.append(self._ref(match, dto))
        refs.sort(key=lambda ref: ref.sort_key)
        return refs

    def _ingest_standings(self, refs: list[MatchRef], report: IngestionReport) -> None:
        pairs = sorted({(r.league_api_id, r.season) for r in refs if r.league_api_id in self._leagues})
        for league_api_id, season in pairs:
            name = self._leagues[league_api_id][1].name_az
            self._step(report, "standings", lambda: self._store_standings(league_api_id, season), context=name)

    def _store_standings(self, league_api_id: int, season: int) -> bool:
        rows = self._client.standings(league_api_id, season)
        with self._db.session() as session:
            league = session.scalar(select(League).where(League.api_id == league_api_id))
            if league is None:
                return True
            upsert = self._upserter(session)
            session.execute(delete(Standing).where(Standing.league_id == league.id, Standing.season == season))
            seen: set[tuple[int, str]] = set()
            for row in rows:
                team = upsert.team(row.team, league.id)
                key = (team.id, row.group or "")
                if key in seen:
                    continue
                seen.add(key)
                session.add(
                    Standing(
                        league_id=league.id,
                        season=season,
                        team_id=team.id,
                        group_name=row.group or "",
                        rank=row.rank,
                        points=row.points,
                        played=row.played,
                        won=row.won,
                        drawn=row.drawn,
                        lost=row.lost,
                        goals_for=row.goals_for,
                        goals_against=row.goals_against,
                        goal_diff=row.goal_diff,
                        form=row.form,
                        description=row.description,
                        home=row.home,
                        away=row.away,
                    )
                )
        return True

    def _ingest_injuries(self, refs: list[MatchRef]) -> bool:
        injuries = self._client.injuries_for_fixtures([ref.api_id for ref in refs])
        by_fixture: dict[int, list[InjuryDTO]] = defaultdict(list)
        for injury in injuries:
            by_fixture[injury.fixture_api_id].append(injury)

        now = self._clock()
        with self._db.session() as session:
            upsert = self._upserter(session)
            for ref in refs:
                session.execute(delete(Injury).where(Injury.match_id == ref.id))
                league_id = session.scalar(select(Match.league_id).where(Match.id == ref.id))
                seen: set[int] = set()
                for injury in by_fixture.get(ref.api_id, []):
                    if injury.player_api_id in seen:
                        continue
                    seen.add(injury.player_api_id)
                    team = upsert.team(injury.team, league_id)
                    player = upsert.player(injury.player_api_id, injury.player_name, team.id)
                    session.add(
                        Injury(
                            match_id=ref.id,
                            player_id=player.id,
                            team_id=team.id,
                            kind=classify_injury(injury.api_type, injury.reason),
                            reason=injury.reason,
                            api_type=injury.api_type,
                            fetched_at=now,
                        )
                    )
                match = session.get(Match, ref.id)
                if match is not None:
                    match.injuries_checked_at = now
        return True

    def _ingest_deep(self, refs: list[MatchRef], report: IngestionReport) -> None:
        # Started or finished matches cannot be bet on: do not spend the daily quota on them.
        planned = [ref for ref in refs if ref.not_started][: self._config.ingestion.max_deep_matches]
        report.deep_planned = len(planned)
        steps: tuple[tuple[str, Callable[[MatchRef], bool]], ...] = (
            ("history", self._ingest_history),
            ("h2h", self._ingest_h2h),
            ("odds", self._ingest_odds),
        )
        for ref in planned:
            if self._quota_exhausted:
                break
            for step, func in steps:
                self._step(report, step, lambda func=func: func(ref), context=ref.label)
                if self._quota_exhausted:
                    break
            else:
                report.deep_completed += 1
        if self._quota_exhausted:
            report.skipped_due_to_quota = report.deep_planned - report.deep_completed
            if report.skipped_due_to_quota:
                self._add_warning(report, t("ingest.skipped_quota", count=report.skipped_due_to_quota))

    def _ingest_history(self, ref: MatchRef) -> bool:
        depth = self._config.ingestion.history_depth
        missing_details: set[int] = set()
        for team_api_id in (ref.home_api_id, ref.away_api_id):
            fixtures = self._client.team_last_fixtures(team_api_id, depth)
            with self._db.session() as session:
                upsert = self._upserter(session)
                for dto in fixtures:
                    match = upsert.match(dto)
                    if dto.is_finished and match.details_fetched_at is None:
                        missing_details.add(dto.api_id)

        if missing_details:
            details = self._client.fixtures_by_ids(sorted(missing_details))
            with self._db.session() as session:
                upsert = self._upserter(session)
                for dto in details:
                    upsert.match_details(upsert.match(dto), dto)

        self._touch(ref.id, "history_checked_at")
        return True

    def _ingest_h2h(self, ref: MatchRef) -> bool:
        fixtures = self._client.head_to_head(ref.home_api_id, ref.away_api_id, H2H_DEPTH)
        with self._db.session() as session:
            upsert = self._upserter(session)
            for dto in fixtures:
                upsert.match(dto)
        self._touch(ref.id, "h2h_checked_at")
        return True

    def _ingest_odds(self, ref: MatchRef) -> bool:
        odds = self._client.odds_for_fixture(ref.api_id)
        now = self._clock()
        with self._db.session() as session:
            latest = self._latest_prices(session, ref.id)
            for value in odds.values if odds else ():
                normalized = normalize_bet(value.bet_name, value.value)
                if normalized is None or value.price is None or value.price <= 1:
                    continue
                market, selection, line = normalized
                key = (value.bookmaker_api_id, market, selection, line)
                if latest.get(key) == value.price:
                    continue
                latest[key] = value.price
                session.add(
                    OddsSnapshot(
                        match_id=ref.id,
                        source=ODDS_SOURCE,
                        bookmaker_api_id=value.bookmaker_api_id,
                        bookmaker=value.bookmaker,
                        market=market,
                        selection=selection,
                        line=line,
                        price=value.price,
                        captured_at=now,
                    )
                )
            match = session.get(Match, ref.id)
            if match is not None:
                match.odds_checked_at = now
        return True

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _latest_prices(session: Session, match_id: int) -> dict[tuple[Any, ...], float]:
        rows = session.execute(
            select(
                OddsSnapshot.bookmaker_api_id,
                OddsSnapshot.market,
                OddsSnapshot.selection,
                OddsSnapshot.line,
                OddsSnapshot.price,
            )
            .where(OddsSnapshot.match_id == match_id, OddsSnapshot.source == ODDS_SOURCE)
            .order_by(OddsSnapshot.captured_at, OddsSnapshot.id)
        )
        return {(r.bookmaker_api_id, r.market, r.selection, r.line): r.price for r in rows}

    def _touch(self, match_id: int, column: str) -> None:
        with self._db.session() as session:
            match = session.get(Match, match_id)
            if match is not None:
                setattr(match, column, self._clock())

    def _ref(self, match: Match, dto: FixtureDTO) -> MatchRef:
        priority = self._leagues.get(dto.league.api_id, (UNCONFIGURED_PRIORITY, None))[0]
        return MatchRef(
            id=match.id,
            api_id=dto.api_id,
            league_api_id=dto.league.api_id,
            season=match.season,
            home_api_id=dto.home.api_id,
            away_api_id=dto.away.api_id,
            label=dto.label,
            sort_key=(priority, dto.kickoff_utc),
            not_started=dto.status in NOT_STARTED_STATUSES,
        )

    def _check_league_countries(self, fixtures: list[FixtureDTO], report: IngestionReport) -> None:
        """Warn when a configured league ID returns an unexpected country (wrong ID in config.yaml)."""
        checked: set[int] = set()
        for dto in fixtures:
            if dto.league.api_id in checked:
                continue
            checked.add(dto.league.api_id)
            expected = self._leagues[dto.league.api_id][1].country
            actual = dto.league.country
            if expected and actual and expected.casefold() != actual.casefold():
                self._add_warning(
                    report,
                    t("ingest.league_mismatch", api_id=dto.league.api_id, expected=expected, actual=actual),
                )

    def _step(
        self, report: IngestionReport, step: str, func: Callable[[], T], *, context: str | None = None
    ) -> tuple[bool, T | None]:
        if self._quota_exhausted or step in self._restricted:
            return False, None
        try:
            return True, func()
        except QuotaExhaustedError as exc:
            self._quota_exhausted = True
            logger.warning("API limiti bitdi (%s): %s", step, exc.detail)
            self._add_warning(report, exc.user_message)
        except PlanRestrictedError as exc:
            if step in COVERED_BY_FREE_SOURCES:
                # Expected on the free plan: note it once and stop asking for the rest of the run.
                self._restricted.add(step)
                logger.info("Plan məhdudiyyəti (%s): %s — pulsuz mənbələr istifadə olunur", step, exc.detail)
                report.notes.append(t("ingest.plan_skip", step=t(f"step.{step}")))
            else:
                logger.warning("Addım uğursuz oldu: %s (%s): %s", step, context or "-", exc.detail)
                self._add_warning(report, self._step_message(step, exc.user_message, context))
        except ProviderError as exc:
            logger.warning("Addım uğursuz oldu: %s (%s): %s", step, context or "-", exc.detail or exc)
            self._add_warning(report, self._step_message(step, exc.user_message, context))
        except Exception:
            logger.exception("Addımda gözlənilməz xəta: %s (%s)", step, context or "-")
            self._add_warning(report, self._step_message(step, t("error.unexpected"), context))
        return False, None

    @staticmethod
    def _step_message(step: str, message: str, context: str | None) -> str:
        if context:
            return t("ingest.step_error_context", step=t(f"step.{step}"), context=context, message=message)
        return t("ingest.step_error", step=t(f"step.{step}"), message=message)

    @staticmethod
    def _add_warning(report: IngestionReport, message: str) -> None:
        if message not in report.warnings and len(report.warnings) < MAX_STORED_WARNINGS:
            report.warnings.append(message)

    def _start_run(self, target_date: date) -> int:
        with self._db.session() as session:
            run = ModelRun(
                kind=RUN_KIND_DAILY,
                run_date=target_date,
                started_at=self._clock(),
                status=RunStatus.RUNNING,
                details={},
            )
            session.add(run)
            session.flush()
            return run.id

    def _finish_run(self, run_id: int, report: IngestionReport) -> None:
        with self._db.session() as session:
            run = session.get(ModelRun, run_id)
            if run is not None:
                run.finished_at = report.finished_at
                run.status = report.status
                run.details = report.to_details()
