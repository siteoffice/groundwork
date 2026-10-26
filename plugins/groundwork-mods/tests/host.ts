// Shared fixture for mounted tests of the ported hooks: the policy pack reads policy/agent-policy.json
// on every tool call, so a test file system must answer that path or the call is denied.
import { shipped } from './default-policy'

export const POLICY_RX = /\/policy\/agent-policy\.json$/

export function policyText(over: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...shipped, mode: 'work', ...over })
}

// The command-scope pins the real Claude Code host adds to every git a plugin runs through $.process.run,
// captured live (2026-10-26, git 2.54.0.vfs.0.2 on Windows) with `git config --show-scope --list`.
const HOOKS = ['applypatch-msg', 'pre-applypatch', 'post-applypatch', 'pre-commit', 'pre-merge-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', 'pre-rebase', 'post-checkout', 'post-merge', 'pre-push', 'pre-receive', 'update', 'proc-receive', 'post-receive', 'post-update', 'reference-transaction', 'push-to-checkout', 'pre-auto-gc', 'post-rewrite', 'sendemail-validate', 'fsmonitor-watchman', 'p4-changelist', 'p4-prepare-changelist', 'p4-post-changelist', 'p4-pre-submit', 'post-index-change']
export const HOST_PINS: string[] = ['core.fsmonitor=', 'core.hookspath=\\\\.\\NUL', 'core.askpass=', 'protocol.ext.allow=never', 'submodule.recurse=false', 'log.showsignature=false', 'format.pretty=medium', 'gc.auto=0', 'maintenance.auto=false', ...HOOKS.map(h => 'hook.' + h + '.enabled=false')]

// What the trust check's pattern picks out of those pins, as git prints it (scope, TAB, key, SPACE, value),
// listed twice as the real host's git lists them, after any system, global and local lines.
export const HOST_CHECK = [...HOST_PINS, ...HOST_PINS]
  .filter(p => /^(core\.askpass|protocol\.|submodule\.recurse|hook\.)/.test(p))
  .map(p => 'command\t' + p.replace('=', ' '))
  .join('\n') + '\n'

// A mocked answer to the trust check as the real host returns it: the host's lines come after the repo's own,
// so git's "no match" exit 1 becomes exit 0. Any other exit code is kept.
export function underHost(stdout: string, exitCode: number): { stdout: string; exitCode: number } {
  return { stdout: (stdout && !stdout.endsWith('\n') ? stdout + '\n' : stdout) + HOST_CHECK, exitCode: exitCode === 1 ? 0 : exitCode }
}
