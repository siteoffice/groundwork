import { describe, expect, mock, test } from 'claude-code/testing'

import { BURNMON_MAX_AGE_SECONDS, BURNMON_RELEASES_URL, atLeast, buildReport, sessionStartReaches, snapshotAgeSeconds, summary } from '../hooks/doctor/checks'
import type { DoctorFacts } from '../types'

const good: DoctorFacts = {
  isWindows: true,
  version: '2.1.303',
  isFullscreen: true,
  git: { ok: true, name: 'Sam', email: 'sam@example.com' },
  gh: { installed: true, authed: true },
  python: 'py -3',
  repo: { inside: true, root: 'C:/work/shop' },
  manifest: { found: true, valid: true, checks: 1, engineOk: true },
  plugin: true,
  hooks: { sessionStart: true, classicSessionStart: true },
  notif: 'terminal_bell',
  needsBell: true,
  statusLine: true,
  burnmon: { exists: true, ageSeconds: 2 },
}

describe('checks', () => {
  test('a fully set up laptop is ready', async () => {
    const s = summary(buildReport(good))
    expect(s).toEqual({ required: 0, recommended: 0 })
  })

  test('each gap gives one fix and where to run it', async () => {
    const probes = buildReport({ ...good, version: '2.1.250', plugin: false, git: { ok: true, name: '', email: 'x' }, python: '', notif: '', gh: { installed: false, authed: false } })
    const byId = Object.fromEntries(probes.map(p => [p.id, p]))
    expect(byId.claude?.ok).toBe(false)
    expect(byId.claude?.fix).toBe('claude update')
    expect(byId.plugin?.fixWhere).toBe('Inside Claude Code')
    expect(byId.plugin?.fix).toContain('/plugin install groundwork@groundwork')
    expect(byId.git?.fix).toContain('git config --global user.name')
    expect(byId.python?.fix).toBe('winget install --id Python.Python.3.12 -e')
    expect(byId.gh?.fix).toContain('winget install --id GitHub.cli -e')
    expect(byId.bell?.ok).toBe(false)
    expect(summary(probes).required).toBe(4)
  })

  test('the engine is only required in a Siteoffice folder; the bell only where the terminal needs it', async () => {
    const plain = buildReport({ ...good, manifest: { found: false, valid: false, checks: 0, engineOk: false }, needsBell: false })
    expect(plain.some(p => p.id === 'engine')).toBe(false)
    expect(plain.some(p => p.id === 'bell')).toBe(false)
    expect(plain.find(p => p.id === 'manifest')?.ok).toBe(false)
  })

  test('versions compare by number', async () => {
    expect(atLeast('2.1.303', '2.1.287')).toBe(true)
    expect(atLeast('2.1.29', '2.1.287')).toBe(false)
    expect(atLeast('2.2.0', '2.1.287')).toBe(true)
  })

  test('a skipped SessionStart hook is named when session.start arrived and the classic event did not', async () => {
    const skipped = buildReport({ ...good, hooks: { sessionStart: true, classicSessionStart: false } })
    const probe = skipped.find(p => p.id === 'sessionstart')
    expect(probe?.ok).toBe(false)
    expect(probe?.level).toBe('recommended')
    expect(probe?.why).toContain('no SessionStart event reached the mods')
    expect(probe?.why).toContain('an organisation manages Claude Code')
    expect(probe?.why).toContain('the agent policy keeps working')
    expect(summary(skipped)).toEqual({ required: 0, recommended: 1 })
  })

  test('no skipped-hook warning when both events arrived, or when neither did', async () => {
    expect(sessionStartReaches({ ...good, hooks: { sessionStart: true, classicSessionStart: true } })).toBe(true)
    expect(sessionStartReaches({ ...good, hooks: { sessionStart: false, classicSessionStart: false } })).toBe(true)
    expect(buildReport(good).find(p => p.id === 'sessionstart')?.ok).toBe(true)
    expect(summary(buildReport(good))).toEqual({ required: 0, recommended: 0 })
  })
})

describe('burnmon check', () => {
  const bm = (b: { exists: boolean; ageSeconds: number | null }) =>
    buildReport({ ...good, burnmon: b }).find(p => p.id === 'burnmon')!

  test('fresh snapshot is green', async () => {
    const p = bm({ exists: true, ageSeconds: 2 })
    expect(p.ok).toBe(true)
    expect(p.level).toBe('optional')
  })
  test('missing file is grey: not installed, fix links the releases page', async () => {
    const p = bm({ exists: false, ageSeconds: null })
    expect(p.ok).toBe(false)
    expect(p.tone).toBe('grey')
    expect(p.why).toContain('not installed')
    expect(p.fix).toBe(BURNMON_RELEASES_URL)
  })
  test('old snapshot is amber: installed but not running', async () => {
    const p = bm({ exists: true, ageSeconds: 600 })
    expect(p.ok).toBe(false)
    expect(p.tone).toBe('amber')
    expect(p.why).toContain('BurnMon is installed but not running')
    expect(p.fix).toBeTruthy()
  })
  test('a snapshot exactly at the age limit is amber, just under it is green', async () => {
    expect(BURNMON_MAX_AGE_SECONDS).toBe(10)
    expect(bm({ exists: true, ageSeconds: 10 }).ok).toBe(false)
    expect(bm({ exists: true, ageSeconds: 9.999 }).ok).toBe(true)
  })
  test('an unreadable file (age unknown) is amber with its own reason', async () => {
    const p = bm({ exists: true, ageSeconds: null })
    expect(p.ok).toBe(false)
    expect(p.tone).toBe('amber')
    expect(p.why).toContain('could not be read')
  })
  test('the optional probe never changes the tally', async () => {
    const gone = buildReport({ ...good, burnmon: { exists: false, ageSeconds: null } })
    expect(summary(gone)).toEqual({ required: 0, recommended: 0 })
  })
  test('snapshotAgeSeconds reads only written_at and survives bad input', async () => {
    const now = Date.parse('2026-10-25T12:00:10Z')
    expect(snapshotAgeSeconds('{"written_at":"2026-10-25T12:00:00Z","sessions":[{"x":1}]}', now)).toBe(10)
    expect(snapshotAgeSeconds('{"written_at":"2026-10-25T12:00:30Z"}', now)).toBe(0) // future: clamped
    expect(snapshotAgeSeconds('{"sessions":[]}', now)).toBeNull() // no written_at
    expect(snapshotAgeSeconds('{"written_at":12}', now)).toBeNull() // not a string
    expect(snapshotAgeSeconds('{"written_at":"not a date"}', now)).toBeNull()
    expect(snapshotAgeSeconds('', now)).toBeNull() // empty file
    expect(snapshotAgeSeconds('{not json', now)).toBeNull()
  })
})

describe('pane', () => {
  test('/setup-doctor shows what is missing, with a copy button per fix', async ($, on) => {
    const clock = mock.clock(on)
    mock.store(on)
    const copied: unknown[] = []
    on('command.register', () => ({ value: {} }) as never)
    on('session.cwd', () => ({ value: 'C:\\work\\shop' }))
    on('config.list', () => ({ value: [] }))
    on('env.get', ($, e) => ({ value: (e as { name: string }).name === 'USERPROFILE' ? 'C:\\Users\\a' : undefined }) as never)
    on('session.version', () => ({ value: { version: '2.1.303' } }) as never)
    on('ui.open', () => ({ value: { isPlaced: true } }) as never)
    on('ui.toast', () => ({ value: undefined }) as never)
    on('ui.copy', ($, e) => (copied.push(e), { value: { isCopied: true } }) as never)
    on('fs.exists', () => ({ value: false }))
    on('process.run', ($, e) => {
      const a = (e as { argv: string[] }).argv.join(' ')
      const out = (stdout: string, exitCode = 0) => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
      if (a === 'git --version') return out('git version 2.47\n')
      if (a.includes('user.name')) return out('Sam\n')
      if (a.includes('user.email')) return out('')
      if (a.startsWith('py -3')) return out('Python 3.12\n')
      if (a.includes('--show-toplevel')) return out('C:/work/shop\n')
      return out('', 1)
    })
    on('session.start', ($, e) => ({ cwd: (e as { cwd: string }).cwd }))
    on('command.run', () => ({}) as never)
    await $.session.start({ cwd: 'C:\\work\\shop', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'setup-doctor', presentation: { isFullscreen: false, columns: 160 } } as never)
    await clock.advance(10)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-doctor', props: { title: 'Setup', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: {}, view: {} } } as never)
    expect(await ui.find({ type: 'Text', text: /0N SETUP/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /to fix before Groundwork works/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Git, with your name and email/ })).toBeDefined()
    expect(await ui.find({ key: 'fix-git' })).toBeDefined()
    expect(await ui.find({ key: 'fix-python' })).toBeUndefined()
    await ui.press({ key: 'fix-git' })
    expect(JSON.stringify(copied[0])).toContain('git config --global user.name')
    await ui.unmount()
  })

  // A Siteoffice folder: siteoffice.json is there. The engine is found only where the test says so.
  async function openInSiteofficeFolder($: any, on: any, engineShips: boolean) {
    const clock = mock.clock(on)
    mock.store(on)
    on('command.register', () => ({ value: {} }) as never)
    on('session.cwd', () => ({ value: 'C:\\work\\shop' }))
    on('config.list', () => ({ value: [] }))
    on('env.get', (_: unknown, e: { name: string }) => ({ value: e.name === 'USERPROFILE' ? 'C:\\Users\\a' : undefined }) as never)
    on('session.version', () => ({ value: { version: '2.1.303' } }) as never)
    on('ui.open', () => ({ value: { isPlaced: true } }) as never)
    on('ui.toast', () => ({ value: undefined }) as never)
    on('fs.exists', (_: unknown, e: { path: string }) => {
      const p = e.path.split('\\').join('/')
      return { value: p.endsWith('/siteoffice.json') || (engineShips && p.endsWith('/groundwork/engine/sync/git_flow.py')) }
    })
    on('fs.list', () => ({ value: [] }) as never)
    on('fs.read', () => ({ value: '{"schemaVersion": 2, "settings": {"git": {"checks": ["pytest"]}}}' }) as never)
    on('process.run', (_: unknown, e: { argv: string[] }) => {
      const a = e.argv.join(' ')
      const out = (stdout: string, exitCode = 0) => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
      if (a === 'git --version') return out('git version 2.47\n')
      if (a.includes('user.name')) return out('Sam\n')
      if (a.includes('user.email')) return out('sam@example.com\n')
      if (a.startsWith('py -3')) return out('Python 3.12\n')
      if (a.includes('--show-toplevel')) return out('C:/work/shop\n')
      return out('', 1)
    })
    on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
    on('command.run', () => ({}) as never)
    await $.session.start({ cwd: 'C:\\work\\shop', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'setup-doctor', presentation: { isFullscreen: false, columns: 160 } } as never)
    await clock.advance(10)
    return await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-doctor', props: { title: 'Setup', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: {}, view: {} } } as never)
  }

  test('the engine that ships with the plugin counts as found', async ($, on) => {
    const ui = await openInSiteofficeFolder($, on, true)
    expect(await ui.find({ type: 'Text', text: /The siteoffice-git engine/ })).toBeDefined()
    expect(await ui.find({ key: 'fix-engine' })).toBeUndefined()
    await ui.unmount()
  })

  test('without a shipped engine the Doctor still names it as missing', async ($, on) => {
    const ui = await openInSiteofficeFolder($, on, false)
    expect(await ui.find({ key: 'fix-engine' })).toBeDefined()
    await ui.unmount()
  })

  // BurnMon's snapshot at <home>/.burnmon/snapshot.json. Only its written_at may be read.
  async function openWithSnapshot($: any, on: any, opts: { home?: string; snapshot: 'missing' | 'throws' | 'fresh' | 'old' | 'fresh-with-sessions' }) {
    const clock = mock.clock(on)
    mock.store(on)
    on('command.register', () => ({ value: {} }) as never)
    on('session.cwd', () => ({ value: 'C:\\work\\shop' }))
    on('config.list', () => ({ value: [] }))
    on('env.get', (_: unknown, e: { name: string }) => {
      if (e.name === 'USERPROFILE') return { value: opts.home === '' ? undefined : (opts.home ?? 'C:\\Users\\a') } as never
      return { value: undefined } as never
    })
    on('session.version', () => ({ value: { version: '2.1.303' } }) as never)
    on('ui.open', () => ({ value: { isPlaced: true } }) as never)
    on('ui.toast', () => ({ value: undefined }) as never)
    on('ui.copy', () => ({ value: { isCopied: true } }) as never)
    on('fs.exists', (_: unknown, e: { path: string }) => ({
      value: opts.snapshot !== 'missing' && e.path.split('\\').join('/').endsWith('/.burnmon/snapshot.json'),
    }))
    on('fs.list', () => ({ value: [] }) as never)
    // Handlers answer synchronously, so the mock clock's start is read once, here. The pane's
    // check runs ms after it (clock.advance(10) below), far inside the 3 s and 600 s ages.
    const now: number = (clock as any).now()
    on('fs.read', (_: unknown, e: { path: string }) => {
      if (!e.path.split('\\').join('/').endsWith('/.burnmon/snapshot.json')) return { value: '' } as never
      if (opts.snapshot === 'throws') throw new Error('locked')
      const age = opts.snapshot === 'old' ? 600 : 3
      const extra = opts.snapshot === 'fresh-with-sessions' ? ',"sessions":[{"project":"SECRETMARK"}]' : ''
      return { value: '{"written_at":"' + new Date(now - age * 1000).toISOString() + '"' + extra + '}' } as never
    })
    on('process.run', (_: unknown, e: { argv: string[] }) => {
      const a = e.argv.join(' ')
      const out = (stdout: string, exitCode = 0) => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
      if (a === 'git --version') return out('git version 2.47\n')
      if (a.includes('user.name')) return out('Sam\n')
      if (a.includes('user.email')) return out('sam@example.com\n')
      if (a.startsWith('py -3')) return out('Python 3.12\n')
      if (a.includes('--show-toplevel')) return out('C:/work/shop\n')
      return out('', 1)
    })
    on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
    on('command.run', () => ({}) as never)
    await $.session.start({ cwd: 'C:\\work\\shop', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'setup-doctor', presentation: { isFullscreen: false, columns: 160 } } as never)
    await clock.advance(10)
    return await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-doctor', props: { title: 'Setup', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: {}, view: {} } } as never)
  }

  test('a fresh snapshot shows BurnMon as running, and none of its other content', async ($, on) => {
    const ui = await openWithSnapshot($, on, { snapshot: 'fresh-with-sessions' })
    expect(await ui.find({ type: 'Text', text: /BurnMon \(feeds the Fleet\)/ })).toBeDefined()
    expect(await ui.find({ key: 'fix-burnmon' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /SECRETMARK/ })).toBeUndefined()
    await ui.unmount()
  })
  test('an old snapshot says installed but not running', async ($, on) => {
    const ui = await openWithSnapshot($, on, { snapshot: 'old' })
    expect(await ui.find({ type: 'Text', text: /BurnMon is installed but not running/ })).toBeDefined()
    expect(await ui.find({ key: 'fix-burnmon' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '◐' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '○' })).toBeUndefined()
    await ui.unmount()
  })
  test('an unreadable snapshot file is amber, not a crash', async ($, on) => {
    const ui = await openWithSnapshot($, on, { snapshot: 'throws' })
    expect(await ui.find({ type: 'Text', text: /could not be read/ })).toBeDefined()
    await ui.unmount()
  })
  test('a missing file is "not installed" with a copyable fix', async ($, on) => {
    const ui = await openWithSnapshot($, on, { snapshot: 'missing' })
    expect(await ui.find({ type: 'Text', text: /not installed/ })).toBeDefined()
    expect(await ui.find({ key: 'fix-burnmon' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '○' })).toBeDefined()
    await ui.unmount()
  })
  test('with no home folder known the Doctor says not installed and does not throw', async ($, on) => {
    const ui = await openWithSnapshot($, on, { home: '', snapshot: 'fresh' })
    expect(await ui.find({ type: 'Text', text: /not installed/ })).toBeDefined()
    await ui.unmount()
  })
})
