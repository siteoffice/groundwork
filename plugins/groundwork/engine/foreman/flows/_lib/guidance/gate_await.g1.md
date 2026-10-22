## G1 is open and waiting on you

Nothing in this flow moves until the breakdown gate has a verdict. The driver
is not stuck and there is nothing to debug: it is doing exactly what it should,
which is refusing to invent your answer.

What to look at: the step list in flow.json, and whether each step is a thing
one sprint can finish and one review can judge.

How to answer, PowerShell from C:\work\10_holding:

    py flows\_lib\flow.py gate <project>/<slug> approve

Verdicts: `approve`, `reject`, `park`.
