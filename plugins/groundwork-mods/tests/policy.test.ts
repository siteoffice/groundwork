import { describe, expect, mock, test } from 'claude-code/testing'

import { decide, mentions, receiptId, words } from '../hooks/policy/policy'
import type { Policy } from '../hooks/policy/policy'
import { shipped } from './default-policy'

const policy = shipped as unknown as Policy
const ROOT = 'C:/work/shop'
const NOW = '2026-10-21T10:00:00.000Z'
const ctx = { root: ROOT, approvals: [], nowIso: NOW }

describe('the rules', () => {
  test('read-only by default: a write outside drafts is denied, a draft is allowed', async () => {
    expect(decide(policy, { tool: 'Write', input: { file_path: 'src/app.ts' } }, ctx).action).toBe('deny')
    const draft = decide(policy, { tool: 'Write', input: { file_path: 'drafts/post.md' } }, ctx)
    expect(draft.action).toBe('allow-write')
    expect(decide(policy, { tool: 'Read', input: { file_path: 'src/app.ts' } }, ctx).action).toBe('allow')
  })

  test('the agent can never write into the policy folder, in either mode', async () => {
    const work = { ...policy, mode: 'work' as const }
    for (const p of [policy, work]) {
      const d = decide(p, { tool: 'Edit', input: { file_path: '.groundwork/approvals.json' } }, ctx)
      expect(d.action === 'deny' && d.kind).toBe('protected')
    }
    const sneaky = decide(policy, { tool: 'Write', input: { file_path: 'drafts/../.groundwork/agent-policy.json' } }, ctx)
    expect(sneaky.action).toBe('deny')
  })

  test('sends are blocked in shell and MCP tools; a draft tool is allowed', async () => {
    const send = decide(policy, { tool: 'Bash', input: { command: 'git push origin main' } }, ctx)
    expect(send.action === 'deny' && send.kind).toBe('send')
    expect(decide(policy, { tool: 'mcp__slack__slack_send_message', input: {} }, ctx).action).toBe('deny')
    expect(decide(policy, { tool: 'mcp__slack__slack_send_message_draft', input: {} }, ctx).action).toBe('allow-write')
    expect(decide(policy, { tool: 'mcp__github__get_issue', input: {} }, ctx).action).toBe('allow')
    expect(decide(policy, { tool: 'mcp__crm__create_contact', input: {} }, ctx).action).toBe('deny')
  })

  test('read-only shell: reads pass, writes and redirects do not', async () => {
    const run = (command: string) => decide(policy, { tool: 'Bash', input: { command } }, ctx).action
    expect(run('git status')).toBe('allow')
    expect(run('git log --oneline | head -5')).toBe('allow')
    expect(run('echo hi > notes.txt')).toBe('deny')
    expect(run('git commit -m x')).toBe('deny')
    expect(run('ls && rm -rf build')).toBe('deny')
    expect(run('echo $(whoami)')).toBe('deny')
  })

  test('a delete needs an approver named in the policy and an unexpired approval for that target', async () => {
    const work = { ...policy, mode: 'work' as const, delete: { ...policy.delete, approvers: ['Sam'] } }
    const call = { tool: 'Bash', input: { command: 'rm -rf build/old-output' } }
    const none = decide(work, call, ctx)
    expect(none.action === 'deny' && none.kind).toBe('delete')
    const ok = [{ person: 'sam', target: 'build', until: '2026-12-31T00:00:00Z' }]
    expect(decide(work, call, { ...ctx, approvals: ok }).action).toBe('allow-write')
    const expired = [{ person: 'Sam', target: 'build', until: '2026-01-01T00:00:00Z' }]
    expect(decide(work, call, { ...ctx, approvals: expired }).action).toBe('deny')
    const stranger = [{ person: 'Kim', target: 'build', until: '2026-12-31T00:00:00Z' }]
    expect(decide(work, call, { ...ctx, approvals: stranger }).action).toBe('deny')
    const elsewhere = [{ person: 'Sam', target: 'dist', until: '2026-12-31T00:00:00Z' }]
    expect(decide(work, call, { ...ctx, approvals: elsewhere }).action).toBe('deny')
    expect(decide({ ...work, delete: { ...work.delete, approvers: [] } }, call, { ...ctx, approvals: ok }).action).toBe('deny')
  })

  test('helpers: whole-word matching, tool words, receipt ids', async () => {
    expect(mentions('echo confirm everything', 'rm')).toBe(false)
    expect(mentions('cd x && rm -rf y', 'rm')).toBe(true)
    expect(words('mcp__slack__slack_send_message_draft')).toEqual(['slack', 'send', 'message', 'draft'])
    expect(receiptId(NOW, 3)).toBe('gw-202610211000' + '00-3')
  })
})

// The hooks, end to end: the engine below them is a small in-memory file system.
function stage($: any, on: any, files: Map<string, string>, core: { n: number }) {
  const fwd = (p: string) => p.split('\\').join('/')
  mock.clock(on)
  on('session.cwd', () => ({ value: ROOT }))
  on('fs.exists', (_: unknown, e: { path: string }) => ({ value: files.has(fwd(e.path)) || fwd(e.path).endsWith('/policy/agent-policy.json') }))
  on('fs.read', (_: unknown, e: { path: string }) => ({ value: fwd(e.path).endsWith('/policy/agent-policy.json') ? JSON.stringify(shipped) : files.get(fwd(e.path)) }))
  on('fs.write', (_: unknown, e: { path: string; text: string }) => (files.set(fwd(e.path), e.text), { value: undefined }))
  on('tool.call', () => ((core.n += 1), { result: {} }))
}

const logOf = (files: Map<string, string>) =>
  (files.get(ROOT + '/.groundwork/agent-log.ndjson') ?? '').split('\n').filter(Boolean).map(l => JSON.parse(l))

describe('the hooks', () => {
  test('a send is blocked and logged; the tool never runs', async ($, on) => {
    const files = new Map<string, string>()
    const core = { n: 0 }
    stage($, on, files, core)
    const out = await $.tool.call({ tool: 'Bash', command: 'git push origin main' })
    expect(out.deny).toContain('sends are blocked')
    expect(core.n).toBe(0)
    expect(logOf(files)[0]).toMatchObject({ decision: 'denied', kind: 'send' })
  })

  test('a draft is allowed and logged with a receipt and an undo note', async ($, on) => {
    const files = new Map<string, string>()
    const core = { n: 0 }
    stage($, on, files, core)
    const out = await $.tool.call({ tool: 'Write', file_path: 'drafts/post.md', content: 'hello' })
    expect(out.deny).toBeUndefined()
    expect(core.n).toBe(1)
    const line = logOf(files)[0]
    expect(line).toMatchObject({ decision: 'written', kind: 'draft', target: 'drafts/post.md' })
    expect(line.receipt).toMatch(/^gw-/)
    expect(line.undo).toContain('delete the new file drafts/post.md')
  })

  test('an overwritten file is kept, and the undo note names the copy', async ($, on) => {
    const files = new Map<string, string>([
      [ROOT + '/drafts/post.md', 'the old text'],
    ])
    const core = { n: 0 }
    stage($, on, files, core)
    await $.tool.call({ tool: 'Write', file_path: 'drafts/post.md', content: 'new' })
    const line = logOf(files)[0]
    expect(files.get(ROOT + '/.groundwork/undo/' + line.receipt + '.bak')).toBe('the old text')
    expect(line.undo).toContain(line.receipt + '.bak over drafts/post.md')
  })

  test('a write outside drafts is denied in read-only mode and allowed in work mode', async ($, on) => {
    const files = new Map<string, string>()
    const core = { n: 0 }
    stage($, on, files, core)
    const denied = await $.tool.call({ tool: 'Write', file_path: 'src/app.ts', content: 'x' })
    expect(denied.deny).toContain('read-only by default')
    files.set(ROOT + '/.groundwork/agent-policy.json', JSON.stringify({ mode: 'work' }))
    const allowed = await $.tool.call({ tool: 'Write', file_path: 'src/app.ts', content: 'x' })
    expect(allowed.deny).toBeUndefined()
    expect(logOf(files).map(l => l.decision)).toEqual(['denied', 'written'])
  })

  test('the agent cannot edit the approvals file', async ($, on) => {
    const files = new Map<string, string>()
    stage($, on, files, { n: 0 })
    files.set(ROOT + '/.groundwork/agent-policy.json', JSON.stringify({ mode: 'work' }))
    const out = await $.tool.call({ tool: 'Write', file_path: '.groundwork/approvals.json', content: '[]' })
    expect(out.deny).toContain('only a person changes it')
  })
})
