---
name: siteoffice-checkup
description: Weekly read-only maintenance check of a Siteoffice tree, skills (frontmatter, size, routing, risky commands), plugins (manifests, one version clock, published versus routed, drift), instruction files (size, em dashes, stale paths, broken imports) and folders (git locks, unit files), plus scheduled tasks against their inventory. Use on "run the checkup", "maintenance check", "health check", "is everything still in order", "onderhoudscheck", or from the weekly scheduled task. Do NOT use to fix or delete anything (siteoffice-cleanup), for instruction drift across harnesses (audit-ai-instructions), or for context usage (audit-my-context).
---

# Siteoffice checkup

One weekly pass that tells you what broke or slid since last week, before it costs a session.
It reads and reports. It never edits, publishes, promotes, deletes or runs git. The only file it
writes is its own dated report.

`<root>` below is the top folder of your Siteoffice tree. `<holding>` is its holding folder
(`10_holding` in the standard layout).

## Steps

1. **Run the script.** Your first tool call runs `scripts/checkup.py`.

   ```bash
   # runs in: bash (macOS, Linux or a Cowork sandbox). Needs <root> attached to the session.
   ROOT="<root>"
   python3 "<skill folder>/scripts/checkup.py" --root "$ROOT" --out "$ROOT/<holding>/03_logs/checkup/$(date +%F)_checkup.md"
   ```

   ```powershell
   # runs in: PowerShell on Windows. cwd: any.
   $root = '<root>'
   python '<skill folder>\scripts\checkup.py' --root $root --out "$root\<holding>\03_logs\checkup\$(Get-Date -Format yyyy-MM-dd)_checkup.md"
   ```

   **Done when:** the report file exists and the script exited 0 (warnings only) or 1 (fails).
   Exit 2 means the root is not attached: say so and stop.

2. **Check scheduled tasks.** If you keep a table of scheduled tasks, for example
   `<root>\<holding>\04_engineering\scheduled_tasks.md`, call `list_scheduled_tasks` and compare
   every live task with that table. Report three lists: live but not documented, documented but
   not live, and enabled tasks whose `lastRunAt` is older than their schedule plus one day. No
   table: say so and skip. **Done when:** every live task and every table row is in exactly one
   bucket or marked matched.

3. **Check the model trigger.** Read the newest earlier report in
   `<root>\<holding>\03_logs\checkup\`. If the model ID you run on differs from the one it names,
   add a FAIL line: "model changed, re-check your skills against the new model". If you keep a
   written refresh routine, name it in that line. Append a line `Model: <your model ID>` to this
   week's report. **Done when:** the line is in the report.

4. **Compare with last week.** From the previous report, list what is new, what is fixed and what
   is still open. A FAIL open for two reports in a row goes to the top of your reply.

5. **Reply.** TL;DR first: fail and warn counts, the top three items, and the report path. Then
   one line per FAIL with the skill or command that fixes it. No fixes applied.

## What each check means

| ID | Checks | Fixed by |
|---|---|---|
| S1 to S6 | Skill frontmatter, SKILL.md over 500 lines, em dashes, routing coverage, unpinned `npx`, `.env` reads, download piped to a shell, zero-width characters, a `.env` file inside a skill folder | Edit the master in `skills\`, then publish with a bump |
| P1 | Every marketplace entry matches its `plugin.json` name, and all manifests share one version | `publish-skills.ps1 -Apply -Bump <level>` |
| P2 | Published skills against `routing.json`, per target | `publish-skills.ps1 -Apply -Bump <level>` |
| P3 | `check-drift.py`: a published copy edited outside the master | `promote.ps1` or a republish, your call |
| I1, I2, I4, I5 | CLAUDE.md, AGENTS.md, MEMORY.md: over 200 lines or 25 KB, em dashes, broken `@` imports, backticked paths that do not exist | `siteoffice-cleanup` instruction pass |
| F1, F2 | Stale `.git\index.lock`; a unit under `50_projects` without AGENTS.md or CLAUDE.md or `siteoffice.json` | Delete the lock by hand; `siteoffice-setup` for the unit files |

The skill and plugin checks (S, P) run only when the tree holds a skills repo at the default
`50_projects/znd-skills`, or the one you pass with `--skills-repo` and `--public-repo`.

## When the check cannot run

If `<root>` is not attached, or the script is missing, say which and stop. Do not report "all
clear" for a check that did not run: name every skipped check. The agents-dir target lives
outside `<root>`, so drift there is only checked when `%USERPROFILE%\.agents\skills` is
attached too.
