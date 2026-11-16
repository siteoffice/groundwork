"""Tests for gw.py and receipt.py: a fresh folder goes idea to close, and receipts appear only
when a change is accepted (ship at the accept gate)."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gw  # noqa: E402

FLOW = "demo/add-a-login-page"


def run(root, *args):
    return gw.main(["--root", str(root)] + list(args))


def to_accept(root, steps="build the page"):
    """Drive a fresh flow to an open accept gate."""
    assert run(root, "new", "demo", "add a login page") == 0
    run(root, "advance", FLOW)  # idea -> design
    run(root, "advance", FLOW)  # design -> breakdown
    run(root, "advance", FLOW, "--steps", steps)  # execute, grill (no gate)
    run(root, "advance", FLOW)  # spec opens
    run(root, "gate", FLOW, "approve")
    run(root, "advance", FLOW, "--spec", str(root / "spec.md"))  # work step ready
    run(root, "advance", FLOW)  # accept opens


def flow_dir(root):
    return pathlib.Path(root) / "flows" / FLOW


def load(root):
    return json.loads((flow_dir(root) / "flow.json").read_text(encoding="utf-8"))


def test_fresh_folder_goes_idea_to_close_with_a_receipt(tmp_path, capsys):
    to_accept(tmp_path)
    assert load(tmp_path)["gate"]["id"] == "accept"
    assert not (flow_dir(tmp_path) / "receipts").exists()

    run(tmp_path, "gate", FLOW, "ship", "looks right")
    receipts = flow_dir(tmp_path) / "receipts"
    data = json.loads((receipts / "step-01.json").read_text(encoding="utf-8"))
    page = (receipts / "step-01.html").read_text(encoding="utf-8")
    assert data["label"] == "self-issued, not signed"
    assert data["verdict"] == "ship" and data["gate"] == "accept"
    assert data["step_title"] == "build the page" and data["note"] == "looks right"
    assert data["flow_log_sha256"]
    assert "self-issued, not signed" in page and "build the page" in page

    run(tmp_path, "advance", FLOW)  # learn, no gate
    flow = load(tmp_path)
    assert flow["gate"] is None and flow["current_step"]["phase"] == "learn"
    run(tmp_path, "advance", FLOW)  # closed
    flow = load(tmp_path)
    assert flow["stage"] == "closed" and flow["status"] == "closed"


def test_receipt_on_accept(tmp_path):
    for name in ("ship", "hold", "park"):
        root = tmp_path / name
        to_accept(root)
        run(root, "gate", FLOW, name)
    receipts = flow_dir(tmp_path / "ship") / "receipts"
    assert (receipts / "step-01.json").exists() and (receipts / "step-01.html").exists()
    assert not (flow_dir(tmp_path / "hold") / "receipts").exists()
    assert not (flow_dir(tmp_path / "park") / "receipts").exists()


def test_receipt_on_old_g4(tmp_path):
    to_accept(tmp_path)
    fpath = flow_dir(tmp_path) / "flow.json"
    flow = json.loads(fpath.read_text(encoding="utf-8"))
    flow["gate"]["id"] = "G4"
    flow["gate"]["token"] = flow["gate"]["token"].replace("-accept-", "-G4-")
    fpath.write_text(json.dumps(flow, indent=2), encoding="utf-8")
    logpath = flow_dir(tmp_path) / "flow-log.ndjson"
    before = logpath.read_bytes()
    run(tmp_path, "gate", FLOW, "ship")
    receipts = flow_dir(tmp_path) / "receipts"
    assert (receipts / "step-01.json").exists() and (receipts / "step-01.html").exists()
    assert logpath.read_bytes().startswith(before)
    data = json.loads((receipts / "step-01.json").read_text(encoding="utf-8"))
    assert "-G4-" in data["gate_token"]


def test_receipt_page_names_the_gate(tmp_path):
    to_accept(tmp_path)
    run(tmp_path, "gate", FLOW, "ship")
    receipts = flow_dir(tmp_path) / "receipts"
    page = (receipts / "step-01.html").read_text(encoding="utf-8")
    assert "ship at the accept gate" in page and "G4" not in page
    data = json.loads((receipts / "step-01.json").read_text(encoding="utf-8"))
    assert set(data) == {
        "schema", "label", "flow_id", "project", "slug", "idea", "step_index",
        "step_title", "spec_path", "gate", "gate_token", "verdict", "note",
        "surface", "decided_at", "flow_log_lines", "flow_log_sha256",
    }


def test_full_loop_names(tmp_path, capsys):
    run(tmp_path, "new", "demo", "add a login page")
    run(tmp_path, "advance", FLOW)
    run(tmp_path, "advance", FLOW)
    run(tmp_path, "advance", FLOW, "--steps", "a;b")
    assert load(tmp_path)["gate"] is None
    for _ in range(2):
        run(tmp_path, "advance", FLOW)  # spec opens
        run(tmp_path, "gate", FLOW, "approve")
        run(tmp_path, "advance", FLOW, "--spec", str(tmp_path / "spec.md"))
        run(tmp_path, "advance", FLOW)  # accept opens
        run(tmp_path, "gate", FLOW, "ship")
        run(tmp_path, "advance", FLOW)  # learn, no gate
        flow = load(tmp_path)
        assert flow["gate"] is None and flow["current_step"]["phase"] == "learn"
        run(tmp_path, "advance", FLOW)  # next step grill, or closed
    assert set(re.findall(r"GATE (\S+)", capsys.readouterr().out)) == {"spec", "accept"}
    assert load(tmp_path)["stage"] == "closed"
    receipts = flow_dir(tmp_path) / "receipts"
    assert (receipts / "step-01.json").exists() and (receipts / "step-02.json").exists()


def test_hold_writes_no_receipt(tmp_path):
    to_accept(tmp_path)
    run(tmp_path, "gate", FLOW, "hold", "not yet")
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_park_and_rejected_gates_write_no_receipt(tmp_path):
    to_accept(tmp_path)
    run(tmp_path, "gate", FLOW, "park")
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_a_repeated_ship_does_not_write_a_second_receipt(tmp_path):
    to_accept(tmp_path)
    run(tmp_path, "gate", FLOW, "ship")
    first = (flow_dir(tmp_path) / "receipts" / "step-01.json").read_text(encoding="utf-8")
    run(tmp_path, "gate", FLOW, "ship", "again")
    again = (flow_dir(tmp_path) / "receipts" / "step-01.json").read_text(encoding="utf-8")
    assert first == again
    assert sorted(p.name for p in (flow_dir(tmp_path) / "receipts").iterdir()) == [
        "step-01.html", "step-01.json"]


def test_a_verdict_that_does_not_fit_the_gate_is_refused(tmp_path, capsys):
    to_accept(tmp_path)
    assert run(tmp_path, "gate", FLOW, "approve") == 1
    assert "refused" in capsys.readouterr().out
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_close_by_hand_and_status(tmp_path, capsys):
    assert run(tmp_path, "new", "demo", "add a login page") == 0
    assert run(tmp_path, "close", FLOW, "dropped") == 0
    assert load(tmp_path)["stage"] == "closed"
    run(tmp_path, "status")
    assert "demo/add-a-login-page" in capsys.readouterr().out


def test_close_needs_a_reason_and_is_safe_to_repeat(tmp_path, capsys):
    assert run(tmp_path, "new", "demo", "add a login page") == 0
    assert run(tmp_path, "close", FLOW, "  ") == 1
    assert load(tmp_path)["stage"] == "idea"
    assert run(tmp_path, "close", FLOW, "dropped") == 0
    assert run(tmp_path, "close", FLOW, "dropped") == 0
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_new_refuses_an_existing_flow(tmp_path, capsys):
    assert run(tmp_path, "new", "demo", "add a login page") == 0
    assert run(tmp_path, "new", "demo", "add a login page") == 1
    assert "already exists" in capsys.readouterr().out
