// BurnMon's snapshot, shared by the Doctor and the Fleet. Only the age is kept; the file's text
// never leaves readBurnmon.

import type { EngineInterface } from 'claude-code'

import { BURNMON_MAX_AGE_SECONDS, BURNMON_RELEASES_URL, snapshotAgeSeconds } from './doctor/checks'
import type { DoctorFacts } from '../types'

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

export type BurnmonNote = { state: 'running' | 'stopped' | 'missing'; text: string }

// One plain line about BurnMon: running, not running, or not installed.
export function burnmonNote(b: DoctorFacts['burnmon']): BurnmonNote {
  if (!b.exists) return { state: 'missing', text: 'BurnMon is not installed. The Fleet works without it. Get it: ' + BURNMON_RELEASES_URL }
  if (b.ageSeconds === null) return { state: 'stopped', text: 'BurnMon is not running: its snapshot could not be read. Start BurnMon.' }
  if (b.ageSeconds >= BURNMON_MAX_AGE_SECONDS) return { state: 'stopped', text: 'BurnMon is not running: its snapshot is older than ' + BURNMON_MAX_AGE_SECONDS + ' seconds. Start BurnMon.' }
  return { state: 'running', text: 'BurnMon is running. Snapshot ' + Math.round(b.ageSeconds) + ' s old.' }
}
