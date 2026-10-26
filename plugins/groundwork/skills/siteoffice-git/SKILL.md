---
name: siteoffice-git
description: Change a Siteoffice repo through six git steps (start, save, check, propose, land, abort) instead of raw git. Use when asked to commit, push, open or merge a pull request, start a work branch, park a branch, or run "siteoffice git" in a Siteoffice repo. Claude Code only. Do NOT use in Cowork or hub chats, or to review someone else's pull request.
---

For Claude Code only, never Cowork: this skill runs a local launcher and relies on a Claude Code hook.

# siteoffice-git

Every commit, push and merge in a Siteoffice repo goes through one launcher. A plugin hook denies the same git typed raw, so there is no second way in.

## Launcher

Run it from inside the repo, with the PowerShell tool:

`& "${CLAUDE_SKILL_DIR}/scripts/git_flow.ps1" <step> [arguments]`

It finds the repo root and the engine itself. Its exit code is the engine's: 0 pass, 2 refused, 3 blocked.

Every step writes a receipt to `_log\runs\git-flow.ndjson`. In a Claude Code session the launcher records the harness as `claude-code`. Set `SITEOFFICE_HARNESS` or pass `--harness` to record another value. Set `SITEOFFICE_MODEL` to record the model; otherwise the receipt says `unknown`.

## The six steps

| Step | What it does | Arguments |
|---|---|---|
| start | Pulls main, creates `work/<owner>/<slug>` | `start <slug>` |
| save | Commits the vetted changed paths on the work branch | `save --type T --scope S --subject TEXT` (subject at most 50 characters, one line, no closing period) |
| check | Runs the repo's configured checks, writes a receipt | none |
| propose | Pushes the work branch and opens a pull request | optional `--title TEXT` (default: the newest commit subject; applies only when a new pull request is opened) |
| land | Merges the pull request, cleans up | optional `--subject TEXT` for the merge commit (default: the subject `gh` writes) |
| abort | Parks the branch as `parked/<owner>/<slug>` | none |

Order: start, save (repeat), check, propose, land. Abort at any point before land.

## Preview, confirm, act

Every step previews by default and changes nothing.

1. Run the step without `--apply`. Show the preview to the user in full.
2. Ask the user to confirm. Wait for a yes. A step the user already approved in this message counts.
3. Run the same step again with `--apply`. Report the outcome line and the receipt path.

Wording of each preview, each refusal and the next move per step: [references/steps.md](references/steps.md). Quote the refusal sentence to the user as printed, then give the next move from that file.

## Who may apply

The agent may run any step itself, always preview first, and applies only after the yes described above.

- `land` (the merge): in an Attended sprint, run `land --apply` only after the person's explicit yes at the
  closing touchpoint, in this session. Report the merge sha and the test result on main. In an
  Unattended run, do not apply: park a HAND line with the full `land --apply` snippet.
- Tag: the launcher has no tag step, and the guard denies the push. Hand the person the tag snippet as a
  HAND line, in both modes, until the launcher gains a tag step.
- Raw git stays denied. Never set `SITEOFFICE_GIT_GUARD=off`.

## Failure branches

- Exit 2 (refused): state the refusal, give the next move, stop. Never retry with raw git.
- Exit 3 (blocked): say what is blocked (a red check, a missing `gh`), give the next move, stop.
- "Engine not found": tell the user to install the groundwork plugin or set `SITEOFFICE_SYNC_DIR` to a folder outside the repo, then stop.
- The hook denies a git command: use the launcher step that covers it. If no step covers it, say so and stop. The person runs that git by hand.

## Guard check

At the start of the first use in a session, run `git push --dry-run` typed raw once. The hook must deny it.

- Denied: the guard is active, continue.
- Not denied: report "guard: missing" to the user, and give this install sentence: "Install or update the plugin with `/plugin install groundwork@groundwork` or `/plugin marketplace update groundwork`, then restart Claude Code." Do not continue until the user says to.

The hook reads `SITEOFFICE_GIT_GUARD=off` from the environment Claude Code was started with and then allows everything. Only a human sets it, for example before running the `commit-and-push-all` skills.
