"""Пресеты формул: слоты и шаблон фразы.

Шаблон — последовательность токенов. Падеж существительного задаётся явно или берётся из
управления глагола (``GOVERNED``); прилагательное согласуется со своим существительным.
"""

from __future__ import annotations

from dataclasses import dataclass

GOVERNED = "gov"


@dataclass(frozen=True)
class SlotSpec:
    name: str
    category: str
    agree: str | None = None  # для прилагательного — имя слота существительного
    plural_ok: bool = False  # для «Кто»: изредка множественное число
    superlative_ok: bool = False  # для прилагательного: изредка «самый …»


@dataclass(frozen=True)
class Lit:
    text: str


@dataclass(frozen=True)
class Noun:
    slot: str
    case: str  # nomn / accs / ... или GOVERNED
    verb: str | None = None  # слот глагола, если case == GOVERNED


@dataclass(frozen=True)
class Adj:
    slot: str


@dataclass(frozen=True)
class Verb:
    slot: str
    form: str  # pres / inf / past
    subject: str = "actor"


@dataclass(frozen=True)
class GovPrep:
    """Предлог управления глагола («мечтает о …»), если он есть; форма — по следующему слову."""

    verb: str


@dataclass(frozen=True)
class Place:
    """«в/на» + место в предложном падеже."""

    slot: str


@dataclass(frozen=True)
class Agree:
    """Слово, согласуемое с «Кто» по роду/числу: ключи masc, femn, neut, plur."""

    forms: tuple[tuple[str, str], ...]
    subject: str = "actor"

    def pick(self, gender: str, number: str) -> str:
        table = dict(self.forms)
        return table["plur"] if number == "plur" else table[gender]


def agree(masc: str, femn: str, neut: str, plur: str) -> Agree:
    return Agree((("masc", masc), ("femn", femn), ("neut", neut), ("plur", plur)))


Token = Lit | Noun | Adj | Verb | GovPrep | Place | Agree


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    slots: tuple[SlotSpec, ...]
    template: tuple[Token, ...]

    def slot_index(self, name: str) -> int:
        for index, spec in enumerate(self.slots):
            if spec.name == name:
                return index
        raise KeyError(name)

    def spec(self, name: str) -> SlotSpec:
        return self.slots[self.slot_index(name)]


ACTOR = SlotSpec("actor", "actor", plural_ok=True)


def _adj(name: str, noun: str) -> SlotSpec:
    return SlotSpec(name, "adj", agree=noun, superlative_ok=True)


PRESETS: dict[str, Preset] = {
    preset.id: preset
    for preset in (
        Preset(
            id="plot",
            title="Сюжет",
            slots=(
                _adj("adj_actor", "actor"),
                ACTOR,
                SlotSpec("verb", "verb_impf"),
                _adj("adj_obj", "obj"),
                SlotSpec("obj", "object"),
                SlotSpec("place", "place"),
                _adj("adj_feel", "feel"),
                SlotSpec("feel", "feeling"),
            ),
            template=(
                Adj("adj_actor"),
                Noun("actor", "nomn"),
                Verb("verb", "pres"),
                GovPrep("verb"),
                Adj("adj_obj"),
                Noun("obj", GOVERNED, verb="verb"),
                Place("place"),
                Lit(","),
                Lit("испытывая"),
                Adj("adj_feel"),
                Noun("feel", "accs"),
                Lit("."),
            ),
        ),
        Preset(
            id="conflict",
            title="Конфликт",
            slots=(
                ACTOR,
                SlotSpec("verb", "verb_perf"),
                SlotSpec("obj", "object"),
                _adj("adj_obj2", "obj2"),
                SlotSpec("obj2", "object"),
            ),
            template=(
                Noun("actor", "nomn"),
                agree("пытается", "пытается", "пытается", "пытаются"),
                Verb("verb", "inf"),
                GovPrep("verb"),
                Noun("obj", GOVERNED, verb="verb"),
                Lit(","),
                Lit("но"),
                agree("ему", "ей", "ему", "им"),
                Lit("мешает"),
                Adj("adj_obj2"),
                Noun("obj2", "nomn"),
                Lit("."),
            ),
        ),
        Preset(
            id="absurd",
            title="Абсурд",
            slots=(
                SlotSpec("place", "place"),
                ACTOR,
                SlotSpec("verb", "verb_impf"),
                SlotSpec("obj", "object"),
                _adj("adj_obj2", "obj2"),
                SlotSpec("obj2", "object"),
            ),
            template=(
                Place("place"),
                Noun("actor", "nomn"),
                Verb("verb", "pres"),
                GovPrep("verb"),
                Noun("obj", GOVERNED, verb="verb"),
                Lit(","),
                Lit("словно"),
                Adj("adj_obj2"),
                Noun("obj2", "nomn"),
                Lit("."),
            ),
        ),
        Preset(
            id="mystery",
            title="Тайна",
            slots=(
                _adj("adj_actor", "actor"),
                ACTOR,
                SlotSpec("verb", "verb_perf"),
                SlotSpec("obj", "object"),
                SlotSpec("place", "place"),
            ),
            template=(
                Lit("Никто не знает, почему"),
                Adj("adj_actor"),
                Noun("actor", "nomn"),
                Verb("verb", "past"),
                GovPrep("verb"),
                Noun("obj", GOVERNED, verb="verb"),
                Place("place"),
                Lit("."),
            ),
        ),
        Preset(
            id="setup",
            title="Завязка",
            slots=(
                ACTOR,
                SlotSpec("obj", "object"),
                SlotSpec("place", "place"),
                SlotSpec("feel", "feeling"),
            ),
            template=(
                Lit("Однажды"),
                Noun("actor", "nomn"),
                agree("находит", "находит", "находит", "находят"),
                Noun("obj", "accs"),
                Place("place"),
                Lit("—"),
                Lit("и с тех пор"),
                Noun("feel", "nomn"),
                Lit("не отпускает"),
                agree("его", "её", "его", "их"),
                Lit("."),
            ),
        ),
        Preset(
            id="logline",
            title="Логлайн",
            slots=(
                _adj("adj_actor", "actor"),
                ACTOR,
                SlotSpec("verb", "verb_perf"),
                SlotSpec("obj", "object"),
                SlotSpec("place", "place"),
            ),
            template=(
                Adj("adj_actor"),
                Noun("actor", "nomn"),
                agree("должен", "должна", "должно", "должны"),
                Verb("verb", "inf"),
                GovPrep("verb"),
                Noun("obj", GOVERNED, verb="verb"),
                Lit("до рассвета"),
                Lit(","),
                Lit("иначе"),
                Noun("place", "nomn"),
                Lit("исчезнет"),
                Lit("."),
            ),
        ),
    )
}

DEFAULT_PRESET = "plot"
