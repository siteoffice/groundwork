"""Import-robustness tests: flows/_lib modules must import model_map on their own.

flows/_lib and sprints/_lib are flat sibling directories with no __init__.py.
Inside a `pytest -q` run from the hub root, pyproject's
testpaths = ["sprints/_lib", "flows/_lib"] puts BOTH on sys.path, which hides
the problem. These tests therefore run a child interpreter with ONLY
flows/_lib on sys.path, cwd outside the repo, and PYTHONPATH cleared, so the
sys.path bootstrap inside each module is the only thing that can make
`import model_map` work (spec Problem 4 / success criterion 4).

Plain pytest, stdlib only, hermetic.
"""
import os
import pathlib
import subprocess
import sys

_FLOWS_LIB = str(pathlib.Path(__file__).resolve().parent)


def _run_isolated(module_name: str, probe: str, tmp_path):
    """Import `module_name` in a child interpreter with only flows/_lib on path."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{_FLOWS_LIB}')\n"
        f"import {module_name} as m\n"
        f"print({probe})\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )


def test_flow_transition_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("flow_transition", "m.mm.DEFAULT_TABLE['dev']", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "sonnet"


def test_flow_state_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("flow_state", "m.mm.CLASS_TO_MODEL['opus']", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "claude-opus-5"


def test_foreman_status_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("foreman_status", "m.GATE_ORDER", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "('G1', 'G2', 'G3', 'G4')"


def test_foreman_run_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("foreman_run", "m.DRIVER_SCHEMA_VERSION", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1"


def test_foreman_cost_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("foreman_cost", "len(m.CATEGORY_PRICE)", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "4"


def test_foreman_routing_imports_with_only_flows_lib_on_syspath(tmp_path):
    proc = _run_isolated("foreman_routing", "m.NONE_CLASS", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "none"
