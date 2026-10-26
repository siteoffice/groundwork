"""Real-git tests for the Fleet's folder-trust check and its hardened git diff.

The Fleet (plugins/groundwork-mods/hooks/fleet/register.tsx) asks git for config settings that run
programs before it runs diff, ls-files or status in a folder. These tests run the same argv with a
real git, so a mocked "no output" can never hide what git really prints.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "plugins" / "groundwork-mods" / "hooks" / "fleet" / "register.tsx"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

NUL = "NUL" if os.name == "nt" else "/dev/null"
SCOPES = ("system", "global", "local", "worktree", "command")


def runs_programs():
    """The RUNS_PROGRAMS string literal of register.tsx, as the regex git receives."""
    m = re.search(r"^const RUNS_PROGRAMS = '(.*)'$", REGISTER.read_text(encoding="utf-8"), re.M)
    assert m, "const RUNS_PROGRAMS = '...' not found in register.tsx"
    return m.group(1).replace("\\\\", "\\")


def fleet_env():
    """A test environment modelled on gitConfigEnv in threads.ts (the same two GIT_CONFIG pairs), plus
    settings the Fleet does not set: no system config, an empty global config, no prompts. Not the Fleet's exact env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": NUL,
        }
    )
    return env


def hardened(*args):
    return [GIT, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + NUL, *args]


def line_is_untrusted(line):
    # Python port of lineIsUntrusted in hooks/fleet/register.tsx; keep in step by hand; the regex text itself is read from register.tsx.
    scope, tab, rest = line.partition("\t")
    scope = scope.strip()
    if scope not in SCOPES:
        return True
    if scope in ("system", "global"):
        return False
    parts = rest.strip().split()
    key, value = (parts[0].lower() if parts else ""), " ".join(parts[1:])
    if key == "extensions.worktreeconfig":
        return value.lower() in ("true", "yes", "on", "1")
    return True


def check(repo):
    """The Fleet's config check, run with the Fleet's flags and env. Returns (exit code, lines)."""
    r = subprocess.run(
        hardened("-C", str(repo), "config", "--show-scope", "--get-regexp", runs_programs()),
        env=fleet_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode, [l for l in r.stdout.splitlines() if l.strip()]


def trusted(code, lines):
    # Mirrors isTrustedFolder: only git's own exit 0 or 1, and no line that counts.
    return code in (0, 1) and not any(line_is_untrusted(l) for l in lines)


def git_in(repo, *args):
    env = fleet_env()
    for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
        env.pop(k)
    r = subprocess.run([GIT, "-C", str(repo), *args], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git_in(d, "init", "-q")
    return d


def test_the_pattern_no_longer_names_the_fleet_own_settings():
    rx = runs_programs()
    assert "fsmonitor" not in rx and "hookspath" not in rx
    assert "sshcommand" in rx and "clean|smudge|process" in rx


def test_a_clean_repo_is_trusted(repo):
    code, lines = check(repo)
    assert trusted(code, lines), (code, lines)
    assert not any(line.startswith("local") or line.startswith("worktree") for line in lines)


def test_any_command_scope_match_is_untrusted():
    assert not trusted(0, ["command\tcore.sshcommand evil"])
    assert not trusted(0, ["command\tcore.pager evil"])


def test_a_thrown_or_failed_check_is_untrusted():
    assert not trusted(-1, [])
    assert not trusted(2, [])
    assert not trusted(128, [])


@pytest.mark.parametrize(
    "key, value",
    [
        ("filter.evil.clean", "sh -c evil"),
        ("filter.evil.smudge", "sh -c evil"),
        ("core.sshCommand", "ssh -o ProxyCommand=evil"),
        ("core.pager", "evil"),
        ("diff.x.textconv", "evil"),
        ("include.path", "../evil.cfg"),
    ],
)
def test_a_repo_local_program_setting_is_a_local_scope_line_and_untrusted(repo, key, value):
    git_in(repo, "config", "--local", key, value)
    code, lines = check(repo)
    local = [l for l in lines if l.startswith("local\t")]
    assert local, (code, lines)
    assert not trusted(code, lines)


def test_a_submodule_filter_runs_through_diff_without_ignore_submodules_and_not_with_it(tmp_path):
    # This exercises git's behaviour with the same flags as hardenGit; it does not call hardenGit itself.
    sub = tmp_path / "sub"
    sup = tmp_path / "super"
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    for d in (sub, sup):
        d.mkdir()
        git_in(d, "init", "-q")
    (sub / "a.txt").write_text("one\n", encoding="ascii")
    (sub / ".gitattributes").write_text("a.txt filter=evil\n", encoding="ascii")
    git_in(sub, "add", "-A")
    git_in(sub, *ident, "commit", "-q", "-m", "sub")
    added = subprocess.run(
        [GIT, "-c", "protocol.file.allow=always", "-C", str(sup), "submodule", "add", "-q", sub.as_posix(), "sub"],
        env=fleet_env() | {"GIT_CONFIG_COUNT": "0"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if added.returncode != 0:
        pytest.skip("git cannot create a local-path submodule here: " + added.stderr.strip())
    git_in(sup, *ident, "commit", "-q", "-m", "super")
    inner = sup / "sub"

    def arm(marker):
        # The filter lives in the submodule's own config and writes a marker file when it runs.
        git_in(inner, "config", "filter.evil.clean", 'echo hit > "' + marker.as_posix() + '"; cat')
        # Touch the file without changing it: git sees a new mtime, so it hashes the content through the filter.
        later = (inner / "a.txt").stat().st_mtime + 60
        os.utime(inner / "a.txt", (later, later))

    def diff(*extra):
        argv = hardened("-C", str(sup), "diff", "--no-ext-diff", "--no-textconv", *extra, "--numstat", "HEAD")
        r = subprocess.run(argv, env=fleet_env(), capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr

    with_flag = tmp_path / "marker-with-flag"
    without_flag = tmp_path / "marker-without-flag"
    arm(with_flag)
    diff("--ignore-submodules=all")
    assert not with_flag.exists()
    arm(without_flag)
    diff()
    assert without_flag.exists(), "without the flag git ran the submodule's own filter"
