"""Команды и нажатия кнопок бота.

В обработчики приходят ``app_state`` (``app.state`` веб-приложения) и ``runtime``
(``TelegramBot``) — их кладёт диспетчер.
"""

from __future__ import annotations

import contextlib
import html
import logging
from typing import TYPE_CHECKING, Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, ExceptionTypeFilter
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
    User,
)

from app import db, service
from app.bot import callbacks as cb
from app.bot import keyboards as kb
from app.bot.render import TONE_TITLES, feed_html, message_html, shared_html
from app.presets import DEFAULT_PRESET, PRESETS
from app.schemas import GenerateIn, PhraseOut, SlotIn, SlotOut
from app.service import Client
from app.typo import typograph

if TYPE_CHECKING:
    from app.bot import TelegramBot

log = logging.getLogger(__name__)

RECENT_LIMIT = 40
INLINE_FRESH = 5
FEED_SORTS = {"t": "top", "n": "new"}

GREETING = (
    "Я собираю сюжеты из случайных слов — грамматически безупречно.\n"
    "Крутите, закрепляйте удачные слова, меняйте остальные."
)
HELP = (
    "<b>🎰 Крутить</b> — новая фраза, закреплённые слова остаются.\n"
    "<b>🔒 Слова</b> — закрепить слово или сменить только его (🔄).\n"
    "<b>📚 Пресет</b> — формула фразы: сюжет, конфликт, тайна…\n"
    "<b>🎭 Тон</b> — настроение слов и «Дикий словарь» с редкими словами.\n"
    "<b>✍️ Промпт</b>, <b>🎨 Художнику</b> — фраза как задание для нейросети.\n"
    "<b>🖼 Открытка</b> — картинка с фразой.\n"
    "<b>❤️</b> — отправить фразу в зал славы.\n"
    "<b>↗️ Поделиться</b> — отправить фразу в любой чат.\n\n"
    "<b>/top</b> — зал славы, <b>/new</b> — прямой эфир.\n"
    "В любом чате наберите @имя_бота — и выберите сюжет из свежих.\n"
    "Любое другое сообщение крутит новую фразу."
)
ERROR_TEXT: dict[type[service.ServiceError], str] = {
    service.TooFast: "Не так быстро 🙂",
    service.NotFound: "Фраза не найдена",
    service.Gone: "Слов этой фразы больше нет в словаре — соберите новую",
    service.Failed: "Не получилось собрать фразу",
}


# --- общие шаги -----------------------------------------------------------------------------


def is_admin(app_state: Any, user: User) -> bool:
    return user.id in app_state.settings.telegram_admin_ids


def admin_url(app_state: Any) -> str | None:
    """Адрес админки (Mini App): Telegram открывает их только по HTTPS."""
    base_url = app_state.settings.base_url
    return f"{base_url}/admin" if base_url.startswith("https://") else None


def user_keyboard(app_state: Any, user: User) -> ReplyKeyboardMarkup:
    return kb.reply_keyboard(admin=is_admin(app_state, user))


def client_of(app_state: Any, user: User) -> Client:
    return Client.from_source(app_state.settings.secret_salt, f"tg:{user.id}")


async def make(
    app_state: Any,
    who: Client,
    user: dict,
    preset_id: str,
    slots: list[SlotOut],
    mask: int,
    reroll: int | None = None,
) -> PhraseOut:
    """Новая фраза с настройками пользователя; запоминает свежие слова в ``recent``."""
    locked = cb.from_mask(mask)
    phrase = await service.generate(
        app_state,
        who,
        GenerateIn(
            preset_id=preset_id,
            slots=[
                SlotIn(id=s.id, number=s.number, gender=s.gender, superlative=s.superlative)
                for s in slots
            ],
            locked_indices=locked,
            reroll_index=reroll,
            recent_ids=user["recent"],
            tone=user["tone"],
            wild=user["wild"],
        ),
    )
    keep = locked if reroll is None else [s.index for s in phrase.slots if s.index != reroll]
    fresh = [s.id for s in phrase.slots if s.index not in keep]
    user["recent"] = [*user["recent"], *fresh][-RECENT_LIMIT:]
    return phrase


def fit_mask(phrase: PhraseOut, mask: int) -> int:
    return mask & ((1 << len(phrase.slots)) - 1)


def all_locked(phrase: PhraseOut) -> int:
    return cb.to_mask(slot.index for slot in phrase.slots)


async def send_phrase(
    bot: Bot, chat_id: int, app_state: Any, user: dict, phrase: PhraseOut, mask: int
) -> None:
    """Новое сообщение с фразой; у предыдущего клавиатура сворачивается."""
    message = await bot.send_message(
        chat_id,
        message_html(phrase, cb.from_mask(mask), user["tone"], user["wild"]),
        reply_markup=kb.main(phrase, mask),
    )
    if user["last_message_id"] and user["last_slug"]:
        # Сообщение могли удалить, или оно слишком старое для правки.
        with contextlib.suppress(TelegramBadRequest):
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=user["last_message_id"],
                reply_markup=kb.collapsed(user["last_slug"]),
            )
    user.update(
        last_message_id=message.message_id,
        last_slug=phrase.slug,
        last_mask=mask,
        preset_id=phrase.preset_id,
    )
    await db.save_tg_user(app_state.db, user)


async def edit_phrase(
    message: Message, app_state: Any, user: dict, phrase: PhraseOut, mask: int, view: cb.View
) -> None:
    """Правка сообщения на месте: другая фраза, закрепления или экран."""
    try:
        await message.edit_text(
            message_html(phrase, cb.from_mask(mask), user["tone"], user["wild"]),
            reply_markup=kb.for_view(view, phrase, mask, user["tone"], user["wild"]),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
    if message.message_id == user["last_message_id"]:
        user.update(last_slug=phrase.slug, last_mask=mask)
    await db.save_tg_user(app_state.db, user)


async def context(query: CallbackQuery, app_state: Any) -> tuple[Client, dict, Message]:
    who = client_of(app_state, query.from_user)
    user = await db.tg_user(app_state.db, who.voter_hash, query.from_user.id)
    if not isinstance(query.message, Message):
        raise service.NotFound  # сообщение недоступно (слишком старое)
    return who, user, query.message


async def spin_fresh(message: Message, app_state: Any) -> None:
    """Спин из текста: от последней фразы с её закреплениями, иначе с нуля."""
    who = client_of(app_state, message.from_user)
    user = await db.tg_user(app_state.db, who.voter_hash, message.from_user.id)
    base, mask = None, 0
    if user["last_slug"]:
        try:
            base = await service.get_phrase(app_state, user["last_slug"])
            mask = fit_mask(base, user["last_mask"])
        except (service.NotFound, service.Gone):
            base = None
    if base is not None:
        phrase = await make(app_state, who, user, base.preset_id, base.slots, mask)
    else:
        preset_id = user["preset_id"] if user["preset_id"] in PRESETS else DEFAULT_PRESET
        phrase = await make(app_state, who, user, preset_id, [], 0)
    await send_phrase(message.bot, message.chat.id, app_state, user, phrase, mask)


# --- команды --------------------------------------------------------------------------------


async def start(message: Message, command: CommandObject, app_state: Any) -> None:
    who = client_of(app_state, message.from_user)
    user = await db.tg_user(app_state.db, who.voter_hash, message.from_user.id)
    await service.record(app_state, who, "start")
    keyboard = user_keyboard(app_state, message.from_user)
    await message.answer(GREETING, reply_markup=keyboard)
    phrase, mask = None, 0
    if command.args and service.valid_slug(command.args):
        try:
            phrase = await service.get_phrase(app_state, command.args)
            mask = all_locked(phrase)
        except (service.NotFound, service.Gone):
            await message.answer("Эта фраза больше недоступна — соберём новую.")
    if phrase is None:
        preset_id = user["preset_id"] if user["preset_id"] in PRESETS else DEFAULT_PRESET
        phrase = await make(app_state, who, user, preset_id, [], 0)
    await send_phrase(message.bot, message.chat.id, app_state, user, phrase, mask)


async def help_(message: Message, app_state: Any) -> None:
    await message.answer(HELP, reply_markup=user_keyboard(app_state, message.from_user))


async def stats(message: Message, app_state: Any) -> None:
    if not is_admin(app_state, message.from_user):
        await spin_fresh(message, app_state)  # для остальных команды нет: как любой текст
        return
    data = await db.stats(app_state.db)
    lines = [
        "<b>📊 Статистика</b>",
        "",
        f"Фраз собрано: {data['phrases']} (за сутки {data['phrases_day']})",
        f"В зале славы: {data['saved']}",
        f"Лайков: {data['likes']} (за сутки {data['likes_day']}), голосовавших: {data['voters']}",
        f"Пользователей бота: {data['tg_users']} "
        f"(за сутки {data['tg_day']}, за неделю {data['tg_week']})",
    ]
    if data["top"]:
        lines += ["", "<b>Топ зала славы</b>"]
        lines += [f"❤️ {likes} — {html.escape(text)}" for text, likes in data["top"]]
    url = admin_url(app_state)
    await message.answer("\n".join(lines), reply_markup=kb.admin_app(url) if url else None)


async def any_text(message: Message, app_state: Any) -> None:
    await spin_fresh(message, app_state)


# --- кнопки ---------------------------------------------------------------------------------


async def on_spin(query: CallbackQuery, callback_data: cb.Spin, app_state: Any) -> None:
    who, user, message = await context(query, app_state)
    base = await service.get_phrase(app_state, callback_data.slug)
    mask = fit_mask(base, callback_data.mask)
    phrase = await make(app_state, who, user, base.preset_id, base.slots, mask)
    await send_phrase(message.bot, message.chat.id, app_state, user, phrase, mask)
    await query.answer()


async def on_reroll(query: CallbackQuery, callback_data: cb.Reroll, app_state: Any) -> None:
    who, user, message = await context(query, app_state)
    base = await service.get_phrase(app_state, callback_data.slug)
    mask = fit_mask(base, callback_data.mask) & ~(1 << callback_data.i)
    phrase = await make(
        app_state, who, user, base.preset_id, base.slots, mask, reroll=callback_data.i
    )
    await edit_phrase(message, app_state, user, phrase, mask, "w")
    await query.answer()


async def on_lock(query: CallbackQuery, callback_data: cb.Lock, app_state: Any) -> None:
    _, user, message = await context(query, app_state)
    phrase = await service.get_phrase(app_state, callback_data.slug)
    mask = fit_mask(phrase, callback_data.mask ^ (1 << callback_data.i))
    await edit_phrase(message, app_state, user, phrase, mask, "w")
    await query.answer()


async def on_show(query: CallbackQuery, callback_data: cb.Show, app_state: Any) -> None:
    _, user, message = await context(query, app_state)
    phrase = await service.get_phrase(app_state, callback_data.slug)
    mask = fit_mask(phrase, callback_data.mask)
    await edit_phrase(message, app_state, user, phrase, mask, callback_data.view)
    await query.answer()


async def on_preset(query: CallbackQuery, callback_data: cb.ChoosePreset, app_state: Any) -> None:
    who, user, message = await context(query, app_state)
    if callback_data.preset not in PRESETS:
        raise service.NotFound
    phrase = await make(app_state, who, user, callback_data.preset, [], 0)
    await send_phrase(message.bot, message.chat.id, app_state, user, phrase, 0)
    await query.answer(PRESETS[callback_data.preset].title)


async def on_tone(query: CallbackQuery, callback_data: cb.ChooseTone, app_state: Any) -> None:
    _, user, message = await context(query, app_state)
    if callback_data.tone in TONE_TITLES:
        user["tone"] = callback_data.tone
    phrase = await service.get_phrase(app_state, callback_data.slug)
    await edit_phrase(message, app_state, user, phrase, fit_mask(phrase, callback_data.mask), "t")
    await query.answer("Тон — со следующей фразы")


async def on_wild(query: CallbackQuery, callback_data: cb.ToggleWild, app_state: Any) -> None:
    _, user, message = await context(query, app_state)
    user["wild"] = not user["wild"]
    phrase = await service.get_phrase(app_state, callback_data.slug)
    await edit_phrase(message, app_state, user, phrase, fit_mask(phrase, callback_data.mask), "t")
    await query.answer("Дикий словарь включён 🌀" if user["wild"] else "Дикий словарь выключен")


async def on_like(query: CallbackQuery, callback_data: cb.Like, app_state: Any) -> None:
    who, _, message = await context(query, app_state)
    result = await service.like(app_state, who, callback_data.slug)
    if callback_data.view == "c":
        markup = kb.collapsed(callback_data.slug, result.likes)
    else:
        phrase = await service.get_phrase(app_state, callback_data.slug)
        markup = kb.main(phrase, fit_mask(phrase, callback_data.mask))
    with contextlib.suppress(TelegramBadRequest):  # счётчик не изменился
        await message.edit_reply_markup(reply_markup=markup)
    await query.answer("Фраза в зале славы ❤️" if result.counted else "Вы уже голосовали ❤️")


async def on_prompt(query: CallbackQuery, callback_data: cb.Prompt, app_state: Any) -> None:
    who, _, message = await context(query, app_state)
    phrase = await service.get_phrase(app_state, callback_data.slug)
    text = phrase.prompt.get(callback_data.kind)
    if text is None:
        raise service.NotFound
    await service.record(app_state, who, "prompt", phrase.preset_id)
    await message.answer(
        f"<pre>{html.escape(text, quote=False)}</pre>", reply_markup=kb.copy_prompt(text)
    )
    await query.answer()


async def on_card(
    query: CallbackQuery, callback_data: cb.Card, app_state: Any, runtime: TelegramBot
) -> None:
    who, _, message = await context(query, app_state)
    phrase = await service.get_phrase(app_state, callback_data.slug)
    await query.answer()  # открытка может рисоваться пару секунд
    await service.record(app_state, who, "card", phrase.preset_id)
    cached = runtime.card_ids.get(phrase.slug)
    mark = f"@{runtime.username}" if runtime.username else None
    photo = cached or FSInputFile(
        await service.card_path(app_state, phrase.slug, "square", mark),
        filename=f"{phrase.slug}.png",
    )
    sent = await message.answer_photo(
        photo, caption=html.escape(typograph(phrase.text), quote=False)
    )
    if sent.photo:
        runtime.card_ids[phrase.slug] = sent.photo[-1].file_id


async def on_base(query: CallbackQuery, callback_data: cb.Base, app_state: Any) -> None:
    _, user, message = await context(query, app_state)
    phrase = await service.get_phrase(app_state, callback_data.slug)
    await send_phrase(message.bot, message.chat.id, app_state, user, phrase, all_locked(phrase))
    await query.answer("Все слова закреплены")


# --- зал славы -----------------------------------------------------------------------------


async def feed_screen(app_state: Any, sort: str, offset: int):
    item, total = await service.feed_at(app_state, FEED_SORTS[sort], offset)
    if item is None and total:  # лента укоротилась, пока её листали
        offset = total - 1
        item, total = await service.feed_at(app_state, FEED_SORTS[sort], offset)
    text = feed_html(item, sort, offset, total)
    markup = kb.feed(item, sort, offset, total) if item else kb.feed_empty(sort)
    return text, markup


async def top(message: Message, app_state: Any) -> None:
    text, markup = await feed_screen(app_state, "t", 0)
    await message.answer(text, reply_markup=markup)


async def new(message: Message, app_state: Any) -> None:
    text, markup = await feed_screen(app_state, "n", 0)
    await message.answer(text, reply_markup=markup)


async def edit_feed(message: Message, app_state: Any, sort: str, offset: int) -> None:
    text, markup = await feed_screen(app_state, sort, offset)
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def on_feed(query: CallbackQuery, callback_data: cb.Feed, app_state: Any) -> None:
    _, _, message = await context(query, app_state)
    await edit_feed(message, app_state, callback_data.sort, callback_data.offset)
    await query.answer()


async def on_feed_like(query: CallbackQuery, callback_data: cb.FeedLike, app_state: Any) -> None:
    who, _, message = await context(query, app_state)
    result = await service.like(app_state, who, callback_data.slug)
    await edit_feed(message, app_state, callback_data.sort, callback_data.offset)
    await query.answer("Ваш голос учтён ❤️" if result.counted else "Вы уже голосовали ❤️")


# --- inline-режим ---------------------------------------------------------------------------


def match_preset(text: str) -> str | None:
    text = text.casefold()
    if not text:
        return None
    for preset in PRESETS.values():
        if preset.title.casefold().startswith(text):
            return preset.id
    return None


async def on_inline(query: InlineQuery, app_state: Any, bot: Bot) -> None:
    """``@бот`` — свежие фразы; ``@бот slug`` — эта фраза; ``@бот тайна`` — пресет."""
    who = client_of(app_state, query.from_user)
    user = await db.tg_user(app_state.db, who.voter_hash)
    await service.record(app_state, who, "inline")
    text = query.query.strip()
    phrases: list[PhraseOut] = []
    if text and service.valid_slug(text):
        with contextlib.suppress(service.NotFound, service.Gone):
            phrases = [await service.get_phrase(app_state, text)]
    personal = not phrases
    if personal:
        preset_id = match_preset(text) or (
            user["preset_id"] if user["preset_id"] in PRESETS else DEFAULT_PRESET
        )
        # Inline-запрос приходит на каждую букву: упёрлись в лимит — отдаём, что успели.
        with contextlib.suppress(service.TooFast):
            for _ in range(INLINE_FRESH):
                phrases.append(await make(app_state, who, user, preset_id, [], 0))
    me = await bot.me()
    unique = {phrase.slug: phrase for phrase in phrases}
    # ?via=tg — превью ссылки с открыткой, где вместо домена @handle бота.
    results = [
        InlineQueryResultArticle(
            id=phrase.slug,
            title=phrase.preset_title,
            description=phrase.text,
            input_message_content=InputTextMessageContent(
                message_text=shared_html(phrase.text, f"{phrase.url}?via=tg"),
                link_preview_options=LinkPreviewOptions(
                    url=f"{phrase.url}?via=tg", prefer_large_media=True, show_above_text=True
                ),
            ),
            reply_markup=kb.shared(phrase.slug, phrase.url, me.username),
        )
        for phrase in unique.values()
    ]
    await query.answer(results, cache_time=0 if personal else 300, is_personal=personal)


# --- ошибки ---------------------------------------------------------------------------------


async def on_service_error(event: ErrorEvent) -> None:
    text = ERROR_TEXT.get(type(event.exception), "Не получилось")
    update = event.update
    try:
        if update.callback_query:
            await update.callback_query.answer(text)
        elif update.message:
            await update.message.answer(text)
        elif update.inline_query:
            await update.inline_query.answer([], cache_time=0, is_personal=True)
    except TelegramBadRequest:
        pass  # на нажатие уже ответили


def make_router() -> Router:
    """Роутер с обработчиками; новый на каждый диспетчер (роутер нельзя подключить дважды)."""
    router = Router(name="storyteller")
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    router.message.register(start, CommandStart())
    router.message.register(help_, Command("help"))
    router.message.register(stats, Command("stats"))
    router.message.register(stats, F.text == kb.STATS_TEXT)
    router.message.register(top, Command("top"))
    router.message.register(top, F.text == kb.FEED_TEXT)
    router.message.register(new, Command("new"))
    router.message.register(any_text, F.text)
    router.callback_query.register(on_spin, cb.Spin.filter())
    router.callback_query.register(on_reroll, cb.Reroll.filter())
    router.callback_query.register(on_lock, cb.Lock.filter())
    router.callback_query.register(on_show, cb.Show.filter())
    router.callback_query.register(on_preset, cb.ChoosePreset.filter())
    router.callback_query.register(on_tone, cb.ChooseTone.filter())
    router.callback_query.register(on_wild, cb.ToggleWild.filter())
    router.callback_query.register(on_like, cb.Like.filter())
    router.callback_query.register(on_prompt, cb.Prompt.filter())
    router.callback_query.register(on_card, cb.Card.filter())
    router.callback_query.register(on_base, cb.Base.filter())
    router.callback_query.register(on_feed, cb.Feed.filter())
    router.callback_query.register(on_feed_like, cb.FeedLike.filter())
    router.inline_query.register(on_inline)
    router.errors.register(on_service_error, ExceptionTypeFilter(service.ServiceError))
    return router
