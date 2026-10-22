# Changelog

All notable changes. The version is one clock for the whole repository: `groundwork`,
`groundwork-mods` and the marketplace move together.

## 0.1.1

Patch for the agent policy, found by using it on this repository.

- **Safer read-only list.** `git branch <name>`, `rg --pre`, `git diff --output` and
  `git ls-remote --upload-pack` could write a file, create a branch or run a program in read-only
  mode. They are blocked now, as are `sort`, `uniq`, `tree`, `date` and `hostname`, which can
  write or set things. A script block (`{`), a sub-expression (`$(`, `@(`) or an output or run flag
  in a read-only command is refused.
- **More harmless reads.** `cd`, `wc`, `cut`, `diff`, `stat`, `du`, `df`, `basename`, `dirname`,
  `realpath`, `Get-Item`, `Measure-Object` and the read-only `git` forms (`rev-list`, `describe`,
  `blame`, `shortlog`, `stash list`, `config --get`, the list forms of `branch`) are on the list.
- **The project root is found upward.** A `cd` into a subfolder no longer drops the project's
  mode. The log and undo copies stay in the project's `.groundwork\` folder.
- **`.groundwork/` is ignored** in this repository and in both templates. 22 mod tests pass.

## 0.1.0

First public version. Windows tested; macOS untested.

- **`groundwork` plugin.** The `one-loop` skill takes one idea from plan to close with four gates.
  `gw.py` is its command line over the vendored Foreman engine (987 tests pass with it). A local
  receipt, a readable page and a JSON file, is written when you ship at G4, labelled
  self-issued, not signed.
- **Skills.** `siteoffice-setup`, `siteoffice-migrate`, `siteoffice-cleanup`,
  `siteoffice-checkup`, `siteoffice-git` and `handover` moved here from the ZeroNonsense.dev
  skills marketplace.
- **Board.** `render_board.py` writes one static HTML file from a folder tree. It only reads.
  185 tests pass.
- **`groundwork-mods` plugin.** The Setup Doctor (`/setup-doctor`) checks the computer and names
  the skipped session-start state. The agent policy is read-only by default, allows drafts, blocks
  sends, needs a named person to delete, and logs every write with a receipt and an undo note.
  18 tests pass.
- **Templates.** An empty unit skeleton and Molenwerk, a finished example.
- **Docs.** Setup guide, the story page and the questionnaire.

Known limits: the agent policy cannot see a symbolic link that leads out of a folder; the engine
runs the work step in your session and ships no sprint runner; the Slack sweep is an optional
adapter that needs a token.
