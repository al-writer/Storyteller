"""Telegram-бот: обновления прогоняются через диспетчер, ответы ловит фальшивая сессия."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    AnswerInlineQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    GetMe,
    SendMessage,
    SendPhoto,
)
from aiogram.types import Chat, Message, PhotoSize, User

from app import db
from app.bot import SECRET_HEADER, WEBHOOK_PATH, TelegramBot
from app.bot import callbacks as cb
from app.bot.render import phrase_html
from app.main import app
from app.schemas import PhraseOut, TokenOut

pytestmark = pytest.mark.usefixtures("no_rate_limits")

USER_ID = 100
ADMIN_ID = 438452289


class FakeSession(BaseSession):
    """Запоминает вызовы Bot API и отвечает правдоподобными объектами."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Бот", username="plotslot_bot")
        if isinstance(method, SendMessage | SendPhoto):
            photo = None
            if isinstance(method, SendPhoto):
                photo = [PhotoSize(file_id="photo-1", file_unique_id="u1", width=1, height=1)]
            return Message(
                message_id=next(self.ids),
                date=datetime.now(),
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None),
                photo=photo,
            )
        return True

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError
        yield b""

    def of(self, kind) -> list:
        return [call for call in self.calls if isinstance(call, kind)]


class Chat_:
    """Пользователь в личке с ботом."""

    def __init__(self, client, user_id: int = USER_ID) -> None:
        self.client = client
        self.user_id = user_id
        self.session = FakeSession()
        self.telegram = TelegramBot(app.state, "42:TEST", secret="s3cret", session=self.session)
        self.updates = itertools.count(1)

    def _user(self) -> dict:
        return {"id": self.user_id, "is_bot": False, "first_name": "Тест"}

    def _chat(self) -> dict:
        return {"id": self.user_id, "type": "private"}

    def feed(self, update: dict) -> None:
        update["update_id"] = next(self.updates)
        self.client.portal.call(self.telegram.dispatcher.feed_raw_update, self.telegram.bot, update)

    def send(self, text: str) -> None:
        message = {"message_id": 1, "date": 0, "chat": self._chat(), "from": self._user()}
        self.feed({"message": {**message, "text": text}})

    def press(self, data: str, message_id: int) -> None:
        message = {"message_id": message_id, "date": 0, "chat": self._chat(), "text": "…"}
        query = {"id": "q", "from": self._user(), "chat_instance": "c", "data": data}
        self.feed({"callback_query": {**query, "message": message}})

    def inline(self, query: str) -> AnswerInlineQuery:
        self.feed(
            {"inline_query": {"id": "iq", "from": self._user(), "query": query, "offset": ""}}
        )
        return self.session.of(AnswerInlineQuery)[-1]

    def last_phrase(self) -> tuple[SendMessage, int]:
        """Последнее сообщение с фразой и его message_id."""
        sent = [c for c in self.session.of(SendMessage) if c.reply_markup and _is_phrase(c)]
        call = sent[-1]
        index = [c for c in self.session.calls if isinstance(c, SendMessage | SendPhoto)].index(
            call
        )
        return call, 1000 + index


def _is_phrase(call) -> bool:
    rows = getattr(call.reply_markup, "inline_keyboard", None)
    return bool(rows) and rows[0][0].text == "🎰 Крутить"


def buttons(markup) -> dict[str, str]:
    return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


def answers(session: FakeSession) -> list[str | None]:
    return [call.text for call in session.of(AnswerCallbackQuery)]


@pytest.fixture
def chat(client):
    return Chat_(client)


def test_start_sends_greeting_and_phrase(chat):
    chat.send("/start")
    greeting, phrase = chat.session.of(SendMessage)
    assert "сюжеты" in greeting.text
    assert greeting.reply_markup.keyboard[0][0].text == "🎰 Крутить"
    assert set(buttons(phrase.reply_markup)) >= {"🎰 Крутить", "🔒 Слова", "✍️ Промпт"}
    assert phrase.text.startswith("<i>")


def test_spin_keeps_locks_and_collapses_previous(chat):
    chat.send("/start")
    first, first_id = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(first.reply_markup)["🎰 Крутить"]).slug

    chat.press(cb.Spin(slug=slug, mask=cb.to_mask([1])).pack(), first_id)
    second, _ = chat.last_phrase()
    assert second is not first
    collapse = chat.session.of(EditMessageReplyMarkup)[-1]
    assert collapse.message_id == first_id
    assert "♻️ За основу" in buttons(collapse.reply_markup)
    actor = "<b>" in second.text
    assert actor, "закреплённое слово выделено"


def test_lock_and_reroll_edit_in_place(chat):
    chat.send("/start")
    phrase, message_id = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug

    chat.press(cb.Lock(slug=slug, mask=0, i=1).pack(), message_id)
    edit = chat.session.of(EditMessageText)[-1]
    assert edit.text.count("<b>") == 1
    words = buttons(edit.reply_markup)
    assert sum(label.startswith("🔒") for label in words) == 1

    chat.press(cb.Reroll(slug=slug, mask=0, i=1).pack(), message_id)
    rerolled = chat.session.of(EditMessageText)[-1]
    new_slug = cb.Lock.unpack(next(iter(buttons(rerolled.reply_markup).values()))).slug
    assert new_slug != slug


def test_tone_and_wild_are_saved(chat):
    chat.send("/start")
    phrase, message_id = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug
    chat.press(cb.ChooseTone(slug=slug, mask=0, tone="dark").pack(), message_id)
    chat.press(cb.ToggleWild(slug=slug, mask=0).pack(), message_id)
    edit = chat.session.of(EditMessageText)[-1]
    assert "Мрачный" in edit.text
    assert "🌀 Дикий словарь: вкл" in buttons(edit.reply_markup)
    assert "✓ Мрачный" in buttons(edit.reply_markup)


def test_like_once(chat):
    chat.send("/start")
    phrase, message_id = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug
    like = cb.Like(slug=slug, mask=0, view="m").pack()
    chat.press(like, message_id)
    chat.press(like, message_id)
    assert answers(chat.session)[-2:] == ["Фраза в зале славы ❤️", "Вы уже голосовали ❤️"]
    assert "❤️ 1" in buttons(chat.session.of(EditMessageReplyMarkup)[-1].reply_markup)


def test_prompt_and_card(chat):
    chat.send("/start")
    phrase, message_id = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug

    chat.press(cb.Prompt(slug=slug, kind="story").pack(), message_id)
    prompt = chat.session.of(SendMessage)[-1]
    assert prompt.text.startswith("<pre>Напиши завязку")

    chat.press(cb.Card(slug=slug).pack(), message_id)
    chat.press(cb.Card(slug=slug).pack(), message_id)
    first, second = chat.session.of(SendPhoto)
    assert not isinstance(first.photo, str)
    assert second.photo == "photo-1"


def test_unknown_phrase(chat):
    chat.press(cb.Spin(slug="unknown1", mask=0).pack(), 5)
    assert answers(chat.session) == ["Фраза не найдена"]


def test_start_with_slug_locks_everything(chat):
    chat.send("/start")
    phrase, _ = chat.last_phrase()
    slug = cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug
    chat.send(f"/start {slug}")
    shared, _ = chat.last_phrase()
    assert cb.Spin.unpack(buttons(shared.reply_markup)["🎰 Крутить"]).slug == slug
    assert shared.text.count("<b>") >= 4


def test_stats_only_for_admin(client):
    settings = app.state.settings
    app.state.settings = replace(settings, telegram_admin_ids=frozenset({ADMIN_ID}))
    try:
        admin = Chat_(client, ADMIN_ID)
        admin.send("/stats")
        assert admin.session.of(SendMessage)[-1].text.startswith("<b>📊 Статистика")
        stranger = Chat_(client, USER_ID + 1)
        stranger.send("/stats")
        assert _is_phrase(stranger.session.of(SendMessage)[-1])
    finally:
        app.state.settings = settings


def test_stats_counts(client):
    data = client.portal.call(db.stats, app.state.db)
    assert data["phrases"] >= 0
    assert set(data) >= {"phrases_day", "likes", "voters", "tg_users", "tg_week", "top"}


def test_webhook_checks_secret(client, chat):
    app.state.telegram = chat.telegram
    try:
        update = {"update_id": 1, "message": {
            "message_id": 1, "date": 0, "chat": {"id": USER_ID, "type": "private"},
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Т"}, "text": "/help",
        }}  # fmt: skip
        assert client.post(WEBHOOK_PATH, json=update).status_code == 403
        bad = client.post(WEBHOOK_PATH, json=update, headers={SECRET_HEADER: "nope"})
        assert bad.status_code == 403
        good = client.post(WEBHOOK_PATH, json=update, headers={SECRET_HEADER: "s3cret"})
        assert good.status_code == 200
        assert "Крутить" in chat.session.of(SendMessage)[-1].text
    finally:
        app.state.telegram = None


def test_callback_data_fits_telegram_limit():
    slug = "Z" * 16
    longest = [
        cb.Reroll(slug=slug, mask=255, i=7),
        cb.ChooseTone(slug=slug, mask=255, tone="fairy"),
        cb.ChoosePreset(slug=slug, preset="conflict"),
        cb.Like(slug=slug, mask=255, view="m"),
        cb.Prompt(slug=slug, kind="story"),
    ]
    assert all(len(data.pack().encode()) <= 64 for data in longest)
    assert cb.from_mask(cb.to_mask([0, 2, 7])) == [0, 2, 7]
    assert cb.from_mask(0) == []


def test_phrase_html_bolds_locked_and_escapes():
    phrase = PhraseOut(
        slug="a", url="", preset_id="plot", preset_title="Сюжет", text="", tone=None,
        card_tone=None, slots=[], prompt={}, likes=0,
        tokens=[TokenOut(text="Кот", slot=0), TokenOut(text="<ест>", slot=1),
                TokenOut(text="в доме", slot=2), TokenOut(text=".", slot=None)],
    )  # fmt: skip
    assert phrase_html(phrase, [0, 2]) == "<b>Кот</b> &lt;ест&gt; <b>в доме</b>."


def test_bot_needs_https(client):
    from app.main import start_telegram

    settings = app.state.settings
    app.state.settings = replace(settings, telegram_token="42:TEST", base_url="http://localhost")
    try:
        assert client.portal.call(start_telegram, app.state) is None
    finally:
        app.state.settings = settings


def slug_of(phrase: SendMessage) -> str:
    return cb.Spin.unpack(buttons(phrase.reply_markup)["🎰 Крутить"]).slug


def test_share_button_opens_inline_query(chat):
    chat.send("/start")
    phrase, _ = chat.last_phrase()
    share = next(
        b for row in phrase.reply_markup.inline_keyboard for b in row if b.text == "↗️ Поделиться"
    )
    assert share.switch_inline_query_chosen_chat.query == slug_of(phrase)


def test_inline_by_slug_and_fresh(chat):
    chat.send("/start")
    phrase, _ = chat.last_phrase()
    slug = slug_of(phrase)

    answer = chat.inline(slug)
    (result,) = answer.results
    assert result.id == slug
    assert not answer.is_personal
    assert result.input_message_content.link_preview_options.url.endswith(f"/s/{slug}")
    deep_link = result.reply_markup.inline_keyboard[0][0].url
    assert deep_link == f"https://t.me/plotslot_bot?start={slug}"

    fresh = chat.inline("")
    assert fresh.is_personal and fresh.cache_time == 0
    assert 1 <= len(fresh.results) <= 5
    assert len({r.id for r in fresh.results}) == len(fresh.results)

    mystery = chat.inline("тай")
    assert {r.title for r in mystery.results} == {"Тайна"}


def test_inline_survives_rate_limit(chat):
    app.state.generate_limiter.limit = 2
    app.state.generate_limiter._hits.clear()
    answer = chat.inline("")
    assert len(answer.results) == 2


def test_feed_carousel(chat):
    for _ in range(2):
        chat.send("🎰 Крутить")
        phrase, message_id = chat.last_phrase()
        chat.press(cb.Like(slug=slug_of(phrase), mask=0, view="m").pack(), message_id)

    chat.send("/new")
    screen = chat.session.of(SendMessage)[-1]
    assert screen.text.startswith("<b>📡 Прямой эфир</b> · 1 из ")
    labels = buttons(screen.reply_markup)
    assert "◀" not in labels and "▶" in labels and "🏆 Топ" in labels

    chat.press(labels["▶"], 777)
    page = chat.session.of(EditMessageText)[-1]
    assert " · 2 из " in page.text
    assert "◀" in buttons(page.reply_markup)

    chat.send("🏆 Зал славы")
    top = chat.session.of(SendMessage)[-1]
    assert top.text.startswith("<b>🏆 Зал славы</b> · 1 из ")
    like = next(label for label in buttons(top.reply_markup) if label.startswith("❤️"))
    chat.press(buttons(top.reply_markup)[like], 778)
    assert answers(chat.session)[-1] in ("Вы уже голосовали ❤️", "Ваш голос учтён ❤️")
    assert chat.session.of(EditMessageText)[-1].text.startswith("<b>🏆 Зал славы</b>")


def test_feed_at(client):
    item, total = client.portal.call(db.feed_at, app.state.db, "new", 0)
    assert total >= 1 and item is not None
    assert client.portal.call(db.feed_at, app.state.db, "new", total) == (None, total)
    top_item, top_total = client.portal.call(db.feed_at, app.state.db, "top", 0)
    assert top_item["likes"] > 0 and top_total >= 1
