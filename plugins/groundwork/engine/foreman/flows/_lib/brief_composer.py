r"""brief_composer.py - pure hub_agent_update brief composer (flow-system slice f).

compose_brief(sprint_report, action_log, learnings, meta) -> str

Turns the learn-stage inputs into a Markdown brief that conforms to the eight
sections of
C:\work\10_holding\.claude\skills\hub-update\reference\input_template.md.
Pure: inputs in, string out. No file write, no network.

Hard rules enforced (hub-update SKILL.md lines 43-62):
  - full C:\work\... paths in prose: caller-supplied paths are preserved verbatim;
    the composer never rewrites or fabricates a path;
  - no em dash (U+2014) anywhere in the output (final strip pass);
  - no invented result: a finding is tagged [RESULT] only when the caller marks it
    measured with a concrete value; otherwise [STATE] (the safe default);
  - no secret persisted: a secret-shaped token in any input is detected, the fact
    is noted in section 3, and a final redaction pass scrubs it from the output.

The learn-stage file write and the /hub-update emit line that CONSUME this string
live in the /flow command surface (slice c) and are out of scope here.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

EM_DASH = "\u2014"
REDACTION = "[REDACTED]"

# Secret-shaped token patterns (documented set; see the plan's "Documented
# decisions"). Targeted shapes only, to keep false positives near zero: sprint IDs
# (a__b__20260714T...), short SHAs (12c3622), and C:\work\... paths must survive.
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),                 # GitHub token
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),               # GitHub fine-grained PAT
    re.compile(r"sk-[A-Za-z0-9-]{20,}"),                       # OpenAI/Anthropic-style key
    re.compile(r"AKIA[0-9A-Z]{16}"),                           # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),               # Slack token
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{20,}=*"),          # bearer token
    re.compile(r"AccountKey=[^;\s]+"),                         # Azure storage key
    re.compile(                                                # JWT / consent token
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
]
# URL query params whose VALUE is a secret; keep the key, redact the value.
SECRET_PARAM_RE = re.compile(
    r"([?&](?:code|access_token|refresh_token|client_secret|id_token)=)[^&\s]+",
    re.IGNORECASE,
)
# key = value / key: value assignment of a secret-named field.
SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b(\s*[=:]\s*)"
    r"[\"']?[A-Za-z0-9/+._~-]{16,}[\"']?"
)


# --- hard-rule sanitizers --------------------------------------------------

def strip_em_dashes(text: str) -> str:
    """Replace every em dash (U+2014) with a hyphen (repo hard rule)."""
    return text.replace(EM_DASH, "-")


def find_secrets(text: str) -> List[str]:
    """Return the secret-shaped substrings found in text (empty list if none)."""
    hits = []  # type: List[str]
    for pat in SECRET_PATTERNS:
        hits.extend(m.group(0) for m in pat.finditer(text))
    hits.extend(m.group(0) for m in SECRET_PARAM_RE.finditer(text))
    hits.extend(m.group(0) for m in SECRET_ASSIGN_RE.finditer(text))
    return hits


def redact_secrets(text: str) -> str:
    """Replace every secret-shaped token with the redaction placeholder.

    URL params and key=value assignments keep their name and lose only the value,
    so the brief can still say a secret was handled without exposing it.
    """
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub(REDACTION, out)
    out = SECRET_PARAM_RE.sub(r"\1" + REDACTION, out)
    out = SECRET_ASSIGN_RE.sub(r"\1\2" + REDACTION, out)
    return out


# --- finding classification + section 4 ------------------------------------

def tag_finding(finding: dict) -> str:
    """[RESULT] only for an explicitly measured finding with a concrete value.

    The safe default is [STATE] (in flight, gets a pending flag). The composer
    never upgrades a finding to a result on its own: no invented results
    (hub-update SKILL.md lines 55-58; spec lines 49-50).
    """
    measured = bool(finding.get("measured"))
    value = finding.get("value")
    if measured and value not in (None, ""):
        return "[RESULT]"
    return "[STATE]"


def render_findings(findings: List[dict]) -> str:
    """Section 4: one bullet per finding, each tagged [RESULT] or [STATE].

    A [RESULT] carries its measured value (and source, when given) in parens so
    the hub can fold the number in. A [STATE] carries only its text.
    """
    lines = ["## 4. Findings worth propagating", ""]
    if not findings:
        lines.append("None this cycle.")
        return "\n".join(lines)
    for f in findings:
        tag = tag_finding(f)
        text = str(f.get("text", "")).strip()
        if tag == "[RESULT]":
            value = str(f.get("value", "")).strip()
            source = str(f.get("source", "")).strip()
            suffix = " (" + value
            suffix += ("; source: " + source + ")") if source else ")"
            lines.append("- " + tag + " " + text + suffix)
        else:
            lines.append("- " + tag + " " + text)
    return "\n".join(lines)


# --- framing + section renderers -------------------------------------------

def render_header(meta: dict) -> str:
    """The pre-section header block (template lines 11-17). Hyphen, not em dash.

    `meta["owner"]` is caller-supplied (no default owner name baked in here);
    an absent owner renders as an empty field rather than guessing one.
    """
    read_order = meta.get("read_order") or []
    ro = "; ".join(read_order) if read_order else "this file"
    return "\n".join([
        "# Hub Agent Update - ZeroNonsense.dev",
        "",
        "**Date:** " + str(meta.get("date", "")) + " - **Owner:** " + str(meta.get("owner", "")),
        "**Project:** " + str(meta.get("project", "")),
        "**Purpose:** " + str(meta.get("purpose", "")),
        "**Read order:** " + ro,
        "**Supersedes:** " + str(meta.get("supersedes", "nothing")),
    ])


def render_headline(meta: dict) -> str:
    """Section 1: one or two sentences, the change and its exact state."""
    return "\n".join(
        ["## 1. Headline", "", str(meta.get("headline", "")).strip()])


def render_changed(changed: Optional[dict]) -> str:
    """Section 2: commits, branch, files touched, untracked, all full paths."""
    lines = ["## 2. What changed on disk", ""]
    changed = changed or {}
    for c in (changed.get("commits") or []):
        lines.append("- **Committed** in `" + str(c.get("repo", "")) + "`: `"
                     + str(c.get("sha", "")) + "` " + str(c.get("message", "")))
    branch = changed.get("branch")
    if branch:
        lines.append("- **On a branch, not merged:** " + str(branch))
    files = changed.get("files") or []
    if files:
        lines.append("- **Files touched** (full paths):")
        for p in files:
            lines.append("  - `" + str(p) + "`")
    untracked = changed.get("untracked") or []
    if untracked:
        lines.append("- **Untracked / outside a repo:**")
        for p in untracked:
            lines.append("  - `" + str(p) + "`")
    if len(lines) == 2:
        lines.append("Nothing changed on disk this cycle.")
    return "\n".join(lines)


def render_not_done(not_done: Optional[List[str]], secret_handled: bool) -> str:
    """Section 3: the fence. Records the secret-handled fact when applicable."""
    lines = ["## 3. What did NOT happen (and why)", ""]
    items = list(not_done or [])
    if secret_handled:
        items.append(
            "A secret-shaped token was present in the sprint inputs; it was "
            "detected and dropped, not written into this brief.")
    if not items:
        lines.append("Nothing to fence this cycle.")
    else:
        for it in items:
            lines.append("- " + str(it))
    return "\n".join(lines)


def render_hub_decision(decision: Optional[dict]) -> str:
    """Section 5: paste-ready decision block, or an explicit None."""
    lines = ["## 5. Hub-level decision (if any)", ""]
    if not decision:
        lines.append("None. No cross-project, park/unpark, goal #3, positioning, "
                     "or anonymity call this cycle.")
        return "\n".join(lines)
    lines.append("**Decision:** " + str(decision.get("decision", "")))
    lines.append("**Context:** " + str(decision.get("context", "")))
    lines.append("**Alternatives considered:** "
                 + str(decision.get("alternatives", "")))
    lines.append("**Consequences:** " + str(decision.get("consequences", "")))
    lines.append("**Links:** " + str(decision.get("links", "")))
    return "\n".join(lines)


def render_next_updates(updates: Optional[List[str]]) -> str:
    """Section 6: canonical files the next hub read should move."""
    lines = ["## 6. What the next hub read should update", ""]
    updates = list(updates or [])
    if not updates:
        lines.append("Nothing this cycle.")
    else:
        for u in updates:
            lines.append("- " + str(u))
    return "\n".join(lines)


def render_open_flags(flags: Optional[List[str]]) -> str:
    """Section 7: anything still open for next session."""
    lines = ["## 7. Open flags for next session", ""]
    flags = list(flags or [])
    if not flags:
        lines.append("None.")
    else:
        for fl in flags:
            lines.append("- " + str(fl))
    return "\n".join(lines)


def render_related(related: Optional[List[str]]) -> str:
    """Section 8: specs, plans, prior briefs, dashboards, all full paths."""
    lines = ["## 8. Related files", ""]
    related = list(related or [])
    if not related:
        lines.append("None.")
    else:
        for r in related:
            lines.append("- `" + str(r) + "`")
    return "\n".join(lines)


# --- top-level composer ----------------------------------------------------

def compose_brief(sprint_report: str, action_log: str,
                  learnings: dict, meta: dict) -> str:
    """Pure: learn-stage inputs in, template-conformant brief string out.

    Assembles the header plus the eight template sections in order, records in
    section 3 that a secret was handled if any input carried one, then runs the
    final hard-rule pass (redact secrets, strip em dashes). No file write, no
    network (spec lines 53-54, 79).
    """
    learnings = learnings or {}
    meta = meta or {}

    # Detect secrets across ALL inputs, raw report and log included, so section 3
    # can honestly record that a secret was handled (template note, input_template
    # lines 70-71). The raw report/log prose is never copied into the brief; only
    # this boolean crosses over.
    serialized_inputs = "\n".join([
        sprint_report or "",
        action_log or "",
        json.dumps(learnings, ensure_ascii=False, sort_keys=True),
        json.dumps(meta, ensure_ascii=False, sort_keys=True),
    ])
    secret_handled = bool(find_secrets(serialized_inputs))

    sections = [
        render_header(meta),
        render_headline(meta),
        render_changed(meta.get("changed")),
        render_not_done(learnings.get("not_done"), secret_handled),
        render_findings(learnings.get("findings") or []),
        render_hub_decision(meta.get("hub_decision")),
        render_next_updates(meta.get("next_updates")),
        render_open_flags(learnings.get("open_flags")),
        render_related(meta.get("related")),
    ]
    brief = "\n\n".join(sections) + "\n"

    # Final hard-rule pass: no secret token survives, no em dash survives.
    brief = redact_secrets(brief)
    brief = strip_em_dashes(brief)
    return brief
