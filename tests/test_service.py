"""Сервисный слой без HTTP — так его вызывает Telegram-бот."""

import pytest

from app import service
from app.main import app
from app.schemas import GenerateIn
from app.service import Client

pytestmark = pytest.mark.usefixtures("no_rate_limits")


@pytest.fixture
def call(client):
    """Запуск корутины сервиса в event loop приложения, где живёт соединение с базой."""
    return lambda fn, *args: client.portal.call(fn, app.state, *args)


def test_client_hash_is_stable_and_depends_on_source():
    first = Client.from_source("salt", "tg:42")
    assert first == Client.from_source("salt", "tg:42")
    assert first.key == first.voter_hash[:32]
    assert first != Client.from_source("salt", "tg:43")
    assert first != Client.from_source("pepper", "tg:42")


def test_generate_like_and_reload(call):
    who = Client.from_source("salt", "tg:1")
    phrase = call(service.generate, who, GenerateIn(preset_id="mystery", seed=50))
    assert phrase.url.endswith(f"/s/{phrase.slug}")

    assert call(service.like, who, phrase.slug).counted
    again = call(service.like, who, phrase.slug)
    assert (again.likes, again.counted) == (1, False)

    stored = call(service.get_phrase, phrase.slug)
    assert stored.text == phrase.text
    assert stored.likes == 1
    assert call(service.card_path, phrase.slug, "square").exists()


def test_errors(call):
    who = Client.from_source("salt", "tg:2")
    with pytest.raises(service.Invalid):
        call(service.generate, who, GenerateIn(preset_id="nope"))
    for fn in (service.get_phrase, service.save, service.card_path):
        with pytest.raises(service.NotFound):
            call(fn, "unknown1")
    with pytest.raises(service.NotFound):
        call(service.card_path, "bad-slug")


def test_rate_limit(call):
    who = Client.from_source("salt", "tg:3")
    app.state.like_limiter.limit = 0
    with pytest.raises(service.TooFast):
        call(service.like, who, "unknown1")
