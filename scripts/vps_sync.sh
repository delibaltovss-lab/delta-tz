#!/usr/bin/env bash
# Pull approved code from GitHub without starting the bot or trading.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Local tracked edits detected; refusing to overwrite: $(pwd)" >&2
  exit 1
fi
# Only fast-forward updates; never merge conflicting history automatically.
git pull --ff-only origin main
printf 'DELTA code synced at %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
