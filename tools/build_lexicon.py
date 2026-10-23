"""Офлайн-сборка словаря: курируемые списки + wordfreq + pymorphy3 → data/lexicon.jsonl.

Ярусы:
  core      — data/curated/core/*.txt, ручной отбор;
  extended  — data/curated/extended/*.txt, размеченные кандидаты из частотного коридора;
  wild      — автоматически: редкие (Zipf < 2.5) леммы, прошедшие грамматические фильтры.

Запуск:
  python tools/build_lexicon.py                     # собрать data/lexicon.jsonl
  python tools/build_lexicon.py --dump-candidates var/candidates
                                                    # выгрузить неразмеченных кандидатов
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import morph  # noqa: E402

CURATED = ROOT / "data" / "curated"
STOPLIST = ROOT / "data" / "stoplist.txt"
OUT = ROOT / "data" / "lexicon.jsonl"

CATEGORIES = tuple(morph.CATEGORY_POS)
TONES = frozenset({"dark", "fairy", "cyber", "city", "myth"})
GOVERNS = frozenset({"accs", "gent", "datv", "ablt", "loct"})
PLACE_PREPS = frozenset({"в", "на"})
ADJ_USE_MARKS = frozenset({"+feel", "-actor", "-object"})
ZIPF_MIN, ZIPF_MAX = 2.5, 5.5
TOP_N = 100_000
WILD_TOP_N = 400_000
WILD_ZIPF_MIN = 1.0
# Относительные прилагательные от топонимов и терминов: в диком ярусе дают шум.
RELATIONAL = ("ский", "цкий")
# Суффиксы отвлечённых существительных: такие слова не годятся в «предметы».
ABSTRACT_SUFFIXES = ("ние", "ость", "есть", "ция", "изм", "ство", "тие", "ура", "ика")


@dataclass
class Entry:
    lemma: str
    category: str
    tier: str
    governs: str | None = None
    governs_prep: str | None = None
    prep: str | None = None
    prefer_loct: bool = False
    tones: list[str] = field(default_factory=list)
    uses: set[str] = field(default_factory=set)  # метки применимости прилагательного
    source: str = ""


class Stoplist:
    def __init__(self, path: Path) -> None:
        self.exact: set[str] = set()
        self.patterns: list[re.Pattern[str]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("~"):
                self.patterns.append(re.compile(line[1:]))
            else:
                self.exact.add(morph.norm(line))

    def __contains__(self, lemma: str) -> bool:
        key = morph.norm(lemma)
        return key in self.exact or any(p.search(key) for p in self.patterns)


def parse_line(line: str, category: str, tier: str, source: str) -> Entry:
    parts = line.split()
    entry = Entry(lemma=parts[0], category=category, tier=tier, source=source)
    for token in parts[1:]:
        if token.startswith("@"):
            tone = token[1:]
            if tone not in TONES:
                raise ValueError(f"{source}: неизвестный тон {tone!r}")
            entry.tones.append(tone)
        elif token.startswith("gov="):
            case, _, prep = token[4:].partition(":")
            if case not in GOVERNS:
                raise ValueError(f"{source}: неизвестный падеж управления {case!r}")
            entry.governs, entry.governs_prep = case, prep or None
        elif token == "!loct":
            entry.prefer_loct = True
        elif token in ADJ_USE_MARKS and category == "adj":
            entry.uses.add(token)
        elif token in PLACE_PREPS and category == "place":
            entry.prep = token
        else:
            raise ValueError(f"{source}: непонятный токен {token!r}")
    if category == "place" and not entry.prep:
        raise ValueError(f"{source}: у места не указан предлог")
    return entry


def read_curated(tier: str) -> list[Entry]:
    entries = []
    directory = CURATED / tier
    for category in CATEGORIES:
        path = directory / f"{category}.txt"
        if not path.exists():
            continue
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if line:
                entries.append(parse_line(line, category, tier, f"{path.name}:{number}"))
    return entries


def word_id(category: str, lemma: str, governs: str | None, governs_prep: str | None) -> int:
    key = f"{category}:{morph.norm(lemma)}:{governs or ''}:{governs_prep or ''}"
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def resolve(entry: Entry, zipf_of) -> tuple[dict | None, str | None]:
    pos = morph.CATEGORY_POS[entry.category]
    animacy = aspect = None
    if entry.category == "actor":
        animacy = "anim"
    elif pos == "NOUN":
        animacy = "inan"
    elif entry.category == "verb_impf":
        aspect = "impf"
    elif entry.category == "verb_perf":
        aspect = "perf"
    parse = morph.lemma_parse(entry.lemma, pos, animacy, aspect)
    if parse is None:
        return None, f"нет разбора {pos} {animacy or aspect or ''}".strip()
    bad = parse.tag.grammemes & morph.BAD_GRAMMEMES
    if bad:
        return None, f"пометы {','.join(sorted(bad))}"
    reason = morph.missing_form(entry.category, parse, entry.prefer_loct)
    if reason:
        return None, reason

    governs, governs_prep = entry.governs, entry.governs_prep
    transitive = None
    if pos == "INFN":
        transitive = parse.tag.transitivity == "tran"
        if governs is None:
            if not transitive:
                return None, "непереходный глагол без управления"
            governs = "accs"
    # Атрибуты тега pymorphy3 — не строки: приводим, чтобы сравнения и JSON были обычными.
    # Общий род («сирота») pymorphy3 не отдаёт в tag.gender — только граммемой ms-f.
    gender = None
    if pos == "NOUN":
        gender = "ms-f" if "ms-f" in parse.tag else str(parse.tag.gender)
    record = {
        "id": word_id(entry.category, parse.word, governs, governs_prep),
        "lemma": parse.word,
        "category": entry.category,
        "tier": entry.tier,
        "gender": gender,
        "animacy": str(parse.tag.animacy) if pos == "NOUN" else None,
        "aspect": str(parse.tag.aspect) if pos == "INFN" else None,
        "transitive": transitive,
        "governs": governs,
        "governs_prep": governs_prep,
        "prep": entry.prep,
        "prefer_loct": entry.prefer_loct,
        "qual": morph.is_qualitative(parse) if pos == "ADJF" else None,
        "feel": "+feel" in entry.uses,
        "noactor": "-actor" in entry.uses,
        "noobject": "-object" in entry.uses,
        "tones": sorted(set(entry.tones)),
        "zipf": round(zipf_of(parse.word), 2),
    }
    return record, None


def wordfreq_lemmas(top_n: int) -> dict[tuple[str, str], tuple[str, float]]:
    """Леммы из частотного списка: (лемма, грамматическая группа) → (лемма, zipf леммы)."""
    from wordfreq import top_n_list, zipf_frequency

    found: dict[tuple[str, str], tuple[str, float]] = {}
    for word in top_n_list("ru", top_n):
        if not re.fullmatch(r"[а-яё]+", word):
            continue
        parse = morph.MORPH.parse(word)[0]
        tag = parse.tag
        if tag.POS not in ("NOUN", "ADJF", "INFN", "VERB"):
            continue
        if tag.grammemes & morph.BAD_GRAMMEMES or not morph.MORPH.word_is_known(parse.normal_form):
            continue
        lemma = parse.normal_form
        if tag.POS == "NOUN":
            group = f"noun-{tag.animacy}"
        elif tag.POS == "ADJF":
            group = "adj"
        else:
            group = f"verb-{tag.aspect}-{tag.transitivity}"
        key = (lemma, group)
        if key not in found:
            found[key] = (lemma, zipf_frequency(lemma, "ru"))
    return found


def auto_category(lemma: str, group: str) -> str | None:
    if group == "noun-anim":
        return "actor"
    if group == "noun-inan":
        return None if lemma.endswith(ABSTRACT_SUFFIXES) else "object"
    if group == "adj":
        return "adj"
    if group == "verb-impf-tran":
        return "verb_impf"
    if group == "verb-perf-tran":
        return "verb_perf"
    return None


def dump_candidates(directory: Path, stoplist: Stoplist, known: set[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[tuple[float, str]]] = {}
    for (lemma, group), (_, zipf) in wordfreq_lemmas(TOP_N).items():
        if not ZIPF_MIN <= zipf <= ZIPF_MAX or lemma in stoplist or morph.norm(lemma) in known:
            continue
        groups.setdefault(group, []).append((zipf, lemma))
    for group, items in sorted(groups.items()):
        items.sort(key=lambda item: (-item[0], item[1]))
        path = directory / f"{group}.txt"
        path.write_text("".join(f"{lemma}\t{zipf:.2f}\n" for zipf, lemma in items), "utf-8")
        print(f"{path}: {len(items)}")


def build(args: argparse.Namespace) -> int:
    from wordfreq import zipf_frequency

    def zipf_of(lemma: str) -> float:
        return zipf_frequency(lemma, "ru")

    stoplist = Stoplist(STOPLIST)
    curated = read_curated("core") + read_curated("extended")
    known = {morph.norm(e.lemma) for e in curated}

    if args.dump_candidates:
        dump_candidates(Path(args.dump_candidates), stoplist, known)
        return 0

    records: dict[int, dict] = {}
    problems: list[str] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()

    def add(entry: Entry, quiet: bool = False) -> None:
        if entry.lemma in stoplist:
            if not quiet:
                problems.append(f"{entry.source}: {entry.lemma} — в стоп-листе")
            return
        record, reason = resolve(entry, zipf_of)
        if record is None:
            if not quiet:
                problems.append(f"{entry.source}: {entry.lemma} — {reason}")
            return
        if entry.tier == "extended" and record["zipf"] < ZIPF_MIN:
            record["tier"] = "wild"
        key = (record["category"], morph.norm(record["lemma"]), record["governs"],
               record["governs_prep"])  # fmt: skip
        if key in seen:
            return
        seen.add(key)
        if record["id"] in records:
            raise SystemExit(f"коллизия id: {record['lemma']} и {records[record['id']]['lemma']}")
        records[record["id"]] = record

    for entry in curated:
        add(entry)

    if not args.no_wild:
        for (lemma, group), (_, zipf) in sorted(wordfreq_lemmas(WILD_TOP_N).items()):
            category = auto_category(lemma, group)
            if category is None or not WILD_ZIPF_MIN <= zipf < ZIPF_MIN:
                continue
            if morph.norm(lemma) in known or (category == "adj" and lemma.endswith(RELATIONAL)):
                continue
            add(Entry(lemma=lemma, category=category, tier="wild", source="wordfreq"), True)

    ordered = sorted(
        records.values(),
        key=lambda r: (r["category"], r["tier"], morph.norm(r["lemma"]), r["governs"] or "",
                       r["governs_prep"] or ""),
    )  # fmt: skip
    with OUT.open("w", encoding="utf-8") as fh:
        for record in ordered:
            # Пустые поля не пишем: файл в репозитории и так большой.
            compact = {k: v for k, v in record.items() if v not in (None, False, [])}
            fh.write(json.dumps(compact, ensure_ascii=False, sort_keys=True) + "\n")

    stats = Counter((r["category"], r["tier"]) for r in ordered)
    for (category, tier), count in sorted(stats.items()):
        print(f"{category:10} {tier:9} {count}")
    print(f"всего: {len(ordered)} → {OUT.relative_to(ROOT)}")
    if problems:
        print(f"\nотброшено из курируемых списков: {len(problems)}", file=sys.stderr)
        for line in problems:
            print("  " + line, file=sys.stderr)
    return 1 if problems and args.strict else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump-candidates", metavar="DIR", help="выгрузить кандидатов на разметку")
    parser.add_argument("--no-wild", action="store_true", help="не собирать дикий ярус")
    parser.add_argument(
        "--strict", action="store_true", help="ошибка, если курируемые леммы отброшены"
    )
    return build(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
