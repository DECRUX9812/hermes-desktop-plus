#!/usr/bin/env bash
set -euo pipefail

# Strict zero-personal-data guard over packages/.
#
# The forbidden identifiers are machine- and owner-specific, so they are NOT
# listed here: they live in a gitignored pattern file (one regex per line).
# Point HERMES_PPD_PATTERNS at a different file to override the location.
# The generic lowercase-`/home/<user>` pattern is always enforced, so the
# guard still does useful work when the pattern file is absent.

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
patterns_file=${HERMES_PPD_PATTERNS:-"$root_dir/.personal-data-patterns"}

patterns=(-e '/home/[a-z]')
if [[ -f "$patterns_file" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    patterns+=(-e "$line")
  done < "$patterns_file"
fi

if matches=$(grep -RInE "${patterns[@]}" "$root_dir/packages" \
  --exclude-dir=node_modules --exclude-dir=dist-webapp --exclude=FORK_SOURCE); then
  printf 'forbidden personal data found:\n%s\n' "$matches" >&2
  exit 1
fi
