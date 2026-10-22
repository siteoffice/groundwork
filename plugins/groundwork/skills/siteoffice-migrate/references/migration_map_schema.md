# migration_map.json: schema and example

One file per restructure or relocate. The user approves it before the dry run. Paths are
absolute on the machine that runs the move. Nothing in this schema is specific to any company.

## Top level

```json
{
  "schemaVersion": 1,
  "oldRoot": "C:\\old-root",
  "newRoot": "C:\\siteoffice\\acme",
  "holdingIsCompany": false,
  "staysSubfolders": ["Other"],
  "rollbackDays": 14,
  "entries": []
}
```

- `oldRoot`, `newRoot`: the two roots. `newRoot` must not sit inside a cloud-synced folder.
- `holdingIsCompany`: true for a single-company estate, where the holding root doubles as the
  company node and projects sit in `<newRoot>\projects\` instead of `40_companies\<co>\projects\`.
- `staysSubfolders`: direct subfolders of `oldRoot` that do not move; their inventory rows are
  skipped in the rewrite.
- `rollbackDays`: how long the old root stays read-only after apply.
- `excludeDirs` (optional): directory NAMES never copied, at any depth (robocopy `/XD`):
  caches, `node_modules`, virtual environments, build output. `.worktrees` is always excluded.
  List the reinstall commands in the runbook.
- `holdingName` (optional): name written into the root manifest; default is the new root's leaf.
- `language` (optional): language code for skeleton files; default `en`.

## Entries

```json
{
  "old": "C:\\old-root\\hub",
  "new": "10_holding",
  "type": "holding",
  "git": "none",
  "note": "the company hub; canonical instruction file is CLAUDE.md here, keep it"
}
```

- `old`: absolute old path, or a relative path under `oldRoot`. A folder is copied with robocopy;
  a single FILE (root-level loose files) is copied with Copy-Item. Empty string means a NEW node
  that has no source: the folder is created and gets the skeleton, nothing is copied.
- `new`: path relative to `newRoot`. Empty string means the root itself.
- `type`: one of `holding`, `company`, `department`, `project`, `packs`, `board`, `log`,
  `funnel`, `archive`, `tools`, `data`, `stays`, `delete`, `generated`, `classify`. `data` is
  copied but never path-rewritten (session logs, exports, databases). `delete` and `generated`
  are report-only: the script lists them, the user deletes or regenerates. `classify` means
  undecided; apply refuses while any entry is `classify`. The rewrite step does not touch the
  FILES of `stays`, `delete`, `generated`, `data` and `archive` entries (frozen history may hold
  old paths), but paths that point INTO `data` and `archive` folders are still remapped in other
  files, because those folders did move. Only `stays` and `delete` are never remapped.
- `git`: `none` (plain folder copy), `clone` (a git repository: re-clone at the new path from
  its remote, then copy untracked files the user names), `copy` (a git repository copied as a
  folder, working tree and `.git` included; simplest, keeps local branches, keeps stale absolute
  paths in `.git\config` worktrees and hooks, which the rewrite plan must cover).
- `note`: free text, shown in the dry run.

## Skeleton files added per type

`holding`, `company`, `department`, `project`: the unit skeleton from `siteoffice-setup` (only
files that do not exist yet). Containers also get `01_projects\` and `03_logs\hub_inbox.md`.
`packs`, `board`, `log`, `funnel`: the folder plus a one-line `README.md` saying what lands
there. `archive`, `tools`: folder only. `stays`: nothing; the entry documents the decision.

## Example: a grown work folder becomes one holding

```json
{
  "schemaVersion": 1,
  "oldRoot": "C:\\old-root",
  "newRoot": "C:\\siteoffice\\acme",
  "holdingIsCompany": true,
  "staysSubfolders": ["Other"],
  "rollbackDays": 14,
  "entries": [
    { "old": "hub",            "new": "10_holding",              "type": "holding",  "git": "clone", "note": "company hub" },
    { "old": "website",        "new": "projects\\website",       "type": "project",  "git": "clone", "note": "" },
    { "old": "shop-app",       "new": "projects\\shop-app",      "type": "project",  "git": "clone", "note": "" },
    { "old": "hub\\packs",     "new": "_packs",                  "type": "packs",    "git": "none",  "note": "expertise packs move up to the holding" },
    { "old": "_archive",       "new": "_archive",                "type": "archive",  "git": "none",  "note": "frozen history" },
    { "old": "_tools",         "new": "_tools",                  "type": "tools",    "git": "none",  "note": "" },
    { "old": "Other",          "new": "",                        "type": "stays",    "git": "none",  "note": "separate estate, keeps C:\\old-root\\Other" },
    { "old": "misc-exports",   "new": "",                        "type": "classify", "git": "none",  "note": "ask: archive or delete" }
  ]
}
```
