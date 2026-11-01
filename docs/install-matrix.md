# Install matrix

What the CI workflow `.github/workflows/install-matrix.yml` proves on each system. It runs on
`windows-latest`, `macos-latest` and `ubuntu-24.04`, each with Python 3.9 and 3.13, on every pull
request and every release tag. The install steps are in [setup-guide.md](setup-guide.md).

CI proves the command-line install and the tests. Claude Desktop app install is not tested by CI.

Last green run: [install-matrix 41332885453](https://github.com/siteoffice/groundwork/actions/runs/41332885453)
on commit `da04fd3`, 2026-11-01. All six cells (three systems, two Pythons each) passed. macOS and
Ubuntu are verified in CI, as is Windows. The install steps, `claude plugin install` for both
plugins, passed on all three systems without a Claude login.

## Per step

| Step | Windows | macOS | Linux |
|---|---|---|---|
| `marketplace add` | proven by CI | proven by CI | proven by CI |
| `install groundwork` | proven by CI | proven by CI | proven by CI |
| `install groundwork-mods` | proven by CI | proven by CI | proven by CI |
| `plugin list` | proven by CI | proven by CI | proven by CI |
| `engine` | proven by CI | proven by CI | proven by CI |
| `board` | proven by CI | proven by CI | proven by CI |
| `root` | proven by CI | proven by CI | proven by CI |
| `mods plugin test` | proven by CI | proven by CI | proven by CI |
| `sync` | not tested | not tested | not tested |
| Claude Desktop app install | not tested | not tested | not tested |

The Claude Desktop app does not exist on Linux, so its Linux cell can never change.

## What each cell means

- **proven by CI**: the step ran and passed on that system, with both Pythons.
- **skipped: needs a Claude login**: the step asked for a Claude login, which CI does not have.
  It did not fail and it did not pass. No step was skipped for this reason in the last green run.
- **not tested**: CI does not run this step.

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
quietly collects fewer tests does not count as green. The root floor is 628 on Windows and 621 on
macOS and Linux, because Windows runs the `os.name == "nt"` test blocks and the others collect
fewer tests. Each job also prints a row for the
Tested on table in its run summary; rows from green runs are copied into the setup guide.
