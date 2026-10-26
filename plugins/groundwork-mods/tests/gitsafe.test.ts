import { describe, expect, test } from 'claude-code/testing'
import * as safe from '../hooks/gitsafe'
import { HOST_CHECK } from './host'
import * as threads from '../hooks/fleet/threads'

describe('gitsafe', () => {
  test('the Fleet helpers are the shared ones', async () => {
    expect(threads.hardenGit).toBe(safe.hardenGit)
    expect(threads.gitConfigEnv).toBe(safe.gitConfigEnv)
    expect(threads.nullDevice).toBe(safe.nullDevice)
    expect(safe.lineIsUntrusted('global\tfilter.lfs.clean git-lfs clean -- %f')).toBe(false)
    expect(safe.lineIsUntrusted('local\tcore.sshcommand ssh -o ProxyCommand=evil')).toBe(true)
    expect(safe.lineIsUntrusted('local\tlfs.customtransfer.x.path evil')).toBe(true)
    expect(safe.lineIsUntrusted('local\tlfs.standalonetransferagent x')).toBe(true)
    expect(safe.QUIET_ENV).toEqual({ GIT_TERMINAL_PROMPT: '0', GIT_OPTIONAL_LOCKS: '0', GH_PROMPT_DISABLED: '1', LC_ALL: 'C' })
  })

  test('the default branch is read from origin/HEAD, else from packed-refs by commit, never by running git', () => {
    expect(safe.originHeadNames('ref: refs/remotes/origin/develop\n', '')).toEqual(['develop'])
    expect(safe.originHeadNames('ref: refs/remotes/origin/release/2\n', '')).toEqual(['release/2'])
    const sha = 'a'.repeat(40)
    const packed = '# pack-refs\n' + sha + ' refs/remotes/origin/HEAD\n' + sha + ' refs/remotes/origin/develop\n' + 'b'.repeat(40) + ' refs/remotes/origin/other\n'
    expect(safe.originHeadNames('', packed)).toEqual(['develop'])
    expect(safe.originHeadNames('', '')).toEqual([])
    expect(safe.originHeadNames('garbage', '# pack-refs\n')).toEqual([])
  })

  test('remote urls are decided by value, other network and commit program keys always count', () => {
    for (const ok of ['https://github.com/a/b.git', 'git@github.com:a/b.git', 'ssh://git@h/a/b.git', 'host.example:path/x.git', 'http://[::1]/x', '/outside/repo', 'C:/work/repo', 'file:///outside/repo'])
      for (const k of ['remote.origin.url', 'remote.origin.pushurl']) expect(safe.lineIsUntrusted('local\t' + k + ' ' + ok)).toBe(false)
    for (const bad of ['ext::sh -c touch% x', 'fd::3', 'EXT::x'])
      for (const k of ['remote.origin.url', 'remote.origin.pushurl']) expect(safe.lineIsUntrusted('local\t' + k + ' ' + bad)).toBe(true)
    for (const line of [
      'local\turl.ext::x.insteadof https://example.com/',
      'worktree\turl.ext::x.pushinsteadof https://example.com/',
      'local\tcore.gitproxy evil',
      'local\tgpg.program evil',
      'local\tgpg.ssh.program evil',
      'local\tsequence.editor evil',
      'local\tmerge.x.driver evil %O %A %B',
      'local\tremote.origin.uploadpack evil',
      'local\tremote.origin.receivepack evil',
      'local\tremote.origin.vcs evil',
      'local\tremote.origin.proxy evil',
      'local\tprotocol.ext.allow always',
      'command\tprotocol.allow always',
    ]) expect(safe.lineIsUntrusted(line)).toBe(true)
    expect(safe.lineIsUntrusted('global\tgpg.program gpg')).toBe(false)
    expect(safe.lineIsUntrusted('system\tprotocol.allow user')).toBe(false)
  })

  test('whole sections, remote names and a hidden helper url count; normal remotes do not', () => {
    for (const line of [
      'local\thook.evil.command sh -c evil',
      'local\tgpg.ssh.defaultkeycommand evil',
      'local\talias.remote-evil !evil',
      'local\turl.https://x/.anything y',
      'local\tcore.alternaterefscommand evil',
      'local\tbranch.main.remote ext::sh -c touch% x',
      'local\tbranch.main.pushremote fd::3',
      'local\tremote.pushdefault ext::x',
      'local\tremote.x.url https://a/ y.url ext::sh -c touch% x',
    ]) expect(safe.lineIsUntrusted(line)).toBe(true)
    for (const line of [
      'local\tbranch.main.remote origin',
      'local\tremote.pushdefault origin',
      'local\tremote.origin.url http://[::1]/x',
      'local\tremote.origin.url /path',
      'local\tremote.origin.url C:/path',
    ]) expect(safe.lineIsUntrusted(line)).toBe(false)
  })

  test('status gets --ignore-submodules=all after its own arguments, other commands do not', () => {
    expect(safe.hardenGit(['git', '-C', '/r', 'status', '--porcelain=v2', '--branch'], false)).toEqual(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', '/r', 'status', '--porcelain=v2', '--branch', '--ignore-submodules=all'])
    expect(safe.hardenGit(['git', '-C', '/r', 'log', '-3'], false)).not.toContain('--ignore-submodules=all')
  })

  test('a relative remote url is untrusted, an absolute one only when it is inside the work tree', () => {
    for (const bad of ['./inside.git', 'inside.git', '../sibling.git', 'sub\\inside.git'])
      for (const k of ['remote.origin.url', 'remote.origin.pushurl']) expect(safe.lineIsUntrusted('local\t' + k + ' ' + bad)).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url /work/repo/inside.git', '/work/repo')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url /work/repo/a/../inside.git', '/work/repo')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url file:///work/repo/inside.git', '/work/repo')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url file:///C:/Work/Repo/inside.git', 'c:/work/repo')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url C:\\Work\\Repo\\inside.git', 'c:/work/repo')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.origin.url /work/repo-other/x.git', '/work/repo')).toBe(false)
    expect(safe.lineIsUntrusted('local\tremote.origin.url /work/repo/../other/x.git', '/work/repo')).toBe(false)
    expect(safe.lineIsUntrusted('local\tremote.origin.url file:///elsewhere/x.git', '/work/repo')).toBe(false)
    expect(safe.lineIsUntrusted('local\tremote.origin.url /work/repo/inside.git', '')).toBe(true)
    expect(safe.lineIsUntrusted('local\tremote.pushdefault origin', '/work/repo')).toBe(false)
  })

  test('hardenGit adds the diff and status flags only when the subcommand itself is diff or status', () => {
    const h = (...a: string[]) => safe.hardenGit(['git', ...a], false)
    const pre = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null']
    for (const a of [['commit', '-m', 'status'], ['commit', '-m', 'diff'], ['push', '-u', 'origin', 'status'], ['switch', 'diff']]) expect(h(...a)).toEqual([...pre, ...a])
    expect(h('-C', '/r', 'status')).toEqual([...pre, '-C', '/r', 'status', '--ignore-submodules=all'])
    expect(h('-c', 'core.quotePath=false', 'status', '--porcelain=v2', '--branch')).toEqual([...pre, '-c', 'core.quotePath=false', 'status', '--porcelain=v2', '--branch', '--ignore-submodules=all'])
    expect(h('-C', 'diff', 'status')).toEqual([...pre, '-C', 'diff', 'status', '--ignore-submodules=all'])
    expect(h('status', '--', 'x')).toEqual([...pre, 'status', '--ignore-submodules=all', '--', 'x'])
    expect(h('-C', '/r', 'diff', '--numstat', 'HEAD')).toEqual([...pre, '-C', '/r', 'diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all', '--numstat', 'HEAD'])
    expect(h('--no-pager', 'diff')).toEqual([...pre, '--no-pager', 'diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules=all'])
  })

  test('UNC remotes and submodule recursion keys are untrusted', () => {
    for (const u of ['\\\\localhost\\C$\\x\\inside.git', '//localhost/C$/x/inside.git', '\\\\server\\share\\repo.git', '//server/share/repo.git'])
      for (const k of ['remote.origin.url', 'remote.origin.pushurl']) expect(safe.lineIsUntrusted('local\t' + k + ' ' + u, '/top')).toBe(true)
    for (const k of ['submodule.recurse', 'fetch.recursesubmodules', 'push.recursesubmodules']) expect(safe.lineIsUntrusted('local\t' + k + ' true')).toBe(true)
    expect(safe.lineIsUntrusted('global\tsubmodule.recurse true')).toBe(false)
  })

  test('a .gitmodules file or a git dir modules folder closes the folder as submodules', async () => {
    const world = (exists: string[], rev: { exitCode: number; stdout: string; stderr: string }) => ({
      plugin: { root: '/x' },
      fs: { exists: async (p: string) => exists.includes(p) },
      process: { run: async (argv: string[]) => (argv.includes('rev-parse') ? rev : { exitCode: 1, stdout: '', stderr: '' }) },
    })
    const ok = { exitCode: 0, stdout: '/r\n/r/.git\n', stderr: '' }
    const closed = async (w: unknown, dir: string) => [await safe.hasSubmodules(w as never, dir, new Set()), safe.whyUntrusted(dir)]
    expect(await closed(world([], ok), '/d1')).toEqual(['', ''])
    expect(await closed(world(['/r/.gitmodules'], ok), '/d2')).toEqual(['submodules', 'submodules'])
    expect(await closed(world(['/r/.git/modules'], ok), '/d3')).toEqual(['submodules', 'submodules'])
    expect(await closed(world([], { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository' }), '/d4')).toEqual(['', ''])
    expect(await closed(world([], { exitCode: 128, stdout: '', stderr: 'fatal: dubious ownership' }), '/d5')).toEqual(['the settings check did not finish', 'the settings check did not finish'])
    // The Fleet's own check does not look for submodules: a .gitmodules file alone leaves it trusted.
    expect(await safe.isTrustedFolder(world(['/r/.gitmodules'], ok) as never, '/d6', new Set())).toBe(true)
  })

  test('command scope is judged by value: the host pins pass, a switch-on or a second value does not', () => {
    for (const l of HOST_CHECK.split('\n').filter(Boolean)) expect(safe.lineIsUntrusted(l)).toBe(false)
    expect(safe.conflictingLine(HOST_CHECK.split('\n').filter(Boolean))).toBeUndefined()
    expect(safe.lineIsUntrusted('command\tcore.hookspath \\\\.\\NUL')).toBe(false)
    for (const bad of ['command\tcore.askpass evil.exe', 'command\tcore.askpass', 'command\thook.x.command sh -c evil', 'command\thook.x.enabled true', 'command\tprotocol.ext.allow always', 'command\tsubmodule.recurse true', 'command\tcore.hookspath C:/evil'])
      expect(safe.lineIsUntrusted(bad)).toBe(true)
    // Local and worktree scope stay strict, whatever the value.
    expect(safe.lineIsUntrusted('local\tcore.askpass ')).toBe(true)
    expect(safe.lineIsUntrusted('worktree\thook.x.enabled false')).toBe(true)
    expect(safe.conflictingLine(['command\tcore.askpass ', 'command\tcore.askpass ', 'command\tcore.askpass x'])).toBe('command\tcore.askpass x')
  })
})
