"""Bankroll pyramid: balance × odds = next stage's balance.

Money is computed with ``Decimal`` and rounded half-up to cents, like a
bookmaker payout. The ledger (``pyramid_stages``) is the single source of
truth; the current state is always derived from it.

A lost stage ends the attempt; the next attempt restarts from the starting
balance. Tracking attempts keeps the real cost of the strategy visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from statistics import fmean

from sqlalchemy import func, select

from backend.config import BankrollConfig
from backend.database.session import Database
from backend.i18n import t
from backend.models import PyramidStage
from backend.models.constants import StageStatus
from backend.utils.timeutils import utcnow

_CENT = Decimal("0.01")
MAX_PROJECTION_STEPS = 500
MAX_WIN_PROBABILITY = 0.999


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def next_balance(balance: float, odds: float) -> float:
    return float(_money(_money(balance) * Decimal(str(odds))))


def milestone_lock(before: float, after: float, config: BankrollConfig, passed: int = 0) -> float:
    """In milestone_lock mode, reaching the next milestone moves a share of the balance out of play.

    Each milestone locks once per attempt, in order (``passed`` = milestones already locked in the
    attempt): after a lock the balance falls back below the milestone, and crossing it again must
    not lock again, or the balance could never grow past the first milestone.
    """
    if config.mode != "milestone_lock":
        return 0.0
    pending = sorted(config.milestones)[passed:]
    if not pending or after < pending[0] or before >= after:
        return 0.0
    return float(_money(Decimal(str(after)) * Decimal(str(config.lock_fraction))))


@dataclass(frozen=True)
class PyramidStep:
    stage_no: int
    balance_before: float
    odds: float
    balance_after: float


@dataclass(frozen=True)
class PyramidProjection:
    odds: float
    steps: tuple[PyramidStep, ...]
    stages_needed: int | None  # None when the target is unreachable at these odds
    assumed_ev: float
    win_probability: float
    success_probability: float


def project_path(balance: float, target: float, odds: float, *, start_stage: int = 1) -> list[PyramidStep]:
    if odds <= 1:
        raise ValueError("odds must be greater than 1")
    steps: list[PyramidStep] = []
    current = _money(balance)
    goal = _money(target)
    multiplier = Decimal(str(odds))
    while current < goal and len(steps) < MAX_PROJECTION_STEPS:
        after = _money(current * multiplier)
        if after <= current:  # odds too close to 1.00 to grow after rounding
            break
        steps.append(PyramidStep(start_stage + len(steps), float(current), odds, float(after)))
        current = after
    return steps


def win_probability_for(odds: float, assumed_ev: float) -> float:
    """Win probability implied by an assumed edge: EV = p × odds − 1  =>  p = (1 + EV) / odds."""
    return min(MAX_WIN_PROBABILITY, (1 + assumed_ev) / odds)


def project(balance: float, target: float, odds: float, assumed_ev: float, *, start_stage: int = 1) -> PyramidProjection:
    steps = project_path(balance, target, odds, start_stage=start_stage)
    reached = bool(steps) and steps[-1].balance_after >= target
    if balance >= target:
        stages_needed: int | None = 0
    else:
        stages_needed = len(steps) if reached else None
    win_probability = win_probability_for(odds, assumed_ev)
    success = win_probability ** stages_needed if stages_needed is not None else 0.0
    return PyramidProjection(
        odds=odds,
        steps=tuple(steps),
        stages_needed=stages_needed,
        assumed_ev=assumed_ev,
        win_probability=win_probability,
        success_probability=success,
    )


class PyramidError(Exception):
    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.user_message = t(message_key)


@dataclass(frozen=True)
class PyramidState:
    attempt_no: int
    stage_no: int
    balance: float
    starting: float
    target: float
    mode: str
    locked_total: float
    average_odds: float | None
    wins_in_attempt: int
    pending_odds: float | None

    @property
    def remaining(self) -> float:
        return max(0.0, float(_money(self.target) - _money(self.balance)))

    @property
    def target_reached(self) -> bool:
        return self.balance >= self.target

    @property
    def total_staked(self) -> float:
        """Every attempt starts from the starting balance, so this is the money put in."""
        return float(_money(self.starting) * self.attempt_no)


class PyramidService:
    def __init__(self, db: Database, config: BankrollConfig) -> None:
        self._db = db
        self._config = config

    def get_state(self) -> PyramidState:
        with self._db.session() as session:
            rows = list(session.scalars(select(PyramidStage).order_by(PyramidStage.id)))
            return self._derive(rows)

    def projection(self, state: PyramidState) -> PyramidProjection:
        odds = state.average_odds or self._config.projection_odds
        return project(state.balance, state.target, odds, self._config.assumed_ev, start_stage=state.stage_no)

    def open_stage(self, odds: float, bet_id: int | None = None) -> PyramidState:
        if odds <= 1:
            raise PyramidError("pyramid.error_bad_odds")
        with self._db.session() as session:
            state = self._derive(list(session.scalars(select(PyramidStage).order_by(PyramidStage.id))))
            if state.pending_odds is not None:
                raise PyramidError("pyramid.error_pending_exists")
            if state.target_reached:
                raise PyramidError("pyramid.error_target_reached")
            session.add(
                PyramidStage(
                    attempt_no=state.attempt_no,
                    stage_no=state.stage_no,
                    balance_before=state.balance,
                    odds=odds,
                    bet_id=bet_id,
                    status=StageStatus.PENDING,
                )
            )
        return self.get_state()

    def settle_stage(self, outcome: StageStatus) -> PyramidState:
        if outcome == StageStatus.PENDING:
            raise ValueError("outcome must be won, lost or void")
        with self._db.session() as session:
            pending = session.scalar(
                select(PyramidStage)
                .where(PyramidStage.status == StageStatus.PENDING)
                .order_by(PyramidStage.id.desc())
                .limit(1)
            )
            if pending is None:
                raise PyramidError("pyramid.error_no_pending")
            if outcome == StageStatus.WON:
                gross = next_balance(pending.balance_before, pending.odds)
                passed = session.scalar(
                    select(func.count(PyramidStage.id)).where(
                        PyramidStage.attempt_no == pending.attempt_no, PyramidStage.locked_amount > 0
                    )
                )
                locked = milestone_lock(pending.balance_before, gross, self._config, passed or 0)
                pending.balance_after = float(_money(gross) - _money(locked))
                pending.locked_amount = locked
            elif outcome == StageStatus.LOST:
                pending.balance_after = 0.0
            else:
                pending.balance_after = pending.balance_before
            pending.status = outcome
            pending.settled_at = utcnow()
        return self.get_state()

    def _derive(self, rows: list[PyramidStage]) -> PyramidState:
        cfg = self._config
        settled = [r for r in rows if r.status in (StageStatus.WON, StageStatus.LOST)]
        average_odds = fmean(r.odds for r in settled) if settled else None
        locked_total = float(sum((_money(r.locked_amount or 0) for r in rows), Decimal("0")))

        pending_odds: float | None = None
        if not rows:
            attempt, stage, balance = 1, 1, cfg.starting
        else:
            last = rows[-1]
            if last.status == StageStatus.PENDING:
                attempt, stage, balance = last.attempt_no, last.stage_no, last.balance_before
                pending_odds = last.odds
            elif last.status == StageStatus.WON:
                attempt, stage, balance = last.attempt_no, last.stage_no + 1, last.balance_after or 0.0
            elif last.status == StageStatus.VOID:
                attempt, stage, balance = last.attempt_no, last.stage_no, last.balance_before
            else:  # lost: a new attempt starts from the starting balance
                attempt, stage, balance = last.attempt_no + 1, 1, cfg.starting

        wins = sum(1 for r in rows if r.attempt_no == attempt and r.status == StageStatus.WON)
        return PyramidState(
            attempt_no=attempt,
            stage_no=stage,
            balance=balance,
            starting=cfg.starting,
            target=cfg.target,
            mode=cfg.mode,
            locked_total=locked_total,
            average_odds=average_odds,
            wins_in_attempt=wins,
            pending_odds=pending_odds,
        )
