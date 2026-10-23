# Генератор сюжетов

Веб-сервис «слот-машина смыслов»: собирает грамматически безупречные русские
фразы-сюжеты, даёт закреплять отдельные слова, делиться открыткой и копировать
фразу как промпт для LLM.

## Разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install
pytest                    # всё, включая приёмочные тесты (~1 мин)
pytest -m "not slow"      # без фазз-теста и прогона словаря
python -m app.engine --sample 100 --seed 1   # фразы для вычитки
```

## Запуск

```bash
docker compose up --build            # сервис на http://localhost:8000
docker compose run --rm --build tests  # все тесты внутри контейнера
```

Переменные окружения:

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `BASE_URL` | абсолютные ссылки в `og:`-тегах и шаринге | `http://localhost:8000` |
| `SECRET_SALT` | соль для хэша голосующих | `change-me` — замените в проде |
| `DATA_DIR` | SQLite и кэш открыток | `/data` (именованный том) |

Один контейнер, один воркер uvicorn: SQLite в WAL-режиме и лимиты запросов живут в
памяти одного процесса. Словарь загружается в базу при старте и перезагружается,
только если изменился `data/lexicon.jsonl`; фразы и лайки при этом сохраняются.

Чтобы превью открытки появилось в Telegram, `BASE_URL` должен быть публичным HTTPS-адресом.

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
  `feat/…`, `fix/…`, `chore/…`, `docs/…`, `test/…`, `build/…`, `ci/…`.
- Сообщения коммитов — [Conventional Commits](https://www.conventionalcommits.org/ru/v1.0.0/).
- Версии — [SemVer](https://semver.org/lang/ru/), единственный источник —
  `app/__init__.py`; изменения — в `CHANGELOG.md`.
- Релиз — коммит `chore(release): X.Y.Z` с новой версией и секцией в `CHANGELOG.md`.
  После зелёного CI workflow `Release` сам ставит аннотированный тег `vX.Y.Z` и
  создаёт GitHub Release с заметками из CHANGELOG; вручную теги не ставятся.
  Если в коммите релиза файлы `.github/workflows` отличаются от текущих, `GITHUB_TOKEN`
  тег поставить не может — нужен секрет `RELEASE_TOKEN` (fine-grained PAT с правами
  Contents и Workflows на запись), затем перезапуск `Release` вручную.
