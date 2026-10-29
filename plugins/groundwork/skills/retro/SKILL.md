---
name: retro
description: Monthly check-up of your own Claude Code and Cowork sessions of the last N days, script only. Collects transcripts on this machine, scrubs secrets, counts human touches per finished piece of work, silent failures (headless runs that end in an API error, hooks and connectors failing in 3 or more sessions, scheduled tasks without a success line) and secret exposures, and writes a counts-only report with a month-over-month trend. R1 measures only; guided apply arrives in R2. Use on "retro", "monthly checkup of my sessions", "what went wrong in my sessions", "improve my setup". Do NOT use for a weekly tree health check (siteoffice-checkup), for context and tool usage (audit-my-context), to diagnose one superpowers session (diagnosing-superpowers), or to apply changes (this skill never does).
---

# Retro

A monthly look back at your own sessions. R1 measures only: it counts, it runs no agent analysis,
and it changes nothing. Applying fixes arrives in R2.

`<skill folder>` is the folder holding this SKILL.md. `<root>` is your Groundwork ROOT.

## Steps

1. **Find the folder.** Run `retro_config.py where`.

   ```powershell
   # runs in: PowerShell 7. cwd: any.
   py -3 -I '<skill folder>\scripts\retro_config.py' where
   ```

   It prints the root and how it was found. Config exists in `<root>\retro\config.json`? Skip to
   step 2. First run: ask these one at a time, each with a recommended answer. ROOT (the found one,
   else `~/Documents`), period in days (30), token ceiling (stored for R2, empty for none),
   separate-world folders each with a report folder, gate patterns. Then save them.

   ```powershell
   # runs in: PowerShell 7. cwd: any. Repeat --world and --gate-pattern as needed.
   $root = '<root>'
   py -3 -I '<skill folder>\scripts\retro_config.py' init --root $root --period 30 --world '<world folder>=<report folder>' --gate-pattern 'go ahead*'
   ```

   Gate patterns are case-insensitive globs: `*` is any run, `?` is one character, everything else
   is literal. A pattern matches the whole text of a user turn up to 200 characters. An empty
   pattern or one over 80 characters is refused: init exits 2 and nothing is saved.

   **Done when:** init printed `created` or `updated` and exited 0.

2. **Run the scan.** One call collects, estimates and writes the report.

   ```powershell
   # runs in: PowerShell 7. cwd: any.
   $root = '<root>'
   py -3 -I '<skill folder>\scripts\retro_report.py' --root $root --days 30
   ```

   ```bash
   # runs in: bash (macOS or Linux, best effort, unverified). cwd: any.
   ROOT="<root>"
   python3 -I "<skill folder>/scripts/retro_report.py" --root "$ROOT" --days 30
   ```

   It prints the paths of `report.md`, `report.html` and `metrics.json`. Exit 2 means a config
   or root problem: print the message and stop. **Done when:** exit 0 and `report.md` exists.

3. **Reply.** Read `report.md`, then give a TL;DR and these, from the report only:
   - the headline with its unit, and the trend (or "no trend" as the report says)
   - silent failures
   - secret exposures: session, date and kind only. Fix: rotate the secret, fix the code that
     wrote it, clean every copy.
   - hook and MCP health, skills used and unused
   - the R2 estimate. It shows assumptions, not live prices.
   - the report path. Separate worlds get their own report; the main report only says
     "N separate worlds analysed, report in <path>".

4. **Optional monthly scan.** Show the plan, which registers nothing, and give the user the
   register command to run.

   ```powershell
   # runs in: PowerShell 7. cwd: any.
   $root = '<root>'
   & '<skill folder>\scripts\register_retro_task.ps1' -Root $root
   ```

   The output ends with the exact `schtasks.exe` line and the command to register: the same call
   plus `-Register`. The user runs it, never you. After the first scheduled run the user checks it
   with the same call plus `-Verify`, which reads Last Result (0 is success). macOS and Linux:
   `<skill folder>/scripts/retro.launchd.plist.example` and `retro.cron.example`, copied and
   edited by the user.

5. **Live test.** The live test is armed with `GROUNDWORK_RETRO_LIVE=1`. Without it the report
   marks it SKIPPED. Say which one applied.

## What it never does

- Edit your settings, hooks, skills or CLAUDE.md files, or apply any fix (R2).
- Launch `claude -p` or any agent, or upload anything. Transcripts are read locally only.
- Register the scheduled task by itself.
- Open a `.env*` file. Any path with a component starting `.env` is refused, so a home path with
  such a component yields zero sessions.
- Store a secret. A hit is scrubbed and becomes a `secret_exposure` finding with session, date, kind.
- Put a separate world's content into the main report.

## When it cannot run

Python missing, or exit 2 from a script: say so with the printed message and stop. Never report
"all clear" for a scan that did not run. Windows and Cowork paths are verified; macOS is a best
effort and unverified.
