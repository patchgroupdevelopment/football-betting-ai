"""AI reviewers: providers (mocked HTTP), parsing, the brake rule, caching and the analysis integration."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.config import AppConfig
from backend.llm.prompt import build_prompt, system_prompt
from backend.llm.providers import ClaudeProvider, GeminiProvider, LlmError, ProviderReply, Source
from backend.llm.review import AiReview, AiReviewService, apply_verdict, combine, extract_json, parse_review
from backend.models.constants import Decision
from backend.presenters.formatters import format_match_detail
from backend.selection.engine import evaluate_match
from backend.services.cache import ResponseCache
from tests.builders import goal_models, value_scenario

ANSWER = {
    "verdict": "against",
    "adjustment_pp": -3,
    "veto": False,
    "summary": "Ev sahibinin əsas hücumçusu zədəlidir.",
    "risks": ["Rotasiya ehtimalı"],
    "news": ["Məşqçi hücumçunun oynamayacağını təsdiqləyib."],
}


def _evaluation():
    evaluation = evaluate_match(value_scenario(), goal_models(), AppConfig())
    assert evaluation.decision == Decision.BET
    return evaluation


class FakeProvider:
    def __init__(self, name: str, label: str, answer: dict | str | Exception, sources: tuple[Source, ...] = ()) -> None:
        self.name, self.label, self.model = name, label, f"{name}-test"
        self.answer, self.sources, self.calls = answer, sources, 0

    def ask(self, system: str, prompt: str) -> ProviderReply:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)
        return ProviderReply(text, self.sources)


SOURCE = (Source("BBC Sport", "https://www.bbc.com/sport/x"),)


# ------------------------------------------------------------------ parsing


def test_extract_json_tolerates_fences_and_text():
    assert extract_json('Bax:\n```json\n{"verdict": "neutral"}\n```') == {"verdict": "neutral"}
    with pytest.raises(ValueError):
        extract_json("cavab yoxdur")


def test_parse_review_clamps_and_drops_unsourced_news():
    provider = FakeProvider("gemini", "Gemini", ANSWER)
    review = parse_review(provider, json.dumps({**ANSWER, "adjustment_pp": -9}), (), max_pp=4)
    assert review.adjustment_pp == -4 and review.news == () and review.risks == ("Rotasiya ehtimalı",)
    sourced = parse_review(provider, json.dumps(ANSWER), SOURCE, max_pp=4)
    assert sourced.news and sourced.grounded
    with pytest.raises(LlmError):
        parse_review(provider, '{"verdict": "maybe"}', (), max_pp=4)


# ------------------------------------------------------------------ the brake rule


def _review(verdict: str, pp: float, veto: bool = False, sources: tuple[Source, ...] = ()) -> AiReview:
    return AiReview("p", "P", "m", verdict, pp, veto, "", sources=sources)


def test_only_decreases_are_applied_and_averaged():
    assert combine([_review("support", +4)], [], 4).applied_pp == 0  # the AI never raises a probability
    assert combine([_review("against", -4), _review("neutral", 0)], [], 4).applied_pp == -2
    assert combine([], ["Gemini"], 4).failed == ("Gemini",)


def test_veto_needs_sources_and_no_support():
    assert not combine([_review("against", -4, veto=True)], [], 4).veto  # unsourced
    assert combine([_review("against", -4, veto=True, sources=SOURCE)], [], 4).veto
    assert not combine([_review("against", -4, True, SOURCE), _review("support", 0)], [], 4).veto


def test_apply_verdict_recomputes_value_and_decision():
    evaluation = _evaluation()
    before = evaluation.best.candidate
    lowered = apply_verdict(evaluation, combine([_review("against", -4)], [], 4), AppConfig().selection)
    after = lowered.best.candidate
    assert after.p_final == pytest.approx(before.p_final - 0.04)
    assert after.ev == pytest.approx(after.p_final * after.odds - 1)
    vetoed = apply_verdict(evaluation, combine([_review("against", -1, True, SOURCE)], [], 4), AppConfig().selection)
    assert vetoed.decision == Decision.NO_BET and vetoed.best.not_bet[0] == "ai_veto"
    unchanged = apply_verdict(evaluation, combine([_review("support", 2)], [], 4), AppConfig().selection)
    assert unchanged is evaluation


# ------------------------------------------------------------------ service


def test_service_asks_every_reviewer_caches_and_survives_failures(db):
    gemini = FakeProvider("gemini", "Gemini", ANSWER, SOURCE)
    claude = FakeProvider("anthropic", "Claude", LlmError("anthropic", "HTTP 429", rate_limited=True))
    service = AiReviewService([gemini, claude], max_pp=4, cache=ResponseCache(db))
    evaluation = _evaluation()
    verdict = service.review_many([evaluation])[id(evaluation)]
    assert [r.label for r in verdict.reviews] == ["Gemini"] and verdict.failed == ("Claude",)
    assert verdict.applied_pp == -3
    service.review_many([evaluation])
    assert gemini.calls == 1  # second run answered from the cache
    assert claude.calls == 2  # failures are not cached


def test_prompt_contains_the_facts_and_rules():
    prompt = build_prompt(_evaluation())
    assert "Qarabağ vs Neftçi" in prompt and "Selection under review" in prompt and "Best odds" in prompt
    rules = system_prompt(4)
    assert "-4" in rules and "Azerbaijani" in rules and "Never invent" in rules


# ------------------------------------------------------------------ providers over HTTP


def _gemini_answer(text: str, chunks: list | None = None) -> httpx.Response:
    candidate = {"content": {"parts": [{"text": "düşünürəm", "thought": True}, {"text": text}]}}
    if chunks:
        candidate["groundingMetadata"] = {"groundingChunks": chunks}
    return httpx.Response(200, json={"candidates": [candidate]})


def test_gemini_researches_with_search_then_the_strongest_free_model_answers():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = request.url.path.rsplit("/", 1)[-1].split(":")[0]
        calls.append((model, "tools" in body, body["contents"][0]["parts"][0]["text"]))
        assert request.headers.get("x-goog-api-key") == "g-key"
        if "tools" in body:  # research step
            return _gemini_answer("- Home striker ruled out (injury).", [{"web": {"uri": "https://x/1", "title": "espn.com"}}])
        if model == "gemini-3.8-flash":
            return httpx.Response(503, text="high demand")  # busy: the next model is tried
        return _gemini_answer(json.dumps(ANSWER))

    provider = GeminiProvider(
        "g-key", ["gemini-3.8-flash", "gemini-3.6-flash"], search_model="gemini-2.5-flash",
        transport=httpx.MockTransport(handler),
    )
    reply = provider.ask("sys", "prompt")
    assert calls[0][:2] == ("gemini-2.5-flash", True)  # only the research step searches
    assert [c[:2] for c in calls[1:]] == [("gemini-3.8-flash", False), ("gemini-3.6-flash", False)]
    assert "Home striker ruled out" in calls[2][2]  # the analyst sees the sourced news
    assert json.loads(reply.text)["verdict"] == "against"  # thought parts are skipped
    assert reply.sources == (Source("espn.com", "https://x/1"),) and reply.model == "gemini-3.6-flash"


def test_gemini_without_news_has_no_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _gemini_answer("NO RELEVANT NEWS" if "tools" in body else json.dumps(ANSWER))

    reply = GeminiProvider("k", ["m1"], search_model="s", transport=httpx.MockTransport(handler)).ask("s", "p")
    assert reply.sources == ()  # → no news and no veto can pass


def test_gemini_quota_error_is_reported_as_rate_limit():
    provider = GeminiProvider("k", "m", transport=httpx.MockTransport(lambda r: httpx.Response(429, text="RESOURCE_EXHAUSTED")))
    with pytest.raises(LlmError) as error:
        provider.ask("s", "p")
    assert error.value.rate_limited


def test_claude_provider_continues_paused_turns_and_collects_citations():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["x-api-key"] == "c-key" and request.headers["anthropic-version"]
        if len(calls) == 1:
            return httpx.Response(200, json={"stop_reason": "pause_turn", "content": [
                {"type": "text", "text": "Axtarıram..."},
                {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "Qarabağ"}},
                {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [
                    {"type": "web_search_result", "url": "https://news.az/1", "title": "news.az"}]},
            ]})
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "Hücumçu zədəlidir.", "citations": [{"url": "https://news.az/1", "title": "news.az"}]},
            {"type": "text", "text": json.dumps(ANSWER)},
        ]})

    provider = ClaudeProvider("c-key", "claude-sonnet-5-5", transport=httpx.MockTransport(handler))
    reply = provider.ask("sys", "prompt")
    assert calls[0]["tools"][0]["name"] == "web_search" and calls[0]["model"] == "claude-sonnet-5-5"
    assert calls[1]["messages"][-1]["role"] == "assistant"  # the paused turn is continued
    assert json.loads(reply.text)["veto"] is False
    assert reply.sources == (Source("news.az", "https://news.az/1"),)


# ------------------------------------------------------------------ presentation


def test_match_detail_shows_the_ai_opinion():
    from tests.test_analysis import _pick

    view = _pick(1, "Qarabağ", "Neftçi")
    review = AiReview("gemini", "Gemini", "m", "against", -3, False, "Hücumçu zədəlidir.", ("Rotasiya",), ("Xəbər",), SOURCE)
    ai = {**combine([review], ["Claude"], 4).to_dict(), "p_before": 0.75, "decision_before": "bet", "rank_before": 1}
    from dataclasses import replace

    text = format_match_detail(replace(view, ai=ai)).render_plain()
    assert "AI RƏYİ" in text and "Gemini: əleyhinə — Hücumçu zədəlidir." in text
    assert "BBC Sport" in text and "Cavab vermədi: Claude" in text
    assert "AI düzəlişi: -3.0 f.b." in text


# ------------------------------------------------------------------ analysis integration


def _analyse_with(db, app_config, fake_api, monkeypatch, answer):
    from dataclasses import replace

    from sqlalchemy import select

    from backend.models import Bet, Prediction
    from backend.services import analysis as analysis_module
    from backend.services.analysis import AnalysisService
    from backend.services.ingestion import IngestionService
    from tests.conftest import make_client
    from tests.test_analysis import MORNING_UTC, TZ
    from tests.test_ingestion import DAY, Scenario

    Scenario(fake_api)
    IngestionService(db, make_client(fake_api, db=db), app_config, "Asia/Baku").run_daily(DAY)
    value = _evaluation()

    def evaluate(context, models, config):  # every match becomes the value pick, keeping its own id
        return replace(value, context=replace(value.context, match_id=context.match_id))

    monkeypatch.setattr(analysis_module, "evaluate_match", evaluate)
    reviewer = AiReviewService([FakeProvider("gemini", "Gemini", answer, SOURCE)], max_pp=4, max_reviews=5)
    report = AnalysisService(db, app_config, TZ, clock=lambda: MORNING_UTC, reviewer=reviewer).run_daily(DAY)
    with db.session() as s:
        predictions = list(s.scalars(select(Prediction).where(Prediction.run_date == DAY)))
        bets = s.query(Bet).count()
        return report, [(p.decision, p.rank, p.final_prob, p.llm_summary) for p in predictions], bets


def test_ai_veto_removes_picks_and_keeps_the_model_decision(db, app_config, fake_api, monkeypatch):
    report, rows, bets = _analyse_with(db, app_config, fake_api, monkeypatch, {**ANSWER, "veto": True})
    assert report.ai_reviewed == 3 and report.ai_vetoed == 3 and report.bets == 0
    for decision, rank, _, ai in rows:
        assert decision == Decision.NO_BET and rank is None
        assert ai["decision_before"] == "bet" and ai["veto"] and ai["reviews"][0]["label"] == "Gemini"
    assert bets == 0  # no paper bet for a blocked pick
    assert {ai["rank_before"] for *_, ai in rows} == {1, 2, 3}  # the model-only TOP 3, kept for comparison


def test_ai_lowers_the_probability_but_the_system_decides(db, app_config, fake_api, monkeypatch):
    report, rows, bets = _analyse_with(db, app_config, fake_api, monkeypatch, {**ANSWER, "adjustment_pp": -1})
    for decision, _, final_prob, ai in rows:
        assert final_prob == pytest.approx(ai["p_before"] - 0.01, abs=1e-4)
        assert decision in (Decision.BET, Decision.WATCH, Decision.NO_BET)
    assert report.ai_reviewed == 3 and report.ai_vetoed == 0
