---
name: siteoffice-cleanup
description: Clean up a Siteoffice tree or any folder tree an AI works in, in three read-first passes, folders (stale, empty, junk, unprotected, dirty repos), documents (unreferenced and idle files), and instruction files (compact a grown AGENTS.md or CLAUDE.md to about 80 lines by moving detail to STATUS.md and SESSION_LOG.md, never deleting). Use on "clean up my folders", "what is stale", "which files can go", "compact my CLAUDE.md", "my instruction file is too long", "tidy the hub", "opruimen", "welke bestanden kunnen weg", "maak mijn CLAUDE.md korter", or before a move with siteoffice-migrate. Do NOT use to move or rename folders (siteoffice-migrate) or to create a tree (siteoffice-setup).
---

# Siteoffice cleanup

A working tree grows three kinds of weight: folders nobody opens, documents nobody links to, and
instruction files that turned into history books. Each costs context on every AI session that
reads the tree. This skill finds all three, asks the user row by row, and moves rather than
deletes. Nothing here is specific to any company or path; examples use `C:\siteoffice\<holding>`.

Read-only until the user says apply. Deletions are always the user's own step, after the
decisions are written down.

## When to use this skill

- "What is stale", "which folders or files can go", "tidy the hub"
- An instruction file (AGENTS.md, CLAUDE.md) is long, and new sessions read history before rules
- Before a move with `siteoffice-migrate`: the audit here feeds its migration map
- A recurring quarterly hygiene pass on a unit
- Not for moving or renaming (`siteoffice-migrate`), not for creating a tree (`siteoffice-setup`)

## Step 0: pick the passes

Ask one multiple-choice question: folders, documents, instruction files, or all three. Default
is all three in that order; each pass ends with the user's decisions written to a CSV or a
findings file before the next starts. State the root and, where the tree has a manifest, read
`siteoffice.json` for the node type and the canonical instruction file name.

**Output:** the passes chosen, the root, the unit type.

## Pass 1: folders (script: `Invoke-SiteofficeAudit.ps1`, read-only)

One row per folder to a depth, with file count, size, days idle, git state (last commit, dirty,
unpushed, remote) and flags: Empty, Stale, Junk (caches, build output, virtual environments),
Worktree, Reports, NameSaysTemporary, NoGit, Dirty, Unpushed, Link.

```powershell
# runs in: PowerShell on the laptop. cwd: this skill's scripts folder. Read-only, writes one CSV.
cd <skill folder>\scripts
.\Invoke-SiteofficeAudit.ps1 -Root 'C:\siteoffice\<holding>' -Depth 2 -StaleDays 60 -OutCsv 'C:\siteoffice\<holding>\_log\2026-01-31_folder_audit.csv'
```

Then walk every flagged row with the user, one question per group of related rows (regenerable
folders together, one stale project at a time, root loose files together). Verdicts: keep,
archive, delete, ask. Junk folders are regenerable: exclude and reinstall, never archive. Dirty
repos are committed before anything is copied or archived. Write the verdicts into the CSV's
`Decision` column and, when a move follows, into the `siteoffice-migrate` map.

**Output:** the CSV with every flagged row decided, plus the list of commits the user makes first.

## Pass 2: documents (script: `Invoke-SiteofficeOrphans.ps1`, read-only)

Documents nobody refers to by filename anywhere in the tree, with age. Entry points (instruction
files, STATUS, DEADLINES, SESSION_LOG, README, roadmap, decisions, manifests) are never listed.

```powershell
# runs in: PowerShell on the laptop. Read-only, writes one CSV. Run per unit, not per estate: the reference check is tree-local.
cd <skill folder>\scripts
.\Invoke-SiteofficeOrphans.ps1 -Root 'C:\siteoffice\<holding>\10_holding' -StaleDays 60 -OutCsv 'C:\siteoffice\<holding>\10_holding\04_assets\2026-01-31_orphans.csv'
```

Walk the stale unreferenced rows with the user. Verdicts: keep (add a pointer from the file
that should reference it), history (`03_logs\_history\`), archive (`_archive\`), delete, ask.
Date-prefixed assets older than the stale window with no reference are the usual history
candidates; a decision record or a plan is kept even when unreferenced, it is the record.

**Output:** the CSV with decisions, and the move list (from, to) for the user to apply.

## Pass 3: instruction files (no script; judgment, read the file first)

Target: the canonical instruction file of a unit is an entry point of about 80 lines. Shape:

```
# <Unit>: <one-line tagline>
> Sibling files: STATUS.md (detailed current state), SESSION_LOG.md (history), <roadmap file> (planning), <parent instruction file> (rules inherited).
## What this is            2 to 4 sentences: identity, purpose, owner, where it runs
## Current state           one paragraph, ending "Detailed status: STATUS.md."
## Key constraints         at most 8 bullets: the rules a brand-new session must know on first read
## Folder map              at most 15 lines, one role per folder
## How to brief a new chat read this file, open STATUS.md, check DEADLINES.md, end of session append to SESSION_LOG.md
```

What moves where, see `references\instruction_file_moves.md`: latest-change narrative to
`SESSION_LOG.md` (dated), status tables and per-track detail to `STATUS.md`, architecture
step-by-step to `STATUS.md` under Architecture, dates to `DEADLINES.md`, priorities to the
roadmap file, house rules that grew a story to a standards file with a one-line pointer.

Sequence per unit: count lines; list what moves and where; write the compact file to a temp
path; diff against the original and confirm nothing is dropped, only moved; write or extend
`STATUS.md` (never create a second status file when one exists under another name, point to it);
append one dated paragraph to `SESSION_LOG.md`; show the user `git diff --cached --stat` and the
instruction-file diff; one commit per unit, pushed only if the unit has an upstream. On a mount
known to truncate large writes (Cowork on a synced Windows folder, roughly 6 to 7 KB), write every file this
pass touches, including the compacted instruction file itself, via a bash heredoc and confirm
with `wc -c`; a byte target (some units gate on KB, not lines) needs the same check.

**Output per unit:** before and after line counts, files created or extended, the commit, and
one line per judgment call (what was tightened, what was kept verbatim as a guardrail).

## Rules

- Never delete content in pass 3. Removed text lands in STATUS.md, SESSION_LOG.md or a standards
  file; the diff shows movement, not loss.
- Never rewrite a guardrail's meaning. "Never assume" lists and refusals can be tightened, not
  reworded, and every tightening is shown to the user.
- Preserve every cross-reference; a pointer that names a file keeps naming it.
- Deletions from pass 1 and 2 are the user's own step, after the CSV holds the decision.
- One commit per unit. Hub edits and project edits never share a commit.
- Scripts stay pure ASCII. A non-ASCII character in a `.ps1` is a script that does not run.
- Answer in the conversation's language. No em dashes in anything on disk.

## Final deliverable

One table: unit, folders flagged and decided, documents moved, instruction file lines before and
after, STATUS.md created or extended, committed, pushed. Plus the judgment calls, one line each.
