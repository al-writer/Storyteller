#!/usr/bin/env bash
# Ставит аннотированные теги vX.Y.Z и GitHub Releases на коммиты
# «chore(release): X.Y.Z» в origin/main, у которых ещё нет тега, — то же, что
# workflow Release, но с правами владельца репозитория. Нужен для релизов, где
# файлы .github/workflows отличаются от текущих: их GITHUB_TOKEN не тегирует.
#
# Запуск из корня клона (на Windows — через Git Bash, не WSL:
#   "C:\Program Files\Git\bin\bash.exe" tools/backfill_release_tags.sh):
#   bash tools/backfill_release_tags.sh
# Нужны git и gh (gh auth login; gh auth refresh -s workflow; gh auth setup-git).

set -euo pipefail

repo=al-writer/Storyteller

command -v gh >/dev/null || { echo "Не найден gh: https://cli.github.com/" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Сначала выполните: gh auth login" >&2; exit 1; }

git fetch origin main --tags

created=0
while read -r sha subject; do
  [[ "$subject" =~ ^chore\(release\):\ ([0-9]+\.[0-9]+\.[0-9]+)$ ]] || continue
  version="${BASH_REMATCH[1]}"
  tag="v$version"
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "$tag уже есть — пропускаю"
    continue
  fi
  notes=$(git show "$sha:CHANGELOG.md" | awk -v v="$version" '
    index($0, "## [" v "]") == 1 { on = 1; next }
    on && /^(## )?\[[^]]+\]/ { exit }
    on')
  if [[ -z "${notes//[[:space:]]/}" ]]; then
    echo "$sha: в CHANGELOG.md нет секции [$version]" >&2
    exit 1
  fi
  echo "$tag → ${sha:0:7}"
  printf '%s\n%s\n' "$tag" "$notes" | git tag -a "$tag" "$sha" -F -
  git push origin "refs/tags/$tag"
  gh release view "$tag" -R "$repo" >/dev/null 2>&1 ||
    gh release create "$tag" -R "$repo" --verify-tag --title "$tag" --notes "$notes"
  created=$((created + 1))
done < <(git log --reverse --first-parent --format='%H %s' origin/main)

echo "Поставлено тегов: $created"

if (( created )); then
  run_id=$(gh run list -R "$repo" -w release.yml -L 1 --json databaseId -q '.[0].databaseId')
  if [[ -n "$run_id" ]]; then
    gh run rerun -R "$repo" "$run_id"
    echo "Перезапущен Release: https://github.com/$repo/actions/runs/$run_id"
  fi
fi
