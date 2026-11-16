## spec is open and waiting on you

Nothing in this flow moves until the spec gate has a verdict. The driver is not stuck
and there is nothing to debug: it is doing exactly what it should, which is refusing
to invent your answer.

On step 1, approving approves both the step list and the first spec. Too few steps and
one sprint carries more judgment than one review can hold; too many and the flow spends
its life crossing boundaries instead of shipping. On a later step, a later spec card is where to adapt the
steps still ahead, or `park` the flow.

What to look at: whether the spec answers every question a builder would otherwise
have to guess at, and whether its verification section describes something that can
actually fail.

How to answer, PowerShell from C:\work\10_holding:

    py flows\_lib\flow.py gate <project>/<slug> approve

Verdicts: `approve` (Approve), `reject` (Reject, do not build this step), `park` (Park,
pause, answer later). On a multi-step flow, approve through the /flow skill so the
per-step spec path is recorded.
