#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if matches=$(grep -RInE -e 'demo' -e 'example' -e 'owner' -e '/home/[a-z]' "$root_dir/packages" \
  --exclude-dir=node_modules --exclude-dir=dist-webapp --exclude=FORK_SOURCE); then
  printf 'forbidden personal data found:\n%s\n' "$matches" >&2
  exit 1
fi
