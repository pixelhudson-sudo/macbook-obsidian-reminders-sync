"""
Tests for macbook-obsidian-sync.
Run: python3 -m pytest test_sync.py -v
"""
import pytest
from sync import (
    parse_obsidian,
    format_obsidian,
    compute_diff,
    load_state,
    save_state,
    should_abort,
)
import json, os, tempfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_OBSIDIAN = """\
# Reminders

- [ ] Fix the fan wiring

# Work

- [ ] Send invoice

# Completed

- [x] Cancel car insurance
"""

SAMPLE_REMINDERS = [
    {"id": "r1", "name": "Fix the fan wiring",   "list": "Reminders", "completed": False, "modified": "2026-06-21T10:00:00"},
    {"id": "r2", "name": "Send invoice",          "list": "Work",      "completed": False, "modified": "2026-06-21T09:00:00"},
    {"id": "r3", "name": "Cancel car insurance",  "list": "Reminders", "completed": True,  "modified": "2026-06-20T08:00:00"},
]

SAMPLE_STATE = {
    "items": SAMPLE_REMINDERS,
    "last_sync": "2026-06-21T10:00:00",
}


# ---------------------------------------------------------------------------
# parse_obsidian
# ---------------------------------------------------------------------------

def test_parse_obsidian_returns_open_items_by_section():
    sections = parse_obsidian(SAMPLE_OBSIDIAN)
    assert "Reminders" in sections
    assert sections["Reminders"] == [{"name": "Fix the fan wiring", "completed": False}]

def test_parse_obsidian_returns_completed_section():
    sections = parse_obsidian(SAMPLE_OBSIDIAN)
    assert "Completed" in sections
    assert sections["Completed"] == [{"name": "Cancel car insurance", "completed": True}]

def test_parse_obsidian_multiple_sections():
    sections = parse_obsidian(SAMPLE_OBSIDIAN)
    assert "Work" in sections
    assert sections["Work"] == [{"name": "Send invoice", "completed": False}]

def test_parse_obsidian_empty_file():
    sections = parse_obsidian("")
    assert sections == {}

def test_parse_obsidian_section_with_no_items():
    content = "# Empty\n\n# Reminders\n\n- [ ] Task\n"
    sections = parse_obsidian(content)
    assert sections.get("Empty") == []
    assert sections["Reminders"] == [{"name": "Task", "completed": False}]


# ---------------------------------------------------------------------------
# format_obsidian
# ---------------------------------------------------------------------------

def test_format_obsidian_groups_by_list():
    result = format_obsidian(SAMPLE_REMINDERS)
    assert "# Reminders" in result
    assert "# Work" in result

def test_format_obsidian_open_items_use_checkbox():
    result = format_obsidian(SAMPLE_REMINDERS)
    assert "- [ ] Fix the fan wiring" in result
    assert "- [ ] Send invoice" in result

def test_format_obsidian_completed_items_in_completed_section():
    result = format_obsidian(SAMPLE_REMINDERS)
    assert "# Completed" in result
    assert "- [x] Cancel car insurance" in result

def test_format_obsidian_completed_section_is_last():
    result = format_obsidian(SAMPLE_REMINDERS)
    completed_pos = result.index("# Completed")
    for section in ["# Reminders", "# Work"]:
        assert result.index(section) < completed_pos

def test_format_obsidian_sorts_open_items_newest_first():
    reminders = [
        {"id": "a", "name": "Old task",  "list": "Reminders", "completed": False, "modified": "2026-06-19T08:00:00"},
        {"id": "b", "name": "New task",  "list": "Reminders", "completed": False, "modified": "2026-06-21T10:00:00"},
    ]
    result = format_obsidian(reminders)
    assert result.index("New task") < result.index("Old task")

def test_format_obsidian_empty_list():
    result = format_obsidian([])
    assert result.strip() == ""


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

def test_diff_new_reminder_added_to_obsidian():
    """New item in Reminders (not in state) → must appear in merged output."""
    state_items = []
    reminders = [{"id": "r1", "name": "New task", "list": "Reminders", "completed": False, "modified": "2026-06-21T10:00:00"}]
    obsidian_sections = {}
    merged, _ = compute_diff(state_items, reminders, obsidian_sections)
    names = [i["name"] for i in merged]
    assert "New task" in names

def test_diff_obsidian_checked_marks_reminder_complete():
    """[ ] → [x] in Obsidian causes completed=True in merged output."""
    state_items = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": False, "modified": "2026-06-21T09:00:00"}]
    reminders   = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": False, "modified": "2026-06-21T09:00:00"}]
    # User checked it off in Obsidian
    obsidian_sections = {"Completed": [{"name": "Task", "completed": True}]}
    merged, _ = compute_diff(state_items, reminders, obsidian_sections)
    task = next(i for i in merged if i["name"] == "Task")
    assert task["completed"] is True

def test_diff_new_obsidian_item_gets_created_in_reminders():
    """New - [ ] line in an Obsidian section → appears in reminders_to_create."""
    state_items = []
    reminders   = []
    obsidian_sections = {"Personal": [{"name": "Buy milk", "completed": False}]}
    _, actions = compute_diff(state_items, reminders, obsidian_sections)
    creates = actions.get("create", [])
    assert any(c["name"] == "Buy milk" and c["list"] == "Personal" for c in creates)

def test_diff_reminder_deleted_removed_from_obsidian():
    """Item in state but gone from Reminders → not in merged output."""
    state_items = [{"id": "r1", "name": "Old task", "list": "Reminders", "completed": False, "modified": "2026-06-20T08:00:00"}]
    reminders   = []  # deleted
    obsidian_sections = {"Reminders": [{"name": "Old task", "completed": False}]}
    merged, _ = compute_diff(state_items, reminders, obsidian_sections)
    names = [i["name"] for i in merged]
    assert "Old task" not in names

def test_diff_obsidian_unchecked_marks_reminder_incomplete():
    """[x] → [ ] in Obsidian causes completed=False in merged output."""
    state_items = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": True, "modified": "2026-06-21T09:00:00"}]
    reminders   = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": True, "modified": "2026-06-21T09:00:00"}]
    # User un-checked it in Obsidian
    obsidian_sections = {"Reminders": [{"name": "Task", "completed": False}]}
    merged, _ = compute_diff(state_items, reminders, obsidian_sections)
    task = next(i for i in merged if i["name"] == "Task")
    assert task["completed"] is False


# ---------------------------------------------------------------------------
# State file round-trip
# ---------------------------------------------------------------------------

def test_state_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_state(path, SAMPLE_STATE)
        loaded = load_state(path)
        assert loaded["items"] == SAMPLE_STATE["items"]
        assert loaded["last_sync"] == SAMPLE_STATE["last_sync"]
    finally:
        os.unlink(path)

def test_load_state_returns_empty_when_missing():
    state = load_state("/tmp/does_not_exist_macbook_obsidian_sync.json")
    assert state == {"items": [], "last_sync": None}


# ---------------------------------------------------------------------------
# should_abort — data-loss guard
#
# WHY: under launchd, macOS TCC denies the `reminders` CLI access to Reminders
# and it returns an empty list with exit 0. Writing that empty result would
# wipe the user's Obsidian file and tracking state. The guard must refuse to
# proceed whenever the fetch is untrustworthy, so a transient/permission
# failure can never destroy real data.
# ---------------------------------------------------------------------------

def test_abort_when_fetch_failed():
    """CLI errored → never write, regardless of contents."""
    abort, _ = should_abort(fetch_ok=False, fetched_items=[], state_items=[])
    assert abort is True

def test_abort_when_zero_items_but_state_had_items():
    """Had tracked items, now fetch returns none → almost certainly a failure."""
    state = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": False, "modified": "x"}]
    abort, _ = should_abort(fetch_ok=True, fetched_items=[], state_items=state)
    assert abort is True

def test_proceed_on_first_run_with_no_items():
    """Genuine empty (fresh install, no reminders, empty state) → fine to proceed."""
    abort, _ = should_abort(fetch_ok=True, fetched_items=[], state_items=[])
    assert abort is False

def test_proceed_when_items_present():
    """Normal successful fetch → proceed."""
    items = [{"id": "r1", "name": "Task", "list": "Reminders", "completed": False, "modified": "x"}]
    abort, _ = should_abort(fetch_ok=True, fetched_items=items, state_items=[])
    assert abort is False
