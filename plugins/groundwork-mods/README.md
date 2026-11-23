# groundwork-mods (plugin)

`groundwork-mods` adds the Setup Doctor and the agent policy, plus the Fleet and the Git lane
behind two switches that are off by default. These parts load when a Claude Code session starts.

## 1. What it is

This plugin checks that Groundwork works on your computer and keeps an agent read-only until you
say otherwise. The Fleet shows what waits on you, and the Git lane draws your change from changes
to merged.

## 2. Where it fits

| Part | What it does | Default |
|---|---|---|
| Setup Doctor (`/setup-doctor`) | Checks the setup, with a copyable fix for each gap | on |
| Agent policy | Read-only agent, drafts allowed, sends blocked, writes logged. See [policy\README.md](policy/README.md) | on |
| Fleet (`/fleet`) | What waits on you in every session | off |
| Git lane (`/git`) | Your change as one lane, the next step as one button | off |

It needs `groundwork` for the engine and the `siteoffice-git` launcher. BurnMon is optional: only
the Doctor's BurnMon line and the Fleet's BurnMon line use it, and the rest works without it. See
the [repo README](../../README.md#what-needs-burnmon).

## 3. Install

```text
# runs in: inside Claude Code, any session.
/plugin marketplace add siteoffice/groundwork
/plugin install groundwork@groundwork
/plugin install groundwork-mods@groundwork
```

Choose user scope. Start a new session afterwards. Build and test from source:

```powershell
# runs in: PowerShell 7 on Windows. cwd: the repo root of your checkout.
cd .\plugins\groundwork-mods; claude plugin test .
```

## 4. Use

Run `/setup-doctor` first. Then, with the switches on:

- **The Fleet** (`/fleet`) shows what waits on you in every session, this session's agents,
  threads and background shells, your other sessions and a usage band. Other commands: `/thread`
  and `/threads` (a task in its own git worktree), `/burnmon` (the usage band on or off),
  `/blackbox` (a log of what Claude did, written as NDJSON; it is not tamper-proof: the file is
  rewritten whole and not protected against edits, and Claude can edit it in work mode), `/plain`
  and `/tower` (the old name of `/fleet`).
- **The Git lane** (`/git`) draws your change as one lane from changes to merged. The next step is
  one button, with the exact commands shown first. It also has a guard on Claude's risky git, a
  Test Bench and a PR Desk. In a SiteOffice repo the writes go through the `siteoffice-git` engine.
  The lane makes no model calls of its own; its "Ask Claude" buttons send a prompt in your session,
  which is a normal turn on your quota.

Do not switch on the Fleet or the Git lane in a session that also loads another plugin that
registers `/fleet`, `/git` or a git guard. Both register their commands on every surface (terminal,
Desktop, mobile and VS Code).

## 5. Settings

| Key | Default | Where |
|---|---|---|
| `fleet` | `false` | `/config` row "Fleet", or `pluginConfigs` in `settings.json` |
| `gitLane` | `false` | `/config` row "Git lane", or `pluginConfigs` in `settings.json` |
| `mode` | `read-only` | `.groundwork\agent-policy.json` in your project |

```json
{ "pluginConfigs": { "groundwork-mods": { "options": { "fleet": true, "gitLane": true } } } }
```

Plain Mode (`/plain`) is off until you switch it on. Then it makes small model calls on your own
quota: about 1,100 tokens for each "So what" and about 400 for each question set, capped at 50,000
tokens a day. Each call adds one line to `~/.burnmon/overhead/<day>.ndjson`.

## 6. Limits and known issues

- **The Fleet runs git only in a thread's own worktree, and hardened.** Every git call runs with
  `core.fsmonitor` off and no hooks path, `git diff` runs with no external diff, no textconv and
  submodules ignored (a submodule's own config can define a filter), and `gh` gets the same
  settings through environment variables (git 2.31 or newer; unverified: that gh passes them on to
  the git it starts). It follows a thread folder only under `.claude\worktrees`, only for thread
  agents, and only after a tool call that succeeded. It refuses to show a diff for a folder whose
  repo-local, worktree or command git config defines programs to run. A clean verdict is checked
  again on every refresh; an untrusted verdict stays for the session, so after you move a setting
  to your global git config you restart the session to open the lane again. Checked live on
  2026-11-04: a new session clears the verdict. The check reads the repo's local, worktree and
  command settings for a list of keys that name a program to run (filters, `core.sshCommand`,
  pager and editor, diff commands, credential helpers, hooks, `gpg`, `alias` and `url` sections,
  includes, and more). It is a best-effort warning from a list of known settings, not a proof that
  git runs nothing else from a repo's config. Do not open a repo you do not trust in a session. A
  repo with submodules, or with a setting from that list (for example `git lfs install --local`),
  shows as closed; move the setting to your global git config. A default branch whose name starts
  with `-` is passed to `git switch` as an option (existing behaviour, not changed in G4).
- **A repo can hide settings from the Fleet's trust check.** A repo's own `.claude\settings.json`
  `env` block can set `GIT_CONFIG_GLOBAL` or `GIT_CONFIG_PARAMETERS`. Settings that git reads this
  way are not seen by the trust check, so only open the Fleet in repos you trust. The hardened git
  calls still override `core.fsmonitor` and `core.hooksPath` themselves.
- **The Git lane runs git hardened, and only in a folder it trusts.** Every git call it makes runs
  with core.fsmonitor off and no hooks path, git diff runs with no external diff, no textconv and
  submodules ignored, git status runs with submodules ignored, and gh gets the same settings
  through environment variables. That also means your own pre-commit and pre-push hooks do not run
  on the lane's own git calls, in a repo without the siteoffice-git engine. In a SiteOffice repo
  the lane's saves run through the engine, whose git calls are not hardened here (nothing in
  `plugins\groundwork\engine\sync\git_flow.py` overrides hooks), so the repo's own hooks run there.
  Before it runs git, the lane runs the Fleet's trust check on the session folder. In a folder it
  calls untrusted the lane only shows one line with the reason (the first setting that closed it,
  in parentheses): it saves, pushes and calls the engine for nothing, and runs no git beyond the
  read-only settings check itself (`git config`, `git rev-parse`) and no gh. The Git guard keeps
  full strength there: it still holds a push to the default branch (main, master and the name in
  `.git\refs\remotes\origin\HEAD`, read as a file and never with git, all count when the lane
  cannot ask; in a linked worktree it follows the `.git` file to the main repository's copy, and a
  repository with no such file, such as a reftable clone, protects main and master only; before
  the lane's first refresh the guard also counts main and master) and a write outside the folder.
  The trust check is a warning from a list of known settings, not a guarantee, and a repo's config
  can make git run a program in ways it does not name, so open the lane only in repos you trust. A
  clean verdict is checked again on every refresh; an untrusted verdict stays for the session, so
  after you move a setting to your global git config you restart the session to open the lane
  again (unverified: that a restart clears the verdict, a check in the live look). The guard shows
  the remote's default branch name in quotes, without control characters and cut at 60 characters.
  The siteoffice-git engine's own steps are not changed by this.
- **The Fleet's tick scan of `.claude\worktrees\agent-<id>` is unchanged.** It still looks in that
  folder for each known thread without waiting for a tool call.
- **The Git lane runs git when you press its button.** The agent policy blocks every agent push. A
  push or merge you start from a lane button is your own action, so the policy does not see it.
- **The agent policy is a guardrail, not a lock.** It cannot see a link that leads out of a folder,
  and in work mode it cannot judge a command it does not know.
- **Hooks can be skipped** on a Claude organisation with managed settings. The Setup Doctor tells
  you when. The policy and the Git guard then do not run.
- **Cowork shows a warning about this plugin.** It names an unknown `types` key in `plugin.json`.
  Safe to ignore: Cowork does not run mods.

Not verified: a live Claude Code session. The mods tests mock the host. The full list is in the
[repo README](../../README.md#6-limits-and-known-issues).

## 7. Next steps and backlog

In 0.5.0 the Fleet shows what waits on you from BurnMon's wait states, tested so far on a fixture
and not against a live BurnMon. See section 7 of the [repo README](../../README.md#7-next-steps-and-backlog).

## 8. Feedback, licence, contact

Open an issue on the repo. MIT, see [LICENSE](../../LICENSE). Mail info@zerononsense.dev.
