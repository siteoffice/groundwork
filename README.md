# Groundwork

Groundwork is a folder structure, one loop and a board for working with AI agents. It keeps the
plans, the decisions and the record in plain files in your own folder, so no chat window holds
the only copy.

## 1. What it is

Groundwork is for a person or a small team that works with an AI agent every day and wants to
know what was decided, by whom, and what can be undone. It is a set of plugins for Claude Code
and Claude Desktop, plus plain Markdown that other tools can read. The engine is Python and needs
nothing beyond the standard library.

You get four things: a folder skeleton for a company, a department or a project; one loop
(`one-loop`) that takes an idea through a plan, a spec, the work and a review with four gates
where you decide; a board that draws your folders as one HTML page; and an agent policy that
makes the agent read-only until you say otherwise.

### What needs BurnMon

BurnMon is a separate app that watches your agent sessions and writes a small snapshot file in
your home folder. Two things need it: the Fleet's BurnMon line and the Doctor's BurnMon line,
which both show whether BurnMon runs. Everything else in Groundwork works without it, the rest of
the Fleet and the whole Git lane included. Both lines read only the `written_at` field of
`~/.burnmon/snapshot.json`, nothing from inside your sessions. The Fleet's line says "BurnMon is
running" with the snapshot's age when the file is younger than ten seconds, "BurnMon is not
running" when it is ten seconds or older or cannot be read, and "BurnMon is not installed" with
the link when the file is missing. BurnMon is linked, never bundled: [get it from its releases
page](https://github.com/siteoffice/burnmon/releases). Run `/setup-doctor` to see whether it is
running, installed but stopped, or missing.

### The Fleet and the Git lane

`groundwork-mods` carries two more parts. Both are off by default, and a switch that is off
registers nothing: no command, no hook, no output.

- **The Fleet** (`/fleet`) shows what waits on you in every session, this session's agents,
  threads and background shells, your other sessions and a usage band. Its other commands are
  `/thread` and `/threads` (a task in its own git worktree), `/burnmon` (the usage band on or off),
  `/blackbox` (a log of what Claude did, written as NDJSON; it is not tamper-proof: the file is rewritten whole
  and not protected against edits, and Claude can edit it in work mode), `/plain` and `/tower` (the old
  name of `/fleet`).
- **The Git lane** (`/git`) draws your change as one lane from changes to merged. The next step is
  one button, with the exact commands shown first. It also has a guard on Claude's risky git, a
  Test Bench and a PR Desk. In a Siteoffice repo the writes go through the `siteoffice-git` engine.
  The lane makes no model calls of its own; its "Ask Claude" buttons send a prompt in your session,
  which is a normal turn on your quota.

To switch them on, open `/config`, find the Groundwork Mods rows "Fleet" and "Git lane", or add
this to `settings.json`, then start a new session. The commands register when a session starts, so
an open session does not pick the switch up.

```json
{ "pluginConfigs": { "groundwork-mods": { "options": { "fleet": true, "gitLane": true } } } }
```

Plain Mode (`/plain`, inside the Fleet) is off until you switch it on. Then it makes small model
calls on your own quota: about 1,100 tokens for each "So what" and about 400 for each question
set, capped at 50,000 tokens a day. Each call adds one line to `~/.burnmon/overhead/<day>.ndjson`.

Do not switch on the Fleet or the Git lane in a session that also loads another plugin that
registers `/fleet`, `/git` or a git guard. Both would register the same commands. Both
register their commands on every surface (terminal, Desktop, mobile and VS Code).

## 2. Goals and benefits

- **The record lives in your folder.** A flow is plain files: `flow.json` and a log. Look at
  `templates\skeleton\` and `templates\molenwerk\` for the layout.
- **You decide at the gates.** The engine has four: plan, spec, ship, learn. No step moves
  without your verdict. `plugins\groundwork\skills\one-loop\SKILL.md` is the whole procedure.
- **A receipt when you accept.** Shipping at the last gate writes a readable page and a JSON file
  (screenshots below). Hold and park write none; `plugins\groundwork\engine\groundwork\test_gw.py`
  shows both cases.
- **The agent starts read-only.** It may write drafts, it cannot send, and it cannot delete
  without a named person. Every write is logged with a receipt and an undo note. The rules are one
  plain file: `plugins\groundwork-mods\policy\agent-policy.json`.
- **A board that only reads.** `plugins\groundwork\board\render_board.py` draws a folder tree as
  one static HTML file.
- **A git guard in agent sessions.** `plugins\groundwork\hooks\hooks.json` denies raw mutating git
  (`commit`, `push`, `merge` and the like) from the agent and points it at the `siteoffice-git`
  steps. `git status` and other read-only git still run.

What it costs you: the gates are slower than letting an agent run. That is on purpose. If you
want a machine that ships without asking you, this is the wrong tool.

I measured what I claim here by running it. The engine carries 987 passing tests, the board 185,
the mods 206, the repository's pytest suite 231 and the `siteoffice-git` engine 448, all green on Windows at this release. Not verified: macOS and Linux, and a live Claude Code session (the mods tests mock the host). The trust
tests that drive real git through the real `gitsafe.ts` need Node 22.18 or newer and are skipped when it is missing or older. That last
suite builds real git repositories and takes about 25 minutes. Claude Code wrote most of the code
and text of this release. I set the scope and I decide what ships.

## 3. Screenshots

All four are rendered from this release. The receipt page is what you get when you ship.

![The Molenwerk example company on the board, light theme: status, next two weeks, waiting on you and roadmap panels](assets/screenshots/board-molenwerk-light.png)
![The Molenwerk example company on the board, dark theme](assets/screenshots/board-molenwerk-dark.png)
![A receipt for an accepted change, light theme: a table with project, decision, time and a hash of the flow log, labelled self-issued, not signed](assets/screenshots/receipt-light.png)
![The same receipt in the dark theme](assets/screenshots/receipt-dark.png)

## 4. Install, no terminal

1. Open Claude Desktop. Click your name (bottom left), then Settings, then Plugins, then Add
   marketplace.
2. Paste `siteoffice/groundwork`.
3. Install `groundwork`. Install `groundwork-mods` as well for the Setup Doctor and the agent
   policy.
4. You need Python 3.8 or newer on the computer. If you are not sure, the Setup Doctor tells you.

The full steps for Windows and macOS are in [docs/setup-guide.md](docs/setup-guide.md).

## 5. Install, developers

```text
# runs in: inside Claude Code, any session.
/plugin marketplace add siteoffice/groundwork
/plugin install groundwork@groundwork
/plugin install groundwork-mods@groundwork
```

Choose user scope, or add `--scope user` to `claude plugin install`. A project-scope install loads
only in that folder.

Build and test from source:

```powershell
# runs in: PowerShell on Windows. cwd: the repo root of your checkout.
pwsh -NoProfile -File .\scripts\check-public-payload.ps1
cd .\plugins\groundwork\engine; py -3 -m pytest foreman groundwork -q
cd ..\; py -3 -m pytest board -q
cd ..\groundwork-mods; claude plugin test .
```

The engine and the board are vendored from Siteoffice by `scripts\sync-engine.ps1`, never edited
by hand. The payload gate `scripts\check-public-payload.ps1` must exit 0 before any commit.

## 6. How to use: the first ten minutes

1. Open Claude Code in a project folder.
2. Type `/setup-doctor`. Fix what it lists.
3. Say: "Start a flow for adding a login page." `one-loop` creates the flow and walks you to
   the plan gate.
4. Answer each gate. At the ship gate, say "ship" if you accept the result. Your receipt lands
   in `flows\<project>\<slug>\receipts\`.
5. Want a folder structure for the whole company? Say "set up my company hub".

## 7. Roadmap

Next: a test run on a Mac, install screenshots for both systems, and a first run by someone who
did not build it, with their findings filed as issues. Not planned here: signing receipts and a
verify page (that is a separate product), a hosted version, a sprint runner, and Slack as the
default. See the [issue list](https://github.com/siteoffice/groundwork/issues).

## 8. Known bugs and limits

- **macOS is untested.** The code has no Windows-only calls and the guide has a macOS path, but
  nobody has run it on a Mac.
- **The board follows folders upward.** It looks for a funnel folder in the parent folders. Put
  your checkout somewhere with no `00_funnel` above it, or the board shows that funnel too.
- **The agent policy is a guardrail, not a lock.** It cannot see a link that leads out of a
  folder, and in work mode it cannot judge a command it does not know.
- **The work step runs in your session.** The engine ships no sprint runner.
- **`siteoffice-git` and the agent policy disagree on pushing.** The skill saves, proposes and
  lands a change with `git push` and a pull request. The policy blocks every push, in work mode
  too, so you run the push or the merge yourself. The skill's own engine ships in
  `plugins\groundwork\engine\sync`.
- **The Git lane runs git when you press its button.** The agent policy blocks every agent push.
  A push or merge you start from a lane button is your own action, so the policy does not see it.
- **In a Siteoffice repo the Git lane, the Setup Doctor and the `siteoffice-git` launcher run only
  an engine you installed.** They take `SITEOFFICE_SYNC_DIR` first, else the engine the
  `groundwork` plugin ships. A repo can no longer name its own engine: `settings.git.engine` in
  `siteoffice.json` and `<repo>\sync\git_flow.py` are gone. They ignore `SITEOFFICE_SYNC_DIR` when
  it resolves inside the repo or any of its worktrees, because a repo's `.claude\settings.json`
  `env` block can set it. Paths are compared by real path (symlinks and junctions are followed);
  relative, drive-relative, UNC and device values, and a bare root, are ignored. The Setup Doctor
  says so in one line and names the reason (inside the repo, not an absolute path, or does not
  resolve), in the launcher's own words. In the launcher on Windows PowerShell the real-path check does not cover 8.3 short names,
  hard links, mapped drives, or a link swapped in between the check and the run.
- **The Fleet runs git only in a thread's own worktree, and hardened.** Every git call runs with
  `core.fsmonitor` off and no hooks path, `git diff` runs with no external diff, no textconv and
  submodules ignored (a submodule's own config can define a filter), and `gh` gets the same settings through environment variables (git 2.31 or newer; unverified: that gh passes them on to the git it starts). It follows a
  thread folder only under `.claude\worktrees`, only for thread agents, and only after a tool call
  that succeeded. It refuses to show a diff for a folder whose repo-local, worktree or command git
  config defines programs to run. A clean verdict is checked again on every refresh; an untrusted
  verdict stays for the session, so after you move a setting to your global git config you restart
  the session to open the lane again (unverified: that a restart clears the verdict, a check in the live look). The check reads the repo's local, worktree and command settings for a list of keys that
  name a program to run (filters, `core.sshCommand`, pager and editor, diff commands, credential
  helpers, hooks, `gpg`, `alias` and `url` sections, includes, and more). It is a best-effort
  warning from a list of known settings, not a proof that git runs nothing else from a repo's
  config. Do not open a repo you do not trust in a session. A repo with submodules, or with a
  setting from that list (for example `git lfs install --local`), shows as closed; move the
  setting to your global git config. A default branch whose name starts with `-` is passed to
  `git switch` as an option (existing behaviour, not changed in G4).
- **A repo can hide settings from the Fleet's trust check.** A repo's own `.claude\settings.json`
  `env` block can set `GIT_CONFIG_GLOBAL` or `GIT_CONFIG_PARAMETERS`. Settings that git reads this
  way are not seen by the trust check, so only open the Fleet in repos you trust. The hardened git
  calls still override `core.fsmonitor` and `core.hooksPath` themselves.
- **The Git lane runs git hardened, and only in a folder it trusts.** Every git call it makes runs with core.fsmonitor off and no hooks path, git diff runs with no external diff, no textconv and submodules ignored, git status runs with submodules ignored, and gh gets the same settings through environment variables. That also means your own pre-commit and pre-push hooks do not run on the lane's own git calls, in a repo without the siteoffice-git engine. In a Siteoffice repo the lane's saves run through the engine, whose git calls are not hardened here (nothing in `plugins\groundwork\engine\sync\git_flow.py` overrides hooks), so the repo's own hooks run there. Before it runs git, the lane runs the Fleet's trust check on the session folder. In a folder it calls untrusted the lane only shows one line with the reason (the first setting that closed it, in parentheses): it saves, pushes and calls the engine for nothing, and runs no git beyond the read-only settings check itself (`git config`, `git rev-parse`) and no gh. The Git guard keeps full strength there: it still holds a push to the default branch (main, master and the name in `.git\refs\remotes\origin\HEAD`, read as a file and never with git, all count when the lane cannot ask; in a linked worktree it follows the `.git` file to the main repository's copy, and a repository with no such file, such as a reftable clone, protects main and master only; before the lane's first refresh the guard also counts main and master) and a write outside the folder. The trust check is a warning from a list of known settings, not a guarantee, and a repo's config can make git run a program in ways it does not name, so open the lane only in repos you trust. A clean verdict is checked again on every refresh; an untrusted verdict stays for the session, so after you move a setting to your global git config you restart the session to open the lane again (unverified: that a restart clears the verdict, a check in the live look). The guard shows the remote's default branch name in quotes, without control characters and cut at 60 characters. The siteoffice-git engine's own steps are not changed by this.
- **The Fleet's tick scan of `.claude\worktrees\agent-<id>` is unchanged.** It still looks in that
  folder for each known thread without waiting for a tool call.
- **Windows PowerShell 5.1 still cannot run the payload gate.** Run `scripts\check-public-payload.ps1`
  on `pwsh`. Inside a git work tree it checks the files git knows (tracked files, and new files that are not ignored), so a local ignored file does not fail it; elsewhere it checks every file in the folder. It reads the working tree copy of each file git lists, not the staged blob, so a hit staged and then removed from the working file is not seen.
- **The git guard needs Windows PowerShell or PowerShell 7, and Git Bash on Windows.** It runs
  `powershell`, else `pwsh`. With neither it does nothing and raw git is not blocked. Like the agent
  policy it is a guardrail, not a lock.
- **Where an organisation manages Claude Code, session-start hooks of plugins you install are
  skipped.** Claude Code says so under `/plugin`, Errors. The Setup Doctor names it as a
  recommended row. The mods then miss the reset after `/clear` and `/resume`; the agent policy
  hooks tool calls and keeps working. Only the organisation's managed settings can change this.
- **Cowork shows a warning about `groundwork-mods`.** It names an unknown `types` key in
  `plugin.json`. Safe to ignore: the key is the type contract Claude Code uses for the Setup
  Doctor, and Cowork does not run mods.

Report a bug with the issue template: the command, what it printed, and your system.

## 9. Licence, support and contact

MIT, see [LICENSE](LICENSE). Questions and bugs: open an issue. Anything else:
info@zerononsense.dev. Made by ZeroNonsense.dev.
