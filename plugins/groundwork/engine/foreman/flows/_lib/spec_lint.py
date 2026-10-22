"""Structural linter for /sprint spec files (flow-system slice b).

`lint(spec_path)` returns a list of human-readable failure strings; an empty
list means the spec passes every structural rule. The linter does I/O only to
read the spec file and (rules 3 and 4) to stat the target project directory
under DEV_ROOT. No model call, no network. See
docs/superpowers/specs/2026-07-18-grill-specs-skill-design.md for the rules.
"""
from __future__ import annotations

import pathlib
import re
from typing import List, Optional, Tuple

import roots

# Root under which /sprint projects live. Module-level so tests can point it at
# a temporary directory instead of the real dev tree. See roots.dev_root for
# the resolution precedence (explicit, ZND_DEV_ROOT env var, hub root's parent).
DEV_ROOT = roots.dev_root()

EM_DASH = chr(0x2014)

TEST_CONFIG_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
)

_INVOCATION_RE = re.compile(r"^/sprint\s+(\S+)\s+(.+?)\s*$")


def _is_out_of_scope(h: str) -> bool:
    return "out of scope" in h or "out-of-scope" in h


# Each required section: (label, predicate over a lowercased heading string).
REQUIRED_SECTIONS = (
    ("problem/objective", lambda h: "problem" in h or "objective" in h),
    ("scope", lambda h: "scope" in h and not _is_out_of_scope(h)),
    ("tests", lambda h: "test" in h),
    ("verification", lambda h: "verif" in h),
    ("out-of-scope", _is_out_of_scope),
    ("success-criteria", lambda h: "success" in h),
)


def _headings(text: str) -> List[str]:
    """Return lowercased ATX ('#'-prefixed) heading texts."""
    out: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(stripped.lstrip("#").strip().lower())
    return out


def _check_required_sections(text: str) -> List[str]:  # rule 1
    headings = _headings(text)
    failures: List[str] = []
    for label, pred in REQUIRED_SECTIONS:
        if not any(pred(h) for h in headings):
            failures.append(f"missing required section: {label}")
    return failures


def _check_source_header(text: str) -> List[str]:  # rule 2
    for line in text.splitlines():
        if re.match(r"^\s*Source\b", line, re.IGNORECASE):
            return []
    return ["missing Source header line"]


def _check_em_dash(text: str) -> List[str]:  # rule 5
    if EM_DASH in text:
        return ["em dash (U+2014) found; use a plain hyphen or rewrite"]
    return []


def _last_content_line(text: str) -> Optional[str]:
    """Last non-blank line that is not a ``` code-fence marker."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        return stripped
    return None


def _parse_invocation(text: str):
    """Return (project, spec_path) from the trailing /sprint line, or None."""
    last = _last_content_line(text)
    if last is None:
        return None
    m = _INVOCATION_RE.match(last)
    if not m:
        return None
    return m.group(1), m.group(2)


def _check_target_project_test_config(text: str) -> List[str]:  # rule 3
    parsed = _parse_invocation(text)
    if parsed is None:
        # No usable invocation: rule 4 reports it; stay silent to avoid a dup.
        return []
    project, _spec_path = parsed
    project_dir = roots.resolve_project_dir(DEV_ROOT, project)
    if not project_dir.is_dir():
        # Missing project is rule 4's failure, not rule 3's.
        return []
    if not any((project_dir / name).exists() for name in TEST_CONFIG_FILES):
        return [
            f"target project {project!r} has no detectable test config "
            f"(one of {', '.join(TEST_CONFIG_FILES)})"
        ]
    return []


def _check_invocation_line(text: str) -> List[str]:  # rule 4
    parsed = _parse_invocation(text)
    if parsed is None:
        return ["file must end with a valid '/sprint <project> <spec-path>' line"]
    project, spec_path = parsed
    failures: List[str] = []
    if not roots.resolve_project_dir(DEV_ROOT, project).is_dir():
        failures.append(
            f"invocation names project {project!r} which is not a directory under {DEV_ROOT}"
        )
    if not pathlib.Path(spec_path).exists():
        failures.append(f"invocation spec-path does not exist: {spec_path}")
    return failures


def lint(spec_path) -> List[str]:
    """Return a list of structural failures for the spec at `spec_path`.

    Empty list means the spec passes every rule. Reads the spec file and, for
    rules 3 and 4, stats the target project directory. No model call, no
    network.
    """
    text = pathlib.Path(spec_path).read_text(encoding="utf-8")
    failures: List[str] = []
    failures += _check_required_sections(text)  # rule 1
    failures += _check_source_header(text)  # rule 2
    failures += _check_target_project_test_config(text)  # rule 3
    failures += _check_invocation_line(text)  # rule 4
    failures += _check_em_dash(text)  # rule 5
    return failures
