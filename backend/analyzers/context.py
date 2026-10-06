"""Everything known about one upcoming match, assembled from the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import fmean
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.analyzers.data_quality import DataQuality, assess_match
from backend.analyzers.fatigue import FatigueInfo, fatigue_info
from backend.analyzers.form import FormStats, form_stats
from backend.analyzers.h2h import H2HSummary, load_h2h
from backend.analyzers.injuries import (
    AbsenceImpact,
    PlayerRole,
    absence_impact,
    load_absences,
    scorer_roles,
    season_goals,
    team_player_roles,
)
from backend.analyzers.motivation import MotivationInfo, computed_standing, load_standing, motivation_info
from backend.analyzers.team_history import TeamMatch, load_team_history
from backend.config import ModelConfig
from backend.models import Match, Result
from backend.predictors.market_odds import OddsQuote, latest_quotes
from backend.services.leagues import league_title
from backend.utils.timeutils import to_local

HISTORY_LIMIT = 20
ROLE_WINDOW = 10
MIN_REFEREE_MATCHES = 5


@dataclass(frozen=True)
class TeamContext:
    team_id: int
    name: str
    is_home: bool
    history: tuple[TeamMatch, ...]
    venue_history: tuple[TeamMatch, ...]  # home games for the home team, away games for the away team
    form5: FormStats
    form10: FormStats
    venue5: FormStats
    venue10: FormStats
    fatigue: FatigueInfo
    motivation: MotivationInfo
    absences: AbsenceImpact


@dataclass(frozen=True)
class MatchContext:
    match_id: int
    api_id: int
    league_id: int
    league_title: str
    season: int
    round_name: str | None
    kickoff_utc: datetime
    kickoff_local: datetime
    home: TeamContext
    away: TeamContext
    h2h: H2HSummary
    quality: DataQuality
    quotes: tuple[OddsQuote, ...] = field(default_factory=tuple)
    referee: str | None = None
    referee_cards_avg: float | None = None


def _team_context(session: Session, match: Match, team_id: int, name: str, is_home: bool, cfg: ModelConfig) -> TeamContext:
    history = load_team_history(session, team_id, match.kickoff_utc, HISTORY_LIMIT)
    venue = [m for m in history if m.is_home == is_home]
    roles, _ = team_player_roles(session, team_id, [m.match_id for m in history[:ROLE_WINDOW]])
    named_roles: list[PlayerRole] = []
    if roles:
        team_goals = sum(role.goals for role in roles.values())
    else:  # free sources: no per-player match data, but the season's scorer list
        named_roles = scorer_roles(session, team_id)
        team_goals = season_goals(session, match.league_id, match.season, team_id)
    absences = absence_impact(
        load_absences(session, match.id, team_id, roles, named_roles),
        team_goals,
        replacement_factor=cfg.replacement_factor,
        max_attack_loss=cfg.max_injury_attack_loss,
        max_defence_gain=cfg.max_injury_defence_gain,
    )
    standing = load_standing(session, match.league_id, match.season, team_id)
    if standing is None and match.league is not None and match.league.full_results:
        standing = computed_standing(session, match.league_id, match.season, team_id)
    return TeamContext(
        team_id=team_id,
        name=name,
        is_home=is_home,
        history=tuple(history),
        venue_history=tuple(venue),
        form5=form_stats(history[:5]),
        form10=form_stats(history[:10]),
        venue5=form_stats(venue[:5]),
        venue10=form_stats(venue[:10]),
        fatigue=fatigue_info(history, match.kickoff_utc),
        motivation=motivation_info(standing, match.round_name),
        absences=absences,
    )


def _referee_cards(session: Session, referee: str | None, before: datetime) -> float | None:
    if not referee:
        return None
    totals = list(
        session.scalars(
            select(Result.total_cards)
            .join(Match, Match.id == Result.match_id)
            .where(Match.referee == referee, Match.kickoff_utc < before, Result.total_cards.is_not(None))
        )
    )
    return fmean(totals) if len(totals) >= MIN_REFEREE_MATCHES else None


def build_match_context(session: Session, match: Match, cfg: ModelConfig, tz: ZoneInfo) -> MatchContext:
    return MatchContext(
        match_id=match.id,
        api_id=match.api_id,
        league_id=match.league_id,
        league_title=league_title(match.league),
        season=match.season,
        round_name=match.round_name,
        kickoff_utc=match.kickoff_utc,
        kickoff_local=to_local(match.kickoff_utc, tz),
        home=_team_context(session, match, match.home_team_id, match.home_team.name, True, cfg),
        away=_team_context(session, match, match.away_team_id, match.away_team.name, False, cfg),
        h2h=load_h2h(session, match.home_team_id, match.away_team_id, match.kickoff_utc),
        quality=assess_match(session, match),
        quotes=tuple(latest_quotes(session, match.id)),
        referee=match.referee,
        referee_cards_avg=_referee_cards(session, match.referee, match.kickoff_utc),
    )
