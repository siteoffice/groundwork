## accept: the ship gate

A sprint succeeded and the deterministic pre-flight checks have run. The accept gate
opens on the merge decision.

What to look at: the pre-flight record on the driver, the branch it names, and the
pre-merge sha it recorded. That sha is the revert point, and `foreman_run.py unship`
prints the revert line from it later if this turns out to be wrong.

How to answer: `ship` (Ship, accept and merge) merges the branch with `--no-ff` and
pushes, `hold` (Hold, send back) leaves the branch unmerged, `park` (Park, pause, answer
later) stops the flow without a verdict. After `ship` or `hold` the learnings run
without a gate. A `hold` stays put until `foreman_run.py ship <ref>` reopens the gate.
