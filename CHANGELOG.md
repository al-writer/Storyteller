# Changelog

Все заметные изменения проекта фиксируются в этом файле.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [Semantic Versioning](https://semver.org/lang/ru/spec/v2.0.0.html).

## [Unreleased]

### Added

- Каркас репозитория: `pyproject.toml`, линтер ruff, pytest, pre-commit, CI.
- Морфологическая обёртка над pymorphy3: выбор разбора леммы, склонение,
  второй предложный падеж, формы предлогов «в/во», «о/об», «с/со», «к/ко».
- Курируемое ядро словаря (~2000 лемм в 7 категориях) с тонами, предлогами мест
  и управлением глаголов; стоп-лист.
- Офлайн-сборщик `tools/build_lexicon.py` и собранный `data/lexicon.jsonl`
  с ярусами core / extended / wild (~24 700 лемм).
