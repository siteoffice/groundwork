# Agent policy

The rules an agent follows in a folder that uses Groundwork. They sit in one plain file,
`agent-policy.json`, so another tool can follow the same rules. In Claude Code the
`groundwork-mods` plugin enforces them with hooks. Other harnesses are not covered yet.

## The rules

1. **Read-only by default.** The agent may read, list and search. It may not change files or run
   commands that change things.
2. **Drafts are allowed.** The agent may write into a drafts folder (`drafts\` by default). A
   draft is a file a person reads and sends. The same goes for an MCP tool whose name says draft.
3. **Sends are blocked.** `git push`, a pull request, an email or chat message, a post, a publish.
   The agent writes the draft; you send it.
4. **No delete without a named person.** A delete runs only when a person named in `approvers`
   has recorded approval for that exact target, and the approval has not expired.
5. **Every write is logged.** One line per write in `.groundwork\agent-log.ndjson`: when, what,
   a receipt id and an undo note. For a file the agent changed, the old copy is kept in
   `.groundwork\undo\` so the undo note is one copy command. Blocked attempts are logged too.
6. **Nobody edits the rules but you.** The agent can never write into `.groundwork\`.

## Change the mode

Set `"mode": "work"` in `.groundwork\agent-policy.json` in your project; it overrides the plugin's
file. In work mode the agent may write anywhere in the project and run other commands. Rules 3 to
6 still apply. Go back to `"read-only"` when you are done.

The project is the nearest folder, from where the agent works upward, that holds a
`.groundwork\agent-policy.json` file; with no such file, the nearest `.groundwork\` folder. A `cd`
into a subfolder does not leave the project, and a log folder left in a subfolder does not hide it. Add `.groundwork/` to the project's
`.gitignore`: the log and the undo copies live there and are yours, not part of the work. The
skeleton in `templates\skeleton\` already does.

In read-only mode a command also has to be free of script blocks (`{`), sub-expressions (`$(`,
`@(`) and flags that name an output file or a program to run (`--output`, `--pre`,
`--upload-pack`, `--ext-diff`, `--textconv`). Those turn a read into a write or a run.

## Record an approval to delete

A person writes this file; the agent cannot. `.groundwork\approvals.json`:

```json
[{ "person": "Sam", "target": "build\\old-output", "until": "2026-12-31T23:59:59Z" }]
```

`person` must also be listed in `approvers` in the policy. `target` is the folder or file; the
approval covers it and what is inside it.

## What this is not

A guardrail, not a security boundary. It stops an agent that is trying to do the work the normal
way. It cannot stop a person, a script outside Claude Code, a link that leads out of a folder, or
a command it does not recognise in work mode. Keep your own backups and read the log.
