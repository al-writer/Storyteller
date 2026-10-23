"""Проверки собранного data/lexicon.jsonl: схема, уникальность, объёмы ярусов."""

import json
from collections import Counter
from pathlib import Path

import pytest

LEXICON = Path(__file__).resolve().parent.parent / "data" / "lexicon.jsonl"
CATEGORIES = {"actor", "object", "place", "feeling", "adj", "verb_impf", "verb_perf"}
TONES = {"dark", "fairy", "cyber", "city", "myth"}


@pytest.fixture(scope="module")
def records():
    with LEXICON.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def test_ids_are_unique(records):
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids))


def test_schema(records):
    for r in records:
        assert r["category"] in CATEGORIES, r
        assert r["tier"] in {"core", "extended", "wild"}, r
        assert set(r.get("tones", [])) <= TONES, r
        if r["category"] == "place":
            assert r["prep"] in {"в", "на"}, r
        if r["category"].startswith("verb"):
            assert r["governs"] in {"accs", "gent", "datv", "ablt", "loct"}, r
        if r["category"] in {"actor", "object", "place", "feeling"}:
            assert r["gender"] in {"masc", "femn", "neut", "ms-f"}, r


def test_core_size_per_category(records):
    core = Counter(r["category"] for r in records if r["tier"] == "core")
    for category in CATEGORIES - {"feeling"}:
        assert core[category] >= 200, (category, core[category])
    assert core["feeling"] >= 120


def test_every_tone_has_core_words_in_every_noun_category(records):
    for category in ("actor", "object", "place", "adj"):
        for tone in TONES:
            count = sum(
                1
                for r in records
                if r["tier"] == "core" and r["category"] == category and tone in r.get("tones", [])
            )
            assert count >= 15, (category, tone, count)
