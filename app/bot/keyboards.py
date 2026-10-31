"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot import callbacks as cb
from app.bot.render import TONE_TITLES
from app.presets import PRESETS
from app.schemas import PhraseOut

SPIN_TEXT = "🎰 Крутить"
WORD_LABEL_LIMIT = 28
COPY_TEXT_LIMIT = 256  # больше Telegram в кнопку копирования не принимает


def button(text: str, data: cb.CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def like_label(likes: int) -> str:
    return f"❤️ {likes}" if likes else "❤️ В зал славы"


def reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=SPIN_TEXT)]], resize_keyboard=True)


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
            [button(like_label(phrase.likes), cb.Like(slug=slug, mask=mask, view="m"))],
        ]
    )


def collapsed(slug: str, likes: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура фразы, которая уже не последняя в чате."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(like_label(likes), cb.Like(slug=slug, mask=0, view="c")),
                button("♻️ За основу", cb.Base(slug=slug)),
            ]
        ]
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
