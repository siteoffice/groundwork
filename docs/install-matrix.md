# Install matrix

What the CI workflow `.github/workflows/install-matrix.yml` proves on each system. It runs on
`windows-latest`, `macos-latest` and `ubuntu-24.04`, each with Python 3.9 and 3.13, on every pull
request and every release tag. The install steps are in [setup-guide.md](setup-guide.md).

CI proves the command-line install and the tests. Claude Desktop app install is not tested by CI.

## Per step

| Step | Windows | macOS | Linux |
|---|---|---|---|
| `marketplace add` | pending first green run | pending first green run | pending first green run |
| `install groundwork` | pending first green run | pending first green run | pending first green run |
| `install groundwork-mods` | pending first green run | pending first green run | pending first green run |
| `plugin list` | pending first green run | pending first green run | pending first green run |
| `engine` | pending first green run | pending first green run | pending first green run |
| `board` | pending first green run | pending first green run | pending first green run |
| `root` | pending first green run | pending first green run | pending first green run |
| `mods plugin test` | pending first green run | pending first green run | pending first green run |
| `sync` | not tested | not tested | not tested |
| Claude Desktop app install | not tested | not tested | not tested |

The Claude Desktop app does not exist on Linux, so its Linux cell can never change.

## What each cell means

- **proven by CI**: the step ran and passed on that system, with both Pythons.
- **skipped: needs a Claude login**: the step asked for a Claude login, which CI does not have.
  It did not fail and it did not pass.
- **not tested**: CI does not run this step.
- **pending first green run**: the workflow runs this step, but no green run has been recorded
  here yet.

## What each step is

- `marketplace add`, `install groundwork`, `install groundwork-mods`, `plugin list`: the
  `claude plugin` commands from the setup guide, run in a terminal after `npm install -g
  @anthropic-ai/claude-code`. One difference: `marketplace add` adds the marketplace from the
  CI checkout of this repository, not from `siteoffice/groundwork`, so the GitHub shorthand
  itself is not proven by CI.
- `engine`: `python -m pytest foreman groundwork -q` in `plugins/groundwork/engine`.
- `board`: `python -m pytest board -q` in `plugins/groundwork`.
- `root`: `python -m pytest tests -q` at the repository root.
- `mods plugin test`: `claude plugin test .` in `plugins/groundwork-mods`.
- `sync`: the `siteoffice-git` engine suite. It builds real git repositories, takes about
  25 minutes and stays out of CI.
- Claude Desktop app install: the no-terminal route through the app's settings. No CI runner has
  the app.

Each test suite must reach a floor (passed plus skipped, with nothing failed), so a run that
quietly collects fewer tests does not count as green. Each job also prints a row for the
Tested on table in its run summary; rows from green runs are copied into the setup guide.
