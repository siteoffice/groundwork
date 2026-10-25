// Groundwork mods: the Setup Doctor and the agent policy pack, always on, plus two switches, both
// off by default: `fleet` (the Fleet) and `gitLane` (the Git lane). A switch that is off registers
// no hook at all. One module so the plugin loads one file; each part registers its own hooks.

import type { Register } from 'claude-code'

import { registerDoctor } from './doctor/register'
import { registerFleet } from './fleet/register'
import { registerGitLane } from './git/register'
import { registerPolicy } from './policy/register'

export const register: Register = (on, options) => {
  registerDoctor(on, options)
  registerPolicy(on, options)
  if (options.fleet === true) registerFleet(on, options)
  if (options.gitLane === true) registerGitLane(on, options)
}
