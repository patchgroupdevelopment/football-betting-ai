"""Bot commands (shared logic) and the one-shot poller used on GitHub Actions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from backend.config import Settings
from backend.container import build_container
from backend.presenters.messages import MessageBuilder
from backend.scheduler.pipeline import PipelineRunner
from backend.telegram.commands import Incoming, parse_command, respond
from backend.telegram.poller import poll_once

CHAT = 1018324710


def _container(db, app_config, chat_ids: str = str(CHAT)):
    settings = Settings(_env_file=None, database_url="sqlite://", telegram_chat_id=chat_ids)
    return build_container(settings, app_config, db=db)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/bugun", ("bugun", [])),
        ("/oyun_12", ("oyun", ["12"])),
        ("/oyun_12@futbol_piramida_bot", ("oyun", ["12"])),
        ("/oyun 7", ("oyun", ["7"])),
        ("/SECIMLER", ("secimler", [])),
        ("salam", ("", ["salam"])),
        ("", ("", [])),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text) == expected


def _reply(container, text: str, chat: int = CHAT) -> str:
    runner = PipelineRunner(container)
    return asyncio.run(respond(container, runner, Incoming(chat, "Fuad", text))).render_plain()


def test_commands_reply_in_azerbaijani(db, app_config):
    container = _container(db, app_config)
    assert "Əmrlər:" in _reply(container, "/start")
    assert "GÜNÜN OYUNLARI" in _reply(container, "/bugun")
    assert "GÜNÜN FUTBOL ANALİZİ" in _reply(container, "/secimler")
    assert "PİRAMİDA" in _reply(container, "/piramida")
    assert "Növbəti planlı yükləmə" in _reply(container, "/status") and "planlaşdırılmayıb" not in _reply(container, "/status")
    assert _reply(container, "/oyun_999") == "Bu ID ilə analiz edilmiş oyun tapılmadı."
    assert "tanımıram" in _reply(container, "/abc")


def test_unknown_chat_is_refused(db, app_config):
    assert _reply(_container(db, app_config), "/bugun", chat=555) == "⛔ Bu bot şəxsi istifadə üçündür."


def test_setup_mode_reveals_chat_id(db, app_config):
    reply = _reply(_container(db, app_config, chat_ids=""), "/start", chat=4242)
    assert "Quraşdırma rejimi" in reply and "4242" in reply


# ------------------------------------------------------------------ poller


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeMessage:
    text: str


@dataclass
class FakeUser:
    full_name: str = "Fuad"


@dataclass
class FakeUpdate:
    update_id: int
    effective_chat: FakeChat
    effective_message: FakeMessage
    effective_user: FakeUser = field(default_factory=FakeUser)


class FakeBot:
    def __init__(self, texts: list[str]) -> None:
        self.pending = [FakeUpdate(100 + i, FakeChat(CHAT), FakeMessage(text)) for i, text in enumerate(texts)]
        self.confirmed_offset: int | None = None

    async def get_updates(self, offset: int | None = None, timeout: int = 0, allowed_updates=None):
        if offset is not None:
            self.confirmed_offset = offset
            self.pending = [u for u in self.pending if u.update_id >= offset]
            return []
        return list(self.pending)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, message: MessageBuilder) -> bool:
        self.sent.append((CHAT, message.render_plain()))
        return True

    async def send_to(self, chat_id: int, message: MessageBuilder) -> bool:
        self.sent.append((chat_id, message.render_plain()))
        return True


def test_poller_answers_and_confirms(db, app_config):
    container = _container(db, app_config)
    bot, notifier = FakeBot(["/bugun", "/piramida"]), RecordingNotifier()
    handled = asyncio.run(poll_once(container, PipelineRunner(container, notifier), bot, notifier))
    assert handled == 2 and bot.confirmed_offset == 102 and not bot.pending
    assert "GÜNÜN OYUNLARI" in notifier.sent[0][1] and "PİRAMİDA" in notifier.sent[1][1]


def test_poller_with_nothing_pending(db, app_config):
    container = _container(db, app_config)
    bot, notifier = FakeBot([]), RecordingNotifier()
    assert asyncio.run(poll_once(container, PipelineRunner(container, notifier), bot, notifier)) == 0
    assert bot.confirmed_offset is None and not notifier.sent


def test_poller_refresh_runs_the_pipeline(db, app_config):
    container = _container(db, app_config)  # no FOOTBALL_API_KEY: the pipeline reports it
    bot, notifier = FakeBot(["/yenile"]), RecordingNotifier()
    asyncio.run(poll_once(container, PipelineRunner(container, notifier), bot, notifier))
    texts = [text for _, text in notifier.sent]
    assert texts[0].startswith("🔄 Məlumatlar yüklənir") and "FOOTBALL_API_KEY" in texts[1]
