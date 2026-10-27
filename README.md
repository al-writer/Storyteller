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
| `BASE_URL` | абсолютные ссылки в `og:`-тегах и шаринге | `https://$RAILWAY_PUBLIC_DOMAIN`, если он задан, иначе `http://localhost:8000` |
| `SECRET_SALT` | соль для хэша голосующих | генерируется при первом запуске и хранится в `DATA_DIR/secret_salt` |
| `DATA_DIR` | SQLite, соль и кэш открыток | `/data` (именованный том) |
| `PORT` | порт uvicorn в контейнере | `8000` |

Один контейнер, один воркер uvicorn: SQLite в WAL-режиме и лимиты запросов живут в
памяти одного процесса. Словарь загружается в базу при старте и перезагружается,
только если изменился `data/lexicon.jsonl`; фразы и лайки при этом сохраняются.

Контейнер стартует от root, отдаёт `DATA_DIR` пользователю `app` и запускает сервис
уже от него (`docker-entrypoint.sh`) — так том работает и на платформах, которые
монтируют его от root.

Чтобы превью открытки появилось в Telegram, `BASE_URL` должен быть публичным HTTPS-адресом.

## Деплой на Railway

Проект описан в `.railway/railway.ts`: сервис собирается из `Dockerfile` ветки `main`,
одна реплика, healthcheck `/healthz`, том `storyteller-data` в `/data`. Переменные
задавать не нужно: адрес берётся из `RAILWAY_PUBLIC_DOMAIN`, соль создаётся на томе.

Один раз, из корня репозитория (нужны Node.js 22+ и
[Railway CLI](https://docs.railway.com/cli)):

```bash
npm install                    # SDK для railway.ts
railway login
railway init -n storyteller    # новый пустой проект; для существующего — railway link
railway config apply           # создаст сервис и том
railway domain -s storyteller  # выдаст публичный домен *.up.railway.app
```

Дальше каждый пуш в `main` деплоится сам. Изменения в `.railway/railway.ts` применяются
повторным `railway config apply` (сначала можно посмотреть `railway config plan`).
Ресурсы проекта, которых нет в файле, `apply` удалит — добавляйте их в файл.

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
