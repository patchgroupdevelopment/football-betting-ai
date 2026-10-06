"""What was known before a given day: team histories, head-to-heads and league tables.

The match context is assembled exactly like the live one (backend.analyzers.context),
only from memory instead of the database. Information that the historical files do
not contain is stated explicitly instead of being invented:
- absences: none known (no historical injury lists);
- referee: not used;
- line-ups: not confirmed (the live analysis runs in the morning, before line-ups).
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.analyzers.context import HISTORY_LIMIT, MatchContext, TeamContext
from backend.analyzers.data_quality import HISTORY_WINDOW, MIN_HISTORY, quality_from
from backend.analyzers.fatigue import fatigue_info
from backend.analyzers.form import form_stats
from backend.analyzers.h2h import H2HSummary
from backend.analyzers.injuries import AbsenceImpact
from backend.analyzers.motivation import Table, add_result, motivation_info, table_standing
from backend.analyzers.team_history import FinishedMatch, TeamMatch
from backend.backtest.data import HistoricalMatch
from backend.predictors.market_odds import OddsQuote
from backend.utils.timeutils import to_local

H2H_LIMIT = 10


def team_view(m: HistoricalMatch, team_id: int) -> TeamMatch:
    home = m.home_id == team_id
    return TeamMatch(
        match_id=m.index,
        kickoff_utc=m.kickoff_utc,
        league_id=m.league_id,
        is_home=home,
        opponent_id=m.away_id if home else m.home_id,
        goals_for=m.home_goals if home else m.away_goals,
        goals_against=m.away_goals if home else m.home_goals,
        ht_for=m.ht_home_goals if home else m.ht_away_goals,
        ht_against=m.ht_away_goals if home else m.ht_home_goals,
        xg_for=m.home_xg if home else m.away_xg,
        xg_against=m.away_xg if home else m.home_xg,
        corners_for=m.home_corners if home else m.away_corners,
        corners_against=m.away_corners if home else m.home_corners,
        cards_for=m.home_cards if home else m.away_cards,
        cards_against=m.away_cards if home else m.home_cards,
    )


class HistoryBook:
    def __init__(self) -> None:
        self._teams: dict[int, list[TeamMatch]] = defaultdict(list)  # oldest first
        self._pairs: dict[tuple[int, int], list[HistoricalMatch]] = defaultdict(list)
        self._tables: dict[tuple[int, int], Table] = defaultdict(dict)
        self.finished: list[FinishedMatch] = []  # chronological
        self._kickoffs: list[datetime] = []

    def add(self, m: HistoricalMatch) -> None:
        self._teams[m.home_id].append(team_view(m, m.home_id))
        self._teams[m.away_id].append(team_view(m, m.away_id))
        self._pairs[(min(m.home_id, m.away_id), max(m.home_id, m.away_id))].append(m)
        add_result(self._tables[(m.league_id, m.season)], m.home_id, m.away_id, m.home_goals, m.away_goals)
        self.finished.append(
            FinishedMatch(
                home_id=m.home_id,
                away_id=m.away_id,
                home_goals=m.home_goals,
                away_goals=m.away_goals,
                ht_home_goals=m.ht_home_goals,
                ht_away_goals=m.ht_away_goals,
                home_xg=m.home_xg,
                away_xg=m.away_xg,
                kickoff_utc=m.kickoff_utc,
            )
        )
        self._kickoffs.append(m.kickoff_utc)

    def window(self, since: datetime) -> list[FinishedMatch]:
        return self.finished[bisect.bisect_left(self._kickoffs, since):]

    def history(self, team_id: int, limit: int = HISTORY_LIMIT) -> list[TeamMatch]:
        """Newest first."""
        return self._teams[team_id][-limit:][::-1]

    def h2h(self, home_id: int, away_id: int, limit: int = H2H_LIMIT) -> H2HSummary:
        meetings = self._pairs.get((min(home_id, away_id), max(home_id, away_id)), [])
        return H2HSummary(tuple(team_view(m, home_id) for m in reversed(meetings[-limit:])))

    def table(self, league_id: int, season: int) -> Table:
        return self._tables.get((league_id, season), {})


def _xg_coverage(home: list[TeamMatch], away: list[TeamMatch]) -> float:
    recent = home[:HISTORY_WINDOW] + away[:HISTORY_WINDOW]
    return sum(1 for m in recent if m.xg_for is not None) / len(recent) if recent else 0.0


def _team_context(book: HistoryBook, m: HistoricalMatch, team_id: int, name: str, is_home: bool) -> TeamContext:
    history = book.history(team_id)
    venue = [x for x in history if x.is_home == is_home]
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
        fatigue=fatigue_info(history, m.kickoff_utc),
        motivation=motivation_info(table_standing(book.table(m.league_id, m.season), team_id), None),
        absences=AbsenceImpact(),
    )


def build_context(
    book: HistoryBook, m: HistoricalMatch, quotes: tuple[OddsQuote, ...], league_title: str, tz: ZoneInfo
) -> MatchContext:
    home = _team_context(book, m, m.home_id, m.home, True)
    away = _team_context(book, m, m.away_id, m.away, False)
    h2h = book.h2h(m.home_id, m.away_id)
    components = {
        "home_history": len(home.history[:HISTORY_WINDOW]) >= MIN_HISTORY,
        "away_history": len(away.history[:HISTORY_WINDOW]) >= MIN_HISTORY,
        "odds": any(q.market in ("1X2", "OU") for q in quotes),
        # The live system checks injury lists for every analysed match; historically they are
        # unknown, so the check counts as done with nothing found.
        "injuries": True,
        "standings": True,  # complete league results -> computed table
        "xg": _xg_coverage(list(home.history), list(away.history)) >= 0.5,
        "h2h": h2h.count > 0,
        "lineups": False,
    }
    return MatchContext(
        match_id=m.index,
        api_id=0,
        league_id=m.league_id,
        league_title=league_title,
        season=m.season,
        round_name=None,
        kickoff_utc=m.kickoff_utc,
        kickoff_local=to_local(m.kickoff_utc, tz),
        home=home,
        away=away,
        h2h=h2h,
        quality=quality_from(components),
        quotes=quotes,
    )
