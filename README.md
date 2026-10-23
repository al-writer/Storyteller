# Генератор сюжетов

Веб-сервис «слот-машина смыслов»: собирает грамматически безупречные русские
фразы-сюжеты, даёт закреплять отдельные слова, делиться открыткой и копировать
фразу как промпт для LLM.

## Разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install
pytest
```

## Словарь

`data/lexicon.jsonl` собирается офлайн и коммитится — в рантайме он только читается.

| Ярус | Источник | Вес |
| --- | --- | --- |
| core | `data/curated/core/*.txt` — ручной отбор с разметкой тона, предлога, управления | 1.0 |
| extended | `data/curated/extended/*.txt` — размеченные кандидаты из частотного коридора Zipf 2.5–5.5 | 0.3 |
| wild | автоматически: леммы с Zipf 1.0–2.5, прошедшие грамматические фильтры | только в «Диком словаре» |

Формат строки курируемого списка: `лемма [предлог] [gov=падеж[:предлог]] [!loct] [@тон ...]`.

```bash
python tools/build_lexicon.py --strict                    # пересобрать data/lexicon.jsonl
python tools/build_lexicon.py --dump-candidates var/cand  # кандидаты на разметку
```

Сборке нужен `wordfreq` из `requirements-dev.txt`. Стоп-лист — `data/stoplist.txt`.

## Процесс

- Ветка `main` всегда рабочая; работа идёт в короткоживущих ветках
  `feat/…`, `fix/…`, `chore/…`, `docs/…`, `test/…`, `build/…`.
- Сообщения коммитов — [Conventional Commits](https://www.conventionalcommits.org/ru/v1.0.0/).
- Версии — [SemVer](https://semver.org/lang/ru/), единственный источник —
  `app/__init__.py`; релизы помечаются тегами `vX.Y.Z`, изменения — в `CHANGELOG.md`.
