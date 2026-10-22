"""Generic table renderer. Not a per-node panel: the flow and sprint board
kinds (guide step 9) need plain tabular layouts (index rows, a flow's step
ladder) that no node board template needs. Building it as its own module
rather than folding it into a node panel is deliberate: named here, not
hidden, since a board-kind-only template file is the H1 kill signal named
in the plan (section 1) and checked at the step 10 fold checkpoint.
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BOARD_DIR = HERE.parent
if str(BOARD_DIR) not in sys.path:
    sys.path.insert(0, str(BOARD_DIR))
from board_common import esc  # noqa: E402


def render_table(headers, rows, empty_text: str = "") -> str:
    """rows: iterable of iterables of cell HTML (callers esc() plain text
    themselves; a cell may also be a small pre-built HTML fragment, e.g. a
    status badge span)."""
    if not rows:
        if empty_text:
            return f'<p class="empty"><span class="msg">{esc(empty_text)}</span></p>'
        return ""
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<table class="board-table"><thead><tr>{thead}</tr></thead><tbody>{body_rows}</tbody></table>'


def render_badge(text: str, sem: str) -> str:
    """sem: one of theme.STATUS_SEM's buckets (run, wait, done, halt)."""
    return f'<span class="badge {esc(sem)}">{esc(text)}</span>'
