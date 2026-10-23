from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.lexicon import Lexicon, Word
from app.main import app

LEXICON_PATH = Path(__file__).resolve().parent.parent / "data" / "lexicon.jsonl"


@pytest.fixture(scope="session")
def lexicon() -> Lexicon:
    return Lexicon.from_jsonl(LEXICON_PATH)


@pytest.fixture(scope="session")
def word(lexicon):
    """Поиск слова словаря по лемме и категории."""

    def find(lemma: str, category: str, governs: str | None = None) -> Word:
        for item in lexicon.pool(category):
            if item.lemma == lemma and (governs is None or item.governs == governs):
                return item
        raise LookupError(f"{lemma} ({category}) нет в словаре")

    return find


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    app.state.settings = Settings(
        base_url="https://example.test", data_dir=data_dir, lexicon_path=LEXICON_PATH
    )
    with TestClient(app) as test_client:
        yield test_client
    del app.state.settings


@pytest.fixture
def no_rate_limits(client):
    """Лимиты проверяются отдельным тестом; остальным они не нужны."""
    app.state.generate_limiter.limit = 10_000
    app.state.like_limiter.limit = 10_000
    yield
    app.state.generate_limiter.limit = 10
    app.state.like_limiter.limit = 30


def generate(client, **payload):
    response = client.post("/api/generate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="перезаписать tests/golden.json текущим выводом движка",
    )
