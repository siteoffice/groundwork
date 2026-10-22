## G2 is open and waiting on you

The spec for this step is written and the flow is parked until you judge it.
The grill has already happened; this is the decision the grill was for.

What to look at: whether the spec answers every question a builder would
otherwise have to guess at, and whether its verification section describes
something that can actually fail.

How to answer, PowerShell from C:\work\10_holding:

    py flows\_lib\flow.py gate <project>/<slug> approve

Verdicts: `approve`, `reject`, `park`. On a multi-step flow, approve through
the /flow skill so the per-step spec path is recorded.
