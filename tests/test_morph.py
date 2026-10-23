import pytest

from app import morph


@pytest.mark.parametrize(
    ("prep", "word", "expected"),
    [
        ("в", "лесу", "в"),
        ("в", "доме", "в"),
        ("в", "сне", "во"),
        ("в", "рту", "во"),
        ("в", "мгле", "во"),
        ("в", "тьме", "во"),
        ("в", "дворе", "во"),
        ("в", "дворце", "во"),
        ("в", "флигеле", "во"),
        ("в", "вселенной", "во"),
        ("в", "вагоне", "в"),
        ("в", "фонаре", "в"),
        ("о", "море", "о"),
        ("о", "утюге", "об"),
        ("о", "ёлке", "о"),
        ("о", "яблоке", "о"),
        ("с", "стола", "со"),
        ("с", "сада", "с"),
        ("к", "второму", "ко"),
        ("к", "дому", "к"),
        ("на", "мосту", "на"),
    ],
)
def test_preposition_form(prep, word, expected):
    assert morph.preposition(prep, word) == expected


def test_lemma_parse_prefers_common_noun_over_name():
    parse = morph.lemma_parse("лев", "NOUN", "anim")
    assert "Name" not in parse.tag
    assert morph.noun(parse, "datv") == "льву"


def test_lemma_parse_keeps_written_variant():
    # «счастье» нормализуется pymorphy3 в «счастие»; склонять нужно написанную форму.
    parse = morph.lemma_parse("счастье", "NOUN", "inan")
    assert morph.noun(parse, "ablt") == "счастьем"


def test_lemma_parse_accepts_participles_as_adjectives():
    parse = morph.lemma_parse("заброшенный", "ADJF")
    assert morph.adjective(parse, "loct", "sing", "femn", "inan") == "заброшенной"


def test_lemma_parse_respects_aspect_of_biaspectual_verb():
    parse = morph.lemma_parse("исследовать", "INFN", aspect="impf")
    assert parse.tag.aspect == "impf"
    assert morph.verb_present(parse, "plur") == "исследуют"


def test_locative_prefers_loc2_unless_disabled():
    forest = morph.lemma_parse("лес", "NOUN", "inan")
    assert morph.noun_locative(forest) == "лесу"
    assert morph.noun_locative(forest, prefer_loct=True) == "лесе"
    cave = morph.lemma_parse("пещера", "NOUN", "inan")
    assert morph.noun_locative(cave) == "пещере"


@pytest.mark.parametrize(
    ("gender", "animacy", "expected"),
    [
        ("masc", "anim", "старого"),
        ("masc", "inan", "старый"),
        ("femn", "anim", "старую"),
        ("neut", "inan", "старое"),
    ],
)
def test_adjective_accusative_depends_on_animacy(gender, animacy, expected):
    parse = morph.lemma_parse("старый", "ADJF")
    assert morph.adjective(parse, "accs", "sing", gender, animacy) == expected


def test_verb_forms():
    fix = morph.lemma_parse("чинить", "INFN", aspect="impf")
    assert morph.verb_present(fix, "sing") == "чинит"
    hide = morph.lemma_parse("спрятать", "INFN", aspect="perf")
    assert morph.verb_past(hide, "femn", "sing") == "спрятала"
    assert morph.verb_past(hide, "masc", "plur") == "спрятали"


def test_missing_form_reports_indeclinable_gaps():
    assert morph.missing_form("object", morph.lemma_parse("ключ", "NOUN", "inan")) is None
    assert morph.missing_form("adj", morph.lemma_parse("тихий", "ADJF")) is None


def test_qualitative_adjectives():
    assert morph.is_qualitative(morph.lemma_parse("тихий", "ADJF"))
    assert not morph.is_qualitative(morph.lemma_parse("деревянный", "ADJF"))
