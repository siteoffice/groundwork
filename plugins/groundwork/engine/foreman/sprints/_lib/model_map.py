"""Pure parser + resolver for /sprint per-phase model routing.

No I/O, no model calls, no network. See
docs/superpowers/specs/2026-07-18-sprint-model-routing-design.md for the
routing table and rationale, and
docs/superpowers/specs/2026-07-25-foreman-model-routing-v1.1-design.md for the
registry below. Wiring this into the live /sprint dispatch loop is a separate,
manual change to ~/.claude/commands/sprint.md.

Model policy
------------
Specs and flows name model CLASSES ONLY (opus, sonnet, haiku, fable) and never
a concrete model id. The dispatch passes a CLASS: the Claude Code subagent
model override accepts a class, not an id, so nothing here selects the model.

CLASS_TO_MODEL therefore records the concrete id each class is EXPECTED to
resolve to. A run logs that expected id next to the observed actual, so a
below-class run, or an alias that has silently repointed, becomes visible in
the receipts. That is the registry's whole job. Editing CLASS_TO_MODEL does
NOT change which model runs; it changes what the receipts say we expected.

resolve_model() is pure and does not verify that a model id exists.
"""
from __future__ import annotations

from typing import Dict, Optional

VALID_CLASSES = {"opus", "sonnet", "haiku", "fable"}

DEFAULT_TABLE: Dict[str, str] = {
    "spec": "opus",
    "plan": "opus",
    "dev": "sonnet",
    "test": "haiku",
    "verify": "opus",
    "iterate": "sonnet",
    "report": "haiku",
}

# Expected concrete id per class, for receipts only (see "Model policy" above).
# Bare aliases uniformly: only haiku has a dated form (claude-haiku-4-5-20251001)
# and a mixed registry would be inconsistent by construction. An alias that
# silently repoints is exactly the drift the receipts are meant to expose.
# fable has an entry for coverage even though no DEFAULT_TABLE phase routes to
# it today. NOT confirmed against a live GET /v1/models: that is controller work.
# This table goes stale on Anthropic's schedule, not this repo's; see
# 00_company/2026-08-08_foreman_platform_and_model_staleness_decision.md for
# why v1 documents the staleness rather than fixing it with a live lookup.
CLASS_TO_MODEL: Dict[str, str] = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",
}


def parse(map_str: str) -> Dict[str, str]:
    """Parse "plan=opus,dev=sonnet,report=haiku" into a phase->class dict.

    Raises ValueError on an unknown phase key, an unknown class, or a
    malformed "phase=class" pair.
    """
    result: Dict[str, str] = {}
    if not map_str:
        return result
    for raw_pair in map_str.split(","):
        pair = raw_pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(
                f"invalid model-map entry (expected phase=class): {pair!r}"
            )
        phase, cls = (part.strip() for part in pair.split("=", 1))
        if phase not in DEFAULT_TABLE:
            raise ValueError(f"unknown phase in model-map: {phase!r}")
        if cls not in VALID_CLASSES:
            raise ValueError(f"unknown model class in model-map: {cls!r}")
        result[phase] = cls
    return result


def resolve(phase: str, override_map: Dict[str, str]) -> str:
    """Return the routed class for `phase`.

    `override_map` wins when it lists `phase`; otherwise falls back to
    `DEFAULT_TABLE`. `resolve(phase, {})` is always the table default:
    no `--model-map` given means no behavior change.
    """
    if override_map and phase in override_map:
        return override_map[phase]
    return DEFAULT_TABLE.get(phase)


def resolve_model(cls: str, pin: Optional[Dict[str, str]] = None) -> str:
    """Return the concrete model id EXPECTED for class `cls`.

    `pin` (class -> id) wins when it names `cls`, letting a caller record a
    dated id for one class without editing the registry. Raises ValueError on
    an unknown class, pinned or not. Pure: does not check availability.
    """
    if cls not in CLASS_TO_MODEL:
        raise ValueError(
            f"unknown model class: {cls!r} (known: {sorted(CLASS_TO_MODEL)})"
        )
    if pin and cls in pin:
        return pin[cls]
    return CLASS_TO_MODEL[cls]
