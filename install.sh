#!/usr/bin/env bash
#
# install.sh — set up obsidian-reminders-sync as a launchd agent.
#
# Usage:
#   ./install.sh "/full/path/to/your/vault/apple-reminders.md"
#   ./install.sh "~/Obsidian/Inbox/reminders.md" --interval 300
#
set -euo pipefail

LABEL="com.github.obsidian-reminders-sync"
INTERVAL=300
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/obsidian-reminders-sync.log"

# ---- parse args ------------------------------------------------------------
TARGET="${1:-}"
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  echo "Usage: ./install.sh \"/full/path/to/your/markdown-file.md\" [--interval SECONDS]" >&2
  exit 1
fi

# Expand ~ and make absolute
TARGET="${TARGET/#\~/$HOME}"
TARGET_DIR="$(cd "$(dirname "$TARGET")" 2>/dev/null && pwd || true)"
if [[ -z "$TARGET_DIR" ]]; then
  echo "ERROR: the folder for '$TARGET' does not exist. Create the vault/folder first." >&2
  exit 1
fi
TARGET="$TARGET_DIR/$(basename "$TARGET")"

PYTHON="$(command -v python3 || echo /usr/bin/python3)"

echo "→ Markdown file : $TARGET"
echo "→ python3       : $PYTHON"
echo "→ Interval      : ${INTERVAL}s"
echo "→ Agent label   : $LABEL"
echo

# ---- one-time Reminders permission prompt ----------------------------------
# Triggers macOS's "wants to control Reminders" Automation prompt. Click OK.
echo "→ Requesting Reminders access (approve the popup if one appears)…"
osascript -e 'tell application "Reminders" to count lists' >/dev/null 2>&1 || true

# ---- write the launchd plist ----------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/sync.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>OBSIDIAN_REMINDERS_FILE</key>
        <string>$TARGET</string>
    </dict>

    <key>StartInterval</key>
    <integer>$INTERVAL</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
</dict>
</plist>
EOF

echo "→ Wrote $PLIST"

# ---- (re)load the agent ----------------------------------------------------
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "→ Loaded launchd agent."

# ---- kick a first run and show the result ----------------------------------
echo "→ Running first sync (this can take 15-50s — iCloud)…"
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 45
echo
echo "===== last log lines ====="
tail -n 6 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
echo "=========================="
echo
echo "Done. It will sync every ${INTERVAL}s."
echo "If the log shows '0 incomplete items' but you have reminders, open"
echo "System Settings → Privacy & Security → Automation and enable Reminders"
echo "for the sync agent (or re-run this installer and approve the popup)."
