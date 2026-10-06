from __future__ import annotations

import pytest

from backend.config import BankrollConfig
from backend.models.constants import StageStatus
from backend.services.pyramid import (
    PyramidError,
    PyramidService,
    next_balance,
    project,
    project_path,
    win_probability_for,
)


def test_next_balance_rounds_like_a_payout():
    assert next_balance(2, 1.5) == 3.0
    assert next_balance(6.75, 1.5) == 10.13  # 10.125 rounds half-up


def test_projection_path_from_the_prompt():
    steps = project_path(2, 10_000, 1.5)
    assert [(s.balance_before, s.balance_after) for s in steps[:3]] == [(2.0, 3.0), (3.0, 4.5), (4.5, 6.75)]
    assert len(steps) == 22 and steps[-1].balance_after >= 10_000 and steps[-2].balance_after < 10_000


@pytest.mark.parametrize(("odds", "stages"), [(1.20, 47), (1.30, 33), (1.50, 22), (1.70, 17)])
def test_stages_needed(odds, stages):
    assert project(2, 10_000, odds, 0.05).stages_needed == stages


def test_success_probability_matches_formula():
    projection = project(2, 10_000, 1.5, 0.05)
    assert projection.win_probability == pytest.approx(0.70)
    assert projection.success_probability == pytest.approx(0.70**22)


def test_unreachable_target_at_tiny_odds():
    assert project(2, 10_000, 1.001, 0.0).stages_needed is None


def test_win_probability_is_capped():
    assert win_probability_for(1.01, 0.5) == 0.999


def test_initial_state(db):
    state = PyramidService(db, BankrollConfig()).get_state()
    assert (state.attempt_no, state.stage_no, state.balance, state.remaining) == (1, 1, 2.0, 9998.0)
    assert state.average_odds is None and state.total_staked == 2.0


def test_win_advances_stage(db):
    service = PyramidService(db, BankrollConfig())
    service.open_stage(1.5)
    state = service.settle_stage(StageStatus.WON)
    assert (state.attempt_no, state.stage_no, state.balance) == (1, 2, 3.0)
    assert state.average_odds == 1.5 and state.wins_in_attempt == 1


def test_loss_starts_a_new_attempt(db):
    service = PyramidService(db, BankrollConfig())
    service.open_stage(1.5)
    service.settle_stage(StageStatus.WON)
    service.open_stage(1.4)
    state = service.settle_stage(StageStatus.LOST)
    assert (state.attempt_no, state.stage_no, state.balance) == (2, 1, 2.0)
    assert state.total_staked == 4.0
    assert state.average_odds == pytest.approx(1.45)


def test_void_keeps_stage_and_balance(db):
    service = PyramidService(db, BankrollConfig())
    service.open_stage(1.5)
    state = service.settle_stage(StageStatus.VOID)
    assert (state.stage_no, state.balance, state.average_odds) == (1, 2.0, None)


def test_pending_stage_rules(db):
    service = PyramidService(db, BankrollConfig())
    with pytest.raises(PyramidError):
        service.settle_stage(StageStatus.WON)
    state = service.open_stage(1.3)
    assert state.pending_odds == 1.3
    with pytest.raises(PyramidError) as info:
        service.open_stage(1.3)
    assert "Gözləyən mərhələ" in info.value.user_message
    with pytest.raises(PyramidError):
        PyramidService(db, BankrollConfig()).open_stage(1.0)


def test_milestone_lock(db):
    config = BankrollConfig(starting=40, mode="milestone_lock", milestones=[50], lock_fraction=0.5)
    service = PyramidService(db, config)
    service.open_stage(1.5)  # 40 -> 60 crosses 50: half is locked
    state = service.settle_stage(StageStatus.WON)
    assert state.balance == 30.0 and state.locked_total == 30.0

    # Crossing the same milestone again (30 -> 45 -> 67.50) must not lock a second time.
    service.open_stage(1.5)
    service.settle_stage(StageStatus.WON)
    service.open_stage(1.5)
    state = service.settle_stage(StageStatus.WON)
    assert state.balance == 67.5 and state.locked_total == 30.0
