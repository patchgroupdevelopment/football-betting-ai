"""AI providers that review a pick: Google Gemini (free tier) and Anthropic Claude.

Both are called over plain HTTPS (httpx) with web search switched on, so the reviewer can read the
latest team news. Each returns the reply text plus the web sources it actually used; a provider error
never breaks the analysis — the caller simply goes on without that review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

MAX_SOURCES = 5


class LlmError(Exception):
    """A provider failed (network, quota, bad answer). ``detail`` is for the log only."""

    def __init__(self, provider: str, detail: str, *, rate_limited: bool = False) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail
        self.rate_limited = rate_limited


@dataclass(frozen=True)
class Source:
    title: str
    url: str


@dataclass(frozen=True)
class ProviderReply:
    text: str
    sources: tuple[Source, ...] = ()


class Provider(Protocol):
    name: str  # "gemini" | "anthropic"
    label: str  # shown to the user
    model: str

    def ask(self, system: str, prompt: str) -> ProviderReply: ...


def _unique(sources: list[Source]) -> tuple[Source, ...]:
    seen: set[str] = set()
    result: list[Source] = []
    for source in sources:
        if source.url and source.url not in seen:
            seen.add(source.url)
            result.append(source)
    return tuple(result[:MAX_SOURCES])


def _raise_for_status(provider: str, response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text[:300]
    limited = response.status_code in (429, 529) or "RESOURCE_EXHAUSTED" in detail
    raise LlmError(provider, f"HTTP {response.status_code}: {detail}", rate_limited=limited)


class GeminiProvider:
    name = "gemini"
    label = "Gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(
        self, api_key: str, model: str, *, web_search: bool = True, timeout: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._key = api_key
        self._web_search = web_search
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def ask(self, system: str, prompt: str) -> ProviderReply:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
        }
        if self._web_search:
            body["tools"] = [{"google_search": {}}]
        try:
            response = self._http.post(self.URL.format(model=self.model), headers={"x-goog-api-key": self._key}, json=body)
        except httpx.HTTPError as exc:
            raise LlmError(self.name, f"network: {exc}") from exc
        _raise_for_status(self.name, response)
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LlmError(self.name, f"no answer: {str(data.get('promptFeedback'))[:200]}")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
        sources = [Source(c["web"].get("title", ""), c["web"].get("uri", "")) for c in chunks if c.get("web")]
        return ProviderReply(text, _unique(sources))

    def close(self) -> None:
        self._http.close()


class ClaudeProvider:
    name = "anthropic"
    label = "Claude"
    URL = "https://api.anthropic.com/v1/messages"
    MAX_CONTINUATIONS = 2  # a long web search can pause the turn ("pause_turn")

    def __init__(
        self, api_key: str, model: str, *, web_search: bool = True, max_searches: int = 3, timeout: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._key = api_key
        self._web_search = web_search
        self._max_searches = max_searches
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            response = self._http.post(self.URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LlmError(self.name, f"network: {exc}") from exc
        _raise_for_status(self.name, response)
        return response.json()

    def ask(self, system: str, prompt: str) -> ProviderReply:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {"model": self.model, "max_tokens": 2000, "system": system, "messages": messages}
        if self._web_search:
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": self._max_searches}]

        blocks: list[dict[str, Any]] = []
        for _ in range(self.MAX_CONTINUATIONS + 1):
            data = self._post(body)
            content = data.get("content") or []
            blocks.extend(content)
            if data.get("stop_reason") != "pause_turn":
                break
            messages.append({"role": "assistant", "content": content})

        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        cited = [
            Source(c.get("title", ""), c.get("url", ""))
            for b in blocks
            if b.get("type") == "text"
            for c in (b.get("citations") or [])
            if c.get("url")
        ]
        found = [
            Source(r.get("title", ""), r.get("url", ""))
            for b in blocks
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list)
            for r in b["content"]
            if r.get("type") == "web_search_result"
        ]
        # The last text block holds the JSON answer; earlier ones are thinking aloud between searches.
        text = texts[-1] if texts else ""
        if "{" not in text:
            text = "\n".join(texts)
        return ProviderReply(text, _unique(cited or found))

    def close(self) -> None:
        self._http.close()
