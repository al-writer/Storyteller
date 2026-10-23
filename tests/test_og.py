import io

import pytest
from PIL import Image

from app.og import FILL_MAX, FORMATS, SERIF, fill_ratio, render_card, wrap
from app.og import font as load_font
from app.typo import NBSP

PHRASES = [
    "Усталый часовщик чинит старинный компас на маяке, испытывая тихую тоску.",
    "Никто не знает, почему старая библиотекарша спрятала компас в колодце.",
    "На мосту дирижёр гладит чемодан, словно бумажный фонарь.",
    "Однажды почтальон находит ключ в оранжерее — и с тех пор тревога не отпускает его.",
]


@pytest.mark.parametrize("fmt", sorted(FORMATS))
@pytest.mark.parametrize("text", PHRASES)
def test_phrase_fills_the_field(fmt, text):
    assert 0.5 <= fill_ratio(text, fmt) <= FILL_MAX


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_card_size(fmt):
    png = render_card(PHRASES[0], "Сюжет", "dark", fmt, "example.test")
    assert Image.open(io.BytesIO(png)).size == FORMATS[fmt]


def test_wrap_never_breaks_at_non_breaking_space():
    face = load_font(SERIF, 60)
    lines = wrap(f"слово в{NBSP}лесу слово в{NBSP}лесу", face, 200)
    assert all(not line.endswith(" в") for line in lines)


def test_unknown_format():
    with pytest.raises(ValueError):
        render_card("Текст.", "Сюжет", None, "huge", "example.test")
