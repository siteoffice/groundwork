import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { PLAIN_CAP_DEFAULT, bar, dayOf, firstLine, groupLineOf, overheadFile, overheadLine, plainTip, tokensOf, isGuardHold, isSetupNoise, outcomeOf, colorFor, levelColor, parseExplanations, perHour, phraseFor, questionsKey, resetLabel, spark, treeOrder, withPlainWords } from '../hooks/fleet/plain'
import { agentViewAttention, attentionOf, canStop, endShell, gitConfigEnv, hardenGit, isUnderWorktrees, liveCards, nullDevice, numstatSum, parseAgentView, plainText, reconcileShells, safeKey, proofOf, resumeCommand, shellState, sortThreads, splitDiff, startShell, threadState, verdictOf, visibleShells } from '../hooks/fleet/threads'
import type { FleetAgentView, FleetCard, FleetShell, FleetThread } from '../types'

import { POLICY_RX, policyText, underHost } from './host'
import { burnmonNote } from '../hooks/burnmon'
import { BURNMON_RELEASES_URL } from '../hooks/doctor/checks'

const TMP = 'C:\\Temp\\gw-fleet'
const ON = { options: { fleet: true } } as const

describe('plain language', () => {
  test('tool calls read as short human phrases', async () => {
    expect(phraseFor('Edit', { file_path: TMP + '\\shop\\checkout.ts' })).toBe('changing checkout.ts')
    expect(phraseFor('Read', { file_path: '/repo/specs/spec.md' })).toBe('reading spec.md')
    expect(phraseFor('Bash', { command: 'npm test', description: 'Run the tests' })).toBe('running Run the tests')
    expect(phraseFor('Agent', { description: 'Review the spec' })).toBe('handing off: Review the spec')
    expect(phraseFor('mcp__github__create_pr', {})).toBe('using github create_pr')
  })

  test('running uses the brand accent that is legible on the theme', async () => {
    expect(colorFor('running', false)).toBe('#3EDCB0')
    expect(colorFor('running', true)).toBe('#0B6B5B')
    expect(colorFor('waiting', true)).toBe('warning')
  })

  test('agents come out as a tree', async () => {
    const order = treeOrder([
      { id: 'b', parentId: 'a' },
      { id: 'a' },
      { id: 'c' },
    ])
    expect(order.map(o => o.item.id + o.depth)).toEqual(['a1', 'b2', 'c1'])
  })
})

describe('BurnMon', () => {
  test('bars, levels, trend and burn rate read right', async () => {
    expect(bar(40, 10)).toEqual(['\u25AE'.repeat(4), '\u25AF'.repeat(6)])
    expect(bar(130, 5)[1]).toBe('')
    expect(levelColor(30)).toBe('success')
    expect(levelColor(70)).toBe('warning')
    expect(levelColor(90)).toBe('error')
    expect(spark([0, 50, 100])).toBe('\u2581\u2585\u2588')
    expect(perHour(2, 0, 2 * 3600000)).toBe('$1.00/h')
    expect(perHour(2, 0, 60000)).toBe('')
    expect(resetLabel(undefined, 0)).toBe('')
    expect(resetLabel(new Date(3 * 3600000).toISOString(), 0)).toMatch(/^\d\d:\d\d$/)
    expect(resetLabel(new Date(3 * 86400000).toISOString(), 0)).toMatch(/^[A-Z][a-z]{2} \d\d:\d\d$/)
  })
})

describe('Quiet transcript', () => {
  test('a finished call says what it came to in a few words', async () => {
    expect(outcomeOf('Edit', { structuredPatch: [{ lines: [' a', '-b', '+c', '+d'] }] })).toBe('+2 \u22121 lines')
    expect(outcomeOf('Write', { type: 'create', content: 'a\nb\nc' })).toBe('new file, 3 lines')
    expect(outcomeOf('Bash', { stdout: '===== 12 passed in 1.2s =====', stderr: '' })).toBe('tests green, 12 passed')
    expect(outcomeOf('Bash', { stdout: 'Tests: 2 failed, 40 passed', stderr: '' })).toBe('tests red, 2 failed, 40 passed')
    expect(outcomeOf('Bash', { stdout: 'a\nb\n', stderr: '' })).toBe('2 lines of output')
    expect(outcomeOf('Grep', { numFiles: 0 })).toBe('nothing found')
    expect(groupLineOf([{ tool: 'Read', input: { file_path: TMP + '\\notes.md' } }, { tool: 'Read' }, { tool: 'Grep' }])).toBe('looked at notes.md and 1 more, searched once')
    expect(isGuardHold('Git lane guard held this call: it deletes a folder')).toBe(true)
    expect(isGuardHold('Exit code 1')).toBe(false)
    expect(isSetupNoise('ecc: hooks.json: unknown keys "description" in hooks.PreToolUse[0]')).toBe(true)
    expect(isSetupNoise('Model from your settings')).toBe(false)
  })
})

const DIFF = [
  'diff --git a/src/thread.ts b/src/thread.ts',
  'index 111..222 100644',
  '--- a/src/thread.ts',
  '+++ b/src/thread.ts',
  '@@ -1,2 +1,3 @@',
  ' const a = 1',
  '-const b = 2',
  '+const b = 3',
  '+const c = 4',
].join('\n')

const thread = (over: Partial<FleetThread>): FleetThread => ({
  id: 'x', name: 'x', task: 'x', status: 'running', startedAt: 0, isOurs: true, answer: '', workedMs: 0, turns: 0, proofs: {}, ...over,
})

describe('threads helpers', () => {
  test('every Fleet git call turns off fsmonitor and hooks, and git diff runs no external program', async () => {
    expect(hardenGit(['git', '-C', '/r', 'rev-parse', 'HEAD'], false)).toEqual(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', '/r', 'rev-parse', 'HEAD'])
    expect(hardenGit(['git', '-C', '/r', 'diff', '--numstat', 'abc'], true)).toEqual(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=NUL', '-C', '/r', 'diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', '--numstat', 'abc'])
    expect(hardenGit(['git', '-C', 'diff', 'ls-files'], false)).toEqual(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', 'diff', 'ls-files'])
    expect(hardenGit(['gh', 'pr', 'view'], true)).toEqual(['gh', 'pr', 'view'])
    expect(gitConfigEnv(true)).toEqual({ GIT_CONFIG_COUNT: '2', GIT_CONFIG_KEY_0: 'core.fsmonitor', GIT_CONFIG_VALUE_0: 'false', GIT_CONFIG_KEY_1: 'core.hooksPath', GIT_CONFIG_VALUE_1: 'NUL' })
    expect(nullDevice(false)).toBe('/dev/null')
    expect(nullDevice(true)).toBe('NUL')
  })
  test('a thread root counts only inside the repo\'s .claude/worktrees folder', async () => {
    expect(isUnderWorktrees('/work/siteoffice/.claude/worktrees/agent-a1', '/work/siteoffice')).toBe(true)
    expect(isUnderWorktrees('C:\\Work\\Shop\\.claude\\worktrees\\agent-a1\\src', 'c:/work/shop/')).toBe(true)
    expect(isUnderWorktrees('/work/wt', '/work/siteoffice')).toBe(false)
    expect(isUnderWorktrees('/work/siteoffice/.claude/worktrees', '/work/siteoffice')).toBe(false)
    expect(isUnderWorktrees('/work/siteoffice/.claude/worktrees/../../evil', '/work/siteoffice')).toBe(false)
    expect(isUnderWorktrees('/work/siteoffice-evil/.claude/worktrees/x', '/work/siteoffice')).toBe(false)
    expect(isUnderWorktrees('/work/siteoffice/.claude/worktrees/x', '')).toBe(false)
  })
  test('checks, counts, diff files, order and verdicts read right', async () => {
    expect(proofOf('npx vitest run src')).toBe('tests')
    expect(proofOf('py -3 -m pytest flows -q')).toBe('tests')
    expect(proofOf('npx tsc -p . --noEmit')).toBe('typecheck')
    expect(proofOf('npx eslint .')).toBe('lint')
    expect(proofOf('git status')).toBeUndefined()
    expect(numstatSum('3\t1\ta.ts\n-\t-\tlogo.png\n')).toEqual({ files: 2, add: 3, del: 1 })
    const split = splitDiff(DIFF)
    expect(split.files[0]?.path).toBe('src/thread.ts')
    expect(split.files[0]?.hunks.startsWith('@@')).toBe(true)
    expect(split.files[0]?.add).toBe(2)
    expect(splitDiff(DIFF, 10).more).toBe(1)
    const order = sortThreads([thread({ id: 'w', startedAt: 3 }), thread({ id: 'r', status: 'completed', startedAt: 1 }), thread({ id: 's', status: 'completed', settled: true })])
    expect(order.map(t => t.id)).toEqual(['r', 'w', 's'])
    expect(threadState(thread({ status: 'completed' }))).toBe('needs-you')
    expect(verdictOf('VERDICT: ok\nNo issues found.')).toBe('ok')
    expect(verdictOf('VERDICT: issues\n- a.ts:3 off by one')).toBe('issues')
  })

  test('fleet keeps live cards, newest per session, waiting first', async () => {
    const card = (over: Partial<FleetCard>): FleetCard => ({
      v: 1, sessionId: 'a', project: 'p', cwd: '/w', state: 'idle', running: 0, questions: 0, threads: { needsYou: 0, working: 0 }, ask: '', answer: '', at: 1000, ...over,
    })
    const rows = liveCards(
      [card({ sessionId: 'a', project: 'zz' }), card({ sessionId: 'b', state: 'waiting', project: 'yy' }), card({ sessionId: 'c', at: 1 - 200000 }), card({ sessionId: 'd', ended: true })],
      1000,
    )
    expect(rows.map(r => r.sessionId)).toEqual(['b', 'a'])
    expect(resumeCommand({ cwd: TMP + '\\office', sessionId: 'abc' })).toBe("Set-Location '" + TMP + "\\office'; claude --resume abc")
    expect(resumeCommand({ cwd: '/work/x', sessionId: 'abc' })).toBe("cd '/work/x' && claude --resume abc")
  })
})

describe('Fleet', () => {
  test('attention reads questions, slow permissions and finished agents, newest first', async () => {
    const list = attentionOf({
      sessionId: 's', project: 'p', mainBusy: false, mainAnswer: 'Done, see the diff.', mainAnswerAt: 10,
      asks: { a1: { text: 'Which file?', at: 30 } }, perms: { a2: { text: 'wants to run npm test', at: 9500 }, a3: { text: 'wants to edit x', at: 2000 } },
      agents: [{ id: 'a1', name: 'one', status: 'waiting' }, { id: 'a2', name: 'two', status: 'running' }, { id: 'a3', name: 'three', status: 'running' }, { id: 'a4', name: 'four', status: 'completed' }],
      answers: { a4: 'All green.' }, doneAt: { a4: 40 }, now: 10000,
    })
    expect(list.map(a => a.agent + ':' + a.kind)).toEqual(['three:permission', 'four:done', 'one:question', 'main thread:done'])
  })
})

describe('Fleet notices', () => {
  test('markdown marks are dropped for the terminal', async () => {
    expect(plainText('**TL;DR:** use `npm test`\n## Next')).toBe('TL;DR: use npm test\nNext')
    expect(plainText('Done.\n---\nNext step')).toBe('Done.\n\nNext step')
  })
})

describe('Plain Questions', () => {
  test('each option gets one plain sentence; labels stay as Claude wrote them', async () => {
    const qs = [{ question: 'Which store?', header: 'Store', options: [{ label: 'D1', description: 'SQLite at the edge' }, { label: 'R2', description: '' }] }]
    const plain = parseExplanations('Here: {"q":[["Keeps data in a small database.","Keeps files, not records."]]}')
    expect(plain).toEqual([['Keeps data in a small database.', 'Keeps files, not records.']])
    const out = withPlainWords(qs, plain as string[][]) as { options: { label: string; description: string }[] }[]
    expect(out[0]?.options[0]?.label).toBe('D1')
    expect(out[0]?.options[0]?.description).toBe('SQLite at the edge \u00B7 In plain words: Keeps data in a small database.')
    expect(out[0]?.options[1]?.description).toBe('In plain words: Keeps files, not records.')
    expect(questionsKey(qs)).toBe(questionsKey(JSON.parse(JSON.stringify(qs))))
    expect(parseExplanations('no json')).toBeUndefined()
  })
})

describe('Fleet and agent view', () => {
  test('agent view rows read right, and only sessions without a Fleet card raise items', async () => {
    const list = parseAgentView(JSON.stringify([
      { cwd: TMP + '\\reports', kind: 'background', id: 'bg7', state: 'blocked', status: 'waiting', waitingFor: 'permission prompt', sessionId: 's-bg', startedAt: 5 },
      { cwd: '/w/shop', kind: 'interactive', status: 'waiting', waitingFor: 'input needed', sessionId: 's-card', name: 'shop' },
      { cwd: '/w/me', kind: 'interactive', status: 'idle', sessionId: 's-me' },
      { cwd: '/w/old', kind: 'background', id: 'bg9', state: 'done', sessionId: 's-done', startedAt: 9 },
    ]))
    expect(list).toHaveLength(4)
    const card = { v: 1, sessionId: 's-card', project: 'shop', cwd: '/w/shop', state: 'waiting', running: 0, questions: 1, threads: { needsYou: 0, working: 0 }, ask: '', answer: '', at: 0 } as FleetCard
    const items = agentViewAttention(list, [card], 's-me', 100)
    expect(items.map(a => a.project + ':' + a.kind + ':' + (a.attach ?? ''))).toEqual(['reports:permission:claude attach bg7', 'old:done:claude attach bg9'])
    expect(parseAgentView('3 agents\nExplore')).toEqual([])
  })
})

describe('background shells helpers', () => {
  test('a shell starts, is reconciled against the turn-end list and ends on its notice', async () => {
    expect(shellState('running')).toBe('running')
    expect(shellState('pending')).toBe('running')
    expect(shellState('failed')).toBe('failed')
    expect(shellState('killed')).toBe('killed')
    expect(shellState('completed')).toBe('completed')
    let list: FleetShell[] = startShell([], 'b1', 'npm run dev', 100)
    expect(startShell(list, 'b1', 'npm run dev', 200)).toBe(list)
    list = reconcileShells(list, [{ id: 'b2', type: 'shell', status: 'running', description: 'watch', command: 'tsc -w' }, { id: 'a1', type: 'subagent', status: 'running', description: 'x' }], 300)
    expect(list.map(s => s.id + ':' + s.state)).toEqual(['b1:completed', 'b2:running'])
    expect(list[0]?.endedAt).toBe(300)
    // The shell's own notice has the last word, even after a turn-end guess.
    list = endShell(list, 'b1', 'failed', 400)
    expect(list[0]).toMatchObject({ state: 'failed', isSure: true })
    list = endShell(list, 'b1', 'completed', 500)
    expect(list[0]?.state).toBe('failed')
  })

  test('a finished shell stays visible for ten minutes', async () => {
    const list: FleetShell[] = [
      { id: 'r', command: 'a', state: 'running', startedAt: 0 },
      { id: 'd', command: 'b', state: 'completed', startedAt: 0, endedAt: 1000 },
    ]
    expect(visibleShells(list, 1000 + 599000).map(s => s.id)).toEqual(['r', 'd'])
    expect(visibleShells(list, 1000 + 600000).map(s => s.id)).toEqual(['r'])
  })
})

const USAGE = { input_tokens: 600, output_tokens: 80, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }

describe('Plain Mode overhead helpers', () => {
  test('one line holds the exact fields BurnMon reads', async () => {
    expect(tokensOf(USAGE)).toBe(680)
    expect(tokensOf({ input_tokens: 1, output_tokens: 2, cache_read_input_tokens: 3, cache_creation_input_tokens: 4 })).toBe(10)
    expect(JSON.parse(overheadLine({ at: '2026-10-22T10:00:00.000Z', session: 's1', sw: 'plain-sowhat', model: 'haiku', usage: USAGE }))).toEqual({
      at: '2026-10-22T10:00:00.000Z', session: 's1', plugin: 'groundwork-mods', switch: 'plain-sowhat', model: 'haiku', input: 600, output: 80, cache_read: 0, cache_creation: 0,
    })
    expect(Object.keys(JSON.parse(overheadLine({ at: 'a', session: 's', sw: 'plain-questions', model: 'm', usage: USAGE })))).toEqual(['at', 'session', 'plugin', 'switch', 'model', 'input', 'output', 'cache_read', 'cache_creation'])
    expect(overheadFile('C:\\Users\\sam', '2026-10-22')).toBe('C:\\Users\\sam\\.burnmon\\overhead\\2026-10-22.ndjson')
    expect(overheadFile('/home/sam', '2026-10-22')).toBe('/home/sam/.burnmon/overhead/2026-10-22.ndjson')
    expect(plainTip(680, 50000, true)).toBe('Plain Mode: 680 tokens today of 50000 \u00B7 on \u00B7 /plain')
    expect(PLAIN_CAP_DEFAULT).toBe(50000)
  })
})

describe('stop button helpers', () => {
  test('only a background session with an id, not this one, can be stopped', async () => {
    const v = (over: Partial<FleetAgentView>): FleetAgentView => ({ cwd: '/w', kind: 'background', id: 'bg1', sessionId: 's-bg', ...over })
    expect(canStop(v({}), 's-me')).toBe(true)
    expect(canStop(v({ kind: 'interactive' }), 's-me')).toBe(false)
    expect(canStop(v({ id: undefined }), 's-me')).toBe(false)
    expect(canStop(v({ sessionId: 's-me' }), 's-me')).toBe(false)
    expect(canStop(v({ id: 's-me' }), 's-me')).toBe(false)
    expect(parseAgentView(JSON.stringify([{ cwd: '/w', kind: 'background', id: 'bg1', pinned: true }]))[0]?.pinned).toBe(true)
    expect(parseAgentView(JSON.stringify([{ cwd: '/w', kind: 'background', id: 'bg1' }]))[0]?.pinned).toBeUndefined()
    const items = agentViewAttention([v({ state: 'blocked', status: 'waiting', waitingFor: 'permission prompt' })], [], 's-me', 100)
    expect(items[0]?.stopId).toBe('bg1')
    expect(firstLine('could not stop\nmore detail')).toBe('could not stop')
    expect(firstLine('')).toBe('')
  })
})

const norm = (p: unknown) => String(p).replace(/\\/g, '/').replace(/^[A-Za-z]:(?=\/)/, '')

const BAND = {
  plugin: 'groundwork-mods',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100 },
} as const

describe('band and pane', () => {
  test('the band shows on terminal and desktop with the Fleet button', ON, async ($, on) => {
    mock.clock(on)
    on('ui.render', ($, e) => ({ type: 'engine', ref: 0 }) as never)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface } as never)
      expect(await ui.find({ key: 'fleet' })).toBeDefined()
      expect(await ui.find({ key: 'open' })).toBeUndefined()
      expect(await ui.find({ type: 'Text', text: /no agents/ })).toBeDefined()
      await ui.unmount()
    }
  })

  test('the Fleet pane reads top to bottom on every surface: needs you, this session, other sessions', ON, async ($, on) => {
    mock.clock(on)
    for (const surface of ['terminal', 'desktop', 'mobile'] as const) {
      const ui = await $.ui.mount({
        plugin: 'groundwork-mods',
        surface,
        component: 'Pane',
        requestId: 'groundwork-fleet',
        props: { title: 'Fleet', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: {}, view: {} },
      } as never)
      expect(await ui.find({ key: 'agent-main' })).toBeDefined()
      expect(await ui.find({ key: 'tab-agents' })).toBeUndefined()
      for (const title of [/NEEDS YOU/, /THIS SESSION/, /OTHER SESSIONS/, /0N FLEET/]) expect(await ui.find({ type: 'Text', text: title })).toBeDefined()
      expect(await ui.find({ key: 'pane-burn' })).toBeDefined()
      await ui.unmount()
    }
  })
})

describe('wire', () => {
  test('a message that never arrived shows as not delivered', ON, async ($, on) => {
    mock.clock(on)
    on('session.send', () => ({ isDelivered: false, reason: 'not reachable' }))
    await $.session.send({ to: 'developer', text: 'spec ok' } as never)
    const ui = await $.ui.mount({
      plugin: 'groundwork-mods',
      surface: 'terminal',
      component: 'Pane',
      requestId: 'groundwork-fleet',
      props: { title: 'Fleet', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: {}, view: {} },
    } as never)
    expect(await ui.find({ type: 'Text', text: /not delivered: not reachable/ })).toBeDefined()
    await ui.unmount()
  })
})

describe('after /clear', () => {
  test('the pane still names the project', ON, async ($, on) => {
    mock.clock(on)
    on('classic.SessionStart', () => ({}))
    await $.classic.SessionStart({ source: 'clear', cwd: '/work/siteoffice' } as never)
    const ui = await $.ui.mount({
      plugin: 'groundwork-mods',
      surface: 'terminal',
      component: 'Pane',
      requestId: 'groundwork-fleet',
      props: { title: 'Fleet', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: {}, view: {} },
    } as never)
    expect(await ui.find({ type: 'Text', text: /siteoffice/ })).toBeDefined()
    await ui.unmount()
  })
})

describe('BurnMon', () => {

  test('the band button switches the BurnMon row off and on, on both surfaces', ON, async ($, on) => {
    mock.clock(on)
    on('ui.render', ($, e) => ({ type: 'engine', ref: 0 }) as never)
    mock.store(on)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface } as never)
      expect(await ui.find({ type: 'Text', text: /^BURN$/ })).toBeDefined()
      await ui.press({ key: 'burn' })
      expect(await ui.find({ type: 'Text', text: /^BURN$/ })).toBeUndefined()
      await ui.press({ key: 'burn' })
      expect(await ui.find({ type: 'Text', text: /^BURN$/ })).toBeDefined()
      await ui.unmount()
    }
  })
})

describe('BurnMon switch', () => {
  test('a saved off survives into the next session', ON, async ($, on) => {
    mock.clock(on)
    on('ui.render', ($, e) => ({ type: 'engine', ref: 0 }) as never)
    mock.store(on, { 'fleet.burnOn': false })
    on('classic.SessionStart', () => ({}))
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    const ui = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await ui.find({ type: 'Text', text: /^BURN$/ })).toBeUndefined()
    expect(await ui.find({ key: 'burn' })).toBeDefined()
    await ui.unmount()
  })
})

const TOOLUSE = (tool: string, input: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  plugin: 'groundwork-mods',
  component: 'ToolUse',
  props: { tool_use_id: 't1', tool, input, isRunning: false, isErrored: false, isInterrupted: false, ...extra },
})

describe('Plain Mode', () => {
  test('a tool row becomes one plain sentence, on terminal and desktop', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on, { 'fleet.plainOn': true })
    on('classic.SessionStart', () => ({}))
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...TOOLUSE('Edit', { file_path: '/work/siteoffice/checkout.ts' }), surface } as never)
      expect(await ui.find({ type: 'Text', text: /changing checkout\.ts/ })).toBeDefined()
      await ui.unmount()
      const bad = await $.ui.mount({ ...TOOLUSE('Bash', { command: 'npm test' }, { isErrored: true }), surface } as never)
      expect(await bad.find({ type: 'Text', text: /failed/ })).toBeDefined()
      await bad.unmount()
    }
  })

  test('a routine result is hidden while Plain Mode is on', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on, { 'fleet.plainOn': true })
    on('classic.SessionStart', () => ({}))
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    const ui = await $.ui.mount({
      plugin: 'groundwork-mods',
      surface: 'terminal',
      component: 'ToolResult',
      props: { tool_use_id: 't1', tool: 'Read', output: {}, isErrored: false },
    } as never)
    expect(await ui.drawn()).toMatchObject({ type: 'Box' })
    expect(await ui.find({ type: 'Text' })).toBeUndefined()
    await ui.unmount()
  })
})

describe('Quiet transcript', () => {

  test('a row folds its output away and unfolds it on a press', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on, { 'fleet.plainOn': true })
    on('classic.SessionStart', () => ({}))
    on('ui.render', () => ({ type: 'engine', ref: 0 }) as never)
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    const row = await $.ui.mount({ ...TOOLUSE('Bash', { command: 'npm test', description: 'run the tests' }, { output: { stdout: '12 passed', stderr: '' } }), surface: 'terminal' } as never)
    expect(await row.find({ type: 'Text', text: /tests green, 12 passed/ })).toBeDefined()
    const RESULT = { plugin: 'groundwork-mods', surface: 'terminal', component: 'ToolResult', props: { tool_use_id: 't1', tool: 'Bash', output: { stdout: '12 passed', stderr: '' }, isErrored: false } }
    const before = await $.ui.mount(RESULT as never)
    expect(await before.find({ type: 'Text' })).toBeUndefined()
    await before.unmount()
    await row.press({ key: 'q-t1' })
    await row.unmount()
    const after = await $.ui.mount(RESULT as never).catch((err: unknown) => String(err))
    expect(typeof after === 'string' ? after : 'drawn by the plugin').toMatch(/engine drew its own|drawn by the plugin/)
    expect(typeof after === 'string' ? 'unfolded' : JSON.stringify(await after.drawn())).not.toBe('{"type":"Box"}')
  })
})

describe('Quiet transcript, plain lines', () => {
  test('a one-call group reads as that call, and a guard hold reads as held, not failed', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on, { 'fleet.plainOn': true })
    on('classic.SessionStart', () => ({}))
    on('ui.render', () => ({ type: 'engine', ref: 0 }) as never)
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    const ui = await $.ui.mount({
      plugin: 'groundwork-mods',
      surface: 'terminal',
      component: 'ToolGroup',
      props: {
        isActive: false,
        isExpanded: false,
        calls: [{ tool_use_id: 'g1', tool: 'PowerShell', input: { command: 'npm run clean', description: 'clean the build folder' }, isRunning: false, isErrored: true, isInterrupted: false, output: 'Git lane guard held this call: it deletes a folder' }],
      },
    } as never)
    expect(await ui.find({ type: 'Text', text: /running clean the build folder/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /held by the Git guard/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /failed/ })).toBeUndefined()
    await ui.unmount()
  })

  test('plugin and hook notices shrink to one short line each; other notices stay', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on, { 'fleet.plainOn': true })
    on('classic.SessionStart', () => ({}))
    on('ui.render', () => ({ type: 'engine', ref: 0 }) as never)
    await $.classic.SessionStart({ source: 'resume', cwd: '/work/siteoffice' } as never)
    const N = (text: string) => ({ plugin: 'groundwork-mods', surface: 'terminal', component: 'InfoNotice', props: { text, command: null } })
    const a = await $.ui.mount(N('ecc: hooks.json: unknown keys "description" in hooks.PreToolUse[0] and 51 more ignored') as never)
    expect(await a.find({ type: 'Text', text: /setup note from ecc/ })).toBeDefined()
    expect(await a.find({ type: 'Text', text: /unknown keys/ })).toBeUndefined()
    await a.unmount()
    const other = await $.ui.mount(N('Model from your settings') as never).catch((err: unknown) => String(err))
    expect(typeof other === 'string' ? other : 'drawn').toMatch(/engine drew its own|drawn/)
  })
})

describe('Black Box', () => {
  test('a tool call lands as one NDJSON line in the project 03_logs folder', ON, async ($, on) => {
    const clock = mock.clock(on)
    mock.store(on, { 'fleet.bbOn': true })
    const files: Record<string, string> = {}
    on('session.cwd', () => ({ value: '/work/siteoffice' }))
    on('agent.list', () => ({ value: [] }))
    on('session.usage', () => ({ value: { startedAt: 0, context: { window: 200000 }, rateLimits: [] } }) as never)
    on('config.list', () => ({ value: [] }))
    on('env.get', () => ({ value: undefined }))
    on('command.register', () => ({ value: {} }) as never)
    on('fs.exists', ($, e) => ({ value: norm((e as { path: string }).path) === '/work/siteoffice/03_logs' || norm((e as { path: string }).path) in files || POLICY_RX.test(norm((e as { path: string }).path)) }))
    on('fs.read', ($, e) => ({ value: POLICY_RX.test(norm((e as { path: string }).path)) ? policyText() : (files[norm((e as { path: string }).path)] ?? '') }))
    on('fs.write', ($, e) => {
      const w = e as { path: string; text: string }
      files[norm(w.path)] = w.text
      return { value: undefined }
    })
    on('tool.call', () => ({ result: { ok: true } }) as never)
    on('session.start', ($, e) => ({ cwd: (e as { cwd: string }).cwd }))
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await $.tool.call({ tool: 'Read', file_path: '/work/siteoffice/spec.md' } as never)
    await clock.advance(4100)
    const path = Object.keys(files).find(p => p.startsWith('/work/siteoffice/03_logs/blackbox/'))
    expect(path).toBeDefined()
    const lines = (files[path as string] ?? '').trim().split('\n').map(l => JSON.parse(l))
    expect(lines.some(l => l.kind === 'session' && l.to === 'start')).toBe(true)
    expect(lines.some(l => l.kind === 'tool' && l.did === 'reading spec.md' && l.outcome === 'ok')).toBe(true)
  })
})

describe('band rule', () => {
  test('a line sits above the band and another mod\'s band stays under it', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on)
    on('ui.render', ($, e) => {
      const { Text } = $.ui.resolve(e)
      return Text({ children: ['GIT LANE'] })
    })
    const ui = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await ui.find({ type: 'Text', text: /^\u2500{10,}$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /GIT LANE/ })).toBeDefined()
    await ui.unmount()
  })
})

describe('chips', () => {
  test('band icons carry a hidden hint that shows on hover, on terminal and desktop', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on)
    on('ui.render', ($, e) => ({ type: 'engine', ref: 0 }) as never)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ ...BAND, surface } as never)
      expect(await ui.find({ key: 'burn' })).toBeDefined()
      expect(await ui.find({ key: 'hint-burn' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /BurnMon on/ })).toBeDefined()
      await ui.unmount()
    }
  })
})

const NOW = Date.UTC(2026, 9, 19, 12, 0, 0)
const PANE_PROPS = (cols: number) => ({ title: 'Fleet', isFocused: false, bodyColumns: cols, placement: 'dock', scroll: {}, view: {} })

// The engine around one session: its folder, its id, a home folder and a
// file system in memory, git answering from a table.
function session(on: On, files: Record<string, string>, agents: unknown[] = [], gitTable: [RegExp, string][] = [], toasts: unknown[] = [], registered: string[] = [], runs: string[] = [], home: string | undefined = '/home/w', inits: { argv: string; env?: Record<string, string> }[] = [], links: Record<string, string> = {}) {
  on('fs.stat', ($, e) => {
    const p = (e as { path: string }).path
    return { value: { kind: 'dir', size: 0, mtimeMs: 0, isLink: false, realPath: links[norm(p)] ?? p } } as never
  })
  on('session.cwd', () => ({ value: '/work/siteoffice' }))
  on('session.id', () => ({ value: 's-me' }) as never)
  on('agent.list', () => ({ value: agents }) as never)
  on('agent.register', ($, e) => (registered.push(String((e as { name: string }).name)), { value: { agent: 'groundwork-mods:x' } }) as never)
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 200000, percent: 12 }, rateLimits: [] } }) as never)
  on('config.list', () => ({ value: [] }))
  on('env.get', ($, e) => ({ value: (e as { name: string }).name === 'HOME' ? home : undefined }) as never)
  on('command.register', () => ({ value: {} }) as never)
  on('ui.open', () => ({ value: { isPlaced: true } }) as never)
  on('fs.exists', ($, e) => ({ value: norm((e as { path: string }).path) in files || POLICY_RX.test(norm((e as { path: string }).path)) }))
  on('fs.read', ($, e) => ({ value: POLICY_RX.test(norm((e as { path: string }).path)) ? policyText() : (files[norm((e as { path: string }).path)] ?? '') }))
  on('fs.write', ($, e) => {
    const w = e as { path: string; text: string }
    files[norm(w.path)] = w.text
    return { value: undefined }
  })
  on('fs.list', ($, e) => {
    const dir = norm((e as { path: string }).path) + '/'
    const names = Object.keys(files).filter(p => p.startsWith(dir) && !p.slice(dir.length).includes('/'))
    return { value: names.map(p => ({ name: p.slice(dir.length), kind: 'file', size: 1, mtimeMs: 0, isLink: false })) } as never
  })
  on('process.run', ($, e) => {
    const line = ((e as { argv: string[] }).argv ?? []).join(' ')
    runs.push(line)
    inits.push({ argv: line, env: (e as { init?: { env?: Record<string, string> } }).init?.env })
    const hit = gitTable.find(([re]) => re.test(line))
    // A table answer that starts with EXIT2 is a failed command (exit 2), THROW is a run that throws; no match is exit 1
    // with no output. That is also real git's answer for a clean repo's config check: the pattern names neither
    // core.fsmonitor nor core.hooksPath, so nothing matches and git exits 1 with empty output.
    const failed = !!hit && hit[1].startsWith('EXIT2')
    if (hit && hit[1].startsWith('THROW')) return { deny: 'spawn failed' } as never
    const raw = { stdout: hit && !failed ? hit[1] : '', exitCode: failed ? 2 : hit ? 0 : 1 }
    // The trust check answers as the real host's git does: its own command-scope pins after the repo's lines.
    const r = / config --show-scope --get-regexp /.test(line) ? underHost(raw.stdout, raw.exitCode) : raw
    return { value: { exitCode: r.exitCode, stdout: r.stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } } as never
  })
  on('ui.toast', ($, e) => (toasts.push(e), { value: undefined }) as never)
  on('ui.notify', () => ({ value: undefined }) as never)
  on('ui.render', () => ({ type: 'engine', ref: 0 }) as never)
  on('command.run', () => ({}) as never)
  on('session.start', ($, e) => ({ cwd: (e as { cwd: string }).cwd }))
}

describe('Threads', () => {
  test('/thread asks Claude to start a thread agent and the Threads tab shows it', ON, async ($, on) => {
    mock.store(on)
    const spawned: unknown[] = []
    const toasts: unknown[] = []
    const agents: unknown[] = []
    const clock = mock.clock(on, { now: NOW })
    const registered: string[] = []
    session(on, {}, agents, [], toasts, registered)
    on('prompt.submit', ($, e) => {
      spawned.push(e)
      return { text: (e as { text: string }).text } as never
    })
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'thread', args: 'Add a dark mode toggle to settings' } as never)
    await clock.advance(10)
    expect(spawned).toHaveLength(1)
    const ask = String((spawned[0] as { text: string }).text)
    expect(ask).toContain('groundwork-mods:thread')
    expect(ask).toContain('"Add a dark mode toggle to settings"')
    expect(registered).toEqual(expect.arrayContaining(['thread', 'reviewer']))
    expect(ask).toContain('Add a dark mode toggle to settings')
    agents.push({ id: 't9', name: 'thread', description: 'Add a dark mode toggle to settings', type: 'groundwork-mods:thread', status: 'running' })
    await clock.advance(4100)
    for (const surface of ['terminal', 'mobile'] as const) {
      const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface, component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(60) } as never)
      expect(await ui.find({ key: 'thread-t9' })).toBeDefined()
      await ui.press({ key: 'thread-t9' })
      expect(await ui.find({ type: 'Text', text: /ADD A DARK MODE TOGGLE/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /toggle to settings/ })).toBeDefined()
      expect((await ui.find({ key: 'thread-msg' })) === undefined).toBe(surface === 'mobile')
      await ui.press({ key: 'back' })
      expect(await ui.find({ type: 'Text', text: /THIS SESSION/ })).toBeDefined()
      await ui.unmount()
    }
  })

  test('a thread in a worktree shows its diff, its checks and that it is ready', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const agents = [{ id: 'a1', name: 'fix settled', description: 'Keep settled responses visible', type: 'groundwork-mods:thread', status: 'running', spawnedBy: 'groundwork-mods' }]
    const files: Record<string, string> = {}
    const asks: string[] = []
    const runs: string[] = []
    const inits: { argv: string; env?: Record<string, string> }[] = []
    on('prompt.submit', ($, e) => (asks.push(String((e as { text: string }).text)), { text: (e as { text: string }).text }) as never)
    session(on, files, agents, [
      [/-C \/work\/siteoffice rev-parse --show-toplevel$/, '/work/siteoffice\n'],
      [/-C \/work\/siteoffice\/\.claude\/worktrees\/agent-a1\/src rev-parse --show-toplevel --abbrev-ref HEAD/, '/work/siteoffice/.claude/worktrees/agent-a1\nthread-a1\n'],
      [/-C \/work\/siteoffice rev-parse HEAD/, 'abc\n'],
      [/merge-base HEAD abc/, 'abc\n'],
      [/diff --no-ext-diff --no-textconv --ignore-submodules=all --numstat abc/, '2\t1\tsrc/thread.ts\n'],
      [/ls-files --others/, ''],
      [/diff --no-ext-diff --no-textconv --ignore-submodules=all --no-color -U3 abc/, DIFF],
    ], [], [], runs, undefined, inits)
    on('tool.call', () => ({ result: { stdout: '', stderr: '', interrupted: false } }) as never)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    await $.tool.call({ tool: 'Edit', file_path: '/work/siteoffice/.claude/worktrees/agent-a1/src/thread.ts', old_string: 'a', new_string: 'b', agentId: 'a1' } as never)
    await $.tool.call({ tool: 'Bash', command: 'npx vitest run', agentId: 'a1' } as never)
    await clock.advance(10)
    agents[0] = { ...agents[0], status: 'completed' } as (typeof agents)[number]
    await clock.advance(4100)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(140) } as never)
    // The finished thread waits under NEEDS YOU; its one action opens its page.
    expect(JSON.stringify(await ui.drawn())).toContain('finished')
    expect(await ui.find({ type: 'Text', text: /ready for you/ })).toBeDefined()
    await ui.press({ key: 'thread-a1' })
    await clock.advance(10)
    expect(await ui.find({ type: 'Text', text: /✓ tests/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /src\/thread\.ts/ })).toBeDefined()
    expect(await ui.find({ type: 'Code' })).toBeDefined()
    await ui.press({ key: 'thread-review' })
    expect(files['/home/w/.claude/groundwork-fleet/reviews/a1.diff']).toContain('+const c = 4')
    expect(asks.some(a => a.includes('groundwork-mods:reviewer') && a.includes('"review a1"'))).toBe(true)
    // The Fleet's calls all name a folder with -C; the Doctor's own `git --version` is not the Fleet's.
    const gitRuns = runs.filter(r => r.startsWith('git ') && r.includes(' -C '))
    expect(gitRuns.length).toBeGreaterThan(0)
    for (const r of gitRuns) expect(r).toMatch(/^git -c core\.fsmonitor=false -c core\.hooksPath=(NUL|\/dev\/null) /)
    for (const r of gitRuns.filter(r => / diff /.test(r))) expect(r).toContain(' diff --no-ext-diff --no-textconv --ignore-submodules=all ')
    for (const kind of ['rev-parse', 'merge-base', 'diff --no-ext-diff --no-textconv --ignore-submodules=all --numstat', 'ls-files', 'diff --no-ext-diff --no-textconv --ignore-submodules=all --no-color']) {
      expect(gitRuns.some(r => r.includes(' ' + kind))).toBe(true)
    }
    const gh = inits.find(i => i.argv.startsWith('gh pr view'))
    expect(gh?.env?.GIT_CONFIG_COUNT).toBe('2')
    expect(gh?.env?.GIT_CONFIG_KEY_0).toBe('core.fsmonitor')
    expect(gh?.env?.GIT_CONFIG_KEY_1).toBe('core.hooksPath')
    for (const i of inits.filter(i => i.argv.startsWith('git ') && i.argv.includes(' -C '))) expect(i.env?.GIT_CONFIG_KEY_0).toBe('core.fsmonitor')
    await ui.unmount()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await band.find({ type: 'Text', text: /1 thread ready for you/ })).toBeDefined()
    await band.unmount()
  })
})

describe('Fleet', () => {
  test('a question in another session shows in the Fleet pane, opens there and takes a reply', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const day = '2026-10-19'
    const dir = '/home/w/.claude/groundwork-fleet/' + day
    const question = { key: 's-other|ag7|question|' + NOW, sessionId: 's-other', project: 'shop', agentId: 'ag7', agent: 'cart-fix', kind: 'question', text: 'Keep the old discount rule or drop it?', at: NOW }
    const files: Record<string, string> = {
      [dir + '/s-other.json']: JSON.stringify({
        v: 1, sessionId: 's-other', project: 'shop', cwd: TMP + '\\shop', state: 'waiting', running: 1, questions: 1,
        threads: { needsYou: 0, working: 0 }, ask: 'ship the cart fix', answer: '', at: NOW, git: { branch: 'work/cart', changed: 3, unshared: 1 },
        agents: [{ id: 'ag7', name: 'cart-fix', status: 'waiting', doing: '' }], attention: [question],
      }),
    }
    const sent: unknown[] = []
    session(on, files)
    on('session.send', ($, e) => {
      sent.push(e)
      return { isDelivered: true } as never
    })
    on('ui.copy', () => ({ value: { isCopied: true } }) as never)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const mine = JSON.parse(files[dir + '/s-me.json'] ?? '{}') as FleetCard
    expect(mine.sessionId).toBe('s-me')
    expect(Array.isArray(mine.attention)).toBe(true)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await band.find({ type: 'Text', text: /1 waiting in other sessions/ })).toBeDefined()
    expect(await band.find({ key: 'fleet' })).toBeDefined()
    await band.unmount()
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: { ...PANE_PROPS(100), title: 'Fleet' } } as never)
    expect(await ui.find({ type: 'Text', text: /NEEDS YOU · 1/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Keep the old discount rule/ })).toBeDefined()
    await ui.press({ key: 'attb-' + safeKey(question.key) })
    // Open shows the full text and where it is; a question clears itself once answered.
    expect(await ui.find({ type: 'Text', text: /Where: the tab named shop/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /clears itself once answered/ })).toBeDefined()
    expect(await ui.find({ key: 'fleet-seen' })).toBeUndefined()
    expect(await ui.find({ key: 'fleet-msg' })).toBeUndefined()
    expect(await ui.find({ key: 'fleet-copy' })).toBeUndefined()
    await ui.press({ key: 'cardb-s-other' })
    expect(await ui.find({ type: 'Text', text: /You asked: ship the cart fix/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /cart-fix/ })).toBeDefined()
    await ui.unmount()
  })

  test('opening an item in another session writes a request that session acts on', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const views: unknown[] = []
    const files: Record<string, string> = { '/home/w/.claude/groundwork-fleet/requests/s-me.json': JSON.stringify({ agentId: 'ag1', agent: 'reviewer', from: 'shop', at: NOW + 1000 }) }
    session(on, files)
    on('ui.view', ($, e) => (views.push(e), { value: {} }) as never)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    expect(views[0]).toMatchObject({ agentId: 'ag1' })
    await clock.advance(4100)
    expect(views).toHaveLength(1)
  })

})

describe('Fleet one click', () => {
  test('the icon on a NEEDS YOU row pings the other session in one press', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const dir = '/home/w/.claude/groundwork-fleet/2026-10-19'
    const q = { key: 's-other|main|question|' + NOW, sessionId: 's-other', project: 'shop', agentId: '', agent: 'main thread', kind: 'question', text: 'Cats or dogs?', at: NOW }
    const files: Record<string, string> = {
      [dir + '/s-other.json']: JSON.stringify({ v: 1, sessionId: 's-other', project: 'shop', cwd: '/w/shop', state: 'waiting', running: 0, questions: 1, threads: { needsYou: 0, working: 0 }, ask: '', answer: '', at: NOW, agents: [], attention: [q] }),
    }
    session(on, files)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(100) } as never)
    await ui.press({ key: 'act-' + safeKey(q.key) })
    expect(JSON.parse(files['/home/w/.claude/groundwork-fleet/requests/s-other.json'] ?? '{}')).toMatchObject({ agentId: '', agent: 'main thread' })
    // A ping does not clear the question: it stays until answered there.
    expect(await ui.find({ type: 'Text', text: /Cats or dogs/ })).toBeDefined()
    await ui.unmount()
  })
})

describe('Fleet notices', () => {
  test('a request still tells the person when a question dialog refuses the view switch', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const toasts: unknown[] = []
    const files: Record<string, string> = { '/home/w/.claude/groundwork-fleet/requests/s-me.json': JSON.stringify({ agentId: '', agent: 'main thread', from: 'office', at: NOW + 1000 }) }
    session(on, files, [], [], toasts)
    on('ui.view', () => {
      throw new Error('a dialog holds the screen')
    })
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    expect(JSON.stringify(toasts)).toContain('office asks you to look at main thread here')
  })

})

describe('Thread root detection', () => {
  const THREAD = { id: 'a1', name: 'fix settled', description: 'Keep settled responses visible', type: 'groundwork-mods:thread', status: 'running', spawnedBy: 'groundwork-mods' }
  const TABLE = (wt: string): [RegExp, string][] => [
    [/-C \/work\/siteoffice rev-parse --show-toplevel$/, '/work/siteoffice\n'],
    [new RegExp('-C ' + wt.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&') + '/src rev-parse --show-toplevel --abbrev-ref HEAD'), wt + '\nthread-a1\n'],
    [/-C \/work\/siteoffice rev-parse HEAD/, 'abc\n'],
    [/merge-base HEAD abc/, 'abc\n'],
  ]
  async function run($: { session: { start: (x: never) => Promise<unknown> }; tool: { call: (x: never) => Promise<unknown> } }, on: On, agents: unknown[], table: [RegExp, string][], tool: Record<string, unknown>, inner: () => unknown, links: Record<string, string> = {}) {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const runs: string[] = []
    session(on, {}, agents, table, [], [], runs, undefined, [], links)
    on('tool.call', inner as never)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true } as never)
    await clock.advance(4100)
    await $.tool.call(tool as never)
    await clock.advance(10)
    return runs
  }
  const OK = () => ({ result: { stdout: '', stderr: '', interrupted: false } })

  test('a folder outside .claude/worktrees is never run as a thread root', ON, async ($, on) => {
    const runs = await run($, on, [THREAD], TABLE('/work/wt'), { tool: 'Edit', file_path: '/work/wt/src/thread.ts', old_string: 'a', new_string: 'b', agentId: 'a1' }, OK)
    expect(runs.some(r => r.includes('-C /work/wt'))).toBe(false)
  })
  test('an agent that is not a thread gets no root detection', ON, async ($, on) => {
    const agents = [{ id: 'x1', name: 'helper', description: 'Look around', type: 'general-purpose', status: 'running' }]
    const runs = await run($, on, agents, TABLE('/work/siteoffice/.claude/worktrees/agent-x1'), { tool: 'Edit', file_path: '/work/siteoffice/.claude/worktrees/agent-x1/src/a.ts', old_string: 'a', new_string: 'b', agentId: 'x1' }, OK)
    expect(runs.some(r => r.includes('worktrees/agent-x1'))).toBe(false)
  })
  test('a failed tool call does not detect a root', ON, async ($, on) => {
    const runs = await run($, on, [THREAD], TABLE('/work/siteoffice/.claude/worktrees/agent-a1'), { tool: 'Edit', file_path: '/work/siteoffice/.claude/worktrees/agent-a1/src/thread.ts', old_string: 'a', new_string: 'b', agentId: 'a1' }, () => ({ isError: true, result: {} }))
    expect(runs.some(r => r.includes('worktrees/agent-a1/src'))).toBe(false)
  })
  test('a folder under .claude/worktrees that really lands outside is refused', ON, async ($, on) => {
    const wt = '/work/siteoffice/.claude/worktrees/agent-a1'
    const runs = await run($, on, [THREAD], TABLE(wt), { tool: 'Edit', file_path: wt + '/src/thread.ts', old_string: 'a', new_string: 'b', agentId: 'a1' }, OK, { [wt + '/src']: '/work/evil/src' })
    expect(runs.some(r => r.includes('worktrees/agent-a1/src'))).toBe(false)
  })
})

describe('Threads worktree folder', () => {
  test('a thread that only reads still shows its worktree, found by its folder', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const agents = [{ id: 'b2', name: 'look', description: 'Check the comment line', type: 'groundwork-mods:thread', status: 'completed' }]
    const files: Record<string, string> = { '/work/siteoffice/.claude/worktrees/agent-b2': '' }
    session(on, files, agents, [
      [/-C \/work\/siteoffice rev-parse --show-toplevel$/, '/work/siteoffice\n'],
      [/-C \/work\/siteoffice\/\.claude\/worktrees\/agent-b2 rev-parse --show-toplevel --abbrev-ref HEAD/, '/work/siteoffice/.claude/worktrees/agent-b2\nworktree-agent-b2\n'],
      [/-C \/work\/siteoffice rev-parse HEAD/, 'abc\n'],
      [/merge-base HEAD abc/, 'abc\n'],
      [/diff --no-ext-diff --no-textconv --ignore-submodules=all --numstat abc/, ''],
      [/ls-files --others/, ''],
    ])
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    await clock.advance(4100)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(140) } as never)
    await ui.press({ key: 'thread-b2' })
    expect(await ui.find({ type: 'Text', text: /worktree-agent-b2/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Not seen in a worktree/ })).toBeUndefined()
    await ui.unmount()
  })
})

describe('Fleet git settings that run programs', () => {
  const PLAIN_LINE = /git settings that run programs/
  async function openThread($: any, on: On, id: string, config: [RegExp, string][]) {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const wt = '/work/siteoffice/.claude/worktrees/agent-' + id
    const agents = [{ id, name: 'look', description: 'Check the line', type: 'groundwork-mods:thread', status: 'completed' }]
    const runs: string[] = []
    const table: [RegExp, string][] = [
      [/-C \/work\/siteoffice rev-parse --show-toplevel$/, '/work/siteoffice\n'],
      [new RegExp('-C ' + wt.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&') + ' rev-parse --show-toplevel --abbrev-ref HEAD'), wt + '\nthread-' + id + '\n'],
      [/-C \/work\/siteoffice rev-parse HEAD/, 'abc\n'],
      [/merge-base HEAD abc/, 'abc\n'],
      [/diff --no-ext-diff --no-textconv --ignore-submodules=all --numstat abc/, '2\t1\tsrc/thread.ts\n'],
      [/ls-files --others/, ''],
      [/diff --no-ext-diff --no-textconv --ignore-submodules=all --no-color -U3 abc/, DIFF],
      ...config,
    ]
    session(on, { [wt]: '' }, agents, table, [], [], runs)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    await clock.advance(4100)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(140) } as never)
    await ui.press({ key: 'thread-' + id })
    await clock.advance(10)
    return { runs, ui, wt, table, clock }
  }
  const treeRuns = (runs: string[], wt: string) => runs.filter(r => r.includes('-C ' + wt + ' '))
  const CHECK = ' config --show-scope --get-regexp '
  const CHECK_RX = /config --show-scope --get-regexp/
  const gitRuns = (runs: string[], wt: string) => treeRuns(runs, wt).filter(r => r.includes(' diff ') || r.includes(' ls-files '))

  test('a clean repo shows its diff as before, after a hardened config check', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'g1', [])
    const mine = treeRuns(runs, wt)
    const checks = mine.filter(r => r.includes(CHECK))
    expect(checks.length).toBeGreaterThan(0)
    expect(checks[0]).toMatch(/^git -c core\.fsmonitor=false -c core\.hooksPath=(NUL|\/dev\/null) -C /)
    expect(mine.some(r => r.includes(' ls-files '))).toBe(true)
    expect(mine.some(r => r.includes(' diff '))).toBe(true)
    expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /src\/thread\.ts/ })).toBeDefined()
    await ui.unmount()
  })

  test('the config check sends a pattern that names every program-valued key family', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'rx1', [])
    const check = treeRuns(runs, wt).find(r => r.includes(CHECK)) ?? ''
    for (const key of ['clean|smudge|process', 'sshcommand', 'pager', 'editor', 'askpass', 'command|textconv', 'credential', 'include', 'includeif', 'extensions\\.worktreeconfig']) {
      expect(check).toContain(key)
    }
    await ui.unmount()
  })

  const FAMILIES: [string, string][] = [
    ['filter clean', 'local\tfilter.lfs.clean sh -c evil'],
    ['core.sshCommand', 'local\tcore.sshcommand ssh -o ProxyCommand=evil'],
    ['diff textconv', 'local\tdiff.doc.textconv evil'],
    ['include.path', 'local\tinclude.path ../evil.cfg'],
    ['worktree scope', 'worktree\tcore.pager evil'],
    ['command scope, core.sshcommand', 'command\tcore.sshcommand evil'],
    ['command scope, core.pager', 'command\tcore.pager evil'],
    ['unparseable scope', 'weird\tcore.pager evil'],
    ['extensions.worktreeconfig true', 'local\textensions.worktreeconfig true'],
  ]
  FAMILIES.forEach(([name, out], i) => {
    test('a folder with ' + name + ' shows no diff, says why, and git diff and ls-files never run there', ON, async ($, on) => {
      const { runs, ui, wt } = await openThread($, on, 'u' + i, [[CHECK_RX, out + '\n']])
      const mine = treeRuns(runs, wt)
      expect(mine.some(r => r.includes(CHECK))).toBe(true)
      expect(mine.some(r => r.includes(' diff ') || r.includes(' ls-files '))).toBe(false)
      expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /src\/thread\.ts/ })).toBeUndefined()
      await ui.unmount()
    })
  })

  test('a config check that throws is untrusted', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'f2', [[CHECK_RX, 'THROW']])
    expect(treeRuns(runs, wt).some(r => r.includes(' diff ') || r.includes(' ls-files '))).toBe(false)
    expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeDefined()
    await ui.unmount()
  })

  test('a failing config check is untrusted', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'f1', [[CHECK_RX, 'EXIT2']])
    expect(treeRuns(runs, wt).some(r => r.includes(' diff ') || r.includes(' ls-files '))).toBe(false)
    expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeDefined()
    await ui.unmount()
  })

  test('a global-scope filter.lfs.clean (Git LFS) and a system-scope pager stay trusted', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'l1', [[CHECK_RX, 'global\tfilter.lfs.clean\tgit-lfs clean -- %f\nsystem\tcore.pager less\n']])
    expect(gitRuns(runs, wt).length).toBeGreaterThan(0)
    expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeUndefined()
    await ui.unmount()
  })

  test('extensions.worktreeconfig set to false in local scope stays trusted', ON, async ($, on) => {
    const { runs, ui, wt } = await openThread($, on, 'l2', [[CHECK_RX, 'local\textensions.worktreeconfig false\n']])
    expect(gitRuns(runs, wt).length).toBeGreaterThan(0)
    await ui.unmount()
  })

  test('a trusted verdict is not cached: the check runs again on each refresh, once within one', ON, async ($, on) => {
    const { runs, ui, wt, clock } = await openThread($, on, 'c1', [])
    const count = () => treeRuns(runs, wt).filter(r => r.includes(CHECK)).length
    const before = count()
    expect(before).toBeGreaterThan(0)
    const diffsBefore = treeRuns(runs, wt).filter(r => r.includes(' diff --no-ext-diff --no-textconv --ignore-submodules=all --no-color')).length
    await ui.press({ key: 'back' })
    await ui.press({ key: 'thread-c1' })
    await clock.advance(10)
    // One pick is two refresh calls (thread, then pull request): two checks, not one per git command.
    expect(count() - before).toBe(2)
    expect(treeRuns(runs, wt).filter(r => r.includes(' diff --no-ext-diff --no-textconv --ignore-submodules=all --no-color')).length).toBeGreaterThan(diffsBefore)
    await ui.unmount()
  })

  test('a folder clean on the first refresh and local-scope dirty on the second turns untrusted: no diff, no ls-files, no gh', ON, async ($, on) => {
    const { runs, ui, wt, table, clock } = await openThread($, on, 'd1', [])
    expect(gitRuns(runs, wt).length).toBeGreaterThan(0)
    table.unshift([CHECK_RX, 'local\tdiff.evil.textconv evil\n'])
    const mark = runs.length
    await ui.press({ key: 'back' })
    await ui.press({ key: 'thread-d1' })
    await clock.advance(10)
    const after = runs.slice(mark)
    expect(after.some(r => r.includes(CHECK))).toBe(true)
    expect(after.some(r => r.includes(' diff ') || r.includes(' ls-files ') || r.startsWith('gh ') || r.includes(' pr view'))).toBe(false)
    expect(await ui.find({ type: 'Text', text: PLAIN_LINE })).toBeDefined()
    await ui.unmount()
  })
})

describe('Fleet and agent view', () => {
  test('a background session that waits shows in NEEDS YOU with its attach command', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const av = JSON.stringify([{ cwd: '/w/reports', kind: 'background', id: 'bg7', state: 'blocked', status: 'waiting', waitingFor: 'permission prompt', sessionId: 's-bg', startedAt: 5 }])
    session(on, {}, [], [[/^claude agents --json$/, av]])
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const ui = await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(100) } as never)
    expect(await ui.find({ type: 'Text', text: /waits for permission prompt/ })).toBeDefined()
    expect(JSON.stringify(await ui.drawn())).toContain('claude attach bg7')
    await ui.unmount()
  })
})

describe('band icons', () => {
  test('the icons come right after the 0N label, before the counts', ON, async ($, on) => {
    mock.clock(on)
    mock.store(on)
    on('ui.render', ($, e) => ({ type: 'engine', ref: 0 }) as never)
    const ui = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn.indexOf('chip-fleet')).toBeGreaterThan(-1)
    expect(drawn.indexOf('chip-fleet')).toBeLessThan(drawn.indexOf('no agents'))
    await ui.unmount()
  })
})

describe('Fleet background shells', () => {
  const paneOf = ($: Parameters<Parameters<typeof test>[1]>[0]) =>
    $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(120) } as never)

  test('a background shell shows under This session, is reconciled at Stop and ends on its notification', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    session(on, {})
    on('classic.Stop', () => ({}))
    on('tool.call', ($, e) => ({ result: { stdout: '', stderr: '', backgroundTaskId: String((e as { command?: string }).command).startsWith('npm') ? 'bx1' : 'bx2' } }) as never)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await $.tool.call({ tool: 'Bash', command: 'npm run dev', run_in_background: true } as never)
    await $.tool.call({ tool: 'PowerShell', command: 'Start-Sleep 99' } as never)

    let ui = await paneOf($)
    expect(await ui.find({ type: 'Text', text: /npm run dev.*running/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Start-Sleep 99.*running/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /no live list/ })).toBeDefined()
    await ui.unmount()

    // Turn end: the first shell is gone from the list, the second still runs,
    // and a third, started with Ctrl+B before the mod looked, appears.
    await $.classic.Stop({
      stop_hook_active: false,
      background_tasks: [
        { id: 'bx2', type: 'shell', status: 'running', description: 'sleep', command: 'Start-Sleep 99' },
        { id: 'bx3', type: 'shell', status: 'running', description: 'serve', command: 'npx serve' },
      ],
    } as never)
    ui = await paneOf($)
    expect(await ui.find({ type: 'Text', text: /npm run dev.*completed/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Start-Sleep 99.*running/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /npx serve.*running/ })).toBeDefined()
    await ui.unmount()

    // The second shell's own notification: drawn once, filed by the next tick.
    const note = await $.ui.mount({
      plugin: 'groundwork-mods',
      surface: 'terminal',
      component: 'UserMessage',
      props: { text: 'Background command failed', origin: { kind: 'task-notification' }, isExpanded: false, task: { id: 'bx2', status: 'failed' } },
    } as never)
    await note.unmount()
    await clock.advance(4100)
    ui = await paneOf($)
    expect(await ui.find({ type: 'Text', text: /Start-Sleep 99.*failed/ })).toBeDefined()
    await ui.unmount()

    // Ten minutes after they ended, the finished shells leave the list.
    await clock.advance(11 * 60000)
    ui = await paneOf($)
    expect(await ui.find({ type: 'Text', text: /npm run dev/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /Start-Sleep 99/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /npx serve.*running/ })).toBeDefined()
    await ui.unmount()
  })
})

describe('Plain Mode overhead', () => {
  const LONG = 'x'.repeat(1300)
  const answerTurn = ($: Parameters<Parameters<typeof test>[1]>[0]) => $.turn.complete({ reason: 'answer', answer: LONG, durationMs: 1 } as never)
  const wire = (on: On, files: Record<string, string>, calls: unknown[], toasts: unknown[], text = 'Done, nothing to decide.') => {
    session(on, files, [], [], toasts)
    on('model.complete', ($, e) => (calls.push(e), { value: { isAnswered: true, text, usage: USAGE } }) as never)
    on('session.append', ($, e, next) => next(e))
    on('turn.complete', () => ({ text: '' }) as never)
    on('tool.call', () => ({ result: {}, text: 'D1' }) as never)
  }
  const file = '/home/w/.burnmon/overhead/' + dayOf(NOW) + '.ndjson'

  test('a So what writes one correct line and the band label shows the total of today', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on, { 'fleet.plainOn': true })
    const files: Record<string, string> = {}
    const calls: unknown[] = []
    wire(on, files, calls, [])
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await answerTurn($)
    await clock.advance(10)
    expect(calls).toHaveLength(1)
    const lines = (files[file] ?? '').trim().split('\n').map(l => JSON.parse(l))
    expect(lines).toHaveLength(1)
    expect(lines[0]).toEqual({ at: new Date(NOW).toISOString(), session: 's-me', plugin: 'groundwork-mods', switch: 'plain-sowhat', model: 'haiku', input: 600, output: 80, cache_read: 0, cache_creation: 0 })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await band.find({ type: 'Text', text: /Plain Mode: 680 tokens today of 50000/ })).toBeDefined()
    await band.unmount()
  })

  test('a question set writes one correct line', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on, { 'fleet.plainOn': true })
    const files: Record<string, string> = {}
    wire(on, files, [], [], '{"q":[["Keeps data in a small database."]]}')
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await $.tool.call({ tool: 'AskUserQuestion', questions: [{ question: 'Which store?', options: [{ label: 'D1', description: '' }] }] } as never)
    await clock.advance(10)
    const lines = (files[file] ?? '').trim().split('\n').map(l => JSON.parse(l))
    expect(lines).toHaveLength(1)
    expect(lines[0]).toMatchObject({ switch: 'plain-questions', model: 'haiku', input: 600, output: 80, plugin: 'groundwork-mods' })
  })

  test('Plain Mode is off on a fresh store: no model call and the label says off', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const files: Record<string, string> = {}
    const calls: unknown[] = []
    wire(on, files, calls, [])
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await answerTurn($)
    await answerTurn($)
    await clock.advance(10)
    expect(calls).toHaveLength(0)
    expect(files[file]).toBeUndefined()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as never)
    expect(await band.find({ type: 'Text', text: /Plain Mode: 0 tokens today of 50000 · off · \/plain/ })).toBeDefined()
    await band.unmount()
  })

  test('the daily cap stops the calls and says so once', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on, { 'fleet.plainOn': true, 'fleet.plainSpend': { day: dayOf(NOW), tokens: 49500 } })
    const files: Record<string, string> = {}
    const calls: unknown[] = []
    const toasts: unknown[] = []
    wire(on, files, calls, toasts)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await answerTurn($)
    await clock.advance(10)
    expect(calls).toHaveLength(1)
    await answerTurn($)
    await answerTurn($)
    await clock.advance(10)
    expect(calls).toHaveLength(1)
    expect(toasts.filter(t => /Plain Mode paused/.test(JSON.stringify(t)))).toHaveLength(1)
    expect((files[file] ?? '').trim().split('\n')).toHaveLength(1)
  })
})

describe('Stop hook trace', () => {
  test('with the Black Box on, each Stop writes one line saying it ran', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on, { 'fleet.bbOn': true })
    const files: Record<string, string> = {}
    session(on, files)
    on('classic.Stop', () => ({}))
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await $.classic.Stop({
      stop_hook_active: false,
      background_tasks: [
        { id: 'bx1', type: 'shell', status: 'running', description: 'a', command: 'npm run dev' },
        { id: 'ag1', type: 'subagent', status: 'running', description: 'b' },
      ],
    } as never)
    await clock.advance(4100)
    const path = Object.keys(files).find(p => p.includes('groundwork-blackbox'))
    expect(path).toBeDefined()
    const lines = (files[path as string] ?? '').trim().split('\n').map(l => JSON.parse(l))
    expect(lines.some(l => l.kind === 'stop' && l.tasks === 2 && l.shells === 1)).toBe(true)
  })
})

describe('Fleet stop button', () => {
  type Ctx = Parameters<Parameters<typeof test>[1]>[0]
  const BG = (over: Record<string, unknown> = {}) => ({ cwd: '/w/reports', kind: 'background', id: 'bg7', state: 'working', status: 'busy', sessionId: 's-bg', name: 'reports', startedAt: 5, ...over })
  const paneOf = ($: Ctx) => $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(120) } as never)
  const OTHER = 'stop-o-' + safeKey('bg7')
  const stops = (runs: string[]) => runs.filter(r => r.startsWith('claude stop'))

  test('two presses stop it and the row leaves; one press does nothing after 5 seconds', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const toasts: unknown[] = []
    const runs: string[] = []
    const table: [RegExp, string][] = [[/^claude agents --json$/, JSON.stringify([BG(), { cwd: '/w/me', kind: 'interactive', id: 'ix1', sessionId: 's-int', status: 'idle' }])], [/^claude stop bg7$/, 'stopped bg7\n']]
    session(on, {}, [], table, toasts, [], runs)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)

    let ui = await paneOf($)
    expect(await ui.find({ key: OTHER })).toBeDefined()
    // Never on an interactive session.
    expect(await ui.find({ key: 'stop-o-' + safeKey('ix1') })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /press Stop again/ })).toBeUndefined()
    // First press: the exact command and the 5 second window, nothing run.
    await ui.press({ key: OTHER })
    expect(await ui.find({ type: 'Text', text: /claude stop bg7.*press Stop again within 5 seconds/ })).toBeDefined()
    expect(stops(runs)).toHaveLength(0)
    await ui.unmount()

    // After 5 seconds the window is gone, and a press then only arms it again.
    await clock.advance(5100)
    ui = await paneOf($)
    expect(await ui.find({ type: 'Text', text: /press Stop again/ })).toBeUndefined()
    await ui.press({ key: OTHER })
    expect(stops(runs)).toHaveLength(0)
    await ui.unmount()
    await clock.advance(5100)
    expect(stops(runs)).toHaveLength(0)

    // Two presses inside the window run the official command, toast its result and refresh the view.
    ui = await paneOf($)
    await ui.press({ key: OTHER })
    table[0] = [/^claude agents --json$/, '[]']
    await ui.press({ key: OTHER })
    expect(stops(runs)).toEqual(['claude stop bg7'])
    expect(JSON.stringify(toasts)).toContain('stopped bg7')
    await ui.unmount()
    ui = await paneOf($)
    expect(await ui.find({ key: OTHER })).toBeUndefined()
    await ui.unmount()
  })

  test('a failure toasts the first line and leaves the row as it was', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const toasts: unknown[] = []
    const runs: string[] = []
    session(on, {}, [], [[/^claude agents --json$/, JSON.stringify([BG()])]], toasts, [], runs)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const ui = await paneOf($)
    await ui.press({ key: OTHER })
    await ui.press({ key: OTHER })
    expect(stops(runs)).toEqual(['claude stop bg7'])
    expect(JSON.stringify(toasts)).toContain('Could not stop bg7')
    expect(await ui.find({ key: OTHER })).toBeDefined()
    await ui.unmount()
  })

  test('a pinned session says so, and the Black Box records who stopped it', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on, { 'fleet.bbOn': true })
    const files: Record<string, string> = {}
    const runs: string[] = []
    session(on, files, [], [[/^claude agents --json$/, JSON.stringify([BG({ pinned: true })])], [/^claude stop bg7$/, 'stopped bg7\n']], [], [], runs)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const ui = await paneOf($)
    await ui.press({ key: OTHER })
    expect(await ui.find({ type: 'Text', text: /pinned/ })).toBeDefined()
    await ui.press({ key: OTHER })
    expect(stops(runs)).toEqual(['claude stop bg7'])
    await ui.unmount()
    await clock.advance(4100)
    const path = Object.keys(files).find(f => f.includes('groundwork-blackbox'))
    const lines = (files[path as string] ?? '').trim().split('\n').map(l => JSON.parse(l))
    expect(lines.some(l => l.to === 'stopped' && l.id === 'bg7' && l.by === 'person')).toBe(true)
  })

  test('a background session that waits on you also has the button under NEEDS YOU', ON, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const runs: string[] = []
    const list = JSON.stringify([BG({ state: 'blocked', status: 'waiting', waitingFor: 'permission prompt' })])
    session(on, {}, [], [[/^claude agents --json$/, list], [/^claude stop bg7$/, 'stopped bg7\n']], [], [], runs)
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    const ui = await paneOf($)
    const key = 'stop-n-' + safeKey('bg7')
    expect(await ui.find({ key })).toBeDefined()
    await ui.press({ key })
    expect(await ui.find({ type: 'Text', text: /claude stop bg7/ })).toBeDefined()
    await ui.press({ key })
    expect(stops(runs)).toEqual(['claude stop bg7'])
    await ui.unmount()
  })
})

describe('Fleet and the policy', () => {
  test('with the Fleet on, the policy still decides: a write outside drafts is denied', ON, async ($, on) => {
    mock.clock(on)
    on('session.cwd', () => ({ value: 'C:/work/shop' }))
    on('fs.exists', ($, e) => ({ value: POLICY_RX.test(norm((e as { path: string }).path)) }))
    on('fs.read', ($, e) => ({ value: POLICY_RX.test(norm((e as { path: string }).path)) ? policyText({ mode: 'read-only' }) : '' }))
    on('fs.write', () => ({ value: undefined }))
    const out = await $.tool.call({ tool: 'Write', file_path: 'src/app.ts', content: 'x' } as never)
    expect(String((out as { deny?: string }).deny)).toContain('Groundwork policy')
  })
})

describe('BurnMon state in the Fleet', () => {
  test('burnmonNote names the three states plainly', async () => {
    expect(burnmonNote({ exists: true, ageSeconds: 3 })).toEqual({ state: 'running', text: 'BurnMon is running. Snapshot 3 s old.' })
    expect(burnmonNote({ exists: true, ageSeconds: 10 }).state).toBe('stopped')
    expect(burnmonNote({ exists: true, ageSeconds: 600 }).text).toBe('BurnMon is not running: its snapshot is older than 10 seconds. Start BurnMon.')
    expect(burnmonNote({ exists: true, ageSeconds: null }).text).toBe('BurnMon is not running: its snapshot could not be read. Start BurnMon.')
    expect(burnmonNote({ exists: false, ageSeconds: null }).text).toBe('BurnMon is not installed. The Fleet works without it. Get it: ' + BURNMON_RELEASES_URL)
  })

  // The snapshot sits at <home>/.burnmon/snapshot.json; the home folder is /home/w unless home is ''.
  async function openFleetWithSnapshot($: any, on: any, opts: { home?: string; snapshot: 'missing' | 'fresh' | 'old' | 'fresh-with-sessions' }) {
    const clock = mock.clock(on, { now: NOW })
    mock.store(on)
    const age = opts.snapshot === 'old' ? 600 : 3
    const extra = opts.snapshot === 'fresh-with-sessions' ? ',"sessions":[{"project":"SECRETMARK"}]' : ''
    const files: Record<string, string> = {}
    if (opts.snapshot !== 'missing') files['/home/w/.burnmon/snapshot.json'] = '{"written_at":"' + new Date(NOW - age * 1000).toISOString() + '"' + extra + '}'
    session(on, files, [], [], [], [], [], opts.home ?? '/home/w')
    await $.session.start({ cwd: '/work/siteoffice', surface: 'terminal', isInteractive: true })
    await clock.advance(4100)
    return await $.ui.mount({ plugin: 'groundwork-mods', surface: 'terminal', component: 'Pane', requestId: 'groundwork-fleet', props: PANE_PROPS(100) } as never)
  }

  test('a fresh snapshot shows BurnMon running and none of its content', ON, async ($, on) => {
    const ui = await openFleetWithSnapshot($, on, { snapshot: 'fresh-with-sessions' })
    expect(await ui.find({ type: 'Text', text: /BurnMon is running/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /SECRETMARK/ })).toBeUndefined()
    await ui.unmount()
  })
  test('an old snapshot says BurnMon is not running', ON, async ($, on) => {
    const ui = await openFleetWithSnapshot($, on, { snapshot: 'old' })
    expect(await ui.find({ type: 'Text', text: /BurnMon is not running/ })).toBeDefined()
    await ui.unmount()
  })
  test('no home folder: not installed, no throw', ON, async ($, on) => {
    const ui = await openFleetWithSnapshot($, on, { home: '', snapshot: 'fresh' })
    expect(await ui.find({ type: 'Text', text: /BurnMon is not installed/ })).toBeDefined()
    await ui.unmount()
  })
})
