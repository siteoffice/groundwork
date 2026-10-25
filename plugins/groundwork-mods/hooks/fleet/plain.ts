// Pure helpers: plain-language phrases, glyphs and colours. No engine calls,
// so the tests can check them directly.

export type Status = 'pending' | 'running' | 'waiting' | 'idle' | 'completed' | 'failed' | 'killed'

const base = (p: unknown): string => {
  const s = typeof p === 'string' ? p : ''
  const parts = s.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? s
}

const clip = (s: string, n: number): string => (s.length > n ? s.slice(0, n - 1) + '…' : s)

// One short human sentence for a tool call: verb first, object second.
export function phraseFor(tool: string, input: Record<string, unknown>): string {
  const t = String(tool)
  if (t === 'Read') return 'reading ' + base(input.file_path)
  if (t === 'Edit' || t === 'MultiEdit') return 'changing ' + base(input.file_path)
  if (t === 'Write') return 'writing ' + base(input.file_path)
  if (t === 'NotebookEdit') return 'changing ' + base(input.notebook_path)
  if (t === 'Bash' || t === 'PowerShell') return 'running ' + clip(String(input.description ?? input.command ?? 'a command'), 36)
  if (t === 'Grep' || t === 'Glob') return 'searching for ' + clip(String(input.pattern ?? ''), 28)
  if (t === 'WebSearch') return 'searching the web'
  if (t === 'WebFetch') return 'reading a web page'
  if (t === 'Agent' || t === 'Task') return 'handing off: ' + clip(String(input.description ?? 'a task'), 30)
  if (t === 'SendMessage') return 'sending a message'
  if (t === 'AskUserQuestion') return 'asking you a question'
  if (t === 'ExitPlanMode') return 'waiting for plan approval'
  if (t === 'TaskCreate' || t === 'TaskUpdate') return 'updating the task list'
  if (t === 'TodoWrite') return 'updating the to-do list'
  if (t === 'TaskGet' || t === 'TaskList' || t === 'TaskOutput') return 'checking the task list'
  if (t === 'TaskStop') return 'stopping a background task'
  if (t === 'Skill') return 'following the ' + clip(String(input.skill ?? input.name ?? 'a'), 24) + ' skill'
  if (t === 'ToolSearch') return 'looking up a tool'
  if (t === 'Monitor') return 'watching a background job'
  if (t === 'EnterPlanMode') return 'switching to planning'
  if (t === 'EnterWorktree') return 'opening a separate copy of the code'
  if (t === 'ExitWorktree') return 'leaving the separate copy of the code'
  if (t === 'Artifact') return 'publishing a page'
  if (t === 'ArtifactData') return 'updating a page\'s data'
  if (t === 'ArtifactComments') return 'reading page comments'
  if (t.startsWith('mcp__')) return 'using ' + t.split('__').slice(1).join(' ')
  return 'using ' + t
}

export const GLYPH: Record<Status, string> = {
  pending: '◌',
  running: '●',
  waiting: '◐',
  idle: '○',
  completed: '✓',
  failed: '✕',
  killed: '■',
}

export const WORD: Record<Status, string> = {
  pending: 'starting',
  running: 'working',
  waiting: 'waiting',
  idle: 'idle',
  completed: 'done',
  failed: 'failed',
  killed: 'stopped',
}

// Brand accent per terminal theme: mint is illegible on light, so light
// terminals get the deep teal from tokens.json. Status colours use theme keys
// so they follow the person's own theme.
export function accentFor(isLight: boolean): string {
  return isLight ? '#0B6B5B' : '#3EDCB0'
}

export function colorFor(status: Status, isLight: boolean): string {
  if (status === 'running') return accentFor(isLight)
  if (status === 'waiting') return 'warning'
  if (status === 'failed') return 'error'
  if (status === 'completed') return 'success'
  return 'inactive'
}

export function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return s + 's'
  const m = Math.round(s / 60)
  if (m < 60) return m + 'm'
  return Math.round(m / 60) + 'h'
}

export function money(usd?: number): string {
  return usd === undefined ? '' : '$' + usd.toFixed(2)
}

export function projectOf(cwd: string): string {
  return base(cwd) || cwd
}

// Agents in tree order: each parent followed by its children, with depth.
export function treeOrder<T extends { id: string; parentId?: string }>(list: T[]): { item: T; depth: number }[] {
  const ids = new Set(list.map(a => a.id))
  const kids = new Map<string, T[]>()
  const roots: T[] = []
  for (const a of list) {
    if (a.parentId && ids.has(a.parentId)) {
      kids.set(a.parentId, [...(kids.get(a.parentId) ?? []), a])
    } else roots.push(a)
  }
  const out: { item: T; depth: number }[] = []
  const walk = (a: T, d: number) => {
    out.push({ item: a, depth: d })
    for (const k of kids.get(a.id) ?? []) walk(k, d + 1)
  }
  roots.forEach(r => walk(r, 1))
  return out
}

export function countBy(list: { status: Status }[]): Record<Status, number> {
  const c = { pending: 0, running: 0, waiting: 0, idle: 0, completed: 0, failed: 0, killed: 0 }
  for (const a of list) c[a.status] += 1
  return c
}

// Rate-limit window names as the status line says them.
export function windowLabel(kind: string): string {
  if (kind === 'five_hour') return '5h'
  if (kind === 'seven_day') return 'week'
  if (kind === 'spend_limit') return 'spend'
  return kind.replace(/_/g, ' ')
}

// BurnMon: bars, colours, trend, reset times, burn rate.

// A bar of `cells` blocks, split into its filled and empty part.
export function bar(pct: number, cells: number): [string, string] {
  const filled = Math.max(0, Math.min(cells, Math.round((pct / 100) * cells)))
  return ['\u25AE'.repeat(filled), '\u25AF'.repeat(cells - filled)]
}

// Fill level as a theme key: fine, getting full, nearly out.
export function levelColor(pct: number): string {
  if (pct >= 85) return 'error'
  if (pct >= 60) return 'warning'
  return 'success'
}

const SPARK = ['\u2581', '\u2582', '\u2583', '\u2584', '\u2585', '\u2586', '\u2587', '\u2588']

// Context fill over time, 0 to 100, as one row of block characters.
export function spark(values: number[]): string {
  return values.map(v => SPARK[Math.max(0, Math.min(7, Math.floor((v / 100) * 8)))] ?? SPARK[0]).join('')
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const two = (n: number) => String(n).padStart(2, '0')

// When a window resets: a clock time within a day, a weekday after that.
export function resetLabel(iso: string | undefined, now: number): string {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const time = two(at.getHours()) + ':' + two(at.getMinutes())
  return at.getTime() - now < 20 * 3600 * 1000 ? time : (DAYS[at.getDay()] ?? '') + ' ' + time
}

// Dollars per hour since the session began; empty in the first five minutes.
export function perHour(usd: number | undefined, startedAt: number | undefined, now: number): string {
  if (usd === undefined || startedAt === undefined) return ''
  const hours = (now - startedAt) / 3600000
  return hours < 1 / 12 ? '' : '$' + (usd / hours).toFixed(2) + '/h'
}

// Black Box: where the log goes and what one line holds.

// Local calendar date, YYYY-MM-DD, for the daily file name.
export function dayOf(ms: number): string {
  const d = new Date(ms)
  return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate())
}

// Joins path parts with the separator the base path already uses.
export function joinPath(base: string, ...parts: string[]): string {
  const sep = base.includes('\\') ? '\\' : '/'
  return [base.replace(/[\\/]+$/, ''), ...parts].join(sep)
}

// What a tool call acted on: a file, a command, a pattern, a page.
export function targetOf(input: Record<string, unknown>): string {
  const v = input.file_path ?? input.notebook_path ?? input.command ?? input.pattern ?? input.url ?? input.query ?? input.description ?? ''
  return clip(String(v), 200)
}

export function clipText(s: string, n: number): string {
  return clip(s.replace(/\s+/g, ' ').trim(), n)
}

// Plain Questions: Claude's question dialog with one plain sentence per
// option. Only the descriptions change: the labels are what the answer
// sends back, so they stay exactly as Claude wrote them.

type QOption = { label?: unknown; description?: unknown; [k: string]: unknown }
type Question = { question?: unknown; options?: unknown; [k: string]: unknown }

// The key a dialog's explanations are kept under: its questions' own text.
export function questionsKey(questions: unknown[]): string {
  const text = JSON.stringify(questions)
  let h = 0
  for (let n = 0; n < text.length; n++) h = (h * 31 + text.charCodeAt(n)) | 0
  return 'q' + (h >>> 0).toString(36)
}

// The fast model's answer, `{"q":[["...","..."],...]}`, as one list per question.
export function parseExplanations(text: string): string[][] | undefined {
  const at = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (at < 0 || end <= at) return undefined
  try {
    const j = JSON.parse(text.slice(at, end + 1)) as { q?: unknown }
    if (!Array.isArray(j.q)) return undefined
    return j.q.map(row => (Array.isArray(row) ? row.map(x => clip(String(x ?? '').replace(/\s+/g, ' ').trim(), 160)) : []))
  } catch {
    return undefined
  }
}

export function withPlainWords(questions: unknown[], plain: string[][]): unknown[] {
  return questions.map((q, i) => {
    const qq = q as Question
    if (!Array.isArray(qq.options)) return q
    const options = (qq.options as QOption[]).map((o, k) => {
      const say = plain[i]?.[k]
      if (!say) return o
      const own = typeof o.description === 'string' ? o.description.trim() : ''
      return { ...o, description: own ? own + ' · In plain words: ' + say : 'In plain words: ' + say }
    })
    return { ...qq, options }
  })
}

// Quiet transcript (Plain Mode): what a finished call came to, in a few
// words, so the row says it and the raw output can stay folded away.
export function outcomeOf(tool: string, output: unknown): string {
  const o = (output && typeof output === 'object' ? output : {}) as Record<string, unknown>
  const t = String(tool)
  if (t === 'Edit' || t === 'MultiEdit' || t === 'Write' || t === 'NotebookEdit') {
    if (o.staged === true) return 'held for review, file unchanged'
    if (o.type === 'create') {
      const n = String(o.content ?? '').split('\n').length
      return 'new file, ' + n + ' line' + (n === 1 ? '' : 's')
    }
    const git = o.gitDiff as { additions?: number; deletions?: number } | undefined
    let add = git?.additions ?? 0
    let del = git?.deletions ?? 0
    if (!git) {
      for (const h of (o.structuredPatch ?? []) as { lines?: string[] }[]) {
        for (const l of h.lines ?? []) {
          if (l.startsWith('+')) add += 1
          else if (l.startsWith('-')) del += 1
        }
      }
    }
    return add || del ? '+' + add + ' −' + del + ' lines' : ''
  }
  if (t === 'Bash' || t === 'PowerShell') {
    if (o.interrupted === true) return 'stopped'
    if (o.backgroundTaskId) return 'running in the background'
    const text = String(o.stdout ?? '') + '\n' + String(o.stderr ?? '')
    const passed = /(\d+) (passed|passing)\b/i.exec(text)
    const failed = /(\d+) (failed|failing)\b/i.exec(text)
    if (failed && Number(failed[1]) > 0) return 'tests red, ' + failed[1] + ' failed' + (passed ? ', ' + passed[1] + ' passed' : '')
    if (passed) return 'tests green, ' + passed[1] + ' passed'
    const n = String(o.stdout ?? '').split('\n').filter(l => l.trim()).length
    return n ? n + ' line' + (n === 1 ? '' : 's') + ' of output' : 'no output'
  }
  if (t === 'Read') {
    const n = (o.file as { numLines?: number } | undefined)?.numLines
    return typeof n === 'number' ? n + ' lines' : ''
  }
  if (t === 'Grep' || t === 'Glob') {
    const n = typeof o.numFiles === 'number' ? o.numFiles : undefined
    return n === undefined ? '' : n ? n + ' found' : 'nothing found'
  }
  return ''
}

// A folded run of calls (reads, searches, listings) as one plain line that
// names what it touched: "looked at notes.md and 2 more, searched once".
export function groupLineOf(calls: readonly { tool: string; input?: unknown }[]): string {
  const of = (names: string[]) => calls.filter(c => names.includes(String(c.tool)))
  const reads = of(['Read'])
  const searches = of(['Grep', 'Glob'])
  const runs = of(['Bash', 'PowerShell'])
  const rest = calls.length - reads.length - searches.length - runs.length
  const first = base(((reads[0]?.input ?? {}) as Record<string, unknown>).file_path)
  const parts = [
    reads.length ? 'looked at ' + (first || 'a file') + (reads.length > 1 ? ' and ' + (reads.length - 1) + ' more' : '') : '',
    searches.length ? 'searched ' + (searches.length > 1 ? searches.length + ' times' : 'once') : '',
    runs.length ? 'ran ' + runs.length + ' command' + (runs.length > 1 ? 's' : '') : '',
    rest ? rest + ' other step' + (rest > 1 ? 's' : '') : '',
  ].filter(Boolean)
  return parts.join(', ') || 'a few steps'
}

// A call the Git lane guard held is no failure: the person decides. Its
// refusal text says so.
export function isGuardHold(output: unknown): boolean {
  const text = typeof output === 'string' ? output : JSON.stringify(output ?? '')
  return /Git lane guard (held|stopped)/.test(text)
}

// Startup and reload notices about plugins and hooks: useful to a developer,
// noise to everyone else, so Plain Mode folds them.
export function isSetupNoise(text: string): boolean {
  return /hooks\.json|hook was skipped|runs first and sent the event|unknown keys|plugin\.json changed|reloaded \(/i.test(text)
}

// Plain Mode overhead: the two model calls Plain Mode makes spend the person's
// own quota and never reach the transcript, so each one is logged here for
// BurnMon, and a daily cap keeps them small.
export const PLAIN_CAP_DEFAULT = 50000

export type OverheadUsage = {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
}

// Every token the call used: new input, output, and both cache counts.
export function tokensOf(u: OverheadUsage): number {
  return (u.input_tokens || 0) + (u.output_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0)
}

export function overheadFile(home: string, day: string): string {
  return joinPath(home, '.burnmon', 'overhead', day + '.ndjson')
}

// One NDJSON line; BurnMon reads these field names, so they stay as they are.
export function overheadLine(o: { at: string; session: string; sw: 'plain-sowhat' | 'plain-questions'; model: string; usage: OverheadUsage }): string {
  return JSON.stringify({
    at: o.at,
    session: o.session,
    plugin: 'groundwork-mods',
    switch: o.sw,
    model: o.model,
    input: o.usage.input_tokens,
    output: o.usage.output_tokens,
    cache_read: o.usage.cache_read_input_tokens,
    cache_creation: o.usage.cache_creation_input_tokens,
  })
}

export function plainTip(spent: number, cap: number, isOn: boolean): string {
  return 'Plain Mode: ' + spent + ' tokens today of ' + cap + ' · ' + (isOn ? 'on' : 'off') + ' · /plain'
}

// The first line of a command's output, for a toast.
export function firstLine(s: string): string {
  return clip(s.trim().split('\n')[0] ?? '', 160)
}
