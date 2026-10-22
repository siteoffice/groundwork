"""roots.py - the engine's data-root resolver.

Foreman cloud package (T04): the private-hub-only name `hub_root` stays as a
back-compat alias (existing ZND callers, tests and env var keep working
unchanged), but the concept this module resolves is now an explicit,
product-neutral "data root": wherever THIS installation's flow/sprint/foreman
records live, which for a customer is their own repository, never the
ZeroNonsense.dev hub. `data_root()` is the name new callers should use.
Precedence, highest first:

  1. an explicit argument (the CLI flag the caller already parsed)
  2. the `ZND_DATA_ROOT` environment variable (the current, product name)
  3. the `ZND_HUB_ROOT` environment variable (back-compat alias, same effect)
  4. a `znd_hub.json` config file next to this resolver's derived root,
     with a top-level `"hub_root"` string key
  5. derived from `__file__`: this module lives at `<root>/flows/_lib/roots.py`

Mirrors the pattern already used correctly at
`04_engineering\\command_center\\assemble.py:83`, the only place in the tree
that read `ZND_HUB_ROOT` before this module existed.

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

ENV_VAR = "ZND_HUB_ROOT"
DATA_ROOT_ENV_VAR = "ZND_DATA_ROOT"
DEV_ROOT_ENV_VAR = "ZND_DEV_ROOT"
CONFIG_FILENAME = "znd_hub.json"

_DERIVED_ROOT = Path(__file__).resolve().parent.parent.parent


def data_root(explicit: Optional[str] = None) -> Path:
    """Resolve this installation's data root: explicit, ZND_DATA_ROOT,
    ZND_HUB_ROOT (back-compat), config file, derived."""
    if explicit:
        return Path(explicit).resolve()

    env = os.environ.get(DATA_ROOT_ENV_VAR, "").strip()
    if env:
        return Path(env).resolve()

    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return Path(env).resolve()

    config_path = _DERIVED_ROOT / CONFIG_FILENAME
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            configured = str(data.get("hub_root", "")).strip()
            if configured:
                return Path(configured).resolve()
        except (OSError, ValueError):
            pass

    return _DERIVED_ROOT


def hub_root(explicit: Optional[str] = None) -> Path:
    """Back-compat alias for data_root(), kept for existing ZND callers."""
    return data_root(explicit)


def flows_root(explicit: Optional[str] = None) -> Path:
    return hub_root(explicit) / "flows"


def sprints_root(explicit: Optional[str] = None) -> Path:
    return hub_root(explicit) / "sprints"


def foreman_board_path(explicit: Optional[str] = None) -> Path:
    return hub_root(explicit) / "foreman" / "index.html"


def status_doc_paths(dev_root_value: Optional[Path] = None) -> list:
    """Paths a closing flow brief should point the reader at next.

    Only the legacy ZND hub layout (`<dev_root>/10_holding/01_projects/portfolio.md`
    and `<dev_root>/10_holding/STATUS.md`) is a known convention; those two
    literal paths used to be hard-coded here regardless of installation. A
    packaged install (any customer) has no such files, so
    this returns them only when they actually exist on disk, and an empty
    list otherwise rather than inventing a customer-side path.
    """
    root = dev_root_value if dev_root_value is not None else dev_root()
    hub = Path(root) / "10_holding"
    portfolio = hub / "01_projects" / "portfolio.md"
    status = hub / "STATUS.md"
    found = [str(p) for p in (portfolio, status) if p.is_file()]
    return found


def dev_root(explicit: Optional[str] = None) -> Path:
    """The work-tree root that holds every project (including the hub) as a
    sibling directory. Precedence: explicit, `ZND_DEV_ROOT` env var, else the
    hub root's parent directory."""
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get(DEV_ROOT_ENV_VAR, "").strip()
    if env:
        return Path(env).resolve()
    return hub_root().parent


def resolve_project_dir(dev_root: Path, name: str) -> Path:
    """Resolve a named project's directory under a dev root.

    Most projects live at `<dev_root>/projects/<name>`; the hub itself
    (`10_holding`) and anything predating that convention live directly at
    `<dev_root>/<name>`. Prefer the nested form when it exists, so a stray
    flat-layout directory left over from before the convention (or from a
    caller that never adopted it) never shadows the real project.
    """
    nested = dev_root / "50_projects" / name
    if nested.is_dir():
        return nested
    return dev_root / name

