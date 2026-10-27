#!/bin/sh
# Платформы вроде Railway монтируют том от root. Если контейнер запущен от root,
# отдаём каталог данных пользователю app и запускаем команду уже от него.
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # Рекурсивно — только когда владелец ещё не app: кэш открыток может быть большим.
    if [ "$(stat -c %U "$DATA_DIR")" != "app" ]; then
        chown -R app:app "$DATA_DIR"
    fi
    exec setpriv --reuid=app --regid=app --init-groups -- "$@"
fi

exec "$@"
