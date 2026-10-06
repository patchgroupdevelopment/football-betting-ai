"""Read model for "today's matches": what the daily report and /bugun show."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.analyzers.data_quality import DataQuality, assess_match
from backend.database.session import Database
from backend.models import Match
from backend.models.constants import RunStatus
from backend.services.leagues import league_title
from backend.services.picks import latest_decisions
from backend.services.runs import get_latest_run
from backend.utils.timeutils import analysis_window, to_local

UNCONFIGURED_PRIORITY = 10_000


@dataclass(frozen=True)
class MatchLine:
    match_id: int
    api_id: int
    kickoff_local: datetime
    home: str
    away: str
    status: str
    home_goals: int | None
    away_goals: int | None
    quality: DataQuality
    decision: str | None = None  # latest analysis decision, if analysed

    @property
    def has_odds(self) -> bool:
        return self.quality.components.get("odds", False)


@dataclass(frozen=True)
class LeagueBlock:
    title: str
    matches: tuple[MatchLine, ...]


@dataclass(frozen=True)
class DailyOverview:
    target_date: date
    loaded: bool
    blocks: tuple[LeagueBlock, ...] = ()
    other_leagues_count: int = 0
    warnings: tuple[str, ...] = ()
    last_run_status: str | None = None

    @property
    def priority_count(self) -> int:
        return sum(len(block.matches) for block in self.blocks)

    @property
    def ready_count(self) -> int:
        return sum(1 for block in self.blocks for m in block.matches if m.quality.level == "full")

    @property
    def incomplete_count(self) -> int:
        return self.priority_count - self.ready_count


def build_daily_overview(db: Database, target_date: date, tz: ZoneInfo, cutoff: str = "08:00") -> DailyOverview:
    """``cutoff`` is the daily run time: the day's window reaches until then on the next morning."""
    start, end = analysis_window(target_date, tz, cutoff)
    with db.session() as session:
        run = get_latest_run(session, target_date)
        decisions = latest_decisions(session, target_date)
        matches = list(
            session.scalars(
                select(Match)
                .options(joinedload(Match.league), joinedload(Match.home_team), joinedload(Match.away_team))
                .where(Match.is_priority.is_(True), Match.kickoff_utc >= start, Match.kickoff_utc < end)
            )
        )
        matches.sort(
            key=lambda m: (
                m.league.priority if m.league.priority is not None else UNCONFIGURED_PRIORITY,
                m.league_id,
                m.kickoff_utc,
                m.id,
            )
        )

        grouped: dict[int, list[MatchLine]] = {}
        titles: dict[int, str] = {}
        for match in matches:
            titles.setdefault(match.league_id, league_title(match.league))
            grouped.setdefault(match.league_id, []).append(
                MatchLine(
                    match_id=match.id,
                    api_id=match.api_id,
                    kickoff_local=to_local(match.kickoff_utc, tz),
                    home=match.home_team.name,
                    away=match.away_team.name,
                    status=match.status,
                    home_goals=match.home_goals,
                    away_goals=match.away_goals,
                    quality=assess_match(session, match),
                    decision=decisions.get(match.id),
                )
            )

        details = (run.details or {}) if run is not None else {}
        run_status = run.status if run is not None else None

    blocks = tuple(LeagueBlock(titles[league_id], tuple(lines)) for league_id, lines in grouped.items())
    loaded = bool(blocks) or run_status in (RunStatus.SUCCESS, RunStatus.PARTIAL)
    return DailyOverview(
        target_date=target_date,
        loaded=loaded,
        blocks=blocks,
        other_leagues_count=int(details.get("other_fixtures") or 0),
        warnings=tuple(details.get("warnings") or ()),
        last_run_status=run_status,
    )
