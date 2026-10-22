## The sprint line is ready to run

This step is at phase sprint, no sprint has been launched for it, and the
driver has composed the exact /sprint invocation from the flow's own state.
The line printed on the DO field is the line to run: it is not a sketch and it
does not need editing.

What to check first: that the spec path it names is the per-step spec, not the
whole-flow one, and that the model map matches what the spec asked for.

Run it in the session, not through the driver. The driver refuses to launch a
sprint under the Cowork mount, and it holds a work-in-progress limit of one
non-terminal sprint per repo, so a second launch is refused rather than queued.
