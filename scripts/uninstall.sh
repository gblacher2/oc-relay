#!/usr/bin/env bash
set -euo pipefail

purge=false
if [ "${1:-}" = "--purge" ]; then
  purge=true
elif [ "$#" -ne 0 ]; then
  printf 'Usage: %s [--purge]\n' "$0" >&2
  exit 2
fi

domain="gui/$(id -u)"
label="com.gabrielblacher.oc-relay"
plist_path="$HOME/Library/LaunchAgents/$label.plist"

if launchctl print "$domain/$label" >/dev/null 2>&1; then
  launchctl bootout "$domain/$label"
fi

rm -f "$plist_path" "$HOME/.local/bin/oc-relay" "$HOME/.local/lib/oc-relay/oc_relay.py"
rmdir "$HOME/.local/lib/oc-relay" 2>/dev/null || true

if [ "$purge" = true ]; then
  rm -f "$HOME/.config/oc-relay/config.json" "$HOME/Library/Logs/oc-relay.log"
  rmdir "$HOME/.config/oc-relay" 2>/dev/null || true
  printf 'Uninstalled oc-relay and removed its config and log.\n'
else
  printf 'Uninstalled oc-relay. Config and log were preserved.\n'
  printf 'Use %s --purge to remove them too.\n' "$0"
fi
