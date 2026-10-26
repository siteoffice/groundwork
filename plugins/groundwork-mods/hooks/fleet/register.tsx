// The Fleet: the overview a semi-developer needs while agents work.
// Band above the prompt: one line, counts and cost, plus the BurnMon row
// (context, 5h and week windows, burn rate) that /burnmon switches on and off.
// Plain Mode (/plain): tool rows as one plain sentence, routine results hidden,
// and a two-line "So what" under long replies.
// Black Box (/blackbox): an NDJSON log of who did what (rewritten whole, not tamper-proof).
// Pane (/tower): every agent as a tree, what each one does in plain words,
// the messages between them; Threads (/thread, /threads): tasks that run as
// agents in their own git worktree, with their diff, checks and answer;
// Fleet: every Claude Code session on this machine as one card each.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, InputProps, Register, RenderElement } from 'claude-code'

import { chipRow, section, tips } from '../doctor/chips'
import type { Chip, Els } from '../doctor/chips'
import { burnmonNote, readBurnmon } from '../burnmon'

import type {
  DoctorFacts,
  FleetActivity,
  FleetAgent,
  FleetAgentView,
  FleetAttention,
  FleetCard,
  FleetDiff,
  FleetPlainSpend,
  FleetShell,
  FleetStatus,
  FleetThread,
  FleetUsage,
  FleetWire,
} from '../types'
import {
  KIND_WORD,
  PROOF_ORDER,
  STOP_CONFIRM_MS,
  agentViewAttention,
  armedText,
  attentionOf,
  canStop,
  parseAgentView,
  plainText,
  safeKey,
  dirOf,
  endShell,
  fleetDir,
  isUnderWorktrees,
  liveCards,
  numstatSum,
  prOf,
  proofOf,
  reconcileShells,
  resumeCommand,
  shellState,
  sortThreads,
  splitDiff,
  startShell,
  threadName,
  threadState,
  verdictOf,
  visibleShells,
  worked,
} from './threads'
import { isTrustedFolder as trustCheck, runGit } from '../gitsafe'
import {
  GLYPH,
  PLAIN_CAP_DEFAULT,
  firstLine,
  overheadFile,
  overheadLine,
  plainTip,
  tokensOf,
  WORD,
  accentFor,
  ago,
  bar,
  clipText,
  colorFor,
  countBy,
  dayOf,
  joinPath,
  levelColor,
  money,
  perHour,
  parseExplanations,
  phraseFor,
  projectOf,
  questionsKey,
  withPlainWords,
  resetLabel,
  spark,
  targetOf,
  treeOrder,
  windowLabel,
  outcomeOf,
  groupLineOf,
  isGuardHold,
  isSetupNoise,
} from './plain'

const PANE = 'groundwork-fleet'
const TICK_MS = 4000
const CARD_BEAT_MS = 30000
const THREAD_TYPE = 'groundwork-mods:thread'
const REVIEWER_TYPE = 'groundwork-mods:reviewer'

const THREAD_PROMPT = [
  'You are a thread: one task, worked end to end in a git worktree of your own, in the background,',
  'while the person watches a list of threads and reads your diff beside it.',
  'Work only inside your worktree. Before you finish, run the tests, type checks and linters',
  'that apply to what you changed. End with a short answer: what changed, what you checked and how,',
  'and anything the person must decide. Do not commit, push, open a pull request or merge unless the',
  'task asks for it. In a repository with a siteoffice.json, any commit, push or pull request goes',
  'through the siteoffice-git skill only, never raw git. No em dashes.',
].join(' ')

const REVIEWER_PROMPT = [
  'You review one diff a thread made. The task, the worktree path and the diff are in your prompt.',
  'Open files in the worktree when you need context. Change nothing.',
  'Your first line is exactly "VERDICT: ok" or "VERDICT: issues".',
  'Then at most six short lines, each one concrete problem with its file and line, most severe first,',
  'or "No issues found." No preamble, no em dashes.',
].join(' ')

// Module state: starts over on a reload, which only costs a recheck.
let ticks = 0
let lastCardAt = 0
// Agent types belong to the loaded copy of the module: a reload drops them,
// so this flag lives here, not in $.state, which outlives a reload.
let typesRegistered = false
// Set by /thread: the next new thread is the one to show.
let wantPick = false
// The full task of each thread asked for, by the short name Claude gives it.
const asked = new Map<string, string>()
const checkedDirs = new Set<string>()
// Fleet: when this copy started (a focus request older than that is old
// news), and which items it already told the person about.
let startedAt = 0
const notified = new Set<string>()
let isPrimed = false
// Background shells: a notification is seen while a row draws, and drawing may
// not write state, so the row leaves it here and the next tick files it.
const shellNotes: { id: string; state: FleetShell['state']; at: number }[] = []
// The day the cap toast was last shown: it says so once a day.
let capToastDay = ''

const sessionKey = atom({ plugin: 'groundwork-mods', key: 'fleet.sessionKey' } as const, '')
const project = atom({ plugin: 'groundwork-mods', key: 'fleet.project' } as const, '')
const isLight = atom({ plugin: 'groundwork-mods', key: 'fleet.isLight' } as const, false)
const isBusy = atom({ plugin: 'groundwork-mods', key: 'fleet.isBusy' } as const, false)
const questions = atom({ plugin: 'groundwork-mods', key: 'fleet.questions' } as const, 0)
const sessionId = atom({ plugin: 'groundwork-mods', key: 'fleet.sessionId' } as const, '')
const tab = atom({ plugin: 'groundwork-mods', key: 'fleet.tab' } as const, 'agents' as 'agents' | 'threads' | 'fleet')
const agents = atom({ plugin: 'groundwork-mods', key: 'fleet.agents' } as const, [] as FleetAgent[])
const activity = atom({ plugin: 'groundwork-mods', key: 'fleet.activity' } as const, {} as Record<string, FleetActivity>)
const wire = atom({ plugin: 'groundwork-mods', key: 'fleet.wire' } as const, [] as FleetWire[])
const usage = atom({ plugin: 'groundwork-mods', key: 'fleet.usage' } as const, { limits: [] } as FleetUsage)
const burnOn = atom({ plugin: 'groundwork-mods', key: 'fleet.burnOn' } as const, true)
const burnmon = atom({ plugin: 'groundwork-mods', key: 'fleet.burnmon' } as const, { exists: false, ageSeconds: null } as DoctorFacts['burnmon'])
const ctxHistory = atom({ plugin: 'groundwork-mods', key: 'fleet.ctxHistory' } as const, [] as number[])
const plainOn = atom({ plugin: 'groundwork-mods', key: 'fleet.plainOn' } as const, false)
const bbOn = atom({ plugin: 'groundwork-mods', key: 'fleet.bbOn' } as const, false)
const bbQueue = atom({ plugin: 'groundwork-mods', key: 'fleet.bbQueue' } as const, [] as string[])
const lastStatus = atom({ plugin: 'groundwork-mods', key: 'fleet.lastStatus' } as const, {} as Record<string, string>)
const soWhat = atom({ plugin: 'groundwork-mods', key: 'fleet.soWhat' } as const, '')
const threads = atom({ plugin: 'groundwork-mods', key: 'fleet.threads' } as const, {} as Record<string, FleetThread>)
const reviewers = atom({ plugin: 'groundwork-mods', key: 'fleet.reviewers' } as const, {} as Record<string, string>)
const pick = atom({ plugin: 'groundwork-mods', key: 'fleet.pick' } as const, '')
const diff = atom({ plugin: 'groundwork-mods', key: 'fleet.diff' } as const, null as FleetDiff | null)
const showSettled = atom({ plugin: 'groundwork-mods', key: 'fleet.showSettled' } as const, false)
const repoRoot = atom({ plugin: 'groundwork-mods', key: 'fleet.repoRoot' } as const, '')
const fleet = atom({ plugin: 'groundwork-mods', key: 'fleet.fleet' } as const, [] as FleetCard[])
const fleetPick = atom({ plugin: 'groundwork-mods', key: 'fleet.fleetPick' } as const, '')
const lastAsk = atom({ plugin: 'groundwork-mods', key: 'fleet.lastAsk' } as const, '')
const lastAnswer = atom({ plugin: 'groundwork-mods', key: 'fleet.lastAnswer' } as const, '')
const cardSent = atom({ plugin: 'groundwork-mods', key: 'fleet.cardSent' } as const, '')
const asks = atom({ plugin: 'groundwork-mods', key: 'fleet.asks' } as const, {} as Record<string, { text: string; at: number }>)
const perms = atom({ plugin: 'groundwork-mods', key: 'fleet.perms' } as const, {} as Record<string, { text: string; at: number }>)
const answers = atom({ plugin: 'groundwork-mods', key: 'fleet.answers' } as const, {} as Record<string, string>)
const doneAt = atom({ plugin: 'groundwork-mods', key: 'fleet.doneAt' } as const, {} as Record<string, number>)
const lastAnswerAt = atom({ plugin: 'groundwork-mods', key: 'fleet.lastAnswerAt' } as const, 0)
const fleetSeen = atom({ plugin: 'groundwork-mods', key: 'fleet.fleetSeen' } as const, [] as string[])
const fleetItem = atom({ plugin: 'groundwork-mods', key: 'fleet.fleetItem' } as const, '')
const plainQs = atom({ plugin: 'groundwork-mods', key: 'fleet.plainQs' } as const, {} as Record<string, string[][]>)
// Quiet transcript: the rows whose details the person unfolded.
const shown = atom({ plugin: 'groundwork-mods', key: 'fleet.shown' } as const, [] as string[])
const shells = atom({ plugin: 'groundwork-mods', key: 'fleet.shells' } as const, [] as FleetShell[])
const plainSpend = atom({ plugin: 'groundwork-mods', key: 'fleet.plainSpend' } as const, { day: '', tokens: 0 } as FleetPlainSpend)
const plainCap = atom({ plugin: 'groundwork-mods', key: 'fleet.plainCap' } as const, PLAIN_CAP_DEFAULT)
// The stop button: which background session waits for its second press, and since when.
const stopArm = atom({ plugin: 'groundwork-mods', key: 'fleet.stopArm' } as const, { id: '', at: 0 })

// One quiet row: state glyph, what Claude does, what it came to, and the
// fold marker. A call the Git guard held reads as held, in yellow.
function quietLine(
  els: Els,
  o: { id: string; key: string; tool: string; input: Record<string, unknown>; output: unknown; isRunning: boolean; isErrored: boolean; isInterrupted: boolean; isShown: boolean; light: boolean; onPress: () => unknown },
): RenderElement {
  const held = o.isErrored && isGuardHold(o.output)
  const state = o.isInterrupted ? 'killed' : held ? 'waiting' : o.isErrored ? 'failed' : o.isRunning ? 'running' : 'completed'
  const came = !o.isRunning && !o.isErrored && !o.isInterrupted ? outcomeOf(o.tool, o.output) : ''
  const tone = /^tests red/.test(came) ? 'error' : /^tests green/.test(came) ? 'success' : undefined
  return els.Button({
    key: o.key,
    plain: true,
    onPress: () => void o.onPress(),
    children: [
      els.Text({
        children: [
          els.Text({ color: colorFor(state, o.light), children: [GLYPH[state]] }),
          ' ' + phraseFor(o.tool, o.input),
          held
            ? els.Text({ color: 'warning', children: ['  held by the Git guard: you decide'] })
            : o.isErrored
              ? els.Text({ color: 'error', children: ['  failed'] })
              : o.isInterrupted
                ? els.Text({ dimColor: true, children: ['  stopped'] })
                : '',
          came ? els.Text({ color: tone, dimColor: !tone, children: ['  ' + came] }) : '',
          els.Text({ dimColor: true, children: [o.isShown ? '  \u25BE' : o.isRunning ? '' : '  \u25B8'] }),
        ],
      }),
    ],
  })
}

async function toggleShown($: EngineInterface, id: string) {
  await update($, shown, s => (s.includes(id) ? s.filter(x => x !== id) : [...s.slice(-200), id]))
}
const agentView = atom({ plugin: 'groundwork-mods', key: 'fleet.agentView' } as const, [] as FleetAgentView[])

// Routine results Plain Mode hides; errors always show.
const QUIET_RESULTS = new Set(['Read', 'Grep', 'Glob', 'Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'Bash', 'PowerShell', 'WebFetch', 'WebSearch', 'TaskCreate', 'TaskUpdate'])
const SO_WHAT_MIN = 1200

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

// One refresh: agents, threads, usage, theme, and this session's fleet card.
async function tick($: EngineInterface) {
  const list = await $.agent.list()
  const nextAgents: FleetAgent[] = list.map(a => ({
    id: a.id,
    name: a.name ?? a.description ?? a.type,
    type: a.type,
    status: a.status,
    parentId: a.parentId,
    description: a.description,
    spawnedBy: a.spawnedBy,
  }))
  if (!same(nextAgents, await read($, agents))) await update($, agents, () => nextAgents)
  await syncShells($).catch(() => undefined)
  await syncThreads($, nextAgents).catch(() => undefined)
  if (!typesRegistered) await registerTypes($).catch(() => undefined)

  // Black Box: every agent status change since the last tick.
  const before = await read($, lastStatus)
  const after: Record<string, string> = {}
  for (const a of nextAgents) {
    after[a.id] = a.status
    if (before[a.id] !== a.status) {
      if (['completed', 'failed', 'killed'].includes(a.status)) {
        const at = await $.clock.now()
        await update($, doneAt, d => ({ ...d, [a.id]: at }))
      }
      await record($, 'agent', { agent: a.name, type: a.type, from: before[a.id] ?? 'new', to: a.status, parent: a.parentId ?? 'main' })
    }
  }
  if (!same(before, after)) await update($, lastStatus, () => after)
  await flushBlackBox($).catch(() => undefined)

  const u = await $.session.usage()
  const nextUsage: FleetUsage = {
    ctx: u.context.percent,
    usd: u.cost?.usd,
    startedAt: u.startedAt,
    limits: u.rateLimits.map(r => ({ label: windowLabel(r.kind), pct: Math.round(r.percentUsed), resetsAt: r.resetsAt })),
  }
  if (!same(nextUsage, await read($, usage))) await update($, usage, () => nextUsage)
  const ctxNow = nextUsage.ctx
  if (ctxNow !== undefined) {
    await update($, ctxHistory, h => (h[h.length - 1] === ctxNow ? h : [...h, ctxNow].slice(-24)))
  }

  const theme = (await $.config.list()).find(row => row.key === 'theme')
  const light = String(theme?.value ?? '').includes('light')
  if (light !== (await read($, isLight))) await update($, isLight, () => light)

  // /clear resets $.state, and the order of its events is not ours to rely
  // on: any tick that finds the name or key gone puts them back.
  if (!(await read($, sessionKey)) || !(await read($, project))) await ensureIdentity($, await $.session.cwd())

  // BurnMon: only the age of its snapshot is kept, never the file's text.
  const bm = await readBurnmon($, await homeDir($)).catch(() => ({ exists: false, ageSeconds: null }) as DoctorFacts['burnmon'])
  if (!same(bm, await read($, burnmon))) await update($, burnmon, () => bm)

  ticks += 1
  await writeCard($, nextAgents, nextUsage).catch(() => undefined)
  await readFleet($).catch(() => undefined)
  await checkRequests($).catch(() => undefined)
  if (ticks % 3 === 1) await readAgentView($).catch(() => undefined)
  // The thread on screen keeps its diff fresh while it works; its pull
  // request is asked about less often, since that goes over the network.
  const shown = await read($, pick)
  const t = shown ? (await read($, threads))[shown] : undefined
  if (t?.root && (await read($, tab)) === 'threads') {
    if (ticks % 3 === 0 && t.status === 'running') await refreshThread($, t.id, true).catch(() => undefined)
    if (ticks % 15 === 0) await refreshPr($, t.id).catch(() => undefined)
  }
}

// File what the rows saw (a shell's own notification) and drop shells that
// ended more than ten minutes ago.
async function syncShells($: EngineInterface) {
  const notes = shellNotes.splice(0)
  const now = await $.clock.now()
  const all = await read($, shells)
  let next = notes.reduce((list, n) => endShell(list, n.id, n.state, n.at), all)
  next = visibleShells(next, now)
  if (!same(all, next)) await update($, shells, () => next)
}

async function ensureIdentity($: EngineInterface, cwd: string) {
  if (!(await read($, sessionKey))) {
    const id = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : String(await $.clock.now()) + Math.random()
    await update($, sessionKey, () => id)
  }
  await update($, project, () => projectOf(cwd))
  // The switches live in $.store, so they hold across sessions.
  await loadSwitches($)
}

// Black Box: queue one line; the tick writes the queue to disk.
async function record($: EngineInterface, kind: string, fields: Record<string, unknown>) {
  if (!(await read($, bbOn))) return
  const at = new Date(await $.clock.now()).toISOString()
  const line = JSON.stringify({ v: 1, at, kind, session: (await read($, sessionKey)).slice(0, 8), project: await read($, project), ...fields })
  await update($, bbQueue, q => [...q, line].slice(-2000))
}

// Where the log goes: the project's own 03_logs when it has one, so the
// trail sits beside the work; otherwise a folder under the home directory,
// so a code repo gets no new folder.
async function blackBoxFile($: EngineInterface): Promise<string> {
  const cwd = await $.session.cwd()
  const day = dayOf(await $.clock.now())
  // One file per session per day: two sessions in one project never write
  // the same file, so neither can overwrite the other's lines.
  const name = day + '_' + ((await read($, sessionKey)).slice(0, 8) || 'session') + '.ndjson'
  if (await $.fs.exists(joinPath(cwd, '03_logs'))) return joinPath(cwd, '03_logs', 'blackbox', name)
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? cwd
  return joinPath(home, '.claude', 'groundwork-blackbox', (await read($, project)) || 'session', name)
}

async function flushBlackBox($: EngineInterface) {
  const queue = await read($, bbQueue)
  if (!queue.length) return
  const file = await blackBoxFile($)
  const old = (await $.fs.exists(file)) ? await $.fs.read(file) : ''
  await $.fs.write(file, old + queue.join('\n') + '\n')
  await update($, bbQueue, q => q.slice(queue.length))
}

async function loadSwitches($: EngineInterface) {
  const burn = await $.store.get('fleet.burnOn')
  const plain = await $.store.get('fleet.plainOn')
  const bb = await $.store.get('fleet.bbOn')
  const spend = (await $.store.get('fleet.plainSpend')) as Partial<FleetPlainSpend> | undefined
  const cap = await $.store.get('fleet.plainCap')
  if (spend && typeof spend.day === 'string' && typeof spend.tokens === 'number') await update($, plainSpend, () => ({ day: spend.day as string, tokens: spend.tokens as number }))
  await update($, plainCap, () => (typeof cap === 'number' && cap > 0 ? cap : PLAIN_CAP_DEFAULT))
  await update($, burnOn, () => burn !== false)
  await update($, plainOn, () => plain === true)
  await update($, bbOn, () => bb === true)
  const seen = await $.store.get('fleet.fleetSeen')
  if (Array.isArray(seen)) await update($, fleetSeen, () => seen.map(String))
}

async function togglePlain($: EngineInterface) {
  const on = !(await read($, plainOn))
  await update($, plainOn, () => on)
  await $.store.set('fleet.plainOn', on)
  const tokens = await spentToday($)
  const cap = await read($, plainCap)
  $.ui.toast((on ? 'Plain Mode on: plain words, quiet tool rows (press a row for details), questions explained' : 'Plain Mode off: full tool rows') + ' · ' + tokens + ' of ' + cap + ' tokens today')
}

// Plain Mode's own model calls: what they cost today, whether one may run,
// and one line per call for BurnMon.
async function spentToday($: EngineInterface): Promise<number> {
  const s = await read($, plainSpend)
  return s.day === dayOf(await $.clock.now()) ? s.tokens : 0
}

async function mayCallModel($: EngineInterface): Promise<boolean> {
  const spent = await spentToday($)
  const cap = await read($, plainCap)
  if (spent < cap) return true
  const day = dayOf(await $.clock.now())
  if (capToastDay !== day) {
    capToastDay = day
    $.ui.toast('Plain Mode paused: ' + spent + ' of ' + cap + ' tokens used today. It resumes tomorrow.')
  }
  return false
}

async function logOverhead($: EngineInterface, sw: 'plain-sowhat' | 'plain-questions', model: string, usage: Parameters<typeof tokensOf>[0]) {
  const total = tokensOf(usage)
  if (!total) return
  const now = await $.clock.now()
  const day = dayOf(now)
  await update($, plainSpend, s => ({ day, tokens: (s.day === day ? s.tokens : 0) + total }))
  await $.store.set('fleet.plainSpend', await read($, plainSpend))
  const home = await homeDir($)
  if (!home) return
  const file = overheadFile(home, day)
  const old = (await $.fs.exists(file)) ? String(await $.fs.read(file)) : ''
  const session = (await read($, sessionId)) || (await $.session.id()) || ''
  await $.fs.write(file, old + overheadLine({ at: new Date(now).toISOString(), session, sw, model, usage }) + '\n')
}

async function toggleBlackBox($: EngineInterface) {
  const on = !(await read($, bbOn))
  // Record the switch itself, so a gap in the trail is visible as a gap.
  if (!on) {
    await record($, 'blackbox', { to: 'off' })
    await flushBlackBox($).catch(() => undefined)
  }
  await update($, bbOn, () => on)
  await $.store.set('fleet.bbOn', on)
  if (on) await record($, 'blackbox', { to: 'on', cwd: await $.session.cwd() })
  $.ui.toast(on ? 'Black Box on: ' + (await blackBoxFile($)) : 'Black Box off')
}

async function summarize($: EngineInterface, answer: string) {
  if (!(await mayCallModel($))) return
  const r = await $.model.complete({
    model: 'haiku',
    prompt:
      'Summarize the reply below for a reader who is not a developer, in at most two short sentences: ' +
      'what was done, and what, if anything, the reader must decide or do. Use the language of the reply. ' +
      'No preamble, no lists, no em dashes.\n\n<reply>\n' + answer + '\n</reply>',
  })
  await logOverhead($, 'plain-sowhat', 'haiku', r.usage).catch(() => undefined)
  if (!r.isAnswered || !r.text.trim()) return
  const text = 'So what: ' + r.text.trim()
  await update($, soWhat, () => text)
  await $.session.append({ message: { type: 'system', content: [{ type: 'text', text }] } })
}

async function toggleBurn($: EngineInterface) {
  const on = !(await read($, burnOn))
  await update($, burnOn, () => on)
  await $.store.set('fleet.burnOn', on)
  $.ui.toast(on ? 'BurnMon band on' : 'BurnMon band off')
}

// Threads and Fleet.

async function git($: EngineInterface, argv: string[], timeoutMs = 8000, cwd?: string) {
  return runGit($, argv, timeoutMs, cwd)
}

const untrustedFolders = new Set<string>()

async function isTrustedFolder($: EngineInterface, dir: string): Promise<boolean> {
  return trustCheck($, dir, untrustedFolders)
}

// The thread and reviewer agent types: a thread works in a worktree of its
// own in the background; the reviewer only reads. Claude starts both, when
// the Fleet asks it to or when the person asks for one directly.
async function registerTypes($: EngineInterface) {
  await $.agent.register({
    name: 'thread',
    description: 'Use when the person asks to run a task as a separate thread: it works in its own git worktree in the background and shows in the Fleet\'s Threads tab with its diff.',
    prompt: THREAD_PROMPT,
    isolation: 'worktree',
    background: true,
  })
  await $.agent.register({
    name: 'reviewer',
    description: 'Use only when the Fleet asks for a review of a thread: reads one thread diff and answers a verdict.',
    prompt: REVIEWER_PROMPT,
    tools: ['Read', 'Grep', 'Glob'],
    background: true,
  })
  typesRegistered = true
}

async function changeThread($: EngineInterface, id: string, fn: (t: FleetThread) => FleetThread) {
  await update($, threads, all => (all[id] ? { ...all, [id]: fn(all[id] as FleetThread) } : all))
}

function newThread(id: string, task: string, name: string, now: number, isOurs: boolean): FleetThread {
  return { id, name, task, status: 'pending', startedAt: now, isOurs, answer: '', workedMs: 0, turns: 0, proofs: {} }
}

// Threads follow the agent list: a status change lands on the thread, and
// one that finishes or fails tells the person.
async function syncThreads($: EngineInterface, list: FleetAgent[]) {
  const all = await read($, threads)
  const revs = await read($, reviewers)
  const now = await $.clock.now()
  const next: Record<string, FleetThread> = { ...all }
  const done: FleetThread[] = []
  const newRevs: Record<string, string> = {}
  let fresh = ''
  for (const a of list) {
    if (revs[a.id]) continue
    // A reviewer names its thread in its description: "review <thread id>".
    if (a.type === REVIEWER_TYPE) {
      const of = /^review (\S+)/.exec(a.description)?.[1]
      const target = of ? next[of] : undefined
      if (of && target) {
        newRevs[a.id] = of
        next[of] = { ...target, review: { state: 'running', text: '', agentId: a.id } }
      }
      continue
    }
    const had = next[a.id]
    if (!had) {
      if (a.type !== THREAD_TYPE && a.spawnedBy !== 'groundwork-mods') continue
      next[a.id] = { ...newThread(a.id, asked.get(a.description) ?? a.description, threadName(a.description || a.name), now, true), status: a.status }
      fresh = a.id
      continue
    }
    if (had.status === a.status) continue
    const moved: FleetThread = { ...had, status: a.status }
    next[a.id] = moved
    if (['completed', 'failed', 'killed'].includes(a.status) && !['completed', 'failed', 'killed'].includes(had.status)) done.push(moved)
  }
  if (Object.keys(newRevs).length) await update($, reviewers, r => ({ ...r, ...newRevs }))
  if (!same(all, next)) await update($, threads, () => next)
  if (fresh && wantPick) {
    wantPick = false
    await update($, pick, () => fresh)
  }
  // Claude Code puts an agent's worktree at .claude/worktrees/agent-<id>
  // under the repo: look there, so a thread that only reads still shows
  // where it works. A file it touches is the fallback (detectRoot).
  for (const t of Object.values(next)) {
    if (t.root) continue
    const base = (await read($, repoRoot)) || (await $.session.cwd())
    const wt = joinPath(base, '.claude', 'worktrees', 'agent-' + t.id)
    if (!checkedDirs.has(t.id + '|' + wt) && (await $.fs.exists(wt).catch(() => false))) await detectRoot($, t.id, joinPath(wt, '.git'))
  }
  for (const t of done) {
    const text = 'Thread "' + t.name + '" ' + (t.status === 'completed' ? 'is ready for you' : t.status === 'failed' ? 'failed' : 'was stopped')
    $.ui.toast(text)
    void $.ui.notify(text, { title: 'Fleet' }).catch(() => undefined)
    if (t.root) void refreshThread($, t.id, (await read($, pick)) === t.id).catch(() => undefined)
  }
}

// A thread agent: one this mod spawned (the thread agent type).
async function isThreadAgent($: EngineInterface, agentId: string): Promise<boolean> {
  if ((await read($, threads))[agentId]) return true
  const a = (await read($, agents)).find(x => x.id === agentId)
  return !!a && (a.type === THREAD_TYPE || a.spawnedBy === 'groundwork-mods')
}

// Under <repo>/.claude/worktrees by spelling and where both really land.
async function isInWorktrees($: EngineInterface, path: string, main: string): Promise<boolean> {
  if (!isUnderWorktrees(path, main)) return false
  const real = async (p: string) => (await $.fs.stat(p, { resolve: true }).catch(() => undefined))?.realPath
  const [rp, rm] = [await real(path), await real(main)]
  return !!rp && !!rm && isUnderWorktrees(rp, rm)
}

// Where a thread agent works, from a file it touched with a call that
// succeeded: only a folder under the repo's .claude/worktrees counts; its
// fork point is the diff's base.
async function detectRoot($: EngineInterface, agentId: string, file: string) {
  if ((await read($, reviewers))[agentId]) return
  if (!(await isThreadAgent($, agentId))) return
  const t = (await read($, threads))[agentId]
  if (t?.root) return
  const dir = dirOf(file)
  if (checkedDirs.has(agentId + '|' + dir)) return
  checkedDirs.add(agentId + '|' + dir)
  let main = await read($, repoRoot)
  if (!main) {
    const top = await git($, ['git', '-C', await $.session.cwd(), 'rev-parse', '--show-toplevel'])
    main = top.exitCode === 0 ? top.stdout.trim() : ''
    if (main) await update($, repoRoot, () => main)
  }
  if (!(await isInWorktrees($, dir, main))) return
  const at = await git($, ['git', '-C', dir, 'rev-parse', '--show-toplevel', '--abbrev-ref', 'HEAD'])
  if (at.exitCode !== 0) return
  const [top = '', branch = ''] = at.stdout.trim().split('\n').map(l => l.trim())
  if (!top || !(await isInWorktrees($, top, main))) return
  const head = main ? await git($, ['git', '-C', main, 'rev-parse', 'HEAD']) : { exitCode: 1, stdout: '' }
  const fork = head.exitCode === 0 ? await git($, ['git', '-C', top, 'merge-base', 'HEAD', head.stdout.trim()]) : { exitCode: 1, stdout: '' }
  const base = fork.exitCode === 0 ? fork.stdout.trim() : head.exitCode === 0 ? head.stdout.trim() : 'HEAD'
  const now = await $.clock.now()
  const agent = (await read($, agents)).find(a => a.id === agentId)
  await update($, threads, all => ({
    ...all,
    [agentId]: {
      ...(all[agentId] ?? { ...newThread(agentId, agent?.description ?? '', threadName(agent?.description || agent?.name || 'thread'), now, false), status: (agent?.status ?? 'running') as FleetStatus }),
      root: top,
      base,
      branch,
    },
  }))
  await refreshThread($, agentId, false)
}

// Counts for the list; the full diff too when the thread is on screen.
async function refreshThread($: EngineInterface, id: string, withDiff: boolean) {
  const t = (await read($, threads))[id]
  if (!t?.root || !t.base) return
  const trusted = await isTrustedFolder($, t.root)
  if (!trusted !== !!t.untrusted) await changeThread($, id, x => ({ ...x, untrusted: !trusted }))
  if (!trusted) {
    await update($, diff, d => (d?.id === id ? null : d))
    return
  }
  const num = await git($, ['git', '-C', t.root, 'diff', '--numstat', t.base])
  const extra = await git($, ['git', '-C', t.root, 'ls-files', '--others', '--exclude-standard'])
  const untracked = extra.exitCode === 0 ? extra.stdout.split('\n').map(l => l.trim()).filter(Boolean) : []
  const sum = numstatSum(num.exitCode === 0 ? num.stdout : '')
  await changeThread($, id, x => ({ ...x, stat: { ...sum, untracked: untracked.length } }))
  if (!withDiff) return
  const full = await git($, ['git', '-C', t.root, 'diff', '--no-color', '-U3', t.base], 15000)
  const split = splitDiff(full.exitCode === 0 ? full.stdout : '')
  const at = await $.clock.now()
  await update($, diff, () => ({ id, files: split.files, more: split.more, untracked: untracked.slice(0, 20), at }))
}

async function refreshPr($: EngineInterface, id: string) {
  const t = (await read($, threads))[id]
  if (!t?.root || !t.branch || t.branch === 'HEAD') return
  if (!(await isTrustedFolder($, t.root))) return
  const out = await git($, ['gh', 'pr', 'view', '--json', 'number,state,statusCheckRollup'], 15000, t.root)
  const pr = out.exitCode === 0 ? prOf(out.stdout) : undefined
  if (!same(pr, t.pr)) await changeThread($, id, x => ({ ...x, pr }))
}

// A thread starts as a request to Claude, not as a call of the mod's own:
// auto mode reviews a tool call against the model reply that made it, so
// the Agent call has to come from Claude's reply. The next tick finds the
// new agent in the list by its type and shows it.
async function spawnThread($: EngineInterface, task: string) {
  const text = task.trim()
  if (!text) {
    $.ui.toast('A thread needs a task: /thread <what it should do>')
    return
  }
  if (!typesRegistered) await registerTypes($)
  const name = threadName(text)
  wantPick = true
  asked.set(name, text)
  // The new thread shows in the Fleet's list; its page is one press away.
  await update($, tab, () => 'agents')
  await record($, 'thread', { to: 'asked', task: clipText(text, 300) })
  // On a timer, so a /thread command has returned before the prompt queues.
  $.clock.after(0, () => void $.prompt.submit({
    text:
      'Start one background agent of type ' + THREAD_TYPE + ' now, with the description "' + name + '" and exactly this task as its prompt:\n\n' +
      text +
      '\n\nThe person asked for this thread with /thread in the Fleet. Start it, do nothing else, do not wait for its result, and answer in one short line.',
  }).catch(() => $.ui.toast('Could not ask Claude to start the thread')))
  $.ui.toast('Asked Claude to start the thread "' + name + '"')
  await openFleet($)
}

async function pickThread($: EngineInterface, id: string) {
  await update($, pick, () => id)
  await update($, diff, d => (d?.id === id ? d : null))
  $.clock.after(0, () => refreshThread($, id, true).then(() => refreshPr($, id)).catch(() => undefined))
}

// The reviewer starts the same way, through Claude. The diff goes to a
// file the reviewer reads, so it never fills the main conversation.
async function askReviewer($: EngineInterface, id: string) {
  const t = (await read($, threads))[id]
  if (!t?.root || !t.base) return
  if (!(await isTrustedFolder($, t.root))) {
    $.ui.toast('This thread\'s folder has git settings that run programs, so its diff is not read')
    return
  }
  if (!typesRegistered) await registerTypes($)
  const full = await git($, ['git', '-C', t.root, 'diff', '--no-color', '-U3', t.base], 15000)
  const body = full.exitCode === 0 ? full.stdout : ''
  if (!body.trim()) {
    $.ui.toast('Nothing to review yet: the thread has no changes')
    return
  }
  const home = await homeDir($)
  if (!home) return
  const file = joinPath(home, '.claude', 'groundwork-fleet', 'reviews', id + '.diff')
  await $.fs.write(file, body)
  await changeThread($, id, x => ({ ...x, review: { state: 'running', text: '', agentId: '' } }))
  await $.prompt.submit({
    text:
      'Start one background agent of type ' + REVIEWER_TYPE + ' now, with the description "review ' + id + '" and this prompt:\n\n' +
      'Review the diff a thread made. Task of the thread: ' + clipText(t.task, 600) + '\nWorktree: ' + t.root + '\nThe diff against where it forked is in ' + file + '; read it there.\n\n' +
      'The person asked for this review with the button in the Fleet. Start it, do nothing else, do not wait for its result, and answer in one short line.',
  })
  $.ui.toast('Asked Claude to start a reviewer for "' + t.name + '"')
}

// A message to an agent goes through Claude, as a thread start does: auto
// mode reviews a SendMessage only against the model reply that made it.
async function messageAgent($: EngineInterface, id: string, name: string, text: string) {
  const body = text.trim()
  if (!body) return
  $.clock.after(0, () => void $.prompt.submit({
    text:
      'Send this message, exactly as written, to the agent with id ' + id + ' ("' + name + '") using SendMessage. ' +
      'The person wrote it in the Fleet. Do nothing else and answer in one short line.\n\n' + body,
  }).catch(() => $.ui.toast('Could not ask Claude to send the message')))
  $.ui.toast('Asked Claude to pass your message to ' + name)
}

async function messageThread($: EngineInterface, id: string, text: string) {
  const t = (await read($, threads))[id]
  await messageAgent($, id, t?.name ?? 'the thread', text)
}

// Fleet: this session's card, in a file only this session writes, so two
// sessions can never overwrite each other. A day's cards share a folder.
async function homeDir($: EngineInterface): Promise<string> {
  return (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
}

async function writeCard($: EngineInterface, list: FleetAgent[], u: FleetUsage, ended = false) {
  let id = await read($, sessionId)
  if (!id) {
    id = await $.session.id()
    if (!id) return
    await update($, sessionId, () => id)
  }
  const home = await homeDir($)
  if (!home) return
  const now = await $.clock.now()
  const counts = countBy(list)
  const q = await read($, questions)
  const ts = Object.values(await read($, threads)).map(threadState)
  const g = await gitOfLane($)
  const revs = await read($, reviewers)
  const act = await read($, activity)
  const card: FleetCard = {
    v: 1,
    sessionId: id,
    project: await read($, project),
    cwd: await $.session.cwd(),
    state: q > 0 || counts.waiting > 0 ? 'waiting' : (await read($, isBusy)) || counts.running > 0 ? 'working' : 'idle',
    running: counts.running,
    questions: q,
    threads: { needsYou: ts.filter(s => s === 'needs-you').length, working: ts.filter(s => s === 'working').length },
    ctx: u.ctx,
    usd: u.usd,
    ask: await read($, lastAsk),
    answer: clipText(await read($, lastAnswer), 240),
    ...(g ? { git: g } : {}),
    agents: list.filter(a => !revs[a.id]).slice(0, 20).map(a => ({ id: a.id, name: a.name, status: a.status, doing: act[a.id]?.phrase ?? '' })),
    attention: attentionOf({
      sessionId: id,
      project: await read($, project),
      mainBusy: await read($, isBusy),
      mainAnswer: await read($, lastAnswer),
      mainAnswerAt: await read($, lastAnswerAt),
      asks: await read($, asks),
      perms: await read($, perms),
      agents: list.filter(a => !revs[a.id]),
      answers: await read($, answers),
      doneAt: await read($, doneAt),
      now,
    }),
    at: now,
    ...(ended ? { ended: true } : {}),
  }
  const { at: _at, ...body } = card
  const text = JSON.stringify(body)
  if (!ended && text === (await read($, cardSent)) && now - lastCardAt < CARD_BEAT_MS) return
  await $.fs.write(joinPath(fleetDir(home, dayOf(now)), id + '.json'), JSON.stringify(card))
  lastCardAt = now
  await update($, cardSent, () => text)
}

// The Git lane's view of this session's repo, read from its state: the
// The Fleet never runs git a second time for the card.
async function gitOfLane($: EngineInterface): Promise<FleetCard['git']> {
  type Lane = { isRepo?: boolean; branch?: string; files?: unknown[]; unshared?: number; pr?: { number: number; checks: { pass: number; fail: number; pending: number } } | null }
  const held = (await $.state.get({ plugin: 'groundwork-mods', key: 'git.repo' } as never).catch(() => ({ value: undefined }))) as unknown as { value?: Lane }
  const r = held.value
  if (!r?.isRepo || !r.branch) return undefined
  const c = r.pr?.checks
  return {
    branch: r.branch,
    changed: r.files?.length ?? 0,
    unshared: r.unshared ?? 0,
    ...(r.pr ? { pr: r.pr.number, prChecks: c ? (c.fail ? c.fail + ' failing' : c.pending ? c.pending + ' running' : 'green') : '' } : {}),
  }
}

async function readFleet($: EngineInterface) {
  const home = await homeDir($)
  if (!home) return
  const now = await $.clock.now()
  const cards: FleetCard[] = []
  for (const day of new Set([dayOf(now), dayOf(now - 86400000)])) {
    const dir = fleetDir(home, day)
    const entries = await $.fs.list(dir).catch(() => [])
    for (const f of entries) {
      if (f.kind !== 'file' || !f.name.endsWith('.json')) continue
      if (f.mtimeMs && now - f.mtimeMs > 180000) continue
      try {
        const c = JSON.parse(String(await $.fs.read(joinPath(dir, f.name)))) as FleetCard
        if (c?.v === 1 && c.sessionId) cards.push(c)
      } catch {
        // A card caught mid-write reads whole on the next tick.
      }
    }
  }
  const rows = liveCards(cards, now)
  if (!same(rows, await read($, fleet))) await update($, fleet, () => rows)
  // Tell the person once about each new item in another session; the
  // first read after a load only learns what is already there.
  const me = await read($, sessionId)
  const seen = new Set(await read($, fleetSeen))
  for (const c of rows) {
    if (c.sessionId === me) continue
    for (const a of c.attention ?? []) {
      if (notified.has(a.key) || seen.has(a.key)) continue
      notified.add(a.key)
      if (!isPrimed) continue
      const text = c.project + ': ' + a.agent + ' ' + KIND_WORD[a.kind]
      $.ui.toast(text + ' · /fleet')
      void $.ui.notify(text, { title: 'Fleet' }).catch(() => undefined)
    }
  }
  isPrimed = true
}

// Everything in the fleet that waits on the person and was not seen yet.
async function needsYou($: EngineInterface): Promise<FleetAttention[]> {
  const seen = new Set(await read($, fleetSeen))
  const cards = await read($, fleet)
  const extra = agentViewAttention(await read($, agentView), cards, await read($, sessionId), await $.clock.now())
  return [...cards.flatMap(c => c.attention ?? []), ...extra].filter(a => !seen.has(a.key)).sort((a, b) => b.at - a.at)
}

// Agent view as a second source: `claude agents --json` lists every live
// session and the background ones. One shared file keeps it, and whichever
// session finds it older than 30 seconds asks again, so the machine runs the
// command about twice a minute however many sessions are open.
const AGENT_VIEW_FRESH_MS = 30000

async function readAgentView($: EngineInterface, force = false) {
  const home = await homeDir($)
  if (!home) return
  const file = joinPath(home, '.claude', 'groundwork-fleet', 'agents.json')
  const now = await $.clock.now()
  let held: { at: number; list: FleetAgentView[] } | undefined
  try {
    if (await $.fs.exists(file)) held = JSON.parse(String(await $.fs.read(file))) as { at: number; list: FleetAgentView[] }
  } catch {
    held = undefined
  }
  if (!held || force || now - held.at > AGENT_VIEW_FRESH_MS) {
    // Claim the refresh first, so two sessions rarely run it at once.
    await $.fs.write(file, JSON.stringify({ at: now, list: held?.list ?? [] }))
    const out = await $.process.run(['claude', 'agents', '--json'], { timeoutMs: 20000 }).catch(() => ({ exitCode: 1, stdout: '' }))
    if (out.exitCode === 0) {
      held = { at: now, list: parseAgentView(out.stdout) }
      await $.fs.write(file, JSON.stringify(held))
    }
  }
  const list = held?.list ?? []
  if (!same(list, await read($, agentView))) await update($, agentView, () => list)
  // Tell the person once about a session only agent view knows of.
  const seen = new Set(await read($, fleetSeen))
  for (const a of agentViewAttention(list, await read($, fleet), await read($, sessionId), now)) {
    if (notified.has(a.key) || seen.has(a.key)) continue
    notified.add(a.key)
    if (!isPrimed) continue
    const text = a.project + ' ' + KIND_WORD[a.kind]
    $.ui.toast(text + ' \u00B7 /fleet')
    void $.ui.notify(text, { title: 'Fleet' }).catch(() => undefined)
  }
}

// Plain Questions (part of Plain Mode): the fast model writes one plain
// sentence per option of Claude's question; the dialog shows it under each.
async function explainQuestions($: EngineInterface, questions: unknown[]) {
  const key = questionsKey(questions)
  if ((await read($, plainQs))[key]) return
  if (!(await mayCallModel($))) return
  const r = await $.model.complete({
    model: 'haiku',
    prompt:
      'You help a reader who is not a developer answer a question from a coding assistant. ' +
      'For each option of each question below, write one short plain sentence, at most 20 words, ' +
      'that says what choosing it means in practice. No jargon, no em dashes, the language of the question. ' +
      'Answer only with JSON: {"q":[["sentence for option 1","sentence for option 2"]]}, one inner list per question, in order.\n\n' +
      JSON.stringify(questions).slice(0, 6000),
  })
  await logOverhead($, 'plain-questions', 'haiku', r.usage).catch(() => undefined)
  if (!r.isAnswered) return
  const plain = parseExplanations(r.text)
  if (!plain) return
  await update($, plainQs, all => ({ ...Object.fromEntries(Object.entries(all).slice(-20)), [key]: plain, last: plain }))
}

async function markSeen($: EngineInterface, key: string) {
  const next = [...(await read($, fleetSeen)).filter(k => k !== key), key].slice(-500)
  await update($, fleetSeen, () => next)
  await $.store.set('fleet.fleetSeen', next)
  await update($, fleetItem, v => (v === key ? '' : v))
}

// The stop button for a background session: the first press shows the exact
// command and opens a five second window; a second press inside it runs the
// official command. Never for an interactive session or for this one.
async function pressStop($: EngineInterface, id: string) {
  const now = await $.clock.now()
  const arm = await read($, stopArm)
  if (arm.id === id && now - arm.at <= STOP_CONFIRM_MS) {
    await update($, stopArm, () => ({ id: '', at: 0 }))
    await runStop($, id)
    return
  }
  await update($, stopArm, () => ({ id, at: now }))
  $.clock.after(STOP_CONFIRM_MS, () => void update($, stopArm, a => (a.id === id && a.at === now ? { id: '', at: 0 } : a)).catch(() => undefined))
}

async function runStop($: EngineInterface, id: string) {
  const row = (await read($, agentView)).find(v => v.id === id)
  if (!row || !canStop(row, await read($, sessionId))) {
    $.ui.toast('Not stopped: ' + id + ' is not a background session of another tab')
    return
  }
  const out = await $.process.run(['claude', 'stop', id], { timeoutMs: 20000 }).catch((err: unknown) => ({ exitCode: 1, stdout: '', stderr: String(err) }))
  if (out.exitCode !== 0) {
    $.ui.toast('Could not stop ' + id + ': ' + (firstLine(out.stderr || out.stdout) || 'no reason given'))
    return
  }
  $.ui.toast(firstLine(out.stdout) || 'stopped ' + id)
  await record($, 'session', { to: 'stopped', id, by: 'person' })
  await readAgentView($, true).catch(() => undefined)
}

function requestFile(home: string, target: string): string {
  return joinPath(home, '.claude', 'groundwork-fleet', 'requests', target + '.json')
}

// Open an item: here, the main view switches to that agent; in another
// session, a request file asks that session's Fleet to do the same.
async function openItem($: EngineInterface, a: FleetAttention) {
  const me = await read($, sessionId)
  if (a.sessionId === me) {
    await $.ui.view(a.agentId ? { agentId: a.agentId } : {})
  } else {
    const home = await homeDir($)
    if (!home) return
    await $.fs.write(requestFile(home, a.sessionId), JSON.stringify({ agentId: a.agentId, agent: a.agent, from: await read($, project), at: await $.clock.now() }))
    $.ui.toast('Pinged ' + a.project + ': its tab rings. Switch to the tab named ' + a.project + '.')
  }
  // A question or permission ask stays until it is answered; only a finished
  // or failed agent, which nothing else clears, counts as seen once opened.
  if (a.kind === 'done' || a.kind === 'failed') await markSeen($, a.key)
}

// A request from another session's Fleet: show that agent here.
async function checkRequests($: EngineInterface) {
  const me = await read($, sessionId)
  const home = await homeDir($)
  if (!me || !home) return
  const file = requestFile(home, me)
  if (!(await $.fs.exists(file))) return
  const r = JSON.parse(String(await $.fs.read(file))) as { agentId?: string; agent?: string; from?: string; at?: number }
  if (!r.at || r.at <= startedAt) return
  startedAt = r.at
  // Tell the person first: a dialog on screen (a question, say) can refuse
  // the view switch, and the notice must still arrive.
  const text = 'Fleet: ' + (r.from ? r.from + ' asks you to look at ' : 'look at ') + (r.agent || 'the main thread') + ' here'
  $.ui.toast(text)
  void $.ui.notify(text, { title: 'Fleet' }).catch(() => undefined)
  await $.ui.view(r.agentId ? { agentId: r.agentId } : {}).catch(() => undefined)
}

// A thread's turn: its answer, the time it worked, the reviewer's verdict.
async function threadTurn($: EngineInterface, agentId: string, answer: string, ms: number) {
  const thread = (await read($, reviewers))[agentId]
  if (thread) {
    if (answer) await changeThread($, thread, x => ({ ...x, review: { state: verdictOf(answer), text: clipText(answer, 600), agentId } }))
    return
  }
  if (!(await read($, threads))[agentId]) return
  await changeThread($, agentId, x => ({ ...x, answer: answer ? answer.slice(0, 1200) : x.answer, workedMs: x.workedMs + ms, turns: x.turns + 1 }))
  const isShown = (await read($, pick)) === agentId
  $.clock.after(0, () => refreshThread($, agentId, isShown).catch(() => undefined))
}

// A thread's tool call: a check it ran, and where it works.
async function threadTool($: EngineInterface, agentId: string, tool: string, input: Record<string, unknown>, outcome: string) {
  if ((tool === 'Bash' || tool === 'PowerShell') && typeof input.command === 'string') {
    const kind = proofOf(input.command)
    if (kind && outcome !== 'denied') await changeThread($, agentId, x => ({ ...x, proofs: { ...x.proofs, [kind]: outcome === 'ok' ? 'ok' : 'fail' } }))
  }
  const file = input.file_path ?? input.notebook_path
  if (outcome === 'ok' && typeof file === 'string' && file) $.clock.after(0, () => detectRoot($, agentId, file).catch(() => undefined))
}

// The icon buttons, the same set in the band and the Fleet pane. The band
// adds one entry icon, the Fleet's: yellow while something waits on you.
function fleetChips($: EngineInterface, where: 'b' | 'p', s: { burn: boolean; plain: boolean; rec: boolean; need: number; plainTip: string }): Chip[] {
  const k = (x: string) => (where === 'b' ? x : 'pane-' + x)
  const chips: Chip[] = [
    { key: k('burn'), glyph: '\u25A4', on: s.burn, tip: s.burn ? 'BurnMon on · /burnmon' : 'BurnMon off · /burnmon', onPress: () => toggleBurn($) },
    { key: k('plain'), glyph: '¶', on: s.plain, tip: s.plainTip, onPress: () => togglePlain($) },
    { key: k('bb'), glyph: '\u25CF', on: s.rec, color: 'error', tip: s.rec ? 'Black Box recording · /blackbox' : 'Black Box off · /blackbox', onPress: () => toggleBlackBox($) },
  ]
  return where === 'b'
    ? [{ key: 'fleet', glyph: '\u25CE', color: s.need ? 'warning' : undefined, tip: s.need ? 'Fleet: ' + s.need + ' need you · /fleet' : 'Open the Fleet · /fleet', onPress: () => openFleet($) }, ...chips]
    : chips
}

async function openFleet($: EngineInterface, columns?: number) {
  await $.ui.open({ id: PANE, title: 'Fleet', ...(columns ? { columns } : {}) })
}

// A thread's page in the Fleet: its answer, checks and diff. Back returns.
async function openThread($: EngineInterface, id: string) {
  await update($, tab, () => 'threads')
  await pickThread($, id)
}

// The one action a NEEDS YOU row stands for: a thread opens its page, an
// agent here opens its conversation, another session gets a ping.
async function actOn($: EngineInterface, a: FleetAttention) {
  const me = await read($, sessionId)
  if (a.sessionId === me && a.agentId && (await read($, threads))[a.agentId]) {
    await openThread($, a.agentId)
    if (a.kind === 'done' || a.kind === 'failed') await markSeen($, a.key)
    return
  }
  await openItem($, a)
}

export const registerFleet: Register = on => {
  // A matcher is needed: the host refuses two matcher-less session.start hooks in one module (the Doctor has one).
  on('session.start', { surface: ['terminal', 'desktop', 'mobile', 'vscode'] },async ($, e, next) => {
    await $.command.register({ name: 'tower', description: 'Open the Fleet (its old name): what waits on you, this session, other sessions' })
    await $.command.register({ name: 'burnmon', description: 'Switch the BurnMon band on or off: context, 5h and week limits, burn rate', immediate: true })
    await $.command.register({ name: 'plain', description: 'Switch Plain Mode on or off: quiet tool rows in plain words, explained questions, a short "So what" under long replies', immediate: true })
    await $.command.register({ name: 'thread', description: 'Start a thread: a task that runs in its own git worktree in the background. /thread <what it should do>' })
    await $.command.register({ name: 'threads', description: 'Open the Fleet: threads are listed under This session, each with its diff' })
    await $.command.register({ name: 'fleet', description: 'Open the Fleet: every session and agent on this machine, and what waits on you' })
    await $.command.register({ name: 'blackbox', description: 'Switch the Black Box log on or off: who did what, written as NDJSON (not tamper-proof)', immediate: true })
    await ensureIdentity($, e.cwd)
    startedAt = await $.clock.now()
    await record($, 'session', { to: 'start', cwd: e.cwd })
    $.clock.every(TICK_MS, () => tick($).catch(() => undefined))
    void tick($).catch(() => undefined)
    const started = await next(e)
    await registerTypes($).catch(() => undefined)
    return started
  })

  // /clear, /resume and /branch reset $.state and fire no session.start:
  // give the session its name and keys back.
  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    await ensureIdentity($, e.cwd)
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    await record($, 'session', { to: 'end', reason: e.reason })
    await flushBlackBox($).catch(() => undefined)
    // The card says the session ended, so the fleet drops it at once.
    await writeCard($, await read($, agents), await read($, usage), true).catch(() => undefined)
    return next(e)
  })

  on('command.run', { command: 'tower' }, async $ => {
    await openFleet($)
    return {}
  })

  on('command.run', { command: 'thread' }, async ($, e) => {
    await spawnThread($, e.args)
    return {}
  })

  on('command.run', { command: 'threads' }, async $ => {
    await update($, tab, () => 'agents')
    await openFleet($)
    return {}
  })

  on('command.run', { command: 'fleet' }, async $ => {
    await openFleet($)
    return {}
  })

  // Fleet: an ask the person must answer, noted only to show it. The
  // verdict passes on as it came.
  on('tool.check', async ($, e, next) => {
    const verdict = await next(e)
    if (verdict.decision === 'ask') {
      const who = e.agentId ?? 'main'
      const at = await $.clock.now()
      const text = 'wants to ' + phraseFor(String(e.tool), (e.input ?? {}) as Record<string, unknown>)
      await update($, perms, p => ({ ...p, [who]: { text, at } }))
    }
    return verdict
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'burnmon' }, async $ => {
    await toggleBurn($)
    return {}
  })

  on('command.run', { command: 'plain' }, async $ => {
    await togglePlain($)
    return {}
  })

  on('command.run', { command: 'blackbox' }, async $ => {
    await toggleBlackBox($)
    return {}
  })

  // Black Box: what the person asked for, as typed (first 500 characters).
  on('prompt.submit', async ($, e, next) => {
    if (!e.isMeta && !e.command) {
      await record($, 'prompt', { by: 'person', text: clipText(e.text, 500) })
      await update($, lastAsk, () => clipText(e.text, 160))
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  // Main-loop turn state: is Claude busy in this session?
  on('turn.start', async ($, e, next) => {
    if (!e.agentId) await update($, isBusy, () => true)
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (!e.agentId) await update($, isBusy, () => false)
    if (!e.agentId && e.reason === 'answer') {
      const at = await $.clock.now()
      // Kept whole up to 2000 characters: the Fleet shows it in full.
      await update($, lastAnswer, () => e.answer.slice(0, 2000))
      await update($, lastAnswerAt, () => at)
    }
    if (e.agentId && e.reason === 'answer') {
      const said = e.answer.slice(0, 2000)
      await update($, answers, a => ({ ...a, [e.agentId as string]: said }))
    }
    if (e.agentId) await threadTurn($, e.agentId, e.reason === 'answer' ? e.answer : '', e.durationMs ?? 0)
    const u = await read($, usage)
    await record($, 'turn', { agent: e.agentId ? (await read($, agents)).find(a => a.id === e.agentId)?.name ?? e.agentId : 'main', reason: e.reason, ms: e.durationMs, usd: u.usd })
    // Plain Mode: a long main reply gets a two-line "So what" from a fast
    // model, appended as a notice the model itself never reads. Runs on a
    // timer so the turn ends without waiting for it.
    if (!e.agentId && e.reason === 'answer' && e.answer.length >= SO_WHAT_MIN && (await read($, plainOn))) {
      const answer = e.answer.slice(0, 6000)
      $.clock.after(0, () => summarize($, answer).catch(() => undefined))
    }
    return next(e)
  })

  // Background shells: at turn end, Stop lists the background work still in flight.
  on('classic.Stop', async ($, e, next) => {
    const now = await $.clock.now()
    await update($, shells, list => reconcileShells(list, e.background_tasks ?? [], now))
    // One line in the Black Box, so a skipped Stop hook shows as a missing line.
    await record($, 'stop', { tasks: (e.background_tasks ?? []).length, shells: (await read($, shells)).length })
    return next(e)
  }).catch(($, e, next) => next(e))

  // A background shell's own notification says how it ended. Drawing may not
  // write state, so the row only notes it; the next tick files it.
  on('ui.render', { component: 'UserMessage' }, async ($, e, next) => {
    const task = e.props.task
    if (e.props.origin.kind === 'task-notification' && task?.id && task.status) {
      shellNotes.push({ id: task.id, state: shellState(task.status), at: await $.clock.now() })
      shellNotes.splice(0, Math.max(0, shellNotes.length - 50))
    }
    return next(e)
  })

  // What each loop is doing right now, in plain words.
  // A matcher is needed: the policy pack already holds the matcher-less tool.call hook (the host allows one).
  on('tool.call', { tool: /./ }, async ($, e, next) => {
    const who = e.agentId ?? 'main'
    const tool = String(e.tool)
    const isQuestion = tool === 'AskUserQuestion' || tool === 'ExitPlanMode'
    const now = await $.clock.now()
    await update($, activity, all => ({
      ...all,
      [who]: { tool, phrase: phraseFor(tool, e as Record<string, unknown>), since: now, calls: (all[who]?.calls ?? 0) + 1 },
    }))
    if (isQuestion) {
      await update($, questions, n => n + 1)
      const input = e as Record<string, unknown>
      const qs = input.questions as { question?: string }[] | undefined
      const text = tool === 'ExitPlanMode' ? 'wants you to approve a plan' : clipText(String(qs?.[0]?.question ?? 'has a question for you'), 300)
      await update($, asks, a => ({ ...a, [who]: { text, at: now } }))
      if (tool === 'AskUserQuestion' && Array.isArray(qs) && (await read($, plainOn))) {
        const asked = qs as unknown[]
        $.clock.after(0, () => explainQuestions($, asked).catch(() => undefined))
      }
    }
    let outcome = 'error'
    let answer = ''
    try {
      const ran = await next(e)
      outcome = ran.deny !== undefined ? 'denied' : ran.isError ? 'error' : 'ok'
      const bgId = (ran.result as { backgroundTaskId?: unknown } | undefined)?.backgroundTaskId
      if (!e.agentId && (tool === 'Bash' || tool === 'PowerShell') && ran.deny === undefined && typeof bgId === 'string' && bgId) {
        await update($, shells, list => startShell(list, bgId, String((e as Record<string, unknown>).command ?? ''), now)).catch(() => undefined)
      }
      if (isQuestion && ran.deny === undefined) answer = clipText(ran.text ?? JSON.stringify(ran.result ?? ''), 500)
      return ran
    } finally {
      const agentName = e.agentId ? (await read($, agents)).find(a => a.id === e.agentId)?.name ?? e.agentId : 'main'
      const input = e as Record<string, unknown>
      await record($, isQuestion ? 'decision' : 'tool', {
        agent: agentName,
        tool,
        did: phraseFor(tool, input),
        target: targetOf(input),
        outcome,
        ms: (await $.clock.now()) - now,
        ...(answer ? { answer } : {}),
      })
      if (isQuestion) {
        await update($, questions, n => Math.max(0, n - 1))
        await update($, asks, a => {
          const { [who]: _gone, ...rest } = a
          return rest
        })
      }
      await update($, perms, p => {
        if (!p[who]) return p
        const { [who]: _gone, ...rest } = p
        return rest
      })
      if (e.agentId) await threadTool($, e.agentId, tool, input, outcome)
      await update($, activity, all => {
        const cur = all[who]
        return cur && cur.since === now ? { ...all, [who]: { ...cur, tool: '', phrase: '' } } : all
      })
    }
  }).catch(($, e, next) => next(e))

  // The wire: every message one agent sends another, and whether it arrived.
  on('session.send', async ($, e, next) => {
    const sent = await next(e)
    const list = await read($, agents)
    const from = e.agentId ? list.find(a => a.id === e.agentId)?.name ?? 'agent' : 'main'
    const at = await $.clock.now()
    const text = sent.isDelivered ? e.text.replace(/\s+/g, ' ').trim() : 'not delivered: ' + sent.reason
    await update($, wire, w => [...w, { from, to: String(e.to), text, at, isDelivered: sent.isDelivered }].slice(-20))
    await record($, 'message', { from, to: String(e.to), delivered: sent.isDelivered, text: clipText(sent.isDelivered ? e.text : sent.reason, 300) })
    return sent
  }).catch(($, e, next) => next(e))

  // Spinner says what is happening, not just "Thinking".
  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    const now = (await read($, activity)).main
    if (!now?.phrase) return next(e)
    return next({ ...e, props: { ...e.props, suffix: ' · ' + now.phrase + '…' } })
  })

  // Plain Mode, quiet transcript: a tool row is one sentence with its state
  // glyph and what it came to ("+4 −1 lines", "tests green, 12 passed").
  // A press unfolds the full row and its output; another folds it again.
  on('ui.render', { component: 'ToolUse' }, async ($, e, next) => {
    if (!(await read($, plainOn))) return next(e)
    const els = $.ui.resolve(e)
    const { Box } = els
    const p = e.props
    const input = (p.input && typeof p.input === 'object' ? p.input : {}) as Record<string, unknown>
    const light = await read($, isLight)
    const id = String(p.tool_use_id)
    const isShown = (await read($, shown)).includes(id)
    const line = quietLine(els as unknown as Els, {
      id,
      key: 'q-' + id,
      tool: String(p.tool),
      input,
      output: p.output,
      isRunning: p.isRunning,
      isErrored: p.isErrored,
      isInterrupted: p.isInterrupted,
      isShown,
      light,
      onPress: () => toggleShown($, id),
    })
    if (!isShown) return line
    return (
      <Box flexDirection="column">
        {line}
        {await next(e)}
      </Box>
    )
  })

  // Plain Mode, quiet transcript: a folded run of reads and searches says
  // what it did in words; a press unfolds it into its rows.
  on('ui.render', { component: 'ToolGroup' }, async ($, e, next) => {
    if (!(await read($, plainOn)) || e.props.isExpanded) return next(e)
    const { Text, Button } = $.ui.resolve(e)
    const calls = e.props.calls
    const id = String(calls[0]?.tool_use_id ?? '')
    if (id && (await read($, shown)).includes(id)) return next({ ...e, props: { ...e.props, isExpanded: true } })
    const light = await read($, isLight)
    // A group of one call reads as that call's own row.
    if (calls.length === 1 && calls[0]) {
      const c = calls[0]
      return quietLine($.ui.resolve(e) as unknown as Els, {
        id,
        key: 'qg-' + (id || 'one'),
        tool: String(c.tool),
        input: (c.input && typeof c.input === 'object' ? c.input : {}) as Record<string, unknown>,
        output: c.output,
        isRunning: c.isRunning,
        isErrored: c.isErrored,
        isInterrupted: c.isInterrupted,
        isShown: false,
        light,
        onPress: () => (id ? toggleShown($, id) : undefined),
      })
    }
    const held = calls.filter(c => c.isErrored && isGuardHold(c.output)).length
    const failed = calls.some(c => c.isErrored && !isGuardHold(c.output))
    const state = e.props.isActive && calls.some(c => c.isRunning) ? 'running' : failed ? 'failed' : held ? 'waiting' : 'completed'
    return (
      <Button key={'qg-' + (id || 'group')} plain onPress={() => (id ? toggleShown($, id) : undefined)}>
        <Text>
          <Text color={colorFor(state, light)}>{GLYPH[state]}</Text>
          {' ' + groupLineOf(calls)}
          {failed ? <Text color="error">{'  one failed'}</Text> : ''}
          {held ? <Text color="warning">{'  held by the Git guard: you decide'}</Text> : ''}
          <Text dimColor>{id ? '  \u25B8' : ''}</Text>
        </Text>
      </Button>
    )
  })

  // Plain Mode: Claude's question dialog with one plain sentence per option,
  // once the fast model wrote them. Only descriptions change, never labels.
  on('ui.render', { component: 'AskUserQuestion' }, async ($, e, next) => {
    if (!(await read($, plainOn))) return next(e)
    const all = await read($, plainQs)
    const qs = (e.props.questions ?? []) as unknown[]
    const plain = all[questionsKey(qs)] ?? (all.last && all.last.length === qs.length ? all.last : undefined)
    if (!plain) return next(e)
    return next({ ...e, props: { ...e.props, questions: withPlainWords(qs, plain) } })
  })

  // Plain Mode: a notice about plugins and hooks shrinks to one short line
  // naming who it is from; a press on any of them shows them all in full.
  // Each notice draws on its own and drawing may not write state, so they
  // cannot merge into one count line. Other notices stay as they are.
  on('ui.render', { component: 'InfoNotice' }, async ($, e, next) => {
    if (!(await read($, plainOn)) || !isSetupNoise(e.props.text)) return next(e)
    if ((await read($, shown)).includes('notices')) return next(e)
    const { Button, Text } = $.ui.resolve(e)
    const from = /^([\w@./-]{1,40}):/.exec(e.props.text.trim())?.[1] ?? 'a plugin'
    let hash = 0
    for (const ch of e.props.text) hash = (hash * 31 + ch.charCodeAt(0)) | 0
    return (
      <Button key={'q-note-' + (hash >>> 0).toString(36)} plain onPress={() => toggleShown($, 'notices')}>
        <Text dimColor>{'\u00B7 setup note from ' + from + '  \u25B8'}</Text>
      </Button>
    )
  })

  // Plain Mode: routine results stay folded until their row is pressed;
  // errors and anything else always show.
  on('ui.render', { component: 'ToolResult' }, async ($, e, next) => {
    if (!(await read($, plainOn)) || e.props.isErrored || !QUIET_RESULTS.has(String(e.props.tool))) return next(e)
    if ((await read($, shown)).includes(String(e.props.tool_use_id))) return next(e)
    const { Box } = $.ui.resolve(e)
    return <Box />
  })

  // Band above the prompt: the whole picture in one line.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const light = await read($, isLight)
    const c = countBy(await read($, agents))
    const u = await read($, usage)
    const me = await read($, sessionId)
    const others = (await read($, fleet)).filter(r => r.sessionId !== me)
    // What waits elsewhere: everything in other sessions, plus this
    // session's own agents (its main thread you are looking at).
    const elsewhere = (await needsYou($)).filter(a => a.sessionId !== me)
    const need = (await needsYou($)).filter(a => a.sessionId !== me || a.agentId !== '').length
    const ready = Object.values(await read($, threads)).filter(t => threadState(t) === 'needs-you').length
    const q = await read($, questions)
    const burn = await read($, burnOn)
    const plain = await read($, plainOn)
    const rec = await read($, bbOn)
    const now = await $.clock.now()
    const cols = e.props.bodyColumns
    const history = await read($, ctxHistory)

    const parts: string[] = []
    if (c.running) parts.push(GLYPH.running + ' ' + c.running + ' working')
    if (c.waiting) parts.push(GLYPH.waiting + ' ' + c.waiting + ' waiting')
    if (c.completed) parts.push(GLYPH.completed + ' ' + c.completed + ' done')
    if (c.failed) parts.push(GLYPH.failed + ' ' + c.failed + ' failed')
    if (!parts.length) parts.push('no agents')

    const bandChips = fleetChips($, 'b', { burn, plain, rec, need, plainTip: plainTip(await spentToday($), await read($, plainCap), plain) })
    const agentRow = (
      <Box flexDirection="row" columnGap={2} alignItems="flex-start">
        <Box flexShrink={0} width={4}>
          <Text color={accentFor(light)} bold>0N</Text>
        </Box>
        {/* The icons come first, right after the label, in every band. */}
        <Box flexShrink={0}>{chipRow($.ui.resolve(e) as unknown as Els, bandChips, light)}</Box>
        <Text>{parts.join('  ')}</Text>
        {q > 0 && <Text color="warning">{q} question waiting on you</Text>}
        {ready > 0 && <Text color="warning">{ready} thread{ready > 1 ? 's' : ''} ready for you</Text>}
        {elsewhere.length > 0 && <Text color="warning">{GLYPH.waiting + ' ' + elsewhere.length + ' waiting in other sessions'}</Text>}
        <Text dimColor>
          {[
            !burn && u.ctx !== undefined ? 'ctx ' + u.ctx + '%' : '',
            !burn ? money(u.usd) : '',
            others.length ? others.length + 1 + ' sessions' : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </Text>
        {/* Last, so the label paints over the band's top line and moves nothing. */}
        {tips($.ui.resolve(e) as unknown as Els, bandChips, 'rule')}
      </Box>
    )
    // A thin rule sets the band apart from the transcript; what other mods
    // draw in the band (the Git lane, say) stays, under ours.
    // The rule takes the pane border's colour, so band and pane read as one set.
    const rule = <Text color="suggestion">{'\u2500'.repeat(Math.max(10, cols))}</Text>
    const theirs = await next(e)
    const othersDrew = (theirs as { type?: string } | undefined)?.type !== 'engine'
    if (!burn) {
      return (
        <Box flexDirection="column">
          {rule}
          {agentRow}
          {othersDrew && theirs}
        </Box>
      )
    }

    // BurnMon row: shorter bars and no trend in a narrow band.
    const wide = cols >= 110
    const gauge = (key: string, label: string, pct: number, cells: number, tail: string) => {
      const [full, empty] = bar(pct, cells)
      return (
        <Text key={key}>
          <Text dimColor>{label + ' '}</Text>
          <Text color={levelColor(pct)}>{full}</Text>
          <Text dimColor>{empty}</Text>
          {' ' + pct + '%'}
          {tail ? <Text dimColor>{' ' + tail}</Text> : ''}
        </Text>
      )
    }
    const rate = perHour(u.usd, u.startedAt, now)
    const burnRow = (
      <Box flexDirection="row" columnGap={2} alignItems="flex-start">
        <Box flexShrink={0} width={4}>
          <Text color={accentFor(light)} bold>BURN</Text>
        </Box>
        <Box flexDirection="row" columnGap={3} alignItems="flex-start">
          {u.ctx !== undefined && gauge('g-ctx', 'ctx', u.ctx, wide ? 10 : 6, '')}
          {(u.limits ?? []).map(l => gauge('g-' + l.label, l.label, l.pct, wide ? 8 : 5, resetLabel(l.resetsAt, now) ? '\u21BA ' + resetLabel(l.resetsAt, now) : ''))}
          {u.usd !== undefined && (
            <Text>
              {money(u.usd)}
              {rate ? <Text dimColor>{' at ' + rate}</Text> : ''}
            </Text>
          )}
          {wide && history.length > 1 && <Text dimColor>{'trend ' + spark(history)}</Text>}
        </Box>
      </Box>
    )
    return (
      <Box flexDirection="column">
        {rule}
        {agentRow}
        {burnRow}
        {othersDrew && theirs}
      </Box>
    )
  })

  // The Fleet: one pane, read top to bottom. What waits on you in any
  // session; what runs in this one; the other sessions. A thread opens its
  // own page here (answer, checks, diff) and Back returns to the list.
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const els = $.ui.resolve(e)
    const { Box, Text, Button, Code } = els
    const E = els as unknown as Els
    // The phone has no text field: it gets the slash command instead.
    const Input = (els as unknown as { Input?: (p: InputProps) => RenderElement }).Input
    const light = await read($, isLight)
    const accent = accentFor(light)
    const now = await $.clock.now()
    const cols = e.props.bodyColumns
    const me = await read($, sessionId)
    const u = await read($, usage)
    const busy = await read($, isBusy)
    const viewing = e.props.view.agentId
    const list = await read($, agents)
    const act = await read($, activity)
    const revs = await read($, reviewers)
    const all = await read($, threads)
    const cards = await read($, fleet)
    const items = (await needsYou($)).filter(a => !(a.sessionId === me && a.agentId === ''))
    const picked = await read($, fleetItem)
    const fp = await read($, fleetPick)
    const showS = await read($, showSettled)
    const rowChars = Math.max(60, cols * 2 - 10)
    const stack = cols < 90
    const kindColor = (k: FleetAttention['kind']) => (k === 'failed' ? 'error' : k === 'done' ? 'success' : 'warning')
    const kindGlyph = (k: FleetAttention['kind']) => (k === 'failed' ? GLYPH.failed : k === 'done' ? GLYPH.completed : GLYPH.waiting)
    const cardOf = (id: string) => cards.find(c => c.sessionId === id)
    // The stop button: armed while its second press is awaited.
    const stopSt = await read($, stopArm)
    const avAll = await read($, agentView)
    const armedFor = (id?: string) => !!id && stopSt.id === id && now - stopSt.at <= STOP_CONFIRM_MS
    const pinnedOf = (id?: string) => avAll.find(v => v.id === id)?.pinned === true
    const stopChipFor = (id: string, where: 'n' | 'o'): Chip => ({
      key: 'stop-' + where + '-' + safeKey(id),
      glyph: '\u25A0',
      color: 'error',
      tip: armedFor(id) ? 'Press again within 5 seconds: claude stop ' + id : 'Stop background session ' + id + ' (asks twice)',
      onPress: () => pressStop($, id),
    })

    const paneChips = fleetChips($, 'p', {
      burn: await read($, burnOn),
      plain: await read($, plainOn),
      rec: await read($, bbOn),
      need: 0,
      plainTip: plainTip(await spentToday($), await read($, plainCap), await read($, plainOn)),
    })
    const header = (
      <Box flexDirection="column">
        <Box flexDirection="row" columnGap={2} alignItems="flex-start">
          <Text wrap="truncate-end">
            <Text color={accent} bold>0N FLEET</Text>
            <Text dimColor>{'  ' + ((await read($, project)) || projectOf(await $.session.cwd().catch(() => '')))}</Text>
          </Text>
          <Box flexShrink={0}>{chipRow(E, paneChips, light)}</Box>
          {tips(E, paneChips)}
        </Box>
        <Text key="burnmon-note" dimColor>{burnmonNote(await read($, burnmon)).text}</Text>
        <Text dimColor>
          {[u.ctx !== undefined ? 'context ' + u.ctx + '%' : '', money(u.usd), ...(u.limits ?? []).map(l => l.label + ' ' + l.pct + '%')].filter(Boolean).join(' · ')}
        </Text>
        <Text> </Text>
      </Box>
    )

    // A thread's page.
    const t = (await read($, tab)) === 'threads' ? all[await read($, pick)] : undefined
    if (t) {
      const d = await read($, diff)
      const proofs = PROOF_ORDER.filter(k => t.proofs[k])
      const files = d?.id === t.id ? d.files : []
      const ci = t.pr?.checks
      return (
        <Box flexDirection="column">
          {header}
          <Button key="back" plain hotkey="b" label={'← Back to the Fleet'} onPress={() => update($, tab, () => 'agents')} />
          <Text> </Text>
          {section(E, t.name.toUpperCase(), cols)}
          <Text dimColor wrap="truncate-end">{[t.branch, t.root ? projectOf(t.root) : 'main folder', WORD[t.status]].filter(Boolean).join(' · ')}</Text>
          <Text>
            {(t.workedMs ? 'Worked for ' + worked(t.workedMs) : 'Started ' + ago(now - t.startedAt) + ' ago') + (t.turns ? ' · ' + t.turns + ' turn' + (t.turns > 1 ? 's' : '') : '')}
          </Text>
          {proofs.length ? (
            <Text>
              {proofs.map(k => (
                <Text key={'proof-' + k} color={t.proofs[k] === 'ok' ? 'success' : 'error'}>
                  {(t.proofs[k] === 'ok' ? '✓ ' : '✕ ') + k + '   '}
                </Text>
              ))}
            </Text>
          ) : (
            <Text dimColor>No tests, type check or lint run yet</Text>
          )}
          {t.pr && ci && (
            <Text>
              {'PR #' + t.pr.number + ' · ' + t.pr.state.toLowerCase() + ' · CI '}
              <Text color={ci.fail ? 'error' : ci.pending ? 'warning' : 'success'}>{ci.fail ? ci.fail + ' failing' : ci.pending ? ci.pending + ' running' : 'green'}</Text>
            </Text>
          )}
          {t.review && (
            <Text color={t.review.state === 'ok' ? 'success' : t.review.state === 'issues' ? 'warning' : 'inactive'}>
              {'◇ Reviewer: ' + (t.review.state === 'running' ? 'reading the diff…' : t.review.text)}
            </Text>
          )}
          <Text> </Text>
          {t.answer ? <Text>{plainText(t.answer)}</Text> : <Text dimColor>{t.task}</Text>}
          <Text> </Text>
          <Box flexDirection={stack ? 'column' : 'row'} columnGap={2}>
            <Button key="thread-open" plain label={'▸ Open conversation'} onPress={() => $.ui.view({ agentId: t.id })} />
            {t.root ? <Button key="thread-review" plain label={'◇ Ask a reviewer'} onPress={() => askReviewer($, t.id)} /> : null}
            <Button key="thread-settle" plain dimColor label={t.settled ? 'Unsettle' : '✓ Settle'} onPress={() => changeThread($, t.id, x => ({ ...x, settled: !x.settled }))} />
          </Box>
          {Input
            ? Input({ key: 'thread-msg', label: 'Message', placeholder: 'Tell this thread what to do next', submitLabel: 'send', onSubmit: (v: string) => void messageThread($, t.id, v) })
            : null}
          <Text> </Text>
          {section(E, 'DIFF', cols)}
          {!t.root && <Text dimColor>  Not seen in a worktree yet: changes in the main folder show in the Git lane.</Text>}
          {t.root && t.untrusted ? <Text color="warning">  This folder has git settings that run programs, so the diff is not shown.</Text> : null}
          {t.root && !t.untrusted && t.stat ? (
            <Text dimColor>
              {'  +' + t.stat.add + ' −' + t.stat.del + ' in ' + t.stat.files + ' file' + (t.stat.files === 1 ? '' : 's') + (t.stat.untracked ? ' · ' + t.stat.untracked + ' new' : '') + ' since it forked'}
            </Text>
          ) : null}
          {t.root && !t.untrusted && d?.id !== t.id ? <Text dimColor>  Reading the diff…</Text> : null}
          {files.map((f, i) => (
            <Box key={'file-' + i} flexDirection="column">
              <Text>
                <Text bold>{f.path}</Text>
                <Text color="success">{'  +' + f.add}</Text>
                <Text color="error">{' −' + f.del}</Text>
              </Text>
              {f.isBinary ? <Text dimColor>  binary file</Text> : f.hunks ? Code({ source: f.hunks, format: 'diff', path: f.path, wrap: 'truncate-end' }) : null}
            </Box>
          ))}
          {d?.id === t.id && d.more > 0 ? <Text dimColor>{'  ' + d.more + ' more file' + (d.more > 1 ? 's' : '') + ': open the conversation to see them'}</Text> : null}
          {d?.id === t.id && d.untracked.length > 0 ? <Text dimColor wrap="truncate-end">{'  new: ' + d.untracked.join(', ')}</Text> : null}
        </Box>
      )
    }

    // THIS SESSION: the main thread, every agent in tree order, and threads
    // the engine already let go of; settled threads fold away.
    const rows = treeOrder(list.filter(a => !revs[a.id]))
    const gone = Object.values(all).filter(x => !list.some(a => a.id === x.id))
    const settledCount = Object.values(all).filter(x => x.settled).length
    const mainAct = act.main
    const agentRow = (id: string, name: string, status: FleetAgent['status'], depth: number) => {
      const th = all[id]
      if (th?.settled && !showS) return null
      const a = act[id]
      const doing = status === 'running' && a?.phrase ? a.phrase + ' · ' + ago(now - a.since) : WORD[status]
      const isSlow = status === 'running' && !!a?.phrase && now - a.since > 30000
      const extra = th
        ? [th.stat ? '+' + th.stat.add + ' −' + th.stat.del : '', ...PROOF_ORDER.filter(k => th.proofs[k]).map(k => (th.proofs[k] === 'ok' ? '✓ ' : '✕ ') + k), th.pr ? '#' + th.pr.number : '']
            .filter(Boolean)
            .join(' · ')
        : ''
      return (
        <Button key={(th ? 'thread-' : 'agent-') + id} plain onPress={() => (th ? openThread($, id) : $.ui.view({ agentId: id }))}>
          {'  '.repeat(depth)}
          <Text color={th && threadState(th) === 'needs-you' ? 'warning' : colorFor(status, light)}>{GLYPH[status]}</Text>
          {' ' + name + (viewing === id ? ' ◂' : '')}
          {th ? <Text color={accent}>{'  thread'}</Text> : ''}
          {isSlow ? <Text color="warning">{'  ' + doing}</Text> : <Text dimColor>{'  ' + (th && status === 'completed' ? 'ready for you' : doing)}</Text>}
          {extra ? <Text dimColor>{' · ' + extra}</Text> : ''}
        </Button>
      )
    }

    const w = (await read($, wire)).slice(-3)
    const others = cards.filter(c => c.sessionId !== me)
    // Sessions agent view lists that write no Fleet card: background ones and
    // ones started without the mods.
    const avOnly = (await read($, agentView)).filter(v => {
      const sid = v.sessionId ?? v.id ?? ''
      return sid !== me && !cards.some(c => c.sessionId === sid)
    })
    const seenKeys = new Set(await read($, fleetSeen))
    const sh = visibleShells(await read($, shells), now)

    return (
      <Box flexDirection="column">
        {header}
        {section(E, 'NEEDS YOU' + (items.length ? ' · ' + items.length : ''), cols)}
        {items.length === 0 && <Text dimColor>  Nothing waits on you. Questions, permission asks and finished agents from every session land here.</Text>}
        {items.map(a => {
          const isOpen = a.key === picked
          const here = a.sessionId === me
          const isThread = here && !!a.agentId && !!all[a.agentId]
          // A session only agent view knows has no Fleet to ring: the icon
          // copies how to open it (background) or names its tab.
          const action: Chip = a.via
            ? {
                key: 'act-' + safeKey(a.key),
                glyph: '\u29C9',
                tip: a.attach ? 'Copy: ' + a.attach : 'In the tab named ' + a.project,
                onPress: () =>
                  a.attach
                    ? void $.ui.copy({ text: a.attach }).then(() => $.ui.toast('Copied: ' + a.attach + ' (paste it in a terminal tab)'))
                    : $.ui.toast('Switch to the tab named ' + a.project + '; no Fleet runs there to ring it'),
              }
            : {
                key: 'act-' + safeKey(a.key),
                glyph: here ? '\u25B8' : '\u25CE',
                tip: here ? (isThread ? 'Open the thread' : 'Open ' + a.agent) : 'Ping ' + a.project + ': its tab shows a notice',
                onPress: () => actOn($, a),
              }
          const chips: Chip[] = a.stopId ? [action, stopChipFor(a.stopId, 'n')] : [action]
          return (
            <Box key={'att-' + safeKey(a.key)} flexDirection="column">
              <Box flexDirection="row" columnGap={1} alignItems="flex-start">
                <Box flexShrink={0}>{chipRow(E, chips, light)}</Box>
                <Box flexGrow={1} flexShrink={1} minWidth={0}>
                  <Button key={'attb-' + safeKey(a.key)} plain onPress={() => update($, fleetItem, v => (v === a.key ? '' : a.key))}>
                    <Text color={kindColor(a.kind)}>{kindGlyph(a.kind)}</Text>
                    {' ' + (here ? 'this tab' : a.project) + ' › ' + a.agent + (isOpen ? ' ▾' : '')}
                    <Text color={kindColor(a.kind)}>{'  ' + KIND_WORD[a.kind]}</Text>
                    <Text dimColor>{'  ' + ago(now - a.at)}</Text>
                  </Button>
                </Box>
                {tips(E, chips)}
              </Box>
              {a.stopId && armedFor(a.stopId) ? <Text color="warning" wrap="truncate-end">{'    ' + armedText(a.stopId, pinnedOf(a.stopId))}</Text> : null}
              {isOpen ? (
                <Box flexDirection="column">
                  <Text wrap="wrap">{'    ' + plainText(a.text)}</Text>
                  <Text dimColor wrap="truncate-end">
                    {a.via
                      ? '    Where: ' + (a.attach ? 'a background session \u00B7 open it with ' + a.attach : 'the tab named ' + a.project + ' (no Fleet runs there)') + ' \u00B7 seen by agent view'
                      : '    Where: ' + (here ? 'this tab' : 'the tab named ' + a.project) + (cardOf(a.sessionId)?.cwd ? ' \u00B7 ' + cardOf(a.sessionId)?.cwd : '') + (a.agentId ? ' \u00B7 agent ' + a.agent : ' \u00B7 its main thread')}
                  </Text>
                  {a.kind === 'done' || a.kind === 'failed' ? (
                    <Box flexDirection="row">
                      <Text>{'    '}</Text>
                      <Button key="fleet-seen" plain dimColor label={'\u2713 Seen, clear it'} onPress={() => markSeen($, a.key)} />
                    </Box>
                  ) : (
                    <Text dimColor>{'    It clears itself once answered.'}</Text>
                  )}
                </Box>
              ) : (
                <Text dimColor wrap="wrap">{'    ' + clipText(plainText(a.text), rowChars)}</Text>
              )}
            </Box>
          )
        })}
        <Text> </Text>
        {section(E, 'THIS SESSION', cols)}
        <Button key="agent-main" plain onPress={() => $.ui.view({})}>
          <Text color={colorFor(busy ? 'running' : 'idle', light)}>{busy ? GLYPH.running : GLYPH.idle}</Text>
          {' main thread' + (viewing ? '' : ' ◂')}
          <Text dimColor>{'  ' + (mainAct?.phrase ? mainAct.phrase + ' · ' + ago(now - mainAct.since) : busy ? 'thinking' : 'waiting for you')}</Text>
        </Button>
        {rows.map(({ item, depth }) => agentRow(item.id, item.name, item.status, depth))}
        {gone.map(x => agentRow(x.id, x.name, x.status, 1))}
        {/* Background shells of this session; no call lists them live, so the label says when they update. */}
        {sh.map(s => {
          const tip: Chip = { key: 'shell-' + safeKey(s.id), glyph: '', tip: 'Updates at start, at turn end and when it finishes: Claude Code has no live list', onPress: () => undefined }
          const when = s.state === 'running' ? ago(now - s.startedAt) : ago(now - (s.endedAt ?? now)) + ' ago'
          return (
            <Box key={'shellrow-' + safeKey(s.id)} flexDirection="row" hover={{ scope: 'tip-' + tip.key }}>
              <Text key={'shell-' + safeKey(s.id)} color={colorFor(s.state, light)} wrap="truncate-end">
                {'  ' + GLYPH[s.state] + ' ' + clipText(s.command, 48) + '  ' + s.state + ' · ' + when}
              </Text>
              {tips(E, [tip])}
            </Box>
          )
        })}
        {/* Messages between agents, the newest few; one that never arrived in red. */}
        {w.map((m, i) => (
          <Text key={'w-' + i} wrap="truncate-end">
            <Text color={m.isDelivered === false ? 'error' : 'inactive'}>{'  \u2709 ' + (m.isDelivered === false ? GLYPH.failed + ' ' : '') + m.from + ' \u2192 ' + m.to}</Text>
            {m.isDelivered === false ? <Text color="error">{'  ' + m.text}</Text> : <Text dimColor>{'  ' + m.text}</Text>}
          </Text>
        ))}
        {settledCount > 0 && (
          <Button key="settled" plain dimColor label={(showS ? '▾' : '▸') + ' settled threads (' + settledCount + ')'} onPress={() => update($, showSettled, v => !v)} />
        )}
        {Input
          ? Input({ key: 'new-thread', label: '+ Thread', placeholder: 'A task for a new thread, in its own copy of the repo', submitLabel: 'start', onSubmit: (v: string) => void spawnThread($, v) })
          : <Text dimColor>  Start a thread with /thread and the task.</Text>}
        <Text> </Text>
        {section(E, 'OTHER SESSIONS' + (others.length + avOnly.length ? ' · ' + (others.length + avOnly.length) : ''), cols)}
        {others.length === 0 && avOnly.length === 0 && <Text dimColor>  None running. Each Claude Code session on this machine shows here.</Text>}
        {avOnly.map(v => {
          const waiting = v.status === 'waiting' || v.state === 'blocked'
          const status = waiting ? 'waiting' : v.state === 'working' || v.status === 'busy' ? 'running' : v.state === 'failed' ? 'failed' : v.state === 'done' ? 'completed' : 'idle'
          const stoppable = canStop(v, me)
          const stopId = v.id ?? ''
          return (
            <Box key={'av-' + safeKey((v.sessionId ?? v.id ?? '') + v.cwd)} flexDirection="column">
              <Box flexDirection="row" columnGap={1} alignItems="flex-start">
                {stoppable ? <Box flexShrink={0}>{chipRow(E, [stopChipFor(stopId, 'o')], light)}</Box> : null}
                <Box flexGrow={1} flexShrink={1} minWidth={0}>
                  <Text wrap="truncate-end">
                    <Text color={colorFor(status, light)}>{GLYPH[status]}</Text>
                    {' ' + (v.name || projectOf(v.cwd))}
                    {waiting ? <Text color="warning">{'  waits for ' + (v.waitingFor || 'you')}</Text> : ''}
                    <Text dimColor>{'  ' + [v.kind === 'background' ? 'background' : 'no Fleet here', v.state ?? v.status ?? '', v.id ? 'claude attach ' + v.id : ''].filter(Boolean).join(' \u00B7 ')}</Text>
                  </Text>
                </Box>
                {stoppable ? tips(E, [stopChipFor(stopId, 'o')]) : null}
              </Box>
              {stoppable && armedFor(stopId) ? <Text color="warning" wrap="truncate-end">{'    ' + armedText(stopId, pinnedOf(stopId))}</Text> : null}
            </Box>
          )
        })}
        {others.map(x => {
          const status = x.state === 'working' ? 'running' : x.state === 'waiting' ? 'waiting' : 'idle'
          const open = (x.attention ?? []).filter(a => !seenKeys.has(a.key)).length
          const n = x.agents?.length ?? 0
          return (
            <Box key={'card-' + x.sessionId} flexDirection="column">
              <Button key={'cardb-' + x.sessionId} plain onPress={() => update($, fleetPick, v => (v === x.sessionId ? '' : x.sessionId))}>
                <Text color={colorFor(status, light)}>{GLYPH[status]}</Text>
                {' ' + x.project + (fp === x.sessionId ? ' ▾' : '')}
                {open > 0 && <Text color="warning">{'  ' + open + ' waiting on you'}</Text>}
                <Text dimColor>
                  {'  ' + [x.git?.branch, x.state, n ? n + ' agent' + (n === 1 ? '' : 's') : '', x.ctx !== undefined ? 'ctx ' + x.ctx + '%' : '', money(x.usd)].filter(Boolean).join(' · ')}
                </Text>
              </Button>
              {fp === x.sessionId && (
                <Box flexDirection="column">
                  <Text dimColor wrap="truncate-end">{'    ' + x.cwd + ' · seen ' + ago(now - x.at) + ' ago'}</Text>
                  {x.ask ? <Text wrap="truncate-end">{'    You asked: ' + x.ask}</Text> : null}
                  {(x.agents ?? []).map(g => (
                    <Text key={'ag-' + x.sessionId + '-' + g.id} wrap="truncate-end">
                      {'    '}
                      <Text color={colorFor(g.status, light)}>{GLYPH[g.status]}</Text>
                      {' ' + g.name}
                      <Text dimColor>{'  ' + (g.status === 'running' && g.doing ? g.doing : WORD[g.status])}</Text>
                    </Text>
                  ))}
                  {n === 0 && <Text dimColor>    Main thread only.</Text>}
                  <Box flexDirection="row" columnGap={2}>
                    <Text> </Text>
                    <Button
                      key={'cardcopy-' + x.sessionId}
                      plain
                      dimColor
                      label={'⧉ Copy open command'}
                      onPress={press => void $.ui.copy({ text: resumeCommand(x), surface: press.surface }).then(() => $.ui.toast('Copied: paste it in a new terminal tab'))}
                    />
                  </Box>
                </Box>
              )}
            </Box>
          )
        })}
        <Text> </Text>
        <Text dimColor>{'▸ opens it here, ◎ rings the tab of another session. Press a row for the full text and where it is.'}</Text>
      </Box>
    )
  })
}
