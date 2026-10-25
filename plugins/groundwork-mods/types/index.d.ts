/** What the Setup Doctor found on this computer, gathered once per check. */
export type DoctorFacts = {
  isWindows: boolean
  version: string
  isFullscreen?: boolean
  git: { ok: boolean; name: string; email: string }
  gh: { installed: boolean; authed: boolean }
  python: string
  repo: { inside: boolean; root: string }
  manifest: { found: boolean; valid: boolean; checks: number; engineOk: boolean }
  plugin: boolean
  /** Which SessionStart events reached these mods in this session. */
  hooks: { sessionStart: boolean; classicSessionStart: boolean }
  notif: string
  needsBell: boolean
  statusLine: boolean
  /** BurnMon's snapshot: file there, and its age from written_at (null when it cannot be read). */
  burnmon: { exists: boolean; ageSeconds: number | null }
}

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
    }
  }
}
