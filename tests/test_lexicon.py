import random

from app.lexicon import MIN_TONE_POOL, Lexicon, UsageTracker, Word, dominant_tone, suits_noun


def make_words(count: int, category: str = "object", **fields) -> list[Word]:
    return [
        Word(id=i, lemma=f"w{i}", category=category, tier="core", **fields) for i in range(count)
    ]


def test_usage_tracker_penalises_and_expires():
    now = [0.0]
    tracker = UsageTracker(window_seconds=10, clock=lambda: now[0])
    tracker.record([1, 1, 1])
    assert tracker.penalty(1) == 0.5
    assert tracker.penalty(2) == 1.0
    now[0] = 11.0
    tracker.record([2])
    assert tracker.penalty(1) == 1.0


def test_tone_falls_back_when_pool_is_small():
    words = make_words(20)
    toned = [Word(id=100 + i, lemma=f"t{i}", category="object", tier="core",
                  tones=frozenset({"dark"})) for i in range(MIN_TONE_POOL - 1)]  # fmt: skip
    lexicon = Lexicon(words + toned)
    picks = {lexicon.sample("object", random.Random(s), tone="dark").id for s in range(200)}
    assert any(pick < 100 for pick in picks)


def test_tone_restricts_pool_when_large_enough():
    words = make_words(20)
    toned = [Word(id=100 + i, lemma=f"t{i}", category="object", tier="core",
                  tones=frozenset({"dark"})) for i in range(MIN_TONE_POOL)]  # fmt: skip
    lexicon = Lexicon(words + toned)
    picks = {lexicon.sample("object", random.Random(s), tone="dark").id for s in range(200)}
    assert all(pick >= 100 for pick in picks)


def test_exclusions_and_empty_pool():
    lexicon = Lexicon(make_words(3))
    rng = random.Random(0)
    assert lexicon.sample("object", rng, exclude_ids={0, 1}).id == 2
    assert lexicon.sample("object", rng, exclude_ids={0, 1, 2}) is None
    assert lexicon.sample("place", rng) is None


def test_adjective_applicability():
    plain = Word(1, "старый", "adj", "core")
    feeling = Word(2, "тихий", "adj", "core", feel=True)
    material = Word(3, "ржавый", "adj", "core", noactor=True)
    assert suits_noun(plain, noun_category="actor")
    assert not suits_noun(plain, noun_category="feeling")
    assert suits_noun(feeling, noun_category="feeling")
    assert not suits_noun(material, noun_category="actor")
    assert suits_noun(material, noun_category="object")


def test_dominant_tone():
    words = [
        Word(1, "a", "adj", "core", tones=frozenset({"dark", "fairy"})),
        Word(2, "b", "actor", "core", tones=frozenset({"dark"})),
        Word(3, "c", "object", "core"),
    ]
    assert dominant_tone(words) == "dark"
    assert dominant_tone(words[2:]) is None
