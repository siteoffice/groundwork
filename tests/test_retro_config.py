import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "retro"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))
EXP = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

import retro_config as rc  # noqa: E402
import retro_report as rr  # noqa: E402
from retro_fixture_util import materialize_fixtures  # noqa: E402


def _init(tmp_path, *extra, root=None):
    root = root or tmp_path / "root"
    return rc.main(["init", "--root", str(root), "--home", str(tmp_path / "home")] + list(extra))


def _w(tmp_path, folder, out):
    return "%s=%s" % (tmp_path / folder, tmp_path / out)


def test_find_groundwork_root(tmp_path):
    (tmp_path / "a" / "flows").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    assert rc.find_groundwork_root(tmp_path / "a" / "b" / "c") == tmp_path / "a"
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "siteoffice.json").write_text("{}", encoding="ascii")
    assert rc.find_groundwork_root(tmp_path / "s") == tmp_path / "s"


def test_resolve_root_default_then_pointer(tmp_path):
    home, start = tmp_path / "home", tmp_path / "elsewhere"
    start.mkdir()
    assert rc.resolve_root(start, home) == (home / "Documents", "default")
    rc.write_pointer(home, tmp_path / "chosen")
    assert rc.resolve_root(start, home) == (tmp_path / "chosen", "pointer")


def test_resolve_root_prefers_groundwork_and_ignores_bad_pointer(tmp_path):
    home = tmp_path / "home"
    (tmp_path / "gw" / "flows").mkdir(parents=True)
    (tmp_path / "gw" / "x").mkdir()
    rc.write_pointer(home, tmp_path / "chosen")
    assert rc.resolve_root(tmp_path / "gw" / "x", home) == (tmp_path / "gw", "groundwork")
    (home / ".groundwork" / "retro.json").write_text("not json", encoding="utf-8")
    (tmp_path / "e").mkdir()
    assert rc.resolve_root(tmp_path / "e", home) == (home / "Documents", "default")


def test_init_writes_config(tmp_path):
    root = tmp_path / "root"
    assert _init(tmp_path, "--period", "14", "--ceiling", "500000",
                 "--world", _w(tmp_path, "w", "w-out"), "--gate-pattern", "LGTM-G?") == 0
    cfg = rc.load_config(root)
    assert (cfg["period_days"], cfg["token_ceiling"], cfg["gate_patterns"]) == (14, 500000, ["lgtm-g?"])
    assert cfg["worlds"] == [{"folder": str(tmp_path / "w"), "report_dir": str(tmp_path / "w-out")}]
    assert json.loads((tmp_path / "home" / ".groundwork" / "retro.json").read_text(encoding="utf-8")) == {"root": str(root)}


def test_pointer_not_written_inside_groundwork_root(tmp_path):
    root = tmp_path / "gw"
    (root / "flows").mkdir(parents=True)
    assert _init(tmp_path, root=root) == 0
    assert not (tmp_path / "home").exists()


def test_load_missing_returns_defaults(tmp_path):
    assert rc.load_config(tmp_path) == rc.DEFAULTS
    assert rc.load_config(tmp_path) is not rc.DEFAULTS


def test_load_invalid_json_raises(tmp_path):
    (tmp_path / "retro").mkdir()
    (tmp_path / "retro" / "config.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(rc.ConfigError):
        rc.load_config(tmp_path)


def test_init_keeps_other_keys_and_unset_values(tmp_path):
    root = tmp_path / "root"
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text(json.dumps({"usd_to_eur": 0.9, "period_days": 7}), encoding="utf-8")
    assert _init(tmp_path, "--ceiling", "100") == 0
    cfg = rc.load_config(root)
    assert (cfg["usd_to_eur"], cfg["period_days"], cfg["token_ceiling"]) == (0.9, 7, 100)


def test_invalid_gate_pattern_is_refused(tmp_path, capsys):
    assert _init(tmp_path, "--gate-pattern", "x" * 81, root=tmp_path) == 2
    assert not (tmp_path / "retro" / "config.json").exists()
    assert "gate pattern" in capsys.readouterr().out
    assert _init(tmp_path, "--gate-pattern", "", root=tmp_path) == 2


def test_gate_pattern_is_a_glob_not_a_regex(tmp_path):
    assert _init(tmp_path, "--gate-pattern", "(") == 0
    assert rc.load_config(tmp_path / "root")["gate_patterns"] == ["("]


def test_world_report_inside_retro_folder_is_refused(tmp_path):
    bad = "%s=%s" % (tmp_path / "w", tmp_path / "retro" / "w")
    assert _init(tmp_path, "--world", bad, root=tmp_path) == 2


@pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5", "nan"])
def test_bad_ceiling_is_refused_by_cli(tmp_path, value):
    assert _init(tmp_path, "--ceiling=" + value) == 2
    assert not (tmp_path / "root" / "retro" / "config.json").exists()


@pytest.mark.parametrize("value", [0, -1, True, False, float("nan"), "5", 2.5])
def test_bad_ceiling_is_refused_by_validate(tmp_path, value):
    cfg = dict(rc.DEFAULTS, token_ceiling=value)
    assert any("token_ceiling" in m for m in rc.validate_config(cfg, tmp_path))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "30", 3651])
def test_bad_period_is_refused(tmp_path, value):
    cfg = dict(rc.DEFAULTS, period_days=value)
    assert any("period_days" in m for m in rc.validate_config(cfg, tmp_path))


def _bad_world(tmp_path, *worlds):
    cfg = dict(rc.DEFAULTS, worlds=[{"folder": f, "report_dir": r} for f, r in worlds])
    return rc.validate_config(cfg, tmp_path / "root")


def test_world_paths_must_be_absolute(tmp_path):
    assert _bad_world(tmp_path, ("rel/w", str(tmp_path / "o")))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), "rel/o"))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), ""))
    assert _bad_world(tmp_path, ("", str(tmp_path / "o")))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), 5))


def test_world_folder_cannot_be_a_drive_root(tmp_path):
    assert _bad_world(tmp_path, (tmp_path.anchor, str(tmp_path / "o")))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), tmp_path.anchor))


def test_world_dot_env_component_is_refused(tmp_path):
    assert _bad_world(tmp_path, (str(tmp_path / ".envs" / "w"), str(tmp_path / "o")))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), str(tmp_path / ".ENV" / "o")))


def test_world_report_dirs_must_not_collide(tmp_path):
    assert _bad_world(tmp_path, (str(tmp_path / "a"), str(tmp_path / "o")), (str(tmp_path / "b"), str(tmp_path / "O")))
    assert _bad_world(tmp_path, (str(tmp_path / "a"), str(tmp_path / "b" / "o")), (str(tmp_path / "b"), str(tmp_path / "o")))
    assert not _bad_world(tmp_path, (str(tmp_path / "a"), str(tmp_path / "oa")), (str(tmp_path / "b"), str(tmp_path / "ob")))


def test_stored_world_paths_are_normalised_absolute(tmp_path):
    assert _init(tmp_path, "--world", "%s=%s" % (tmp_path / "a" / ".." / "w", tmp_path / "o")) == 0
    assert rc.load_config(tmp_path / "root")["worlds"][0]["folder"] == str(tmp_path / "w")


def test_relative_world_flag_is_refused(tmp_path):
    assert _init(tmp_path, "--world", "w=o") == 2


def test_init_does_not_prompt_without_a_tty(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPTS / "retro_config.py"), "init", "--root", str(tmp_path / "r"),
                        "--home", str(tmp_path / "home")], stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=60)
    assert p.returncode == 0
    assert rc.load_config(tmp_path / "r")["period_days"] == 30


def test_show_prints_a_summary_not_the_config(tmp_path, capsys):
    _init(tmp_path, "--world", _w(tmp_path, "w", "o"), "--gate-pattern", "ship it")
    capsys.readouterr()
    assert rc.main(["show", "--root", str(tmp_path / "root")]) == 0
    out = capsys.readouterr().out
    assert "period_days" in out and "ship it" not in out


def test_where_prints_root_and_how(tmp_path, capsys):
    (tmp_path / "e").mkdir()
    assert rc.main(["where", "--start", str(tmp_path / "e"), "--home", str(tmp_path / "home")]) == 0
    assert capsys.readouterr().out.strip() == "%s\tdefault" % (tmp_path / "home" / "Documents")


def test_report_main_uses_config_worlds(tmp_path):
    root, fx = tmp_path / "root", materialize_fixtures(tmp_path / "fx")
    _init(tmp_path, "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "w-out"))
    assert rr.main(["--root", str(root), "--fixtures", str(fx), "--now", "2026-10-15"]) == 0
    assert (tmp_path / "w-out" / "2026-10" / "report.md").is_file()
    assert EXP["world_marker"] not in (root / "retro" / "2026-10" / "report.md").read_text(encoding="utf-8")


def test_report_cli_world_wins_over_config(tmp_path):
    root, fx = tmp_path / "root", materialize_fixtures(tmp_path / "fx")
    _init(tmp_path, "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "cfg-out"))
    assert rr.main(["--root", str(root), "--fixtures", str(fx), "--now", "2026-10-15",
                    "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "cli-out")]) == 0
    assert (tmp_path / "cli-out" / "2026-10" / "report.md").is_file()
    assert not (tmp_path / "cfg-out").exists()


def test_report_main_bad_config_exits_2_on_stderr(tmp_path, capsys):
    root = tmp_path / "root"
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text("{nope", encoding="utf-8")
    assert rr.main(["--root", str(root), "--fixtures", str(tmp_path)]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and len(cap.err.strip().splitlines()) == 1
    (root / "retro" / "config.json").write_text(json.dumps({"period_days": 0}), encoding="utf-8")
    assert rr.main(["--root", str(root), "--fixtures", str(tmp_path)]) == 2
    assert "period_days" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [0, -1, True, "x", float("nan")])
def test_bad_ceiling_warns_on_stderr_and_scan_continues(tmp_path, capsys, bad):
    root = tmp_path / "root"
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text(json.dumps({"token_ceiling": bad}), encoding="utf-8")
    assert rr.main(["--root", str(root), "--fixtures", str(tmp_path), "--now", "2026-10-15"]) == 0
    err = capsys.readouterr().err
    assert err.startswith("token ceiling in config.json is invalid, ignoring it: ") and len(err.strip().splitlines()) == 1


def test_report_main_refuses_bad_root(tmp_path, capsys):
    assert rr.main(["--root", str(tmp_path / ".envs" / "r"), "--fixtures", str(tmp_path)]) == 2
    assert ".env" in capsys.readouterr().err


# --- review fixes: normalise first, then check -------------------------------------------

def _folder_problems(tmp_path, folder):
    return _bad_world(tmp_path, (folder, str(tmp_path / "o")))


def _report_problems(tmp_path, out):
    return _bad_world(tmp_path, (str(tmp_path / "w"), out))


@pytest.mark.parametrize("suffix", ["x/..", "a/../.."])
def test_dotdot_to_a_drive_root_is_refused_for_both_paths(tmp_path, suffix):
    bad = tmp_path.anchor + suffix
    assert _folder_problems(tmp_path, bad)
    assert _report_problems(tmp_path, bad)
    assert _init(tmp_path, "--world", "%s=%s" % (bad, tmp_path / "o")) == 2
    assert not (tmp_path / "root" / "retro" / "config.json").exists()


def test_dotdot_cannot_hide_or_fake_a_dot_env_component(tmp_path):
    assert not _folder_problems(tmp_path, str(tmp_path / ".env" / ".." / "ok"))
    assert not _report_problems(tmp_path, str(tmp_path / ".env" / ".." / "ok"))
    assert _folder_problems(tmp_path, str(tmp_path / "ok" / ".." / ".env" / "z"))
    assert _report_problems(tmp_path, str(tmp_path / "ok" / ".." / ".env" / "z"))


def test_posix_folder_accepted_after_normalising(tmp_path):
    cfg = dict(rc.DEFAULTS, worlds=[{"folder": "/home/demo/a/../x", "report_dir": str(tmp_path / "o")}])
    assert rc.validate_config(cfg, tmp_path / "root") == []
    assert _init(tmp_path, "--world", "/home/demo/a/../x=%s" % (tmp_path / "o")) == 0
    assert rc.load_config(tmp_path / "root")["worlds"][0]["folder"] == "/home/demo/x"


def test_posix_folder_root_is_refused(tmp_path):
    assert _folder_problems(tmp_path, "/")
    assert _folder_problems(tmp_path, "/..")
    assert _folder_problems(tmp_path, "/home/..")


@pytest.mark.skipif(sys.platform != "win32", reason="drive-style POSIX folders only matter on Windows")
@pytest.mark.parametrize("folder", ["/c/users/x", "/x/..", "/c", "/C/"])
def test_posix_drive_style_folder_is_refused_on_windows(tmp_path, folder):
    assert _folder_problems(tmp_path, folder)


@pytest.mark.skipif(sys.platform != "win32", reason="drive-style POSIX folders only matter on Windows")
def test_drive_style_message_says_to_use_the_windows_form(tmp_path):
    assert any("Windows" in m for m in _folder_problems(tmp_path, "/c/users/x"))


@pytest.mark.skipif(sys.platform != "win32", reason="a POSIX-rooted path is only relative on Windows")
def test_report_dir_stays_strictly_absolute(tmp_path):
    assert _report_problems(tmp_path, "/home/demo/out")


# --- re-run messages ----------------------------------------------------------------------

def test_init_reports_created_then_updated_and_replaced_lists(tmp_path, capsys):
    path = tmp_path / "root" / "retro" / "config.json"
    assert _init(tmp_path, "--world", _w(tmp_path, "a", "oa"), "--world", _w(tmp_path, "b", "ob"),
                 "--gate-pattern", "ok") == 0
    out = capsys.readouterr().out
    assert "created %s" % path in out and "replaced" not in out
    assert _init(tmp_path, "--world", _w(tmp_path, "c", "oc"), "--gate-pattern", "x", "--gate-pattern", "y") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "updated %s" % path in lines
    assert "replaced worlds: 2 earlier entries replaced by 1" in lines
    assert "replaced gate_patterns: 1 earlier entries replaced by 2" in lines
    assert _init(tmp_path, "--period", "9") == 0
    assert "replaced" not in capsys.readouterr().out


# --- validate_config extras ---------------------------------------------------------------

def test_report_dir_inside_own_folder_is_refused(tmp_path):
    assert _bad_world(tmp_path, (str(tmp_path / "w"), str(tmp_path / "w" / "out")))
    assert _bad_world(tmp_path, (str(tmp_path / "w"), str(tmp_path / "w")))


def test_nested_world_folders_are_refused(tmp_path):
    assert _bad_world(tmp_path, (str(tmp_path / "a"), str(tmp_path / "oa")), (str(tmp_path / "a" / "b"), str(tmp_path / "ob")))
    assert _bad_world(tmp_path, (str(tmp_path / "a" / "b"), str(tmp_path / "oa")), (str(tmp_path / "a"), str(tmp_path / "ob")))
    assert _bad_world(tmp_path, (str(tmp_path / "a"), str(tmp_path / "oa")), (str(tmp_path / "A"), str(tmp_path / "ob")))


# --- ROOT validation ----------------------------------------------------------------------

def test_init_refuses_bad_roots(tmp_path, capsys):
    assert _init(tmp_path, root=tmp_path / ".envs" / "r") == 2
    assert _init(tmp_path, root=tmp_path.anchor) == 2
    assert _init(tmp_path, root=tmp_path.anchor + "x/..") == 2
    assert not (tmp_path / "home").exists()
    assert ".env" in capsys.readouterr().out


def test_bad_pointer_shapes_fall_back_to_default(tmp_path):
    home, start = tmp_path / "home", tmp_path / "e"
    start.mkdir()
    (home / ".groundwork").mkdir(parents=True)
    ptr = home / ".groundwork" / "retro.json"
    for body in ('["x"]', '{"root": 5}', '{"root": ""}', '{"root": "relative/dir"}',
                 json.dumps({"root": str(tmp_path / ".envs" / "r")}), json.dumps({"root": tmp_path.anchor})):
        ptr.write_text(body, encoding="utf-8")
        assert rc.resolve_root(start, home) == (home / "Documents", "default"), body


def test_groundwork_root_with_dot_env_component_is_skipped(tmp_path):
    root = tmp_path / ".envs" / "gw"
    (root / "flows").mkdir(parents=True)
    (root / "x").mkdir()
    assert rc.resolve_root(root / "x", tmp_path / "home") == (tmp_path / "home" / "Documents", "default")


# --- prompts ------------------------------------------------------------------------------

class _Tty:
    def isatty(self):
        return True


def test_init_prompts_only_on_a_tty(tmp_path, monkeypatch):
    answers = iter([str(tmp_path / "asked"), "14", "777"])
    asked = []
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or next(answers))
    assert rc.main(["init", "--home", str(tmp_path / "home")]) == 0
    assert len(asked) == 3
    cfg = rc.load_config(tmp_path / "asked")
    assert (cfg["period_days"], cfg["token_ceiling"]) == (14, 777)


def test_init_prompt_blank_answers_take_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert rc.main(["init", "--root", str(tmp_path / "r"), "--home", str(tmp_path / "home")]) == 0
    cfg = rc.load_config(tmp_path / "r")
    assert (cfg["period_days"], cfg["token_ceiling"]) == (30, None)


def test_show_refuses_a_bad_root(tmp_path, capsys):
    assert rc.main(["show", "--root", str(tmp_path / ".envs" / "r")]) == 2
    assert ".env" in capsys.readouterr().out
    assert rc.main(["show", "--root", tmp_path.anchor]) == 2


# --- CLI worlds and patterns go through the same checks as config -------------------------

def _report(tmp_path, *extra):
    fx = materialize_fixtures(tmp_path / "fx")
    return rr.main(["--root", str(tmp_path / "root"), "--fixtures", str(fx), "--now", "2026-10-15"] + list(extra))


@pytest.mark.parametrize("folder", ["ANCHORx/..", "ANCHOR", "ANCHORa/../..", "TMP/.envs/w"])
def test_report_cli_world_goes_through_the_same_guards(tmp_path, capsys, folder):
    folder = folder.replace("ANCHOR", tmp_path.anchor.replace("\\", "/")).replace("TMP", str(tmp_path))
    assert _report(tmp_path, "--world", "%s=%s" % (folder, tmp_path / "out")) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err.startswith("retro config error:")
    assert not (tmp_path / "root").exists() and not (tmp_path / "out").exists()

def test_report_cli_gate_pattern_is_vetted(tmp_path, capsys):
    assert _report(tmp_path, "--gate-pattern", "x" * 81) == 2
    assert "gate pattern" in capsys.readouterr().err
    assert not (tmp_path / "root").exists()


def test_report_cli_world_nested_with_config_world_is_refused(tmp_path, capsys):
    _init(tmp_path, "--world", _w(tmp_path, "a", "oa"))
    assert _report(tmp_path, "--world", _w(tmp_path, "a/b", "ob")) == 2
    assert "nested" in capsys.readouterr().err
    assert not (tmp_path / "ob").exists()


def test_report_cli_world_shares_report_dir_with_config_world_is_refused(tmp_path):
    _init(tmp_path, "--world", _w(tmp_path, "a", "oa"))
    assert _report(tmp_path, "--world", _w(tmp_path, "b", "oa")) == 2


def test_report_cli_world_wins_for_same_folder_and_other_config_worlds_stay(tmp_path):
    _init(tmp_path, "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "cfg-out"),
          "--world", _w(tmp_path, "other", "other-out"))
    assert _report(tmp_path, "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "cli-out")) == 0
    assert (tmp_path / "cli-out" / "2026-10" / "report.md").is_file()
    assert not (tmp_path / "cfg-out").exists()


def test_report_valid_cli_world_still_routes(tmp_path):
    assert _report(tmp_path, "--world", "%s=%s" % (EXP["world_folder"], tmp_path / "w-out")) == 0
    assert (tmp_path / "w-out" / "2026-10" / "report.md").is_file()

def test_report_config_worlds_not_a_list_still_exits_2(tmp_path, capsys):
    (tmp_path / "root" / "retro").mkdir(parents=True)
    (tmp_path / "root" / "retro" / "config.json").write_text(json.dumps({"worlds": "x"}), encoding="utf-8")
    assert _report(tmp_path) == 2
    assert "worlds must be a list" in capsys.readouterr().err