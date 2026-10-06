"""A small message builder rendering to Telegram HTML or plain text (CLI).

Text is escaped at render time, so team names such as "Brighton & Hove" can
never break Telegram's HTML parser.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Literal

DIVIDER = "━━━━━━━━━━━━━━"
TELEGRAM_LIMIT = 4096


@dataclass(frozen=True)
class Span:
    text: str
    style: Literal["plain", "bold", "code"] = "plain"


def bold(text: object) -> Span:
    return Span(str(text), "bold")


def code(text: object) -> Span:
    return Span(str(text), "code")


def _html(span: Span) -> str:
    escaped = html.escape(span.text, quote=False)
    if span.style == "bold":
        return f"<b>{escaped}</b>"
    if span.style == "code":
        return f"<code>{escaped}</code>"
    return escaped


class MessageBuilder:
    def __init__(self) -> None:
        self._lines: list[tuple[Span, ...]] = []

    def line(self, *parts: str | Span) -> MessageBuilder:
        self._lines.append(tuple(p if isinstance(p, Span) else Span(str(p)) for p in parts))
        return self

    def blank(self) -> MessageBuilder:
        self._lines.append(())
        return self

    def section(self) -> MessageBuilder:
        """Blank line, divider, blank line — the visual break used between report sections."""
        return self.blank().line(DIVIDER).blank()

    def render_html(self) -> str:
        return "\n".join("".join(_html(span) for span in line) for line in self._lines)

    def render_plain(self) -> str:
        return "\n".join("".join(span.text for span in line) for line in self._lines)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph boundaries (then lines) so each chunk fits Telegram's limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""

    def push(piece: str, separator: str) -> None:
        nonlocal current
        candidate = f"{current}{separator}{piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
            return
        if current:
            chunks.append(current)
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        current = piece

    for paragraph in text.split("\n\n"):
        if len(paragraph) <= limit:
            push(paragraph, "\n\n")
        else:
            for index, line in enumerate(paragraph.split("\n")):
                push(line, "\n\n" if index == 0 else "\n")
    if current:
        chunks.append(current)
    return chunks
