#!/usr/bin/env bash
#
# uninstall.sh — remove the obsidian-reminders-sync launchd agent.
# Your Markdown file and Reminders are left untouched.
#
set -euo pipefail

LABEL="com.github.obsidian-reminders-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

echo "Removed launchd agent ($LABEL)."
echo "State and logs (if you want them gone too):"
echo "  rm -f ~/.local/state/obsidian-reminders-sync/state.json"
echo "  rm -f ~/Library/Logs/obsidian-reminders-sync.log"
