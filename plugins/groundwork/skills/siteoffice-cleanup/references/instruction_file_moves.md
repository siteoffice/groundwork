# Instruction file: what moves where

The canonical instruction file (AGENTS.md or CLAUDE.md, one of them imports the other) is read
by every AI session before any work. Every line that is not a rule or a pointer costs context on
every session. This table is the routing for pass 3.

| Content found in the instruction file | Destination | Leave behind |
|---|---|---|
| "Latest change", "recent work", anything dated to one session | `SESSION_LOG.md`, one dated paragraph, newest on top | nothing, or one sentence in Current state |
| Status tables, per-track or per-product state | `STATUS.md` under Detailed tracks | "Detailed status: STATUS.md." |
| Pipeline or architecture described step by step | `STATUS.md` under Architecture detail | one sentence in What this is |
| Long open-items lists | `STATUS.md` under Open items | nothing |
| Hard dates, gates, expiries | `DEADLINES.md`, most imminent on top | pointer in How to brief |
| Priority order, allocation, plans | the roadmap file (`02_roadmap\roadmap.md`) | pointer in How to brief |
| A house rule that grew an incident story | a standards file (`docs\standards\<topic>.md`) | the rule in one line, with the pointer |
| Decisions with their why | `03_logs\decisions.md` | nothing |
| Folder map longer than 15 lines | trim to top level; details in `STATUS.md` or a README per folder | the trimmed map |
| Duplicates of a fact that lives in another file | delete the copy, keep the pointer | the pointer |

Kept in the instruction file, always: identity (2 to 4 sentences), the constraints a new session
must know before its first action (at most 8 bullets), the refusals (what stays human), the
folder map, the brief flow, the sibling-file line.

## STATUS.md shape

```
# <Unit> - Status
## Snapshot (YYYY-MM-DD)
## Detailed tracks
## Open items
## Architecture detail   (only if the instruction file had it)
```

If a file already serves this role under another name (a portfolio file, a state page), point to
it instead of creating STATUS.md. Two status files drift and the stale one wins whichever tool
loads it.

## Verification before commit

1. Line count before and after (`(Get-Content <file>).Count` in PowerShell).
2. Every removed paragraph is found verbatim or tightened in its destination file.
3. Every filename mentioned in the old version is still mentioned in the new one or its
   destination.
4. Guardrail bullets: same set, same meaning; tightening shown to the user as a before and after.
5. `git diff --cached --stat` shows only the unit's instruction file, STATUS.md and SESSION_LOG.md
   (plus a standards file if one was created).
6. On a mount known to truncate large writes (Cowork on a synced Windows folder, roughly 6 to 7 KB for Write and
   Edit alike), write the compacted file via a bash heredoc and confirm with `wc -c`, never trust
   a Read-back. If the compacted file itself lands over that threshold (an 80-line target can
   still exceed 7 KB with wide tables or long paths), it must be written the same way.

## Worked example: a tree-wide root AGENTS.md, 2026-09-09

Tree-wide root file, not a per-unit one, so the byte size mattered more than the line count (the
tool-scoping plan's own gate was "under 8 KB", not a line count). 28,927 to 7,908 bytes. Four
house-standard rules that had grown a full incident narrative moved to
`docs\standards\<topic>.md` (scheduled-task hidden window and exit code, a pytest `os.name`
patch bug, dated Cowork MCP tool findings, backup coverage), one line plus a pointer left in
place per row 15's pattern. One whole duplicated section, "Imported Claude Cowork project
instructions", a full copy of the hub's own Cowork project instructions pasted into the
tree-wide file, was deleted outright per row 18 rather than trimmed, since the hub's `CLAUDE.md`
already carries it and a pasted-in duplicate is exactly what goes stale first. Watch for this
shape specifically: a heading like "Imported X project instructions" or "Imported Y instructions"
followed by a near-complete copy of another file's content is a row-18 duplicate, not a
row-1-through-14 candidate, even though it reads like ordinary prose rather than a table or a
dated list.
