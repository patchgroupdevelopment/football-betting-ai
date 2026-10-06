"""Daily import from the free sources into the database.

For every configured league:
- results (+ shots, corners, cards, xG) from football-data.co.uk when it covers
  the league, otherwise results from football-data.org;
- standings and top scorers from football-data.org when it covers the league.
Leagues with football-data.co.uk run first, football-data.org-only
competitions (Champions League) last, so clubs are already known by then.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.config import AppConfig, LeagueConfig
from backend.database.session import Database
from backend.i18n import t
from backend.models import League, Match, ScorerStat, Standing
from backend.services.errors import ProviderError
from backend.services.external import football_data_couk as couk_module
from backend.services.external import football_data_org as org_module
from backend.services.external.football_data_couk import CsvMatch, FootballDataCoUk
from backend.services.external.football_data_org import FootballDataOrgClient, OrgMatch
from backend.services.external.linking import (
    TeamResolver,
    fill_match_stats,
    find_same_match,
    link_alias,
    upsert_result,
)
from backend.utils.timeutils import utcnow

logger = logging.getLogger(__name__)
MAX_WARNINGS = 20
FIXTURE_TOLERANCE_SECONDS = 12 * 3600  # football-data.co.uk sometimes lacks the kick-off time
MIN_FIXTURE_VOTES = 3.0
FIXTURE_AGREEMENT = 0.6


@dataclass
class ExternalSyncReport:
    matches_added: int = 0
    matches_updated: int = 0
    with_xg: int = 0
    standings_leagues: int = 0
    scorer_leagues: int = 0
    teams_created: int = 0
    teams_linked: int = 0
    warnings: list[str] = field(default_factory=list)


class ExternalDataSync:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        couk: FootballDataCoUk | None,
        org: FootballDataOrgClient | None,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._config = config
        self._couk = couk
        self._org = org
        self._clock = clock
        self._threshold = config.external_data.name_match_threshold

    def close(self) -> None:
        for client in (self._couk, self._org):
            if client is not None:
                client.close()

    def run(self, today: date) -> ExternalSyncReport:
        report = ExternalSyncReport()
        ordered = sorted(
            (cfg for cfg in self._config.leagues if cfg.fd_couk or cfg.fd_org),
            key=lambda cfg: 0 if cfg.fd_couk else 1,
        )
        for cfg in ordered:
            if cfg.fd_couk and self._couk is not None:
                self._guard(report, "external_couk", cfg, lambda cfg=cfg: self._import_couk(cfg, today, report))
                if cfg.fd_org and self._org is not None:
                    self._guard(report, "external_org_linking", cfg, lambda cfg=cfg: self._link_org_by_fixtures(cfg, report))
            elif cfg.fd_org and self._org is not None:
                self._guard(report, "external_org_matches", cfg, lambda cfg=cfg: self._import_org_matches(cfg, report))
            if cfg.fd_org and self._org is not None:
                self._guard(report, "external_org_standings", cfg, lambda cfg=cfg: self._import_standings(cfg, report))
                self._guard(report, "external_org_scorers", cfg, lambda cfg=cfg: self._import_scorers(cfg, report))
        logger.info(
            "Pulsuz mənbələr: +%d oyun (%d xG ilə), %d yenilənib, cədvəl %d, bombardirlər %d, komanda +%d / %d uyğunlaşdı",
            report.matches_added, report.with_xg, report.matches_updated, report.standings_leagues,
            report.scorer_leagues, report.teams_created, report.teams_linked,
        )
        return report

    # ------------------------------------------------------------------ steps

    def _guard(self, report: ExternalSyncReport, step: str, cfg: LeagueConfig, func: Callable[[], Any]) -> None:
        try:
            func()
        except ProviderError as exc:
            logger.warning("Pulsuz mənbə addımı alınmadı: %s (%s): %s", step, cfg.name_az, exc.detail or exc)
            self._warn(report, t("ingest.step_error_context", step=t(f"step.{step}"), context=cfg.name_az, message=exc.user_message))
        except Exception:
            logger.exception("Pulsuz mənbə addımında gözlənilməz xəta: %s (%s)", step, cfg.name_az)
            self._warn(report, t("ingest.step_error_context", step=t(f"step.{step}"), context=cfg.name_az, message=t("error.unexpected")))

    @staticmethod
    def _warn(report: ExternalSyncReport, message: str) -> None:
        if message not in report.warnings and len(report.warnings) < MAX_WARNINGS:
            report.warnings.append(message)

    def _league(self, session: Session, cfg: LeagueConfig) -> League:
        league = session.scalar(select(League).where(League.api_id == cfg.api_id))
        if league is None:
            league = League(api_id=cfg.api_id, name=cfg.name_az, country=cfg.country)
            session.add(league)
        priority = next(i for i, c in enumerate(self._config.leagues) if c.api_id == cfg.api_id)
        league.priority, league.tier, league.name_az = priority, cfg.tier, cfg.name_az
        session.flush()
        return league

    def _store_match(
        self,
        session: Session,
        resolver: TeamResolver,
        league: League,
        report: ExternalSyncReport,
        *,
        source: str,
        external_id: str,
        alias_keys: tuple[str, str],
        names: tuple[list[str], list[str]],
        kickoff: datetime,
        season: int,
        goals: tuple[int, int],
        halftime: tuple[int | None, int | None],
        referee: str | None,
        round_name: str | None = None,
        stats: tuple[dict, dict] | None = None,
    ) -> None:
        home = resolver.resolve(source, alias_keys[0], names[0], league.id)
        away = resolver.resolve(source, alias_keys[1], names[1], league.id)
        if home.id == away.id:
            return  # a name collision; never store a team playing itself
        match = session.scalar(select(Match).where(Match.source == source, Match.external_id == external_id))
        if match is None:
            match = find_same_match(session, home.id, away.id, kickoff)
        created = match is None
        if match is None:
            match = Match(
                api_id=None, source=source, external_id=external_id, league_id=league.id, season=season,
                kickoff_utc=kickoff, home_team_id=home.id, away_team_id=away.id, is_priority=False,
            )
            session.add(match)
        if match.home_goals is None:  # never overwrite a result API-Football already delivered
            match.status = "FT"
            match.home_goals, match.away_goals = goals
            match.ht_home_goals, match.ht_away_goals = halftime
        match.referee = match.referee or referee
        match.round_name = match.round_name or round_name
        session.flush()
        if stats is not None:
            fill_match_stats(session, match, home.id, True, stats[0], source)
            fill_match_stats(session, match, away.id, False, stats[1], source)
        upsert_result(session, match)
        if created:
            report.matches_added += 1
        else:
            report.matches_updated += 1

    def _import_couk(self, cfg: LeagueConfig, today: date, report: ExternalSyncReport) -> None:
        assert self._couk is not None and cfg.fd_couk is not None
        matches: list[CsvMatch] = self._couk.division(cfg.fd_couk, today)
        country = cfg.country or cfg.fd_couk
        with self._db.session() as session:
            league = self._league(session, cfg)
            resolver = TeamResolver(session, self._threshold)
            for m in matches:
                self._store_match(
                    session, resolver, league, report,
                    source=couk_module.SOURCE,
                    external_id=m.external_id,
                    alias_keys=(f"{country}:{m.home}", f"{country}:{m.away}"),
                    names=([m.home], [m.away]),
                    kickoff=m.kickoff_utc,
                    season=m.season,
                    goals=(m.home_goals, m.away_goals),
                    halftime=(m.ht_home_goals, m.ht_away_goals),
                    referee=m.referee,
                    stats=(m.home_stats, m.away_stats),
                )
                report.with_xg += int(m.has_xg)
            league.full_results = bool(matches) or league.full_results
            report.teams_created += resolver.created
            report.teams_linked += resolver.linked

    def _link_org_by_fixtures(self, cfg: LeagueConfig, report: ExternalSyncReport) -> None:
        """Link football-data.org teams to football-data.co.uk teams through the games both report.

        Names can differ completely ("Lyon" / "Olympique Lyonnais", "Athletico-PR" /
        "CA Paranaense"); the same kick-off with the same score cannot. Each
        football-data.org fixture votes for the teams of the matching stored
        game(s); a team is linked when the votes clearly agree.
        """
        assert self._org is not None and cfg.fd_org is not None
        org_matches = self._org.finished_matches(cfg.fd_org)
        with self._db.session() as session:
            league = self._league(session, cfg)
            stored = session.execute(
                select(Match.kickoff_utc, Match.home_team_id, Match.away_team_id, Match.home_goals, Match.away_goals).where(
                    Match.league_id == league.id, Match.source == couk_module.SOURCE
                )
            ).all()
            by_score: dict[tuple[int, int], list[Any]] = defaultdict(list)
            for row in stored:
                by_score[(row.home_goals, row.away_goals)].append(row)

            votes: dict[int, Counter[int]] = defaultdict(Counter)
            appearances: Counter[int] = Counter()
            names: dict[int, str] = {}
            for m in org_matches:
                candidates = [
                    r for r in by_score.get((m.home_goals, m.away_goals), [])
                    if abs((r.kickoff_utc - m.kickoff_utc).total_seconds()) <= FIXTURE_TOLERANCE_SECONDS
                ]
                if not candidates:
                    continue
                weight = 1.0 / len(candidates)
                for r in candidates:
                    votes[m.home.id][r.home_team_id] += weight
                    votes[m.away.id][r.away_team_id] += weight
                appearances[m.home.id] += 1
                appearances[m.away.id] += 1
                names[m.home.id], names[m.away.id] = m.home.name, m.away.name

            claimed: set[int] = set()
            for org_id, counter in sorted(votes.items(), key=lambda item: -max(item[1].values())):
                team_id, score = counter.most_common(1)[0]
                if score < MIN_FIXTURE_VOTES or score < FIXTURE_AGREEMENT * appearances[org_id] or team_id in claimed:
                    continue
                claimed.add(team_id)
                if link_alias(session, org_module.SOURCE, str(org_id), names[org_id], team_id):
                    report.teams_linked += 1

    def _import_org_matches(self, cfg: LeagueConfig, report: ExternalSyncReport) -> None:
        assert self._org is not None and cfg.fd_org is not None
        matches: list[OrgMatch] = self._org.finished_matches(cfg.fd_org)
        with self._db.session() as session:
            league = self._league(session, cfg)
            resolver = TeamResolver(session, self._threshold)
            for m in matches:
                self._store_match(
                    session, resolver, league, report,
                    source=org_module.SOURCE,
                    external_id=str(m.id),
                    alias_keys=(str(m.home.id), str(m.away.id)),
                    names=(m.home.names, m.away.names),
                    kickoff=m.kickoff_utc,
                    season=m.season,
                    goals=(m.home_goals, m.away_goals),
                    halftime=(m.ht_home_goals, m.ht_away_goals),
                    referee=m.referee,
                    round_name=m.stage,
                )
            league.full_results = bool(matches) or league.full_results
            report.teams_created += resolver.created
            report.teams_linked += resolver.linked

    def _import_standings(self, cfg: LeagueConfig, report: ExternalSyncReport) -> None:
        assert self._org is not None and cfg.fd_org is not None
        season, rows = self._org.standings(cfg.fd_org)
        if season is None or not rows:
            return
        with self._db.session() as session:
            league = self._league(session, cfg)
            resolver = TeamResolver(session, self._threshold)
            session.execute(delete(Standing).where(Standing.league_id == league.id, Standing.season == season))
            seen: set[tuple[int, str]] = set()
            for row in rows:
                team = resolver.resolve(org_module.SOURCE, str(row.team.id), row.team.names, league.id)
                key = (team.id, row.group or "")
                if key in seen:
                    continue
                seen.add(key)
                session.add(
                    Standing(
                        league_id=league.id, season=season, team_id=team.id, group_name=row.group or "",
                        rank=row.position, points=row.points, played=row.played, won=row.won, drawn=row.draw,
                        lost=row.lost, goals_for=row.goals_for, goals_against=row.goals_against,
                        goal_diff=row.goal_diff, form=row.form, description=None, home={}, away={},
                    )
                )
            report.standings_leagues += 1
            report.teams_created += resolver.created
            report.teams_linked += resolver.linked

    def _import_scorers(self, cfg: LeagueConfig, report: ExternalSyncReport) -> None:
        assert self._org is not None and cfg.fd_org is not None
        season, rows = self._org.scorers(cfg.fd_org)
        if season is None or not rows:
            return
        with self._db.session() as session:
            league = self._league(session, cfg)
            resolver = TeamResolver(session, self._threshold)
            session.execute(delete(ScorerStat).where(ScorerStat.league_id == league.id, ScorerStat.season == season))
            seen: set[tuple[int, str]] = set()
            for row in rows:
                team = resolver.resolve(org_module.SOURCE, str(row.team.id), row.team.names, league.id)
                if (team.id, row.player_name) in seen:
                    continue
                seen.add((team.id, row.player_name))
                session.add(
                    ScorerStat(
                        league_id=league.id, season=season, team_id=team.id, player_name=row.player_name,
                        position=row.position, goals=row.goals, assists=row.assists, played=row.played,
                    )
                )
            report.scorer_leagues += 1
