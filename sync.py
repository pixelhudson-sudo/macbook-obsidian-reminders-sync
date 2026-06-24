#!/usr/bin/env python3
"""
obsidian-reminders-sync — bidirectional sync between Apple Reminders and a
single Markdown file in an Obsidian vault (or any Markdown file).

Configuration is read from environment variables (set by the launchd plist that
install.sh generates):

    OBSIDIAN_REMINDERS_FILE   (required)  full path to the Markdown file to sync
    OBSIDIAN_SYNC_STATE_FILE  (optional)  default: ~/.local/state/obsidian-reminders-sync/state.json
    OBSIDIAN_SYNC_LOG_FILE    (optional)  default: ~/Library/Logs/obsidian-reminders-sync.log
    OBSIDIAN_SYNC_TIMEOUT     (optional)  per-osascript timeout in seconds (default 120)

Run it directly to sync once. See README.md for scheduling with launchd.
"""
import fcntl
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

OBSIDIAN_FILE = os.environ.get("OBSIDIAN_REMINDERS_FILE", "").strip()

STATE_FILE = (
    os.environ.get("OBSIDIAN_SYNC_STATE_FILE", "").strip()
    or os.path.expanduser("~/.local/state/obsidian-reminders-sync/state.json")
)
LOG_FILE = (
    os.environ.get("OBSIDIAN_SYNC_LOG_FILE", "").strip()
    or os.path.expanduser("~/Library/Logs/obsidian-reminders-sync.log")
)
OSA_TIMEOUT = int(os.environ.get("OBSIDIAN_SYNC_TIMEOUT", "120"))
LOCK_FILE = STATE_FILE + ".lock"

os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ---------------------------------------------------------------------------
# Obsidian file parsing / formatting
# ---------------------------------------------------------------------------

def parse_obsidian(content: str) -> dict:
    """
    Parse markdown into {section_name: [{name, completed}, ...], ...}.
    Sections are headed by '# Title' lines.
    """
    sections = {}
    current = None
    for line in content.splitlines():
        if line.startswith("# "):
            current = line[2:].strip()
            sections[current] = []
        elif current is not None:
            if line.startswith("- [ ] "):
                sections[current].append({"name": line[6:].strip(), "completed": False})
            elif line.startswith("- [x] "):
                sections[current].append({"name": line[6:].strip(), "completed": True})
    return sections


def format_obsidian(reminders: list) -> str:
    """
    Render reminder dicts to markdown.
    Open items grouped by list (sorted newest-modified first).
    Completed items in a single '# Completed' section at the end.
    """
    if not reminders:
        return ""

    by_list = {}
    completed = []
    for r in reminders:
        if r["completed"]:
            completed.append(r)
        else:
            by_list.setdefault(r["list"], []).append(r)

    lines = []
    for list_name, items in by_list.items():
        items_sorted = sorted(items, key=lambda x: x.get("modified", ""), reverse=True)
        lines.append(f"# {list_name}")
        lines.append("")
        for item in items_sorted:
            lines.append(f"- [ ] {item['name']}")
        lines.append("")

    if completed:
        lines.append("# Completed")
        lines.append("")
        for item in completed:
            lines.append(f"- [x] {item['name']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single-instance lock (prevents overlapping runs)
# ---------------------------------------------------------------------------

def acquire_lock(path: str):
    """
    Try to take an exclusive, non-blocking lock.
    Returns an open file handle holding the lock, or None if another process
    already holds it. The lock releases automatically when the handle is closed
    or the process exits. Caller must keep the handle alive for the run.
    """
    f = open(path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (BlockingIOError, OSError):
        f.close()
        return None


# ---------------------------------------------------------------------------
# Data-loss guard
# ---------------------------------------------------------------------------

def should_abort(fetch_ok: bool, fetched_items: list, state_items: list):
    """
    Decide whether to skip this sync to avoid destroying real data.

    Returns (abort: bool, reason: str). Abort when the fetch is untrustworthy:
      - the Reminders read failed outright, or
      - it returned zero items while we were previously tracking some
        (under launchd, a permission denial can yield an empty list).
    """
    if not fetch_ok:
        return True, "Reminders read failed"
    had_incomplete = any(not i.get("completed") for i in state_items)
    if not fetched_items and had_incomplete:
        return True, (
            "fetch returned 0 open reminders but state had open items "
            "— likely a Reminders permission failure under launchd"
        )
    return False, ""


# ---------------------------------------------------------------------------
# Diff / merge
# ---------------------------------------------------------------------------

def compute_diff(state_items: list, reminders: list, obsidian_sections: dict):
    """
    Merge the three sources into the final item list + the actions to push back
    to Reminders. Completion is tracked WITHOUT querying completed reminders
    (which is far too slow over iCloud):

      - `reminders`  — fresh OPEN reminders from Reminders.app (real ids).
      - `state_items`— last snapshot (open + completed), bridges name → id.
      - obsidian     — the user's checkbox intent.

    An item shows in '# Completed' when the user checks it in Obsidian, or when a
    tracked open item disappears from Reminders (completed/deleted elsewhere).
    Deleting its line from the file clears it.

    Returns (merged, actions) where
        actions = {"create": [...], "complete": [ids], "uncomplete": [ids]}
    """
    state_by_name = {i["name"]: i for i in state_items}
    incomplete = {r["name"]: r for r in reminders}

    obs = {}
    for section, items in obsidian_sections.items():
        for it in items:
            obs[it["name"]] = {
                "completed": it["completed"],
                "list": None if section == "Completed" else section,
            }

    merged = []
    actions = {"create": [], "complete": [], "uncomplete": []}
    handled = set()

    # 1) Open reminders — Reminders' truth for open items.
    for name, r in incomplete.items():
        handled.add(name)
        o = obs.get(name)
        if o and o["completed"]:
            # Checked off in Obsidian → complete in Reminders, show as completed.
            actions["complete"].append(r["id"])
            merged.append({"id": r["id"], "name": name, "list": r["list"], "completed": True})
        else:
            merged.append({"id": r["id"], "name": name, "list": r["list"], "completed": False})

    # 2) Tracked items that are no longer open in Reminders.
    for st in state_items:
        name = st["name"]
        if name in handled:
            continue
        handled.add(name)
        o = obs.get(name)
        was_completed = st.get("completed", False)
        if o is None:
            # Line removed from the file → clear it (open or completed).
            continue
        if o["completed"]:
            # Marked [x] in the file → completed (just-checked or persisting).
            merged.append({"id": st.get("id", ""), "name": name,
                           "list": st.get("list", "Reminders"), "completed": True})
        elif was_completed:
            # Was completed, now [ ] in the file → user reopened it.
            if st.get("id"):
                actions["uncomplete"].append(st["id"])
            merged.append({"id": st.get("id", ""), "name": name,
                           "list": (o["list"] or st.get("list", "Reminders")), "completed": False})
        else:
            # Was open, still [ ] in the file, but gone from Reminders →
            # completed/deleted elsewhere → show as completed.
            merged.append({"id": st.get("id", ""), "name": name,
                           "list": st.get("list", "Reminders"), "completed": True})

    # 3) Brand-new lines typed in Obsidian.
    for name, o in obs.items():
        if name in handled:
            continue
        handled.add(name)
        if o["completed"]:
            merged.append({"id": "", "name": name, "list": o["list"] or "Reminders", "completed": True})
        else:
            actions["create"].append({"name": name, "list": o["list"] or "Reminders"})

    return merged, actions


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": [], "last_sync": None}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Apple Reminders via osascript → Reminders.app (Apple Events / Automation)
#
# We use osascript, NOT the `reminders` CLI: the CLI uses direct EventKit access
# (kTCCServiceReminders) which macOS DENIES when spawned under launchd, silently
# returning an empty list. osascript controls Reminders.app via Apple Events
# (Automation), which IS permitted under launchd. Slower (~15-50s, iCloud) but it
# actually works headless.
# ---------------------------------------------------------------------------

_FETCH_INCOMPLETE = '''tell application "Reminders"
\tset out to ""
\trepeat with aList in lists
\t\tset ln to name of aList
\t\tset theIds to id of (reminders of aList whose completed is false)
\t\tset theNames to name of (reminders of aList whose completed is false)
\t\trepeat with i from 1 to count of theIds
\t\t\tset out to out & (item i of theIds) & "|||" & ln & "|||" & (item i of theNames) & linefeed
\t\tend repeat
\tend repeat
\treturn out
end tell'''


def _osa(script: str):
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=OSA_TIMEOUT
    )


def fetch_reminders():
    """
    Read all INCOMPLETE reminders via osascript → Reminders.app in one bulk query.
    Returns (ok, items): ok=False if osascript errored.

    We deliberately do NOT query completed reminders: filtering/looking those up
    over iCloud via AppleScript takes minutes. An item that is completed or deleted
    elsewhere simply drops off the incomplete list (and out of the Markdown file).
    """
    r = _osa(_FETCH_INCOMPLETE)
    if r.returncode != 0:
        logging.error("osascript fetch failed (rc=%s): %s", r.returncode, r.stderr.strip())
        return False, []

    now = datetime.now().isoformat(timespec="seconds")
    items = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|||")
        if len(parts) != 3:
            continue
        rid, ln, name = parts
        items.append({"id": rid, "name": name, "list": ln, "completed": False, "modified": now})

    logging.info("osascript returned %d incomplete items", len(items))
    return True, items


def apply_actions(actions: dict) -> None:
    for item_id in actions.get("complete", []):
        _osa_set_completed(item_id, True)
    for item_id in actions.get("uncomplete", []):
        _osa_set_completed(item_id, False)
    for item in actions.get("create", []):
        _osa_create(item["name"], item["list"])


def _osa_set_completed(reminder_id: str, completed: bool) -> None:
    val = "true" if completed else "false"
    r = _osa(f'tell application "Reminders" to set completed of reminder id "{reminder_id}" to {val}')
    if r.returncode != 0:
        logging.warning("osascript set completed=%s for %s failed: %s", val, reminder_id, r.stderr.strip())


def _osa_create(name: str, list_name: str) -> None:
    safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
    safe_list = list_name.replace("\\", "\\\\").replace('"', '\\"')
    r = _osa(
        f'tell application "Reminders" to make new reminder '
        f'at end of list "{safe_list}" with properties {{name:"{safe_name}"}}'
    )
    if r.returncode != 0:
        logging.warning("osascript create '%s' in '%s' failed: %s", name, list_name, r.stderr.strip())


# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

def run_sync() -> None:
    if not OBSIDIAN_FILE:
        logging.error("OBSIDIAN_REMINDERS_FILE is not set — nothing to sync.")
        sys.stderr.write("ERROR: set OBSIDIAN_REMINDERS_FILE to the Markdown file to sync.\n")
        sys.exit(2)

    parent = os.path.dirname(OBSIDIAN_FILE)
    if parent and not os.path.isdir(parent):
        logging.error("Folder for OBSIDIAN_REMINDERS_FILE does not exist: %s", parent)
        sys.stderr.write(f"ERROR: folder does not exist: {parent}\n")
        sys.exit(2)

    lock = acquire_lock(LOCK_FILE)
    if lock is None:
        logging.info("another sync is already running — skipping this cycle")
        return

    try:
        _do_sync()
    finally:
        lock.close()


def _stamp_modified(merged: list, state_items: list, now: str) -> None:
    """Give each merged item a `modified` time: keep the old one if unchanged,
    else stamp `now`. Matched by id when available, otherwise by name."""
    by_id = {i["id"]: i for i in state_items if i.get("id")}
    by_name = {i["name"]: i for i in state_items}
    for m in merged:
        old = by_id.get(m.get("id")) or by_name.get(m["name"])
        if old and old.get("completed") == m["completed"] and old.get("name") == m["name"]:
            m["modified"] = old.get("modified", now)
        else:
            m["modified"] = now


def _do_sync() -> None:
    logging.info("sync start")

    state = load_state(STATE_FILE)

    fetch_ok, reminders = fetch_reminders()

    abort, reason = should_abort(fetch_ok, reminders, state["items"])
    if abort:
        logging.error("ABORT — %s. Leaving Obsidian file and state untouched.", reason)
        sys.exit(1)

    now = datetime.now().isoformat(timespec="seconds")

    obsidian_content = ""
    if os.path.exists(OBSIDIAN_FILE):
        with open(OBSIDIAN_FILE) as f:
            obsidian_content = f.read()
    obsidian_sections = parse_obsidian(obsidian_content)

    merged, actions = compute_diff(state["items"], reminders, obsidian_sections)

    apply_actions(actions)

    # Re-fetch after applying actions so newly created/reopened reminders get
    # their real ids, then recompute the merged view from the fresh data.
    if actions["complete"] or actions["uncomplete"] or actions["create"]:
        refetch_ok, refetched = fetch_reminders()
        if refetch_ok:
            merged, _ = compute_diff(state["items"], refetched, obsidian_sections)

    _stamp_modified(merged, state["items"], now)

    new_content = format_obsidian(merged)
    with open(OBSIDIAN_FILE, "w") as f:
        f.write(new_content)

    save_state(STATE_FILE, {
        "items": merged,
        "last_sync": now,
    })

    logging.info("sync done — %d items, %d created, %d completed, %d uncompleted",
                 len(merged),
                 len(actions["create"]),
                 len(actions["complete"]),
                 len(actions["uncomplete"]))


if __name__ == "__main__":
    try:
        run_sync()
    except Exception as e:
        logging.exception("sync crashed: %s", e)
        sys.exit(1)
