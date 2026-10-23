# Технический долг

Известные недоделки и обходные решения, которые нужно закрыть. Закрытый пункт
удаляется из файла в том же коммите, что его исправляет.

## Нет тегов и релизов v0.1.0–v0.5.0 на GitHub

**Добавлено:** 2026-10-23 · **Приоритет:** низкий

**Состояние.** Workflow `Release` (`.github/workflows/release.yml`) поставил тег и
GitHub Release только для `v0.6.0`. Для `v0.1.0`–`v0.5.0` GitHub отвечает
`Resource not accessible by integration (HTTP 403)`. Поэтому последний запуск
`Release` красный: он специально завершается ошибкой со списком непроставленных
тегов. Ссылки сравнения версий в `CHANGELOG.md` для этих версий пока не открываются.

**Причина.** `GITHUB_TOKEN` не может создать ref на коммит, где файлы
`.github/workflows/*` отличаются от текущих в `main`. В релизах 0.1.0–0.5.0 `ci.yml`
ещё старый, без docker-задачи. Ограничение срабатывает и на `git push` тега, и на
создание тега через REST API. Новых релизов это не касается: в них workflow
совпадает с `main`.

**Как закрыть.** Подойдёт любой из вариантов ниже. В конце нужно убедиться,
что релизы `v0.1.0`–`v0.6.0` на месте и последний запуск `Release` зелёный, затем
удалить этот пункт.

### Вариант А. Через workflow `Release` с токеном

Нужен fine-grained PAT для `al-writer/Storyteller` с правами **Contents: write** и
**Workflows: write** (GitHub → Settings → Developer settings → Fine-grained tokens).
Workflow сам возьмёт секрет и пропустит теги, которые уже есть.

*Веб:*
1. Settings → Secrets and variables → Actions → New repository secret,
   имя `RELEASE_TOKEN`, значение — токен.
2. Actions → Release → Run workflow → ветка `main` → Run workflow.
3. После зелёного запуска удалить секрет там же.

*Терминал* (нужен [GitHub CLI](https://cli.github.com/), `gh auth login`):

```sh
gh secret set RELEASE_TOKEN -R al-writer/Storyteller   # вставить токен
gh workflow run release.yml -R al-writer/Storyteller --ref main
gh run watch -R al-writer/Storyteller \
  "$(gh run list -R al-writer/Storyteller -w release.yml -L 1 --json databaseId -q '.[0].databaseId')"
gh secret delete RELEASE_TOKEN -R al-writer/Storyteller
```

### Вариант Б. Вручную, без токена

Своими правами владельца репозитория ограничение `GITHUB_TOKEN` не действует.
Скрипт повторяет логику workflow: тег на каждый коммит `chore(release): X.Y.Z`
без тега, заметки — из секции CHANGELOG этой версии.

*Терминал* (нужны `git` и `gh`, запускать из корня клона):

```sh
git fetch origin main --tags
git log --reverse --first-parent --format='%H %s' origin/main |
while read -r sha subject; do
  case "$subject" in "chore(release): "*) ;; *) continue ;; esac
  version=${subject#"chore(release): "}; tag="v$version"
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null && continue
  notes=$(git show "$sha:CHANGELOG.md" | awk -v v="$version" '
    index($0, "## [" v "]") == 1 { on = 1; next }
    on && /^(## )?\[[^]]+\]/ { exit }
    on')
  printf '%s\n%s\n' "$tag" "$notes" | git tag -a "$tag" "$sha" -F -
  git push origin "refs/tags/$tag"
  gh release create "$tag" -R al-writer/Storyteller --verify-tag --title "$tag" --notes "$notes"
done
gh run rerun -R al-writer/Storyteller \
  "$(gh run list -R al-writer/Storyteller -w release.yml -L 1 --json databaseId -q '.[0].databaseId')"
```

Последняя команда перезапускает `Release`: он не найдёт пропущенных тегов и
станет зелёным.

*Веб:* для каждой версии из таблицы — Releases → Draft a new release → Choose a tag →
ввести имя тега → Target: нужный коммит (если его нет в списке, сначала поставить
тег из терминала, как выше) → заголовок `vX.Y.Z`, описание — секция версии из
`CHANGELOG.md` → Publish release. Затем Actions → Release → последний запуск →
Re-run all jobs.

| Тег | Коммит |
|---|---|
| `v0.1.0` | `0d07797` |
| `v0.2.0` | `4865153` |
| `v0.3.0` | `edca3ca` |
| `v0.4.0` | `c78e835` |
| `v0.5.0` | `29c6cdd` |

**Если старые теги не нужны:** убрать финальную ошибку в `release.yml` (оставить
предупреждение) и поправить ссылки в `CHANGELOG.md`.
