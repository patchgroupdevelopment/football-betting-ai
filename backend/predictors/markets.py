"""Model probabilities for every supported market, derived from the goal model.

Half lines only for goal, corner and card totals and Asian handicaps: they have
no push, so "probability" and break-even odds stay unambiguous. Draw-no-bet is
the one market with a push (refund on a draw), handled explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.predictors.poisson import Matrix, count_over_probability, score_matrix


def is_half_line(line: float | None) -> bool:
    return line is not None and abs((line * 2) % 2 - 1) < 1e-9


@dataclass(frozen=True)
class SelectionProbability:
    win: float
    push: float = 0.0

    @property
    def effective(self) -> float:
        """Win probability given the bet is not refunded (comparable with 1 / odds)."""
        return self.win / (1.0 - self.push) if self.push < 1.0 else 0.0

    def expected_value(self, odds: float) -> float:
        return self.win * (odds - 1.0) - (1.0 - self.win - self.push)


@dataclass
class GoalModel:
    lambda_home: float
    lambda_away: float
    rho: float = -0.05
    first_half_fraction: float = 0.44
    elo_probabilities: tuple[float, float, float] | None = None
    elo_weight: float = 0.25
    corners_mean: float | None = None
    cards_mean: float | None = None
    corners_dispersion: float = 1.25
    cards_dispersion: float = 1.35
    _matrix: Matrix = field(init=False, repr=False)
    _first_half: Matrix = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._matrix = score_matrix(self.lambda_home, self.lambda_away, self.rho)
        self._first_half = score_matrix(
            self.lambda_home * self.first_half_fraction, self.lambda_away * self.first_half_fraction, 0.0
        )

    # ------------------------------------------------------------ building blocks

    @property
    def matrix(self) -> Matrix:
        return self._matrix

    def outcome_probabilities(self) -> tuple[float, float, float]:
        """Home/draw/away: Dixon-Coles blended with Elo (if available)."""
        size = len(self._matrix)
        home = sum(self._matrix[x][y] for x in range(size) for y in range(size) if x > y)
        draw = sum(self._matrix[x][x] for x in range(size))
        away = 1.0 - home - draw
        if self.elo_probabilities is None or self.elo_weight <= 0:
            return home, draw, away
        w = self.elo_weight
        elo_home, elo_draw, elo_away = self.elo_probabilities
        return (1 - w) * home + w * elo_home, (1 - w) * draw + w * elo_draw, (1 - w) * away + w * elo_away

    @staticmethod
    def _total_over(matrix: Matrix, line: float) -> float:
        size = len(matrix)
        return sum(matrix[x][y] for x in range(size) for y in range(size) if x + y > line)

    def _team_over(self, home: bool, line: float) -> float:
        size = len(self._matrix)
        return sum(self._matrix[x][y] for x in range(size) for y in range(size) if (x if home else y) > line)

    def _margin_over(self, home: bool, line: float) -> float:
        """P(team's goal difference + line > 0)."""
        size = len(self._matrix)
        return sum(
            self._matrix[x][y] for x in range(size) for y in range(size) if ((x - y) if home else (y - x)) + line > 0
        )

    def btts(self) -> float:
        size = len(self._matrix)
        return sum(self._matrix[x][y] for x in range(1, size) for y in range(1, size))

    # ------------------------------------------------------------------ public

    def probability(self, market: str, selection: str, line: float | None) -> SelectionProbability | None:
        home, draw, away = self.outcome_probabilities()
        if market == "1X2":
            return {"1": SelectionProbability(home), "X": SelectionProbability(draw), "2": SelectionProbability(away)}.get(selection)
        if market == "DC":
            return {
                "1X": SelectionProbability(home + draw),
                "X2": SelectionProbability(draw + away),
                "12": SelectionProbability(home + away),
            }.get(selection)
        if market == "DNB":
            return {"1": SelectionProbability(home, draw), "2": SelectionProbability(away, draw)}.get(selection)
        if market == "BTTS":
            p = self.btts()
            return {"YES": SelectionProbability(p), "NO": SelectionProbability(1 - p)}.get(selection)

        if not is_half_line(line):
            return None
        assert line is not None
        if market == "AH" and selection in ("1", "2"):
            return SelectionProbability(self._margin_over(selection == "1", line))

        over: float | None = None
        if market == "OU":
            over = self._total_over(self._matrix, line)
        elif market == "OU_1H":
            over = self._total_over(self._first_half, line)
        elif market == "TEAM_TOTAL_HOME":
            over = self._team_over(True, line)
        elif market == "TEAM_TOTAL_AWAY":
            over = self._team_over(False, line)
        elif market == "CORNERS_OU" and self.corners_mean is not None:
            over = count_over_probability(self.corners_mean, line, self.corners_dispersion)
        elif market == "CARDS_OU" and self.cards_mean is not None:
            over = count_over_probability(self.cards_mean, line, self.cards_dispersion)
        if over is None:
            return None
        return {"OVER": SelectionProbability(over), "UNDER": SelectionProbability(1 - over)}.get(selection)
