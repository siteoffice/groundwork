# <Name>: instructions

This folder is the record of <Name>. One source of truth per fact: every other file links to
these, and a second copy anywhere else is stale and gets deleted.

| Question | File |
|---|---|
| What is true now | STATUS.md |
| Hard dates | DEADLINES.md |
| Priority order | 02_roadmap/roadmap.md |
| Why a decision was made | 03_logs/decisions.md |
| What a word means here | GLOSSARY.md |
| What this is, who owns it, who approves | 00_context/brief.md |

## Rules

- Live files state what is true now. Replaced content moves to `03_logs/_history/`.
- A durable file gets a date prefix (`YYYY-MM-DD_topic.md`) and goes in a fitting subfolder,
  never in this folder's root.
- A decision without its reason written down will be taken again. Record both.
- Read the project's `GLOSSARY.md` before using a term. Add a term when a decision settles it.

## What stays human

Money, anything sent to a customer, anything published, and any delete. The approver is named in
`00_context/brief.md`. An agent drafts; the approver decides.

## Before substantive work

Read this file, STATUS.md and the roadmap. Say which file you took each fact from. If two files
disagree, say so instead of picking one.
