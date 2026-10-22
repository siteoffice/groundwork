## G1 approved: it still has to be consumed

G1 is decided `approve`. Nothing else has happened yet: the verdict sits on the gate until the
next advance reads it and moves the flow into breakdown proper. The command printed on the DO
line, `/flow advance <ref>`, is what consumes it.

Before running that line, check whether this flow has more than one step. On a multi-step flow,
the advance call needs its own `spec_path` argument naming this step's spec, because the printed
`/flow advance <ref>` alone carries no reminder of that. Leave it off and the flow still advances,
but onto a guessed fallback path instead of the real one, which strands the step at the next gate
with a `SpecMissingError` or, worse, a stale log entry that looks runnable but is not. This is not
theoretical: it is exactly what happened on 2026-08-24 during the first live run through this gate.

Single-step flows are unaffected: the conventional per-project fallback path is already correct
for them, so nothing extra to add.
