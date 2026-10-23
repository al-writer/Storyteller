"""Морфологический движок: подбор слов в слоты пресета и сборка согласованной фразы.

Фраза никогда не хранится строкой как источник истины: слот — это ID леммы и параметры
формы, текст каждый раз пересобирается из слотов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import partial

from app import morph
from app.lexicon import (
    RECENT_LIMIT,
    TONES,
    Lexicon,
    UsageTracker,
    Word,
    dominant_tone,
    suits_noun,
)
from app.presets import (
    GOVERNED,
    PRESETS,
    Adj,
    Agree,
    GovPrep,
    Lit,
    Noun,
    Place,
    Preset,
    Verb,
)

log = logging.getLogger(__name__)

TONE_PROBABILITY = 0.6
PLURAL_PROBABILITY = 0.1
SUPERLATIVE_PROBABILITY = 0.07
MAX_REPLACEMENTS = 5
SLUG_LENGTH = 8
BASE62 = string.digits + string.ascii_letters
ATTACHED_PUNCTUATION = frozenset({",", "."})


class GenerationError(Exception):
    pass


class RenderError(Exception):
    def __init__(self, slot_index: int, lemma: str, detail: str):
        super().__init__(f"слот {slot_index} ({lemma}): {detail}")
        self.slot_index = slot_index
        self.lemma = lemma


@dataclass(frozen=True)
class Slot:
    """Содержимое слота: лемма и выбранные параметры формы."""

    id: int
    number: str = "sing"
    gender: str | None = None  # род, выбранный для слов общего рода («сирота»)
    superlative: bool = False

    def key(self) -> list:
        return [self.id, self.number, self.gender or "", int(self.superlative)]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "number": self.number,
            "gender": self.gender,
            "superlative": self.superlative,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Slot:
        number = data.get("number", "sing")
        gender = data.get("gender")
        return cls(
            id=int(data["id"]),
            number=number if number in ("sing", "plur") else "sing",
            gender=gender if gender in ("masc", "femn") else None,
            superlative=bool(data.get("superlative", False)),
        )


@dataclass(frozen=True)
class Token:
    text: str
    slot: int | None = None


@dataclass(frozen=True)
class Inflection:
    """Что движок запросил у морфологии для слова слота — для проверок и отладки."""

    slot: int
    lemma: str
    pos: str
    grammemes: frozenset[str]
    text: str


@dataclass
class Phrase:
    preset: Preset
    slots: list[Slot]
    words: list[Word]
    tokens: list[Token]
    text: str
    tone: str | None = None
    inflections: list[Inflection] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return make_slug(self.preset.id, self.slots)

    @property
    def card_tone(self) -> str | None:
        return dominant_tone(self.words)

    def key(self) -> dict:
        return phrase_key(self.preset.id, self.slots)


def phrase_key(preset_id: str, slots: Sequence[Slot]) -> dict:
    return {"preset": preset_id, "slots": [slot.key() for slot in slots]}


def canonical_key(preset_id: str, slots: Sequence[Slot]) -> str:
    return json.dumps(phrase_key(preset_id, slots), separators=(",", ":"), ensure_ascii=False)


def base62(number: int) -> str:
    digits = []
    while number:
        number, remainder = divmod(number, 62)
        digits.append(BASE62[remainder])
    return "".join(reversed(digits)) or "0"


def make_slug(preset_id: str, slots: Sequence[Slot]) -> str:
    digest = hashlib.sha256(canonical_key(preset_id, slots).encode()).digest()
    return base62(int.from_bytes(digest, "big"))[:SLUG_LENGTH]


def slots_from_key(key: dict) -> tuple[str, list[Slot]]:
    slots = [
        Slot(id=item[0], number=item[1], gender=item[2] or None, superlative=bool(item[3]))
        for item in key["slots"]
    ]
    return key["preset"], slots


# --- сборка фразы -------------------------------------------------------------------------


@dataclass
class _Bound:
    index: int
    slot: Slot
    word: Word


def _gender(bound: _Bound) -> str:
    if bound.word.gender == "ms-f":
        return bound.slot.gender or "masc"
    return bound.word.gender or "masc"


def _parse(bound: _Bound):
    word = bound.word
    pos = morph.CATEGORY_POS[word.category]
    parse = morph.lemma_parse(word.lemma, pos, word.animacy, word.aspect)
    if parse is None:
        raise RenderError(bound.index, word.lemma, "нет разбора")
    return parse


def _noun_cases(preset: Preset, bound: dict[str, _Bound]) -> dict[str, str]:
    cases = {}
    for token in preset.template:
        if isinstance(token, Noun):
            if token.case == GOVERNED:
                cases[token.slot] = bound[token.verb].word.governs or "accs"
            else:
                cases[token.slot] = token.case
    return cases


def adjective_form(
    adjective: Word,
    noun: Word,
    case: str,
    number: str = "sing",
    gender: str | None = None,
    superlative: bool = False,
) -> str | None:
    """Прилагательное, согласованное со своим существительным.

    Род — в единственном числе (для общего рода берётся ``gender``), число, падеж; в
    винительном мужского рода и множественного числа — одушевлённость существительного.
    """
    if gender is None:
        gender = "masc" if noun.gender in (None, "ms-f") else noun.gender
    animacy = noun.animacy or "inan"
    parse = morph.lemma_parse(adjective.lemma, "ADJF")
    if parse is None:
        return None
    text = morph.adjective(parse, case, number, gender, animacy)
    if text is None or not superlative:
        return text
    prefix = morph.adjective(morph.lemma_parse("самый", "ADJF"), case, number, gender, animacy)
    return f"{prefix} {text}" if prefix else None


def place_form(place: Word) -> str | None:
    """«в/на» + место в предложном падеже: «в лесу», «на почте», «во сне»."""
    parse = morph.lemma_parse(place.lemma, "NOUN", place.animacy)
    if parse is None:
        return None
    text = morph.noun_locative(parse, "sing", place.prefer_loct)
    if text is None:
        return None
    return f"{morph.preposition(place.prep or 'в', text)} {text}"


def render(lexicon: Lexicon, preset: Preset, slots: Sequence[Slot]) -> Phrase:
    if len(slots) != len(preset.slots):
        raise GenerationError("число слотов не совпадает с пресетом")
    bound: dict[str, _Bound] = {}
    for index, (spec, slot) in enumerate(zip(preset.slots, slots, strict=True)):
        word = lexicon.get(slot.id)
        if word is None or word.category != spec.category:
            raise RenderError(index, str(slot.id), "слово не найдено в категории")
        bound[spec.name] = _Bound(index, slot, word)

    cases = _noun_cases(preset, bound)
    inflections: list[Inflection] = []
    parts: list[Token | GovPrep] = []

    def noun_form(name: str, case: str) -> str:
        item = bound[name]
        number = item.slot.number if preset.spec(name).plural_ok else "sing"
        grammemes = {case, number}
        text = morph.noun(_parse(item), case, number)
        if text is None:
            raise RenderError(item.index, item.word.lemma, f"нет формы {case} {number}")
        inflections.append(
            Inflection(item.index, item.word.lemma, "NOUN", frozenset(grammemes), text)
        )
        return text

    for token in preset.template:
        if isinstance(token, Lit):
            parts.append(Token(token.text))
        elif isinstance(token, Noun):
            parts.append(Token(noun_form(token.slot, cases[token.slot]), bound[token.slot].index))
        elif isinstance(token, Adj):
            item = bound[token.slot]
            noun_name = preset.spec(token.slot).agree
            noun = bound[noun_name]
            noun_number = noun.slot.number if preset.spec(noun_name).plural_ok else "sing"
            case = cases[noun_name]
            gender = _gender(noun)
            text = adjective_form(
                item.word, noun.word, case, noun_number, gender, item.slot.superlative
            )
            if text is None:
                raise RenderError(item.index, item.word.lemma, f"нет формы {case}")
            grammemes = {case, noun_number} | ({gender} if noun_number == "sing" else set())
            inflections.append(
                Inflection(item.index, item.word.lemma, "ADJF", frozenset(grammemes), text)
            )
            parts.append(Token(text, item.index))
        elif isinstance(token, Verb):
            item = bound[token.slot]
            subject = bound[token.subject]
            parse = _parse(item)
            if token.form == "pres":
                text = morph.verb_present(parse, subject.slot.number)
            elif token.form == "past":
                text = morph.verb_past(parse, _gender(subject), subject.slot.number)
            else:
                text = parse.word
            if text is None:
                raise RenderError(item.index, item.word.lemma, f"нет формы {token.form}")
            parts.append(Token(text, item.index))
        elif isinstance(token, GovPrep):
            if bound[token.verb].word.governs_prep:
                parts.append(token)
        elif isinstance(token, Place):
            item = bound[token.slot]
            text = place_form(item.word)
            if text is None:
                raise RenderError(item.index, item.word.lemma, "нет предложного падежа")
            noun_text = text.split(" ", 1)[1]
            inflections.append(
                Inflection(
                    item.index, item.word.lemma, "NOUN", frozenset({"loct", "sing"}), noun_text
                )
            )
            parts.append(Token(text, item.index))
        elif isinstance(token, Agree):
            subject = bound[token.subject]
            parts.append(Token(token.pick(_gender(subject), subject.slot.number)))

    tokens: list[Token] = []
    for position, part in enumerate(parts):
        if isinstance(part, GovPrep):
            following = next(p for p in parts[position + 1 :] if isinstance(p, Token))
            prep = bound[part.verb].word.governs_prep
            tokens.append(Token(morph.preposition(prep, following.text.split()[0])))
        else:
            tokens.append(part)
    if tokens:
        first = tokens[0]
        tokens[0] = Token(first.text[:1].upper() + first.text[1:], first.slot)

    text = ""
    for token in tokens:
        if not text:
            text = token.text
        elif token.text in ATTACHED_PUNCTUATION:
            text += token.text
        else:
            text += " " + token.text

    words = [bound[spec.name].word for spec in preset.slots]
    return Phrase(preset, list(slots), words, tokens, text, inflections=inflections)


# --- генерация ----------------------------------------------------------------------------


class Engine:
    def __init__(self, lexicon: Lexicon, tracker: UsageTracker | None = None):
        self.lexicon = lexicon
        self.tracker = tracker

    def _valid_current(self, preset: Preset, current: Sequence[Slot] | None) -> list[Slot | None]:
        result: list[Slot | None] = [None] * len(preset.slots)
        if not current or len(current) != len(preset.slots):
            return result
        for index, (spec, slot) in enumerate(zip(preset.slots, current, strict=True)):
            word = self.lexicon.get(slot.id)
            if word is not None and word.category == spec.category:
                result[index] = slot
        return result

    def _new_slot(self, spec, word: Word, rng: random.Random) -> Slot:
        number = "sing"
        gender = None
        superlative = False
        if spec.plural_ok and rng.random() < PLURAL_PROBABILITY:
            number = "plur"
        if word.gender == "ms-f":
            gender = rng.choice(("masc", "femn"))
        if spec.superlative_ok and word.qual and rng.random() < SUPERLATIVE_PROBABILITY:
            superlative = True
        return Slot(word.id, number, gender, superlative)

    def generate(
        self,
        preset_id: str,
        *,
        current: Sequence[Slot] | None = None,
        locked: Iterable[int] = (),
        reroll: int | None = None,
        recent_ids: Iterable[int] = (),
        tone: str | None = None,
        wild: bool = False,
        seed: int | None = None,
    ) -> Phrase:
        preset = PRESETS.get(preset_id)
        if preset is None:
            raise GenerationError(f"неизвестный пресет {preset_id!r}")
        rng = random.Random(seed)
        tracker = self.tracker if seed is None else None

        existing = self._valid_current(preset, current)
        if reroll is not None and 0 <= reroll < len(preset.slots) and all(existing):
            keep = set(range(len(preset.slots))) - {reroll}
        else:
            keep = {i for i in locked if 0 <= i < len(preset.slots) and existing[i] is not None}
        slots: list[Slot | None] = [
            existing[i] if i in keep else None for i in range(len(preset.slots))
        ]

        kept_words = [self.lexicon.by_id[s.id] for s in slots if s is not None]
        phrase_tone = tone if tone in TONES else None
        if phrase_tone is None and rng.random() < TONE_PROBABILITY:
            kept_tones = sorted({t for w in kept_words for t in w.tones})
            phrase_tone = rng.choice(kept_tones or list(TONES))

        recent = frozenset(list(recent_ids)[-RECENT_LIMIT:])
        failed: set[int] = set()
        # Сначала существительные и глаголы, потом прилагательные — им нужен род своего слова.
        order = sorted(range(len(preset.slots)), key=lambda i: preset.slots[i].category == "adj")

        for _ in range(MAX_REPLACEMENTS * len(preset.slots)):
            used_ids = {s.id for s in slots if s is not None}
            used_lemmas = {self.lexicon.by_id[i].lemma for i in used_ids}
            for index in order:
                if slots[index] is not None:
                    continue
                spec = preset.slots[index]
                predicate = None
                if spec.agree:
                    noun_category = preset.spec(spec.agree).category
                    predicate = partial(suits_noun, noun_category=noun_category)
                options = dict(
                    tone=phrase_tone,
                    wild=wild,
                    exclude_lemmas=used_lemmas,
                    predicate=predicate,
                    tracker=tracker,
                )
                # Окно недавних слов — пожелание, а не запрет: если без них пул пуст, берём из всех.
                word = self.lexicon.sample(
                    spec.category, rng, exclude_ids=used_ids | recent | failed, **options
                ) or self.lexicon.sample(
                    spec.category, rng, exclude_ids=used_ids | failed, **options
                )
                if word is None:
                    raise GenerationError(f"пустой пул для категории {spec.category}")
                slots[index] = self._new_slot(spec, word, rng)
                used_ids.add(word.id)
                used_lemmas.add(word.lemma)
            try:
                phrase = render(self.lexicon, preset, slots)  # type: ignore[arg-type]
            except RenderError as error:
                log.warning("не склоняется, слово заменено: %s", error)
                failed.add(slots[error.slot_index].id)  # type: ignore[union-attr]
                slots[error.slot_index] = None
                keep.discard(error.slot_index)
                continue
            phrase.tone = phrase_tone
            if tracker is not None:
                tracker.record(s.id for i, s in enumerate(phrase.slots) if i not in keep)
            return phrase
        raise GenerationError("не удалось собрать фразу")


def main() -> None:  # pragma: no cover - ручной инструмент вычитки
    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Вывести случайные фразы для вычитки")
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--preset", choices=sorted(PRESETS), default=None)
    parser.add_argument("--wild", action="store_true")
    parser.add_argument("--tone", choices=TONES, default=None)
    args = parser.parse_args()
    engine = Engine(Lexicon.from_jsonl(get_settings().lexicon_path))
    presets = [args.preset] if args.preset else sorted(PRESETS)
    for number in range(args.sample):
        preset_id = presets[number % len(presets)]
        phrase = engine.generate(preset_id, seed=args.seed + number, wild=args.wild, tone=args.tone)
        print(f"{preset_id:9} {phrase.text}")


if __name__ == "__main__":
    main()
