## Waiting on a sprint that is already running

This step is at phase sprint and the driver has nothing to do but wait. Two different states share
this one guidance key, and the WHY line on the stop tells you which:

- **A launch was authorised but no `sprint.json` exists yet.** The DO field still carries the
  authorised `/sprint ...` line. If nobody has run it in a live session, it will sit here
  indefinitely: the driver never spawns a process and never runs it for you. Paste it into a
  Claude Code session yourself, or, once built, let the headless runner pick it up on its next poll
  (see `docs\superpowers\specs\2026-08-24-foreman-headless-sprint-runner-design.md`).
- **A `sprint.json` exists but its status is not yet terminal.** A sprint is genuinely mid-flight:
  planning, developing, testing or verifying. There is nothing to run here; re-running the driver
  just re-confirms the same wait. `render_dashboard.py status <dir> --watch 10` shows live progress
  without spamming the driver, and `foreman_run.py run ... --await` polls automatically until the
  sprint reaches a terminal status.

Either way, `AUTHORISED no` on this stop means the driver is not the thing that will move this
forward. A human's session, or the poller once it exists, is.
