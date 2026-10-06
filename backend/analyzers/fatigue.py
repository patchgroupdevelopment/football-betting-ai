"""Rest days and fixture congestion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.analyzers.team_history import TeamMatch

CONGESTION_WINDOW = timedelta(days=14)
CONGESTED_MATCHES = 4


@dataclass(frozen=True)
class FatigueInfo:
    rest_days: float | None
    matches_last_14d: int

    def is_short_rest(self, threshold_days: float) -> bool:
        return self.rest_days is not None and self.rest_days <= threshold_days


def fatigue_info(history: Sequence[TeamMatch], kickoff_utc: datetime) -> FatigueInfo:
    """``history`` newest first."""
    if not history:
        return FatigueInfo(rest_days=None, matches_last_14d=0)
    rest = (kickoff_utc - history[0].kickoff_utc).total_seconds() / 86_400
    recent = sum(1 for m in history if kickoff_utc - m.kickoff_utc <= CONGESTION_WINDOW)
    return FatigueInfo(rest_days=round(rest, 1), matches_last_14d=recent)


def fatigue_multiplier(info: FatigueInfo, short_rest_days: float, penalty: float) -> float:
    """Attack multiplier (≤ 1) for a tired team; the same factor inflates goals it concedes."""
    multiplier = 1.0
    if info.is_short_rest(short_rest_days):
        multiplier -= penalty
        if info.rest_days is not None and info.rest_days <= 2:
            multiplier -= penalty / 2
    if info.matches_last_14d >= CONGESTED_MATCHES:
        multiplier -= penalty / 2
    return max(1.0 - 2 * penalty, multiplier)
