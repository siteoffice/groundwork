## G2: the spec gate

A step has been grilled and the spec it produced is ready to be judged. G2
opens on that spec, not on the code, because the code does not exist yet.

What to look at: is the spec locked enough that a dev agent can build from it
without asking a question that is really a design decision. Open questions
left in a spec become invented answers in an implementation.

How to answer: `approve` sends the step to its sprint, `reject` sends the
spec back for another grill, `park` stops the flow here. On a multi-step flow
approve it through the /flow skill so the per-step `spec_path` is recorded;
the driver refuses this one with `needs_spec` rather than letting every later
step silently fall back to the whole-flow spec.
