## G1: the breakdown gate

The flow has a breakdown and no gate open on it yet. Opening G1 asks one
question: is this the right list of steps to actually build.

What to look at: the step list itself. Too few steps and a single sprint
carries more judgment than one review can hold. Too many and the flow spends
its life crossing boundaries instead of shipping.

How to answer: `approve` accepts the list as the plan of record, `reject`
sends the breakdown back for another pass, `park` stops the flow here without
a verdict. Approving G1 needs the step list on hand, because the driver
cannot derive it from flow.json and will stop with `needs_steps` if it is
missing.
