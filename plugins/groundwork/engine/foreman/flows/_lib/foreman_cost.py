"""foreman_cost.py - the budget fence's cost source (spec slice b, "Budget fence").

Spec: (design spec, not shipped)
      "Cost comes from BurnRate, not from a guess."

The join, one link at a time:

    flow  ->  foreman_status.resolve_sprint  ->  sprint.json["worktree_path"]
          ->  C:\\work\\50_projects\\burnrate\\db\\sessions.json[].cwd   (verbatim equal)
          ->  session_id                                   (main plus every agent-*)
          ->  C:\\work\\50_projects\\burnrate\\db\\turns.json[].session_id
          ->  dedup by (session_id, request_id)
          ->  sum(turn_cost_usd)

Two caveats that belong in every number this module returns, and are repeated
in the fence's halt text:

1. BurnRate ingests on an HOURLY scheduled job (changed from daily on
   2026-08-08), so a reading taken mid-sprint can be up to an hour behind.
   This is a between-boundary fence, never a real-time circuit breaker on a
   runaway sprint.
2. The dollar figure is Anthropic public per-MTok list price applied to
   subscription-plan usage. BurnRate's own CLAUDE.md says "token burn is not
   dollar cost until Phase 2". Call it estimated API-equivalent spend, not
   money that left an account.

Fail-open by contract: absent BurnRate, an unreadable store, a sprint with no
worktree_path or no matching session all return None, and None never trips a
fence. A driver must not be wedged by a sibling repository being absent.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Optional, Tuple

import roots

DEFAULT_BURNRATE_DB = str(roots.dev_root() / "BurnRate" / "db")

# Vendored from <devroot>\BurnRate\src\cowork_estimator.py on 2026-08-02,
# refreshed 2026-08-08 to match BurnRate's 2026-08-06 pricing correction
# (docs\pricing_and_cost_basis.md): Opus is 5/25, not 15/75, and fable-5 has
# had its own published rate (10/50) since that fix, no longer proxied as opus.
# Refreshed 2026-10-02 to BurnRate's 2026-09-29 table (opus 4/20, sonnet 2/10);
# BurnRate's own comment says an Opus 5 session (5/25) is under-counted by 20%
# by a family table, and the same holds here.
# USD per MTok: (input, output, cache_read, cache_write).
# test_foreman_cost.test_the_vendored_price_table_has_not_drifted_from_burnrate
# is the drift guard; it skips when BurnRate is not on the machine.
# This table goes stale on Anthropic's pricing schedule and has no meaning for
# a non-Claude model; see
# 00_company/2026-08-08_foreman_platform_and_model_staleness_decision.md.
CATEGORY_PRICE = {
    "opus":    (4, 20, 0.2, 5),
    "fable-5": (10, 50, 1.0, 12.5),
    "sonnet":  (2, 10, 0.2, 2.5),
    "haiku":   (1, 5, 0.1, 1.25),
}

# Per-model-id prices, same tuple order, keyed by the exact lowercase model id as
# BurnRate's turns.json records it. This is the table BurnRate's cowork_estimator.py
# says "BurnMon prices per model id" about; BurnMon's own book
# (burnmon\internal\pricing\books\anthropic_api.json, 2026-09-29) has every id
# below. Each number except claude-fable-5-1's is taken from BurnRate's
# docs\pricing_and_cost_basis.md:
# the Opus 5 row (5/25, cache write 6.25, cache read 0.50), and the 2026-09-29
# update for Opus 5.5 (4/20, 5.00, 0.20) and Sonnet 5 / 5.5 (2/10, 2.50, 0.20).
# claude-fable-5-1 is the exception: BurnMon's book is its only per-id source
# (10/50, cache write 12.5, cache read 0.25). BurnRate's Fable 5 family row has
# cache read 1.00, which CATEGORY_PRICE keeps. 0.25 is 2.5% of input where every
# other row is 10%, so that one figure is unconfirmed.
# A model id not listed here is priced at its family price (CATEGORY_PRICE), so a
# new or unsourced id is never hidden. claude-opus-5 is the one row that differs
# from its family: the family table under-counts it by 20%.
# test_foreman_cost.test_the_vendored_model_price_table_has_not_drifted_from_burnrate
# is the drift guard for this table.
MODEL_PRICE = {
    "claude-opus-5":     (5, 25, 0.5, 6.25),
    "claude-opus-5-5":   (4, 20, 0.2, 5),
    "claude-sonnet-5":   (2, 10, 0.2, 2.5),
    "claude-sonnet-5-5": (2, 10, 0.2, 2.5),
    "claude-fable-5-1":  (10, 50, 0.25, 12.5),
}


def model_family(model: str) -> str:
    """Lowercase and match fable before opus before sonnet before haiku,
    defaulting to sonnet (the middle of the price range) for an unrecognised
    string: an unknown model must neither hide a cost nor invent one."""
    m = (model or "").lower()
    if "fable" in m:
        return "fable-5"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


# A dated snapshot id (claude-opus-5-20260601) is priced as its base id. That is an
# assumption, not a sourced fact: no file this table is checked against prices a
# dated id, so a dated snapshot that costs differently would be mispriced here.
DATED_ID_SUFFIX = re.compile(r"-\d{8}$")


def model_price(model: str) -> Tuple[float, float, float, float]:
    """The model id's price when MODEL_PRICE has one (a trailing -YYYYMMDD is
    dropped first), else the price of its family: a per-id price is used only
    where a source exists."""
    lowered = (model or "").lower()
    return (MODEL_PRICE.get(lowered)
            or MODEL_PRICE.get(DATED_ID_SUFFIX.sub("", lowered))
            or CATEGORY_PRICE[model_family(model)])


def turn_cost_usd(model: str, input_tokens, cache_write_tokens, cache_read_tokens,
                  output_tokens) -> float:
    price_in, price_out, price_cache_read, price_cache_write = model_price(model)
    total = (input_tokens * price_in + output_tokens * price_out
             + cache_read_tokens * price_cache_read
             + cache_write_tokens * price_cache_write)
    return total / 1e6


def dedup_turns(turns: list) -> list:
    """Collapse duplicate rows for the same API request.

    Keyed on (session_id, request_id), keeping the row with the highest total
    tokens (input + cache_creation + cache_read + output). This is a
    deliberate divergence from BurnRate's own _dedup_by_session_request,
    which ties on output_tokens alone: total token weight is the more
    correct tie-break for a cost sum, since input and cache tokens price
    differently from output tokens and a row with more output but far fewer
    input/cache tokens can carry the lower total cost.
    """
    best = {}
    order = []
    for t in turns:
        key = (t.get("session_id"), t.get("request_id"))
        if key not in best:
            best[key] = t
            order.append(key)
        else:
            existing_total = (best[key].get("input_tokens", 0)
                              + best[key].get("cache_creation_tokens", 0)
                              + best[key].get("cache_read_tokens", 0)
                              + best[key].get("output_tokens", 0))
            candidate_total = (t.get("input_tokens", 0)
                              + t.get("cache_creation_tokens", 0)
                              + t.get("cache_read_tokens", 0)
                              + t.get("output_tokens", 0))
            if candidate_total >= existing_total:
                best[key] = t
    return [best[k] for k in order]


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def session_ids_for_worktree(worktree_path, sessions: list) -> set:
    target = _norm(worktree_path)
    return {s.get("session_id") for s in sessions
            if _norm(s.get("cwd", "")) == target}


def cost_for_worktree(worktree_path, sessions: list, turns: list) -> Optional[float]:
    """None when the worktree path is falsy or no session matches: unknown
    and free are different answers, and only one of them may pass a budget
    fence. 0.0 when a session matches but has no (assistant) turns: a known
    zero, not an unknown."""
    if not worktree_path:
        return None
    ids = session_ids_for_worktree(worktree_path, sessions)
    if not ids:
        return None
    total = 0.0
    for t in dedup_turns(turns):
        if t.get("role") != "assistant":
            continue
        if t.get("session_id") not in ids:
            continue
        total += turn_cost_usd(
            t.get("model"), t.get("input_tokens", 0),
            t.get("cache_creation_tokens", 0), t.get("cache_read_tokens", 0),
            t.get("output_tokens", 0))
    return total


def load_store(db_dir) -> Tuple[list, list]:
    """Read sessions.json and turns.json from db_dir, returning ([], []) if
    either is absent, unreadable, not JSON, or not a list. Fail-open: this
    must never raise."""
    db_dir = pathlib.Path(db_dir)
    sessions = _load_json_list(db_dir / "sessions.json")
    turns = _load_json_list(db_dir / "turns.json")
    if sessions is None or turns is None:
        return ([], [])
    return (sessions, turns)


def _load_json_list(path: pathlib.Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return data


def sprint_cost_usd(sprint, db_dir=None) -> Optional[float]:
    """None before touching the disk when sprint is falsy or has no
    worktree_path, which is what keeps the 51 MB read off every invocation."""
    if not sprint:
        return None
    worktree_path = sprint.get("worktree_path")
    if not worktree_path:
        return None
    sessions, turns = load_store(db_dir if db_dir is not None else DEFAULT_BURNRATE_DB)
    return cost_for_worktree(worktree_path, sessions, turns)
