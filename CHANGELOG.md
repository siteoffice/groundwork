# Changelog

All notable changes. The version is one clock for the whole repository: `groundwork`,
`groundwork-mods` and the marketplace move together.

## 0.1.6

- **The Doctor names the real cause of the skipped session-start row.** Claude Code skips the
  `classic.SessionStart` hooks of installed plugins where an organisation manages it; only managed
  settings can change that. The row says so, says what is lost (the reset after `/clear` and
  `/resume`) and what is not (the agent policy), and no longer sends you looking for a plugin to
  remove. The README known limit says the same.

## 0.1.5

- **`siteoffice-git` now works from a fresh install.** Its engine (`git_flow.py`, `git_ops.py`,
  `owner_check.py`, standard library only) was missing from the plugin, so the skill stopped at
  "Engine not found". The engine ships in `plugins\groundwork\engine\sync`, the launcher finds it
  last of its four places, and the Setup Doctor counts it as found.
- **Install scope documented.** A project-scope install loads only in that folder; the guide and
  README say to choose user scope.
- **README known limit:** `siteoffice-git` pushes, and the agent policy blocks pushes.
- 25 mod tests pass. The vendored engine suite passes 448 tests in about 25 minutes on Windows; five
  more check the Siteoffice repository's own layout and are skipped here (`engine\conftest.py`).

## 0.1.4

- **README:** the known limits now explain the Cowork warning about an unknown `types` key in the
  `groundwork-mods` manifest. Nothing else changed.

## 0.1.3

- **`siteoffice-migrate` loads with its description.** The description held `Modes: audit ...`;
  a colon followed by a space ends a plain YAML value, so the skill loaded with no description and
  never triggered by name or phrase. It now reads `Modes are audit ...`. Found by installing into
  an empty Claude config and running `claude plugin validate` on the installed copy.
- **A test parses every skill's frontmatter** (`tests\test_skills.py`), so this cannot recur.

## 0.1.2

Patch for the agent policy: the read-only check had gaps that the 0.1.1 review found.

- **Read-only commands are stricter.** Any grouping (`( ) [ ] { }`), sub-expression or process
  substitution, and a lone `&` (which starts a second command) are refused. `git branch -a -D x`,
  `git remote -v add ...` and `python --version -c ...` got through on a prefix match. All `git
  branch` forms, `git remote -v` and the three `--version` commands now match exactly. `rg
  --hostname-bin` and `--search-zip` are blocked flags.
- **The project root is the nearest folder with a policy file.** In 0.1.1 a leftover log folder in
  a subfolder hid the project's mode.
- **README:** the line about install screenshots is gone; the repository shows features, not
  install steps. 23 mod tests pass.

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
