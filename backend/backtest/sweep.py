"""Parameter search with an out-of-sample check.

Every combination of model weight, minimum EV and minimum confidence is replayed on the stored
candidates. A combination counts as robust only if it is profitable in *both* halves of the
period on enough bets and its prices beat the closing line on average (CLV > 0) — CLV does not
depend on match luck, so it guards against combinations that were merely fortunate. Even then
the in-sample ROI of the winner is optimistic; the CLV is the more honest estimate of the edge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from backend.backtest.engine import CandidateRecord
from backend.backtest.metrics import summarize
from backend.backtest.simulate import Params, select_bets

WEIGHTS = (0.0, 0.05, 0.1, 0.2, 0.3, 0.4)
MIN_EVS = (0.0, 0.01, 0.02, 0.03)
MIN_CONFIDENCES = (55, 60, 65, 70, 75)
MIN_BETS_PER_HALF = 30


@dataclass(frozen=True)
class SweepRow:
    params: Params
    total: dict
    first: dict
    second: dict

    @property
    def robust(self) -> bool:
        return (
            self.first["bets"] >= MIN_BETS_PER_HALF
            and self.second["bets"] >= MIN_BETS_PER_HALF
            and self.first["roi"] > 0
            and self.second["roi"] > 0
            and (self.total["avg_clv"] or 0) > 0
        )

    @property
    def worst_half_roi(self) -> float:
        return min(self.first["roi"] or 0.0, self.second["roi"] or 0.0)


def sweep(records: Sequence[CandidateRecord], base: Params, split: date) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for weight in WEIGHTS:
        for min_ev in MIN_EVS:
            for min_confidence in MIN_CONFIDENCES:
                params = replace(base, model_weight=weight, min_ev=min_ev, min_confidence=min_confidence)
                bets = select_bets(records, params)
                rows.append(
                    SweepRow(
                        params,
                        summarize(bets),
                        summarize([b for b in bets if b.record.day < split]),
                        summarize([b for b in bets if b.record.day >= split]),
                    )
                )
    return rows


def choose(rows: Sequence[SweepRow]) -> SweepRow | None:
    """The robust combination with the best worse half; ties go to the stricter, more market-based rules."""
    robust = [row for row in rows if row.robust]
    if not robust:
        return None
    return max(
        robust,
        key=lambda r: (
            round(r.worst_half_roi, 2),
            r.total["avg_clv"] or 0.0,
            r.params.min_confidence,
            -r.params.model_weight,
        ),
    )


def row_view(row: SweepRow) -> dict:
    return {
        "model_weight": row.params.model_weight,
        "min_ev": row.params.min_ev,
        "min_confidence": row.params.min_confidence,
        "robust": row.robust,
        "bets": row.total["bets"],
        "hit_rate": row.total["hit_rate"],
        "roi": row.total["roi"],
        "avg_clv": row.total["avg_clv"],
        "first_roi": row.first["roi"],
        "second_roi": row.second["roi"],
        "first_bets": row.first["bets"],
        "second_bets": row.second["bets"],
    }
