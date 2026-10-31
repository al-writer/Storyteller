"""Рассылка: получатели, блокировки, повтор после лимита, отмена, API админки."""

from dataclasses import replace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app import db
from app.bot import broadcast
from app.main import app
from tests.test_admin import ADMIN_ID, TOKEN, init_data
from tests.test_bot import Chat_, FakeSession

pytestmark = pytest.mark.usefixtures("no_rate_limits")

BLOCKED_ID, SLOW_ID = 900001, 900002


class FlakySession(FakeSession):
    """BLOCKED_ID заблокировал бота; SLOW_ID в первый раз упирается в лимит."""

    def __init__(self) -> None:
        super().__init__()
        self.slowed = False

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.chat_id == BLOCKED_ID:
            raise TelegramForbiddenError(method, "bot was blocked by the user")
        if isinstance(method, SendMessage) and method.chat_id == SLOW_ID and not self.slowed:
            self.slowed = True
            raise TelegramRetryAfter(method, "Too Many Requests", retry_after=0)
        return await super().make_request(bot, method, timeout)


@pytest.fixture
def telegram(client, monkeypatch):
    monkeypatch.setattr(broadcast, "RATE", 10_000)
    chat = Chat_(client)
    chat.session = FlakySession()
    chat.telegram.bot.session = chat.session
    settings = app.state.settings
    app.state.settings = replace(
        settings, telegram_token=TOKEN, telegram_admin_ids=frozenset({ADMIN_ID})
    )
    app.state.telegram = chat.telegram

    async def seed():
        await app.state.db.execute("DELETE FROM tg_users")
        for n, tg_id in enumerate((ADMIN_ID, BLOCKED_ID, SLOW_ID, 900003)):
            user = await db.tg_user(app.state.db, f"h{n}", tg_id=tg_id)
            await db.save_tg_user(app.state.db, user)

    client.portal.call(seed)
    yield chat
    app.state.telegram = None
    app.state.settings = settings


def call(client, method, path, **kwargs):
    headers = {"X-Telegram-Init-Data": init_data(ADMIN_ID)}
    return client.request(method, path, headers=headers, **kwargs)


def test_broadcast_end_to_end(client, telegram):
    assert call(client, "GET", "/api/admin/broadcasts").json()["recipients"] == 4

    assert (
        call(client, "POST", "/api/admin/broadcast/test", json={"text": "Привет <b>"}).status_code
        == 200
    )
    test = telegram.session.of(SendMessage)[-1]
    assert (test.chat_id, test.text, test.parse_mode) == (ADMIN_ID, "Привет <b>", None)

    started = call(client, "POST", "/api/admin/broadcast", json={"text": "Новые пресеты!"})
    broadcast_id = started.json()["id"]
    client.portal.call(telegram.telegram.broadcaster.wait)

    info = call(client, "GET", "/api/admin/broadcasts").json()
    item = next(i for i in info["items"] if i["id"] == broadcast_id)
    assert (item["status"], item["total"], item["sent"], item["blocked"], item["failed"]) == (
        "done",
        4,
        3,
        1,
        0,
    )
    assert info["recipients"] == 3  # заблокировавший выпал
    assert not info["running"]


def test_blocked_user_returns_after_action(client, telegram):
    client.portal.call(db.mark_blocked, app.state.db, 900003)
    assert 900003 not in client.portal.call(db.broadcast_recipients, app.state.db)

    async def act():
        user = await db.tg_user(app.state.db, "h3")
        await db.save_tg_user(app.state.db, user)

    client.portal.call(act)
    assert 900003 in client.portal.call(db.broadcast_recipients, app.state.db)


def test_broadcast_validation_and_cancel(client, telegram):
    assert call(client, "POST", "/api/admin/broadcast", json={"text": ""}).status_code == 422
    assert (
        call(client, "POST", "/api/admin/broadcast", json={"text": "x" * 4097}).status_code == 422
    )
    assert client.post("/api/admin/broadcast", json={"text": "x"}).status_code == 403
    assert call(client, "POST", "/api/admin/broadcast/999/cancel").status_code == 409

    broadcaster = telegram.telegram.broadcaster

    async def start_and_cancel():
        broadcast_id = await broadcaster.start("стоп")
        assert broadcaster.cancel(broadcast_id)
        await broadcaster.wait()
        return broadcast_id

    broadcast_id = client.portal.call(start_and_cancel)
    item = next(
        i for i in client.portal.call(db.list_broadcasts, app.state.db) if i["id"] == broadcast_id
    )
    assert item["status"] == "cancelled"


def test_broadcast_without_bot(client):
    settings = app.state.settings
    app.state.settings = replace(
        settings, telegram_token=TOKEN, telegram_admin_ids=frozenset({ADMIN_ID})
    )
    try:
        assert call(client, "POST", "/api/admin/broadcast", json={"text": "x"}).status_code == 503
    finally:
        app.state.settings = settings


def test_interrupted_on_restart(client):
    async def scenario():
        broadcast_id = await db.create_broadcast(app.state.db, "оборвалась", 5)
        await db.interrupt_broadcasts(app.state.db)
        return next(i for i in await db.list_broadcasts(app.state.db) if i["id"] == broadcast_id)

    assert client.portal.call(scenario)["status"] == "interrupted"
