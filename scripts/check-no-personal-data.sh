#!/usr/bin/env bash
set -euo pipefail

# Strict zero-personal-data guard over every tracked file in the repo.
#
# The forbidden identifiers are machine- and owner-specific, so they are NOT
# listed here: they live in a gitignored pattern file (one regex per line).
# Point HERMES_PPD_PATTERNS at a different file to override the location.
# The generic lowercase-`/home/<user>` pattern is always enforced, so the
# guard still does useful work when the pattern file is absent.

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root_dir"
patterns_file=${HERMES_PPD_PATTERNS:-"$root_dir/.personal-data-patterns"}

patterns=(-e '/home/[a-z]')
if [[ -f "$patterns_file" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    patterns+=(-e "$line")
  done < "$patterns_file"
fi

# Every tracked file except generated or vendored trees, the fork-import
# marker, and the coordinator's .oc/ notes. grep -I skips binary files.
matches=$(git ls-files -z \
  | grep -zvE '(^|/)\.oc/|(^|/)node_modules/|(^|/)dist-webapp/|(^|/)FORK_SOURCE$' \
  | xargs -0 -r grep -InE "${patterns[@]}" -- || true)
if [[ -n "$matches" ]]; then
  printf 'forbidden personal data found:\n%s\n' "$matches" >&2
  exit 1
fi
