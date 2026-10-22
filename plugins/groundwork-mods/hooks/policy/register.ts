// The agent policy pack: the hooks. Reads the policy file (the project's own copy first, the
// plugin's default second), decides each tool call with policy.ts, and for every write keeps the
// old copy, writes one log line and hands the model a receipt id. The rules live in
// policy/README.md and policy/agent-policy.json; this file only enforces them.

import type { EngineInterface, Register } from 'claude-code'

import { decide, parents, receiptId, undoNote } from './policy'
import type { Approval, Decision, Policy } from './policy'

const PROJECT_POLICY = '.groundwork/agent-policy.json'

async function readJson<T>($: EngineInterface, path: string, fallback: T): Promise<T> {
  try {
    if (!(await $.fs.exists(path))) return fallback
    return JSON.parse(String(await $.fs.read(path))) as T
  } catch {
    return fallback
  }
}

async function loadPolicy($: EngineInterface, root: string): Promise<Policy> {
  const shipped = await readJson<Policy | null>($, $.plugin.root + '/policy/agent-policy.json', null)
  if (!shipped) throw new Error('the policy file is missing from the plugin')
  const own = await readJson<Partial<Policy>>($, root + '/' + PROJECT_POLICY, {})
  return { ...shipped, ...own, send: { ...shipped.send, ...(own.send ?? {}) }, delete: { ...shipped.delete, ...(own.delete ?? {}) } }
}

let counter = 0

async function append($: EngineInterface, path: string, entry: Record<string, unknown>) {
  const old = (await $.fs.exists(path)) ? String(await $.fs.read(path)) : ''
  await $.fs.write(path, old + JSON.stringify(entry) + '\n')
}

export const registerPolicy: Register = on => {
  on('tool.call', async ($, e, next) => {
    const cwd = await $.session.cwd()
    // The project root is the nearest folder, from the working directory up, that holds a
    // .groundwork/agent-policy.json: a folder with only a log in it must not shadow the project.
    // With no policy file anywhere, the nearest .groundwork folder, else the working directory.
    let root = cwd.replace(/\\/g, '/')
    const dirs = parents(cwd)
    let found = ''
    for (const dir of dirs) {
      if (await $.fs.exists(dir + '/' + PROJECT_POLICY)) {
        found = dir
        break
      }
    }
    if (!found) {
      for (const dir of dirs) {
        if (await $.fs.exists(dir + '/.groundwork')) {
          found = dir
          break
        }
      }
    }
    if (found) root = found
    const policy = await loadPolicy($, root)
    const nowIso = new Date(await $.clock.now()).toISOString()
    const input = e as unknown as Record<string, unknown>
    const tool = String(input.tool ?? '')
    const approvals = await readJson<Approval[]>($, root + '/' + policy.delete.approvalFile, [])
    const d: Decision = decide(policy, { tool, input }, { root, approvals, nowIso })
    const log = root + '/' + policy.log

    if (d.action === 'deny') {
      await append($, log, { ts: nowIso, tool, decision: 'denied', kind: d.kind, why: d.why }).catch(() => undefined)
      return { deny: 'Groundwork policy: ' + d.why + '.' }
    }
    if (d.action === 'allow') return next(e)

    // A write: keep the old copy first, run it, then log it with a receipt and an undo note.
    const receipt = receiptId(nowIso, (counter += 1))
    let existed = false
    if (d.backup) {
      const path = d.target.replace(/\\/g, '/')
      const full = /^([a-zA-Z]:)?\//.test(path) ? path : root + '/' + path
      try {
        if (await $.fs.exists(full)) {
          await $.fs.write(root + '/' + policy.undoFolder + '/' + receipt + '.bak', String(await $.fs.read(full)))
          existed = true
        }
      } catch {
        existed = false
      }
    }
    const ran = await next(e)
    const failed = ran.deny !== undefined || ran.isError === true
    const undo = undoNote(d, receipt, policy.undoFolder, existed)
    await append($, log, {
      ts: nowIso, tool, decision: failed ? 'failed' : 'written', kind: d.kind, target: d.target,
      receipt, undo,
    }).catch(() => undefined)
    if (failed || ran.deny !== undefined) return ran
    return { ...ran, context: [...(ran.context ?? []), 'Groundwork receipt ' + receipt + '. ' + undo] }
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'Groundwork policy: the policy check failed, so this call was stopped. Run the Setup Doctor.' }))
}
