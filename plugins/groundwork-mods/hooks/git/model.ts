// Pure logic for the Git lane: parse git and gh output, place the repo on the
// lane, and pick the one next step. No engine calls, so tests check it directly.

import type { GitLaneChecks, GitLaneDeskPr, GitLaneFile, GitLanePr, GitLaneRepo } from '../types'

export type Stage = 'changes' | 'saved' | 'shared' | 'review' | 'merged'
export type StageState = 'todo' | 'active' | 'done' | 'warn' | 'bad'

export const STAGES: { id: Stage; label: string }[] = [
  { id: 'changes', label: 'Changes' },
  { id: 'saved', label: 'Saved' },
  { id: 'shared', label: 'Shared' },
  { id: 'review', label: 'Review' },
  { id: 'merged', label: 'Merged' },
]

export const EMPTY_REPO: GitLaneRepo = {
  isRepo: false,
  mode: 'git',
  root: '',
  engine: '',
  head: '',
  checked: false,
  project: '',
  branch: '',
  upstream: '',
  ahead: 0,
  behind: 0,
  unshared: 0,
  defaultBranch: 'main',
  files: [],
  commits: [],
  hasGh: false,
  pr: null,
}

export type Status = { branch: string; upstream: string; ahead: number; behind: number; files: GitLaneFile[] }

// `git status --porcelain=v2 --branch` (paths unquoted).
export function parseStatus(out: string): Status {
  const s: Status = { branch: '', upstream: '', ahead: 0, behind: 0, files: [] }
  for (const line of out.split(/\r?\n/)) {
    if (!line) continue
    if (line.startsWith('# branch.head ')) s.branch = line.slice(14).trim()
    else if (line.startsWith('# branch.upstream ')) s.upstream = line.slice(18).trim()
    else if (line.startsWith('# branch.ab ')) {
      const m = /\+(\d+) -(\d+)/.exec(line)
      if (m) {
        s.ahead = Number(m[1])
        s.behind = Number(m[2])
      }
    } else if (line.startsWith('1 ')) {
      const parts = line.split(' ')
      const xy = parts[1] ?? '..'
      s.files.push({ path: parts.slice(8).join(' '), kind: xy.includes('D') ? 'deleted' : xy.includes('A') ? 'new' : 'changed', added: 0, removed: 0 })
    } else if (line.startsWith('2 ')) {
      const parts = line.split(' ')
      const path = parts.slice(9).join(' ').split('\t')[0] ?? ''
      s.files.push({ path, kind: 'renamed', added: 0, removed: 0 })
    } else if (line.startsWith('u ')) {
      s.files.push({ path: line.split(' ').slice(10).join(' '), kind: 'conflict', added: 0, removed: 0 })
    } else if (line.startsWith('? ')) {
      s.files.push({ path: line.slice(2), kind: 'new', added: 0, removed: 0 })
    }
  }
  return s
}

// `git diff HEAD --numstat`: adds line counts to the files status found.
export function mergeNumstat(files: GitLaneFile[], out: string): GitLaneFile[] {
  const counts = new Map<string, [number, number]>()
  for (const line of out.split(/\r?\n/)) {
    const [a, r, ...rest] = line.split('\t')
    if (!rest.length) continue
    const path = rest.join('\t').replace(/^.*=> /, '').replace(/[{}]/g, '')
    counts.set(path, [Number(a) || 0, Number(r) || 0])
  }
  return files.map(f => {
    const c = counts.get(f.path)
    return c ? { ...f, added: c[0], removed: c[1] } : f
  })
}

// `git log --format=%h%x09%s%x09%cr`
export function parseLog(out: string) {
  return out
    .split(/\r?\n/)
    .filter(Boolean)
    .map(l => {
      const [hash = '', subject = '', when = ''] = l.split('\t')
      return { hash, subject, when }
    })
}

type RollupItem = { conclusion?: string | null; status?: string | null; state?: string | null; name?: string; context?: string; detailsUrl?: string; targetUrl?: string }

export function checksOf(rollup: RollupItem[] | null | undefined): GitLaneChecks {
  const c = { pass: 0, fail: 0, pending: 0 }
  for (const r of rollup ?? []) {
    const v = String(r.conclusion || r.state || r.status || '').toUpperCase()
    if (['SUCCESS', 'NEUTRAL', 'SKIPPED'].includes(v)) c.pass += 1
    else if (['FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE'].includes(v)) c.fail += 1
    else c.pending += 1
  }
  return c
}

// `gh pr view --json number,title,url,state,isDraft,reviewDecision,mergeable,statusCheckRollup`
export function parsePr(out: string): GitLanePr | null {
  try {
    const j = JSON.parse(out) as Record<string, unknown>
    if (typeof j.number !== 'number') return null
    const review = String(j.reviewDecision ?? '')
    const mergeable = String(j.mergeable ?? 'UNKNOWN')
    return {
      number: j.number,
      title: String(j.title ?? ''),
      url: String(j.url ?? ''),
      state: (['OPEN', 'MERGED', 'CLOSED'].includes(String(j.state)) ? j.state : 'OPEN') as GitLanePr['state'],
      isDraft: j.isDraft === true,
      review: (['APPROVED', 'CHANGES_REQUESTED', 'REVIEW_REQUIRED'].includes(review) ? review : '') as GitLanePr['review'],
      checks: checksOf(j.statusCheckRollup as RollupItem[]),
      mergeable: (['MERGEABLE', 'CONFLICTING'].includes(mergeable) ? mergeable : 'UNKNOWN') as GitLanePr['mergeable'],
    }
  } catch {
    return null
  }
}

// Where the repo stands on each stage of the lane.
export function laneOf(r: GitLaneRepo): Record<Stage, { state: StageState; note: string }> {
  const conflicts = r.files.filter(f => f.kind === 'conflict').length
  const pr = r.pr
  const merged = pr?.state === 'MERGED'
  const changes = conflicts
    ? { state: 'bad' as StageState, note: conflicts + ' conflict' + (conflicts > 1 ? 's' : '') }
    : r.files.length
      ? { state: 'active' as StageState, note: r.files.length + ' file' + (r.files.length > 1 ? 's' : '') }
      : { state: 'done' as StageState, note: 'clean' }
  const saved = r.unshared
    ? r.mode === 'siteoffice'
      ? { state: (r.checked ? 'done' : 'active') as StageState, note: r.checked ? 'checked' : 'needs check' }
      : { state: 'active' as StageState, note: r.unshared + ' to share' }
    : r.files.length
      ? { state: 'todo' as StageState, note: '' }
      : { state: 'done' as StageState, note: 'all saved' }
  const shared = !r.upstream
    ? { state: (r.unshared ? 'todo' : 'todo') as StageState, note: 'not online' }
    : r.unshared
      ? { state: 'todo' as StageState, note: r.unshared + ' waiting' }
      : { state: 'done' as StageState, note: r.behind ? r.behind + ' new online' : 'up to date' }
  let review: { state: StageState; note: string }
  if (!pr) review = { state: 'todo', note: r.branch === r.defaultBranch ? '' : r.hasGh ? 'not asked' : 'no gh' }
  else if (merged) review = { state: 'done', note: '#' + pr.number }
  else if (pr.checks.fail) review = { state: 'bad', note: pr.checks.fail + ' check' + (pr.checks.fail > 1 ? 's' : '') + ' failed' }
  else if (pr.review === 'CHANGES_REQUESTED') review = { state: 'bad', note: 'changes asked' }
  else if (pr.checks.pending) review = { state: 'warn', note: 'checks running' }
  else if (pr.review === 'APPROVED') review = { state: 'done', note: 'approved' }
  else review = { state: 'warn', note: pr.review === 'REVIEW_REQUIRED' ? 'waits for review' : '#' + pr.number + ' open' }
  const mergedStage = merged ? { state: 'done' as StageState, note: '#' + pr?.number } : { state: 'todo' as StageState, note: '' }
  return { changes, saved, shared, review, merged: mergedStage }
}

export type NextStep =
  | { kind: 'none'; label: string; short?: string }
  | { kind: 'commit' | 'branch' | 'push' | 'pull' | 'pr' | 'merge' | 'finish' | 'ask'; label: string; prompt?: string; short?: string }

// The one thing to do now, in plain words.
export function nextStep(r: GitLaneRepo, protectMain: boolean): NextStep {
  if (!r.isRepo) return { kind: 'none', label: 'This folder is not a git repository.' }
  const conflicts = r.files.filter(f => f.kind === 'conflict').length
  if (conflicts)
    return { kind: 'ask', label: 'Ask Claude to resolve ' + conflicts + ' conflict' + (conflicts > 1 ? 's' : ''), prompt: 'There are merge conflicts in this repository. Resolve them, explain each choice in one line, and do not commit.' }
  if (r.pr?.state === 'MERGED' && r.branch !== r.defaultBranch) return { kind: 'finish', label: 'Back to ' + r.defaultBranch + ' and get the latest' }
  if (r.behind && !r.files.length) return { kind: 'pull', label: 'Get the latest (' + r.behind + ' new online)' }
  if (r.files.length && protectMain && r.branch === r.defaultBranch) return { kind: 'branch', label: 'Start a branch for these changes' }
  if (r.files.length) return { kind: 'commit', label: 'Save ' + r.files.length + ' change' + (r.files.length > 1 ? 's' : '') }
  if (r.unshared || (!r.upstream && r.branch !== r.defaultBranch && r.commits.length))
    return { kind: 'push', label: 'Share ' + (r.unshared || 'your') + ' saved change' + (r.unshared === 1 ? '' : 's') }
  const pr = r.pr
  if (r.branch !== r.defaultBranch && r.hasGh && r.upstream && !pr) return { kind: 'pr', label: 'Ask for a review' }
  if (pr && pr.state === 'OPEN') {
    if (pr.checks.fail) return { kind: 'ask', label: 'Ask Claude to fix the failing checks', prompt: 'The checks on pull request #' + pr.number + ' fail. Find out why with gh, fix the cause, and tell me what you changed.' }
    if (pr.review === 'CHANGES_REQUESTED') return { kind: 'ask', label: 'Ask Claude to handle the review comments', prompt: 'Pull request #' + pr.number + ' has review comments asking for changes. Read them with gh and make the changes.' }
    if (pr.checks.pending) return { kind: 'none', label: 'Checks are running. Nothing to do yet.' }
    if (pr.mergeable === 'CONFLICTING') return { kind: 'ask', label: 'Ask Claude to bring the branch up to date', prompt: 'Pull request #' + pr.number + ' conflicts with ' + r.defaultBranch + '. Merge ' + r.defaultBranch + ' into this branch and resolve the conflicts. Do not push.' }
    if (pr.review !== 'REVIEW_REQUIRED') return { kind: 'merge', label: 'Merge into ' + r.defaultBranch }
    return { kind: 'none', label: 'Waiting for a review.' }
  }
  return { kind: 'none', label: 'Everything is saved and shared.' }
}

// Commands a step runs, shown to the person before they confirm.
export function commandsFor(kind: string, r: GitLaneRepo, message: string, branchName: string): string[][] {
  if (kind === 'commit') return [['git', 'add', '-A'], ['git', 'commit', '-m', message]]
  if (kind === 'branch') return [['git', 'switch', '-c', branchName]]
  if (kind === 'push') return r.upstream ? [['git', 'push']] : [['git', 'push', '-u', 'origin', r.branch]]
  if (kind === 'pull') return [['git', 'pull', '--ff-only']]
  if (kind === 'pr') return [['gh', 'pr', 'create', '--fill', '--base', r.defaultBranch]]
  if (kind === 'merge') return [['gh', 'pr', 'merge', String(r.pr?.number ?? ''), '--squash', '--delete-branch']]
  if (kind === 'finish') return [['git', 'switch', r.defaultBranch], ['git', 'pull', '--ff-only']]
  return []
}

// A warning for steps that touch the shared default branch.
export function warningFor(kind: string, r: GitLaneRepo): string {
  if (kind === 'push' && r.branch === r.defaultBranch) return 'This sends your changes straight to ' + r.defaultBranch + ', without a review.'
  if (kind === 'merge') return 'This puts the branch into ' + r.defaultBranch + ' for everyone.'
  return ''
}

// A branch name from a sentence: lowercase words joined by dashes.
export function branchNameOf(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
  return slug ? 'work/' + slug : 'work/changes'
}

// Claude's own git commands that need a person: risky enough to stop.
export function guardReason(command: string, defaultBranch: string, protectMain: boolean): string {
  const c = command.replace(/\s+/g, ' ')
  if (/\bgit push\b[^;&|]*(--force\b|--force-with-lease\b|\s-f\b)/.test(c)) return 'a force push rewrites shared history'
  if (/\bgit reset\b[^;&|]*--hard\b/.test(c)) return 'reset --hard throws away work that is not saved'
  if (/\bgit clean\b[^;&|]*\s-[a-zA-Z]*f/.test(c)) return 'clean -f deletes files git does not track'
  if (/\bgit branch\b[^;&|]*\s-D\b/.test(c)) return 'branch -D deletes a branch even when it is not merged'
  if (/\bgit checkout\b[^;&|]*\s--\s+\./.test(c)) return 'checkout -- . throws away every unsaved change'
  if (protectMain && new RegExp('\\bgit push\\b[^;&|]*\\b' + defaultBranch + '\\b').test(c)) return 'pushing to ' + defaultBranch + ' skips the review'
  return ''
}

// A bar of `cells` blocks split into added and removed parts.
export function churnBar(added: number, removed: number, max: number, cells: number): [string, string, string] {
  const total = added + removed
  if (!total || !max) return ['', '', '▯'.repeat(cells)]
  const used = Math.max(1, Math.round((total / max) * cells))
  const a = Math.round((added / total) * used)
  return ['▮'.repeat(a), '▮'.repeat(used - a), '▯'.repeat(cells - used)]
}

export function fileName(path: string): string {
  const parts = path.split(/[\\/]/)
  return parts[parts.length - 1] ?? path
}

// "66 changed · 135 new": /diff counts changed files git already tracks; new
// files are counted apart, so the two views agree.
export function countsOf(files: GitLaneFile[]): string {
  const added = files.filter(f => f.kind === 'new').length
  const changed = files.length - added
  return [changed ? changed + ' changed' : '', added ? added + ' new' : ''].filter(Boolean).join(' \u00B7 ')
}

// --- Siteoffice repos: the six steps of siteoffice-git -----------------------

const PROTECTED = ['main', 'master']

// The one thing to do now, as a siteoffice-git step. Nothing here runs raw git.
export function nextStepSo(r: GitLaneRepo): NextStep | { kind: 'start' | 'save' | 'check' | 'propose' | 'land'; label: string; short?: string } {
  if (r.mode === 'siteoffice-missing') return { kind: 'none', label: 'Engine not found: set SITEOFFICE_SYNC_DIR or settings.git.engine.', short: 'siteoffice-git engine not found' }
  const conflicts = r.files.filter(f => f.kind === 'conflict').length
  if (conflicts)
    return { kind: 'ask', label: 'Ask Claude to resolve ' + conflicts + ' conflict' + (conflicts > 1 ? 's' : ''), prompt: 'There are merge conflicts in this repository. Resolve them, explain each choice in one line, and save through the siteoffice-git skill.' }
  const onMain = r.branch === r.defaultBranch || PROTECTED.includes(r.branch.toLowerCase())
  // The engine saves only on a work branch and starts one only from a clean
  // tree, so changes already made on main are a person's commit by hand.
  if (onMain)
    return r.files.length
      ? {
          kind: 'none',
          label: r.files.length + ' unsaved change' + (r.files.length > 1 ? 's' : '') + ' on main. siteoffice-git cannot save on main: commit these by hand, then start a work branch.',
          short: r.files.length + ' unsaved on main \u00B7 commit by hand',
        }
      : { kind: 'start', label: 'Start a work branch for new work' }
  if (r.files.length) return { kind: 'save', label: 'Save ' + r.files.length + ' change' + (r.files.length > 1 ? 's' : '') }
  const pr = r.pr
  if (pr && pr.state === 'MERGED') return { kind: 'none', label: 'Merged on GitHub. Land again, or switch to main by hand.' }
  const needsShare = r.unshared > 0 || !pr
  if (needsShare && r.commits.length) return r.checked ? { kind: 'propose', label: 'Share and ask for a review' } : { kind: 'check', label: 'Run the checks' }
  if (pr && pr.state === 'OPEN') {
    if (pr.checks.fail) return { kind: 'ask', label: 'Ask Claude to fix the failing checks', prompt: 'The checks on pull request #' + pr.number + ' fail. Find out why with gh, fix the cause, then save, check and propose through the siteoffice-git skill.' }
    if (pr.review === 'CHANGES_REQUESTED') return { kind: 'ask', label: 'Ask Claude to handle the review comments', prompt: 'Pull request #' + pr.number + ' has review comments asking for changes. Read them with gh, make the changes, then save, check and propose through the siteoffice-git skill.' }
    if (pr.checks.pending) return { kind: 'none', label: 'Checks are running. Nothing to do yet.' }
    return { kind: 'land', label: 'Merge into ' + r.defaultBranch }
  }
  return { kind: 'none', label: 'Everything is saved and shared.' }
}

// The engine argv for one step; preview by default, --apply to act.
export function engineArgv(python: string, r: GitLaneRepo, step: string, args: string[], apply: boolean): string[] {
  return [python, r.engine, step, ...args, ...(apply ? ['--apply'] : []), '--repo', r.root, '--harness', 'groundwork-git-lane']
}

// A commit scope the engine accepts: the first folder every path shares,
// else the project folder, lower-cased, letters digits . _ / - only.
export function scopeOf(paths: string[], project: string): string {
  const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9._/-]+/g, '-').replace(/^[^a-z0-9]+/, '')
  const firsts = new Set(paths.map(p => (p.includes('/') ? p.split('/')[0] ?? '' : '')))
  const shared = firsts.size === 1 ? [...firsts][0] ?? '' : ''
  return clean(shared) || clean(project) || 'repo'
}

// True when the newest check receipt for this commit passed.
export function checkedFor(receipts: string, head: string): boolean {
  if (!head) return false
  let result = false
  for (const line of receipts.split(/\r?\n/)) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as { step?: string; sha_after?: string; outcome?: string }
      if (r.step === 'check' && r.sha_after === head) result = r.outcome === 'pass'
    } catch {
      // a broken line is skipped, as the engine skips it
    }
  }
  return result
}

// The outcome word the engine prints first: "[save] refused".
export function outcomeOf(stdout: string, exitCode: number): 'pass' | 'refused' | 'blocked' {
  const m = /^\[[a-z]+\] (pass|refused|blocked)/m.exec(stdout)
  if (m) return m[1] as 'pass' | 'refused' | 'blocked'
  return exitCode === 0 ? 'pass' : exitCode === 3 ? 'blocked' : 'refused'
}

// Test Bench: the checks a repo declares, or the usual ones its files show.
// A Siteoffice repo's own list (settings.git.checks) wins; else package.json
// scripts, then Python, Go and Rust by their marker files.
export type CheckSources = {
  manifest?: string
  packageJson?: string
  hasPyproject?: boolean
  hasPytestIni?: boolean
  hasGoMod?: boolean
  hasCargo?: boolean
}

export function checksFrom(src: CheckSources): { source: string; cmds: string[] } {
  try {
    const j = src.manifest ? (JSON.parse(src.manifest) as { settings?: { git?: { checks?: unknown } } }) : undefined
    const list = j?.settings?.git?.checks
    if (Array.isArray(list) && list.length) return { source: 'siteoffice.json', cmds: list.map(String).filter(Boolean) }
  } catch {
    // A broken manifest falls through to the files.
  }
  try {
    const p = src.packageJson ? (JSON.parse(src.packageJson) as { scripts?: Record<string, unknown> }) : undefined
    const scripts = p?.scripts ?? {}
    const names = ['test', 'typecheck', 'lint', 'check'].filter(n => typeof scripts[n] === 'string')
    if (names.length) return { source: 'package.json', cmds: names.map(n => (n === 'test' ? 'npm test' : 'npm run ' + n)) }
  } catch {
    // Same: an unreadable package.json is skipped.
  }
  if (src.hasPyproject || src.hasPytestIni) return { source: 'python', cmds: ['python -m pytest -q'] }
  if (src.hasGoMod) return { source: 'go.mod', cmds: ['go test ./...'] }
  if (src.hasCargo) return { source: 'Cargo.toml', cmds: ['cargo test'] }
  return { source: '', cmds: [] }
}

// Flaky: on one commit, the same check both passed and failed.
export function isFlaky(history: { ok: boolean; head: string }[]): boolean {
  const byHead = new Map<string, Set<boolean>>()
  for (const h of history) byHead.set(h.head, (byHead.get(h.head) ?? new Set()).add(h.ok))
  return [...byHead.values()].some(s => s.size === 2)
}

// The last lines of a run's output, the part that says why it failed.
export function tailOf(out: string, lines = 8): string[] {
  return out.replace(/\r/g, '').split('\n').map(l => l.replace(/\s+$/, '')).filter(Boolean).slice(-lines)
}

export function shellFor(isWindows: boolean, cmd: string): string[] {
  return isWindows ? ['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd] : ['sh', '-c', cmd]
}

// --- PR Desk: every open pull request, grouped by who it waits on ----------

const FAILED = ['FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE']
const AGENT_BRANCH = /^(worktree-|agent-|claude\/)|\/agent-[a-z0-9]/i
const AGENT_BODY = /Generated with \[Claude Code\]|Co-Authored-By: Claude/i

export const PR_FIELDS = 'number,title,author,headRefName,url,isDraft,reviewDecision,mergeable,statusCheckRollup,updatedAt,body'

// The repo's open PRs from `gh pr list --json PR_FIELDS`, the numbers that
// ask for my review, the unresolved thread count per PR (from GraphQL) and the
// branches the Fleet's threads work on.
export function deskFrom(
  listOut: string,
  reviewNumbers: number[],
  me: string,
  agentBranches: string[],
  threads: Record<number, number>,
): GitLaneDeskPr[] {
  let rows: Record<string, unknown>[] = []
  try {
    const j = JSON.parse(listOut)
    rows = Array.isArray(j) ? j : []
  } catch {
    return []
  }
  const order = { review: 0, mine: 1, agent: 2, other: 3 }
  return rows
    .filter(r => typeof r.number === 'number')
    .map(r => {
      const rollup = (r.statusCheckRollup ?? []) as RollupItem[]
      const checks = checksOf(rollup)
      const failing = rollup
        .filter(x => FAILED.includes(String(x.conclusion || x.state || x.status || '').toUpperCase()))
        .map(x => ({ name: String(x.name || x.context || 'a check'), url: String(x.detailsUrl || x.targetUrl || '') }))
      const review = String(r.reviewDecision ?? '')
      const mergeable = String(r.mergeable ?? 'UNKNOWN')
      const author = String((r.author as { login?: string } | undefined)?.login ?? '')
      const branch = String(r.headRefName ?? '')
      const number = r.number as number
      const isDraft = r.isDraft === true
      const pr: GitLaneDeskPr = {
        number,
        title: String(r.title ?? ''),
        author,
        branch,
        url: String(r.url ?? ''),
        isDraft,
        review: (['APPROVED', 'CHANGES_REQUESTED', 'REVIEW_REQUIRED'].includes(review) ? review : '') as GitLaneDeskPr['review'],
        mergeable: (['MERGEABLE', 'CONFLICTING'].includes(mergeable) ? mergeable : 'UNKNOWN') as GitLaneDeskPr['mergeable'],
        checks,
        failing,
        threads: threads[number] ?? -1,
        group: reviewNumbers.includes(number)
          ? 'review'
          : agentBranches.includes(branch) || AGENT_BRANCH.test(branch) || AGENT_BODY.test(String(r.body ?? ''))
            ? 'agent'
            : me && author.toLowerCase() === me.toLowerCase()
              ? 'mine'
              : 'other',
        ready: false,
        updatedAt: String(r.updatedAt ?? ''),
      }
      pr.ready = !isDraft && pr.mergeable === 'MERGEABLE' && !checks.fail && !checks.pending && pr.review !== 'CHANGES_REQUESTED' && pr.review !== 'REVIEW_REQUIRED' && pr.threads <= 0
      return pr
    })
    .sort((a, b) => order[a.group] - order[b.group] || (a.ready === b.ready ? 0 : a.ready ? -1 : 1) || b.updatedAt.localeCompare(a.updatedAt))
}

// What on the desk waits on the person: a review asked of them, and on their
// own or an agent's PR a failing check, changes asked, open threads or a
// PR ready to land.
export function deskNeeds(prs: GitLaneDeskPr[]): number {
  return prs.filter(p => p.group === 'review' || (p.group !== 'other' && (p.checks.fail > 0 || p.review === 'CHANGES_REQUESTED' || p.threads > 0 || p.ready))).length
}

// `{ data: { repository: { pullRequests: { nodes: [{ number, reviewThreads: { nodes: [{ isResolved }] } }] } } } }`
export function openThreadsOf(out: string): Record<number, number> {
  const res: Record<number, number> = {}
  try {
    const nodes = JSON.parse(out)?.data?.repository?.pullRequests?.nodes ?? []
    for (const n of nodes) {
      if (typeof n?.number !== 'number') continue
      res[n.number] = (n.reviewThreads?.nodes ?? []).filter((t: { isResolved?: boolean }) => t && t.isResolved === false).length
    }
  } catch {
    // No counts: the desk shows none.
  }
  return res
}

// One PR's unresolved threads: where, who, and the first line they wrote.
export function threadListOf(out: string): { path: string; author: string; text: string }[] {
  try {
    const nodes = JSON.parse(out)?.data?.repository?.pullRequest?.reviewThreads?.nodes ?? []
    return nodes
      .filter((t: { isResolved?: boolean }) => t && t.isResolved === false)
      .map((t: { path?: string; comments?: { nodes?: { author?: { login?: string }; body?: string }[] } }) => {
        const c = t.comments?.nodes?.[0]
        const line = String(c?.body ?? '').replace(/\r/g, '').split('\n').map(l => l.trim()).find(Boolean) ?? ''
        return { path: String(t.path ?? ''), author: String(c?.author?.login ?? ''), text: line.length > 140 ? line.slice(0, 139) + '…' : line }
      })
  } catch {
    return []
  }
}

// A check's link names its GitHub Actions job: .../actions/runs/<run>/job/<job>.
export function jobOf(url: string): string {
  return /\/actions\/runs\/\d+\/job\/(\d+)/.exec(url)?.[1] ?? ''
}

// "owner/name" from `gh repo view --json nameWithOwner`.
export function repoSlugOf(out: string): { owner: string; name: string } | null {
  try {
    const [owner, name] = String(JSON.parse(out)?.nameWithOwner ?? '').split('/')
    return owner && name ? { owner, name } : null
  } catch {
    return null
  }
}

// --- Blast Radius: risky calls the guard holds for the person ---------------

export type Blast = { kind: 'delete' | 'database' | 'migration' | 'deploy' | 'publish' | 'outside' | 'many'; what: string; undo: string; key: string }

const UNDO = {
  delete: 'Saved files come back from git; unsaved changes and files git never saw are gone for good.',
  database: 'Only from a backup.',
  migration: 'Undo needs a down-migration; data a migration drops stays dropped.',
  deploy: 'Live at once; undo is a new deploy of the old version.',
  publish: 'Cannot be taken back; a newer version can only replace it.',
  outside: 'Git does not track it here, so the Git lane cannot undo it.',
  many: 'All of it is in git, so one discard undoes it; a save first keeps the good part.',
}

const RULES: { kind: Exclude<Blast['kind'], 'outside' | 'many'>; what: string; re: RegExp }[] = [
  { kind: 'delete', what: 'deletes a folder and everything in it', re: /\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b|\bRemove-Item\b[^;|&\n]*-Recurse|\b(rmdir|rd)\s+\/s\b|\bdel\s+[^;|&\n]*\/s\b|shutil\.rmtree|\brimraf\b/i },
  { kind: 'delete', what: 'deletes many files at once', re: /\b(rm|del|Remove-Item)\s+[^;|&\n]*\*/i },
  { kind: 'database', what: 'drops or empties database tables', re: /\bDROP\s+(TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\b|\bDELETE\s+FROM\s+[\w."]+\s*(;|"|'|$)/i },
  {
    kind: 'migration',
    what: 'changes the structure of a database',
    re: /\balembic\s+(upgrade|downgrade)\b|\bprisma\s+(migrate\s+(deploy|reset|dev)|db\s+push)\b|manage\.py\s+(migrate|flush)\b|\b(rails|rake)\s+db:(migrate|rollback|reset|drop|schema:load)\b|\bknex\s+migrate\b|\bflyway\s+(migrate|clean)\b|\bsupabase\s+db\s+(push|reset)\b|\bsequelize\s+db:migrate\b|\bdrizzle-kit\s+(push|migrate)\b|\bdotnet\s+ef\s+database\s+update\b/i,
  },
  {
    kind: 'deploy',
    what: 'changes a live server or what users see',
    re: /\bvercel\b[^;|&\n]*--prod\b|\bnetlify\s+deploy\b[^;|&\n]*--prod\b|\bfly(ctl)?\s+deploy\b|\bkubectl\s+(apply|delete|rollout|scale|patch)\b|\bterraform\s+(apply|destroy)\b|\bpulumi\s+(up|destroy)\b|\bhelm\s+(install|upgrade|uninstall|rollback)\b|\baz\s+(webapp|functionapp|containerapp|deployment)\b[^;|&\n]*\b(deploy|up|delete|create)\b|\bgcloud\b[^;|&\n]*\b(deploy|delete)\b|\baws\b[^;|&\n]*\b(deploy|delete-\w+|terminate-\w+)\b|\bdocker\s+push\b|\bssh\s+\S+[^;|&\n]*\b(rm|systemctl|docker|reboot|shutdown|apt|dnf)\b|\brsync\b[^;|&\n]*--delete\b/i,
  },
  { kind: 'publish', what: 'publishes a package or release for everyone', re: /\b(npm|pnpm)\s+publish\b|\byarn\s+(npm\s+)?publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+release\s+create\b|\bdotnet\s+nuget\s+push\b/i },
]

const norm = (p: string) => p.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
const isAbs = (p: string) => /^([a-zA-Z]:)?[\\/]/.test(p)
// Claude's own notes, plans and scratch space are not the project's files.
const SCRATCH = /\/\.claude\/|\/(tmp|temp)\/|\/appdata\/local\/temp\//i

export const MANY_FILES = 15

const FILE_TOOLS = new Set(['Edit', 'MultiEdit', 'Write', 'NotebookEdit'])
export const pathOf = (input: Record<string, unknown>) => String(input.file_path ?? input.notebook_path ?? '')

// The text a command hands to a file or a program as data: a shell heredoc
// (`<<'EOF' ... EOF`) or a PowerShell here-string (`@' ... '@`). Writing a
// test or a doc that mentions a delete is no delete, so the rules skip it.
// Trade-off, an accepted trade-off: a script fed through a heredoc and run at
// once (`python3 - <<EOF`) is not looked into either.
export function withoutData(command: string): string {
  return command
    .replace(/<<-?\s*(['"]?)([A-Za-z_][\w-]*)\1[^\n]*\n[\s\S]*?\n[ \t]*\2[ \t]*(?=\n|$)/g, '<<data')
    .replace(/@(['"])\r?\n[\s\S]*?\r?\n\1@/g, '@data@')
}

// Is this call one to hold? `files` is what Claude changed this turn so far.
export function blastOf(tool: string, input: Record<string, unknown>, root: string, files: string[]): Blast | null {
  if (tool === 'Bash' || tool === 'PowerShell') {
    const command = withoutData(String(input.command ?? ''))
    const c = command.replace(/\s+/g, ' ').trim()
    for (const r of RULES) {
      if (r.re.test(command)) return { kind: r.kind, what: r.what, undo: UNDO[r.kind], key: r.kind + ':' + c.slice(0, 200) }
    }
    return null
  }
  if (!FILE_TOOLS.has(tool)) return null
  const path = pathOf(input)
  if (!path) return null
  const p = norm(path)
  if (root && isAbs(path) && !SCRATCH.test(p + '/') && p !== norm(root) && !p.startsWith(norm(root) + '/')) {
    return { kind: 'outside', what: 'changes a file outside this project: ' + path, undo: UNDO.outside, key: 'outside:' + p }
  }
  if (!files.map(norm).includes(p) && files.length >= MANY_FILES) {
    return { kind: 'many', what: 'changes more than ' + MANY_FILES + ' files in one turn', undo: UNDO.many, key: 'many' }
  }
  return null
}
