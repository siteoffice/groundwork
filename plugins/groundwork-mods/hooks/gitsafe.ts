import type { EngineInterface } from 'claude-code'

// The Fleet runs git in folders a downloaded repo controls: no fsmonitor, no
// hooks, no external diff or textconv program. The platform comes from the
// caller as a flag, never from a global.
export function nullDevice(isWindows: boolean): string {
  return isWindows ? 'NUL' : '/dev/null'
}

export function hardenGit(argv: string[], isWindows: boolean): string[] {
  if (argv[0] !== 'git') return argv
  const rest = argv.slice(1)
  // The subcommand is the first argument that is not a global option (-c and -C take a value); free
  // text such as `commit -m status` must never be mistaken for it.
  let sub = 0
  while (sub < rest.length && rest[sub].startsWith('-')) sub += rest[sub] === '-c' || rest[sub] === '-C' ? 2 : 1
  let body = rest
  if (rest[sub] === 'diff') {
    body = [...rest.slice(0, sub + 1), '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', ...rest.slice(sub + 1)]
  } else if (rest[sub] === 'status') {
    // status runs a submodule's own git (and its filters) unless told not to; the flag goes after the
    // other arguments (before any `--`), so the argv still reads `status --porcelain=v2 ...`.
    const dash = rest.indexOf('--', sub)
    body = dash < 0 ? [...rest, '--ignore-submodules=all'] : [...rest.slice(0, dash), '--ignore-submodules=all', ...rest.slice(dash)]
  }
  return ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + nullDevice(isWindows), ...body]
}

// The same two settings for the git that `gh` runs (git 2.31 or newer).
export function gitConfigEnv(isWindows: boolean): Record<string, string> {
  return {
    GIT_CONFIG_COUNT: '2',
    GIT_CONFIG_KEY_0: 'core.fsmonitor',
    GIT_CONFIG_VALUE_0: 'false',
    GIT_CONFIG_KEY_1: 'core.hooksPath',
    GIT_CONFIG_VALUE_1: nullDevice(isWindows),
  }
}

// LC_ALL=C keeps git's messages in English: the trust check reads "not a git repository" from stderr.
export const QUIET_ENV = { GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', GH_PROMPT_DISABLED: '1', LC_ALL: 'C' }

export function onWindows($: EngineInterface): boolean {
  return /^[a-zA-Z]:/.test($.plugin.root)
}

export async function runGit($: EngineInterface, argv: string[], timeoutMs = 8000, cwd?: string): Promise<{ exitCode: number | null; stdout: string; stderr: string }> {
  try {
    const win = onWindows($)
    return await $.process.run(hardenGit(argv, win), { timeoutMs, ...(cwd ? { cwd } : {}), env: { ...QUIET_ENV, ...gitConfigEnv(win) } })
  } catch (err) {
    // -1 is no real exit code: a run that threw is told apart from git's own "no match" exit 1.
    return { exitCode: -1, stdout: '', stderr: String(err) }
  }
}

// A repo's own .git/config can name programs that git runs against the working
// tree (clean and smudge filters, textconv, a pager, ssh). A flag list never
// covers them all, so before the Fleet runs diff, ls-files or status in a
// folder it asks, with the same hardened git, whether the effective config
// holds any such setting set by the repo or the command line (local, worktree
// and command scope; the person's own system and global settings, such as
// Git LFS filters, do not count). Any match, an unreadable scope and any
// failure of the check mean the folder is not trusted and git is not run
// there. A clean verdict is never kept, because an agent can change the
// config after the check: the check runs again on each refresh. Only an
// untrusted verdict is kept for the session.
// Whole sections are refused (hook, gpg, alias, url, credential, include), because git keeps adding
// keys to them that run a program. Everything else is a list of known keys.
const RUNS_PROGRAMS = '^(filter\\..*\\.(clean|smudge|process)|core\\.(sshcommand|pager|editor|askpass|gitproxy|alternaterefscommand)|diff\\.external|diff\\..*\\.(command|textconv)|credential\\..*|include\\..*|includeif\\..*|extensions\\.worktreeconfig|remote\\..*\\.(url|pushurl|uploadpack|receivepack|vcs|proxy)|remote\\.pushdefault|branch\\..*\\.(remote|pushremote)|url\\..*|hook\\..*|gpg\\..*|alias\\..*|sequence\\.editor|lfs\\.(customtransfer\\..*|extension\\..*|standalonetransferagent)|merge\\..*\\.driver|protocol\\.(.*\\.)?allow|submodule\\.recurse|fetch\\.recursesubmodules|push\\.recursesubmodules)$'

// Keys that name a remote: a normal url or remote name stays trusted, a transport-helper prefix (ext::, fd::) runs a program.
const BY_VALUE = /^(remote\..*\.(url|pushurl)|remote\.pushdefault|branch\..*\.(remote|pushremote))$/
const HELPER = /^[A-Za-z][A-Za-z0-9+.-]*::/

// A remote url that names a place inside the repo's own work tree (a relative path, or an absolute
// path or file:// url under the work tree top level) points at a bare repo the repo ships itself; a
// push or fetch there runs that repo's hooks, which the lane's -c overrides do not reach. An absolute
// path outside the work tree is the person's own other repo and stays trusted (a known limit).
const URL_KEY = /^remote\..*\.(url|pushurl)$/
const SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//
const SCP = /^[^/\\:]{2,}:/

// '' for a url that is not a local path, 'relative' for one git resolves against the work tree, else
// the normalised absolute path (backslashes to slashes, lower-case drive, no file://, dots collapsed).
function localUrl(raw: string): string {
  let v = raw.trim()
  if (/^file:\/\//i.test(v)) {
    v = v.slice(7)
    try { v = decodeURIComponent(v) } catch { /* keep it as written */ }
    v = v.replace(/^\/(?=[A-Za-z]:)/, '')
  } else if (SCHEME.test(v) || SCP.test(v)) return ''
  if (!v) return ''
  v = v.replace(/\\/g, '/')
  if (!/^([A-Za-z]:|\/)/.test(v)) return 'relative'
  return normalPath(v)
}

function normalPath(p: string): string {
  const q = p.replace(/\\/g, '/').replace(/^file:\/\//i, '').replace(/^\/(?=[A-Za-z]:)/, '')
  const drive = /^([A-Za-z]):/.exec(q)
  const unc = q.startsWith('//')
  const parts: string[] = []
  for (const s of (drive ? q.slice(2) : q).split('/')) {
    if (s === '' || s === '.') continue
    if (s === '..') parts.pop()
    else parts.push(s)
  }
  const joined = (drive ? drive[1].toLowerCase() + ':' : unc ? '//' : '') + '/' + parts.join('/')
  return drive || unc ? joined.toLowerCase() : joined
}

function insideTop(path: string, top: string): boolean {
  const t = normalPath(top)
  const base = t.endsWith('/') ? t : t + '/'
  return path === t || path.startsWith(base)
}

// A UNC-style remote (\\host\share or //host/share) can map onto a local drive (\\localhost\C$\...), so it
// can reach a bare repo inside the work tree. The lane closes on any UNC remote and does not map admin shares.
const UNC = /^[\\/]{2}[^\\/]+[\\/]+[^\\/]/

// True when the line is a remote url that is an absolute local path: only then the work tree top level is needed.
export function needsTop(line: string): boolean {
  const tab = line.indexOf('\t')
  if (tab < 0 || !/^(local|worktree|command)$/.test(line.slice(0, tab).trim())) return false
  const rest = line.slice(tab + 1).trim()
  const sp = rest.search(/\s/)
  const key = (sp < 0 ? rest : rest.slice(0, sp)).toLowerCase()
  if (!URL_KEY.test(key) || sp < 0) return false
  if (UNC.test(rest.slice(sp).trim())) return false
  const l = localUrl(rest.slice(sp))
  return l !== '' && l !== 'relative'
}

// top: the work tree top level, needed to judge an absolute path ('' means it could not be read, which fails closed).
export function lineIsUntrusted(line: string, top?: string): boolean {
  const tab = line.indexOf('\t')
  const scope = (tab < 0 ? line : line.slice(0, tab)).trim()
  if (!['system', 'global', 'local', 'worktree', 'command'].includes(scope)) return true
  if (scope === 'system' || scope === 'global') return false
  const rest = tab < 0 ? '' : line.slice(tab + 1).trim()
  const [key = '', ...value] = rest.split(/\s+/)
  if (key.toLowerCase() === 'extensions.worktreeconfig') return /^(true|yes|on|1)$/i.test(value.join(' '))
  // Every token counts, the key too: a remote name with spaces can hide a helper url inside the printed key.
  if (BY_VALUE.test(key.toLowerCase())) {
    if ([key, ...value].some(t => HELPER.test(t))) return true
    if (!URL_KEY.test(key.toLowerCase())) return false
    if (UNC.test(value.join(' ').trim())) return true
    const l = localUrl(value.join(' '))
    if (l === 'relative') return true
    if (l === '' || top === undefined) return false
    return top === '' || insideTop(l, top)
  }
  return true
}

const NOT_FINISHED = 'the settings check did not finish'
const reasons = new Map<string, string>()

// The first setting that closed the folder, or why the check itself failed. Empty if the folder was never closed.
export function whyUntrusted(dir: string): string {
  return reasons.get(dir) ?? ''
}

// The pattern names neither core.fsmonitor nor core.hooksPath, so the Fleet's own
// command-scope pairs never match; any command-scope match that does come back is untrusted.
// (A repo can change the config between this check and a later diff; the check reruns on each refresh.)
export async function isTrustedFolder($: EngineInterface, dir: string, untrusted: Set<string>): Promise<boolean> {
  if (untrusted.has(dir)) return false
  const r = await runGit($, ['git', '-C', dir, 'config', '--show-scope', '--get-regexp', RUNS_PROGRAMS])
  const lines = String(r.stdout).split('\n').filter(l => l.trim())
  // Only git's own exit 1 (no match) or exit 0 (matches) can be clean; a run that threw (-1) or any other exit is not.
  const ran = r.exitCode === 1 || r.exitCode === 0
  // Only when a remote url is an absolute local path: where is the work tree? (fail closed: '' if unknown)
  let top: string | undefined
  if (ran && lines.some(needsTop)) {
    const t = await runGit($, ['git', '-C', dir, 'rev-parse', '--show-toplevel'])
    top = t.exitCode === 0 ? String(t.stdout).trim() : ''
  }
  const bad = ran ? lines.find(l => lineIsUntrusted(l, top)) : undefined
  const clean = ran && bad === undefined
  if (!clean) {
    untrusted.add(dir)
    const tab = (bad ?? '').indexOf('\t')
    reasons.set(dir, (tab < 0 ? '' : (bad as string).slice(tab + 1).trim().split(/\s+/)[0]) || NOT_FINISHED)
  }
  return clean
}

// The Git lane runs add, pull and switch, which run a submodule's own git config (a clean filter, an alias
// behind a transport helper), and the config check above never reads it. So the lane, and only the lane,
// calls this after isTrustedFolder says trusted. The Fleet runs only status, diff and ls-files, hardened with
// --ignore-submodules=all, which a verifier showed do not run a submodule's programs, so it does not call this.
// Returns 'submodules' (a .gitmodules file in the work tree or a modules folder in the git dir),
// NOT_FINISHED (rev-parse failed for any reason except git's "not a git repository", or fs failed) or ''.
// A non-empty result is also kept for the session, as isTrustedFolder keeps its own.
export async function hasSubmodules($: EngineInterface, dir: string, untrusted: Set<string>): Promise<string> {
  const close = (why: string) => {
    untrusted.add(dir)
    reasons.set(dir, why)
    return why
  }
  const t = await runGit($, ['git', '-C', dir, 'rev-parse', '--show-toplevel', '--absolute-git-dir'])
  const out = String(t.stdout).split('\n').map(s => s.trim()).filter(Boolean)
  if (t.exitCode !== 0 && t.exitCode !== null && /not a git repository/i.test(String(t.stderr))) return ''
  if (t.exitCode !== 0 || out.length !== 2) return close(NOT_FINISHED)
  try {
    const base = (p: string) => p.replace(/[\\/]+$/, '')
    if ((await $.fs.exists(base(out[0]) + '/.gitmodules')) || (await $.fs.exists(base(out[1]) + '/modules'))) return close('submodules')
  } catch {
    return close(NOT_FINISHED)
  }
  return ''
}

// The remote's default branch names, read from the files git keeps and never by running git: the loose
// refs/remotes/origin/HEAD ("ref: refs/remotes/origin/<name>"), else the packed-refs branches that share
// origin/HEAD's commit. Used where git must not run (an untrusted folder). Empty when nothing is found.
export function originHeadNames(loose: string, packed: string): string[] {
  const one = /^ref:\s*refs\/remotes\/origin\/(\S+)\s*$/m.exec(loose)
  if (one) return [one[1]]
  const lines = packed.split(/\r?\n/)
  const sha = /^([0-9a-f]{40,64}) refs\/remotes\/origin\/HEAD$/m.exec(packed)?.[1]
  if (!sha) return []
  return lines.flatMap(l => (l.startsWith(sha + ' refs/remotes/origin/') && !l.endsWith('/HEAD') ? [l.slice(sha.length + 1 + 'refs/remotes/origin/'.length).trim()] : []))
}

// A linked worktree's .git is a file ("gitdir: <path>"); the remote refs live in the common dir that the
// worktree's gitdir names in its commondir file. Returns '' when dotGit is not such a file.
async function commonDirOf($: EngineInterface, dotGit: string): Promise<string> {
  // Collapses . and .. itself (git writes commondir as ../..), so the host's fs never sees a .. segment.
  const abs = (p: string, base: string) => {
    const out: string[] = []
    for (const s of (/^([a-zA-Z]:|\/)/.test(p) ? p : base + '/' + p).split('/')) {
      if (s === '..') {
        if (out.length > 1) out.pop()
      } else if (s !== '.' && (s !== '' || out.length === 0)) out.push(s)
    }
    return out.join('/')
  }
  const text = String(await $.fs.read(dotGit))
  const m = /^gitdir:\s*(.+?)\s*$/m.exec(text)
  if (!m) return ''
  const gitdir = abs(m[1].replace(/[\\]/g, '/'), dotGit.slice(0, -'/.git'.length))
  const commonFile = gitdir + '/commondir'
  if (!(await $.fs.exists(commonFile))) return gitdir
  return abs(String(await $.fs.read(commonFile)).trim().replace(/[\\]/g, '/'), gitdir)
}

// Walks up from dir to the first .git folder or worktree .git file. A failed read gives [].
// A reftable repository keeps no such file, so it also gives [] (the guard still has main and master).
export async function defaultBranchesFromFiles($: EngineInterface, dir: string): Promise<string[]> {
  try {
    const parts = dir.replace(/[\\]/g, '/').replace(/\/+$/, '').split('/')
    for (let i = parts.length; i > 0; i--) {
      let git = parts.slice(0, i).join('/') + '/.git'
      let hasHead = await $.fs.exists(git + '/refs/remotes/origin/HEAD')
      let hasPacked = await $.fs.exists(git + '/packed-refs')
      if (!hasHead && !hasPacked && (await $.fs.exists(git))) {
        const common = await commonDirOf($, git).catch(() => '')
        if (common) {
          git = common
          hasHead = await $.fs.exists(git + '/refs/remotes/origin/HEAD')
          hasPacked = await $.fs.exists(git + '/packed-refs')
        }
      }
      if (!hasHead && !hasPacked) continue
      const headFile = git + '/refs/remotes/origin/HEAD'
      const packedFile = git + '/packed-refs'
      const loose = hasHead ? String(await $.fs.read(headFile)) : ''
      const packed = hasPacked ? String(await $.fs.read(packedFile)) : ''
      return originHeadNames(loose, packed).filter(n => n.length <= 200)
    }
  } catch {
    // unreadable: the guard still has main and master
  }
  return []
}
