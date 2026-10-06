"""Football data: leagues, teams, players, matches and match-level facts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base, TimestampMixin
from backend.models.constants import FINISHED_STATUSES
from backend.utils.timeutils import utcnow


class League(TimestampMixin, Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120))
    name_az: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    logo_url: Mapped[str | None] = mapped_column(String(255))
    tier: Mapped[int | None]
    # Position in config.yaml (0 = most important); None = league not configured.
    priority: Mapped[int | None]
    # True when an external source delivers the league's complete results, so a
    # table can be computed from them if no official standings are available.
    full_results: Mapped[bool] = mapped_column(default=False)


class Team(TimestampMixin, Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    # API-Football id; None for teams known only from free sources (linked later by name).
    api_id: Mapped[int | None] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    logo_url: Mapped[str | None] = mapped_column(String(255))
    elo: Mapped[float | None]


class Player(TimestampMixin, Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(unique=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(8))
    number: Mapped[int | None]
    # e.g. ["key_goalkeeper", "top_scorer"] — filled by the injury-impact analyzer.
    role_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    minutes_share: Mapped[float | None]
    goals: Mapped[int | None]
    assists: Mapped[int | None]


class Match(TimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # API-Football id; None for matches imported from free sources.
    api_id: Mapped[int | None] = mapped_column(unique=True)
    source: Mapped[str] = mapped_column(String(24), default="api_football")
    external_id: Mapped[str | None] = mapped_column(String(120))
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season: Mapped[int]
    round_name: Mapped[str | None] = mapped_column(String(80))
    kickoff_utc: Mapped[datetime] = mapped_column(index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    status: Mapped[str] = mapped_column(String(8), default="NS")
    home_goals: Mapped[int | None]
    away_goals: Mapped[int | None]
    ht_home_goals: Mapped[int | None]
    ht_away_goals: Mapped[int | None]
    referee: Mapped[str | None] = mapped_column(String(120))
    venue: Mapped[str | None] = mapped_column(String(160))
    # True when the match is selected for full analysis (configured league).
    is_priority: Mapped[bool] = mapped_column(default=False, index=True)

    # Ingestion state: when each kind of data was last fetched for this match.
    details_fetched_at: Mapped[datetime | None]
    history_checked_at: Mapped[datetime | None]
    h2h_checked_at: Mapped[datetime | None]
    injuries_checked_at: Mapped[datetime | None]
    odds_checked_at: Mapped[datetime | None]
    lineups_checked_at: Mapped[datetime | None]

    league: Mapped[League] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    stats: Mapped[list[MatchStats]] = relationship(back_populates="match", cascade="all, delete-orphan")

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES


class MatchStats(Base):
    __tablename__ = "match_stats"
    __table_args__ = (UniqueConstraint("match_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    is_home: Mapped[bool]
    shots_total: Mapped[int | None]
    shots_on_target: Mapped[int | None]
    shots_off_target: Mapped[int | None]
    shots_blocked: Mapped[int | None]
    shots_inside_box: Mapped[int | None]
    shots_outside_box: Mapped[int | None]
    possession: Mapped[float | None]
    corners: Mapped[int | None]
    yellow_cards: Mapped[int | None]
    red_cards: Mapped[int | None]
    fouls: Mapped[int | None]
    offsides: Mapped[int | None]
    passes_total: Mapped[int | None]
    passes_accurate: Mapped[int | None]
    pass_accuracy: Mapped[float | None]
    saves: Mapped[int | None]
    xg: Mapped[float | None]
    # "api" | "understat" | "proxy" — proxy xG is always labelled as an estimate.
    xg_source: Mapped[str | None] = mapped_column(String(16))
    # Not provided by API-Football; reserved for other sources.
    big_chances: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    match: Mapped[Match] = relationship(back_populates="stats")


class TeamAlias(Base):
    """How a team is identified in another source (football-data.org id, football-data.co.uk name)."""

    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("source", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ScorerStat(Base):
    """Season scoring numbers per player (football-data.org), used to recognise key absentees."""

    __tablename__ = "scorers"
    __table_args__ = (UniqueConstraint("league_id", "season", "team_id", "player_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season: Mapped[int]
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(40))
    goals: Mapped[int] = mapped_column(default=0)
    assists: Mapped[int | None]
    played: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class PlayerMatchStats(Base):
    """Per-player numbers from detailed fixtures; the basis for player importance."""

    __tablename__ = "player_match_stats"
    __table_args__ = (UniqueConstraint("match_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    minutes: Mapped[int | None]
    position: Mapped[str | None] = mapped_column(String(4))  # G / D / M / F
    rating: Mapped[float | None]
    substitute: Mapped[bool] = mapped_column(default=False)
    goals: Mapped[int | None]
    assists: Mapped[int | None]
    yellow_cards: Mapped[int | None]
    red_cards: Mapped[int | None]


class Injury(Base):
    __tablename__ = "injuries"
    __table_args__ = (UniqueConstraint("match_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # InjuryKind
    reason: Mapped[str | None] = mapped_column(String(160))
    api_type: Mapped[str | None] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class Lineup(Base):
    __tablename__ = "lineups"
    __table_args__ = (UniqueConstraint("match_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    formation: Mapped[str | None] = mapped_column(String(16))
    coach: Mapped[str | None] = mapped_column(String(120))
    starters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    substitutes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confirmed_at: Mapped[datetime] = mapped_column(default=utcnow)


class Standing(Base):
    __tablename__ = "standings"
    __table_args__ = (UniqueConstraint("league_id", "season", "team_id", "group_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season: Mapped[int]
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    group_name: Mapped[str] = mapped_column(String(120), default="")
    rank: Mapped[int | None]
    points: Mapped[int | None]
    played: Mapped[int | None]
    won: Mapped[int | None]
    drawn: Mapped[int | None]
    lost: Mapped[int | None]
    goals_for: Mapped[int | None]
    goals_against: Mapped[int | None]
    goal_diff: Mapped[int | None]
    form: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(160))
    home: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    away: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TeamRating(Base):
    __tablename__ = "team_ratings"
    __table_args__ = (UniqueConstraint("team_id", "as_of", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    rating: Mapped[float]
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(16), default="elo")


class Result(Base):
    __tablename__ = "results"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), unique=True)
    home_goals: Mapped[int | None]
    away_goals: Mapped[int | None]
    ht_home_goals: Mapped[int | None]
    ht_away_goals: Mapped[int | None]
    total_corners: Mapped[int | None]
    total_cards: Mapped[int | None]
    settled_at: Mapped[datetime] = mapped_column(default=utcnow)
