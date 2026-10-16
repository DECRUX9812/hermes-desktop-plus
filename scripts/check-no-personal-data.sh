#!/usr/bin/env bash
set -euo pipefail

# Strict zero-personal-data guard over every tracked file in the repo.
#
# The forbidden identifiers are machine- and owner-specific, so they are NOT
# listed here: they live in a gitignored pattern file (one `REGEX[=>REPLACEMENT]`
# per line, the same format scripts/sync-ui-from-fork.sh reads). The replacement
# is ignored here. Point HERMES_PPD_PATTERNS at a different file to override the
# location. The generic lowercase-`/home/<user>` pattern is always enforced, so
# the guard still does useful work when the pattern file is absent.

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root_dir"
patterns_file=${HERMES_PPD_PATTERNS:-"$root_dir/.personal-data-patterns"}

# Public identifiers that legitimately contain an owner pattern. A matching line
# passes when it still matches after these substrings are removed.
allowlist=(
  'DECRUX9812/hermes-desktop-plus'
  '60716910+DECRUX9812@users.noreply.github.com'
  'favoownerortcut'
)

failed=0
rule_index=0

scan_rule() {
  local pattern=$1
  shift
  local hits hit file rest lineno content residue
  hits=$(git ls-files -z \
    | grep -zvE '(^|/)\.oc/|(^|/)node_modules/|(^|/)dist-webapp/|(^|/)FORK_SOURCE$' \
    | xargs -0 -r grep -InH "$@" -e "$pattern" -- || true)
  while IFS= read -r hit; do
    [[ -z "$hit" ]] && continue
    file=${hit%%:*}
    rest=${hit#*:}
    lineno=${rest%%:*}
    content=${rest#*:}
    residue=$content
    for allowed in "${allowlist[@]}"; do
      residue=${residue//"$allowed"/}
    done
    if grep -q "$@" -e "$pattern" <<< "$residue"; then
      printf 'forbidden personal data: %s:%s (pattern #%s)\n' "$file" "$lineno" "$rule_index" >&2
      failed=1
    fi
  done <<< "$hits"
}

rule_index=1
scan_rule '/home/[a-z]'

if [[ -f "$patterns_file" ]]; then
  while IFS= read -r line; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    pattern=${line%%=>*}
    rule_index=$((rule_index + 1))
    rc=0
    grep -E -e "$pattern" < /dev/null > /dev/null 2>&1 || rc=$?
    if ((rc == 2)); then
      printf 'invalid regex in patterns file (pattern #%s)\n' "$rule_index" >&2
      exit 2
    fi
    scan_rule "$pattern" -i
  done < "$patterns_file"
fi

if ((failed)); then
  exit 1
fi
