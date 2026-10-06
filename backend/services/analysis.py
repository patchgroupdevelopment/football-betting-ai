"""Daily analysis: fit the models, evaluate every upcoming match, store decisions and picks.

Runs after ingestion. Predictions are kept as history (one row per match per
run); readers take the latest row per match. The day's top picks are also
recorded as paper bets (stake 1 unit) so that results and ROI can be tracked
from the first day, whether or not a real bet is placed.

With AI review on, the best picks get a second opinion (``backend.llm.review``) before the
day's TOP selection; the AI can only lower a probability or veto with sources. Both the
model-only and the final decision are stored (``Prediction.llm_summary``) to measure its effect.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from backend.analyzers.context import build_match_context
from backend.analyzers.team_history import load_finished_matches
from backend.config import AppConfig
from backend.database.session import Database
from backend.i18n import t
from backend.llm.review import AiReviewService, AiVerdict, apply_verdict
from backend.models import Bet, Match, ModelRun, Prediction, Result, Team, TeamRating
from backend.models.constants import NOT_STARTED_STATUSES, BetStatus, Decision, RunStatus
from backend.predictors.model import FittedModels, fit_models, global_cards_average
from backend.selection.engine import MatchEvaluation, evaluate_match, select_top
from backend.selection.narrative import top_factors
from backend.services.runs import RUN_KIND_ANALYSIS
from backend.utils.timeutils import analysis_window, utcnow

logger = logging.getLogger(__name__)

PAPER_STAKE = 1.0
STORED_CANDIDATES = 6


@dataclass
class AnalysisReport:
    target_date: date
    analyzed: int = 0
    bets: int = 0
    watch: int = 0
    no_bet: int = 0
    pick_prediction_ids: list[int] = field(default_factory=list)
    matches_used: int = 0
    warnings: list[str] = field(default_factory=list)
    ai_reviewed: int = 0
    ai_vetoed: int = 0


def _candidate_summary(evaluation: MatchEvaluation) -> list[dict]:
    return [
        {
            "market": e.candidate.market,
            "selection": e.candidate.selection,
            "line": e.candidate.line,
            "odds": e.candidate.odds,
            "bookmaker": e.candidate.bookmaker,
            "p_model": round(e.candidate.p_model, 4),
            "p_market": round(e.candidate.p_market, 4),
            "p_final": round(e.candidate.p_final, 4),
            "ev": round(e.candidate.ev, 4),
            "confidence": e.confidence,
            "risk": e.risk,
            "decision": e.decision,
        }
        for e in evaluation.candidates[:STORED_CANDIDATES]
    ]


def _ai_summary(verdict: AiVerdict | None, before: MatchEvaluation, model_rank: int | None) -> dict | None:
    """What the AI said, plus the model-only decision, so its effect can be measured later."""
    if verdict is None:
        return None
    best = before.best
    return {
        **verdict.to_dict(),
        "decision_before": str(before.decision),
        "rank_before": model_rank,
        "p_before": round(best.candidate.p_final, 4) if best else None,
        "ev_before": round(best.candidate.ev, 4) if best else None,
    }


def _reasons_payload(evaluation: MatchEvaluation) -> dict:
    best = evaluation.best
    model = evaluation.match_model
    quality = evaluation.context.quality
    return {
        "why": list(evaluation.why),
        "against": list(evaluation.against),
        "analysis": list(evaluation.analysis),
        "factors": {k: (round(v, 1) if v is not None else None) for k, v in best.factors.items()} if best else {},
        "top_factors": top_factors(best.factors) if best else [],
        "flags": list(best.flags) if best else [],
        "not_bet": list(evaluation.not_bet_reasons) if evaluation.decision != Decision.BET else [],
        "match_flags": list(evaluation.match_flags),
        "candidates": _candidate_summary(evaluation),
        "lambdas": [round(model.lambdas[0], 3), round(model.lambdas[1], 3)],
        "base_lambdas": [round(model.base_lambdas[0], 3), round(model.base_lambdas[1], 3)],
        "adjustments": [
            {"key": a.key, "team": a.team, "target": a.target, "multiplier": round(a.multiplier, 4)}
            for a in model.adjustments
        ],
        "elo": [round(model.elo_home), round(model.elo_away)],
        "quality": {"score": quality.score, "level": quality.level, "components": quality.components},
    }


class AnalysisService:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        tz: ZoneInfo,
        *,
        clock: Callable[[], datetime] = utcnow,
        reviewer: AiReviewService | None = None,
    ) -> None:
        self._db = db
        self._reviewer = reviewer
        self._config = config
        self._tz = tz
        self._clock = clock

    def run_daily(self, target_date: date) -> AnalysisReport:
        report = AnalysisReport(target_date=target_date)
        now = self._clock()
        started = now
        evaluations: list[MatchEvaluation] = []

        with self._db.session() as session:
            models = self._fit(session, now)
            report.matches_used = models.matches_used
            self._store_elo(session, models, target_date)
            for match in self._upcoming(session, target_date, now):
                try:
                    context = build_match_context(session, match, self._config.model, self._tz)
                    evaluations.append(evaluate_match(context, models, self._config))
                except Exception:
                    logger.exception("Oyunun analizində xəta: %s", match.api_id)
                    report.warnings.append(
                        t("analysis_run.match_failed", match=f"{match.home_team.name} – {match.away_team.name}")
                    )

        max_picks = self._config.selection.max_daily_picks
        model_ranks = {id(e): rank for rank, e in enumerate(select_top(evaluations, max_picks), start=1)}
        verdicts = self._review(evaluations, report)
        final = [
            (apply_verdict(e, verdicts[id(e)], self._config.selection) if id(e) in verdicts else e, e)
            for e in evaluations
        ]
        top = select_top([f for f, _ in final], max_picks)
        ranks = {id(e): rank for rank, e in enumerate(top, start=1)}
        picks: list[tuple[int, int]] = []
        with self._db.session() as session:
            for evaluation, before in final:
                ai = _ai_summary(verdicts.get(id(before)), before, model_ranks.get(id(before)))
                prediction = self._store_prediction(session, evaluation, target_date, ranks.get(id(evaluation)), ai)
                if prediction.rank is not None:
                    picks.append((prediction.rank, prediction.id))
                    self._record_paper_bet(session, prediction)
        report.pick_prediction_ids = [prediction_id for _, prediction_id in sorted(picks)]

        report.analyzed = len(evaluations)
        report.bets = sum(1 for e, _ in final if e.decision == Decision.BET)
        report.watch = sum(1 for e, _ in final if e.decision == Decision.WATCH)
        report.no_bet = report.analyzed - report.bets - report.watch
        self._record_run(target_date, started, report)
        logger.info(
            "Analiz bitdi: %s — %d oyun, %d mərc, %d izləmə, model %d oyunla",
            target_date, report.analyzed, report.bets, report.watch, report.matches_used,
        )
        return report

    # ---------------------------------------------------------------- steps

    def _review(self, evaluations: list[MatchEvaluation], report: AnalysisReport) -> dict[int, AiVerdict]:
        """AI second opinion on the best BET candidates (more than the TOP, in case one is vetoed)."""
        if self._reviewer is None:
            return {}
        candidates = select_top(evaluations, self._reviewer.max_reviews)
        try:
            verdicts = self._reviewer.review_many(candidates)
        except Exception:
            logger.exception("AI rəyi mərhələsində xəta")
            return {}
        report.ai_reviewed = sum(1 for v in verdicts.values() if v.reviews)
        report.ai_vetoed = sum(1 for v in verdicts.values() if v.veto)
        return verdicts

    def _fit(self, session: Session, now: datetime) -> FittedModels:
        since = now - timedelta(days=self._config.model.fit_window_days)
        models = fit_models(load_finished_matches(session, since, now), now, self._config.model)
        cards = list(session.scalars(select(Result.total_cards).where(Result.total_cards.is_not(None))))
        return replace(models, global_cards_avg=global_cards_average(cards))

    @staticmethod
    def _store_elo(session: Session, models: FittedModels, day: date) -> None:
        if not models.elo:
            return
        session.execute(delete(TeamRating).where(TeamRating.as_of == day, TeamRating.source == "elo"))
        for team in session.scalars(select(Team).where(Team.id.in_(list(models.elo)))):
            team.elo = round(models.elo[team.id], 1)
            session.add(TeamRating(team_id=team.id, rating=team.elo, as_of=day, source="elo"))

    def _upcoming(self, session: Session, day: date, now: datetime) -> list[Match]:
        start, end = analysis_window(day, self._tz, self._config.schedule.daily_pipeline)
        return list(
            session.scalars(
                select(Match)
                .options(joinedload(Match.league), joinedload(Match.home_team), joinedload(Match.away_team))
                .where(
                    Match.is_priority.is_(True),
                    Match.kickoff_utc >= max(start, now),
                    Match.kickoff_utc < end,
                    Match.status.in_(NOT_STARTED_STATUSES),
                )
                .order_by(Match.kickoff_utc)
            )
        )

    def _store_prediction(
        self, session: Session, evaluation: MatchEvaluation, day: date, rank: int | None, ai: dict | None = None
    ) -> Prediction:
        best = evaluation.best
        candidate = best.candidate if best else None
        prediction = Prediction(
            match_id=evaluation.context.match_id,
            run_date=day,
            rank=rank,
            market=candidate.market if candidate else "NONE",
            selection=candidate.selection if candidate else "NONE",
            line=candidate.line if candidate else None,
            model_prob=candidate.p_model if candidate else None,
            market_prob=candidate.p_market if candidate else None,
            final_prob=candidate.p_final if candidate else None,
            fair_odds=round(1 / candidate.p_final, 3) if candidate and candidate.p_final > 0 else None,
            best_odds=candidate.odds if candidate else None,
            bookmaker=candidate.bookmaker if candidate else None,
            edge_pp=round(candidate.edge * 100, 2) if candidate else None,
            ev=round(candidate.ev, 4) if candidate else None,
            confidence=best.confidence if best else None,
            risk_level=best.risk if best else None,
            decision=evaluation.decision,
            reasons=_reasons_payload(evaluation),
            llm_summary=ai,
            model_version=self._config.model.version,
        )
        session.add(prediction)
        session.flush()
        return prediction

    @staticmethod
    def _record_paper_bet(session: Session, prediction: Prediction) -> None:
        line_filter = Prediction.line.is_(None) if prediction.line is None else Prediction.line == prediction.line
        existing = session.scalar(
            select(Bet.id)
            .join(Prediction, Prediction.id == Bet.prediction_id)
            .where(
                Bet.is_paper.is_(True),
                Prediction.match_id == prediction.match_id,
                Prediction.market == prediction.market,
                Prediction.selection == prediction.selection,
                line_filter,
            )
        )
        if existing is None and prediction.best_odds:
            session.add(
                Bet(
                    prediction_id=prediction.id,
                    is_paper=True,
                    stake=PAPER_STAKE,
                    odds_taken=prediction.best_odds,
                    status=BetStatus.PENDING,
                )
            )

    def _record_run(self, day: date, started: datetime, report: AnalysisReport) -> None:
        with self._db.session() as session:
            session.add(
                ModelRun(
                    kind=RUN_KIND_ANALYSIS,
                    run_date=day,
                    started_at=started,
                    finished_at=self._clock(),
                    status=RunStatus.PARTIAL if report.warnings else RunStatus.SUCCESS,
                    details={
                        "analyzed": report.analyzed,
                        "bets": report.bets,
                        "watch": report.watch,
                        "no_bet": report.no_bet,
                        "matches_used": report.matches_used,
                        "warnings": report.warnings,
                    },
                )
            )
