# groundwork (plugin)

The `groundwork` plugin holds the skills, the engine, the board and the git guard. Install it
first; `groundwork-mods` is optional.

## 1. What it is

This plugin gives an AI agent a loop with two gates where you decide, and a record in plain files
in your own folder. It also ships the skills for setting up, checking, moving and cleaning a
folder tree.

## 2. Where it fits

| Piece | What it does | Where |
|---|---|---|
| Skills | The tools you call by name | `skills\` |
| Engine | Flows, gates, locks and the local receipt | `engine\`, see [engine\README.md](engine/README.md) |
| Board | Draws a folder tree as one static HTML page | `board\render_board.py` |
| Git guard | Denies raw mutating git from the agent | `hooks\hooks.json` |

It needs Python 3.9 or newer and no account or network. It does not need `groundwork-mods`, BurnMon
or Claude Desktop. The whole picture is in the [repo README](../../README.md).

### The skills

- `one-loop`: runs one piece of work from idea to close, with two gates where you decide and a local receipt when you accept.
- `sprint`: builds one approved step on its own branch, with the spec's tests, a blind check and a report, while you are present.
- `retro`: a monthly script-only check-up of your Claude Code and Cowork sessions that counts human touches, silent failures and secret exposures.
- `handover`: ends a long chat with a short handover document so the next chat starts fresh.
- `siteoffice-setup`: sets up or improves a SiteOffice folder structure for a holding, company, department or project.
- `siteoffice-checkup`: a weekly read-only check of a SiteOffice tree.
- `siteoffice-cleanup`: cleans up a folder tree in three read-first passes, and compacts instruction files without deleting.
- `siteoffice-migrate`: moves, renames, restructures or adopts a folder tree without breaking tools that store the path.
- `siteoffice-git`: changes a SiteOffice repo through six git steps (start, save, check, propose, land, abort) instead of raw git. Claude Code only.

## 3. Install

```text
# runs in: inside Claude Code, any session.
/plugin marketplace add siteoffice/groundwork
/plugin install groundwork@groundwork
```

In Claude Desktop: Settings, Plugins, Add marketplace, paste `siteoffice/groundwork`, install
`groundwork`. From source and per system: see the [repo README](../../README.md#3-install) and
[docs\setup-guide.md](../../docs/setup-guide.md).

## 4. Use

Say "start a flow for <your change>" and `one-loop` stops at the spec gate. There are two gates.
Spec: approve; reject (do not build this step); park (pause, answer later). Accept: ship (accept
and merge); hold (send back); park (pause, answer later). Learnings run without a gate. Shipping at
the accept gate writes a receipt to `flows\<project>\<slug>\receipts\`; it is the only verdict that
does. Old records stay readable: older flows keep their old gate codes and still read, and the 0.5.0
entry in `CHANGELOG.md` lists how they map.
Say "retro" for the monthly check-up. The first 15 minutes are
in the [repo README](../../README.md#4-use), and every screen is in [docs\tour.md](../../docs/tour.md).

## 5. Settings

The plugin has no switches of its own. Retro keeps `period_days`, `token_ceiling`, `worlds` and
`gate_patterns` in `<root>\retro\config.json`. `SITEOFFICE_SYNC_DIR` names the `siteoffice-git`
engine. The engine's data root order is in [engine\README.md](engine/README.md). The table of every
key is in the [repo README](../../README.md#5-settings).

## 6. Limits and known issues

- The work step runs in your session, as plain work or as a sprint. The engine folder also holds a
  headless launcher, `sprint_runner.py`, that no skill calls and that is not supported in this release.
- `siteoffice-git` saves, proposes and lands with `git push` and a pull request, and the agent
  policy blocks every push. You run the push or the merge yourself.
- The git guard needs Windows PowerShell or PowerShell 7, and Git Bash on Windows. With neither,
  raw git is not blocked.
- Retro on Cowork for macOS is unverified, and its touch classes are heuristics. Read the counts as
  a trend.
- The board follows folders upward: keep a `00_funnel` folder out of the parent folders.

The full list is in the [repo README](../../README.md#6-limits-and-known-issues).

## 7. Next steps and backlog

See section 7 of the [repo README](../../README.md#7-next-steps-and-backlog) and the
[CHANGELOG](../../CHANGELOG.md).

## 8. Feedback, licence, contact

Open an issue on the repo. MIT, see [LICENSE](../../LICENSE). Mail info@zerononsense.dev.
