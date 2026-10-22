import { describe, expect, mock, test } from 'claude-code/testing'

import { atLeast, buildReport, sessionStartReaches, summary } from '../hooks/doctor/checks'
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
    expect(summary(skipped)).toEqual({ required: 0, recommended: 1 })
  })

  test('no skipped-hook warning when both events arrived, or when neither did', async () => {
    expect(sessionStartReaches({ ...good, hooks: { sessionStart: true, classicSessionStart: true } })).toBe(true)
    expect(sessionStartReaches({ ...good, hooks: { sessionStart: false, classicSessionStart: false } })).toBe(true)
    expect(buildReport(good).find(p => p.id === 'sessionstart')?.ok).toBe(true)
    expect(summary(buildReport(good))).toEqual({ required: 0, recommended: 0 })
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
})
