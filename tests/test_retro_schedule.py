import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "plugins" / "groundwork" / "skills" / "retro" / "scripts"

PS1 = SCRIPTS / "register_retro_task.ps1"
WRAP = SCRIPTS / "run_retro_task.ps1"
PLIST = SCRIPTS / "retro.launchd.plist.example"
CRON = SCRIPTS / "retro.cron.example"
PWSH = shutil.which("pwsh")
SCHEDULE_FILES = [PS1, WRAP, PLIST, CRON]
EM = chr(0x2014)
FORBIDDEN = ["wmi", "cim", "win32_process", "psexec", "wmic", "invoke-cimmethod", "claude -p", "claude"]


def _text(p):
    return p.read_bytes().decode("ascii")


def test_every_script_file_is_pure_ascii():
    files = [p for p in SCRIPTS.iterdir() if p.is_file()]
    assert len(files) >= 9
    for p in files:
        assert all(b < 128 for b in p.read_bytes()), p.name


def test_no_forbidden_terms_in_schedule_files():
    for p in SCHEDULE_FILES:
        low = _text(p).lower()
        assert EM not in low
        for term in FORBIDDEN:
            assert term not in low, (p.name, term)
        assert "s4u" not in low and "/ru system" not in low, p.name
        # core.autocrlf may turn the committed LF into CRLF in a working copy: only a lone CR is a defect.
        assert "\r" not in _text(p).replace("\r\n", "\n"), p.name


def test_register_script_uses_hidden_shim_and_interactive_logon():
    text = _text(PS1)
    for needle in ("wscript.exe", "/IT", "WScript.Quit", ", 0, True", "Last Result", "-Register"):
        assert needle in text, needle
    assert "Register-ScheduledTask" not in text


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
@pytest.mark.parametrize("path", [PS1, WRAP], ids=lambda p: p.name)
def test_ps1_parses_with_zero_errors(path):
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
           "'%s',[ref]$t,[ref]$e);$e.Count" % path)
    r = subprocess.run([PWSH, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120)
    assert r.stdout.strip() == "0", r.stdout + r.stderr


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
def test_default_run_prints_plan_and_does_not_register(tmp_path):
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(PS1), "-Root", str(tmp_path)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "Not registered" in out
    assert "MONTHLY" in out and "wscript.exe" in out and "/IT" in out
    assert "exit code" in out.lower() and "Last Result" in out
    vbs = (tmp_path / "retro" / "run_retro_hidden.vbs").read_text(encoding="ascii")
    assert "WScript.Quit" in vbs and ", 0, True" in vbs and "run_retro_task.ps1" in vbs


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
def test_wrapper_propagates_python_exit_code_and_logs(tmp_path):
    root = tmp_path / "root"
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text("{}", encoding="utf-8")
    fake = tmp_path / "fake_report.py"
    fake.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(WRAP), "-Root", str(root),
                        "-Python", sys.executable, "-ReportScript", str(fake)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 3, r.stdout + r.stderr
    log = (root / "retro" / "scheduled_runs.log").read_text(encoding="ascii")
    assert re.search(r"FAILED exit 3", log)
    fake.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(WRAP), "-Root", str(root),
                        "-Python", sys.executable, "-ReportScript", str(fake)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK exit 0" in (root / "retro" / "scheduled_runs.log").read_text(encoding="ascii")


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
def test_wrapper_fails_with_one_line_when_config_missing(tmp_path):
    fake = tmp_path / "fake_report.py"
    fake.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(WRAP), "-Root", str(tmp_path / "none"),
                        "-Python", sys.executable, "-ReportScript", str(fake)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 4, r.stdout + r.stderr
    log = (tmp_path / "none" / "retro" / "scheduled_runs.log").read_text(encoding="ascii")
    assert "config missing" in log and len(log.strip().splitlines()) == 1


def test_unix_examples_use_placeholders_only():
    plist = _text(PLIST)
    cron = _text(CRON)
    assert "StartCalendarInterval" in plist
    assert re.search(r"^0 9 1 \* \* .*python3 -I .*retro_report\.py", cron, re.M)
    assert ">>" in cron and "<your root>" in cron
    assert "&lt;path to groundwork plugin&gt;" in plist and "&lt;your root&gt;" in plist
    assert "<path to groundwork plugin>" in cron
    import xml.dom.minidom
    xml.dom.minidom.parseString(plist)
    for t in (plist, cron):
        assert not re.search(r"[A-Za-z]:\\", t)
        assert not re.search(r"/(Users|home)/(?!<)", t)


def _pwsh(*args):
    return subprocess.run([PWSH, "-NoProfile", "-File"] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=120)


def test_this_test_file_is_pure_ascii():
    assert all(b < 128 for b in Path(__file__).read_bytes())


ODD_NAMES = ["plain\\", "with space\\", "it's here\\"]


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
@pytest.mark.parametrize("name", ODD_NAMES)
def test_plan_and_vbs_survive_trailing_backslash_space_apostrophe(tmp_path, name):
    root = str(tmp_path) + "\\" + name
    Path(root).mkdir(parents=True, exist_ok=True)
    r = _pwsh(PS1, "-Root", root)
    assert r.returncode == 0, r.stdout + r.stderr
    vbs_path = tmp_path / name.rstrip("\\") / "retro" / "run_retro_hidden.vbs"
    vbs = vbs_path.read_text(encoding="ascii")
    assert '\\""' not in vbs
    assert '-Root ""%s""' % str(tmp_path / name.rstrip("\\")) in vbs
    low = vbs.lower()
    assert all(b < 128 for b in vbs.encode("ascii"))
    for term in FORBIDDEN + ["s4u"]:
        assert term not in low, term


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
@pytest.mark.parametrize("name", ODD_NAMES)
def test_wrapper_runs_with_trailing_backslash_space_apostrophe(tmp_path, name):
    root = tmp_path / name.rstrip("\\")
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text("{}", encoding="utf-8")
    seen = tmp_path / "argv.txt"
    fake = tmp_path / "fake report.py"
    code = "import sys\nopen(%r, 'w').write('|'.join(sys.argv[1:]))\nsys.exit(0)\n"
    fake.write_text(code % str(seen), encoding="utf-8")
    r = _pwsh(WRAP, "-Root", str(tmp_path) + "\\" + name, "-Python", sys.executable, "-ReportScript", fake)
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text(encoding="utf-8") == "--root|%s" % root


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
def test_wrapper_logs_python_not_found(tmp_path):
    root = tmp_path / "r"
    (root / "retro").mkdir(parents=True)
    (root / "retro" / "config.json").write_text("{}", encoding="utf-8")
    r = _pwsh(WRAP, "-Root", root, "-Python", str(tmp_path / "no_such_python.exe"))
    assert r.returncode == 1
    log = (root / "retro" / "scheduled_runs.log").read_text(encoding="ascii")
    assert "FAILED exit 1: python not found or could not start (" in log


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
@pytest.mark.parametrize("val,code,word", [("0", 0, "success"), ("267011", 267011, "never run"),
                                           ("3", 3, "3"), ("2147943726", -2147023570, "2147943726")])
def test_verify_is_locale_independent(val, code, word):
    r = _pwsh(PS1, "-Verify", "-VerifyUseResult", val)
    assert r.returncode == code % (2 ** 32) or r.returncode == code, r.stdout + r.stderr
    assert word in r.stdout


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
@pytest.mark.parametrize("args", [["-Day", "0"], ["-Day", "29"], ["-Time", "25:00"], ["-Time", "9am"],
                                  ["-TaskName", 'bad"name']])
def test_invalid_day_time_taskname_are_refused(tmp_path, args):
    r = _pwsh(PS1, "-Root", tmp_path, *args)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Not registered" not in r.stdout
    assert "must be" in r.stdout


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")
def test_plan_quotes_taskname_and_has_notes(tmp_path):
    r = _pwsh(PS1, "-Root", tmp_path, "-TaskName", "My Task")
    assert r.returncode == 0
    assert '/TN "My Task"' in r.stdout
    low = r.stdout.lower()
    assert "battery" in low and "plugin install folder" in low and "xcode" in low


def test_verify_does_not_use_english_label():
    text = _text(PS1)
    assert "Get-ScheduledTaskInfo" in text and "LastTaskResult" in text
    assert "Last Result:" not in text
