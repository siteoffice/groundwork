"""Token parsing: old G-coded tokens and new name tokens both read (D3)."""
import flow_state as fs
import outbox

OLD_G2 = "FLOW-BURNMON-burnmon-b2-section-groups-per-G2-S0-20261102T101500Z-7QF3"
OLD_G4 = "FLOW-DSI-payoff-books-G4-S1-20261102T101500Z-AB12"
STAMP = "20261102T101500Z"


def test_old_g2_token_parses():
    assert outbox.token_gate(OLD_G2) == "G2"
    assert outbox.token_step_index(OLD_G2) == 0


def test_old_g4_token_parses():
    assert outbox.token_gate(OLD_G4) == "G4"
    assert outbox.token_step_index(OLD_G4) == 1


def test_new_spec_and_accept_tokens_parse():
    spec = f"FLOW-P-some-slug-spec-S3-{STAMP}-Z9Z9"
    acc = f"FLOW-P-some-slug-accept-S4-{STAMP}-Z9Z9"
    assert (outbox.token_gate(spec), outbox.token_step_index(spec)) == ("spec", 3)
    assert (outbox.token_gate(acc), outbox.token_step_index(acc)) == ("accept", 4)


def test_slug_ending_in_a_gate_word_is_safe():
    for slug in ("fix-the-spec", "accept", "g2-cleanup"):
        tok = f"FLOW-P-{slug}-spec-S2-{STAMP}-ABCD"
        assert outbox.token_gate(tok) == "spec"
        assert outbox.token_step_index(tok) == 2


def test_g1_token_without_step_gives_none():
    tok = f"FLOW-DSI-payoff-books-G1-{STAMP}-7QF3"
    assert outbox.token_step_index(tok) is None
    assert outbox.token_gate(tok) is None


def test_garbage_gives_none():
    for bad in ("", None, "hello world", "FLOW-x-y"):
        assert outbox.token_gate(bad) is None
        assert outbox.token_step_index(bad) is None


def test_gate_token_writes_the_name_and_round_trips():
    tok = fs.gate_token("p", "fix-the-spec", "G2", 0,
                        "2026-11-02T10:15:00Z", "7QF3")
    assert tok == f"FLOW-P-fix-the-spec-spec-S0-{STAMP}-7QF3"
    assert outbox.token_gate(tok) == "spec"
    assert outbox.token_step_index(tok) == 0
