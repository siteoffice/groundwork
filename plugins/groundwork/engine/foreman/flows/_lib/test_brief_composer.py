r"""Tests for brief_composer.py. Plain pytest, stdlib only, hermetic.

Covers the five spec assertions (spec lines 71-79):
  1. eight template sections in order            -> Task 4
  2. measured -> [RESULT], unmeasured -> [STATE] -> Task 2 + Task 4
  3. zero em dashes (U+2014) in output           -> Task 1 (strip) + Task 4
  4. full C:\work\ paths + no secret leak         -> Task 1 (redact) + Task 4
  5. compose_brief is pure (same in/out, no IO)  -> Task 4
"""
import brief_composer as bc


# --- Task 1: sanitizers ----------------------------------------------------

def test_strip_em_dashes_replaces_u2014_with_hyphen():
    assert bc.strip_em_dashes("a \u2014 b") == "a - b"
    assert "\u2014" not in bc.strip_em_dashes("x\u2014y")


def test_find_secrets_flags_github_token():
    tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    assert tok in bc.find_secrets("here is a token " + tok + " ok")


def test_find_secrets_flags_oauth_consent_url_code_param():
    url = ("https://login.microsoftonline.com/common/oauth2/authorize"
           "?code=0.ABcdEF_secretvalue123&state=x")
    hits = bc.find_secrets(url)
    assert any("code=" in h for h in hits)


def test_find_secrets_ignores_sprint_id_short_sha_and_path():
    benign = ("sustainability-engine__regname-adapter-eu__20260714T181958Z "
              "commit 12c3622 in C:\\work\\50_projects\\dsi-engine\\adapters\\eprtr_eu.py")
    assert bc.find_secrets(benign) == []


def test_redact_secrets_removes_token_but_keeps_param_name():
    tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    out = bc.redact_secrets("token " + tok)
    assert tok not in out
    assert bc.REDACTION in out
    url = "https://x/callback?code=0.ABsecretXYZ123&state=ok"
    out2 = bc.redact_secrets(url)
    assert "0.ABsecretXYZ123" not in out2
    assert "code=" in out2      # param name preserved
    assert "state=ok" in out2   # non-secret param untouched


# --- Task 2: finding classification + section 4 ----------------------------

def test_tag_finding_measured_with_value_is_result():
    f = {"text": "coverage", "measured": True, "value": "95.1%"}
    assert bc.tag_finding(f) == "[RESULT]"


def test_tag_finding_unmeasured_is_state():
    assert bc.tag_finding({"text": "adapter wired, not run on Azure"}) == "[STATE]"
    assert bc.tag_finding({"text": "x", "measured": True, "value": None}) == "[STATE]"
    assert bc.tag_finding({"text": "x", "measured": True, "value": ""}) == "[STATE]"
    assert bc.tag_finding({"text": "x", "measured": False, "value": "9"}) == "[STATE]"


def test_render_findings_tags_and_preserves_text():
    findings = [
        {"text": "EU regname coverage", "measured": True, "value": "95.1%",
         "source": "research_1 line 100"},
        {"text": "not yet loaded to Azure", "measured": False},
    ]
    out = bc.render_findings(findings)
    assert out.startswith("## 4. Findings worth propagating")
    assert "[RESULT] EU regname coverage (95.1%; source: research_1 line 100)" in out
    assert "[STATE] not yet loaded to Azure" in out


def test_render_findings_empty_says_none():
    assert "None this cycle." in bc.render_findings([])


# --- Task 3: framing + section renderers -----------------------------------

def _meta():
    return {
        "project": "DSI",
        "date": "2026-07-18",
        "topic": "regname-adapter-eu",
        "headline": "EU regname adapter code-complete on a branch, not merged.",
        "purpose": "Tell the hub the EU adapter is built and awaiting merge.",
        "read_order": [
            r"C:\work\50_projects\dsi\04_assets\hub_agent_update_2026-07-18_regname.md"
        ],
        "supersedes": "nothing",
        "changed": {
            "commits": [{"repo": "sustainability-engine", "sha": "12c3622",
                         "message": "normalization helpers plus tests"}],
            "branch": "sprint/regname-adapter-eu (4 commits ahead of master)",
            "files": [r"C:\work\50_projects\dsi-engine\adapters\eprtr_eu.py"],
            "untracked": [],
        },
        "hub_decision": None,
        "next_updates": ["STATUS.md", "01_projects/portfolio.md"],
        "related": [
            r"C:\work\50_projects\dsi-engine\05_docs\superpowers\specs\2026-07-14-regname-adapter-eu-design.md"
        ],
    }


def test_render_header_carries_project_and_read_order():
    out = bc.render_header(_meta())
    assert out.startswith("# Hub Agent Update - ZeroNonsense.dev")
    assert "**Project:** DSI" in out
    assert "hub_agent_update_2026-07-18_regname.md" in out


def test_render_headline_is_section_one():
    out = bc.render_headline(_meta())
    assert out.startswith("## 1. Headline")
    assert "code-complete on a branch, not merged" in out


def test_render_changed_lists_commits_and_full_paths():
    out = bc.render_changed(_meta()["changed"])
    assert out.startswith("## 2. What changed on disk")
    assert "`12c3622`" in out
    assert "sprint/regname-adapter-eu (4 commits ahead of master)" in out
    assert r"C:\work\50_projects\dsi-engine\adapters\eprtr_eu.py" in out


def test_render_changed_empty_says_nothing_changed():
    assert "Nothing changed on disk this cycle." in bc.render_changed({})


def test_render_not_done_appends_secret_note_when_handled():
    out = bc.render_not_done(["not merged", "not loaded to Azure"], True)
    assert out.startswith("## 3. What did NOT happen (and why)")
    assert "- not merged" in out
    assert "secret-shaped token" in out


def test_render_not_done_no_secret_note_when_clean():
    out = bc.render_not_done(["not merged"], False)
    assert "secret-shaped token" not in out


def test_render_hub_decision_none_says_none():
    out = bc.render_hub_decision(None)
    assert out.startswith("## 5. Hub-level decision (if any)")
    assert "None." in out


def test_render_hub_decision_block_when_present():
    d = {"decision": "Park GitHub Actions", "context": "no traction",
         "alternatives": "keep it warm", "consequences": "frees 2h/wk",
         "links": r"C:\work\10_holding\03_logs\decisions.md"}
    out = bc.render_hub_decision(d)
    assert "**Decision:** Park GitHub Actions" in out
    assert "**Consequences:** frees 2h/wk" in out


def test_render_next_updates_open_flags_related_headers():
    assert bc.render_next_updates(["STATUS.md"]).startswith(
        "## 6. What the next hub read should update")
    assert "- STATUS.md" in bc.render_next_updates(["STATUS.md"])
    assert bc.render_open_flags([]).startswith("## 7. Open flags for next session")
    assert "None." in bc.render_open_flags([])
    out = bc.render_related([r"C:\work\x\spec.md"])
    assert out.startswith("## 8. Related files")
    assert r"C:\work\x\spec.md" in out


# --- Task 4: compose_brief (the five spec assertions) ----------------------

SECTION_HEADERS = [
    "## 1. Headline",
    "## 2. What changed on disk",
    "## 3. What did NOT happen (and why)",
    "## 4. Findings worth propagating",
    "## 5. Hub-level decision (if any)",
    "## 6. What the next hub read should update",
    "## 7. Open flags for next session",
    "## 8. Related files",
]


def _learnings():
    return {
        "findings": [
            {"text": "EU regname coverage", "measured": True, "value": "95.1%",
             "source": "research_1 line 100"},
            {"text": "adapter not yet run on Azure", "measured": False},
        ],
        "not_done": ["branch not merged", "not loaded to Azure",
                     "no live numbers yet"],
        "open_flags": ["merge decision pending Wilco"],
    }


def test_compose_brief_has_all_eight_sections_in_order():
    out = bc.compose_brief("report text", "log text", _learnings(), _meta())
    positions = [out.find(h) for h in SECTION_HEADERS]
    assert all(p >= 0 for p in positions)   # every section present
    assert positions == sorted(positions)   # and in template order


def test_compose_brief_tags_result_and_state_from_fixtures():
    out = bc.compose_brief("report", "log", _learnings(), _meta())
    assert "[RESULT] EU regname coverage (95.1%" in out
    assert "[STATE] adapter not yet run on Azure" in out


def test_compose_brief_output_has_no_em_dash():
    meta = _meta()
    meta["headline"] = "EU adapter \u2014 built \u2014 not merged."
    out = bc.compose_brief("a \u2014 b", "c \u2014 d", _learnings(), meta)
    assert "\u2014" not in out


def test_compose_brief_uses_full_paths_and_leaks_no_secret():
    tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    # secret in the RAW sprint_report (spec: raw input may carry a secret)
    report = "Sprint done. Pasted token " + tok + " during setup."
    meta = _meta()
    meta["headline"] = "done; token " + tok  # secret also in a rendered field
    out = bc.compose_brief(report, "action-log ok", _learnings(), meta)
    # full path preserved verbatim
    assert r"C:\work\50_projects\dsi-engine\adapters\eprtr_eu.py" in out
    # no secret leaks anywhere in the output
    assert tok not in out
    # and section 3 records that a secret was handled
    assert "secret-shaped token" in out


def test_compose_brief_is_pure_and_does_no_io(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ("report", "log", _learnings(), _meta())
    first = bc.compose_brief(*args)
    second = bc.compose_brief(*args)
    assert first == second                   # deterministic
    assert list(tmp_path.iterdir()) == []    # no file written
