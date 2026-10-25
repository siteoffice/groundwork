# Set up Groundwork

Groundwork needs two plugin installs and one folder. Nothing runs by itself: the agent works
inside your session, and you decide at each gate.

macOS is untested. The steps below are written for it from the code and the docs, not from a run
on a Mac. Windows is what I tested.

## What you need

- **Claude Code or Claude Desktop.** Claude Code is the main route; the skills are plain Markdown
  and other tools can read them, but the hooks only work in Claude Code.
- **Python 3.8 or newer.** The engine and the board run on it and need nothing else.
- **A folder for your work.** Any folder. Git is useful but not required.

Check Python first.

```powershell
# runs in: PowerShell on Windows. cwd: any.
py -3 --version
```

```bash
# runs in: Terminal on macOS. cwd: any.
python3 --version
```

No version printed? Install Python, then check again.

```powershell
# runs in: PowerShell on Windows. cwd: any.
winget install --id Python.Python.3.12 -e
```

```bash
# runs in: Terminal on macOS. cwd: any. Needs Homebrew.
brew install python
```

## Install the plugins

**In Claude Desktop, no terminal:** click your name (bottom left), then Settings, then Plugins,
then Add marketplace, and paste `siteoffice/groundwork`. Install `groundwork`. Install
`groundwork-mods` too if you want the Setup Doctor and the agent policy.

**In Claude Code:**

```text
# runs in: inside Claude Code, in any session.
/plugin marketplace add siteoffice/groundwork
/plugin install groundwork@groundwork
/plugin install groundwork-mods@groundwork
```

Pick **user scope** when Claude Code asks where to install, or add `--scope user` to
`claude plugin install` in a terminal. A project-scope install loads only in the folder you were
in, so `/setup-doctor` is "unknown" everywhere else.

Restart Claude Code after the install. The mods load at the start of a session.

## Check it

Type `/setup-doctor` in Claude Code. It lists what is missing, each with one fix you can copy.
It only reads; it changes nothing. Press the refresh button after you fix something. An optional
BurnMon line at the bottom says whether BurnMon runs; it never counts as something to fix.

If the doctor says "Session start events reach the mods" is not met, a plugin that runs before the
mods is passing the start event on past them. Groundwork still works. The mods then miss the reset
after `/clear` and `/resume`. Restart once and check again before you worry.

## What needs BurnMon

BurnMon is a separate app that watches your agent sessions and writes a small snapshot file in
your home folder. The Fleet and the Doctor's BurnMon line need it; everything else in Groundwork
works without it. BurnMon is not bundled: get it from
<https://github.com/siteoffice/burnmon/releases>. The Doctor shows grey "not installed" when the
snapshot file is missing, amber "installed but not running" when it is older than ten seconds or
cannot be read, and green when it is fresh.

## Run your first loop

1. Make a folder for the work and open Claude Code in it.
2. Say: "Start a flow for <your idea>". The `one-loop` skill takes it from there.
3. You decide at four gates: plan (G1), spec (G2), ship (G4), learn (G3).
4. Say "ship" at G4 when you accept the result. That writes a receipt, a readable page and a JSON
   file, under `flows\<project>\<slug>\receipts\`. It is self-issued and not signed: your own
   record, not proof to anyone else.

A closed flow is not deleted. Everything stays in plain files in your folder.

## Build the folder structure

Say "set up my company hub" and the `siteoffice-setup` skill builds the folders and fills them
from an interview. Or copy `templates\skeleton\` yourself and fill it in. `templates\molenwerk\` is
a finished example; open it to see where the words go.

## See the board

```powershell
# runs in: PowerShell on Windows. cwd: the folder of this checkout.
py -3 .\plugins\groundwork\board\render_board.py node .\templates\molenwerk
```

```bash
# runs in: Terminal on macOS. cwd: the folder of this checkout.
python3 ./plugins/groundwork/board/render_board.py node ./templates/molenwerk
```

Open `_board\board.html` in a browser. It is one file and it only reads your folders.

## Turn on write access

Out of the box the agent can read and write drafts. It cannot change other files, send anything,
or delete without a named person. That is the agent policy in `groundwork-mods`; the rules are in
`plugins\groundwork-mods\policy\README.md`. To let the agent change files, set `"mode": "work"` in
`.groundwork\agent-policy.json` in your project, and add `.groundwork/` to the project's
`.gitignore`. Sends and deletes stay blocked.

The policy is a guardrail, not a lock. It stops an agent doing the work the normal way. It does not
stop you, and it cannot see a command it does not recognise once you are in work mode.

## Why it is built this way

Chat windows lose things. A decision made in a chat is gone when the chat is. Groundwork keeps the
plans, the decisions and the record in plain files in your folder, so any person or any tool can
read them tomorrow. The gates exist because a plan nobody approved is a guess, and an agent that
ships without asking is a risk you did not choose.

## If something goes wrong

- `python` is not found: install Python, restart the terminal, try again.
- A command prints `refused: ...`: the engine rejected the call. Read the reason; it names the
  gate or the missing step.
- The doctor shows nothing: you are not in Claude Code, or `groundwork-mods` is not installed.
- Anything else: open an issue on the repository with the command and what it printed.
