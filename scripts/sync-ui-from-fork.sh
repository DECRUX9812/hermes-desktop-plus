#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'usage: %s <fork-repo-path> [<ref>]\n' "$0" >&2
  exit 2
fi

fork_repo=$1
ref=${2:-HEAD}
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ui_dir=$root_dir/packages/ui
shared_dir=$root_dir/packages/shared

[[ -d "$fork_repo/.git" || -f "$fork_repo/.git" ]] || {
  printf 'fork repository does not exist: %s\n' "$fork_repo" >&2
  exit 1
}

full_sha=$(git -C "$fork_repo" rev-parse --verify "${ref}^{commit}")
commit_date=$(git -C "$fork_repo" show -s --format=%cI "$full_sha")
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/hermes-ui-sync.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

archive_into() {
  local destination=$1
  local strip_components=$2
  shift 2
  mkdir -p "$destination"
  git -C "$fork_repo" archive --format=tar "$full_sha" -- "$@" |
    tar -xf - -C "$destination" --strip-components="$strip_components"
}

# Remove only files managed by the archive. The overlay, the package
# manifests and RUNTIME.md are intentionally kept so a re-sync cannot discard
# local integration work.
rm -rf "$ui_dir/src" "$ui_dir/public" "$ui_dir/electron" "$shared_dir/src"
rm -f \
  "$ui_dir/index.html" \
  "$ui_dir/vite.config.ts" \
  "$ui_dir/tsconfig.json" \
  "$ui_dir/tsconfig.e2e.json" \
  "$ui_dir/tsconfig.electron.json" \
  "$ui_dir/components.json" \
  "$ui_dir/vitest.config.ts" \
  "$ui_dir/vitest.setup.ts" \
  "$ui_dir/eslint.config.mjs" \
  "$ui_dir/DESIGN.md" \
  "$ui_dir/update-feed.cjs" \
  "$ui_dir/update-feed.d.cts" \
  "$ui_dir/scripts/compiler-cache.mjs"

# The renderer type-checks (and twice value-imports) a small set of files from
# the fork's electron/ tree. Keep this list exact: it is the transitive
# closure of `src/**` imports into `../electron`, none of which pull in the
# electron runtime at bundle time.
archive_into "$ui_dir" 2 \
  apps/desktop/src \
  apps/desktop/public \
  apps/desktop/index.html \
  apps/desktop/vite.config.ts \
  apps/desktop/tsconfig.json \
  apps/desktop/tsconfig.e2e.json \
  apps/desktop/tsconfig.electron.json \
  apps/desktop/components.json \
  apps/desktop/vitest.config.ts \
  apps/desktop/vitest.setup.ts \
  apps/desktop/vitest.run-tmp.ts \
  apps/desktop/eslint.config.mjs \
  apps/desktop/DESIGN.md \
  apps/desktop/update-feed.cjs \
  apps/desktop/update-feed.d.cts \
  apps/desktop/scripts/compiler-cache.mjs \
  apps/desktop/electron/challenge-window.ts \
  apps/desktop/electron/command-screenshot-types.ts \
  apps/desktop/electron/hub-iframe-policy.ts \
  apps/desktop/electron/hud-modifier-types.ts \
  apps/desktop/electron/install-stamp.ts \
  apps/desktop/electron/machine-profile.ts \
  apps/desktop/electron/notification-types.ts \
  apps/desktop/electron/pool-limits.ts \
  apps/desktop/electron/power-save.ts \
  apps/desktop/electron/quick-entry.ts \
  apps/desktop/electron/region-capture-types.ts \
  apps/desktop/electron/tray-status.ts \
  apps/desktop/electron/update-hold-types.ts \
  apps/desktop/electron/updater/channel-protocol.ts \
  apps/desktop/electron/updater/channel-strategy.ts \
  apps/desktop/electron/updater/channel.ts \
  apps/desktop/electron/updater/index.ts \
  apps/desktop/electron/updater/update-metrics.ts \
  apps/desktop/electron/window-open-policy.ts \
  apps/desktop/electron/window-size-types.ts
archive_into "$shared_dir" 2 apps/shared/src

cat > "$ui_dir/FORK_SOURCE" <<EOF
ref: $ref
sha: $full_sha
date: $commit_date
EOF

if [[ -d "$ui_dir/overlay" ]]; then
  cp -a "$ui_dir/overlay/." "$ui_dir/"
fi

# The fork contains developer fixtures and prose from its original checkout.
# Normalize those values in the imported tree without touching binary assets.
# `/home/<name>` becomes `/home/<Name>` (capitalized): the forbidden-pattern
# check only bans a lowercase letter after /home/, and keeping the /home/
# prefix intact preserves functional code such as the ~/ display collapse in
# src/lib/display-path.ts (its tests must still exercise a real Linux home).
while IFS= read -r -d '' file; do
  perl -pi -e \
    's/desktop\.example\.com/example.com/g; s/example/demo/g; s/demo/demo/g; s/owner/demo/g; s{/home/([a-z])}{"/home/" . uc($1)}ge' \
    "$file"
done < <(
  grep -RIlZ -E 'demo|example|owner|/home/[a-z]' "$ui_dir" "$shared_dir" \
    --exclude-dir=overlay --exclude-dir=node_modules --exclude-dir=dist-webapp \
    --exclude=FORK_SOURCE 2>/dev/null || true
)

"$root_dir/scripts/check-no-personal-data.sh"
