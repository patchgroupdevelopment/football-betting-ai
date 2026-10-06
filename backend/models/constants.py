"""Shared enumerations and status sets."""

from __future__ import annotations

from enum import StrEnum

# API-Football fixture status codes.
FINISHED_STATUSES = frozenset({"FT", "AET", "PEN"})
NOT_STARTED_STATUSES = frozenset({"NS", "TBD"})
LIVE_STATUSES = frozenset({"1H", "HT", "2H", "ET", "BT", "P", "INT", "SUSP", "LIVE"})
CANCELLED_STATUSES = frozenset({"PST", "CANC", "ABD", "AWD", "WO"})


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class InjuryKind(StrEnum):
    INJURY = "injury"
    SUSPENSION = "suspension"
    DOUBTFUL = "doubtful"


class StageStatus(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


class BetStatus(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    HALF_WON = "half_won"
    HALF_LOST = "half_lost"


class Decision(StrEnum):
    BET = "bet"
    WATCH = "watch"
    NO_BET = "no_bet"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
