---
name: siteoffice-setup
description: Set up, extend or improve a Siteoffice structure and its Claude Desktop projects, folder structure on disk included, for a holding, company, department or project, in any combination. Use when the user says "set up a project", "new project in Claude Desktop", "create a folder structure", "set up my company hub", "set up a holding", "add a department", "add a project to my hub", "improve my hub files", "fill in my structure", "nieuw project opzetten", "mappenstructuur maken", "zet een hub op", "richt een holding in", "voeg een afdeling toe", "verbeter mijn hub", or wants help deciding what the description, instructions and work folder of a Claude project should be.
---

# Siteoffice setup

A Claude Desktop project is three things: a description (so a human picks the right project from
the list), instructions (so the AI behaves the same in every chat), and a connected work folder
(so knowledge lives in files, not in chat history). This skill sets up all three, plus the folder
structure on disk, filled with the customer's real answers, never with placeholder text.

## The structure model: one unit, freely composed

There is exactly ONE folder skeleton, the unit. A holding, a company, a department and a project
all use it. Structures differ only in how units nest: a holding can contain companies,
departments and projects; a company can contain departments and projects; a department can
contain projects. Projects are always leaves. Children live INSIDE their parent folder, so one
connected folder covers the whole tree.

The unit:

```
<Node>\
  AGENTS.md            the instruction file (canonical)
  CLAUDE.md            one line: @AGENTS.md  (import, so every tool reads the same file)
  STATUS.md            what is true now, one screen
  DEADLINES.md         hard dates only, most imminent on top
  SESSION_LOG.md       one paragraph per work session, newest on top
  siteoffice.json      manifest: type, name, parent, children, language, approver, setup state
  00_context\brief.md  what this node is, who owns it, who approves
  02_roadmap\roadmap.md  priority order lives here and only here
  03_logs\decisions.md   decisions with their why, newest on top
  03_logs\_history\      rolled-up old DEADLINES and SESSION_LOG content
  04_assets\             briefs, drafts, handovers, date-prefixed YYYY-MM-DD_topic.md
  05_docs\               optional: this node's own standards and guides, create only when asked
```

Container nodes additionally get `01_projects\` (one file per child: goal, what, owner, state),
`03_logs\hub_inbox.md`, and `50_projects\<slug>\` for every child of type project, whether the
container is the estate root or a company. The estate root, the one node with no parent, may
also carry `90_docs\` optional: estate-wide standards and guides, create only when asked.

A folder at a root is numbered (a person edits it) or starts with an underscore (a tool writes
it); a dotfolder is tool config; nothing else belongs at a root. Refuse to create a folder there
that fits none of these, and propose the matching numbered name instead.

## Schema v2: the four fields siteoffice.json gains

A Create or Add run writes `siteoffice.json` at `schemaVersion: 2` with four extra blocks, on top
of the existing `type`, `name`, `parent`, `children`, `language`, `approver`, `setup` fields:

```
"settings": {
  "planningHorizonWeeks": 2,   allowed 1 to 4, default 2
  "languages": ["en"],         first entry is the node's rendered language
  "timeRegistration": false
},
"labels": {},
"display_name": "<the node's name>",
"profiles": {}
```

An existing v1 manifest (no `schemaVersion` key, or `schemaVersion: 1`) is read with these same
defaults by the board renderer and is never rewritten to v2 unless the user explicitly says so.

Roadmap convention (architecture note 2026-09-09, section 10): `02_roadmap\roadmap.md` carries a
numbered priority list first, and an optional `## Backlog` heading (any suffix accepted, for
example "Backlog (v1.x)") last. Nothing else in the file is read by the board.

A `children` entry in `siteoffice.json` keeps its `type` and `path` fields; a project child's
`path` now reads `50_projects\<slug>`. An existing manifest whose child path reads
`projects\<slug>` is read as it is and never rewritten silently, the same rule the schema v2
fields already follow.

## Step 0: pick the mode

Your first action is to look for a siteoffice.json in or above the folder the user names, to see
whether a tree already exists there. Then ask which of three jobs this is (one question, multiple
choice):

- **Create**: nothing exists yet. Full flow, steps 1 to 5. If a siteoffice.json turns up where
  Create was chosen, stop and offer Extend or Improve instead of building a second tree on top of
  the first.
- **Extend**: a tree exists and gets a new node (a department in a company, a project in a hub).
  Run steps 1 to 5 for the new node only, update the parent's siteoffice.json children list, and
  add the child's one-pager to the parent's 01_projects. Never touch sibling nodes.
- **Improve**: the tree exists but files are thin, stale or empty. Read the node's files first,
  list concretely what is missing (empty sections, a STATUS that contradicts a newer decision,
  deadlines without dates, decisions without a why), then ask the content questions from Step 3
  only for the gaps. Propose each edit and apply it after a yes. Never overwrite silently, never
  invent facts the user did not give. When the skill meets a root with a bare `projects\` or
  `docs\` folder, it says so once and points to `siteoffice-migrate rename-to-rule`; it does not
  move anything itself.

Every run may stop after any node. **Output:** the chosen mode, plus each node's siteoffice.json
recording `"setup": "structure-only"` or `"setup": "filled"`, so the next run sees where it
stopped and offers to continue there.

## Step 1: structure questions, one at a time

1. What are you setting up: holding, company, department, project, or a whole tree at once? If a
   tree, have the user sketch it and read it back.
2. What is each node called? Short names, no spaces preferred.
3. Where on disk? If a structure already exists there, new nodes go inside it.
4. Who approves decisions, per node or one name for all?
5. Language of the files? Default is the conversation's language; confirm.
6. Does this node need time registration (WBSO hours)? Sets `settings.timeRegistration` in the
   schema v2 block.
7. New or existing? Existing files are never overwritten.

**Output:** the node list, its disk location, approver(s), language and new-vs-existing status,
one answer at a time, ready to feed Step 2.

## Step 2: create the skeleton

Create the folders and files for every requested node. `siteoffice.json` is written at
`schemaVersion: 2` per the fields above; an existing manifest is never rewritten to v2 here.

**Output:** the skeleton on disk, plus the tree shown to the user for a check before anything
gets filled in.

## Step 3: the content interview, per node

Ask, one question at a time, and write the answers into the files as you go. The customer's
words, tightened, not invented content:

1. **What does this node do, for whom?** One or two sentences. Goes into 00_context\brief.md and
   the Claude Desktop description.
2. **What is important here, what must never go wrong?** Goes into brief.md and the instructions
   (the refusals).
3. **Top priorities right now, in order?** Goes into 02_roadmap\roadmap.md as a numbered list,
   per the roadmap convention above.
4. **Hard dates in the next months?** Goes into DEADLINES.md, most imminent on top. None is a
   fine answer; write "none known" with the date asked.
5. **What is running today?** Projects or initiatives with owner and state: one file each in the
   parent's 01_projects, plus a STATUS.md snapshot. If `timeRegistration` is true for this node,
   add where hours land: `03_logs\time\YYYY-MM.ndjson`, written by `/log-time-from-sessions` or a
   manual `log time` chat command.
6. **What stays human, always?** Beyond the standing set (money, customer sends, publishing):
   anything extra. Goes into AGENTS.md.

Skipping is allowed: write the section header with "to be filled" plus the date, mark the node
"structure-only", and say what was skipped.

**Output:** brief.md, roadmap.md, DEADLINES.md, the relevant 01_projects file, STATUS.md and the
AGENTS.md refusals, each holding the customer's own words or an explicit "to be filled."

## Step 4: produce the paste-ready texts

One Claude Desktop project per node the user will actually work in. For each, in copyable blocks:
the project name; the one-sentence description (from question 1); the instructions containing the
role in one line, the source-of-truth table, where new files go, what stays human (the approver
by name plus the refusals from question 6), the language, and "read AGENTS.md before substantive
work."

The same folder works unchanged in OpenAI Codex and Claude Code: AGENTS.md is the open-standard
file those tools read natively, and CLAUDE.md imports it. Say that once when handing over.

**Output:** one copyable block per node, containing name, description and instructions, ready to
paste into Claude Desktop.

## Step 5: tell the user what to click

**Output:** a numbered, short list: create the project in Claude Desktop, paste name, description
and instructions, connect the node's folder, start the first chat with "read the instructions
file and confirm the setup." If the node has a rendered board, add: open `_board\board.html` in
this node's folder to see the dashboard.

## Rules

- One source of truth per fact; live files state what is true NOW, superseded content moves to
  03_logs\_history; every durable file is date-prefixed and lands in a fitting subfolder, never
  in the node root.
- Ask before creating; one question at a time; never overwrite existing files without showing the
  change and getting a yes.
- Write only what the user said, tightened. No invented examples, no lorem.
- Answer in the conversation's language. No em dashes in anything on disk.
- The structure is plain folders and markdown on purpose, readable by any AI vendor and any
  human. Say that once when handing over.
