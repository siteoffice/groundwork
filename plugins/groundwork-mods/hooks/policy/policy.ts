// The agent policy: pure decisions from a tool call and the policy file. No I/O here, so the
// tests can check every rule. The hooks in register.ts read the files and call decide().

export type Policy = {
  version: number
  mode: 'read-only' | 'work'
  draftFolders: string[]
  neverWrite: string[]
  send: { commands: string[]; toolWords: string[] }
  draftWords: string[]
  delete: { commands: string[]; toolWords: string[]; approvers: string[]; approvalFile: string }
  readOnlyCommands: string[]
  readWords: string[]
  log: string
  undoFolder: string
}

export type Approval = { person: string; target: string; until: string }

export type Call = { tool: string; input: Record<string, unknown> }

export type Decision =
  | { action: 'allow'; why: string }
  | { action: 'deny'; why: string; kind: 'read-only' | 'send' | 'delete' | 'protected' }
  | { action: 'allow-write'; why: string; target: string; kind: 'draft' | 'write' | 'shell' | 'tool'; backup: boolean }

export const WRITE_TOOLS = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit']
export const SHELL_TOOLS = ['Bash', 'PowerShell']

// Normalises a path to forward slashes, lower case and no dot segments, against root.
export function norm(root: string, p: string): string {
  const slashed = p.replace(/\\/g, '/')
  const abs = /^([a-zA-Z]:)?\//.test(slashed) ? slashed : root.replace(/\\/g, '/').replace(/\/+$/, '') + '/' + slashed
  const out: string[] = []
  for (const part of abs.split('/')) {
    if (part === '..') out.pop()
    else if (part !== '.' && part !== '') out.push(part)
  }
  const head = abs.startsWith('/') && !/^[a-zA-Z]:/.test(out[0] ?? '') ? '/' : ''
  return (head + out.join('/')).toLowerCase()
}

const inside = (target: string, folder: string) => target === folder || target.startsWith(folder + '/')

// Splits a tool name such as mcp__slack__send_message_draft into lower-case words.
export function words(name: string): string[] {
  const tail = name.startsWith('mcp__') ? name.split('__').slice(2).join('_') : name
  return tail
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
}

const has = (list: string[], ws: string[]) => ws.some(w => list.includes(w))

const lower = (s: string) => s.toLowerCase()

// True when the command line (any case) contains `c` as whole words, so "rm" does not match "confirm".
export function mentions(line: string, c: string): boolean {
  const esc = c.trim().toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp('(^|[\\s;&|(])' + esc + '(?=$|[\\s;&|)])').test(line.toLowerCase())
}

// Each segment of a shell line, split on the usual joiners.
const segments = (cmd: string) => cmd.split(/&&|\|\||;|\||\r?\n/).map(s => s.trim()).filter(Boolean)

function startsWithCommand(seg: string, cmds: string[]): boolean {
  const s = lower(seg)
  return cmds.some(c => s === c || s.startsWith(c + ' ') || s.startsWith(c + '\t'))
}

const writesToFile = (seg: string) => /(^|[^2&])>(?!&)/.test(seg.replace(/>\s*(\/dev\/null|\$null|nul)\b/gi, ''))

// The targets a shell delete names, best effort: words after the command that are not flags.
export function deleteTargets(cmd: string, deleteCommands: string[]): string[] {
  const out: string[] = []
  for (const seg of segments(cmd)) {
    const s = lower(seg)
    if (!deleteCommands.some(c => mentions(s, c))) continue
    const toks = seg.split(/\s+/).slice(1).filter(t => t && !/^[-/]/.test(t))
    for (const t of toks) out.push(t.replace(/^['"]|['"]$/g, ''))
  }
  return out
}

export function approved(policy: Policy, approvals: Approval[], targets: string[], root: string, nowIso: string): string | null {
  const names = policy.delete.approvers.map(lower)
  if (!names.length) return 'no approver is named in the policy'
  if (!targets.length) return 'the delete names no target the policy can check'
  for (const t of targets) {
    const nt = norm(root, t)
    const ok = approvals.some(a => names.includes(lower(a.person)) && a.until > nowIso && inside(nt, norm(root, a.target)))
    if (!ok) return 'no unexpired approval from a named person covers ' + t
  }
  return null
}

export function decide(policy: Policy, call: Call, ctx: { root: string; approvals: Approval[]; nowIso: string }): Decision {
  const { tool, input } = call
  const readOnly = policy.mode === 'read-only'

  if (WRITE_TOOLS.includes(tool)) {
    const raw = String(input.file_path ?? input.notebook_path ?? input.path ?? '')
    const target = norm(ctx.root, raw)
    if (policy.neverWrite.some(f => inside(target, norm(ctx.root, f)))) {
      return { action: 'deny', kind: 'protected', why: raw + ' is part of the policy itself; only a person changes it' }
    }
    if (policy.draftFolders.some(f => inside(target, norm(ctx.root, f)))) {
      return { action: 'allow-write', kind: 'draft', target: raw, backup: true, why: 'a draft' }
    }
    if (readOnly) {
      return { action: 'deny', kind: 'read-only', why: 'read-only by default. Write a draft into ' + policy.draftFolders.join(' or ') + ', or ask the person to set the mode to work' }
    }
    return { action: 'allow-write', kind: 'write', target: raw, backup: true, why: 'work mode' }
  }

  if (SHELL_TOOLS.includes(tool)) {
    const cmd = String(input.command ?? '')
    const l = lower(cmd)
    if (policy.send.commands.some(c => mentions(l, c))) {
      return { action: 'deny', kind: 'send', why: 'sends are blocked. Write the message or change as a draft; a person sends it' }
    }
    if (policy.delete.commands.some(c => mentions(l, c))) {
      const why = approved(policy, ctx.approvals, deleteTargets(cmd, policy.delete.commands), ctx.root, ctx.nowIso)
      if (why) return { action: 'deny', kind: 'delete', why: 'a delete needs a named person. ' + why }
      return { action: 'allow-write', kind: 'shell', target: cmd, backup: false, why: 'an approved delete' }
    }
    if (readOnly) {
      const segs = segments(cmd)
      const readable = segs.length > 0 && segs.every(s => startsWithCommand(s, policy.readOnlyCommands) && !writesToFile(s)) && !/`|\$\(/.test(cmd)
      if (!readable) return { action: 'deny', kind: 'read-only', why: 'read-only by default: this command is not on the read-only list' }
      return { action: 'allow', why: 'a read-only command' }
    }
    return { action: 'allow-write', kind: 'shell', target: cmd, backup: false, why: 'work mode' }
  }

  if (tool.startsWith('mcp__')) {
    const ws = words(tool)
    if (has(ws, policy.draftWords)) return { action: 'allow-write', kind: 'tool', target: tool, backup: false, why: 'a draft tool' }
    if (has(ws, policy.send.toolWords)) return { action: 'deny', kind: 'send', why: tool + ' sends; sends are blocked. Make a draft instead' }
    if (has(ws, policy.delete.toolWords)) {
      const why = approved(policy, ctx.approvals, [tool], ctx.root, ctx.nowIso)
      if (why) return { action: 'deny', kind: 'delete', why: 'a delete needs a named person. ' + why }
      return { action: 'allow-write', kind: 'tool', target: tool, backup: false, why: 'an approved delete' }
    }
    if (has(ws, policy.readWords)) return { action: 'allow', why: 'a read tool' }
    if (readOnly) return { action: 'deny', kind: 'read-only', why: tool + ' may change something; read-only by default' }
    return { action: 'allow-write', kind: 'tool', target: tool, backup: false, why: 'work mode' }
  }

  return { action: 'allow', why: 'not a write, shell or MCP tool' }
}

export function receiptId(nowIso: string, n: number): string {
  return 'gw-' + nowIso.replace(/[-:]/g, '').replace(/\.\d+Z?$/, '').replace('T', '') + '-' + n
}

export function undoNote(d: Extract<Decision, { action: 'allow-write' }>, receipt: string, undoFolder: string, existed: boolean): string {
  if (d.kind === 'shell') return 'A command ran. There is no automatic undo: read the log line, then use git status and git diff to see what changed.'
  if (d.kind === 'tool') return 'A tool changed something outside the project. Undo it in that tool; the log line has the call.'
  return existed
    ? 'To undo, copy ' + undoFolder + '/' + receipt + '.bak over ' + d.target + '.'
    : 'To undo, delete the new file ' + d.target + '.'
}
