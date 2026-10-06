"""Settling a selection against a final result — shared by live bets and the backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["win", "loss", "push"]


@dataclass(frozen=True)
class FinalScore:
    home: int
    away: int
    ht_home: int | None = None
    ht_away: int | None = None
    corners: int | None = None
    cards: int | None = None


def _over(value: int | None, selection: str, line: float) -> Outcome | None:
    if value is None:
        return None
    if value == line:
        return "push"  # integer lines only; the models use half lines
    over = value > line
    return "win" if over == (selection == "OVER") else "loss"


def settle(market: str, selection: str, line: float | None, score: FinalScore) -> Outcome | None:
    """None when the result cannot be decided from the data (e.g. missing half-time score)."""
    hg, ag = score.home, score.away
    if market == "1X2":
        winner = "1" if hg > ag else "2" if ag > hg else "X"
        return "win" if selection == winner else "loss"
    if market == "DC":
        wins = {"1X": hg >= ag, "X2": ag >= hg, "12": hg != ag}.get(selection)
        return None if wins is None else "win" if wins else "loss"
    if market == "DNB":
        if hg == ag:
            return "push"
        return "win" if (hg > ag) == (selection == "1") else "loss"
    if market == "BTTS":
        both = hg > 0 and ag > 0
        return "win" if both == (selection == "YES") else "loss"
    if line is None:
        return None
    if market == "AH":
        margin = (hg - ag if selection == "1" else ag - hg) + line
        return "push" if margin == 0 else "win" if margin > 0 else "loss"
    if market == "OU":
        return _over(hg + ag, selection, line)
    if market == "OU_1H":
        if score.ht_home is None or score.ht_away is None:
            return None
        return _over(score.ht_home + score.ht_away, selection, line)
    if market == "TEAM_TOTAL_HOME":
        return _over(hg, selection, line)
    if market == "TEAM_TOTAL_AWAY":
        return _over(ag, selection, line)
    if market == "CORNERS_OU":
        return _over(score.corners, selection, line)
    if market == "CARDS_OU":
        return _over(score.cards, selection, line)
    return None


def profit(outcome: Outcome, odds: float, stake: float = 1.0) -> float:
    if outcome == "win":
        return stake * (odds - 1.0)
    if outcome == "push":
        return 0.0
    return -stake
