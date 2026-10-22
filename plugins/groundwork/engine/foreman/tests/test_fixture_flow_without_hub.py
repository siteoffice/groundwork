"""T04 acceptance check 1: a clean environment executes a fixture flow
without the private ZND hub installed.

Same isolation technique flows/_lib/test_lib_imports.py already uses: a
child interpreter with ONLY foreman\\flows\\_lib on sys.path, cwd outside any
repository, PYTHONPATH cleared and no ZND_HUB_ROOT/ZND_DATA_ROOT pointing at
the real hub. The child creates a flow, opens a G2 gate and approves it,
using nothing but this package plus stdlib. Proves the engine runs standalone
from a customer-style checkout, not merely that it imports (test_lib_imports.py
already proved the import).

Plain pytest, stdlib only, hermetic.
"""
import json
import os
import pathlib
import subprocess
import sys

_FLOWS_LIB = str(pathlib.Path(__file__).resolve().parent.parent / "flows" / "_lib")

_CHILD_SCRIPT = r"""
import sys
sys.path.insert(0, r"{flows_lib}")
import flow_transition as ft

flows_root = r"{flows_root}"
flow_dir, flow = ft.cmd_new(flows_root, "acme", "pilot the fixture flow",
                            now="2026-09-16T09:00:00Z", slug="pilot")
flow["stage"] = "execute"
flow["current_step"] = {{"index": 2, "of": 4, "phase": "sprint"}}
flow["steps"] = [{{"index": 2, "title": "build it", "status": "running"}}]
ft.write_flow(flow_dir, flow)

flow = ft.load_flow(flow_dir)
ft.persist_open_gate(flow_dir, flow, "G2", 2,
                      now="2026-09-16T09:05:00Z", nonce="AB12")
ft.cmd_gate(flow_dir, "approve", surface="session",
            now="2026-09-16T09:06:00Z")

result = ft.load_flow(flow_dir)
assert result["gate"]["status"] == "decided"
assert result["gate"]["decision"]["verdict"] == "approve"
print("FIXTURE_FLOW_OK")
"""


def test_fixture_flow_runs_standalone_without_the_private_hub(tmp_path):
    flows_root = tmp_path / "flows"
    flows_root.mkdir()
    script = _CHILD_SCRIPT.format(flows_lib=_FLOWS_LIB, flows_root=str(flows_root))

    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env.pop("ZND_HUB_ROOT", None)
    env.pop("ZND_DATA_ROOT", None)
    env.pop("ZND_DEV_ROOT", None)

    outside_any_repo = tmp_path / "cwd"
    outside_any_repo.mkdir()

    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(outside_any_repo), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "FIXTURE_FLOW_OK" in proc.stdout

    written = json.loads(
        (flows_root / "acme" / "pilot" / "flow.json").read_text(encoding="utf-8"))
    assert written["gate"]["status"] == "decided"
    assert written["gate"]["decision"]["verdict"] == "approve"
