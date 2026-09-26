#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
cd "$repo_dir"

python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
fi

if command -v plutil >/dev/null 2>&1; then
  rendered=$(mktemp "${TMPDIR:-/tmp}/oc-relay-plist.XXXXXX")
  trap 'rm -f "$rendered"' EXIT
  sed \
    -e 's|__PROGRAM__|/tmp/oc-relay|g' \
    -e 's|__LOG_PATH__|/tmp/oc-relay.log|g' \
    launchd/com.gabrielblacher.oc-relay.plist.in >"$rendered"
  plutil -lint "$rendered" >/dev/null
fi

if grep -R -n -E '0\.0\.0\.0|log[^\n]*(Authorization|headers|body)' src bin scripts launchd; then
  printf 'Unsafe bind address or logging pattern found.\n' >&2
  exit 1
fi

printf 'All checks passed.\n'
