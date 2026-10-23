import asyncio
import json

from app import db
from tests.conftest import LEXICON_PATH


def run(coro):
    return asyncio.run(coro)


def test_lexicon_load_is_idempotent_and_keeps_phrases(tmp_path):
    small = tmp_path / "lexicon.jsonl"
    lines = LEXICON_PATH.read_text(encoding="utf-8").splitlines()[:50]
    small.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def scenario():
        async with db.connect(tmp_path / "test.sqlite3") as conn:
            await db.create_schema(conn)
            assert await db.load_lexicon(conn, small) is True
            assert await db.load_lexicon(conn, small) is False
            await db.remember_phrase(
                conn, "abc12345", "plot", {"preset": "plot", "slots": []}, "Текст."
            )
            await db.save_idea(conn, "abc12345")

            small.write_text("\n".join(lines[:10]) + "\n", encoding="utf-8")
            assert await db.load_lexicon(conn, small) is True
            words = await db.read_words(conn)
            phrase = await db.get_phrase(conn, "abc12345")
            return words, phrase

    words, phrase = run(scenario())
    assert len(words) == 10
    assert {w.lemma for w in words} == {json.loads(line)["lemma"] for line in lines[:10]}
    assert phrase["text"] == "Текст."
    assert phrase["saved_at"] is not None


def test_wal_mode(tmp_path):
    async def scenario():
        async with (
            db.connect(tmp_path / "wal.sqlite3") as conn,
            conn.execute("PRAGMA journal_mode") as cursor,
        ):
            return (await cursor.fetchone())[0]

    assert run(scenario()) == "wal"


def test_hot_score_decays_with_age():
    now = 1_000_000.0
    fresh = db.hot_score(10, now - 3600, now)
    old = db.hot_score(10, now - 3600 * 48, now)
    assert fresh > old
    assert db.hot_score(0, now, now) == 0
