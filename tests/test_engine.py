import pytest

from app import morph
from app.engine import (
    Engine,
    GenerationError,
    Slot,
    adjective_form,
    canonical_key,
    make_slug,
    place_form,
    render,
    slots_from_key,
)
from app.presets import PRESETS

# --- таблица согласования -------------------------------------------------------------------

CASES = ("nomn", "accs", "datv", "ablt", "loct")

# (прилагательное, существительное, категория, род для общего рода, число): формы по падежам
AGREEMENT = [
    ("старый", "часовщик", "actor", None, "sing",
     ["старый часовщик", "старого часовщика", "старому часовщику", "старым часовщиком",
      "старом часовщике"]),
    ("старый", "ключ", "object", None, "sing",
     ["старый ключ", "старый ключ", "старому ключу", "старым ключом", "старом ключе"]),
    ("рыжий", "ведьма", "actor", None, "sing",
     ["рыжая ведьма", "рыжую ведьму", "рыжей ведьме", "рыжей ведьмой", "рыжей ведьме"]),
    ("тихий", "лампа", "object", None, "sing",
     ["тихая лампа", "тихую лампу", "тихой лампе", "тихой лампой", "тихой лампе"]),
    ("старый", "зеркало", "object", None, "sing",
     ["старое зеркало", "старое зеркало", "старому зеркалу", "старым зеркалом",
      "старом зеркале"]),
    ("огромный", "чудовище", "actor", None, "sing",
     ["огромное чудовище", "огромное чудовище", "огромному чудовищу", "огромным чудовищем",
      "огромном чудовище"]),
    ("старый", "сирота", "actor", "masc", "sing",
     ["старый сирота", "старого сироту", "старому сироте", "старым сиротой", "старом сироте"]),
    ("старый", "сирота", "actor", "femn", "sing",
     ["старая сирота", "старую сироту", "старой сироте", "старой сиротой", "старой сироте"]),
    ("старый", "часовщик", "actor", None, "plur",
     ["старые часовщики", "старых часовщиков", "старым часовщикам", "старыми часовщиками",
      "старых часовщиках"]),
    ("старый", "ключ", "object", None, "plur",
     ["старые ключи", "старые ключи", "старым ключам", "старыми ключами", "старых ключах"]),
    ("синий", "кукла", "object", None, "sing",
     ["синяя кукла", "синюю куклу", "синей кукле", "синей куклой", "синей кукле"]),
    ("синий", "кукла", "object", None, "plur",
     ["синие куклы", "синих кукол", "синим куклам", "синими куклами", "синих куклах"]),
]  # fmt: skip


@pytest.mark.parametrize(
    ("adj", "noun", "category", "gender", "number", "expected"),
    AGREEMENT,
    ids=[f"{a}-{n}-{g or ''}{num}" for a, n, _, g, num, _e in AGREEMENT],
)
def test_adjective_agreement_table(word, adj, noun, category, gender, number, expected):
    adjective = word(adj, "adj")
    noun_word = word(noun, category)
    parse = morph.lemma_parse(noun_word.lemma, "NOUN", noun_word.animacy)
    for case, phrase in zip(CASES, expected, strict=True):
        text = adjective_form(adjective, noun_word, case, number, gender)
        assert f"{text} {morph.noun(parse, case, number)}" == phrase, case


def test_superlative_agrees_too(word):
    adjective = word("тихий", "adj")
    noun = word("часовщик", "actor")
    assert adjective_form(adjective, noun, "accs", superlative=True) == "самого тихого"
    witch = word("ведьма", "actor")
    assert adjective_form(adjective, witch, "ablt", superlative=True) == "самой тихой"


# --- предлоги и места ---------------------------------------------------------------------

PLACES = {
    "лес": "в лесу",
    "мост": "на мосту",
    "сон": "во сне",
    "флигель": "во флигеле",
    "почта": "на почте",
    "луг": "на лугу",
    "сад": "в саду",
    "берег": "на берегу",
    "порт": "в порту",
    "пруд": "на пруду",
    "двор": "во дворе",
    "дворец": "во дворце",
    "пляж": "на пляже",
    "склад": "на складе",
    "холм": "на холме",
    "пещера": "в пещере",
    "маяк": "на маяке",
    "вокзал": "на вокзале",
    "вселенная": "во вселенной",
    "угол": "в углу",
    "штаб": "в штабе",
    "край": "на краю",
}


@pytest.mark.parametrize(("lemma", "expected"), PLACES.items())
def test_place_prepositions(word, lemma, expected):
    assert place_form(word(lemma, "place")) == expected


# --- рендер фраз ----------------------------------------------------------------------------


def build(word, preset_id, mapping):
    """Слоты пресета по словарю {имя слота: (лемма, параметры Slot)}."""
    preset = PRESETS[preset_id]
    slots = []
    for spec in preset.slots:
        lemma, *params = mapping[spec.name]
        governs = None
        if isinstance(lemma, tuple):
            lemma, governs = lemma
        options = params[0] if params else {}
        slots.append(Slot(word(lemma, spec.category, governs).id, **options))
    return preset, slots


def test_plot_example(lexicon, word):
    preset, slots = build(
        word,
        "plot",
        {
            "adj_actor": ("усталый",),
            "actor": ("часовщик",),
            "verb": ("чинить",),
            "adj_obj": ("старинный",),
            "obj": ("компас",),
            "place": ("маяк",),
            "adj_feel": ("тихий",),
            "feel": ("тоска",),
        },
    )
    phrase = render(lexicon, preset, slots)
    assert phrase.text == "Усталый часовщик чинит старинный компас на маяке, испытывая тихую тоску."


def test_governed_case_and_preposition(lexicon, word):
    mapping = {
        "adj_actor": ("рыжий",),
        "actor": ("ведьма",),
        "verb": (("мечтать", "loct"),),
        "adj_obj": ("огромный",),
        "obj": ("ключ",),
        "place": ("лес",),
        "adj_feel": ("тихий",),
        "feel": ("тоска",),
    }
    phrase = render(lexicon, *build(word, "plot", mapping))
    assert phrase.text.startswith("Рыжая ведьма мечтает об огромном ключе в лесу,")
    mapping["verb"] = (("бояться", "gent"),)
    phrase = render(lexicon, *build(word, "plot", mapping))
    assert phrase.text.startswith("Рыжая ведьма боится огромного ключа в лесу,")


@pytest.mark.parametrize(
    ("actor", "options", "datv", "accs", "must"),
    [
        ("часовщик", {}, "ему", "его", "должен"),
        ("ведьма", {}, "ей", "её", "должна"),
        ("чудовище", {}, "ему", "его", "должно"),
        ("сирота", {"gender": "femn"}, "ей", "её", "должна"),
        ("сирота", {"gender": "masc"}, "ему", "его", "должен"),
        ("часовщик", {"number": "plur"}, "им", "их", "должны"),
    ],
)
def test_pronouns_follow_actor(lexicon, word, actor, options, datv, accs, must):
    conflict = render(
        lexicon,
        *build(
            word,
            "conflict",
            {
                "actor": (actor, options),
                "verb": ("украсть",),
                "obj": ("луна",),
                "adj_obj2": ("ржавый",),
                "obj2": ("маятник",),
            },
        ),
    )
    assert f", но {datv} мешает ржавый маятник." in conflict.text
    setup = render(
        lexicon,
        *build(
            word,
            "setup",
            {"actor": (actor, options), "obj": ("ключ",), "place": ("оранжерея",),
             "feel": ("тревога",)},
        ),
    )  # fmt: skip
    assert setup.text.endswith(f"тревога не отпускает {accs}.")
    logline = render(
        lexicon,
        *build(
            word,
            "logline",
            {"adj_actor": ("рыжий",), "actor": (actor, options), "verb": ("вернуть",),
             "obj": ("компас",), "place": ("город",)},
        ),
    )  # fmt: skip
    assert f" {must} вернуть компас до рассвета, иначе город исчезнет." in logline.text


def test_past_tense_follows_actor_gender(lexicon, word):
    mapping = {
        "adj_actor": ("старый",),
        "actor": ("библиотекарша",),
        "verb": ("спрятать",),
        "obj": ("компас",),
        "place": ("колодец",),
    }
    phrase = render(lexicon, *build(word, "mystery", mapping))
    assert phrase.text == "Никто не знает, почему старая библиотекарша спрятала компас в колодце."
    mapping["actor"] = ("библиотекарь", {"number": "plur"})
    phrase = render(lexicon, *build(word, "mystery", mapping))
    assert "старые библиотекари спрятали компас" in phrase.text


def test_absurd_starts_with_capitalised_preposition(lexicon, word):
    phrase = render(
        lexicon,
        *build(
            word,
            "absurd",
            {"place": ("мост",), "actor": ("дирижёр",), "verb": ("гладить",),
             "obj": ("чемодан",), "adj_obj2": ("бумажный",), "obj2": ("фонарь",)},
        ),
    )  # fmt: skip
    assert phrase.text == "На мосту дирижёр гладит чемодан, словно бумажный фонарь."
    assert phrase.tokens[0].slot == 0


# --- генерация ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(lexicon):
    return Engine(lexicon)


@pytest.mark.parametrize("preset_id", sorted(PRESETS))
def test_generation_is_deterministic_with_seed(engine, preset_id):
    first = engine.generate(preset_id, seed=42)
    second = engine.generate(preset_id, seed=42)
    assert first.text == second.text
    assert first.slug == second.slug


def test_generation_varies_without_seed(engine):
    texts = {engine.generate("plot").text for _ in range(20)}
    assert len(texts) > 15


def test_locked_slots_survive_regeneration(engine):
    first = engine.generate("plot", seed=1)
    second = engine.generate("plot", current=first.slots, locked=[1, 4], seed=2)
    assert second.slots[1] == first.slots[1]
    assert second.slots[4] == first.slots[4]
    assert second.slots[0] != first.slots[0] or second.slots[2] != first.slots[2]


def test_reroll_changes_only_one_slot(engine):
    first = engine.generate("plot", seed=3)
    second = engine.generate("plot", current=first.slots, reroll=1, seed=4)
    changed = [i for i, (a, b) in enumerate(zip(first.slots, second.slots, strict=True)) if a != b]
    assert changed == [1]


def test_recent_ids_are_avoided(engine, lexicon):
    first = engine.generate("setup", seed=5)
    recent = [slot.id for slot in first.slots]
    for seed in range(6, 16):
        phrase = engine.generate("setup", recent_ids=recent, seed=seed)
        assert not set(recent) & {slot.id for slot in phrase.slots}


def test_tone_coherence(engine, lexicon):
    phrase = engine.generate("plot", tone="cyber", seed=8)
    assert phrase.tone == "cyber"
    toned = sum("cyber" in w.tones for w in phrase.words)
    assert toned >= len(phrase.words) - 2


def test_wild_tier_only_in_wild_mode(engine):
    for seed in range(30):
        assert all(w.tier != "wild" for w in engine.generate("plot", seed=seed).words)
    wild_words = [w for s in range(30) for w in engine.generate("plot", wild=True, seed=s).words]
    assert any(w.tier == "wild" for w in wild_words)


def test_unknown_preset_is_rejected(engine):
    with pytest.raises(GenerationError):
        engine.generate("nope")


def test_invalid_current_slots_are_ignored(engine):
    phrase = engine.generate("plot", current=[Slot(1)] * 8, locked=range(8), seed=9)
    assert all(slot.id != 1 for slot in phrase.slots)


# --- slug ---------------------------------------------------------------------------------


def test_slug_is_stable_and_compact(engine):
    phrase = engine.generate("plot", seed=10)
    assert len(phrase.slug) == 8
    assert phrase.slug.isalnum()
    assert make_slug("plot", phrase.slots) == phrase.slug
    preset_id, slots = slots_from_key(phrase.key())
    assert (preset_id, slots) == ("plot", phrase.slots)
    assert canonical_key("plot", slots) == canonical_key(phrase.preset.id, phrase.slots)


def test_slug_depends_on_form_parameters(engine):
    phrase = engine.generate("plot", seed=11)
    plural = [*phrase.slots]
    plural[1] = Slot(plural[1].id, "plur" if plural[1].number == "sing" else "sing")
    assert make_slug("plot", plural) != phrase.slug
