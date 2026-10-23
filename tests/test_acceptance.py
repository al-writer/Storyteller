"""Приёмочные проверки движка: прогон словаря, фазз-тест и золотые фразы."""

import json
from pathlib import Path

import pytest

from app import morph
from app.engine import Engine
from app.lexicon import TONES
from app.presets import PRESETS

GOLDEN = Path(__file__).with_name("golden.json")
FUZZ_PER_PRESET = 10_000

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def engine(lexicon):
    return Engine(lexicon)


def test_every_lemma_inflects_into_every_required_form(lexicon):
    failures = []
    for word in lexicon.by_id.values():
        pos = morph.CATEGORY_POS[word.category]
        parse = morph.lemma_parse(word.lemma, pos, word.animacy, word.aspect)
        reason = (
            "нет разбора"
            if parse is None
            else morph.missing_form(word.category, parse, word.prefer_loct)
        )
        if reason:
            failures.append(f"{word.lemma} ({word.category}): {reason}")
    assert not failures, failures[:20]


def form_matches(inflection, word) -> bool:
    """Слово в тексте — форма из парадигмы своей леммы с запрошенными граммемами.

    Сверяемся с парадигмой (а не с разбором текста), чтобы варианты вроде
    «ущелье»/«ущелие» не давали ложных срабатываний; начальная форма там, где нужна
    косвенная, сюда не пройдёт.
    """
    text = morph.norm(inflection.text.split()[-1])
    wanted = set(inflection.grammemes)
    parse = morph.lemma_parse(inflection.lemma, inflection.pos, word.animacy, word.aspect)
    for form in parse.lexeme:
        tag = set(form.tag.grammemes)
        if "loc2" in tag:
            tag.add("loct")
        if morph.norm(form.word) == text and wanted <= tag:
            return True
    return False


@pytest.mark.parametrize("preset_id", sorted(PRESETS))
def test_fuzz(engine, preset_id):
    tones = [None, *TONES]
    for seed in range(FUZZ_PER_PRESET):
        tone = tones[seed % len(tones)]
        phrase = engine.generate(preset_id, seed=seed, tone=tone, wild=seed % 10 == 0)
        text = phrase.text
        assert text[0].isupper() and text.endswith("."), text
        assert "  " not in text and " ," not in text and " ." not in text, text
        for inflection in phrase.inflections:
            word = phrase.words[inflection.slot]
            assert form_matches(inflection, word), (text, inflection)


def golden_phrases(engine) -> list[dict]:
    result = []
    for preset_id in sorted(PRESETS):
        for seed in range(5):
            phrase = engine.generate(preset_id, seed=1000 + seed)
            result.append({"preset": preset_id, "seed": 1000 + seed, "text": phrase.text,
                           "slug": phrase.slug})  # fmt: skip
    return result


def test_golden_phrases(engine, request):
    current = golden_phrases(engine)
    if request.config.getoption("--update-golden") or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", "utf-8")
        pytest.skip("снапшот золотых фраз обновлён")
    assert current == json.loads(GOLDEN.read_text("utf-8"))
