"""Морфология: выбор разбора леммы, склонение, формы предлогов.

Обёртка над pymorphy3, общая для рантайма (движок) и офлайн-сборки словаря.
"""

from __future__ import annotations

from functools import lru_cache

import pymorphy3

MORPH = pymorphy3.MorphAnalyzer()

CATEGORY_POS: dict[str, str] = {
    "actor": "NOUN",
    "object": "NOUN",
    "place": "NOUN",
    "feeling": "NOUN",
    "adj": "ADJF",
    "verb_impf": "INFN",
    "verb_perf": "INFN",
}

# Леммы с этими пометами в словарь не попадают.
BAD_GRAMMEMES = frozenset(
    {"Name", "Surn", "Patr", "Geox", "Orgn", "Trad", "Abbr", "Init", "Arch", "Erro", "Dist",
     "Fixd", "Pltm"}
)  # fmt: skip

CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct")
GENDERS = ("masc", "femn", "neut")
CONSONANTS = frozenset("бвгджзйклмнпрстфхцчшщ")
VOWELS_FOR_OB = frozenset("аиоуэ")

# «во» перед словами, которые не подпадают под правило «в/ф + согласная».
VO_EXCEPTIONS = frozenset(
    {"сне", "рту", "мгле", "тьме", "дворе", "дворце", "льду", "лбу", "мху", "рву", "ржи", "всём", "многом"}
)


def norm(text: str) -> str:
    return text.replace("ё", "е").replace("Ё", "Е")


@lru_cache(maxsize=65536)
def lemma_parse(
    lemma: str, pos: str, animacy: str | None = None, aspect: str | None = None
) -> pymorphy3.analyzer.Parse | None:
    """Разбор начальной формы леммы с нужной частью речи.

    Берётся разбор самого слова в начальной форме (им. ед. / инфинитив), а не
    ``normalized``: так варианты вроде «счастье»/«счастие» склоняются от написанной формы.
    Для прилагательных подходят и причастия («заброшенный»). Предпочтение — разборам без
    помет из BAD_GRAMMEMES и с запрошенной одушевлённостью; вид глагола обязателен.
    """
    want = norm(lemma)
    candidates = []
    for index, parse in enumerate(MORPH.parse(lemma)):
        tag = parse.tag
        if norm(parse.word) != want:
            continue
        if pos == "NOUN":
            if tag.POS != "NOUN" or "nomn" not in tag:
                continue
        elif pos == "ADJF":
            if tag.POS not in ("ADJF", "PRTF") or not {"nomn", "masc", "sing"} <= tag.grammemes:
                continue
        elif pos != tag.POS:
            continue
        if aspect and tag.aspect != aspect:
            continue
        rank = (
            bool(tag.grammemes & BAD_GRAMMEMES),
            animacy is not None and tag.animacy != animacy,
            "sing" not in tag,
            index,
        )
        candidates.append((rank, parse))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def inflect(parse, grammemes: set[str]) -> str | None:
    result = parse.inflect(frozenset(grammemes))
    return result.word if result else None


def noun(parse, case: str, number: str = "sing") -> str | None:
    return inflect(parse, {case, number})


def noun_locative(parse, number: str = "sing", prefer_loct: bool = False) -> str | None:
    """Предложный падеж места: сначала loc2 («в лесу»), если он есть в словаре."""
    if not prefer_loct:
        result = parse.inflect(frozenset({"loc2", number}))
        if result is not None and "loc2" in result.tag:
            return result.word
    return noun(parse, "loct", number)


def has_loc2(parse) -> bool:
    result = parse.inflect(frozenset({"loc2", "sing"}))
    return result is not None and "loc2" in result.tag


def adjective(parse, case: str, number: str, gender: str, animacy: str) -> str | None:
    grammemes = {case, number}
    if number == "sing":
        grammemes.add(gender)
    if case == "accs" and (number == "plur" or gender == "masc"):
        grammemes.add(animacy)
    return inflect(parse, grammemes)


def is_qualitative(parse) -> bool:
    """Качественное прилагательное: есть сравнительная степень или помета Qual."""
    if "Qual" in parse.tag:
        return True
    return any(form.tag.POS == "COMP" for form in parse.lexeme)


def verb_present(parse, number: str) -> str | None:
    return inflect(parse, {"pres", "3per", number})


def verb_past(parse, gender: str, number: str) -> str | None:
    if number == "plur":
        return inflect(parse, {"past", "plur"})
    return inflect(parse, {"past", gender, "sing"})


def missing_form(category: str, parse, prefer_loct: bool = False) -> str | None:
    """Первая форма из нужных формулам, которую не удалось построить, или None."""
    if category in ("actor", "object", "place", "feeling"):
        cases = {"actor": ("nomn",), "feeling": ("nomn", "accs")}.get(category, CASES)
        for case in cases:
            if noun(parse, case) is None:
                return f"нет формы {case}"
        if category == "actor" and noun(parse, "nomn", "plur") is None:
            return "нет множественного числа"
        if category == "place" and noun_locative(parse, "sing", prefer_loct) is None:
            return "нет предложного падежа"
        return None
    if category == "adj":
        for case in CASES:
            for gender in GENDERS:
                for animacy in ("anim", "inan"):
                    if adjective(parse, case, "sing", gender, animacy) is None:
                        return f"нет формы {case} {gender} {animacy}"
            for animacy in ("anim", "inan"):
                if adjective(parse, case, "plur", "masc", animacy) is None:
                    return f"нет формы {case} plur {animacy}"
        return None
    if category == "verb_impf":
        for number in ("sing", "plur"):
            if verb_present(parse, number) is None:
                return f"нет настоящего времени ({number})"
        return None
    if category == "verb_perf":
        for gender in GENDERS:
            if verb_past(parse, gender, "sing") is None:
                return f"нет прошедшего времени ({gender})"
        if verb_past(parse, "masc", "plur") is None:
            return "нет прошедшего времени (plur)"
        return None
    return f"неизвестная категория {category}"


def preposition(prep: str, next_word: str) -> str:
    """Форма предлога перед словом: в/во, о/об, с/со, к/ко."""
    word = next_word.lower()
    first, second = (word[:1], word[1:2])
    second_is_consonant = second in CONSONANTS
    if prep == "в":
        if word in VO_EXCEPTIONS or (first in "вф" and second_is_consonant):
            return "во"
        return "в"
    if prep == "о":
        return "об" if first in VOWELS_FOR_OB else "о"
    if prep == "с":
        if first in "сзшж" and second_is_consonant:
            return "со"
        return "с"
    if prep == "к":
        return "ко" if word.startswith(("вт", "вс", "мн")) else "к"
    return prep
