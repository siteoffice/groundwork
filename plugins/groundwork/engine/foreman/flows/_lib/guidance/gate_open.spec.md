## spec: the spec gate

The spec gate is the first of two gates in a flow. On step 1 it opens on the whole
breakdown and the first spec together: approving it approves both the step list and
the first spec. The first card opens only after the plan passes spec_lint.

What to look at on step 1: the step list. Too few steps and one sprint carries more
judgment than one review can hold. Too many and the flow spends its life crossing
boundaries instead of shipping. Then the spec itself: is it locked enough that a dev
agent can build from it without asking a question that is really a design decision.
Open questions left in a spec become invented answers in an implementation.

On a later step, a later spec card is where to adapt the steps still ahead, or `park` the
flow. A sprint that passed its tests can still have invalidated the next step's premise.

How to answer: `approve` (Approve) sends the step to its sprint, `reject` (Reject, do
not build this step) sends the spec back for another grill, `park` (Park, pause, answer
later) stops the flow here. On a multi-step flow approve it through the /flow skill so
the per-step `spec_path` is recorded; the driver refuses this one with `needs_spec`
rather than letting every later step silently fall back to the whole-flow spec.
