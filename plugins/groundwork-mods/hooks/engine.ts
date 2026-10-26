// One rule for the siteoffice-git engine, shared by the Git lane and the Setup Doctor so they
// cannot disagree. Candidates in order: SITEOFFICE_SYNC_DIR (a .py file, or a folder holding
// git_flow.py), then the engine the groundwork plugin ships. SITEOFFICE_SYNC_DIR is ignored when
// it resolves inside the repo or one of its worktrees, links and case aliases followed (a repo can
// set it through its own settings), and the engine that runs is the resolved path. A repo's siteoffice.json and its sync folder are never engine sources.

import type { EngineInterface } from 'claude-code'
import { runGit } from './gitsafe'

export type EngineIO = {
  env(name: string): Promise<string | undefined>
  exists(path: string): Promise<boolean>
  // Where the path lands after links are followed; undefined when it leads nowhere.
  realPath(path: string): Promise<string | undefined>
  worktrees(root: string): Promise<string[]>
  bundled(): Promise<string>
}

// `engine` is '' when none; `ignoredSyncDir` is the raw variable value when it was ignored, and
// `ignoredReason` says why ('' when it was not).
export type IgnoredReason = '' | 'inside' | 'invalid-form' | 'unresolved'
export type EngineChoice = { engine: string; ignoredSyncDir: string; ignoredReason: IgnoredReason }

// The same plain lines the launcher (git_flow.ps1) prints, so the Doctor and the launcher agree.
export const SYNC_DIR_IGNORED = 'SITEOFFICE_SYNC_DIR points inside this repo or one of its worktrees, so it is ignored.'
export const SYNC_DIR_INVALID = 'SITEOFFICE_SYNC_DIR is not an absolute path, so it is ignored.'
export const SYNC_DIR_UNRESOLVED = 'SITEOFFICE_SYNC_DIR does not resolve to a folder or file, so it is ignored.'

export function syncDirNote(reason: IgnoredReason): string | undefined {
  return reason === 'inside' ? SYNC_DIR_IGNORED : reason === 'invalid-form' ? SYNC_DIR_INVALID : reason === 'unresolved' ? SYNC_DIR_UNRESOLVED : undefined
}

// Backslashes to slashes, `.` and `..` collapsed, trailing slash stripped, case kept.
export function resolvePath(p: string): string {
  const s = p.replace(/\\/g, '/')
  const abs = s.startsWith('/')
  const out: string[] = []
  for (const seg of s.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') {
      if (out.length > 0 && out[out.length - 1] !== '..' && !/^[a-zA-Z]:$/.test(out[out.length - 1])) out.pop()
      else if (!abs && out.length === 0) out.push('..')
      continue
    }
    out.push(seg)
  }
  return (abs ? '/' : '') + out.join('/')
}

export function isInsideAny(path: string, roots: string[]): boolean {
  const p = resolvePath(path).toLowerCase()
  for (const r of roots) {
    if (!r) continue
    const q = resolvePath(r).toLowerCase()
    if (!q) continue
    if (p === q || p.startsWith(q.endsWith('/') ? q : q + '/')) return true
  }
  return false
}

export function parseWorktrees(porcelain: string): string[] {
  const out: string[] = []
  for (const line of porcelain.split(/\r?\n/)) {
    if (line.startsWith('worktree ')) out.push(line.slice('worktree '.length).trim())
  }
  return out
}

export async function engineFor(io: EngineIO, root: string): Promise<EngineChoice> {
  const folders = [root, root + '/.claude/worktrees', ...(await io.worktrees(root))]
  const raw = (await io.env('SITEOFFICE_SYNC_DIR')) ?? ''
  let ignored = ''
  let reason: IgnoredReason = ''
  if (raw.trim()) {
    // Relative, drive-relative, UNC and device values, and a bare root, are never usable, and cost no file system call.
    if (!/^([\\/](?![\\/])|[a-zA-Z]:[\\/])/.test(raw) || !raw.replace(/[\\/]+/g, '')) return { engine: await io.bundled(), ignoredSyncDir: raw, ignoredReason: 'invalid-form' }
    const file = resolvePath(/\.py$/i.test(raw) ? raw : raw.replace(/[\\/]+$/, '') + '/git_flow.py')
    const real = await placed(io, file)
    if (real === undefined) {
      ignored = raw
      reason = 'unresolved'
    } else {
      // Roots are judged by where they land and by how they are spelled; a root that does not
      // resolve (the worktrees folder may not exist) still counts by its spelling.
      const roots = [...folders, ...(await Promise.all(folders.map(f => io.realPath(f)))).filter((r): r is string => !!r)]
      if (isInsideAny(file, folders) || isInsideAny(real, roots)) {
        ignored = raw
        reason = 'inside'
      } else if (await io.exists(real)) return { engine: real, ignoredSyncDir: '', ignoredReason: '' }
    }
  }
  return { engine: await io.bundled(), ignoredSyncDir: ignored, ignoredReason: reason }
}

// Where the engine file lands: the file itself when it resolves, else its folder plus its name.
async function placed(io: EngineIO, file: string): Promise<string | undefined> {
  const own = await io.realPath(file)
  if (own !== undefined) return own
  const cut = file.lastIndexOf('/')
  if (cut < 0) return undefined
  const dir = await io.realPath(file.slice(0, cut + 1))
  return dir === undefined ? undefined : dir.replace(/[\\/]+$/, '') + '/' + file.slice(cut + 1)
}

// The siteoffice-git engine the Groundwork plugin ships. Installed, the two plugins sit side by
// side in one cache folder, one folder per version; in a checkout they are siblings under plugins.
export async function bundledEnginePath($: EngineInterface): Promise<string> {
  const root = $.plugin.root.replace(/\\/g, '/').replace(/\/+$/, '')
  const up = (p: string) => p.replace(/\/[^/]*$/, '')
  const direct = up(root) + '/groundwork/engine/sync/git_flow.py'
  if (await $.fs.exists(direct)) return direct
  const versions = up(up(root)) + '/groundwork'
  try {
    for (const e of await $.fs.list(versions)) {
      const f = versions + '/' + e.name + '/engine/sync/git_flow.py'
      if (await $.fs.exists(f)) return f
    }
  } catch {
    return ''
  }
  return ''
}

export function engineIO($: EngineInterface): EngineIO {
  return {
    // The host wants a literal name here, and the rule only ever reads this one.
    env: async () => (await $.env.get('SITEOFFICE_SYNC_DIR')) ?? undefined,
    exists: async path => {
      try {
        return !!(await $.fs.exists(path))
      } catch {
        return false
      }
    },
    realPath: async path => {
      try {
        return (await $.fs.stat(path, { resolve: true })).realPath
      } catch {
        return undefined
      }
    },
    worktrees: async root => {
      const r = await runGit($, ['git', '-C', root, 'worktree', 'list', '--porcelain'], 5000)
      return r.exitCode === 0 ? parseWorktrees(r.stdout) : []
    },
    bundled: () => bundledEnginePath($),
  }
}
