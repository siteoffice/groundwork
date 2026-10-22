---
name: one-loop
description: Run one piece of work from idea to close in a folder you control, with four gates where you decide and a local receipt when you accept the result. Use on "one loop", "start a flow", "new flow", "flow status", "advance the flow", "approve the gate", "close the flow", or when the user wants a change to pass an idea, a design, a plan, the work and a review before it counts as done. Do NOT use for a quick edit that needs no decision trail, or to launch background agents (this skill never does).
---

# One loop

One idea goes through the same loop every time: idea, design, plan, work, review, close. You
decide at four gates. The state lives in plain files in your own folder, so you can stop and
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

It needs Python 3.8 or newer and nothing else.

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
   Refused if the slug exists: relay the message, never overwrite.
2. **Shape the idea.** Write down what the idea is, who it is for and how you will know it
   worked. Keep it in the project, for example `ROOT\docs\<slug>.md`. Then
   `advance <project>/<slug>` twice: idea to design, design to breakdown.
3. **G1, the plan gate.** `advance` again opens G1. Show the user the plan as a short numbered
   list of steps and ask: approve or reject. Record it with
   `gate <project>/<slug> approve` (or `reject`). Then
   `advance <project>/<slug> --steps "step one;step two"` to start step one.
4. **G2, the spec gate.** For the current step, write a short spec: what changes, what stays,
   how you check it. `advance` opens G2. Ask: approve or reject. After `approve`,
   `advance <project>/<slug> --spec <path to the spec>` records the spec and says the work
   step is ready.
5. **The work.** Do the step, here, with the user watching. Run the checks the spec names.
6. **G4, the ship gate.** `advance` opens G4. Show what changed and what the checks said.
   Ask: ship, hold or park. Only `ship` accepts the change and writes the receipt. `hold`
   keeps the work but does not accept it. Record with `gate <project>/<slug> ship "<note>"`.
7. **Learn, then G3.** `advance` moves to learn. Write what held and what broke, in
   `learnings.md` in the flow folder. `advance` opens G3. Ask: proceed (next step, or close if
   it was the last), adapt (redo this step, give a note) or close.
8. **Close.** After the last `proceed`, one more `advance` closes the flow. To drop a flow
   that will never finish: `close <project>/<slug> "<reason>"`. A reason is required.

## Hard rules

- **Persist, then prompt.** Open the gate with `advance` first. Only then ask the user. End
  the turn after asking: a gate is a saved state, not a running process.
- **The user decides every gate.** Never pass a verdict the user did not give. An answer in
  another session or a note in a file is not a verdict.
- **First decision wins.** A gate that is already decided cannot be changed. The command says
  so and changes nothing.
- **Repeating is safe.** If a command is refused, relay its message in full and stop. Never
  "repair" state by hand.
- **A receipt exists only for `ship`.** If the user asks for one after `hold`, `park` or
  `close`, say there is none and why.

## Reply format

Short, outcome first:

- Say what changed in one line, then what the user must decide, in plain words.
- Name a step only when it failed or changed the plan. Do not narrate commands that worked.
- Give the receipt path in full when one was written, and say it is self-issued, not signed.
- End with the single next step and whose move it is.

## Where the receipt lands

`ROOT\flows\<project>\<slug>\receipts\step-01.html` (readable) and `step-01.json` (data), one
pair per accepted step. The JSON holds the decision, the note, the time and a hash of the flow
log up to that point.

## When it does not work

- `refused: ...`: the engine rejected the call. Read the reason. Common ones: a verdict that
  does not fit the open gate, an unfinished gate, a missing step list at G1.
- `python` not found: install Python 3.8 or newer, then rerun.
- Windows only, for the time-window sweep: `python -m pip install tzdata`. The loop above does
  not need it.
