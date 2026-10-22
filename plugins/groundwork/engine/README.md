# Engine

The state machine behind the one-loop skill: flows, gates (G1 to G4), sprints, locks and the
local receipt. Python 3.8 or newer, standard library only. No service, no network call, no
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

## Tests

Run from `plugins\groundwork\engine`, in PowerShell on Windows. `py -3` picks a Python that has
pytest installed:

```powershell
# runs in: PowerShell on Windows. cwd: plugins\groundwork\engine in the repo checkout
py -3 -m pytest foreman -q
```

Expect `979 passed, 4 skipped` for engine 0.1.0. macOS is untested.
