// Pure helpers for Threads and Fleet: no engine calls, so the tests can
// check them directly.

import type { FleetAgentView, FleetAttention, FleetCard, FleetDiffFile, FleetShell, FleetStatus, FleetThread } from '../types'

// Which check a command is, from the command line alone. The last run of
// each kind wins, so a fixed test run turns a red tick green.
const PROOFS: [string, RegExp][] = [
  ['typecheck', /\b(tsc|mypy|pyright|vue-tsc|typecheck|type-check)\b/],
  ['lint', /\b(eslint|ruff|lint|flake8|pylint|biome|prettier --check|golangci-lint)\b/],
  ['tests', /\b(pytest|vitest|jest|mocha|go test|cargo test|dotnet test|npm (run )?test|pnpm (run )?test|yarn test|bun test|plugin test|unittest)\b/],
  ['build', /\b(npm run build|pnpm build|yarn build|cargo build|go build|dotnet build|vite build|next build)\b/],
]

export function proofOf(command: string): string | undefined {
  const c = command.toLowerCase()
  for (const [name, re] of PROOFS) if (re.test(c)) return name
  return undefined
}

export const PROOF_ORDER = ['tests', 'typecheck', 'lint', 'build']

// `git diff --numstat`: lines added and removed, and how many files.
export function numstatSum(out: string): { files: number; add: number; del: number } {
  let files = 0
  let add = 0
  let del = 0
  for (const line of out.split('\n')) {
    const m = /^(\d+|-)\t(\d+|-)\t(.+)$/.exec(line)
    if (!m) continue
    files += 1
    add += m[1] === '-' ? 0 : Number(m[1])
    del += m[2] === '-' ? 0 : Number(m[2])
  }
  return { files, add, del }
}

// A unified diff, one entry per file: its path, its hunks only (what the
// Code element reads), and its counts. Cut at `budget` characters, whole
// files only; `more` says how many did not fit.
export function splitDiff(out: string, budget = 60000): { files: FleetDiffFile[]; more: number } {
  const chunks = out.split(/^diff --git /m).slice(1)
  const files: FleetDiffFile[] = []
  let used = 0
  let more = 0
  for (const chunk of chunks) {
    const lines = chunk.split('\n')
    const plus = lines.find(l => l.startsWith('+++ '))
    const minus = lines.find(l => l.startsWith('--- '))
    const head = /^a\/(.+?) b\/(.+)$/.exec(lines[0] ?? '')
    let path = head?.[2] ?? ''
    if (plus && plus !== '+++ /dev/null') path = plus.slice(4).replace(/^b\//, '')
    else if (minus && minus !== '--- /dev/null') path = minus.slice(4).replace(/^a\//, '')
    const at = lines.findIndex(l => l.startsWith('@@'))
    const hunks = at < 0 ? '' : lines.slice(at).join('\n').replace(/\n+$/, '')
    const add = hunks.split('\n').filter(l => l.startsWith('+')).length
    const del = hunks.split('\n').filter(l => l.startsWith('-')).length
    const isBinary = at < 0 && /Binary files/.test(chunk)
    if (used + hunks.length > budget) {
      more += 1
      continue
    }
    used += hunks.length
    files.push({ path, hunks, add, del, isBinary })
  }
  return { files, more }
}

// Where a thread stands for the person: needs you, working, or out of the way.
export type ThreadState = 'needs-you' | 'working' | 'failed' | 'settled'

export function threadState(t: FleetThread): ThreadState {
  if (t.settled) return 'settled'
  if (t.status === 'failed' || t.status === 'killed') return 'failed'
  if (t.status === 'completed' || t.status === 'idle' || t.status === 'waiting') return 'needs-you'
  return 'working'
}

const RANK: Record<ThreadState, number> = { 'needs-you': 0, failed: 1, working: 2, settled: 3 }

// Needs you first, then failed, then working; newest first within each.
export function sortThreads(list: FleetThread[]): FleetThread[] {
  return [...list].sort((a, b) => RANK[threadState(a)] - RANK[threadState(b)] || b.startedAt - a.startedAt)
}

// A short name for a thread from its task: the first few words.
export function threadName(task: string): string {
  const words = task.replace(/\s+/g, ' ').trim().split(' ').slice(0, 7).join(' ').replace(/[.,:;]+$/, '')
  return words.length > 40 ? words.slice(0, 39) + '…' : words || 'thread'
}

// `gh pr view --json number,state,statusCheckRollup`
export function prOf(out: string): FleetThread['pr'] {
  try {
    const j = JSON.parse(out) as { number?: unknown; state?: unknown; statusCheckRollup?: { conclusion?: string; state?: string; status?: string }[] }
    if (typeof j.number !== 'number') return undefined
    const checks = { pass: 0, fail: 0, pending: 0 }
    for (const r of j.statusCheckRollup ?? []) {
      const v = String(r.conclusion || r.state || r.status || '').toUpperCase()
      if (['SUCCESS', 'NEUTRAL', 'SKIPPED'].includes(v)) checks.pass += 1
      else if (['FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE'].includes(v)) checks.fail += 1
      else checks.pending += 1
    }
    return { number: j.number, state: String(j.state ?? 'OPEN'), checks }
  } catch {
    return undefined
  }
}

// The reviewer's answer: its first line says ok or issues.
export function verdictOf(answer: string): 'ok' | 'issues' {
  const first = answer.trim().split('\n')[0] ?? ''
  return /VERDICT:\s*ok/i.test(first) ? 'ok' : 'issues'
}

// Fleet: a card is live while its session wrote it in the last 90 seconds.
export const FLEET_STALE_MS = 90000

export function liveCards(cards: FleetCard[], now: number): FleetCard[] {
  const best = new Map<string, FleetCard>()
  for (const c of cards) {
    if (c.ended || now - c.at > FLEET_STALE_MS) continue
    const had = best.get(c.sessionId)
    if (!had || had.at < c.at) best.set(c.sessionId, c)
  }
  const rank = (c: FleetCard) => (c.state === 'waiting' ? 0 : c.state === 'working' ? 1 : 2)
  return [...best.values()].sort((a, b) => rank(a) - rank(b) || a.project.localeCompare(b.project))
}

// What to paste to open a session in another terminal pane: PowerShell on
// Windows, a POSIX shell elsewhere.
export function resumeCommand(card: Pick<FleetCard, 'cwd' | 'sessionId'>): string {
  if (/^[A-Za-z]:\\/.test(card.cwd)) return "Set-Location '" + card.cwd.replace(/'/g, "''") + "'; claude --resume " + card.sessionId
  return "cd '" + card.cwd.replace(/'/g, "'\\''") + "' && claude --resume " + card.sessionId
}

// The folder one day's cards live in, so old days never need listing.
export function fleetDir(home: string, day: string): string {
  const sep = home.includes('\\') ? '\\' : '/'
  return [home.replace(/[\\/]+$/, ''), '.claude', 'groundwork-fleet', day].join(sep)
}

export function dirOf(path: string): string {
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return cut <= 0 ? path : path.slice(0, cut)
}

// The Fleet runs git in folders a downloaded repo controls: no fsmonitor, no
// hooks, no external diff or textconv program. The platform comes from the
// caller as a flag, never from a global.
export function nullDevice(isWindows: boolean): string {
  return isWindows ? 'NUL' : '/dev/null'
}

export function hardenGit(argv: string[], isWindows: boolean): string[] {
  if (argv[0] !== 'git') return argv
  const rest = argv.slice(1)
  const at = rest.findIndex((a, i) => a === 'diff' && rest[i - 1] !== '-C')
  const body = at < 0 ? rest : [...rest.slice(0, at + 1), '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', ...rest.slice(at + 1)]
  return ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + nullDevice(isWindows), ...body]
}

// The same two settings for the git that `gh` runs (git 2.31 or newer).
export function gitConfigEnv(isWindows: boolean): Record<string, string> {
  return {
    GIT_CONFIG_COUNT: '2',
    GIT_CONFIG_KEY_0: 'core.fsmonitor',
    GIT_CONFIG_VALUE_0: 'false',
    GIT_CONFIG_KEY_1: 'core.hooksPath',
    GIT_CONFIG_VALUE_1: nullDevice(isWindows),
  }
}

export function sameDir(a: string, b: string): boolean {
  const n = (s: string) => s.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  return n(a) === n(b)
}

// A thread works only in a folder under <repo>/.claude/worktrees/<name>.
export function isUnderWorktrees(path: string, repoRoot: string): boolean {
  const n = (s: string) => s.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  const root = n(repoRoot)
  if (!root) return false
  const p = n(path)
  if (p.split('/').some(seg => seg === '.' || seg === '..')) return false
  const head = root + '/.claude/worktrees/'
  return p.startsWith(head) && p.length > head.length
}

export function worked(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return s + 's'
  const m = Math.floor(s / 60)
  if (m < 60) return m + 'm ' + (s % 60) + 's'
  return Math.floor(m / 60) + 'h ' + (m % 60) + 'm'
}

// Fleet: what in one session needs the person, newest first. A permission
// counts once it has waited five seconds, so asks that auto mode settles at
// once never show.
export type AttentionInput = {
  sessionId: string
  project: string
  mainBusy: boolean
  mainAnswer: string
  mainAnswerAt: number
  asks: Record<string, { text: string; at: number }>
  perms: Record<string, { text: string; at: number }>
  agents: { id: string; name: string; status: FleetStatus }[]
  answers: Record<string, string>
  doneAt: Record<string, number>
  now: number
}

export function attentionOf(i: AttentionInput): FleetAttention[] {
  const out: FleetAttention[] = []
  const nameOf = (loop: string) => (loop === 'main' ? 'main thread' : i.agents.find(a => a.id === loop)?.name ?? 'agent')
  const item = (loop: string, kind: FleetAttention['kind'], text: string, at: number) => {
    const agentId = loop === 'main' ? '' : loop
    out.push({ key: [i.sessionId, loop, kind, at].join('|'), sessionId: i.sessionId, project: i.project, agentId, agent: nameOf(loop), kind, text, at })
  }
  for (const [loop, a] of Object.entries(i.asks)) item(loop, 'question', a.text, a.at)
  for (const [loop, p] of Object.entries(i.perms)) if (i.now - p.at >= 5000 && !i.asks[loop]) item(loop, 'permission', p.text, p.at)
  for (const a of i.agents) {
    if (i.asks[a.id] || i.perms[a.id]) continue
    if (a.status === 'completed') item(a.id, 'done', i.answers[a.id] || 'finished', i.doneAt[a.id] ?? 0)
    else if (a.status === 'failed' || a.status === 'killed') item(a.id, 'failed', i.answers[a.id] || (a.status === 'killed' ? 'was stopped' : 'failed'), i.doneAt[a.id] ?? 0)
  }
  if (!i.mainBusy && i.mainAnswer && !i.asks.main && !i.perms.main) item('main', 'done', i.mainAnswer, i.mainAnswerAt)
  return out.sort((a, b) => b.at - a.at).slice(0, 12)
}

export const KIND_WORD: Record<FleetAttention['kind'], string> = {
  question: 'asks you',
  permission: 'needs your OK',
  done: 'finished',
  failed: 'failed',
}

// A key a Button can carry: letters, digits, dash and underscore only.
export function safeKey(s: string): string {
  let h = 0
  for (let n = 0; n < s.length; n++) h = (h * 31 + s.charCodeAt(n)) | 0
  return (h >>> 0).toString(36)
}

// Markdown marks read as noise in a terminal row: bold, code ticks, headings.
export function plainText(md: string): string {
  return md
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*(-{3,}|\*{3,}|_{3,})\s*$/gm, '')
    .trim()
}

// Agent view: `claude agents --json`, every live session and the background
// ones still working or blocked. Fields as the docs list them; others dropped.
export function parseAgentView(out: string): FleetAgentView[] {
  try {
    const j = JSON.parse(out.trim()) as unknown
    if (!Array.isArray(j)) return []
    return j
      .filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
      .map(x => ({
        sessionId: typeof x.sessionId === 'string' ? x.sessionId : undefined,
        id: typeof x.id === 'string' ? x.id : undefined,
        name: typeof x.name === 'string' ? x.name : undefined,
        cwd: String(x.cwd ?? ''),
        kind: String(x.kind ?? ''),
        state: typeof x.state === 'string' ? x.state : undefined,
        status: typeof x.status === 'string' ? x.status : undefined,
        waitingFor: typeof x.waitingFor === 'string' ? x.waitingFor : undefined,
        startedAt: typeof x.startedAt === 'number' ? x.startedAt : undefined,
        ...(x.pinned === true ? { pinned: true } : {}),
      }))
  } catch {
    return []
  }
}

const tail = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p

// What agent view knows that the Fleet cards do not: sessions with no Fleet
// running (background ones, or ones started before the mods) that wait on
// you, finished or failed.
export function agentViewAttention(list: FleetAgentView[], cards: FleetCard[], me: string, now: number): FleetAttention[] {
  const known = new Set(cards.map(c => c.sessionId))
  const out: FleetAttention[] = []
  for (const e of list) {
    const sid = e.sessionId ?? e.id ?? ''
    if (!sid || sid === me || known.has(sid)) continue
    const waiting = e.status === 'waiting' || e.state === 'blocked'
    const kind: FleetAttention['kind'] | undefined = waiting
      ? /permission|sandbox|worker/.test(e.waitingFor ?? '') ? 'permission' : 'question'
      : e.state === 'done' && e.kind === 'background'
        ? 'done'
        : e.state === 'failed'
          ? 'failed'
          : undefined
    if (!kind) continue
    const text = waiting ? 'waits for ' + (e.waitingFor || 'you') : kind === 'done' ? 'background session finished' : 'background session failed'
    out.push({
      key: ['av', sid, kind, e.startedAt ?? 0].join('|'),
      sessionId: sid,
      project: e.name || tail(e.cwd) || 'session',
      agentId: '',
      agent: 'main thread',
      kind,
      text,
      at: waiting ? now : e.startedAt ?? now,
      via: 'agents',
      ...(e.id ? { attach: 'claude attach ' + e.id } : {}),
      ...(e.kind === 'background' && e.id ? { stopId: e.id } : {}),
    })
  }
  return out
}

// Background shells of this session. No call lists them live, so the list
// follows three events: a shell starts (a Bash or PowerShell result names its
// task), a turn ends (Stop lists what is still in flight), a shell finishes
// (its notification names its task and how it ended).
export const SHELL_KEEP_MS = 600000

export type ShellTask = { id: string; type: string; status: string; description: string; command?: string }

export function shellState(status: string): FleetShell['state'] {
  const s = status.toLowerCase()
  if (s === 'running' || s === 'pending') return 'running'
  if (s === 'failed' || s === 'error') return 'failed'
  if (s === 'killed' || s === 'stopped' || s === 'cancelled') return 'killed'
  return 'completed'
}

export function startShell(list: FleetShell[], id: string, command: string, now: number): FleetShell[] {
  if (!id || list.some(s => s.id === id)) return list
  return [...list, { id, command, state: 'running', startedAt: now }].slice(-30)
}

// A notification is the shell's own word on how it ended: it wins over a guess
// made from a turn-end list, and nothing overrides it.
export function endShell(list: FleetShell[], id: string, state: FleetShell['state'], now: number): FleetShell[] {
  return list.map(s => (s.id === id && !s.isSure ? { ...s, state, endedAt: s.endedAt ?? now, isSure: true } : s))
}

// At turn end: a running shell the list no longer holds has finished (how, the
// notification will say); a shell in the list that was never seen is new.
export function reconcileShells(list: FleetShell[], tasks: readonly ShellTask[], now: number): FleetShell[] {
  const live = new Map(tasks.filter(t => t.type === 'shell').map(t => [t.id, t]))
  let next = list.map(s => {
    if (s.state !== 'running') return s
    const t = live.get(s.id)
    const state = t ? shellState(t.status) : 'completed'
    return state === 'running' ? s : { ...s, state, endedAt: now }
  })
  for (const t of live.values()) {
    if (shellState(t.status) === 'running' && !next.some(s => s.id === t.id)) next = [...next, { id: t.id, command: t.command ?? t.description, state: 'running', startedAt: now }]
  }
  return next.slice(-30)
}

// What the pane shows: running shells, and finished ones for ten minutes.
export function visibleShells(list: FleetShell[], now: number): FleetShell[] {
  return list.filter(s => s.state === 'running' || now - (s.endedAt ?? now) < SHELL_KEEP_MS)
}

// Stop button for a background session: the first press shows the exact
// command and opens a window; a second press inside it runs the command.
export const STOP_CONFIRM_MS = 5000

// Only a background session that agent view gives an id, and never this one.
export function canStop(v: FleetAgentView, me: string): boolean {
  return v.kind === 'background' && !!v.id && v.sessionId !== me && v.id !== me
}

export function armedText(id: string, isPinned: boolean): string {
  return 'claude stop ' + id + (isPinned ? ' (pinned session)' : '') + ' \u00B7 press Stop again within 5 seconds'
}
