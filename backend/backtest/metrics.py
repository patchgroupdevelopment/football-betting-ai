"""Backtest metrics: profitability, risk, calibration and the pyramid's course."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from statistics import fmean

from backend.backtest.engine import CandidateRecord, MatchRecord
from backend.backtest.simulate import Attempt, SimBet, value

CONFIDENCE_BUCKETS = ((75, 79), (80, 84), (85, 100))
ODDS_BUCKETS = (("1.20–1.34", 1.20, 1.35), ("1.35–1.49", 1.35, 1.50), ("1.50–1.70", 1.50, 1.7001))
PROBABILITY_BINS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def summarize(bets: Sequence[SimBet]) -> dict:
    n = len(bets)
    wins = sum(1 for b in bets if b.record.outcome == "win")
    losses = sum(1 for b in bets if b.record.outcome == "loss")
    pushes = n - wins - losses
    total = sum(b.profit for b in bets)

    running = peak = drawdown = 0.0
    streak = longest = 0
    for bet in sorted(bets, key=lambda b: (b.record.kickoff_utc, b.rank)):
        running += bet.profit
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        if bet.record.outcome == "loss":
            streak += 1
            longest = max(longest, streak)
        elif bet.record.outcome == "win":
            streak = 0

    clvs = [b.clv for b in bets if b.clv is not None]
    return {
        "bets": n,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": _round(wins / (wins + losses) if wins + losses else None),
        "expected_hit_rate": _round(fmean(b.p_final for b in bets) if bets else None),
        "avg_odds": _round(fmean(b.odds for b in bets) if bets else None, 3),
        "profit": round(total, 2),
        "roi": _round(total / n if n else None),
        "avg_ev": _round(fmean(b.ev for b in bets) if bets else None),
        "max_drawdown": round(drawdown, 2),
        "longest_losing_streak": longest,
        "avg_clv": _round(fmean(clvs) if clvs else None),
        "clv_positive": _round(sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None),
        "clv_bets": len(clvs),
    }


def breakdown(bets: Iterable[SimBet], key: Callable[[SimBet], str | None]) -> dict[str, dict]:
    groups: dict[str, list[SimBet]] = defaultdict(list)
    for bet in bets:
        name = key(bet)
        if name is not None:
            groups[name].append(bet)
    return {name: summarize(items) for name, items in sorted(groups.items())}


def confidence_bucket(bet: SimBet) -> str | None:
    for low, high in CONFIDENCE_BUCKETS:
        if low <= bet.record.confidence <= high:
            return f"{low}–{high}" if high < 100 else f"{low}+"
    return f"<{CONFIDENCE_BUCKETS[0][0]}"


def odds_bucket(bet: SimBet) -> str | None:
    return next((label for label, low, high in ODDS_BUCKETS if low <= bet.odds < high), None)


def calibration(records: Sequence[CandidateRecord], model_weight: float) -> dict[str, list[dict]]:
    """Predicted vs actual win rate of every priced candidate (not only the picks)."""
    settled = [r for r in records if r.outcome in ("win", "loss")]
    sources: dict[str, Callable[[CandidateRecord], float]] = {
        "model": lambda r: r.p_model,
        "market": lambda r: r.p_market,
        "final": lambda r: value(r, model_weight)[1],
    }
    result: dict[str, list[dict]] = {}
    for name, probability in sources.items():
        rows = []
        for low, high in zip(PROBABILITY_BINS, PROBABILITY_BINS[1:]):
            members = [r for r in settled if low <= probability(r) < high]
            if not members:
                continue
            rows.append(
                {
                    "bin": f"{low:.2f}–{high:.2f}",
                    "count": len(members),
                    "predicted": round(fmean(probability(r) for r in members), 4),
                    "actual": round(sum(1 for r in members if r.outcome == "win") / len(members), 4),
                }
            )
        result[name] = rows
    return result


def _brier(probabilities: tuple[float, float, float], actual: int) -> float:
    return sum((p - (1.0 if i == actual else 0.0)) ** 2 for i, p in enumerate(probabilities))


def probability_scores(matches: Sequence[MatchRecord], model_weight: float) -> dict[str, dict]:
    """Brier score and log loss of the 1X2 probabilities (lower is better) on the matches priced by the market."""
    priced = [m for m in matches if m.market_1x2 is not None]
    sources: dict[str, Callable[[MatchRecord], tuple[float, float, float] | None]] = {
        "model": lambda m: m.model_1x2,
        "market": lambda m: m.market_1x2,
        "final": lambda m: tuple(  # type: ignore[return-value]
            model_weight * a + (1 - model_weight) * b for a, b in zip(m.model_1x2, m.market_1x2)  # type: ignore[arg-type]
        ),
        "closing": lambda m: m.closing_1x2,
    }
    result: dict[str, dict] = {}
    for name, get in sources.items():
        briers, losses = [], []
        for m in priced:
            probabilities = get(m)
            if probabilities is None:
                continue
            actual = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
            briers.append(_brier(probabilities, actual))
            losses.append(-math.log(max(probabilities[actual], 1e-9)))
        if briers:
            result[name] = {"matches": len(briers), "brier": round(fmean(briers), 5), "log_loss": round(fmean(losses), 5)}
    return result


# ------------------------------------------------------------------ pyramid


def _stage_view(stage, titles: dict[int, str], matches: Callable[[int], MatchRecord]) -> dict:  # noqa: ANN001
    record = stage.bet.record
    match = matches(record.match_index)
    return {
        "day": record.day.isoformat(),
        "league": titles.get(record.league_id, ""),
        "home": match.home,
        "away": match.away,
        "score": f"{match.home_goals}:{match.away_goals}",
        "market": record.market,
        "selection": record.selection,
        "line": record.line,
        "odds": stage.bet.odds,
        "balance_before": stage.balance_before,
        "balance_after": stage.balance_after,
    }


def pyramid_summary(
    attempts: Sequence[Attempt],
    starting: float,
    titles: dict[int, str],
    matches: Callable[[int], MatchRecord],
) -> dict:
    if not attempts or not attempts[0].stages:
        return {"attempts": 0, "stages": 0}
    peak_attempt = max(attempts, key=lambda a: a.peak)
    lost = [a for a in attempts if a.status == "lost"]
    breaks: dict[int, int] = defaultdict(int)  # stage number at which an attempt broke -> count
    for attempt in lost:
        breaks[len(attempt.stages)] += 1
    peak_stage = max(peak_attempt.stages, key=lambda s: max(s.balance_after, s.balance_before))
    current = attempts[-1]
    return {
        "attempts": len(attempts),
        "stages": sum(len(a.stages) for a in attempts),
        "targets_reached": sum(1 for a in attempts if a.status == "target"),
        "lost_attempts": len(lost),
        "invested": round(starting * len(attempts), 2),
        "locked_total": round(sum(a.locked for a in attempts), 2),
        "max_balance": round(peak_attempt.peak, 2),
        "max_attempt": {
            "number": peak_attempt.number,
            "wins": peak_attempt.wins,
            "status": peak_attempt.status,
            "start": peak_attempt.stages[0].bet.record.day.isoformat(),
            "peak_day": peak_stage.bet.record.day.isoformat(),
            "path": [_stage_view(s, titles, matches) for s in peak_attempt.stages],
        },
        "longest_run": max(a.wins for a in attempts),
        "avg_wins_before_break": round(fmean(a.wins for a in lost), 2) if lost else None,
        "break_histogram": {str(k): v for k, v in sorted(breaks.items())},
        "current": {
            "number": current.number,
            "status": current.status,
            "wins": current.wins,
            "balance": current.stages[-1].balance_after if current.stages else starting,
        },
        "attempt_list": [
            {
                "number": a.number,
                "start": a.stages[0].bet.record.day.isoformat() if a.stages else None,
                "wins": a.wins,
                "peak": round(a.peak, 2),
                "status": a.status,
                "broke_at": _stage_view(a.breaking_stage, titles, matches) if a.breaking_stage else None,
            }
            for a in attempts
        ],
    }
