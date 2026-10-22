## G4: the ship gate

A sprint succeeded and the deterministic pre-flight checks have run. G4 opens
on the merge decision.

What to look at: the pre-flight record on the driver, the branch it names, and
the pre-merge sha it recorded. That sha is the revert point, and
`foreman_run.py unship` prints the revert line from it later if this turns out
to be wrong.

How to answer: `ship` merges the branch with `--no-ff` and pushes, `hold`
leaves the branch unmerged and the gate reopenable with
`foreman_run.py ship <ref>`, `park` stops the flow without a verdict.
