"""Словарь в памяти: пулы по категориям, веса, антиповтор, тематическая сцепка."""

from __future__ import annotations

import json
import random
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

TONES = ("dark", "fairy", "cyber", "city", "myth")
TIER_WEIGHTS = {"core": 1.0, "extended": 0.3, "wild": 0.3}
# Если в пуле тона после исключений осталось меньше слов, тон для этого слота снимается.
MIN_TONE_POOL = 8
RECENT_LIMIT = 40


@dataclass(frozen=True, slots=True)
class Word:
    id: int
    lemma: str
    category: str
    tier: str
    zipf: float = 0.0
    gender: str | None = None
    animacy: str | None = None
    aspect: str | None = None
    transitive: bool = False
    governs: str | None = None
    governs_prep: str | None = None
    prep: str | None = None
    prefer_loct: bool = False
    qual: bool = False
    feel: bool = False  # прилагательное годится к чувствам
    noactor: bool = False
    noobject: bool = False
    tones: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_record(cls, record: dict) -> Word:
        return cls(
            id=int(record["id"]),
            lemma=record["lemma"],
            category=record["category"],
            tier=record["tier"],
            zipf=float(record.get("zipf") or 0.0),
            gender=record.get("gender"),
            animacy=record.get("animacy"),
            aspect=record.get("aspect"),
            transitive=bool(record.get("transitive")),
            governs=record.get("governs"),
            governs_prep=record.get("governs_prep"),
            prep=record.get("prep"),
            prefer_loct=bool(record.get("prefer_loct")),
            qual=bool(record.get("qual")),
            feel=bool(record.get("feel")),
            noactor=bool(record.get("noactor")),
            noobject=bool(record.get("noobject")),
            tones=frozenset(record.get("tones") or ()),
        )

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "lemma": self.lemma,
            "category": self.category,
            "tier": self.tier,
            "zipf": self.zipf,
            "gender": self.gender,
            "animacy": self.animacy,
            "aspect": self.aspect,
            "transitive": self.transitive,
            "governs": self.governs,
            "governs_prep": self.governs_prep,
            "prep": self.prep,
            "prefer_loct": self.prefer_loct,
            "qual": self.qual,
            "feel": self.feel,
            "noactor": self.noactor,
            "noobject": self.noobject,
            "tones": sorted(self.tones),
        }


def suits_noun(adjective: Word, *, noun_category: str) -> bool:
    """Подходит ли прилагательное к существительному этой категории по меткам применимости."""
    if noun_category == "feeling":
        return adjective.feel
    if noun_category == "actor":
        return not adjective.noactor
    return not adjective.noobject


def frequency_factor(zipf: float) -> float:
    """Мягкая поправка на частотность: знакомые слова чуть вероятнее редких."""
    return 0.6 + 0.4 * min(max((zipf - 1.0) / 4.0, 0.0), 1.0)


class UsageTracker:
    """Счётчик выпадений за последние сутки (в памяти процесса) для штрафа часто мелькающих слов."""

    def __init__(self, window_seconds: float = 86_400, clock: Callable[[], float] = time.monotonic):
        self.window = window_seconds
        self.clock = clock
        self._events: deque[tuple[float, int]] = deque()
        self._counts: dict[int, int] = defaultdict(int)
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.window:
            _, word_id = self._events.popleft()
            self._counts[word_id] -= 1
            if self._counts[word_id] <= 0:
                del self._counts[word_id]

    def record(self, word_ids: Iterable[int]) -> None:
        now = self.clock()
        with self._lock:
            self._expire(now)
            for word_id in word_ids:
                self._events.append((now, word_id))
                self._counts[word_id] += 1

    def penalty(self, word_id: int) -> float:
        count = self._counts.get(word_id, 0)
        return 1.0 / (1.0 + count / 3.0)


class Lexicon:
    def __init__(self, words: Iterable[Word]):
        self.by_id: dict[int, Word] = {}
        self._pools: dict[str, list[Word]] = defaultdict(list)
        # Без дикого яруса — отдельные списки: это обычный режим, и он не должен
        # каждый раз фильтровать тысячи редких слов.
        self._tame: dict[str, list[Word]] = defaultdict(list)
        for word in sorted(words, key=lambda w: w.id):
            self.by_id[word.id] = word
            self._pools[word.category].append(word)
            if word.tier != "wild":
                self._tame[word.category].append(word)

    @classmethod
    def from_jsonl(cls, path: Path) -> Lexicon:
        with path.open(encoding="utf-8") as fh:
            return cls(Word.from_record(json.loads(line)) for line in fh if line.strip())

    def __len__(self) -> int:
        return len(self.by_id)

    def get(self, word_id: int) -> Word | None:
        return self.by_id.get(word_id)

    def pool(self, category: str, wild: bool = True) -> list[Word]:
        return (self._pools if wild else self._tame).get(category, [])

    def sample(
        self,
        category: str,
        rng: random.Random,
        *,
        tone: str | None = None,
        wild: bool = False,
        exclude_ids: frozenset[int] | set[int] = frozenset(),
        exclude_lemmas: frozenset[str] | set[str] = frozenset(),
        predicate: Callable[[Word], bool] | None = None,
        tracker: UsageTracker | None = None,
    ) -> Word | None:
        """Взвешенная выборка слова категории с учётом тона, исключений и штрафов."""

        def eligible(word: Word) -> bool:
            if word.id in exclude_ids or word.lemma in exclude_lemmas:
                return False
            return predicate is None or predicate(word)

        candidates = [w for w in self.pool(category, wild) if eligible(w)]
        if tone:
            toned = [w for w in candidates if tone in w.tones]
            if len(toned) >= MIN_TONE_POOL:
                candidates = toned
        if not candidates:
            return None
        weights = [
            TIER_WEIGHTS[w.tier]
            * frequency_factor(w.zipf)
            * (tracker.penalty(w.id) if tracker else 1.0)
            for w in candidates
        ]
        return rng.choices(candidates, weights=weights, k=1)[0]


def dominant_tone(words: Iterable[Word]) -> str | None:
    """Самый частый тон среди слов фразы (для акцентного цвета открытки)."""
    counts: dict[str, int] = defaultdict(int)
    for word in words:
        for tone in word.tones:
            counts[tone] += 1
    if not counts:
        return None
    return max(TONES, key=lambda tone: (counts.get(tone, 0), -TONES.index(tone)))
