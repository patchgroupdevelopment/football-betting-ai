"""Predictions, (paper) bets and the bankroll pyramid ledger."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base
from backend.utils.timeutils import utcnow

if TYPE_CHECKING:
    from backend.models.football import Match


class Prediction(Base):
    """One row per analysed match and analysis run: the best candidate and the decision.

    ``market == "NONE"`` marks a match with no usable candidate (e.g. no odds).
    """

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    run_date: Mapped[date | None] = mapped_column(Date, index=True)  # the analysis day it belongs to
    rank: Mapped[int | None]  # 1..3 for the day's top picks
    market: Mapped[str] = mapped_column(String(24))
    selection: Mapped[str] = mapped_column(String(16))
    line: Mapped[float | None]
    model_prob: Mapped[float | None]
    market_prob: Mapped[float | None]
    final_prob: Mapped[float | None]
    fair_odds: Mapped[float | None]
    best_odds: Mapped[float | None]
    bookmaker: Mapped[str | None] = mapped_column(String(80))
    edge_pp: Mapped[float | None]  # probability edge, percentage points
    ev: Mapped[float | None]  # expected value: probability × odds − 1
    confidence: Mapped[int | None]
    risk_level: Mapped[str | None] = mapped_column(String(8))  # RiskLevel
    decision: Mapped[str] = mapped_column(String(8))  # Decision
    reasons: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    llm_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    match: Mapped[Match] = relationship()


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"), index=True)
    is_paper: Mapped[bool] = mapped_column(default=True)
    stake: Mapped[float]
    odds_taken: Mapped[float]  # the price actually obtained (entered by the user)
    odds_closing: Mapped[float | None]
    clv: Mapped[float | None]  # closing-line value
    status: Mapped[str] = mapped_column(String(12), default="pending")  # BetStatus
    pnl: Mapped[float | None]
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)
    settled_at: Mapped[datetime | None]


class PyramidStage(Base):
    __tablename__ = "pyramid_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_no: Mapped[int] = mapped_column(index=True)
    stage_no: Mapped[int]
    balance_before: Mapped[float]
    odds: Mapped[float]
    bet_id: Mapped[int | None] = mapped_column(ForeignKey("bets.id"))
    balance_after: Mapped[float | None]
    locked_amount: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(8), default="pending")  # StageStatus
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    settled_at: Mapped[datetime | None]
