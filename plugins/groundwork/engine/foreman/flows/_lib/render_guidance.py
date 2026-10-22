"""render_guidance.py - the guidance rung renderer (slice e).

Spec: (design spec, not shipped)

One public function:

    render(guidance_key, level, why, flow=None, guidance_root=None) -> str

It turns a NextAction's `guidance_key` and the operator's rung into the block
the CLI prints under the banner. It reads static fragment files and NOTHING
else: no clock, no network, no model, no write of any kind. A fragment that is
not there is not an error, it is a rung the operator has not needed yet, so the
render degrades to the action's own `why` string and logs one warning. This
function must never raise: it sits on a presentation path, and a missing
markdown file is not worth losing a chain over.

Fragments accumulate reactively. Ten exist today, one per happy-path key; the
rest of the keys (`halt.*`, `gate_consume.*`, `sprint_recover.*`,
`flow_advance.*`, and the tail kinds) fall back by design until a real operator
stalls on one and it gets written.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Optional

import foreman_plan as fp  # GUIDE_LEVELS only; it is pure and does no I/O

# flows/_lib/render_guidance.py -> flows/_lib -> flows/_lib/guidance
GUIDANCE_ROOT = pathlib.Path(__file__).resolve().parent / "guidance"

# Flow ids whose road-ahead block has already been shown in THIS process.
# Deliberately not persisted: the block is session orientation, so a new
# session earns it again, and a read path stays a read path. See the plan's
# Task 3 note for the full reasoning.
_ROAD_AHEAD_SEEN = set()

_ROAD_AHEAD_HEADING = "The road ahead for this flow:"
_NO_STEPS = "  (no steps recorded yet)"


def _fragment_path(guidance_key: str, guidance_root) -> Optional[pathlib.Path]:
    """The fragment path for `guidance_key`, or None when the key is not a
    plain filename.

    `guidance_key` arrives from NextAction and is composed straight into a
    path, so anything carrying a separator, a parent reference or nothing at
    all is refused rather than resolved. foreman_plan already halts rather
    than emitting a malformed key, so this is the belt to that braces.
    """
    if not guidance_key or not isinstance(guidance_key, str):
        return None
    if guidance_key != pathlib.PurePosixPath(guidance_key).name:
        return None
    if "\\" in guidance_key or guidance_key in (".", ".."):
        return None
    root = pathlib.Path(guidance_root) if guidance_root else GUIDANCE_ROOT
    return root / (guidance_key + ".md")


def _fragment(guidance_key: str, why: str, guidance_root) -> str:
    """The fragment body for `guidance_key`, or `why` plus one stderr warning.

    Every failure mode collapses to the same fallback: no directory, no file,
    an unreadable file, an empty file. The operator gets the `why` either way
    and the warning says which key wanted a fragment, which is exactly the
    list the first-operator test is meant to produce.
    """
    path = _fragment_path(guidance_key, guidance_root)
    text = ""
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        except UnicodeDecodeError:
            text = ""
    if text.strip():
        return text.strip()
    print("render_guidance: no fragment for guidance_key {!r}, falling back "
          "to the why string".format(guidance_key), file=sys.stderr)
    return why


def _road_ahead(flow: dict) -> str:
    """The one-time orientation block, built from flow['steps'] and nothing
    else: index, title, status, no new data source."""
    lines = [_ROAD_AHEAD_HEADING]
    steps = (flow or {}).get("steps") or []
    for position, step in enumerate(steps):
        step = step if isinstance(step, dict) else {}
        index = step.get("index")
        number = (index if isinstance(index, int) else position) + 1
        lines.append("  {}. {} [{}]".format(
            number, step.get("title") or "(untitled)",
            step.get("status") or "unknown"))
    if len(lines) == 1:
        lines.append(_NO_STEPS)
    return "\n".join(lines)


def render(guidance_key: str, level: str, why: str,
           flow: Optional[dict] = None, guidance_root=None) -> str:
    """The guidance block for one action at one rung. Never raises.

    silent   -> "". The JSON output path already carries every field guidance
                would summarize, so this function is not called there; it
                returns "" defensively if it is, and an unrecognised level is
                treated the same way (quiet is the safe direction on a
                presentation path).
    terse    -> `why`, verbatim.
    coached  -> the fragment at <guidance_root>/<guidance_key>.md, else `why`
                plus one stderr warning.
    teaching -> the same, plus the road-ahead block, but only the first time
                this process renders anything for flow['flow_id'].
    """
    if level not in fp.GUIDE_LEVELS or level == "silent":
        return ""
    if level == "terse":
        return why
    body = _fragment(guidance_key, why, guidance_root)
    if level != "teaching":
        return body
    flow_id = (flow or {}).get("flow_id")
    if not flow_id or flow_id in _ROAD_AHEAD_SEEN:
        return body
    _ROAD_AHEAD_SEEN.add(flow_id)
    return body + "\n\n" + _road_ahead(flow)
