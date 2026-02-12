#!/bin/sh
if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail

OUT="${HOME}/images.txt"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "Ошибка: запусти скрипт внутри git-репозитория." >&2
  exit 1
}

normalize() {
  printf '%s' "$1" | sed -e 's/^[[:space:]]*//' \
                      -e 's/[[:space:]]*$//' \
                      -e 's/^[`"'\''(]*//' \
                      -e 's/[`"'\''),;]*$//' \
                      -e 's/\\$//'
}

is_valid_image() {
  local t="$1"
  [[ -n "$t" ]] || return 1
  [[ "$t" != "&&" && "$t" != "||" && "$t" != ";" ]] || return 1
  [[ "$t" != -* ]] || return 1
  [[ "$t" != *'$'* ]] || return 1   # выкидываем $VAR / ${VAR}
  [[ "$t" =~ ^[A-Za-z0-9] ]] || return 1
  [[ "$t" =~ [A-Za-z0-9] ]] || return 1
  return 0
}

echo "[1/3] Fetch: скачиваю все ветки со всех remotes..."
mapfile -t REMOTES < <(git remote)
((${#REMOTES[@]})) || { echo "Ошибка: remotes не настроены." >&2; exit 1; }

retry_fetch() {
  local remote="$1"
  local refspec="+refs/heads/*:refs/remotes/${remote}/*"
  local i
  for i in 1 2 3 4; do
    if git -c http.lowSpeedLimit=0 -c http.lowSpeedTime=999999 \
         fetch --prune --no-tags "$remote" "$refspec"; then
      return 0
    fi
    echo "  ! fetch для '$remote' не удался (попытка ${i}/4)" >&2
    sleep $((i*2))
  done
  return 1
}

for r in "${REMOTES[@]}"; do
  echo "  - remote: $r"
  retry_fetch "$r" || {
    echo "Ошибка: не удалось скачать ветки с '$r'. Прерываю, чтобы не делать неполный результат." >&2
    exit 2
  }
done

echo "[2/3] Собираю вершины веток (локальные + remote-tracking)..."
COMMITS=$(
  git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/remotes \
  | awk '$1 !~ /\/HEAD$/ {print $2}' \
  | sort -u
)

echo "[3/3] Ищу образы (Dockerfile* / *.yml / docker pull)..."

: >"$TMP"

while IFS= read -r commit; do
  [[ -n "$commit" ]] || continue

  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    git show "${commit}:${path}" 2>/dev/null | awk '
      /^[ \t]*[Ff][Rr][Oo][Mm][ \t]+/{
        s=$0
        sub(/^[ \t]*[Ff][Rr][Oo][Mm][ \t]+/, "", s)
        while (s ~ /^[ \t]*--[^ \t]+[ \t]+/) sub(/^[ \t]*--[^ \t]+[ \t]+/, "", s)
        split(s,a,/[ \t]+/)
        print a[1]
      }' | while IFS= read -r img; do
        img="$(normalize "$img")"
        if is_valid_image "$img"; then
          printf '%s\n' "$img" >>"$TMP"
        fi
      done
  done < <(git ls-tree -r --name-only "$commit" | grep -iE '(^|/)dockerfile([._-].*)?$' || true)

  (
    git grep -nI --no-color -i -E '^[[:space:]-]*image[[:space:]]*:[[:space:]]*' "$commit" -- '*.yml' 2>/dev/null || true
  ) | awk '
      {
        m = match($0, /:[0-9]+:/)
        s = (m ? substr($0, RSTART + RLENGTH) : $0)
        sub(/#.*/, "", s)
        sub(/.*image[ \t]*:[ \t]*/, "", s)
        gsub(/^[ \t]+|[ \t]+$/, "", s)
        split(s,a,/[ \t]+/)
        print a[1]
      }' | while IFS= read -r img; do
        img="$(normalize "$img")"
        if is_valid_image "$img"; then
          printf '%s\n' "$img" >>"$TMP"
        fi
      done

  (
    git grep -nI --no-color -E '(^|[[:space:]])docker([[:space:]]+image)?[[:space:]]+pull[[:space:]]+' "$commit" 2>/dev/null || true
  ) | awk '
      {
        m = match($0, /:[0-9]+:/)
        s = (m ? substr($0, RSTART + RLENGTH) : $0)
        sub(/#.*/, "", s)
        sub(/.*docker([ \t]+image)?[ \t]+pull[ \t]+/, "", s)
        gsub(/^[ \t]+|[ \t]+$/, "", s)
        n=split(s,a,/[ \t]+/)
        for (i=1;i<=n;i++){
          if (a[i] ~ /^-/) continue
          if (a[i] == "&&" || a[i] == "||" || a[i] == ";") continue
          if (a[i] == "\\") continue
          print a[i]
        }
      }' | while IFS= read -r img; do
        img="$(normalize "$img")"
        if is_valid_image "$img"; then
          printf '%s\n' "$img" >>"$TMP"
        fi
      done

done <<<"$COMMITS"

LC_ALL=C sort -u "$TMP" > "$OUT"
echo "Готово: $(wc -l < "$OUT") уникальных образов -> $OUT"
