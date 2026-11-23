# Engine

The state machine behind the one-loop skill: flows, gates (spec and accept), sprints, locks and the
local receipt. Python 3.9 or newer, standard library only. No service, no network call, no
account is needed to run a flow.

## Where it comes from

`foreman\` is vendored from the Siteoffice engine by `scripts\sync-engine.ps1` and rewritten so
it carries no private path or name. Do not edit it by hand: the next sync overwrites it. The
receipt `SYNCED_FROM.json` names the engine version and source commit of the last sync.

## Where your records live

The engine writes flows, sprints and receipts to a data root, never inside the engine folder.
It picks the root in this order:

1. an explicit argument to the command,
2. the `ZND_DATA_ROOT` environment variable,
3. the older alias `ZND_HUB_ROOT`,
4. a `znd_hub.json` config file with a top-level `"hub_root"` string, next to the derived root,
5. otherwise the folder two levels above `flows\_lib` (the `foreman` folder itself).

The variable names keep their old prefix in v0.1 so existing installs keep working. A later
major version renames them.

## Slack is optional

A flow runs, gates open and close, and receipts are written with no Slack account. The Slack
sweep (`slack_sweep_runner.py`) only runs when you start it yourself and set `SLACK_BOT_TOKEN`.
The default is the terminal: the one-loop skill shows each gate in the session and you answer
there.

## Time zones

`sweep_core.py` uses `zoneinfo` (Python 3.9 or newer). On Windows run
`python -m pip install tzdata` once, because Windows ships no timezone database. The core
decision functions never call it.

## Snippets and the snippet lint

Both tools are off by default. A repo turns them on with `"outputStandards": true` inside
`settings` of its `siteoffice.json` (the nearest one above the file or folder). A missing key,
`false`, or no `siteoffice.json` means off.

- `snip\snip.py` prints one fenced block in a fixed form: `# runs in:`, `# cwd:`, `# needs:`,
  for git also `# recipe: git <name>`, then the command that enters the folder, the commands, and
  `# expect:` as the last line. `--os win` prints PowerShell, `--os mac` prints sh that runs in
  zsh and bash; the default is the system it runs on. Kinds: `run` (your own command lines),
  `paste` (text to paste into a named session) and five git recipes, one form each: `save` (add
  the named files and commit), `share` (push the branch), `land` (merge through the
  siteoffice-git engine), `tag` (an annotated tag, pushed and checked) and `sync` (fetch and
  fast-forward). It refuses an empty value, a line break in a one-line value, an em dash, raw
  `git` or a folder change in `run`, and a path of the other system, with one line and exit code 2.
- `snip\snippet_lint.py <file>... [--force]` checks Markdown files: the header and its order, git
  blocks without a recipe, command lines in a fence with no language, an empty paste target,
  control characters, and paths where a `(` follows a name because a backslash was eaten. Exit 0
  on a pass, 1 with one line per failure, 2 on a usage error. A file in a repo with the switch off
  is skipped with one line; `--force` checks it anyway.

The limit: the lint checks files, not chat replies. Nothing calls either tool for you yet.

## Tests

Run from `plugins\groundwork\engine` in your checkout. `py -3` picks a Python that has pytest
installed. The example uses `C:\src\groundwork` as the checkout; put your own path in both lines:

```powershell
# runs in: PowerShell 7 on Windows, normal window
# cwd: C:\src\groundwork\plugins\groundwork\engine
# needs: Python 3.9 or newer with pytest
Set-Location -LiteralPath 'C:\src\groundwork\plugins\groundwork\engine'
py -3 -m pytest foreman groundwork snip -q
# expect: no failures; the last line names the passed and skipped counts
```

Expect `1021 passed, 4 skipped` for `foreman groundwork` on engine 0.1.0, plus `65 passed` for
`snip` when `sh` and `pwsh` are both on the PATH. Two `snip` tests depend on the host: the sh
syntax test skips without `sh` on the PATH (a PowerShell window on Windows usually prints
`64 passed, 1 skipped`), and the `pwsh` parse check inside the curly quote test skips without
`pwsh`. macOS is untested.
