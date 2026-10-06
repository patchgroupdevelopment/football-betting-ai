"""Linking teams and matches across sources.

One club, one ``teams`` row: a team first seen in a football-data.co.uk file is
created without an API-Football id and is adopted (given its API id) when
API-Football shows it with a matching name in the same league. One match, one
``matches`` row: the same fixture from two sources is recognised by its teams
and kick-off (±36 h) and merged, keeping the richer data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, union
from sqlalchemy.orm import Session

from backend.models import Match, MatchStats, Result, ScorerStat, Standing, Team, TeamAlias
from backend.services.external.teamnames import best_match, similarity, unique_partial_match
from backend.utils.timeutils import utcnow

SAME_MATCH_WINDOW = timedelta(hours=36)


def league_team_ids(session: Session, league_id: int) -> set[int]:
    query = union(
        select(Match.home_team_id).where(Match.league_id == league_id),
        select(Match.away_team_id).where(Match.league_id == league_id),
        select(Standing.team_id).where(Standing.league_id == league_id),
    )
    return {row[0] for row in session.execute(query)}


def team_names(session: Session, team_ids: set[int]) -> dict[int, list[str]]:
    """Every name a team is known by: its own and those of its aliases."""
    if not team_ids:
        return {}
    names: dict[int, list[str]] = {tid: [] for tid in team_ids}
    for team_id, name in session.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids))):
        names[team_id].append(name)
    for team_id, name in session.execute(select(TeamAlias.team_id, TeamAlias.name).where(TeamAlias.team_id.in_(team_ids))):
        names[team_id].append(name)
    return names


class TeamResolver:
    """Maps a team of an external source to our ``teams`` row, creating it if needed."""

    def __init__(self, session: Session, threshold: float) -> None:
        self.s = session
        self.threshold = threshold
        self.created = 0
        self.linked = 0
        self._league_names: dict[int, dict[int, list[str]]] = {}
        self._aliases: dict[tuple[str, str], int] = {}
        self._taken: dict[str, set[int]] = {}  # source -> teams that already have an alias from it

    def _candidates(self, league_id: int) -> dict[int, list[str]]:
        if league_id not in self._league_names:
            self._league_names[league_id] = team_names(self.s, league_team_ids(self.s, league_id))
        return self._league_names[league_id]

    def _remember(self, league_id: int, team: Team, name: str) -> None:
        self._candidates(league_id).setdefault(team.id, [team.name]).append(name)

    def _teams_with_alias_from(self, source: str) -> set[int]:
        if source not in self._taken:
            self._taken[source] = set(self.s.scalars(select(TeamAlias.team_id).where(TeamAlias.source == source)))
        return self._taken[source]

    def resolve(self, source: str, key: str, names: list[str], league_id: int) -> Team:
        cached = self._aliases.get((source, key))
        if cached is not None:
            return self.s.get(Team, cached)  # type: ignore[return-value]
        alias = self.s.scalar(select(TeamAlias).where(TeamAlias.source == source, TeamAlias.key == key))
        if alias is not None:
            self._aliases[(source, key)] = alias.team_id
            team = self.s.get(Team, alias.team_id)
            self._remember(league_id, team, names[0])
            return team  # type: ignore[return-value]

        # One club has one name per source: a team already known to this source under another
        # key is a different club ("Dundee" vs "Dundee United" in the same file).
        taken = self._teams_with_alias_from(source)
        candidates = {tid: names for tid, names in self._candidates(league_id).items() if tid not in taken}
        best_id, best_score = None, 0.0
        for name in names:
            team_id, score = best_match(name, candidates, self.threshold)
            if team_id is not None and score > best_score:
                best_id, best_score = team_id, score
        if best_id is not None:
            team = self.s.get(Team, best_id)
            self.linked += 1
        else:
            team = Team(api_id=None, name=names[0])
            self.s.add(team)
            self.s.flush()
            self.created += 1
        self.s.add(TeamAlias(team_id=team.id, source=source, key=key, name=names[0]))
        self.s.flush()
        self._aliases[(source, key)] = team.id
        self._teams_with_alias_from(source).add(team.id)
        self._remember(league_id, team, names[0])
        return team  # type: ignore[return-value]


def adopt_unlinked_team(session: Session, name: str, league_id: int | None, threshold: float) -> Team | None:
    """An API-Football team not yet in the database: is it a team we already know from a free source?"""
    candidates: dict[int, list[str]] = {}
    if league_id is not None:
        ids = league_team_ids(session, league_id)
        unlinked = {tid for (tid,) in session.execute(select(Team.id).where(Team.id.in_(ids), Team.api_id.is_(None)))}
        candidates = team_names(session, unlinked)
    team_id, _ = best_match(name, candidates, threshold) if candidates else (None, 0.0)
    if team_id is None and candidates:
        team_id = unique_partial_match(name, candidates)
    if team_id is None:
        # First appearance in a cup: accept only an exact (normalised) name match anywhere.
        for tid, team_name in session.execute(select(Team.id, Team.name).where(Team.api_id.is_(None))):
            if similarity(name, team_name) == 1.0:
                team_id = tid
                break
    return session.get(Team, team_id) if team_id is not None else None


def link_alias(session: Session, source: str, key: str, name: str, team_id: int) -> bool:
    """Point (source, key) at ``team_id``. A duplicate team created earlier for that key is dissolved.

    Returns True when something changed.
    """
    alias = session.scalar(select(TeamAlias).where(TeamAlias.source == source, TeamAlias.key == key))
    if alias is None:
        session.add(TeamAlias(team_id=team_id, source=source, key=key, name=name))
        session.flush()
        return True
    if alias.team_id == team_id:
        return False
    old_team_id = alias.team_id
    alias.team_id = team_id
    # Rows hung on the duplicate are re-imported for the right team in the same run.
    session.execute(delete(Standing).where(Standing.team_id == old_team_id))
    session.execute(delete(ScorerStat).where(ScorerStat.team_id == old_team_id))
    session.flush()
    orphan = session.get(Team, old_team_id)
    still_used = session.scalar(select(TeamAlias.id).where(TeamAlias.team_id == old_team_id).limit(1)) or session.scalar(
        select(Match.id).where((Match.home_team_id == old_team_id) | (Match.away_team_id == old_team_id)).limit(1)
    )
    if orphan is not None and orphan.api_id is None and not still_used:
        session.delete(orphan)
    session.flush()
    return True


def find_same_match(session: Session, home_id: int, away_id: int, kickoff: datetime) -> Match | None:
    return session.scalar(
        select(Match)
        .where(
            Match.home_team_id == home_id,
            Match.away_team_id == away_id,
            Match.kickoff_utc >= kickoff - SAME_MATCH_WINDOW,
            Match.kickoff_utc <= kickoff + SAME_MATCH_WINDOW,
        )
        .limit(1)
    )


def fill_match_stats(session: Session, match: Match, team_id: int, is_home: bool, values: dict[str, float | int | None], xg_source: str) -> None:
    """Add statistics from a free source without overwriting values that are already known."""
    known = {k: v for k, v in values.items() if v is not None}
    if not known:
        return
    row = session.scalar(select(MatchStats).where(MatchStats.match_id == match.id, MatchStats.team_id == team_id))
    if row is None:
        row = MatchStats(match_id=match.id, team_id=team_id, is_home=is_home)
        session.add(row)
    for column, value in known.items():
        if getattr(row, column, None) is None:
            setattr(row, column, value)
    if row.xg is not None and row.xg_source is None:
        row.xg_source = xg_source


def upsert_result(session: Session, match: Match) -> None:
    session.flush()
    stats = list(session.scalars(select(MatchStats).where(MatchStats.match_id == match.id)))
    corners = [s.corners for s in stats]
    cards = [s.yellow_cards for s in stats]
    result = session.scalar(select(Result).where(Result.match_id == match.id))
    if result is None:
        result = Result(match_id=match.id)
        session.add(result)
    result.home_goals, result.away_goals = match.home_goals, match.away_goals
    result.ht_home_goals, result.ht_away_goals = match.ht_home_goals, match.ht_away_goals
    complete = len(stats) == 2
    result.total_corners = sum(corners) if complete and None not in corners else None
    result.total_cards = sum((s.yellow_cards or 0) + (s.red_cards or 0) for s in stats) if complete and None not in cards else None
    result.settled_at = utcnow()
