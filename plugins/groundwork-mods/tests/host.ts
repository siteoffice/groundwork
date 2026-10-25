// Shared fixture for mounted tests of the ported hooks: the policy pack reads policy/agent-policy.json
// on every tool call, so a test file system must answer that path or the call is denied.
import { shipped } from './default-policy'

export const POLICY_RX = /\/policy\/agent-policy\.json$/

export function policyText(over: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...shipped, mode: 'work', ...over })
}
