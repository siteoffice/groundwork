"""Tests for gw.py and receipt.py: a fresh folder goes idea to close, and receipts appear only
when a change is accepted (G4 ship)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gw  # noqa: E402

FLOW = "demo/add-a-login-page"


def run(root, *args):
    return gw.main(["--root", str(root)] + list(args))


def to_g4(root):
    """Drive a fresh flow to an open G4 ship gate."""
    assert run(root, "new", "demo", "add a login page") == 0
    for _ in range(3):
        run(root, "advance", FLOW)  # idea -> design -> breakdown -> G1 opens
    run(root, "gate", FLOW, "approve")
    run(root, "advance", FLOW, "--steps", "build the page")  # execute, grill
    run(root, "advance", FLOW)  # G2 opens
    run(root, "gate", FLOW, "approve")
    run(root, "advance", FLOW, "--spec", str(root / "spec.md"))  # work step ready
    run(root, "advance", FLOW)  # G4 opens


def flow_dir(root):
    return pathlib.Path(root) / "flows" / FLOW


def load(root):
    return json.loads((flow_dir(root) / "flow.json").read_text(encoding="utf-8"))


def test_fresh_folder_goes_idea_to_close_with_a_receipt(tmp_path, capsys):
    to_g4(tmp_path)
    assert load(tmp_path)["gate"]["id"] == "G4"
    assert not (flow_dir(tmp_path) / "receipts").exists()

    run(tmp_path, "gate", FLOW, "ship", "looks right")
    receipts = flow_dir(tmp_path) / "receipts"
    data = json.loads((receipts / "step-01.json").read_text(encoding="utf-8"))
    page = (receipts / "step-01.html").read_text(encoding="utf-8")
    assert data["label"] == "self-issued, not signed"
    assert data["verdict"] == "ship" and data["gate"] == "G4"
    assert data["step_title"] == "build the page" and data["note"] == "looks right"
    assert data["flow_log_sha256"]
    assert "self-issued, not signed" in page and "build the page" in page

    run(tmp_path, "advance", FLOW)  # consume G4 -> learn
    run(tmp_path, "advance", FLOW)  # G3 opens
    run(tmp_path, "gate", FLOW, "proceed")
    run(tmp_path, "advance", FLOW)
    flow = load(tmp_path)
    assert flow["stage"] == "closed" and flow["status"] == "closed"


def test_hold_writes_no_receipt(tmp_path):
    to_g4(tmp_path)
    run(tmp_path, "gate", FLOW, "hold", "not yet")
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_park_and_rejected_gates_write_no_receipt(tmp_path):
    to_g4(tmp_path)
    run(tmp_path, "gate", FLOW, "park")
    assert not (flow_dir(tmp_path) / "receipts").exists()


def test_a_repeated_ship_does_not_write_a_second_receipt(tmp_path):
    to_g4(tmp_path)
    run(tmp_path, "gate", FLOW, "ship")
    first = (flow_dir(tmp_path) / "receipts" / "step-01.json").read_text(encoding="utf-8")
    run(tmp_path, "gate", FLOW, "ship", "again")
    again = (flow_dir(tmp_path) / "receipts" / "step-01.json").read_text(encoding="utf-8")
    assert first == again
    assert sorted(p.name for p in (flow_dir(tmp_path) / "receipts").iterdir()) == [
        "step-01.html", "step-01.json"]


def test_a_verdict_that_does_not_fit_the_gate_is_refused(tmp_path, capsys):
    to_g4(tmp_path)
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
