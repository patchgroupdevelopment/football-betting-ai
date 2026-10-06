"""Second opinion on the day's picks from AI reviewers (Gemini and/or Claude), used only as a brake.

Rules (the AI is never the decision maker and never does the arithmetic):
- every reviewer sees the same facts and answers independently;
- the probability change is the reviewers' average, and only a *decrease* is applied (at most
  ``llm_max_adjustment_pp``); the system then recomputes EV and the decision itself;
- a veto blocks the pick only if it is backed by web sources and no reviewer supports the pick;
- news without sources is discarded, so nothing unverifiable reaches the user;
- a failing reviewer (quota, network, unreadable answer) is skipped; with none left the pick stays as is.
Answers are cached per pick, so the afternoon run does not ask again.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.config import SelectionConfig
from backend.llm.prompt import build_prompt, system_prompt
from backend.llm.providers import LlmError, Provider, Source
from backend.models.constants import Decision
from backend.selection.engine import MatchEvaluation
from backend.selection.scoring import decide
from backend.services.cache import ResponseCache

logger = logging.getLogger(__name__)

MAX_ITEMS = 4
MAX_TEXT = 300


class _Answer(BaseModel):
    verdict: Literal["support", "neutral", "against"]
    adjustment_pp: float = 0.0
    veto: bool = False
    summary: str = ""
    risks: list[str] = Field(default_factory=list)
    news: list[str] = Field(default_factory=list)

    @field_validator("risks", "news", mode="before")
    @classmethod
    def _strings(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:MAX_TEXT] for item in value if str(item).strip()][:MAX_ITEMS]


def extract_json(text: str) -> dict:
    """The JSON object in a reply, tolerating markdown fences and text around it."""
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(cleaned[start : end + 1])


@dataclass(frozen=True)
class AiReview:
    provider: str
    label: str
    model: str
    verdict: str
    adjustment_pp: float
    veto: bool
    summary: str
    risks: tuple[str, ...] = ()
    news: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()

    @property
    def grounded(self) -> bool:
        return bool(self.sources)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sources"] = [asdict(s) for s in self.sources]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> AiReview:
        return cls(
            provider=data["provider"],
            label=data["label"],
            model=data["model"],
            verdict=data["verdict"],
            adjustment_pp=float(data["adjustment_pp"]),
            veto=bool(data["veto"]),
            summary=data.get("summary", ""),
            risks=tuple(data.get("risks") or ()),
            news=tuple(data.get("news") or ()),
            sources=tuple(Source(s.get("title", ""), s.get("url", "")) for s in data.get("sources") or ()),
        )


def parse_review(provider: Provider, text: str, sources: tuple[Source, ...], max_pp: float) -> AiReview:
    try:
        answer = _Answer.model_validate(extract_json(text))
    except (ValueError, ValidationError) as exc:
        raise LlmError(provider.name, f"unreadable answer: {exc}") from exc
    grounded = bool(sources)
    return AiReview(
        provider=provider.name,
        label=provider.label,
        model=provider.model,
        verdict=answer.verdict,
        adjustment_pp=max(-max_pp, min(max_pp, answer.adjustment_pp)),
        veto=answer.veto,
        summary=answer.summary.strip()[:MAX_TEXT * 2],
        risks=tuple(answer.risks),
        news=tuple(answer.news) if grounded else (),  # unsourced news is never shown
        sources=sources,
    )


@dataclass(frozen=True)
class AiVerdict:
    reviews: tuple[AiReview, ...]
    failed: tuple[str, ...] = ()  # labels of reviewers that gave no usable answer
    applied_pp: float = 0.0
    veto: bool = False

    def to_dict(self) -> dict:
        return {
            "reviews": [r.to_dict() for r in self.reviews],
            "failed": list(self.failed),
            "applied_pp": self.applied_pp,
            "veto": self.veto,
        }


def combine(reviews: Sequence[AiReview], failed: Sequence[str], max_pp: float) -> AiVerdict:
    if not reviews:
        return AiVerdict((), tuple(failed))
    applied = max(-max_pp, min(0.0, fmean(r.adjustment_pp for r in reviews)))
    veto = any(r.veto and r.grounded for r in reviews) and not any(r.verdict == "support" for r in reviews)
    return AiVerdict(tuple(reviews), tuple(failed), round(applied, 2), veto)


def apply_verdict(evaluation: MatchEvaluation, verdict: AiVerdict, cfg: SelectionConfig) -> MatchEvaluation:
    """The pick after the AI's brake: lower probability → EV and decision recomputed by the system."""
    best = evaluation.best
    if best is None or (verdict.applied_pp == 0 and not verdict.veto):
        return evaluation
    c = best.candidate
    p_final = min(0.99, max(0.01, c.p_final + verdict.applied_pp / 100))
    adjusted = replace(c, p_final=p_final, ev=(1 - c.push) * (p_final * c.odds - 1))
    decision, not_bet = decide(adjusted, best.confidence, best.risk, best.flags, cfg)
    if verdict.veto:
        decision, not_bet = Decision.NO_BET, ["ai_veto", *[r for r in not_bet if r != "ai_veto"]]
    new_best = replace(best, candidate=adjusted, decision=decision, not_bet=tuple(not_bet))
    return replace(evaluation, candidates=(new_best, *evaluation.candidates[1:]))


@dataclass
class AiReviewService:
    providers: list[Provider]
    max_pp: float
    max_reviews: int = 5
    cache: ResponseCache | None = None
    cache_seconds: int = 8 * 3600
    workers: int = 4
    on_error: Callable[[LlmError], None] | None = field(default=None, repr=False)

    @property
    def labels(self) -> list[str]:
        return [p.label for p in self.providers]

    def _cache_params(self, provider: Provider, evaluation: MatchEvaluation) -> dict:
        c = evaluation.best.candidate  # type: ignore[union-attr]
        ctx = evaluation.context
        return {
            "match": ctx.match_id,
            "kickoff": ctx.kickoff_utc.isoformat(),
            "market": c.market,
            "selection": c.selection,
            "line": c.line,
            "model": provider.model,
        }

    def _ask(self, provider: Provider, evaluation: MatchEvaluation, system: str, prompt: str) -> AiReview:
        endpoint = f"llm_{provider.name}"
        params = self._cache_params(provider, evaluation)
        if self.cache is not None:
            cached = self.cache.get(endpoint, params)
            if cached is not None:
                return AiReview.from_dict(cached)
        reply = provider.ask(system, prompt)
        review = parse_review(provider, reply.text, reply.sources, self.max_pp)
        if self.cache is not None and self.cache_seconds > 0:
            self.cache.set(endpoint, params, review.to_dict(), self.cache_seconds)
        return review

    def review_many(self, evaluations: Sequence[MatchEvaluation]) -> dict[int, AiVerdict]:
        """``id(evaluation)`` → verdict, for the given picks (the caller passes the best ones)."""
        targets = [e for e in evaluations if e.best is not None][: self.max_reviews]
        if not targets or not self.providers:
            return {}
        system = system_prompt(self.max_pp)
        jobs = [(e, p, build_prompt(e)) for e in targets for p in self.providers]
        results: dict[int, list[AiReview]] = {id(e): [] for e in targets}
        failed: dict[int, list[str]] = {id(e): [] for e in targets}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [(e, p, pool.submit(self._ask, p, e, system, prompt)) for e, p, prompt in jobs]
            for evaluation, provider, future in futures:
                try:
                    results[id(evaluation)].append(future.result())
                except LlmError as exc:
                    logger.warning("AI rəyi alınmadı (%s): %s", provider.label, exc.detail)
                    failed[id(evaluation)].append(provider.label)
                    if self.on_error is not None:
                        self.on_error(exc)
                except Exception:  # never let a reviewer break the analysis
                    logger.exception("AI rəyində gözlənilməz xəta (%s)", provider.label)
                    failed[id(evaluation)].append(provider.label)
        return {key: combine(results[key], failed[key], self.max_pp) for key in results}
