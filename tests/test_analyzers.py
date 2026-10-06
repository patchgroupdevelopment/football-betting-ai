"""Form, fatigue, motivation, injuries."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.analyzers.fatigue import fatigue_info, fatigue_multiplier
from backend.analyzers.form import form_stats, total_goals_over_rate
from backend.analyzers.injuries import Absence, PlayerRole, absence_impact
from backend.analyzers.motivation import StandingInfo, motivation_info, motivation_multiplier
from tests.builders import KICKOFF, team_matches


def test_form_stats():
    matches = team_matches(1, [(2, 0), (1, 1), (0, 2), (3, 1), (1, 0)])
    stats = form_stats(matches)
    assert (stats.wins, stats.draws, stats.losses, stats.points) == (3, 1, 1, 10)
    assert stats.sequence == "WDLWW"
    assert stats.goals_for_avg == pytest.approx(1.4) and stats.clean_sheet_rate == pytest.approx(0.4)
    assert stats.btts_rate == pytest.approx(0.4) and stats.over25_rate == pytest.approx(0.2)
    assert stats.xg_for_avg == pytest.approx(1.5) and stats.ppg == pytest.approx(2.0)
    assert total_goals_over_rate(matches, 1.5) == pytest.approx(0.8)


def test_xg_needs_half_coverage():
    with_xg = team_matches(1, [(1, 0)] * 2)
    without = team_matches(1, [(1, 0)] * 3, xg=None)
    assert form_stats(with_xg + without).xg_for_avg is None
    assert form_stats(with_xg * 2 + without[:1]).xg_for_avg == pytest.approx(1.5)


def test_empty_form():
    assert form_stats([]).matches == 0 and form_stats([]).ppg == 0


def test_fatigue():
    tired = fatigue_info(team_matches(1, [(1, 0)] * 4, rest_days=2), KICKOFF)
    rested = fatigue_info(team_matches(1, [(1, 0)] * 4, rest_days=7), KICKOFF)
    assert tired.rest_days == 2 and rested.rest_days == 7
    assert fatigue_multiplier(tired, 3, 0.03) == pytest.approx(0.955)
    assert fatigue_multiplier(rested, 3, 0.03) == 1.0
    assert fatigue_info([], KICKOFF).rest_days is None


def _standing(rank: int, points: int, played: int = 30, size: int = 20, gap_top: int = 10, safety: int | None = 12,
              description: str | None = None) -> StandingInfo:
    return StandingInfo(rank, points, played, size, gap_top, safety, description, None)


def test_motivation_levels():
    assert motivation_info(_standing(2, 60, gap_top=1), None).tags[0] == "title_race"
    assert motivation_info(_standing(18, 25, safety=0), None).level == 90
    assert motivation_info(_standing(10, 40, played=33), None).tags == ("safe_midtable",)
    assert motivation_info(_standing(5, 50, description="Promotion - Europa League"), None).tags[0] == "europe_race"
    assert motivation_info(None, "Quarter-finals").tags == ("cup_knockout",)
    assert motivation_info(_standing(1, 9, played=3), None).tags == ("early_season",)
    assert motivation_info(None, None).level == 60


def test_motivation_multiplier_is_small():
    high = motivation_info(_standing(18, 25, safety=0), None)
    assert motivation_multiplier(high, 0.03) == pytest.approx(1.03)
    assert motivation_multiplier(motivation_info(None, None), 0.03) == 1.0


def _absence(name: str, position: str, share: float, goals: int = 0, roles: tuple[str, ...] = (), weight: float = 1.0,
             kind: str = "injury") -> Absence:
    role = PlayerRole(1, name, position, share, goals, 0, roles)
    return Absence(1, name, kind, None, role, weight)


def _impact(absences, team_goals=20):
    return absence_impact(absences, team_goals, replacement_factor=0.5, max_attack_loss=0.2, max_defence_gain=0.15)


def test_top_scorer_absence_hurts_attack():
    impact = _impact([_absence("Bombardir", "F", 0.95, goals=10, roles=("top_scorer", "key_attacker"))])
    assert impact.attack_loss == pytest.approx(0.2)  # capped: 10/20 × 0.5 + baseline
    assert impact.critical and "key_attacker_out" in impact.notes


def test_goalkeeper_absence_is_critical():
    impact = _impact([_absence("Qapıçı", "G", 1.0, roles=("key_goalkeeper",))])
    assert impact.defence_gain == pytest.approx(0.10) and impact.critical


def test_doubtful_counts_half():
    full = _impact([_absence("Müdafiəçi", "D", 0.9)])
    doubtful = _impact([_absence("Müdafiəçi", "D", 0.9, weight=0.5, kind="doubtful")])
    assert doubtful.defence_gain == pytest.approx(full.defence_gain / 2)
    assert doubtful.key_absent == 0 and full.key_absent == 1


def test_no_absences():
    impact = _impact([])
    assert (impact.attack_loss, impact.defence_gain, impact.critical) == (0, 0, False)


def test_history_kickoffs_are_newest_first():
    matches = team_matches(1, [(1, 0)] * 3)
    assert matches[0].kickoff_utc - matches[1].kickoff_utc == timedelta(days=7)
