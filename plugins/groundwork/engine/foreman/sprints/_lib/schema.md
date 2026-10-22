# Sprint state schemas

This is the canonical source of truth for `sprint.json` and `action-log.ndjson` formats, referenced by `sprint.md` and `render_dashboard.py`.

## `sprint.json`, current state, overwritten each phase boundary

```json
{
  "schema_version": 2,
  "sprint_id": "jarvis__voice-latency-fix__20260707T140502Z",
  "project": "jarvis",
  "slug": "voice-latency-fix",
  "spec_path": "C:\\work\\_archive\\jarvis\\docs\\superpowers\\specs\\2026-07-05-voice-latency-fix-design.md",
  "plan_path": "C:\\work\\_archive\\jarvis\\docs\\superpowers\\plans\\2026-07-06-voice-latency-fix.md",
  "worktree_path": "C:\\work\\_archive\\jarvis\\.worktrees\\sprint-voice-latency-fix",
  "worktree_branch": "sprint/voice-latency-fix",
  "max_iterations": 5,
  "iteration": 2,
  "phase": "dev",
  "status": "running",
  "created_at": "2026-07-07T14:05:02Z",
  "updated_at": "2026-07-07T14:41:19Z",
  "last_test_result": {
    "command": "pytest -q",
    "exit_code": 1,
    "summary": "3 failed, 47 passed",
    "at": "2026-07-07T14:38:11Z"
  },
  "last_verify_result": {
    "skill": "superpowers:verification-before-completion",
    "outcome": "fail",
    "summary": "wake-word latency regression at 780ms (target <500ms)",
    "at": "2026-07-07T14:41:19Z"
  },
  "tasks": [
    {"n": 1, "title": "agent-runtime package scaffold", "state": "done",
     "attempts": 1, "commit": "3f9a1c2", "started_at": "2026-07-07T14:12:04Z",
     "ended_at": "2026-07-07T14:13:48Z", "model": "haiku"},
    {"n": 2, "title": "tool-call envelope + zod schemas", "state": "running",
     "attempts": 1, "commit": null, "started_at": "2026-07-07T14:14:02Z",
     "ended_at": null, "model": "sonnet"}
  ]
}
```

- `phase` ∈ `{"spec","plan","dev","test","iterate","report","done"}`.
- `status` ∈ `{"running","passing","capped","failed","escalated","paused"}`.
- Timestamps are ISO 8601 UTC with trailing `Z`, second precision.
- `last_test_result` / `last_verify_result` may be `null` before the first run.
- `tasks` (optional, schema_version 2): one entry per plan task, written once
  at Phase 1 exit from the plan file that phase just produced. Absent `tasks`
  means a pre-v2 sprint; renderers treat that as "no task view", the same way
  they already treat a missing `model` field as "not recorded." Field shape:
  `n` (integer, 1-based, matches the plan's task numbering), `title` (string),
  `state` ∈ `{"pending","running","review","fixing","done","blocked"}`,
  `attempts` (integer, starts at 0, incremented on each `task_start`),
  `commit` (short sha string or `null`), `started_at` / `ended_at` (ISO 8601
  `Z` timestamp or `null`), `model` (model class string or `null`).

## `action-log.ndjson`, append-only receipts, one JSON per line

```json
{"ts":"2026-07-07T14:05:02Z","iteration":0,"phase":"spec","actor":"orchestrator","action":"sprint_start","outcome":"info","detail":"spec resolved: docs/superpowers/specs/2026-07-05-voice-latency-fix-design.md","ref":null}
{"ts":"2026-07-07T14:05:14Z","iteration":0,"phase":"plan","actor":"agent:writing-plans","action":"phase_enter","outcome":"info","detail":"no plan file found, invoking superpowers:writing-plans","ref":null}
{"ts":"2026-07-07T14:12:43Z","iteration":1,"phase":"dev","actor":"agent:implementer","action":"dispatch","outcome":"pass","detail":"task 1 of 4 implemented and committed to worktree","ref":"C:\\work\\_archive\\jarvis\\.worktrees\\sprint-voice-latency-fix"}
{"ts":"2026-07-07T14:38:11Z","iteration":1,"phase":"test","actor":"test","action":"test_run","outcome":"fail","detail":"pytest -q -> exit 1, 3 failed / 47 passed","ref":null}
{"ts":"2026-07-07T14:41:19Z","iteration":1,"phase":"test","actor":"verify","action":"verify_run","outcome":"fail","detail":"wake-word latency regression 780ms vs target <500ms","ref":null}
{"ts":"2026-07-07T14:41:20Z","iteration":2,"phase":"iterate","actor":"orchestrator","action":"iterate","outcome":"info","detail":"feeding failure back into dev phase","ref":null}
```

Field domains:

- `actor` ∈ `{"human","orchestrator","agent:<type>","test","verify"}` where `<type>` is a superpowers skill name or `implementer` / `reviewer` / `fix`.
- `action` ∈ `{"sprint_start","phase_enter","phase_exit","dispatch","decision","test_run","verify_run","iterate","cap_hit","escalate","sprint_done","task_start","task_review","task_fix","task_done"}`.
- `outcome` ∈ `{"pass","fail","info","blocked"}`.
- `detail` ≤ 240 chars, no newlines. `ref` may be `null`.
- `model` (optional): the actual model class (`opus`/`sonnet`/`haiku`/`fable`) that ran this
  entry's dispatch, when known. Absent on entries where no subagent dispatch occurred, and on
  every entry written before this field existed: the renderer treats a missing `model` as
  "not recorded," not as an error.
- `task` (required on `task_start`/`task_review`/`task_fix`/`task_done`, absent on every other
  action): integer matching a `sprint.json.tasks[].n` entry. These four actions are the
  per-task boundary receipts: `task_start` when an implementer subagent is dispatched for the
  task, `task_review` when its reviewer subagent runs, `task_fix` when a review finding sends
  it back for a fix pass, `task_done` when the task reviewer approves (`outcome="pass"`) or the
  task is abandoned/blocked (`outcome="blocked"`).

## Slug generation

1. If the spec argument is an existing file whose basename matches the regex `^\d{4}-\d{2}-\d{2}-(?P<slug>.+?)(?:-design|-spec)?\.md$`, use `<slug>`.
2. Else lowercase the description, replace any run of non-`[a-z0-9]` with `-`, strip leading/trailing `-`, and take the first 6 hyphen-separated tokens.
3. If `C:\work\10_holding\sprints\<project>\<slug>\` already exists and its `sprint.json` has `status` ∈ `{"done","capped","failed"}`, append `-2` (then `-3`, …) until the directory is free. If it exists and status is `running`/`paused`, that's a **resume**, reuse the directory (see spec §Resuming).

## Progress-percent formula

Fixed phase weights sum to 100:

| phase | weight |
|---|---|
| spec | 5 |
| plan | 10 |
| dev | 40 |
| test | 15 |
| iterate | 25 |
| report | 5 |

Progress % = sum of weights for **completed** phases (this iteration's already-passed steps) + a fractional term for the current phase, capped at 99 while `status == "running"`, becoming 100 exactly when `phase == "done"`.

The fractional term is `(iteration / max_iterations) * weight[current_phase]` for every phase **except** `dev` when `sprint.json.tasks` is present and non-empty, in which case it is `(tasks_done / tasks_total) * weight["dev"]` instead (`tasks_done` = count of entries with `state == "done"`). A `dev` phase with no `tasks` array (pre-v2 sprints) or an empty one keeps the `iteration / max_iterations` formula. This is the reason `tasks[]` exists at all: `iteration` counts full dev→test→iterate loops, not work done inside a single long dev phase, so the old formula guessed at dev progress rather than measuring it.
