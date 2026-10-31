"""Текст сообщения с фразой (HTML для Telegram)."""

from __future__ import annotations

import html

from app.schemas import PhraseOut
from app.typo import typograph

ATTACHED = {",", "."}
TONE_TITLES = {
    "auto": "Авто",
    "dark": "Мрачный",
    "fairy": "Сказочный",
    "cyber": "Кибер",
    "city": "Город",
    "myth": "Миф",
}
# Метки закреплённых слов: переживают типограф и экранирование, потом становятся <b>.
_OPEN, _CLOSE = "\x01", "\x02"


def phrase_html(phrase: PhraseOut, locked: list[int]) -> str:
    """Фраза с закреплёнными словами жирным, после типографа."""
    parts: list[str] = []
    for i, token in enumerate(phrase.tokens):
        if i and token.text not in ATTACHED:
            parts.append(" ")
        if token.slot is not None and token.slot in locked:
            parts.append(f"{_OPEN}{token.text}{_CLOSE}")
        else:
            parts.append(token.text)
    text = html.escape(typograph("".join(parts)), quote=False)
    return text.replace(_OPEN, "<b>").replace(_CLOSE, "</b>")


def header(phrase: PhraseOut, tone: str, wild: bool) -> str:
    """Пресет и настройки, с которыми будет крутиться следующая фраза."""
    bits = [phrase.preset_title]
    if tone != "auto":
        bits.append(TONE_TITLES.get(tone, tone))
    if wild:
        bits.append("🌀 дикий словарь")
    return f"<i>{html.escape(' · '.join(bits))}</i>"


def message_html(phrase: PhraseOut, locked: list[int], tone: str, wild: bool) -> str:
    return f"{header(phrase, tone, wild)}\n\n{phrase_html(phrase, locked)}"
