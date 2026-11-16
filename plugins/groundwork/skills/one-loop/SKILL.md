---
name: one-loop
description: >-
  Runs one piece of work from idea to close in a folder you control, with two gates where you decide and a local receipt when you accept. Use on "one loop", "start a flow", "flow status", "advance the flow", "approve the gate", "close the flow". Do NOT use for a quick edit that needs no decision trail.
---

# One loop

One idea goes through the same loop every time: idea, design, plan, work, review, close. You
decide at two gates. The state lives in plain files in your own folder, so you can stop and
pick it up tomorrow. When you accept the work at the last gate, you get a receipt.

## What this skill is, and is not

- It calls a small command line, `gw.py`, that sits in this plugin. The command line changes
  state through the engine's own tested functions. You never edit `flow.json` by hand.
- It records decisions. It does not run your work for you, send anything, or start another
  agent. The work step is you and the agent, in this session.
- The receipt is **self-issued, not signed**. It is your own record, not proof to anyone else.

## Setup (once per session)

`gw.py` is in this plugin at `engine\groundwork\gw.py`. From this skill's folder that is
`..\..\engine\groundwork\gw.py`. Keep two values in mind and reuse them in every command:

- `GW`: the full path to `gw.py`.
- `ROOT`: the folder where this work's records should live. Use the project folder you are in.
  The engine writes to `ROOT\flows\<project>\<slug>\`.

It needs Python 3.9 or newer and nothing else.

```powershell
# runs in: PowerShell on Windows. cwd: the project folder.
$GW = '<full path to the plugin>\engine\groundwork\gw.py'
$ROOT = (Get-Location).Path
python $GW --root $ROOT status
```

```bash
# runs in: bash or zsh on macOS or Linux. cwd: the project folder.
GW='<full path to the plugin>/engine/groundwork/gw.py'
ROOT="$PWD"
python3 "$GW" --root "$ROOT" status
```

Expected: `no flows yet`, or one line per existing flow. If Python is missing, say so and stop.

## The loop

Each command prints one status line: stage, status, and any open gate with its token and the
verdicts it accepts. Run the next command only after you have read it.

1. **New.** `gw.py --root ROOT new <project> "<idea>"`. Creates the flow at stage `idea`.
   Refused if the slug exists: relay the message and leave the existing flow as it is; it
   holds the user's earlier decisions.
2. **Shape the idea.** Write down what the idea is, who it is for and how you will know it
   worked. Keep it in the project, for example `ROOT\docs\<slug>.md`. Then
   `advance <project>/<slug>` twice: idea to design, design to breakdown.
3. **Break it down.** Show the plan as a short numbered list of steps.
   `advance <project>/<slug> --steps "step one;step two"` starts step one. No gate opens
   here: the first spec gate shows the whole breakdown and approves it with that step's spec.
4. **The spec gate (gate 1 of 2).** For the current step, write a short spec: what changes,
   what stays, how you check it. `advance` opens `spec`. Ask, with the meaning beside each
   verdict: approve; reject (do not build this step); park (pause, answer later). Record with
   `gate <project>/<slug> approve`, then
   `advance <project>/<slug> --spec <path to the spec>` records the spec and says the work
   step is ready.
5. **The work.** Do the step, here, with the user watching. Run the checks the spec names.
6. **The accept gate (gate 2 of 2).** `advance` opens `accept`. Show what changed and what
   the checks said. Ask: ship (accept and merge); hold (send back); park (pause, answer
   later). Only `ship` accepts the change and writes the receipt. Groundwork merges nothing:
   tell the user the merge is theirs to do. Record with
   `gate <project>/<slug> ship "<note>"`.
7. **Learn, without a gate.** `advance` moves to learn. Write what held and what broke, in
   `learnings.md` in the flow folder. `advance` then goes to the next step's spec, or closes
   the flow after the last step. Adapting happens at the next spec gate.
8. **Close.** A flow closes after the last step's learn. To drop a flow that will never
   finish: `close <project>/<slug> "<reason>"`. A reason is required.

## Hard rules

- **Persist, then prompt.** Open the gate with `advance` first. Only then ask the user. End
  the turn after asking: a gate is a saved state, not a running process.
- **The user decides every gate.** Never pass a verdict the user did not give. An answer in
  another session or a note in a file is not a verdict.
- **First decision wins.** A gate that is already decided cannot be changed. The command says
  so and changes nothing.
- **Repeating is safe.** If a command is refused, relay its message in full and stop. Never
  "repair" state by hand: the engine's own checks are what make the record and the receipt
  worth trusting.
- **A receipt exists only for `ship`.** If the user asks for one after `hold`, `park` or
  `close`, say there is none and why.

## Reply format

Short, outcome first:

- Say what changed in one line, then what the user must decide, in plain words.
- Name a step only when it failed or changed the plan. Commands that worked need no mention.
- Give the receipt path in full when one was written, and say it is self-issued, not signed.
- End with the single next step and whose move it is.

## Where the receipt lands

`ROOT\flows\<project>\<slug>\receipts\step-01.html` (readable) and `step-01.json` (data), one
pair per accepted step. The JSON holds the decision, the note, the time and a hash of the flow
log up to that point.

## When it does not work

- `refused: ...`: the engine rejected the call. Read the reason. Common ones: a verdict that
  does not fit the open gate, an unfinished gate.
- A gate shown as retired (an old G3) is dropped on the next `advance`.
- `python` not found: install Python 3.9 or newer, then rerun.
- Windows only, for the time-window sweep: `python -m pip install tzdata`. The loop above does
  not need it.
