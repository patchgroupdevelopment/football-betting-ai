"""Player importance and the impact of absences (injuries, suspensions, doubtful players).

Importance comes from the team's recent matches: minutes share, goals and
assists (per-player fixture statistics), falling back to starting line-ups
when per-player statistics are missing. The impact is expressed as an attack
loss (fewer goals scored) and a defence gain (more goals conceded), both
capped, and is applied to the goal model as multipliers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Injury, Lineup, Match, Player, PlayerMatchStats, ScorerStat, Standing
from backend.models.constants import InjuryKind
from backend.services.external.teamnames import normalize

REGULAR_SHARE = 0.6
DOUBTFUL_WEIGHT = 0.5
# Contribution beyond goals/assists (chance creation, pressing) by position.
_ATTACK_BASELINE = {"F": 0.03, "M": 0.02, "D": 0.0, "G": 0.0}
# Without goal data: rough share of team attack per regular starter.
_ATTACK_FALLBACK = {"F": 0.10, "M": 0.05, "D": 0.01, "G": 0.0}
_DEFENCE_IMPACT = {"G": 0.10, "D": 0.04, "M": 0.015, "F": 0.0}


@dataclass(frozen=True)
class PlayerRole:
    player_id: int
    name: str
    position: str | None
    minutes_share: float
    goals: int
    assists: int
    roles: tuple[str, ...] = ()
    has_goal_data: bool = True

    @property
    def is_regular(self) -> bool:
        return self.minutes_share >= REGULAR_SHARE


@dataclass(frozen=True)
class Absence:
    player_id: int
    name: str
    kind: str  # InjuryKind
    reason: str | None
    role: PlayerRole | None
    weight: float


@dataclass(frozen=True)
class AbsenceImpact:
    absences: tuple[Absence, ...] = ()
    attack_loss: float = 0.0
    defence_gain: float = 0.0
    critical: bool = False
    key_absent: int = 0
    team_goals: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)  # reason keys, e.g. "key_goalkeeper_out"


def team_player_roles(session: Session, team_id: int, match_ids: Sequence[int]) -> tuple[dict[int, PlayerRole], int]:
    """Roles for a team's players over the given matches, plus the number of matches with data."""
    match_ids = list(match_ids)
    if not match_ids:
        return {}, 0
    stats = list(
        session.scalars(
            select(PlayerMatchStats).where(PlayerMatchStats.team_id == team_id, PlayerMatchStats.match_id.in_(match_ids))
        )
    )
    names = {}
    minutes: dict[int, int] = defaultdict(int)
    goals: dict[int, int] = defaultdict(int)
    assists: dict[int, int] = defaultdict(int)
    positions: dict[int, Counter[str]] = defaultdict(Counter)
    has_goal_data = bool(stats)

    if stats:
        matches_with_data = len({s.match_id for s in stats})
        for s in stats:
            minutes[s.player_id] += s.minutes or 0
            goals[s.player_id] += s.goals or 0
            assists[s.player_id] += s.assists or 0
            if s.position:
                positions[s.player_id][s.position] += 1
    else:  # fall back to starting line-ups: a start counts as 90 minutes
        lineups = list(
            session.scalars(select(Lineup).where(Lineup.team_id == team_id, Lineup.match_id.in_(match_ids)))
        )
        matches_with_data = len(lineups)
        api_to_player = {}
        api_ids = {p.get("api_id") for lu in lineups for p in lu.starters if p.get("api_id")}
        if api_ids:
            api_to_player = {p.api_id: p.id for p in session.scalars(select(Player).where(Player.api_id.in_(api_ids)))}
        for lineup in lineups:
            for starter in lineup.starters:
                player_id = api_to_player.get(starter.get("api_id"))
                if player_id is None:
                    continue
                minutes[player_id] += 90
                if starter.get("pos"):
                    positions[player_id][starter["pos"]] += 1

    if not matches_with_data:
        return {}, 0
    if minutes:
        names = {p.id: p.name for p in session.scalars(select(Player).where(Player.id.in_(list(minutes))))}

    max_minutes = matches_with_data * 90
    team_goals = sum(goals.values())
    roles: dict[int, list[str]] = defaultdict(list)
    share = {pid: minutes[pid] / max_minutes for pid in minutes}
    position = {pid: (positions[pid].most_common(1)[0][0] if positions[pid] else None) for pid in minutes}

    def top(pos: str, count: int, threshold: float) -> list[int]:
        players = [pid for pid in minutes if position[pid] == pos and share[pid] >= threshold]
        return sorted(players, key=lambda pid: minutes[pid], reverse=True)[:count]

    for pid in top("G", 1, REGULAR_SHARE):
        roles[pid].append("key_goalkeeper")
    for pid in top("D", 2, REGULAR_SHARE):
        roles[pid].append("key_defender")
    for pid in top("M", 2, REGULAR_SHARE):
        roles[pid].append("key_midfielder")
    for pid in top("F", 1, 0.5):
        roles[pid].append("key_attacker")
    if has_goal_data and goals:
        best = max(goals.values())
        if best >= 2 and best >= 0.2 * max(team_goals, 1):
            for pid, value in goals.items():
                if value == best:
                    roles[pid].append("top_scorer")
    if has_goal_data and assists:
        best = max(assists.values())
        if best >= 2:
            for pid, value in assists.items():
                if value == best:
                    roles[pid].append("top_assister")

    result = {
        pid: PlayerRole(
            player_id=pid,
            name=names.get(pid, str(pid)),
            position=position[pid],
            minutes_share=round(min(1.0, share[pid]), 3),
            goals=goals[pid],
            assists=assists[pid],
            roles=tuple(roles[pid]),
            has_goal_data=has_goal_data,
        )
        for pid in minutes
    }
    return result, matches_with_data


_ATTACKING_POSITIONS = ("offence", "forward", "attack", "striker", "winger")


def scorer_roles(session: Session, team_id: int) -> list[PlayerRole]:
    """Roles from the season's top-scorer list (football-data.org) when no per-player match data exists."""
    latest = session.scalar(select(func.max(ScorerStat.season)).where(ScorerStat.team_id == team_id))
    if latest is None:
        return []
    rows = list(
        session.scalars(
            select(ScorerStat)
            .where(ScorerStat.team_id == team_id, ScorerStat.season == latest)
            .order_by(ScorerStat.goals.desc())
        )
    )
    if not rows:
        return []
    most_played = max((r.played or 0) for r in rows) or 1
    best_goals = rows[0].goals
    best_assists = max((r.assists or 0) for r in rows)
    roles: list[PlayerRole] = []
    for index, row in enumerate(rows):
        tags: list[str] = []
        if row.goals == best_goals and best_goals >= 2:
            tags.append("top_scorer")
        if (row.assists or 0) == best_assists and best_assists >= 2:
            tags.append("top_assister")
        attacking = any(word in (row.position or "").lower() for word in _ATTACKING_POSITIONS)
        roles.append(
            PlayerRole(
                player_id=-(index + 1),  # not a players row: identified by name
                name=row.player_name,
                position="F" if attacking or "top_scorer" in tags else "M",
                minutes_share=min(1.0, (row.played or most_played) / most_played),
                goals=row.goals,
                assists=row.assists or 0,
                roles=tuple(tags),
            )
        )
    return roles


def same_player(api_name: str, other: str) -> bool:
    """"E. Haaland" (API-Football) vs "Erling Haaland" (football-data.org)."""
    a = [t for t in normalize(api_name).split() if t]
    b = [t for t in normalize(other).split() if t]
    if not a or not b or a[-1] != b[-1]:
        return False
    if len(a) == 1 or len(b) == 1:
        return True
    return a[0][0] == b[0][0]


def season_goals(session: Session, league_id: int, season: int, team_id: int) -> int:
    standing = session.scalar(
        select(Standing.goals_for).where(Standing.league_id == league_id, Standing.season == season, Standing.team_id == team_id)
    )
    if standing:
        return standing
    rows = session.execute(
        select(Match.home_team_id, Match.home_goals, Match.away_goals).where(
            Match.league_id == league_id,
            Match.season == season,
            Match.home_goals.is_not(None),
            (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
        )
    ).all()
    return sum((hg if home == team_id else ag) or 0 for home, hg, ag in rows)


def load_absences(
    session: Session,
    match_id: int,
    team_id: int,
    roles: dict[int, PlayerRole],
    named_roles: list[PlayerRole] | None = None,
) -> list[Absence]:
    rows = session.execute(
        select(Injury, Player.name)
        .join(Player, Player.id == Injury.player_id)
        .where(Injury.match_id == match_id, Injury.team_id == team_id)
    ).all()
    absences = []
    for injury, name in rows:
        role = roles.get(injury.player_id)
        if role is None and named_roles:
            role = next((r for r in named_roles if same_player(name, r.name)), None)
        absences.append(
            Absence(
                player_id=injury.player_id,
                name=name,
                kind=injury.kind,
                reason=injury.reason,
                role=role,
                weight=DOUBTFUL_WEIGHT if injury.kind == InjuryKind.DOUBTFUL else 1.0,
            )
        )
    return absences


def absence_impact(
    absences: Sequence[Absence],
    team_goals: int,
    *,
    replacement_factor: float,
    max_attack_loss: float,
    max_defence_gain: float,
) -> AbsenceImpact:
    attack = 0.0
    defence = 0.0
    key_absent = 0
    notes: list[str] = []
    for absence in absences:
        role = absence.role
        if role is None:
            attack += absence.weight * 0.005  # unknown squad player: marginal
            continue
        pos = role.position or "M"
        if role.has_goal_data and team_goals > 0:
            contribution = (role.goals + 0.6 * role.assists) / team_goals
            attack += absence.weight * (contribution * replacement_factor + role.minutes_share * _ATTACK_BASELINE.get(pos, 0))
        else:
            attack += absence.weight * role.minutes_share * _ATTACK_FALLBACK.get(pos, 0.02) * replacement_factor * 2
        defence += absence.weight * role.minutes_share * _DEFENCE_IMPACT.get(pos, 0.0)
        if role.is_regular and absence.weight >= 1.0:
            key_absent += 1
        if "key_goalkeeper" in role.roles and absence.weight >= 1.0:
            notes.append("key_goalkeeper_out")
        if ("top_scorer" in role.roles or "key_attacker" in role.roles) and absence.weight >= 1.0:
            notes.append("key_attacker_out")
        if "key_defender" in role.roles and absence.weight >= 1.0:
            notes.append("key_defender_out")

    attack = min(attack, max_attack_loss)
    defence = min(defence, max_defence_gain)
    critical = "key_goalkeeper_out" in notes or attack >= 0.12 or key_absent >= 3
    return AbsenceImpact(
        absences=tuple(absences),
        attack_loss=round(attack, 4),
        defence_gain=round(defence, 4),
        critical=critical,
        key_absent=key_absent,
        team_goals=team_goals,
        notes=tuple(dict.fromkeys(notes)),
    )
