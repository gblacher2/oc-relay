#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
install_root="$HOME/.local"
library_dir="$install_root/lib/oc-relay"
bin_dir="$install_root/bin"
config_dir="$HOME/.config/oc-relay"
config_path="$config_dir/config.json"
launch_agents_dir="$HOME/Library/LaunchAgents"
logs_dir="$HOME/Library/Logs"
plist_path="$launch_agents_dir/com.gabrielblacher.oc-relay.plist"
log_path="$logs_dir/oc-relay.log"
program_path="$bin_dir/oc-relay"
domain="gui/$(id -u)"
label="com.gabrielblacher.oc-relay"

mkdir -p "$library_dir" "$bin_dir" "$config_dir" "$launch_agents_dir" "$logs_dir"
install -m 0644 "$repo_dir/src/oc_relay.py" "$library_dir/oc_relay.py"
install -m 0755 "$repo_dir/bin/oc-relay" "$program_path"

if [ ! -e "$config_path" ]; then
  cat >"$config_path" <<'JSON'
{
  "port": 8788,
  "target_url": "https://opencode.ai/zen/go/v1",
  "fallback_model": null
}
JSON
  chmod 0600 "$config_path"
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

escaped_program=$(escape_sed_replacement "$program_path")
escaped_log=$(escape_sed_replacement "$log_path")
sed \
  -e "s|__PROGRAM__|$escaped_program|g" \
  -e "s|__LOG_PATH__|$escaped_log|g" \
  "$repo_dir/launchd/com.gabrielblacher.oc-relay.plist.in" >"$plist_path"
chmod 0644 "$plist_path"
plutil -lint "$plist_path" >/dev/null

if launchctl print "$domain/$label" >/dev/null 2>&1; then
  launchctl bootout "$domain/$label"
fi
launchctl bootstrap "$domain" "$plist_path"

healthy=false
for _ in 1 2 3 4 5; do
  if "$program_path" status >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 1
done

if [ "$healthy" != true ]; then
  printf 'Installed, but the health check failed. Inspect with: %s status\n' "$program_path" >&2
  exit 1
fi

printf 'Installed and running.\n'
printf 'Command: %s\n' "$program_path"
printf 'Base URL: http://127.0.0.1:8788\n'
printf 'Config: %s\n' "$config_path"
printf 'Log: %s\n' "$log_path"
