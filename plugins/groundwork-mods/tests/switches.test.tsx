import { expect, mock, test } from 'claude-code/testing'

// Which commands does a session start register? The Doctor always registers one; the Fleet and
// the Git lane only when their switch is on.
async function commandsAtStart($: any, on: any, surface = 'terminal'): Promise<string[]> {
  mock.clock(on)
  mock.store(on)
  const names: string[] = []
  on('command.register', ($: unknown, e: unknown) => (names.push((e as { name: string }).name), { value: {} }) as never)
  on('session.cwd', () => ({ value: 'C:\\work\\shop' }))
  on('env.get', () => ({ value: undefined }) as never)
  on('fs.exists', () => ({ value: false }))
  on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) as never)
  on('ui.toast', () => ({ value: undefined }) as never)
  on('config.list', () => ({ value: [] }))
  on('session.start', ($: unknown, e: unknown) => ({ cwd: (e as { cwd: string }).cwd }))
  await $.session.start({ cwd: 'C:\\work\\shop', surface, isInteractive: true })
  return names
}

const FLEET_CMDS = ['fleet', 'tower', 'threads', 'thread', 'burnmon', 'plain', 'blackbox']

test('switches off by default: no Fleet and no Git lane command', async ($, on) => {
  const names = await commandsAtStart($, on)
  expect(names).toContain('setup-doctor')
  for (const c of [...FLEET_CMDS, 'git']) expect(names).not.toContain(c)
})

test('fleet on registers /fleet, not /git', { options: { fleet: true } }, async ($, on) => {
  const names = await commandsAtStart($, on)
  expect(names).toContain('fleet')
  expect(names).not.toContain('git')
})

test('gitLane on registers /git, not /fleet', { options: { gitLane: true } }, async ($, on) => {
  const names = await commandsAtStart($, on)
  expect(names).toContain('git')
  expect(names).not.toContain('fleet')
})

test('both switches on register /fleet and /git together', { options: { fleet: true, gitLane: true } }, async ($, on) => {
  const names = await commandsAtStart($, on)
  expect(names).toContain('setup-doctor')
  expect(names).toContain('fleet')
  expect(names).toContain('git')
})

for (const surface of ['vscode', 'desktop', 'mobile']) {
  test('fleet registers its commands on the ' + surface + ' surface too', { options: { fleet: true } }, async ($, on) => {
    expect(await commandsAtStart($, on, surface)).toContain('fleet')
  })
  test('gitLane registers /git on the ' + surface + ' surface too', { options: { gitLane: true } }, async ($, on) => {
    expect(await commandsAtStart($, on, surface)).toContain('git')
  })
}

test('switches off: the band above the prompt carries nothing from the Fleet or the lane', async ($, on) => {
  mock.clock(on)
  on('ui.render', () => ({ type: 'engine', ref: 0 }) as never)
  const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120 } } as never)
  expect(await ui.find({ key: 'fleet' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /Changes|Saved|Shared|no agents/ })).toBeUndefined()
  await ui.unmount()
})
