## The merge is next

G4 is decided `ship` and the merge has not run yet. The order here is
deliberate: the merge and the push happen before the verdict is consumed, so
the receipts read ship first and advance second, and a crash in between leaves
a gate that is still decided rather than a flow that thinks it shipped.

What happens: the sprint branch merges with `--no-ff` and pushes. The
pre-merge sha was recorded at pre-flight and stays the revert point.

If it turns out to be wrong, PowerShell from C:\work\10_holding:

    py flows\_lib\foreman_run.py unship <project>/<slug>

That prints the `git revert -m 1 <sha>` line. It never runs it.
