import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { engineFor, isInsideAny, parseWorktrees, resolvePath } from '../hooks/engine'
import type { EngineIO } from '../hooks/engine'
import { POLICY_RX, policyText } from './host'

describe('engine rule', () => {
  const ROOT = '/work/siteoffice'
  // realPath mimics the host: a mapped path answers its target, an unresolved path answers
  // nothing, a missing engine file answers nothing, anything else lands where it is spelled.
  function io(over: { env?: string; files?: string[]; worktrees?: string[]; bundled?: string; real?: Record<string, string>; unresolved?: string[]; calls?: string[] }): EngineIO {
    const key = (p: string) => resolvePath(p).toLowerCase()
    const files = (over.files ?? []).map(key)
    const real = Object.fromEntries(Object.entries(over.real ?? {}).map(([k, v]) => [key(k), v]))
    return {
      env: async n => (n === 'SITEOFFICE_SYNC_DIR' ? over.env : undefined),
      exists: async p => {
        over.calls?.push('exists ' + p)
        return files.includes(key(p))
      },
      realPath: async p => {
        over.calls?.push('realPath ' + p)
        if ((over.unresolved ?? []).map(key).includes(key(p))) return undefined
        if (key(p) in real) return real[key(p)]
        if (key(p).endsWith('.py') && !files.includes(key(p))) return undefined
        return p
      },
      worktrees: async () => over.worktrees ?? [],
      bundled: async () => over.bundled ?? '',
    }
  }
  const B = '/plug/groundwork/engine/sync/git_flow.py'

  test('paths resolve and compare the same way however they are spelled', async () => {
    expect(resolvePath('C:\\Work\\Shop\\.\\x\\..\\evil\\')).toBe('C:/Work/Shop/evil')
    expect(isInsideAny('c:/work/shop/evil/git_flow.py', ['C:\\Work\\Shop'])).toBe(true)
    expect(isInsideAny('/work/siteoffice-evil/x', [ROOT])).toBe(false)
    expect(isInsideAny(ROOT, [ROOT])).toBe(true)
    expect(isInsideAny('/x', ['', ROOT])).toBe(false)
  })

  test('worktree paths come from git worktree list', async () => {
    expect(parseWorktrees('worktree /work/siteoffice\nHEAD abc\nbranch refs/heads/main\n\nworktree C:/w/wt-a\nHEAD def\n')).toEqual(['/work/siteoffice', 'C:/w/wt-a'])
  })

  test('SITEOFFICE_SYNC_DIR inside the repo or a worktree is ignored, outside honoured, unset falls to the bundled engine', async () => {
    expect(await engineFor(io({ bundled: B }), ROOT)).toEqual({ engine: B, ignoredSyncDir: '', ignoredReason: '' })
    expect(await engineFor(io({}), ROOT)).toEqual({ engine: '', ignoredSyncDir: '', ignoredReason: '' })
    expect(await engineFor(io({ env: '/own', files: ['/own/git_flow.py'], bundled: B }), ROOT)).toEqual({ engine: '/own/git_flow.py', ignoredSyncDir: '', ignoredReason: '' })
    expect(await engineFor(io({ env: '/work/siteoffice/evil', files: ['/work/siteoffice/evil/git_flow.py'], bundled: B }), ROOT)).toEqual({ engine: B, ignoredSyncDir: '/work/siteoffice/evil', ignoredReason: 'inside' })
    expect((await engineFor(io({ env: '/work/other/../siteoffice/evil', files: ['/work/siteoffice/evil/git_flow.py'], bundled: B }), ROOT)).ignoredSyncDir).not.toBe('')
    expect((await engineFor(io({ env: 'evil', files: ['/work/siteoffice/evil/git_flow.py'], bundled: B }), ROOT)).engine).toBe(B)
    expect(await engineFor(io({ env: '/work/wt-a/evil', files: ['/work/wt-a/evil/git_flow.py'], worktrees: ['/work/wt-a'], bundled: B }), ROOT)).toEqual({ engine: B, ignoredSyncDir: '/work/wt-a/evil', ignoredReason: 'inside' })
    expect((await engineFor(io({ env: '/work/siteoffice/.claude/worktrees/agent-1/evil.py', files: ['/work/siteoffice/.claude/worktrees/agent-1/evil.py'], bundled: B }), ROOT)).engine).toBe(B)
    expect((await engineFor(io({ env: 'C:\\Work\\Shop\\evil', files: ['C:/Work/Shop/evil/git_flow.py'], bundled: B }), 'c:/work/shop')).engine).toBe(B)
  })

  test('a link outside the repo that resolves inside the repo or a worktree is ignored', async () => {
    const file = io({ env: '/link/eng', real: { '/link/eng/git_flow.py': '/work/siteoffice/evil/git_flow.py' }, files: ['/work/siteoffice/evil/git_flow.py', '/link/eng/git_flow.py'], bundled: B })
    expect(await engineFor(file, ROOT)).toEqual({ engine: B, ignoredSyncDir: '/link/eng', ignoredReason: 'inside' })
    const folder = io({ env: '/link/eng', real: { '/link/eng': '/work/siteoffice/evil' }, files: ['/work/siteoffice/evil/git_flow.py'], bundled: B })
    expect(await engineFor(folder, ROOT)).toEqual({ engine: B, ignoredSyncDir: '/link/eng', ignoredReason: 'inside' })
    const wt = io({ env: '/link/eng/git_flow.py', real: { '/link/eng/git_flow.py': '/work/wt-a/x/git_flow.py' }, files: ['/work/wt-a/x/git_flow.py'], worktrees: ['/work/wt-a'], bundled: B })
    expect(await engineFor(wt, ROOT)).toEqual({ engine: B, ignoredSyncDir: '/link/eng/git_flow.py', ignoredReason: 'inside' })
    const rootLink = io({ env: '/real/repo/evil', real: { '/work/siteoffice': '/real/repo' }, files: ['/real/repo/evil/git_flow.py'], bundled: B })
    expect(await engineFor(rootLink, ROOT)).toEqual({ engine: B, ignoredSyncDir: '/real/repo/evil', ignoredReason: 'inside' })
  })

  test('the canonical path is what runs, for an existing file and for a missing one placed by its folder', async () => {
    const f = io({ env: '/link/eng/git_flow.py', real: { '/link/eng/git_flow.py': '/outside/real/git_flow.py' }, files: ['/outside/real/git_flow.py'], bundled: B })
    expect(await engineFor(f, ROOT)).toEqual({ engine: '/outside/real/git_flow.py', ignoredSyncDir: '', ignoredReason: '' })
    const d = io({ env: '/link/eng', real: { '/link/eng': '/outside/eng' }, files: ['/outside/eng/git_flow.py'], bundled: B })
    expect(await engineFor(d, ROOT)).toEqual({ engine: '/outside/eng/git_flow.py', ignoredSyncDir: '', ignoredReason: '' })
  })

  test('a drive-relative, UNC or device value is ignored with no file system call', async () => {
    for (const env of ['C:evil', 'C:evil\\git_flow.py', '\\\\srv\\share\\eng', '//srv/share/eng', '\\\\.\\C:\\eng', '\\\\?\\C:\\eng', 'relative/eng', '/', '\\', '///']) {
      const calls: string[] = []
      expect(await engineFor(io({ env, files: ['/srv/share/eng/git_flow.py'], bundled: B, calls }), ROOT)).toEqual({ engine: B, ignoredSyncDir: env, ignoredReason: 'invalid-form' })
      expect(calls).toEqual([])
    }
  })

  test('a value that does not resolve is ignored', async () => {
    const calls: string[] = []
    expect(await engineFor(io({ env: '/own', files: ['/own/git_flow.py'], unresolved: ['/own/git_flow.py', '/own'], bundled: B, calls }), ROOT)).toEqual({ engine: B, ignoredSyncDir: '/own', ignoredReason: 'unresolved' })
    expect(await engineFor(io({ env: '/nowhere', bundled: B }), ROOT)).toEqual({ engine: B, ignoredSyncDir: '', ignoredReason: '' })
  })
})

describe('the Doctor and the lane agree', () => {
  const norm = (p: unknown) => String(p).replace(/\\/g, '/').replace(/^[A-Za-z]:(?=\/)/, '')
  type Case = { name: string; syncDir?: string; files: string[]; real?: Record<string, string>; bundled: boolean; found: boolean; ignored: boolean; why?: RegExp }
  const CLEAN = ['# branch.oid abc', '# branch.head main', '# branch.upstream origin/main', '# branch.ab +0 -0'].join('\n')

  function agreeWorld(on: On, c: Case) {
    const out = (stdout: string, exitCode = 0) => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
    on('command.register', () => ({ value: {} }) as never)
    on('command.run', () => ({}) as never)
    on('config.list', () => ({ value: [] }))
    on('session.cwd', () => ({ value: '/work/siteoffice' }))
    on('session.version', () => ({ value: { version: '2.1.303' } }) as never)
    on('ui.open', () => ({ value: { isPlaced: true } }) as never)
    on('ui.toast', () => ({ value: undefined }) as never)
    on('env.get', (_$, e) => {
      const name = (e as { name: string }).name
      return { value: name === 'HOME' ? '/home/a' : name === 'SITEOFFICE_SYNC_DIR' ? c.syncDir : undefined }
    })
    on('fs.exists', (_$, e) => {
      const p = norm((e as { path: string }).path)
      return { value: p === '/work/siteoffice/siteoffice.json' || c.files.includes(p) || (c.bundled && p.endsWith('/groundwork/engine/sync/git_flow.py')) || POLICY_RX.test(p) }
    })
    on('fs.stat', (_$, e) => {
      const p = norm((e as { path: string }).path)
      return { value: { kind: 'file', size: 0, mtimeMs: 0, isLink: false, realPath: c.real?.[p] ?? p } } as never
    })
    on('fs.read', (_$, e) => ({ value: POLICY_RX.test(norm((e as { path: string }).path)) ? policyText() : '{}' }))
    on('fs.list', () => ({ value: [] }) as never)
    on('process.run', (_$, e) => {
      const a = (e as { argv: string[] }).argv.join(' ')
      if (a === 'git --version') return out('git version 2.47\n')
      if (a.includes('user.name')) return out('Sam\n')
      if (a.includes('user.email')) return out('sam@example.com\n')
      if (/--version$/.test(a) && /^(python3|python|py)\b/.test(a)) return out('Python 3.12\n')
      if (a.includes('--is-inside-work-tree')) return out('true\n')
      if (a.includes('--show-toplevel')) return out('/work/siteoffice\n')
      if (a.includes('worktree list --porcelain')) return out('worktree /work/siteoffice\n\nworktree /work/wt-a\n')
      if (a.includes('status --porcelain=v2')) return out(CLEAN)
      if (a.includes('symbolic-ref')) return out('origin/main\n')
      return out('', 1)
    })
    on('session.start', (_$, e) => ({ cwd: (e as { cwd: string }).cwd }))
  }

  const B: string[] = []
  const AGREE: Case[] = [
    { name: 'unset, the bundled engine', files: B, bundled: true, found: true, ignored: false },
    { name: 'unset, no engine', files: B, bundled: false, found: false, ignored: false },
    { name: 'outside the repo, honoured', syncDir: '/own', files: ['/own/git_flow.py'], bundled: false, found: true, ignored: false },
    { name: 'inside the repo, ignored', syncDir: '/work/siteoffice/evil', files: ['/work/siteoffice/evil/git_flow.py'], bundled: false, found: false, ignored: true },
    { name: 'inside a worktree, ignored', syncDir: '/work/wt-a/evil', files: ['/work/wt-a/evil/git_flow.py'], bundled: true, found: true, ignored: true },
    { name: 'a link that resolves inside the repo, ignored', syncDir: '/link/eng', files: ['/link/eng/git_flow.py', '/work/siteoffice/evil/git_flow.py'], real: { '/link/eng/git_flow.py': '/work/siteoffice/evil/git_flow.py' }, bundled: false, found: false, ignored: true },
    { name: 'a relative value, ignored for its form', syncDir: 'evil', files: ['/work/siteoffice/evil/git_flow.py'], bundled: true, found: true, ignored: true, why: /SITEOFFICE_SYNC_DIR is not an absolute path/ },
    { name: 'a bare root, ignored for its form', syncDir: '/', files: B, bundled: true, found: true, ignored: true, why: /SITEOFFICE_SYNC_DIR is not an absolute path/ },
  ]
  for (const c of AGREE) {
    test('Doctor and lane agree: ' + c.name, { options: { gitLane: true } }, async ($, on) => {
      const clock = mock.clock(on)
      mock.store(on)
      agreeWorld(on, c)
      await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
      await clock.advance(3100)
      const lane = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-git-lane', props: { title: 'Git', isFocused: true, bodyColumns: 70, placement: 'dock', scroll: {}, view: {} } } as never)
      expect((await lane.find({ type: 'Text', text: /engine not found/ })) === undefined).toBe(c.found)
      await lane.unmount()
      await $.command.run({ command: 'setup-doctor', presentation: { isFullscreen: false, columns: 160 } } as never)
      await clock.advance(10)
      const doc = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-doctor', props: { title: 'Setup', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: {}, view: {} } } as never)
      expect((await doc.find({ key: 'fix-engine' })) === undefined).toBe(c.found)
      expect((await doc.find({ type: 'Text', text: c.why ?? /SITEOFFICE_SYNC_DIR points inside this repo/ })) !== undefined).toBe(c.ignored)
      if (c.why) expect(await doc.find({ type: 'Text', text: /SITEOFFICE_SYNC_DIR points inside this repo/ })).toBeUndefined()
      await doc.unmount()
    })
  }
})
