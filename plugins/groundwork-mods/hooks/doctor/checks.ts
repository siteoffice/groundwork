// The Setup Doctor's checks: pure, from the facts the hooks gathered, so
// the tests can check every line. Each check says what it is for in one
// sentence and, when it fails, gives one fix and where to run it.

import { syncDirNote } from '../engine'
import type { DoctorFacts } from '../types'

export type Probe = {
  id: string
  title: string
  level: 'required' | 'recommended' | 'optional'
  ok: boolean
  /** Only on a failed optional probe: amber is "installed, not running", grey is "not installed". */
  tone?: 'amber' | 'grey'
  why: string
  fix?: string
  fixWhere?: string
  /** One plain line under the title, shown whether the row is ok or not. */
  note?: string
}

export const MIN_VERSION = '2.1.287'

// BurnMon writes its snapshot every 5 seconds (snapshot schema version 1). The file counts as
// fresh while it is younger than two of those ticks.
export const BURNMON_TICK_SECONDS = 5
export const BURNMON_MAX_AGE_SECONDS = 2 * BURNMON_TICK_SECONDS
export const BURNMON_RELEASES_URL = 'https://github.com/siteoffice/burnmon/releases'

// Age of a snapshot from its written_at field alone; null when the text is not JSON or the
// field is missing or not a date.
export function snapshotAgeSeconds(raw: string, nowMs: number): number | null {
  try {
    const at = (JSON.parse(raw) as { written_at?: unknown }).written_at
    if (typeof at !== 'string') return null
    const parsed = Date.parse(at)
    if (Number.isNaN(parsed)) return null
    return Math.max(0, (nowMs - parsed) / 1000)
  } catch {
    return null
  }
}

// 2.1.303 against 2.1.287: true when `v` is at least `min`.
export function atLeast(v: string, min: string): boolean {
  const a = (v.match(/\d+/g) ?? []).map(Number)
  const b = (min.match(/\d+/g) ?? []).map(Number)
  for (let i = 0; i < 3; i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return true
}

const shell = (f: DoctorFacts) => (f.isWindows ? 'PowerShell' : 'Terminal')

export function buildReport(f: DoctorFacts): Probe[] {
  const out: Probe[] = []
  const win = f.isWindows
  out.push({
    id: 'claude',
    title: 'Claude Code ' + MIN_VERSION + ' or newer',
    level: 'required',
    ok: atLeast(f.version, MIN_VERSION),
    why: 'The Fleet, the Git lane and this doctor are mods, which need ' + MIN_VERSION + '. You have ' + (f.version || 'an unknown version') + '.',
    fix: 'claude update',
    fixWhere: shell(f),
  })
  out.push({
    id: 'plugin',
    title: 'The Groundwork plugin',
    level: 'required',
    ok: f.plugin,
    why: 'The one loop, the setup skills, siteoffice-git and the board come with it.',
    fix: '/plugin marketplace add siteoffice/groundwork\n/plugin install groundwork@groundwork',
    fixWhere: 'Inside Claude Code',
  })
  out.push({
    id: 'git',
    title: 'Git, with your name and email',
    level: 'required',
    ok: f.git.ok && !!f.git.name && !!f.git.email,
    why: f.git.ok ? 'Every save carries a name and an email; ' + (f.git.name ? 'the email' : 'the name') + ' is missing.' : 'Git keeps every version of your work.',
    fix: f.git.ok
      ? 'git config --global user.name "Your Name"\ngit config --global user.email "you@example.com"'
      : win
        ? 'winget install --id Git.Git -e'
        : 'xcode-select --install',
    fixWhere: shell(f),
  })
  out.push({
    id: 'python',
    title: 'Python 3',
    level: 'required',
    ok: !!f.python,
    why: 'siteoffice-git and the engine behind the one loop run on Python.',
    fix: win ? 'winget install --id Python.Python.3.12 -e' : 'brew install python',
    fixWhere: shell(f),
  })
  if (f.manifest.found) {
    out.push({
      id: 'engine',
      title: 'The siteoffice-git engine',
      level: 'required',
      ok: f.manifest.engineOk,
      why: 'This folder is a Siteoffice unit, so saves and pull requests go through siteoffice-git.',
      note: syncDirNote(f.manifest.syncDirReason ?? ''),
      fix: '/plugin install groundwork@groundwork',
      fixWhere: 'Inside Claude Code',
    })
  }
  out.push({
    id: 'gh',
    title: 'GitHub CLI, logged in',
    level: 'recommended',
    ok: f.gh.installed && f.gh.authed,
    why: 'Pull requests and their checks show in the Git lane through it.',
    fix: f.gh.installed ? 'gh auth login' : (win ? 'winget install --id GitHub.cli -e' : 'brew install gh') + '\ngh auth login',
    fixWhere: shell(f),
  })
  out.push({
    id: 'repo',
    title: 'This folder is a git repository',
    level: 'recommended',
    ok: f.repo.inside,
    why: 'The Git lane and threads work inside a repository.',
    fix: 'git init',
    fixWhere: shell(f) + ' in this folder',
  })
  out.push({
    id: 'manifest',
    title: 'Siteoffice structure (siteoffice.json)',
    level: 'recommended',
    ok: f.manifest.found && f.manifest.valid,
    why: f.manifest.found ? 'siteoffice.json is there but does not read as JSON.' : 'The one loop, its gates and the board read this file.',
    fix: f.manifest.found ? 'Ask Claude: "fix siteoffice.json so it parses"' : 'Ask Claude: "make this folder a Siteoffice unit"',
    fixWhere: 'Inside Claude Code',
  })
  if (f.manifest.found && f.manifest.valid) {
    out.push({
      id: 'checks',
      title: 'Your tests are declared',
      level: 'recommended',
      ok: f.manifest.checks > 0,
      why: 'settings.git.checks tells siteoffice-git and the Test Bench what to run before a change lands.',
      fix: 'Ask Claude: "add the command that runs my tests to settings.git.checks in siteoffice.json"',
      fixWhere: 'Inside Claude Code',
    })
  }
  out.push({
    id: 'sessionstart',
    title: 'Session start events reach the mods',
    level: 'recommended',
    ok: sessionStartReaches(f),
    why:
      'Claude Code started this session, but no SessionStart event reached the mods. Where an organisation manages Claude Code, these events skip the plugins you install, and only the managed settings can change that. The mods lose only the reset after /clear and /resume; the agent policy keeps working. If you only just installed Groundwork, restart Claude Code and check again.',
    fix: '/plugin',
    fixWhere: 'Inside Claude Code: the Errors tab names each plugin that was skipped. You cannot fix this by installing something; only whoever manages Claude Code can change it',
  })
  out.push({
    id: 'fullscreen',
    title: 'Fullscreen layout',
    level: 'recommended',
    ok: f.isFullscreen !== false,
    why: 'Panes such as the Fleet sit beside the conversation only in fullscreen.',
    fix: '/tui fullscreen',
    fixWhere: 'Inside Claude Code',
  })
  if (f.needsBell) {
    out.push({
      id: 'bell',
      title: 'A bell when Claude needs you',
      level: 'recommended',
      ok: !!f.notif,
      why: 'Your terminal shows no desktop notice by itself; the bell marks the tab that waits.',
      fix: win
        ? '$s = Get-Content "$env:USERPROFILE\\.claude\\settings.json" -Raw | ConvertFrom-Json\n$s | Add-Member -NotePropertyName preferredNotifChannel -NotePropertyValue \'terminal_bell\' -Force\n$s | ConvertTo-Json -Depth 30 | Set-Content "$env:USERPROFILE\\.claude\\settings.json" -Encoding utf8'
        : 'Ask Claude: "set preferredNotifChannel to terminal_bell in my user settings"',
      fixWhere: win ? 'PowerShell' : 'Inside Claude Code',
    })
  }
  out.push({
    id: 'statusline',
    title: 'A status line',
    level: 'recommended',
    ok: f.statusLine,
    why: 'Model, effort and how long the prompt cache stays warm, always in view.',
    fix: '/statusline show the folder, the model and effort, and minutes left on the prompt cache',
    fixWhere: 'Inside Claude Code',
  })
  const bm = f.burnmon
  const fresh = bm.exists && bm.ageSeconds !== null && bm.ageSeconds < BURNMON_MAX_AGE_SECONDS
  const probe: Probe = {
    id: 'burnmon',
    title: 'BurnMon (feeds the Fleet)',
    level: 'optional',
    ok: fresh,
    why: 'The Fleet reads its snapshot, written seconds ago.',
  }
  if (!fresh) {
    if (!bm.exists) {
      probe.tone = 'grey'
      probe.why = 'BurnMon is not installed. The Fleet and this line need it; the rest of Groundwork works without it.'
      probe.fix = BURNMON_RELEASES_URL
      probe.fixWhere = 'Your browser, open this page'
    } else {
      probe.tone = 'amber'
      probe.why =
        bm.ageSeconds === null
          ? 'BurnMon is installed but not running: its snapshot file could not be read.'
          : 'BurnMon is installed but not running. Its snapshot is older than two ticks.'
      probe.fix = 'Start BurnMon, then press the refresh button here.'
      probe.fixWhere = 'Your computer'
    }
  }
  out.push(probe)
  return out
}

// The skipped-hook state: the session did start (session.start reached the mods) but the
// classic SessionStart event never did. Only that combination is a problem; a session where
// neither arrived, for example one the mods were loaded into mid-way, says nothing.
export function sessionStartReaches(f: DoctorFacts): boolean {
  return !(f.hooks.sessionStart && !f.hooks.classicSessionStart)
}

export function summary(probes: Probe[]): { required: number; recommended: number } {
  return {
    required: probes.filter(p => p.level === 'required' && !p.ok).length,
    recommended: probes.filter(p => p.level === 'recommended' && !p.ok).length,
  }
}
