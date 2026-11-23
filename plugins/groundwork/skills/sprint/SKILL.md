---
name: sprint
description: >-
  Builds one approved step as a sprint with you present: a task list, its own branch, the spec's tests, then a blind check by a fresh agent, five rounds at most, and a report with a Close-out block. Use on "run a sprint", "build this step as a sprint", "sprint status". Do NOT use for a quick edit, or for any run without a person present.
---

# Sprint

A sprint builds one step against a written spec. It checks the result twice, first with the
spec's tests and then with a blind check by a fresh agent. It ends with a report. You stay in
the session the whole time and decide what ships.

## What this skill is, and is not

- It builds one step against a written spec, checks it twice (tests, then a blind check) and
  writes a report.
- It never merges, pushes or tags. Only the person ships.
- It never runs without the person. It calls no engine script other than `sprint_step.py` and
  `render_dashboard.py`, and it never starts a headless session or a background run.
- The records are plain files. They help you resume and review. They are your own notes, not
  proof to anyone else.

## Setup (once per session)

Both scripts are in this plugin. From this skill's folder they are
`..\..\engine\foreman\sprints\_lib\sprint_step.py` (records an action) and
`..\..\engine\foreman\sprints\_lib\render_dashboard.py` (shows the status). You need Python 3.9
or newer and nothing else. If `python` is not found on Windows, use `py -3` in its place.
Optionally, `engine\foreman\sprints\_lib\model_map.py` can pick a model per phase. A sprint does
not need it.

```powershell
# runs in: PowerShell on Windows. cwd: the project folder.
$LIB = '<full path to the plugin>\engine\foreman\sprints\_lib'
$STEP = "$LIB\sprint_step.py"
$DASH = "$LIB\render_dashboard.py"
$ROOT = (Get-Location).Path
python --version
```

```bash
# runs in: bash or zsh on macOS or Linux. cwd: the project folder.
LIB='<full path to the plugin>/engine/foreman/sprints/_lib'
STEP="$LIB/sprint_step.py"
DASH="$LIB/render_dashboard.py"
ROOT="$PWD"
python3 --version
# In the record lines below, write python3 "$STEP" "$SPRINT" in place of python $STEP $SPRINT.
```

If Python is missing, say so and stop.

## The spec

The sprint starts from a spec file with six sections: Problem, Scope, Tests, Verification,
Out of scope, Success criteria. Check it before planning:

- Each of the six sections is present and not empty.
- Every test command is named, with where it runs.
- The text has no em dash.
- Each success criterion is something a reader can check.

If an item is missing, say which one and ask the person before you plan.

## The plan

Show the person a numbered task list. Once they agree, write `sprint.json` in
`ROOT\sprints\<project>\<slug>\` from this template. Replace every `<...>` value. Use a UTC time
such as `2026-11-22T09:00:00Z` for `<now>`. Keep one entry per task in `tasks`, numbered from 1.

```json
{
  "schema_version": 2,
  "sprint_id": "<project>__<slug>__<stamp>",
  "project": "<project>",
  "slug": "<slug>",
  "run_mode": "attended",
  "spec_path": "<spec path>",
  "plan_path": "<plan path>",
  "worktree_path": "<worktree path>",
  "worktree_branch": "<branch>",
  "max_iterations": 5,
  "iteration": 0,
  "phase": "plan",
  "status": "running",
  "created_at": "<now>",
  "updated_at": "<now>",
  "last_test_result": null,
  "last_verify_result": null,
  "tasks": [
    {"n": 1, "title": "<task 1 title>", "state": "pending", "attempts": 0, "commit": null, "started_at": null, "ended_at": null, "model": null}
  ]
}
```

Then point `$SPRINT` at the folder and record the start:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $ROOT from Setup.
$SPRINT = "$ROOT\sprints\<project>\<slug>"
python $STEP $SPRINT --phase plan --action sprint_start --actor orchestrator --iteration 1 --detail "spec read, task list written"
```

A `--detail` is one line of at most 240 characters. A newline or a longer text makes the command
fail. Every record call also redraws the board into `ROOT\_board\`. The records in
`ROOT\sprints\` and the board in `ROOT\_board\` belong to the person. Never stage them in a code
commit. The person decides whether to keep them.

## Its own branch

Never work on `main` or `master`.

- **In a Siteoffice repo,** start the branch with the `siteoffice-git` step `start <slug>`. It
  makes `work/<owner>/<slug>`. Every commit is a `save` step of that skill.
- **Elsewhere,** the plugin's git guard stops the agent from running raw git. Show the person the
  lines to run. Before task 1: `git switch -c sprint/<slug>`. After each task:
  `git add <paths>`, then `git commit -m "<type>(<scope>): <subject>"`. Name the paths of the
  code only, never `sprints\` or `_board\`.

The agent can write code only when the agent policy is in `work` mode. In `read-only` mode it
cannot. The person sets `"mode": "work"` in `.groundwork\agent-policy.json` in the project,
because the agent cannot write inside `.groundwork\`. After the sprint, the person sets it back
to `read-only`.

## The loop, five rounds at most

For each task, record the start, build the task, then record the end:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase dev --action task_start --actor agent:implementer --task 1 --detail "task 1 started"
python $STEP $SPRINT --phase dev --action task_done --actor agent:implementer --task 1 --outcome pass --commit abc1234 --detail "task 1 done"
```

Use `--outcome blocked` when a task cannot finish. When every task is done, run the tests the
spec names and record the result:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase test --action test_run --actor test --outcome pass --detail "pytest -q: exit 0, 12 passed"
```

Then the **blind check**. Start a fresh sub agent. Give it the spec path, the diff against the
base branch and the success criteria, and nothing else: no summary from the builder. It answers
pass or fail for each criterion, with the evidence. Words such as "should work" or "probably"
count as not verified. Record it:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase test --action verify_run --actor verify --outcome pass --detail "blind check: 5 of 5 criteria met"
```

If both pass, write the report. Otherwise start the next round: record it, give the findings to
the builder and go again.

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase iterate --action iterate --actor orchestrator --iteration 2 --detail "round 2: two findings fed back"
```

If round five ends without a pass, record the cap and go to the report:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase iterate --action cap_hit --actor orchestrator --outcome blocked --status capped --detail "round 5 ended without a pass"
```

## When to stop and ask

Stop for a destructive action, a secret, or doubt about what done means. Record it, ask, and end
the turn:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase dev --action escalate --actor orchestrator --outcome blocked --status paused --detail "stopped to ask: what done means for task 1"
```

## The report

Write `report.md` in the sprint folder. It holds the result, the test counts, the blind check's
findings and what was not done. It ends with a `## Close-out` block. Every leftover is one line
that starts with DO, ASK, HAND or DROP, followed by its reason. Then close the sprint:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $STEP and $SPRINT.
python $STEP $SPRINT --phase done --action sprint_done --actor orchestrator --outcome pass --status passing --detail "report written"
```

Use `--status capped` or `--status failed` when that is the result, and `--outcome fail` with it.
The skill writes the Close-out block. No code enforces it.

## Status

This shows where the sprint stands:

```powershell
# runs in: PowerShell on Windows. cwd: the project folder. Needs $DASH and $SPRINT.
python $DASH status $SPRINT --no-color
```

Ignore the `Resume:` lines at the bottom. They name a command this plugin does not ship.

## How it fits the one loop

After the spec gate of `one-loop` is approved, the work step may be a sprint. After the report,
go back to `one-loop`, where `advance` opens the accept gate. Only the person ships.

## Reply format

Short, outcome first:

- Say what happened in one line, then what the person must decide, in plain words.
- Name a round or a task only when it failed or changed the plan.
- End with the single next step and whose move it is.
