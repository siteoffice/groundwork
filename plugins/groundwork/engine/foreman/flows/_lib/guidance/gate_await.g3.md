## G3 is open and waiting on you

The sprint is done and the flow is parked on what it means. This is the only
gate where the honest answer is sometimes to stop: `close` is a result, not a
failure.

What to look at: the sprint report, the flow log receipts for this step, and
the steps still queued behind this one.

How to answer, PowerShell from C:\work\10_holding:

    py flows\_lib\flow.py gate <project>/<slug> proceed

Verdicts: `proceed`, `adapt`, `close`, `park`.
