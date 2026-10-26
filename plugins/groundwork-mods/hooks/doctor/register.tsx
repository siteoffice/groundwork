// Groundwork Setup Doctor: is Groundwork set up on this computer? One pane
// (/setup-doctor) with the required and the recommended checks, each with
// one copyable fix. At the start of a session it says so once a day when a
// required check fails; otherwise it stays quiet.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { chipRow, section, tips } from './chips'
import type { Els } from './chips'
import { buildReport, summary } from './checks'
import { engineFor, engineIO } from '../engine'
import type { IgnoredReason } from '../engine'
import { readBurnmon } from '../burnmon'
import type { DoctorFacts } from '../types'

const PANE = 'groundwork-doctor'

const facts = atom({ plugin: 'groundwork-mods', key: 'facts' } as const, null as DoctorFacts | null)
const isChecking = atom({ plugin: 'groundwork-mods', key: 'isChecking' } as const, false)
const isLight = atom({ plugin: 'groundwork-mods', key: 'isLight' } as const, false)
const isFullscreen = atom({ plugin: 'groundwork-mods', key: 'isFullscreen' } as const, null as boolean | null)
const checkedAt = atom({ plugin: 'groundwork-mods', key: 'checkedAt' } as const, 0)
// Which SessionStart events reached this module. The pair tells the doctor whether an earlier
// plugin passed the event on past the mods.
const sawSessionStart = atom({ plugin: 'groundwork-mods', key: 'sawSessionStart' } as const, false)
const sawClassicSessionStart = atom({ plugin: 'groundwork-mods', key: 'sawClassicSessionStart' } as const, false)

async function run($: EngineInterface, argv: string[], cwd?: string) {
  try {
    return await $.process.run(argv, { timeoutMs: 15000, ...(cwd ? { cwd } : {}), env: { GIT_TERMINAL_PROMPT: '0', GH_PROMPT_DISABLED: '1' } })
  } catch {
    return { exitCode: 1, stdout: '', stderr: '' }
  }
}

// Gathers every fact the checks need. Nothing here changes anything.
async function gather($: EngineInterface): Promise<DoctorFacts> {
  const cwd = await $.session.cwd()
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
  const isWindows = /^[a-zA-Z]:/.test(cwd) || (await $.env.get('OS')) === 'Windows_NT'
  const gitOk = (await run($, ['git', '--version'])).exitCode === 0
  const name = gitOk ? (await run($, ['git', 'config', '--global', 'user.name'])).stdout.trim() : ''
  const email = gitOk ? (await run($, ['git', 'config', '--global', 'user.email'])).stdout.trim() : ''
  const ghInstalled = (await run($, ['gh', '--version'])).exitCode === 0
  const ghAuthed = ghInstalled && (await run($, ['gh', 'auth', 'status'])).exitCode === 0
  let python = ''
  for (const c of isWindows ? [['py', '-3'], ['python'], ['python3']] : [['python3'], ['python']]) {
    if ((await run($, [...c, '--version'])).exitCode === 0) {
      python = c.join(' ')
      break
    }
  }
  const top = gitOk ? await run($, ['git', 'rev-parse', '--show-toplevel'], cwd) : { exitCode: 1, stdout: '' }
  const root = top.exitCode === 0 ? top.stdout.trim() : ''
  // siteoffice.json: at the repo root or the folder itself.
  const base = root || cwd
  const mpath = base.replace(/[\\/]+$/, '') + '/siteoffice.json'
  const manifest = { found: false, valid: false, checks: 0, engineOk: false, syncDirReason: '' as IgnoredReason }
  if (await $.fs.exists(mpath)) {
    manifest.found = true
    try {
      const j = JSON.parse(String(await $.fs.read(mpath))) as { settings?: { git?: { checks?: unknown } } }
      manifest.valid = true
      const checks = j.settings?.git?.checks
      manifest.checks = Array.isArray(checks) ? checks.length : 0
    } catch {
      manifest.valid = false
    }
    // The lane's own rule: SITEOFFICE_SYNC_DIR outside the repo, else the engine the plugin ships.
    const c = await engineFor(engineIO($), base)
    manifest.engineOk = !!c.engine
    manifest.syncDirReason = c.ignoredReason
  }
  // User settings: the plugin, the bell, the status line.
  let settings: { enabledPlugins?: Record<string, unknown>; preferredNotifChannel?: unknown; statusLine?: unknown } = {}
  const spath = home ? home.replace(/[\\/]+$/, '') + '/.claude/settings.json' : ''
  try {
    if (spath && (await $.fs.exists(spath))) settings = JSON.parse(String(await $.fs.read(spath)))
  } catch {
    settings = {}
  }
  const plugin = Object.entries(settings.enabledPlugins ?? {}).some(([k, v]) => /^groundwork@/.test(k) && v !== false)
  const term = ((await $.env.get('TERM_PROGRAM')) ?? '').toLowerCase()
  const needsBell = !(term.includes('iterm') || term.includes('ghostty') || term.includes('kitty') || (await $.env.get('KITTY_WINDOW_ID')))
  const v = await $.session.version().catch(() => ({ version: '' }))
  const fullscreen = await read($, isFullscreen)
  return {
    isWindows,
    version: v.version,
    ...(fullscreen === null ? {} : { isFullscreen: fullscreen }),
    git: { ok: gitOk, name, email },
    gh: { installed: ghInstalled, authed: ghAuthed },
    python,
    repo: { inside: !!root, root },
    manifest,
    plugin,
    hooks: { sessionStart: await read($, sawSessionStart), classicSessionStart: await read($, sawClassicSessionStart) },
    notif: typeof settings.preferredNotifChannel === 'string' ? settings.preferredNotifChannel : '',
    needsBell,
    statusLine: !!settings.statusLine,
    burnmon: await readBurnmon($, home),
  }
}

async function check($: EngineInterface) {
  if (await read($, isChecking)) return
  await update($, isChecking, () => true)
  try {
    const f = await gather($)
    const at = await $.clock.now()
    await update($, facts, () => f)
    await update($, checkedAt, () => at)
  } finally {
    await update($, isChecking, () => false)
  }
}

// Once a day at most: a toast when a required check fails.
async function nudge($: EngineInterface) {
  await check($)
  const f = await read($, facts)
  if (!f) return
  const s = summary(buildReport(f))
  if (!s.required) return
  const day = new Date(await $.clock.now()).toISOString().slice(0, 10)
  if ((await $.store.get('nudgedOn')) === day) return
  await $.store.set('nudgedOn', day)
  $.ui.toast('Setup: ' + s.required + ' thing' + (s.required > 1 ? 's' : '') + ' to fix before Groundwork works · /setup-doctor')
}

export const registerDoctor: Register = on => {
  // No matcher: every source, startup included. If this never fires while session.start does,
  // something ahead of the mods is not passing the event on.
  on('classic.SessionStart', async ($, e, next) => {
    await update($, sawClassicSessionStart, () => true)
    return next(e)
  })

  on('session.start', async ($, e, next) => {
    await update($, sawSessionStart, () => true)
    await $.command.register({ name: 'setup-doctor', description: 'Check that Groundwork is set up on this computer, with a copyable fix for each gap' })
    $.clock.after(3000, () => nudge($).catch(() => undefined))
    return next(e)
  })

  on('command.run', { command: 'setup-doctor' }, async ($, e) => {
    await update($, isFullscreen, () => e.presentation.isFullscreen)
    const theme = (await $.config.list()).find(row => row.key === 'theme')
    await update($, isLight, () => String(theme?.value ?? '').includes('light'))
    await $.ui.open({ id: PANE, title: 'Setup' })
    $.clock.after(0, () => check($).catch(() => undefined))
    return {}
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const els = $.ui.resolve(e)
    const { Box, Text } = els
    const E = els as unknown as Els
    const light = await read($, isLight)
    const accent = light ? '#0B6B5B' : '#3EDCB0'
    const cols = e.props.bodyColumns
    const f = await read($, facts)
    const busy = await read($, isChecking)
    const probes = f ? buildReport(f) : []
    const s = summary(probes)
    const recheck = [{ key: 'recheck', glyph: '↻', tip: 'Check again', onPress: () => check($) }]
    const row = (p: (typeof probes)[number]) => {
      const fixChip = {
        key: 'fix-' + p.id,
        glyph: '⧉',
        tip: 'Copy the fix',
        onPress: () => void $.ui.copy({ text: p.fix as string }).then(() => $.ui.toast('Copied: run it ' + (p.fixWhere === 'Inside Claude Code' ? 'inside Claude Code' : 'in ' + p.fixWhere))),
      }
      const grey = !p.ok && p.tone === 'grey'
      return (
      <Box key={'p-' + p.id} flexDirection="column">
        <Box flexDirection="row" columnGap={1} alignItems="flex-start">
          <Text color={p.ok ? 'success' : p.level === 'required' ? 'error' : 'warning'} dimColor={grey}>{p.ok ? '✓' : p.level === 'required' ? '✕' : grey ? '○' : '◐'}</Text>
          <Text bold={!p.ok}>{p.title}</Text>
        </Box>
        {p.note && <Text dimColor wrap="wrap">{'  ' + p.note}</Text>}
        {!p.ok && (
          <Box flexDirection="column">
            <Text dimColor wrap="wrap">{'  ' + p.why}</Text>
            {p.fix && (
              <Box flexDirection="row" columnGap={1} alignItems="flex-start">
                <Text>{' '}</Text>
                <Box flexShrink={0}>{chipRow(E, [fixChip], light)}</Box>
                <Box flexDirection="column" flexShrink={1} minWidth={0}>
                  <Text dimColor>{(p.fixWhere ?? '') + ':'}</Text>
                  <Text color={accent} wrap="wrap">{p.fix}</Text>
                </Box>
                {tips(E, [fixChip])}
              </Box>
            )}
          </Box>
        )}
      </Box>
      )
    }
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" columnGap={2} alignItems="flex-start">
          <Text>
            <Text color={accent} bold>0N SETUP</Text>
            <Text dimColor>{'  ' + (f?.repo.root || (await $.session.cwd().catch(() => '')))}</Text>
          </Text>
          <Box flexShrink={0}>{chipRow(E, recheck, light)}</Box>
          {tips(E, recheck)}
        </Box>
        <Text dimColor>
          {busy && !f
            ? 'Checking this computer…'
            : s.required
              ? s.required + ' to fix before Groundwork works' + (s.recommended ? ', ' + s.recommended + ' recommended' : '')
              : s.recommended
                ? 'Ready. ' + s.recommended + ' recommended to make it easier.'
                : 'Ready. Everything is set up.'}
        </Text>
        <Text> </Text>
        {section(E, 'REQUIRED', cols)}
        {probes.filter(p => p.level === 'required').map(row)}
        <Text> </Text>
        {section(E, 'RECOMMENDED', cols)}
        {probes.filter(p => p.level === 'recommended').map(row)}
        <Text> </Text>
        {section(E, 'OPTIONAL', cols)}
        {probes.filter(p => p.level === 'optional').map(row)}
        <Text> </Text>
        <Text dimColor>The doctor only reads: it changes nothing. Copy a fix, run it where it says, then press ↻.</Text>
      </Box>
    )
  })
}
