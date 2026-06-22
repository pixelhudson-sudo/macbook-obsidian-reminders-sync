# obsidian-reminders-sync

Two-way sync between **Apple Reminders** and a single **Markdown file** in your
Obsidian vault (or any Markdown file), running locally on your Mac via `launchd`.

- Check a box in Obsidian → the reminder completes in Apple Reminders.
- Add a `- [ ]` line in Obsidian → a new reminder is created.
- Add/complete a reminder on your iPhone → it shows up in the Markdown file.

No cloud service, no account, no API keys. Everything runs on your machine.

```markdown
# Reminders

- [ ] Buy milk
- [ ] Call the plumber

# Work

- [ ] Send the invoice

# Completed

- [x] Renew car insurance
```

Each Apple Reminders list becomes a `#` section. Open items are sorted
most-recently-changed first. Completed items collapse into a `# Completed`
section at the bottom.

---

## Requirements

- macOS (uses the built-in `osascript` and `launchd`)
- Python 3 (the system `/usr/bin/python3` is fine — no packages required)
- Obsidian (or any folder with a Markdown file)

## Install

```bash
git clone https://github.com/YOURNAME/obsidian-reminders-sync.git
cd obsidian-reminders-sync
chmod +x install.sh uninstall.sh
./install.sh "/full/path/to/your/vault/Inbox/reminders.md"
```

The installer will:

1. Trigger the macOS **"… wants to control Reminders"** prompt — click **OK**.
2. Write a `launchd` agent that runs the sync every 5 minutes.
3. Run one sync immediately and print the log.

Change the interval with `--interval` (seconds):

```bash
./install.sh "~/Obsidian/reminders.md" --interval 600
```

> The Markdown file's **folder** must already exist. The file itself will be
> created on first sync if it isn't there.

## Permissions (important)

This tool talks to Reminders through **Apple Events / Automation**, so the first
run needs you to approve a prompt. If the log shows `0 incomplete items` but you
do have reminders:

- Open **System Settings → Privacy & Security → Automation**
- Enable **Reminders** under the sync agent / `osascript`
- Re-run `./install.sh …` and approve the popup

(See [Why osascript?](#why-osascript-and-not-the-reminders-cli) for the gory
details.)

## Uninstall

```bash
./uninstall.sh
```

Your Markdown file and your reminders are left untouched.

---

## How it works

`sync.py` keeps a small state file (`~/.local/state/obsidian-reminders-sync/state.json`)
recording the last-known snapshot of every tracked reminder by its stable id.
Each run it:

1. Reads incomplete reminders from all lists (via `osascript`).
2. Reads the Markdown file and diffs it against the last snapshot.
3. Decides what changed on each side:

| Change | Result |
|---|---|
| New reminder in Apple Reminders | added to the Markdown file |
| New `- [ ]` line in Markdown | reminder created in that list's section |
| `- [ ]` → `- [x]` in Markdown | reminder completed in Apple Reminders |
| `- [x]` → `- [ ]` in Markdown | reminder un-completed |
| Reminder completed on your phone | moves to `# Completed` in Markdown |
| Reminder deleted in Apple Reminders | removed from Markdown |

4. Writes the merged result back to the Markdown file and saves the new snapshot.

Items are matched between the two sides by **name**, and tracked across runs by
their Apple Reminders id.

### Safety guard

If the Reminders read fails — or returns **zero** items while the snapshot had
some (a classic permission glitch under `launchd`) — the sync **aborts and
leaves your file and state untouched**. It never blanks your file on a failed
read.

## Configuration

The installer sets these via the launchd plist; you can also set them yourself:

| Variable | Default | Meaning |
|---|---|---|
| `OBSIDIAN_REMINDERS_FILE` | *(required)* | Full path to the Markdown file |
| `OBSIDIAN_SYNC_STATE_FILE` | `~/.local/state/obsidian-reminders-sync/state.json` | Snapshot location |
| `OBSIDIAN_SYNC_LOG_FILE` | `~/Library/Logs/obsidian-reminders-sync.log` | Log location |
| `OBSIDIAN_SYNC_TIMEOUT` | `120` | Per-osascript timeout (seconds) |

Run a one-off sync manually:

```bash
OBSIDIAN_REMINDERS_FILE="/path/to/reminders.md" python3 sync.py
```

Watch the log:

```bash
tail -f ~/Library/Logs/obsidian-reminders-sync.log
```

## Why osascript and not the `reminders` CLI?

The popular `reminders` CLI uses direct EventKit access. That works from a
Terminal, but when macOS runs it from a background `launchd` job it is **denied
Reminders access by TCC and silently returns an empty list** — which would make
a naive sync wipe your file every few minutes.

`osascript` controls **Reminders.app** through Apple Events ("Automation"),
which *is* permitted under `launchd`. It's slower (each read is a few iCloud
round-trips, ~15-50s) — hence the default 5-minute interval — but it works
headless and reliably. This is the whole reason the project exists in this shape.

## Limitations

- Matching is by reminder **name**; two reminders with the identical title in the
  same context can be ambiguous.
- Notes, due dates, priorities, sub-tasks and tags are **not** synced — titles
  and completion state only.
- iCloud latency makes each sync take a while; this is not real-time.
- Reminders typed in Markdown are created in a list named after their `#`
  section — that list must already exist in Apple Reminders.

## Development

Pure logic (parsing, formatting, diffing, the safety guard) is unit-tested and
does not touch your real reminders:

```bash
pip3 install pytest
python3 -m pytest -v
```

## License

MIT — see [LICENSE](LICENSE).
