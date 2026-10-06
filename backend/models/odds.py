"""Bookmaker odds, stored as a time series of snapshots.

A row is written only when a price changes, so the table doubles as odds
history (needed later for closing-line value) without growing on every fetch.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base
from backend.utils.timeutils import utcnow


class OddsSnapshot(Base):
    __tablename__ = "odds"
    __table_args__ = (Index("ix_odds_lookup", "match_id", "market", "selection", "line", "captured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    source: Mapped[str] = mapped_column(String(24), default="api_football")
    bookmaker_api_id: Mapped[int | None]
    bookmaker: Mapped[str] = mapped_column(String(80))
    # Normalised market code: 1X2, DC, DNB, OU, OU_1H, BTTS, AH, TEAM_TOTAL_HOME, ...
    market: Mapped[str] = mapped_column(String(24))
    # 1/X/2, 1X/12/X2, OVER/UNDER, YES/NO
    selection: Mapped[str] = mapped_column(String(16))
    line: Mapped[float | None]
    price: Mapped[float]
    captured_at: Mapped[datetime] = mapped_column(default=utcnow)
    is_closing: Mapped[bool] = mapped_column(default=False)
