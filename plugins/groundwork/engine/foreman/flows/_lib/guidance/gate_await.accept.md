## accept is open and waiting on you

The pre-flight checks have run and the merge is one verdict away. Nothing has
been merged and nothing has been pushed yet.

What to look at: the pre-flight results, the diff on the sprint branch, and
the pre-merge sha recorded on the driver. Everything after `ship` is
reversible from that sha, but reversing it is still work you can avoid here.

How to answer, PowerShell from C:\work\10_holding:

    py flows\_lib\flow.py gate <project>/<slug> ship

Verdicts: `ship` (Ship, accept and merge), `hold` (Hold, send back), `park` (Park, pause,
answer later). After `ship` or `hold` the learnings run without a gate. A `hold` stays
put until `foreman_run.py ship <ref>` reopens it.
