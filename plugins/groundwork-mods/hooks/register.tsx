// Groundwork mods: the Setup Doctor and the agent policy pack. One module so the plugin loads
// one file; each part registers its own hooks.

import type { Register } from 'claude-code'

import { registerDoctor } from './doctor/register'
import { registerPolicy } from './policy/register'

export const register: Register = (on, options) => {
  registerDoctor(on, options)
  registerPolicy(on, options)
}
