# Changelog

All notable changes. The version is one clock for the whole repository: `groundwork`,
`groundwork-mods` and the marketplace move together.

## Unreleased

- **The trust check no longer closes every folder in a live Claude Code session.** Claude Code adds
  its own command-scope git settings to every git a plugin runs (an empty `core.askpass`,
  `hook.<name>.enabled=false` for each hook, `protocol.ext.allow=never`, `submodule.recurse=false`),
  and the check counted them, so the lane closed with reason `core.askpass` even in a fresh repo.
  Command scope is now judged by value: a value that switches a program off passes, any other
  value, a key that switches one on and a second, different value for the same key still close the
  folder. Local and worktree scope stay strict. The mods tests' mock host now answers the check
  with the 37 settings captured from a live session, and a real-git test reproduces the case.
- **The Git lane runs every git and gh call hardened, and only shows in a folder it does not trust.** Git runs with core.fsmonitor off and no hooks path (so your own pre-commit and pre-push hooks do not run on the lane's own git calls; in a Siteoffice repo the lane's saves run through the siteoffice-git engine, whose git calls are not hardened here, so the repo's own hooks run there), `git diff` with no external diff, no textconv and submodules ignored, `git status` with submodules ignored, and gh gets the same settings through environment variables. Before it runs git the lane runs the Fleet's trust check on the session folder. In a folder it calls untrusted the lane only shows one line with the reason: it saves, pushes and calls the engine for nothing, and runs no git beyond the read-only settings check itself (`git config`, `git rev-parse`) and no gh. The Git guard keeps full strength there: a push to the default branch (main, master and the name in `.git\refs\remotes\origin\HEAD`, read as a file and never with git, all count when the lane cannot ask; a linked worktree follows its `.git` file to the main repository, a repository with no such file, such as a reftable clone, protects main and master only; before the lane's first refresh the guard also counts main and master) and a write outside the folder are still held. The check is a warning from a list of known settings, not a guarantee, so open the lane only in repos you trust. A clean verdict is checked again on every refresh; an untrusted verdict stays for the session (restart the session after moving a setting to your global git config). A repo with submodules closes the lane on purpose.
- **The Fleet shares the lane's git safety code, so its check is a little stricter.** It sets `LC_ALL=C`, runs `git status` with submodules ignored, and now also closes a folder whose local config has a `gpg.*`, `alias.*`, `hook.*`, `url.*`, `lfs.customtransfer.*`, `lfs.extension.*` or `lfs.standalonetransferagent` setting, or a remote url with a transport helper. Its other behaviour is unchanged.
- **The siteoffice-git engine's worktree list runs hardened,** with the same git settings as the lane.
- **The guard message shows the remote's branch name safely.** It is quoted, control characters are removed, it is cut at 60 characters, and the guard matches it literally, not as a pattern.
- **The payload gate checks the files git knows.** Inside a git work tree it lists tracked files and new files that are not ignored; outside a repository it walks the folder as before.
- **The `siteoffice-git` skill names this repository's own marketplace** and no person.
- **`siteoffice-checkup` no longer warns about skills another repo authors.** Check P2 judges
  "published but no longer routed" against `publish-manifest.json`, the same list the publish
  script prunes from, so `one-loop` is no longer flagged.
- **`siteoffice-migrate` description trimmed to 888 characters.** The duplicate trigger
  "path inventory" is gone; the inventory mode is still named.
- **The Git guard can allow a folder outside the project for good.** When it holds a write outside
  the project, a second chip sits next to Allow once: "Allow always for this folder, in this
  project". The folder is stored per project root and survives sessions; writes into it or below it
  are no longer held. A chip in the pane forgets the folders for this project. Deletes, deploys,
  publishes and migrations stay allow-once only.
- **The Doctor has an optional BurnMon row.** Green when `~/.burnmon/snapshot.json` is younger
  than two ticks (10 seconds), amber "installed but not running" when it is older or cannot be
  read, grey "not installed" when it is missing. It reads only the `written_at` field, and it never
  counts toward the tally or the daily toast. The README and the setup guide say what needs BurnMon.
- **`groundwork-mods` has two switches, both off by default.** `fleet` turns on the Fleet (`/fleet`,
  `/thread`, `/threads`, `/burnmon`, `/blackbox`, `/plain`, `/tower`): what waits on you, this
  session's agents, threads and shells, other sessions, a usage band. `gitLane` turns on the Git
  lane (`/git`): your change as one lane, a next-step button, a guard on Claude's risky git, a Test
  Bench and a PR Desk, with no model calls of its own (its Ask Claude buttons send a prompt in your session). A switch that is off registers nothing. Start a new
  session after switching. Do not switch either on next to another plugin that registers `/fleet`,
  `/git` or a git guard.
- **Plain Mode inside the Fleet stays off until you switch it on.** It then makes small model calls
  on your own quota (about 1,100 tokens per "So what", about 400 per question set), at most 50,000
  tokens a day, each call logged to `~/.burnmon/overhead`.
- **The Fleet shows a BurnMon line.** It reads only `written_at` from `~/.burnmon/snapshot.json`
  and says running (younger than 10 seconds), not running (10 seconds or older, or unreadable) or
  not installed. The README says what needs BurnMon.
- **The Git lane, the Setup Doctor and the `siteoffice-git` launcher take their engine only from
  `SITEOFFICE_SYNC_DIR` or the bundled engine.** A repo can no longer name its own: `settings.git.engine`
  and `<repo>\sync\git_flow.py` are gone. `SITEOFFICE_SYNC_DIR` is ignored when it resolves inside the
  repo or any of its worktrees, compared by real path; relative, drive-relative, UNC and device
  values, and a bare root such as `/`, are ignored too (the launcher no longer crashes on a bare
  root). The Doctor says so in one line and names the reason, in the launcher's own words.
- **The Fleet runs every git call hardened.** `core.fsmonitor` is off and no hooks path is used,
  `git diff` runs without external diff or textconv and with submodules ignored, and `gh` gets the
  same settings through environment variables (git 2.31 or newer). It follows a thread folder only
  under `.claude\worktrees`, only for thread agents, only after a successful tool call. It refuses
  to show a diff for a folder whose git config defines programs to run, and checks again on every
  refresh. The check counts a hit in the local, worktree or command scope, and a check that fails
  or cannot run, as untrusted. In tests, the pattern and the classification run against real git;
  the Fleet's own code paths run against a mock host.
- **The Git lane's prompts carry only the pull request number.** The remote's default branch name
  no longer reaches a prompt.
- **The Doctor no longer names a private tool, and the payload gate skips a worktree's `.git`
  pointer file,** so it exits 0 inside a worktree (run it on `pwsh`).

## 0.2.0

- **The `groundwork` plugin now registers the `siteoffice-git` guard hook.** It runs before every
  Bash and PowerShell call and denies raw mutating git (`commit`, `push`, `merge`, `rebase` and the
  like) in an agent session; read-only git such as `git status` runs as before. The registration is
  `plugins\groundwork\hooks\hooks.json`, next to the skill, so a Groundwork install gets the guard.
- **It runs on `powershell`, else `pwsh`, and stays silent when neither exists.** No error and no
  output on a machine without PowerShell; the call is allowed. The entry sets `"shell": "bash"`
  explicitly, because the command line is POSIX.
- `tests\test_guard_hook.py` covers the deny, the allow, both fallbacks and the single
  registration.

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
