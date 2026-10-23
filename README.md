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

## Процесс

- Ветка `main` всегда рабочая; работа идёт в короткоживущих ветках
  `feat/…`, `fix/…`, `chore/…`, `docs/…`, `test/…`, `build/…`.
- Сообщения коммитов — [Conventional Commits](https://www.conventionalcommits.org/ru/v1.0.0/).
- Версии — [SemVer](https://semver.org/lang/ru/), единственный источник —
  `app/__init__.py`; релизы помечаются тегами `vX.Y.Z`, изменения — в `CHANGELOG.md`.
