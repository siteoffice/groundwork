/** What the Setup Doctor found on this computer, gathered once per check. */
export type DoctorFacts = {
  isWindows: boolean
  version: string
  isFullscreen?: boolean
  git: { ok: boolean; name: string; email: string }
  gh: { installed: boolean; authed: boolean }
  python: string
  repo: { inside: boolean; root: string }
  manifest: { found: boolean; valid: boolean; checks: number; engineOk: boolean; syncDirReason?: '' | 'inside' | 'invalid-form' | 'unresolved' }
  plugin: boolean
  /** Which SessionStart events reached these mods in this session. */
  hooks: { sessionStart: boolean; classicSessionStart: boolean }
  notif: string
  needsBell: boolean
  statusLine: boolean
  /** BurnMon's snapshot: file there, and its age from written_at (null when it cannot be read). */
  burnmon: { exists: boolean; ageSeconds: number | null }
}

export type FleetActivity = { tool: string; phrase: string; since: number; calls: number }
export type FleetStatus = 'pending' | 'running' | 'waiting' | 'idle' | 'completed' | 'failed' | 'killed'
export type FleetAgent = {
  id: string
  name: string
  type: string
  status: FleetStatus
  parentId?: string
  description: string
  spawnedBy?: string
}
export type FleetWire = { from: string; to: string; text: string; at: number; isDelivered: boolean }
export type FleetLimit = { label: string; pct: number; resetsAt?: string }
export type FleetUsage = { ctx?: number; usd?: number; startedAt?: number; limits: FleetLimit[] }
/** One thread: an agent with a task, usually in a git worktree of its own. */
export type FleetThread = {
  id: string
  name: string
  task: string
  status: FleetStatus
  startedAt: number
  isOurs: boolean
  /** The worktree's root, once a file the agent touched shows where it works. */
  root?: string
  /** Where the worktree forked from the session's repo: the diff's left side. */
  base?: string
  branch?: string
  answer: string
  workedMs: number
  turns: number
  /** Last result of each kind of check the agent ran: tests, typecheck, lint, build. */
  proofs: Record<string, 'ok' | 'fail'>
  stat?: { files: number; add: number; del: number; untracked: number }
  /** The folder's own git config names programs git would run, so no diff is read there. */
  untrusted?: boolean
  pr?: { number: number; state: string; checks: { pass: number; fail: number; pending: number } }
  review?: { state: 'running' | 'ok' | 'issues'; text: string; agentId: string }
  settled?: boolean
}
/** One background shell of this session. `isSure`: its end came from its own notification, not from a turn-end list. */
export type FleetShell = {
  id: string
  command: string
  state: 'running' | 'completed' | 'failed' | 'killed'
  startedAt: number
  endedAt?: number
  isSure?: boolean
}
/** Plain Mode's model tokens for one local day, kept in the store. */
export type FleetPlainSpend = { day: string; tokens: number }
export type FleetDiffFile ={ path: string; hunks: string; add: number; del: number; isBinary: boolean }
export type FleetDiff = { id: string; files: FleetDiffFile[]; more: number; untracked: string[]; at: number }

/** Something in one session that needs the person: a question, a permission, an agent that finished or failed. */
export type FleetAttention = {
  key: string
  sessionId: string
  project: string
  /** The agent's id, or '' for the session's main thread. */
  agentId: string
  agent: string
  kind: 'question' | 'permission' | 'done' | 'failed'
  text: string
  at: number
  /** Set for a session found only through `claude agents`: no Fleet runs there to ring. */
  via?: 'agents'
  /** For a background session: the command that opens it. */
  attach?: string
  /** For a background session: its id, which the stop button needs. */
  stopId?: string
}
export type FleetCardAgent = { id: string; name: string; status: FleetStatus; doing: string }

/** One row of `claude agents --json`: a live session or a background one. */
export type FleetAgentView = {
  sessionId?: string
  id?: string
  name?: string
  cwd: string
  kind: 'interactive' | 'background' | string
  state?: string
  status?: string
  waitingFor?: string
  startedAt?: number
  /** A pinned background session: the stop button says so. */
  pinned?: boolean
}

/** One session's card in the fleet folder, written by that session alone. */
export type FleetCard = {
  v: 1
  sessionId: string
  project: string
  cwd: string
  state: 'working' | 'waiting' | 'idle'
  running: number
  questions: number
  threads: { needsYou: number; working: number }
  ctx?: number
  usd?: number
  ask: string
  answer: string
  git?: { branch: string; changed: number; unshared: number; pr?: number; prChecks?: string }
  agents?: FleetCardAgent[]
  attention?: FleetAttention[]
  at: number
  ended?: boolean
}

export type GitLaneFile = { path: string; kind: 'new' | 'changed' | 'deleted' | 'renamed' | 'conflict'; added: number; removed: number }
export type GitLaneChecks = { pass: number; fail: number; pending: number }
export type GitLanePr = {
  number: number
  title: string
  url: string
  state: 'OPEN' | 'MERGED' | 'CLOSED'
  isDraft: boolean
  review: 'APPROVED' | 'CHANGES_REQUESTED' | 'REVIEW_REQUIRED' | ''
  checks: GitLaneChecks
  mergeable: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN'
}
export type GitLaneCommit = { hash: string; subject: string; when: string }
export type GitLaneRepo = {
  isRepo: boolean
  /** git: plain git; siteoffice: every write goes through the siteoffice-git engine; siteoffice-missing: a Siteoffice repo whose engine was not found, so the lane only shows */
  mode: 'git' | 'siteoffice' | 'siteoffice-missing'
  root: string
  engine: string
  head: string
  checked: boolean
  project: string
  branch: string
  upstream: string
  ahead: number
  behind: number
  unshared: number
  defaultBranch: string
  files: GitLaneFile[]
  commits: GitLaneCommit[]
  hasGh: boolean
  pr: GitLanePr | null
  /** the folder's own git config runs programs, so the lane runs no git there */
  untrusted?: boolean
  /** the setting that closed the lane, as git printed it (shown cleaned and cut) */
  untrustedWhy?: string
}
export type GitLanePending = { kind: string; label: string; argvs: string[][]; warn: string; preview: string; outcome: 'pass' | 'refused' | 'blocked' | '' }
export type GitLaneCheck = {
  cmd: string
  state: 'never' | 'running' | 'ok' | 'fail'
  ms: number
  at: number
  head: string
  tail: string[]
  history: { ok: boolean; head: string }[]
}
export type GitLaneBench = { root: string; source: string; checks: GitLaneCheck[] }
export type GitLaneResult = { ok: boolean; text: string; at: number }
/** PR Desk: one open pull request of the repo. */
export type GitLaneDeskPr = {
  number: number
  title: string
  author: string
  branch: string
  url: string
  isDraft: boolean
  review: 'APPROVED' | 'CHANGES_REQUESTED' | 'REVIEW_REQUIRED' | ''
  mergeable: 'MERGEABLE' | 'CONFLICTING' | 'UNKNOWN'
  checks: GitLaneChecks
  /** Failing checks by name, with the job's link (for its log). */
  failing: { name: string; url: string }[]
  /** Review threads not yet resolved; -1 when not known. */
  threads: number
  group: 'review' | 'mine' | 'agent' | 'other'
  ready: boolean
  updatedAt: string
}
export type GitLaneDesk = { me: string; prs: GitLaneDeskPr[]; at: number; error: string }
/** What the PR Desk fetched for one opened pull request. */
export type GitLaneDeskDetail = {
  number: number
  isLoading: boolean
  logs: { name: string; tail: string[] }[]
  threads: { path: string; author: string; text: string }[]
}
/** Blast Radius: a risky call the guard held for the person. */
export type GitLaneHeld = { key: string; kind: string; what: string; undo: string; at: number }

declare module 'claude-code' {
  interface PluginState {
    'groundwork-mods': {
      facts: DoctorFacts | null
      isChecking: boolean
      isLight: boolean
      isFullscreen: boolean | null
      checkedAt: number
      sawSessionStart: boolean
      sawClassicSessionStart: boolean
      'fleet.sessionKey': string
      'fleet.sessionId': string
      'fleet.project': string
      'fleet.isLight': boolean
      'fleet.isBusy': boolean
      'fleet.questions': number
      'fleet.tab': 'agents' | 'threads' | 'fleet'
      'fleet.agents': FleetAgent[]
      'fleet.activity': Record<string, FleetActivity>
      'fleet.wire': FleetWire[]
      'fleet.usage': FleetUsage
      'fleet.burnOn': boolean
      'fleet.burnmon': DoctorFacts['burnmon']
      'fleet.plainOn': boolean
      'fleet.bbOn': boolean
      'fleet.bbQueue': string[]
      'fleet.lastStatus': Record<string, string>
      'fleet.soWhat': string
      'fleet.ctxHistory': number[]
      'fleet.threads': Record<string, FleetThread>
      'fleet.reviewers': Record<string, string>
      'fleet.pick': string
      'fleet.diff': FleetDiff | null
      'fleet.showSettled': boolean
      'fleet.repoRoot': string
      'fleet.fleet': FleetCard[]
      'fleet.fleetPick': string
      'fleet.lastAsk': string
      'fleet.lastAnswer': string
      'fleet.cardSent': string
      'fleet.asks': Record<string, { text: string; at: number }>
      'fleet.perms': Record<string, { text: string; at: number }>
      'fleet.answers': Record<string, string>
      'fleet.doneAt': Record<string, number>
      'fleet.lastAnswerAt': number
      'fleet.fleetSeen': string[]
      'fleet.fleetItem': string
      'fleet.plainQs': Record<string, string[][]>
      'fleet.agentView': FleetAgentView[]
      'fleet.shown': string[]
      'fleet.shells': FleetShell[]
      'fleet.plainSpend': FleetPlainSpend
      'fleet.plainCap': number
      'fleet.stopArm': { id: string; at: number }
      'git.repo': GitLaneRepo
      'git.pending': GitLanePending | null
      'git.composeKind': '' | 'commit' | 'branch'
      'git.message': string
      'git.result': GitLaneResult | null
      'git.isBusy': boolean
      'git.isLight': boolean
      'git.bandOn': boolean
      'git.protectMain': boolean
      'git.guardOn': boolean
      'git.isDirty': boolean
      'git.isLoaded': boolean
      'git.bench': GitLaneBench | null
      'git.desk': GitLaneDesk | null
      'git.deskOpen': boolean
      'git.deskPick': number
      'git.deskDetail': GitLaneDeskDetail | null
      'git.held': GitLaneHeld | null
      'git.allowed': Record<string, number>
      'git.folders': Record<string, string[]>
      'git.turnFiles': string[]
    }
  }
}
