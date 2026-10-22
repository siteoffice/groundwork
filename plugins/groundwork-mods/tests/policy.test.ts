import { describe, expect, mock, test } from 'claude-code/testing'

import { decide, mentions, parents, receiptId, words } from '../hooks/policy/policy'
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

  test('harmless read commands pass, including cd and wc', async () => {
    const run = (command: string) => decide(policy, { tool: 'Bash', input: { command } }, ctx).action
    expect(run('cd src && ls')).toBe('allow')
    expect(run('wc -l README.md')).toBe('allow')
    expect(run('git diff --stat | wc -l')).toBe('allow')
    expect(run('Set-Location C:/work/shop; Get-ChildItem | Measure-Object')).toBe('allow')
    expect(run('Get-ChildItem | Where-Object Name -like *.md')).toBe('allow')
    expect(run('git stash list')).toBe('allow')
    expect(run('git branch')).toBe('allow')
    expect(run('git branch --list')).toBe('allow')
    expect(run('git branch -a | wc -l')).toBe('allow')
    expect(run('git diff 2>&1 | wc -l')).toBe('allow')
    expect(run('git remote -v')).toBe('allow')
    expect(run('python --version')).toBe('allow')
    expect(run('cd src && git status && git log --oneline')).toBe('allow')
  })

  test('commands that can write or run something stay denied in read-only mode', async () => {
    const run = (command: string) => decide(policy, { tool: 'Bash', input: { command } }, ctx).action
    for (const bad of [
      'sed -i s/a/b/ notes.txt',
      'find . -delete',
      'env FOO=1 python x.py',
      'sort -o out.txt in.txt',
      'uniq in.txt out.txt',
      'date -s 2020-01-01',
      'hostname other',
      'git ls-remote --upload-pack=x origin',
      'git branch newname',
      'git diff --output=out.txt',
      'git log --ext-diff',
      'rg --pre cat x .',
      'Get-ChildItem | Where-Object { Remove-Item $_ }',
      'Get-ChildItem | Select-Object @{n="a";e={1}}',
      'Get-ChildItem | Format-List @(1)',
      'cd src && echo hi > notes.txt',
      'cd src && git commit -m x',
      'ls & touch x',
      'ls &> out.txt',
      'Get-Content (New-Item x)',
      'cat <(touch x)',
      'git branch -a -D x',
      'git branch -a -m old new',
      'git branch -v newname',
      'git remote -v add x https://example.com/x.git',
      'python --version -c print(1)',
      'rg --hostname-bin x y .',
      'rg --search-zip x .',
    ]) {
      expect([bad, run(bad)]).toEqual([bad, 'deny'])
    }
  })

  test('the project root is found by looking upward for the .groundwork folder', async () => {
    expect(parents('C:\\work\\shop\\src\\deep')).toEqual(['C:/work/shop/src/deep', 'C:/work/shop/src', 'C:/work/shop', 'C:/work'])
    expect(parents('/home/a/shop')).toEqual(['/home/a/shop', '/home/a', '/home'])
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
function stage($: any, on: any, files: Map<string, string>, core: { n: number }, cwd: string = ROOT) {
  const fwd = (p: string) => p.split('\\').join('/')
  const isDir = (p: string) => [...files.keys()].some(k => k.startsWith(p + '/'))
  mock.clock(on)
  on('session.cwd', () => ({ value: cwd }))
  on('fs.exists', (_: unknown, e: { path: string }) => ({ value: files.has(fwd(e.path)) || isDir(fwd(e.path)) || fwd(e.path).endsWith('/policy/agent-policy.json') }))
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

  test('a cd into a subfolder keeps the project mode, and the log stays in the project root', async ($, on) => {
    const files = new Map<string, string>([[ROOT + '/.groundwork/agent-policy.json', JSON.stringify({ mode: 'work' })]])
    stage($, on, files, { n: 0 }, ROOT + '/src/deep')
    const out = await $.tool.call({ tool: 'Write', file_path: ROOT + '/src/app.ts', content: 'x' })
    expect(out.deny).toBeUndefined()
    expect(logOf(files)[0]).toMatchObject({ decision: 'written', kind: 'write' })
    expect(files.has(ROOT + '/src/deep/.groundwork/agent-log.ndjson')).toBe(false)
  })

  test('a folder with only a log in it does not shadow the project that holds the policy file', async ($, on) => {
    const files = new Map<string, string>([
      [ROOT + '/.groundwork/agent-policy.json', JSON.stringify({ mode: 'work' })],
      [ROOT + '/src/.groundwork/agent-log.ndjson', ''],
    ])
    stage($, on, files, { n: 0 }, ROOT + '/src')
    const out = await $.tool.call({ tool: 'Write', file_path: ROOT + '/src/app.ts', content: 'x' })
    expect(out.deny).toBeUndefined()
    expect(files.has(ROOT + '/.groundwork/agent-log.ndjson')).toBe(true)
  })

  test('the agent cannot edit the approvals file', async ($, on) => {
    const files = new Map<string, string>()
    stage($, on, files, { n: 0 })
    files.set(ROOT + '/.groundwork/agent-policy.json', JSON.stringify({ mode: 'work' }))
    const out = await $.tool.call({ tool: 'Write', file_path: '.groundwork/approvals.json', content: '[]' })
    expect(out.deny).toContain('only a person changes it')
  })
})
