---
name: siteoffice-migrate
description: Move, restructure, rename or adopt a Siteoffice tree (holding, company, department, project) or any folder tree an AI works in, without losing state that tools key by path. Modes are audit (stale, junk or unprotected folders, read-only), inventory (every hard-coded path before a move, read-only), restructure (map a grown tree onto the Siteoffice layout at a new root, with dry run, apply and path rewrite), relocate (same layout, new root), rename, adopt. Use on "move my hub to a new drive or folder", "bring our folders into the Siteoffice structure", "what still points at the old path", "which folders are stale or can go", "rename this project folder", "make this existing folder a Siteoffice unit", "path inventory", or Dutch "verhuis onze mappen", "zet onze structuur om", "welke mappen kunnen weg". Do NOT use to create a new tree (siteoffice-setup) or to scope tools per unit (siteoffice-scope).
---

# Siteoffice migrate

A folder tree that an AI works in is never just files. Scheduler tasks, hooks, MCP configs,
plugin routing, WSL mounts, editor workspaces and Claude Code's own approvals and memory all hold
the path. Move the folder and each of those breaks in its own way, some silently. This skill
finds them first, moves or restructures with a rollback, and prints the steps no script can do.

Works for any customer, any root, any Siteoffice tree or loose folder. Nothing in this skill
names a specific company or path; examples use `C:\old-root` and `C:\siteoffice\<holding>`.
Scripts are pure ASCII PowerShell 5.1 (bash second, later). Read-only until the user says apply.

**Status:** `audit`, `inventory`, `restructure` (with `relocate` as its one-entry case), the
path rewrite, the four tool-state steps (Task Scheduler, Claude Code state, Claude Desktop
and Cowork stores, shortcuts), `rename`, `adopt` and `rename-to-rule` all have scripts in
`scripts\`. The restructure path ran on one real estate (a 47k-file work tree with nine git
repos, September 2026); `rename-to-rule` then ran on the same estate (October 2026) and its
fixes from that run are in (plugin 2.6.3 adds relative forms and ledger keys to the Mode 2
rewrite and finds a renamed folder in any segment of a relative form); `rename` and `adopt` ran only
on synthetic scratch trees so far.
Still: run dry, read the plan, test on a throwaway tree, then apply.

**Where the estate bit back, in one paragraph.** The text rewrite is the easy half. What broke
after the move was state that names the path outside the tree, or code that derives paths from
the tree's shape: scheduled-task shims, Claude Code approvals keyed by path, Cowork project
folders in an app store the UI cannot edit, a Desktop shortcut (binary, invisible to grep), a
script that walked `parents[3]` and recreated the old folder every ten minutes. The inventory
lists all of these now (sources `task`, `claude-json`, `claude-projects`, `claude-desktop`,
`shortcut`, `layout`), and each has a script or a hand step below.

## When to use this skill

- A whole tree or one unit moves to a new root, drive or machine
- A tree that grew organically must become the Siteoffice layout (holding root plus units)
- Before a move: "what still points at the old path"
- A unit is renamed and its parent, manifest and pointers must follow
- An existing folder should become a Siteoffice unit without moving anything
- Not for creating a tree (`siteoffice-setup`), not for per-unit tool scoping (`siteoffice-scope`)

## The target layout, in one screen

Holding root (from the Siteoffice architecture): `siteoffice.json`, `00_funnel\`, `10_holding\`,
`20_departments\<dept>\`, `40_companies\<company>\projects\<project>\`, `_packs\`, `_board\`,
`_log\`. Every node inside is one unit as `siteoffice-setup` defines it: canonical instruction
file plus a one-line import file, `STATUS.md`, `DEADLINES.md`, `SESSION_LOG.md`,
`siteoffice.json`, `00_context\brief.md`, `02_roadmap\roadmap.md`, `03_logs\decisions.md`,
`03_logs\_history\`, `04_assets\`; containers add `01_projects\` and `03_logs\hub_inbox.md`.
A single-company estate may use the holding root as the company node; record that choice in
`siteoffice.json` (`"holdingIsCompany": true`).

## Step 0: pick the mode

Look for `siteoffice.json` in or above the folder named. Ask one multiple-choice question:
audit, inventory, restructure, relocate, rename, adopt or rename-to-rule. A move (restructure or
relocate) without an inventory for the old root runs inventory first; apply is refused without
one.

## Mode 0: audit (runnable, read-only)

Before deciding what moves, see what is there. One row per folder to a depth, with file count,
size, days idle, git state (last commit, dirty, unpushed, remote) and flags: Empty, Stale, Junk
(caches, build output), Worktree, Reports, NameSaysTemporary, NoGit, Dirty, Unpushed, Link. The
CSV has an empty `Decision` column; walk it with the user, one flagged row at a time (keep,
archive, delete, ask), and feed the answers into the migration map.

```powershell
# runs in: PowerShell on the laptop. cwd: this skill's scripts folder. Read-only, writes one CSV.
cd <skill folder>\scripts
.\Invoke-SiteofficeAudit.ps1 -Root 'C:\old-root' -Depth 2 -StaleDays 60 -OutCsv 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_folder_audit.csv'
```

**Output:** the CSV with decisions filled in, and the map entries that follow from them.

**Output:** the mode, the old root, the new root if any, and whether a manifest tree exists.

## Mode 1: inventory (runnable)

Every hard-coded reference to the old root in all its spellings (`C:\old-root`, `C:/old-root`,
`C:\\old-root`, `/mnt/c/old-root`, `//c/old-root`), in files under the root and under the
per-user tool folders (`~\.claude`, `~\.agents`, `~\.codex`; add application-data folders with
`-ExtraRoots`), plus Task Scheduler actions, Claude Code per-path state (`~\.claude.json`
project keys, `~\.claude\projects\<hash>` folders), Claude Desktop and Cowork stores
(`claude_desktop_config.json`, `spaces.json`), `.lnk` shortcuts on the Desktop and Start Menus,
layout assumptions in code (`parents[n]`, `Split-Path -Parent` chains, `Get-ChildItem <root>`,
and old folder names given with `-LayoutNames`; expect a handful of rows, a count near the
number of code lines means the pattern is broken), and every junction or symlink under the root.
Excluded by default: `.git`, `.worktrees`, `node_modules`, `_archive`, generated folders, and
Claude session transcripts. Read-only. One CSV out.

1. Run on the machine that owns the tree (a sandbox times out on a large tree; say so).

```powershell
# runs in: PowerShell on the laptop. cwd: this skill's scripts folder. Read-only.
# -StaysSubfolders: direct subfolders of the old root that keep their path (a sibling estate).
# -LayoutNames: folder names the map renames (hub, packs); scripts that hard-code them are listed as 'layout'.
cd <skill folder>\scripts
.\Invoke-SiteofficeInventory.ps1 -Root 'C:\old-root' -StaysSubfolders @('Other') -LayoutNames @('hub','packs') -OutCsv 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_path_inventory.csv'
```

   Expected: per-source counts (file, task, claude-json, claude-projects, link), the top 15 files
   by hit count, the CSV path, exit code 0. Nothing changed on disk except the CSV.

2. Read the CSV with the user, sorted by `Note`. `stays subtree`: no rewrite. `generated`:
   regenerate, never edit. `prose path in markdown`: rewrite. Unmarked rows are config and code,
   the ones that break first. `task` rows are re-registered and proven by exit code plus output
   on disk. `claude-json` and `claude-projects` rows are the state Claude Code loses unless the
   entries are copied to the new path or hash. `claude-desktop` and `shortcut` rows are rewritten
   by `Invoke-SiteofficeToolStateRewrite.ps1` with the app quit. `layout` rows are read by hand:
   no rewrite fixes a script that computes its root from the folder depth.

3. Junction rehearsal, zero risk, before any real move: a junction at the new root pointing at
   the old one, one AI session opened from the new path, record what the tool reports
   (`/context`, `/mcp`, approvals asked again or not). Nothing moves.

```powershell
# runs in: PowerShell on the laptop. Rollback: Remove-Item 'C:\siteoffice\<holding>' removes the link only, never the target.
New-Item -ItemType Junction -Path 'C:\siteoffice\<holding>' -Target 'C:\old-root' | Out-Null
Get-Item 'C:\siteoffice\<holding>' | Select-Object FullName, LinkType, Target
```

**Output:** the CSV, the per-source counts, the list of things the move will break, and the
rehearsal findings as `YYYY-MM-DD_migration_findings.md` in the tree's `04_engineering\migration\`
(or `04_assets\` on a unit without an engineering folder).

## Mode 2: restructure (script: `Invoke-SiteofficeRestructure.ps1`, then `Invoke-SiteofficeRewrite.ps1`)

A grown tree becomes the Siteoffice layout at a new root. Driven by one file the user approves
before anything runs: `migration_map.json` (schema and example in
`references\migration_map_schema.md`). Each entry maps an old folder to a new node path with a
node type (holding, company, department, project, packs, archive, stays, classify) and, for git
repositories, whether they move as a clone (re-clone at the new path) or as a folder copy.

1. Propose the map: walk the old root one level deep, classify every folder from its contents
   (a `.git` folder, a manifest, an instruction file, a date-prefixed asset folder), write the
   map with `classify` on everything uncertain. Ask about `classify` rows one at a time.
2. Dry run: the copy plan per entry, the skeleton files each new node lacks, the rewrite plan
   from the inventory (old path to new path per row, `stays` and `generated` skipped), task
   re-registration list, Claude state copy list, and the manual list (Claude Desktop project
   folders, Cowork mounts, VS Code workspaces, WSL paths). Show it. Nothing changes.
3. Apply, after a yes, in this order, each script dry then `-Apply`:
   `Invoke-SiteofficeRestructure.ps1` (robocopy, skeleton files without overwriting anything,
   every node's `siteoffice.json`); `Invoke-SiteofficeRewrite.ps1` (the text rewrite);
   `Invoke-SiteofficeTaskReregister.ps1` (elevated; actions in place, registration scripts for
   shims, proof by LastTaskResult 0 plus output on disk); `Invoke-SiteofficeClaudeStateCopy.ps1`
   (pwsh 7, Claude Code closed; copies `~\.claude.json` keys and `~\.claude\projects` folders);
   `Invoke-SiteofficeToolStateRewrite.ps1` (Claude Desktop quit; `claude_desktop_config.json`,
   Cowork `spaces.json`, `.lnk` shortcuts). Then the hand steps: WSL rc files, editor
   workspaces, per-user skill copies (re-publish from the new root, then check drift), graph
   regeneration. Leave the old root read-only for the rollback window (default 14 days), write
   the rollback note.
4. Verify: inventory again against the NEW root looking for the OLD root's spellings (run the
   inventory with `-Root <old>` but read only rows whose Path starts with the new root). Every
   remaining row is `stays`, a `data` or `archive` entry, or a named exception in the findings
   file. Then `siteoffice-setup` Improve mode fills the thin nodes.

```powershell
# runs in: PowerShell on the laptop. Dry run of the copy plan, then the rewrite plan. Nothing changes without -Apply.
cd <skill folder>\scripts
.\Invoke-SiteofficeRestructure.ps1 -Map 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_migration_map.json'
.\Invoke-SiteofficeRewrite.ps1 -Inventory 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_path_inventory.csv' -Map 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_migration_map.json'
```

```powershell
# runs in: PowerShell on the laptop, after the rewrite. Tool state, all dry runs. Task step needs an elevated window for -Apply; state copy needs pwsh 7 with Claude Code closed; tool-state rewrite needs Claude Desktop quit.
cd <skill folder>\scripts
.\Invoke-SiteofficeTaskReregister.ps1 -Map 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_migration_map.json' -TaskPrefix 'ACME-' -PlanDir 'C:\old-root\<hub>\04_engineering\migration\plans'
pwsh -File .\Invoke-SiteofficeClaudeStateCopy.ps1 -Map 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_migration_map.json'
.\Invoke-SiteofficeToolStateRewrite.ps1 -Map 'C:\old-root\<hub>\04_engineering\migration\2026-01-31_migration_map.json'
```

Always test both on a throwaway tree first (two folders, one fake repo, one file with the old
path in three spellings, one path in backticks, one archive entry), with `-Apply`, and read the
result, before touching the real estate.

Limits, said out loud: `git: clone` entries are copied as folders, not re-cloned (verify
`git remote -v` afterwards, and check that GitHub renames decided in the map were actually made
before re-pointing a remote: `git ls-remote --exit-code -h <url>` answers in one call); WSL rc
files, editor workspaces and skill copies in `~\.agents\skills` are printed as manual steps;
files in per-user tool folders other than the stores named above are listed, not rewritten.
An estate rewritten with the v1 rewrite (before 2026-09-09) has `<newRoot>\<oldName>` paths that
do not exist; `Invoke-SiteofficeEntryFixup.ps1` repairs them, dry run first. The rewrite is
driven by the map. It rewrites absolute spellings of the old root, and since 2.6.3 also relative
forms (`hub\packs\x`, `..\apps\web\x`, `apps\web\x` where the moved folder is a later segment) and
the forward-slash keys of a `.tsv` ledger, with the same safety rules as Mode 6: a form counts only
if a moved folder is on its path and the next segment exists on disk, read in the OLD tree, next to
the file or one of its ancestors up to the old root. The new form is recomputed from where file and
target land (a `..\..` form follows the new depth). Every proposal is written to
`<ReportCsv>_relative.csv`, on a dry run too: read it before `-Apply`. A form that cannot keep its
shape (the target stays while the file moves, or the file left the folder the form was anchored on)
is listed with a `review` note and left alone. Limit: a form is only found when a moved folder is
on its path, so a link between two files that moved by different depths but crosses no moved
folder is not seen; read what is left by hand. History files and folders (`decisions.md`,
`SESSION_LOG.md`, `_archive`, `handovers`, `03_logs\_history`) are never touched. The rewrite skips a
file that is not valid UTF-8 (a Windows-1252 `.bat`) and lists it in its output and report.

**Output:** the approved map, the dry-run plans, the applied plans with per-item result, the
rollback note in `_log\`, the rewrite report, the post-move inventory.

## Mode 3: relocate (same scripts)

Same layout, new root. Restructure with an identity map: one entry, old root to new root, type
`holding`. Steps 2 to 4 of Mode 2 apply unchanged.

## Mode 4: rename (script: `Invoke-SiteofficeRename.ps1`)

A unit changes name. Update its `siteoffice.json` and the parent's children list, the one-pager
in the parent's `01_projects\`, the folder name, and every pointer to the old name in the tree's
markdown. Dry run, then apply. Tool state keyed by path follows Mode 2 for that one folder.
A unit with no tracked files (gitignored or untracked) moves as a plain folder, because
`git mv` fails on it. `-Apply` refuses while the shell's working directory is inside the unit,
and runs a temp copy of the rewrite script, so the skill may itself live inside the unit.

```powershell
# runs in: PowerShell 7 on the laptop. cwd: C:\ (outside the unit, and outside this skill if it lives in the unit). Dry run, then -Apply.
cd C:\
& '<skill folder>\scripts\Invoke-SiteofficeRename.ps1' -Root 'C:\estate\50_projects\old-name' -NewName 'new-name' -OutDir 'C:\estate\04_engineering\migration'
& '<skill folder>\scripts\Invoke-SiteofficeRename.ps1' -Root 'C:\estate\50_projects\old-name' -NewName 'new-name' -OutDir 'C:\estate\04_engineering\migration' -Apply
```

## Mode 5: adopt (script: `Invoke-SiteofficeAdopt.ps1`)

An existing folder becomes a Siteoffice unit in place. Write `siteoffice.json` (type, name,
parent, children, language, approver, `"setup": "structure-only"`), add the missing skeleton
files without touching existing ones, register the unit in the parent's manifest and
`01_projects\`. Hand over to `siteoffice-setup` Improve mode for the content.

```powershell
# runs in: PowerShell on the laptop. cwd: this skill's scripts folder. Dry run, then -Apply.
cd <skill folder>\scripts
.\Invoke-SiteofficeAdopt.ps1 -Root 'C:\estate\50_projects\loose-folder' -Type project -OutDir 'C:\estate\04_engineering\migration'
.\Invoke-SiteofficeAdopt.ps1 -Root 'C:\estate\50_projects\loose-folder' -Type project -OutDir 'C:\estate\04_engineering\migration' -Apply
```

## Mode 6: rename-to-rule (script: `Invoke-SiteofficeRenameToRule.ps1`)

Brings a tree in line with the folder-naming rule: a folder at a root is numbered (a person
edits it) or starts with an underscore (a tool writes it); a dotfolder is tool config; nothing
else. Refuse to run while a `.worktrees\` folder anywhere under the root has an active worktree,
or while any `_board\board.json` under the root is younger than 10 minutes (a render is in
flight); say why and stop.

1. Inventory first: every hard-coded occurrence of the old folder paths (a bare `projects`
   folder, a root-level `docs`, a unit-level `docs`, `graphify-out`) in `.md`, `.json`, `.ps1`,
   `.py`, `.html`, `.toml`, `.yml`, `.tsv`, `.bat` and `.cmd` files under the root. Dotfolders
   are excluded except `.claude` and `.mcp.json`. Two kinds of hit:
   - Absolute, in every spelling of the full path (`C:\estate\projects\x`).
   - Relative (`projects\x`, `..\..\projects\x`, `docs/standards/some-file.md`, `siteoffice\docs\x`
     where the renamed folder is a later segment, and the forward-slash keys of a `.tsv` ledger),
     written to `<date>_rename_to_rule_inventory_relative.csv`.
     A relative form counts only if, next to the file or one of its ancestors up to the root, a
     folder on its path is in the map and the next segment exists on disk. That keeps prose such as
     "and/or projects/programs" out. A script's relative path is relative to its working directory,
     which the scan cannot know, so read that CSV before apply.
2. Dry run: list the rename map before anything moves. The default map applies at the tree root
   itself (`root`) and at every folder anywhere under it holding its own `siteoffice.json`
   (`unit`), a table the user can edit before apply:

   | Old | New | Kind |
   |---|---|---|
   | `projects` | `50_projects` | root or unit |
   | `docs` (tree root) | `90_docs` | root |
   | `docs` (a unit) | `05_docs` | unit |
   | `graphify-out` | `_graphify` | tool (checked 2026-09-11: `graphify update`, the command sessions run, has no output-directory flag; only `merge-graphs` has `--out`. So this row is Kind `tool`, reported only, never renamed, matching the standing exception for generated output that most trees keep. `-GraphifyHasOutputOption` flips it back to a real rename candidate if a future graphify version adds the flag.) |

3. Before apply, print the stop list: paths outside the tree that point in and this mode cannot
   fix, written generically because they are ZND-specific in practice: Cowork project
   instructions and mounted folders, `.mcp.json` server roots, scheduled tasks, skill routing
   files, the Claude memory directory. It warns about each one it finds, it does not touch them.
4. Apply, after a yes. Before the first move the script refuses if the shell's working directory
   is inside a folder it would rename (Windows answers "Permission denied" halfway otherwise),
   and copies the rewrite script to a temp folder, so the skill may live inside the tree. Then:
   - Deepest folders first, so a child renamed inside a renamed parent moves before the parent.
   - `git mv` for a folder with tracked files. A folder with none (gitignored or untracked) moves
     as a plain folder, logged in the moves log; a warning follows when the old name was ignored
     and the new one is not, because the `.gitignore` line still names the old folder.
   - The relative forms, then the absolute rewrite over the files the inventory found. Nested
     renames compose: a path under `projects\x\docs` is written as `50_projects\x\05_docs`, in
     text and for the files inside it.
   - Files that are not valid UTF-8 (a Windows-1252 `.bat`) are never rewritten; they are listed
     and stay in the verification as named exceptions to fix by hand.
5. Verification pass: re-run the inventory and expect zero hits for the old paths. Any remaining
   row is a named exception in the findings file, never silently accepted.

**Output:** the inventory CSV, the approved rename map, the stop list, the applied moves and
rewrite, the verification inventory showing zero old paths.

```powershell
# runs in: PowerShell 7 on the laptop. cwd: C:\ (outside the tree, so a skill folder inside it can move). Dry run, then -Apply.
cd C:\
& '<skill folder>\scripts\Invoke-SiteofficeRenameToRule.ps1' -Root 'C:\estate' -OutDir 'C:\estate\04_engineering\migration'
& '<skill folder>\scripts\Invoke-SiteofficeRenameToRule.ps1' -Root 'C:\estate' -OutDir 'C:\estate\04_engineering\migration' -Apply
```

The rename map's `Kind` column (`root`, `unit` or `tool`) marks where a row applies: `root` at
the tree root itself, `unit` at any folder holding its own `siteoffice.json`, `tool` for a name a
tool imposes (never renamed, reported only; the graphify caveat can turn `graphify-out` into one).

## Scripts

| Script | Mode | Runs as | Changes |
|---|---|---|---|
| `Invoke-SiteofficeAudit.ps1` | audit | PS 5.1 | nothing, one CSV |
| `Invoke-SiteofficeInventory.ps1` | inventory | PS 5.1 | nothing, one CSV |
| `Invoke-SiteofficeRestructure.ps1` | restructure, relocate | PS 5.1 | copies, skeletons, manifests (`-Apply`) |
| `Invoke-SiteofficeRewrite.ps1` | after restructure | PS 5.1 | paths, relative forms and ledger keys inside copied files (`-Apply`) |
| `SiteofficeRelativeForms.ps1` | helper | dot-sourced | nothing; the relative-form finder and rewriter that Mode 2 and Mode 6 share |
| `Invoke-SiteofficeEntryFixup.ps1` | repair after a v1 rewrite | PS 5.1 | `<newRoot>\<oldName>` paths (`-Apply`) |
| `Invoke-SiteofficeTaskReregister.ps1` | after rewrite | PS 5.1, elevated | Task Scheduler actions (`-Apply`) |
| `Invoke-SiteofficeClaudeStateCopy.ps1` | after rewrite | pwsh 7, Claude Code closed | `~\.claude.json`, `~\.claude\projects` (`-Apply`, copies only) |
| `Invoke-SiteofficeToolStateRewrite.ps1` | after rewrite | PS 5.1, Claude Desktop quit | `claude_desktop_config.json`, `spaces.json`, `.lnk` (`-Apply`) |
| `Invoke-SiteofficeRename.ps1` | rename | pwsh 7 | manifest, parent children[], one-pager, folder, markdown pointers (`-Apply`) |
| `Invoke-SiteofficeAdopt.ps1` | adopt | pwsh 7 | manifest, missing skeleton files, parent children[], one-pager stub (`-Apply`) |
| `Invoke-SiteofficeRenameToRule.ps1` | rename-to-rule | pwsh 7 | folder renames, path rewrite (`-Apply`) |

`SiteofficeRelativeForms.ps1` must sit next to `Invoke-SiteofficeRenameToRule.ps1` and
`Invoke-SiteofficeRewrite.ps1`. Mode 6 loads it before the first move, so it survives the skill
folder being renamed; the staged temp copy of the rewrite script runs with `-NoRelativeForms`
(Mode 6 and Mode 4 pass it) and never needs it.

Every script is dry run without `-Apply`, writes a `.bak` next to anything it changes, and is
pure ASCII.

## Rules

- Read-only until the user says apply; every apply follows a dry run the user has seen.
- Anything with a precondition (app closed, elevated, file exists) ships as a `.ps1` run as a
  unit, never as a pasted block: an interactive console runs pasted lines one by one and
  continues after a failed `throw` or `return`.
- "Close Claude" is two different things. `Get-Process -Name Claude` matches Claude Desktop
  (`Claude.exe`) and Claude Code alike; check `Path` before telling the operator what to quit.
  Claude Desktop rewrites its stores on exit, Claude Code rewrites `~\.claude.json` on exit.
- Copy tool state, never move it; the old entries are the rollback.
- A scheduled task is not migrated until it reports exit code 0 and its output exists on disk
  with a fresh timestamp. Dirty regenerated files under the new root are the cheapest proof.
- A path rewrite does not catch code that derives a path from the tree's shape. Read every
  `layout` row and fix the script; a wrong one recreates the old folder silently.
- Cowork sandbox git against a mounted repo can leave a stale `index.lock`; hand every git
  command to the operator as a snippet.
- Never run a rename with the shell's working directory inside the folder being renamed, and
  never rely on `$PSScriptRoot` after a move: the script may sit inside what moved. The rename
  scripts check the first and stage a temp copy of the rewrite script for the second.
- Never delete the old root inside this skill. Rollback window first; archiving is the user's step.
- A scheduled task is not migrated until it reports exit code 0 and its output exists on disk.
- Refuse a new root inside a cloud-synced folder (OneDrive, Dropbox, iCloud); two sync engines
  on one folder corrupt each other.
- Scripts stay pure ASCII. A non-ASCII character in a `.ps1` is a script that does not run.
- One findings file per run, date-prefixed, in a fitting subfolder, never in the unit root.
- Answer in the conversation's language. No em dashes in anything on disk.
