// BurnMon's snapshot, shared by the Doctor and the Fleet. readBurnmon keeps only the age; the
// file's text never leaves it. readBurnWaits is the Fleet's second, separate read: the wait counts
// and the group of each session, nothing else.

import type { EngineInterface } from 'claude-code'

import { BURNMON_MAX_AGE_SECONDS, BURNMON_RELEASES_URL, snapshotAgeSeconds } from './doctor/checks'
import type { BurnWaits, DoctorFacts } from '../types'

// BurnMon's snapshot: is the file there, and how old is it.
export async function readBurnmon($: EngineInterface, home: string): Promise<DoctorFacts['burnmon']> {
  if (!home) return { exists: false, ageSeconds: null }
  const path = home.replace(/[\\/]+$/, '') + '/.burnmon/snapshot.json'
  if (!(await $.fs.exists(path))) return { exists: false, ageSeconds: null }
  try {
    return { exists: true, ageSeconds: snapshotAgeSeconds(String(await $.fs.read(path)), await $.clock.now()) }
  } catch {
    return { exists: true, ageSeconds: null }
  }
}

// The wait states of a snapshot younger than two ticks: wait_counts.blocked and .your_turn, and
// the group of each session item, by session id (the last part of a `session:` or `agentview:`
// id). Titles, details and paths are never read. null when the file is stale or not usable.
export function parseBurnWaits(raw: string, nowMs: number): BurnWaits | null {
  try {
    const j = JSON.parse(raw) as { written_at?: unknown; wait_counts?: { blocked?: unknown; your_turn?: unknown }; wait_items?: unknown }
    if (typeof j.written_at !== 'string') return null
    const at = Date.parse(j.written_at)
    if (Number.isNaN(at) || (nowMs - at) / 1000 >= BURNMON_MAX_AGE_SECONDS) return null
    const c = j.wait_counts
    if (!c || typeof c.blocked !== 'number' || typeof c.your_turn !== 'number') return null
    const groups: BurnWaits['groups'] = {}
    for (const it of Array.isArray(j.wait_items) ? (j.wait_items as unknown[]) : []) {
      if (!it || typeof it !== 'object') continue
      const { id, group } = it as { id?: unknown; group?: unknown }
      if (typeof id !== 'string' || !/^(session|agentview):/.test(id)) continue
      if (group !== 'blocked' && group !== 'your_turn') continue
      const sid = id.slice(id.lastIndexOf(':') + 1)
      if (sid && groups[sid] !== 'blocked') groups[sid] = group
    }
    return { blocked: c.blocked, yourTurn: c.your_turn, groups }
  } catch {
    return null
  }
}

export async function readBurnWaits($: EngineInterface, home: string): Promise<BurnWaits | null> {
  if (!home) return null
  const path = home.replace(/[\\/]+$/, '') + '/.burnmon/snapshot.json'
  try {
    if (!(await $.fs.exists(path))) return null
    return parseBurnWaits(String(await $.fs.read(path)), await $.clock.now())
  } catch {
    return null
  }
}

export type BurnmonNote ={ state: 'running' | 'stopped' | 'missing'; text: string }

// One plain line about BurnMon: running, not running, or not installed.
export function burnmonNote(b: DoctorFacts['burnmon']): BurnmonNote {
  if (!b.exists) return { state: 'missing', text: 'BurnMon is not installed. The Fleet works without it. Get it: ' + BURNMON_RELEASES_URL }
  if (b.ageSeconds === null) return { state: 'stopped', text: 'BurnMon is not running: its snapshot could not be read. Start BurnMon.' }
  if (b.ageSeconds >= BURNMON_MAX_AGE_SECONDS) return { state: 'stopped', text: 'BurnMon is not running: its snapshot is older than ' + BURNMON_MAX_AGE_SECONDS + ' seconds. Start BurnMon.' }
  return { state: 'running', text: 'BurnMon is running. Snapshot ' + Math.round(b.ageSeconds) + ' s old.' }
}
