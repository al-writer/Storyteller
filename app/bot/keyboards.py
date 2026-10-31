"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    SwitchInlineQueryChosenChat,
    WebAppInfo,
)

from app.bot import callbacks as cb
from app.bot.render import TONE_TITLES
from app.presets import PRESETS
from app.schemas import FeedItem, PhraseOut

SPIN_TEXT = "🎰 Крутить"
FEED_TEXT = "🏆 Зал славы"
STATS_TEXT = "📊 Статистика"
ADMIN_TEXT = "🛠 Админка"
WORD_LABEL_LIMIT = 28
COPY_TEXT_LIMIT = 256  # больше Telegram в кнопку копирования не принимает


def button(text: str, data: cb.CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def like_label(likes: int) -> str:
    return f"❤️ {likes}" if likes else "❤️ В зал славы"


def reply_keyboard(admin: bool = False, admin_url: str | None = None) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода; у админов — ещё статистика и админка."""
    rows = [[KeyboardButton(text=SPIN_TEXT), KeyboardButton(text=FEED_TEXT)]]
    if admin:
        row = [KeyboardButton(text=STATS_TEXT)]
        if admin_url:
            row.append(KeyboardButton(text=ADMIN_TEXT, web_app=WebAppInfo(url=admin_url)))
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def share(slug: str) -> InlineKeyboardButton:
    """Выбор чата и inline-запрос ``@бот slug`` — бот отвечает карточкой фразы."""
    return InlineKeyboardButton(
        text="↗️ Поделиться",
        switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query=slug, allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True
        ),
    )


def shared(slug: str, url: str, username: str | None) -> InlineKeyboardMarkup:
    """Кнопки под фразой, отправленной в чужой чат."""
    rows = []
    if username:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎰 Сгенерировать свою", url=f"https://t.me/{username}?start={slug}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🌐 На сайте", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main(phrase: PhraseOut, mask: int) -> InlineKeyboardMarkup:
    slug = phrase.slug
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(SPIN_TEXT, cb.Spin(slug=slug, mask=mask))],
            [
                button("🔒 Слова", cb.Show(slug=slug, mask=mask, view="w")),
                button("📚 Пресет", cb.Show(slug=slug, mask=mask, view="p")),
                button("🎭 Тон", cb.Show(slug=slug, mask=mask, view="t")),
            ],
            [
                button("✍️ Промпт", cb.Prompt(slug=slug, kind="story")),
                button("🎨 Художнику", cb.Prompt(slug=slug, kind="art")),
                button("🖼 Открытка", cb.Card(slug=slug)),
            ],
            [
                button(like_label(phrase.likes), cb.Like(slug=slug, mask=mask, view="m")),
                share(slug),
            ],
        ]
    )


def collapsed(slug: str, likes: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура фразы, которая уже не последняя в чате."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(like_label(likes), cb.Like(slug=slug, mask=0, view="c")),
                button("♻️ За основу", cb.Base(slug=slug)),
                share(slug),
            ]
        ]
    )


def feed(item: FeedItem, sort: str, offset: int, total: int) -> InlineKeyboardMarkup:
    nav = []
    if offset > 0:
        nav.append(button("◀", cb.Feed(sort=sort, offset=offset - 1)))
    nav.append(button(f"❤️ {item.likes}", cb.FeedLike(slug=item.slug, sort=sort, offset=offset)))
    if offset + 1 < total:
        nav.append(button("▶", cb.Feed(sort=sort, offset=offset + 1)))
    other = button("📡 Новое", cb.Feed(sort="n", offset=0))
    if sort == "n":
        other = button("🏆 Топ", cb.Feed(sort="t", offset=0))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            nav,
            [button("♻️ За основу", cb.Base(slug=item.slug)), share(item.slug)],
            [other],
        ]
    )


def feed_empty(sort: str) -> InlineKeyboardMarkup:
    other = cb.Feed(sort="n", offset=0) if sort == "t" else cb.Feed(sort="t", offset=0)
    return InlineKeyboardMarkup(
        inline_keyboard=[[button("📡 Новое" if sort == "t" else "🏆 Топ", other)]]
    )


def words(phrase: PhraseOut, mask: int) -> InlineKeyboardMarkup:
    locked = cb.from_mask(mask)
    rows = []
    for slot in phrase.slots:
        if not slot.text:
            continue
        label = slot.text if len(slot.text) <= WORD_LABEL_LIMIT else slot.text[:27] + "…"
        icon = "🔒" if slot.index in locked else "🔓"
        rows.append(
            [
                button(f"{icon} {label}", cb.Lock(slug=phrase.slug, mask=mask, i=slot.index)),
                button("🔄", cb.Reroll(slug=phrase.slug, mask=mask, i=slot.index)),
            ]
        )
    rows.append(
        [
            button(SPIN_TEXT, cb.Spin(slug=phrase.slug, mask=mask)),
            button("← Готово", cb.Show(slug=phrase.slug, mask=mask, view="m")),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def presets(phrase: PhraseOut, mask: int) -> InlineKeyboardMarkup:
    items = [
        button(
            f"✓ {preset.title}" if preset.id == phrase.preset_id else preset.title,
            cb.ChoosePreset(slug=phrase.slug, preset=preset.id),
        )
        for preset in PRESETS.values()
    ]
    rows = [items[i : i + 3] for i in range(0, len(items), 3)]
    rows.append([button("← Назад", cb.Show(slug=phrase.slug, mask=mask, view="m"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tones(phrase: PhraseOut, mask: int, tone: str, wild: bool) -> InlineKeyboardMarkup:
    items = [
        button(
            f"✓ {title}" if tone_id == tone else title,
            cb.ChooseTone(slug=phrase.slug, mask=mask, tone=tone_id),
        )
        for tone_id, title in TONE_TITLES.items()
    ]
    rows = [items[i : i + 3] for i in range(0, len(items), 3)]
    rows.append(
        [
            button(
                f"🌀 Дикий словарь: {'вкл' if wild else 'выкл'}",
                cb.ToggleWild(slug=phrase.slug, mask=mask),
            )
        ]
    )
    rows.append(
        [
            button(SPIN_TEXT, cb.Spin(slug=phrase.slug, mask=mask)),
            button("← Назад", cb.Show(slug=phrase.slug, mask=mask, view="m")),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def for_view(
    view: cb.View, phrase: PhraseOut, mask: int, tone: str, wild: bool
) -> InlineKeyboardMarkup:
    if view == "w":
        return words(phrase, mask)
    if view == "p":
        return presets(phrase, mask)
    if view == "t":
        return tones(phrase, mask, tone, wild)
    return main(phrase, mask)


def copy_prompt(text: str) -> InlineKeyboardMarkup | None:
    if len(text) > COPY_TEXT_LIMIT:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Скопировать", copy_text=CopyTextButton(text=text))]
        ]
    )
