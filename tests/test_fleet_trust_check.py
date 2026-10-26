"""Real-git tests for the Fleet's folder-trust check and its hardened git diff.

The Fleet (via plugins/groundwork-mods/hooks/gitsafe.ts) asks git for config settings that run
programs before it runs diff, ls-files or status in a folder. These tests run the same argv with a
real git, so a mocked "no output" can never hide what git really prints.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITSAFE = Path(os.environ.get("GITSAFE_TS") or ROOT / "plugins" / "groundwork-mods" / "hooks" / "gitsafe.ts")  # GITSAFE_TS: a scratch copy, to prove a test can fail
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

NUL = "NUL" if os.name == "nt" else "/dev/null"
SCOPES = ("system", "global", "local", "worktree", "command")


def runs_programs():
    """The RUNS_PROGRAMS string literal of gitsafe.ts, as the regex git receives."""
    m = re.search(r"^const RUNS_PROGRAMS = '(.*)'$", GITSAFE.read_text(encoding="utf-8"), re.M)
    assert m, "const RUNS_PROGRAMS = '...' not found in gitsafe.ts"
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


NODE = shutil.which("node")


def _node_runs_ts():
    """Node strips types by default from 22.18 (earlier 22.x needs a flag this suite does not pass)."""
    if NODE is None:
        return False
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=30).stdout.strip().lstrip("v")
    major, minor = (int(x) for x in out.split(".")[:2])
    return (major, minor) >= (22, 18)



def node_json(body, *args):
    """Run a snippet in node against the REAL gitsafe.ts (node strips the types), never a Python copy of it."""
    if not _node_runs_ts():
        pytest.skip("node 22.18 or newer is not installed")
    script = "import(process.argv[1]).then(m => { const a = process.argv.slice(2).map(x => JSON.parse(x)); return Promise.resolve((" + body + ")(m, ...a)).then(v => console.log(JSON.stringify(v))) })"
    r = subprocess.run(
        [NODE, "-e", script, GITSAFE.as_uri(), *[json.dumps(a) for a in args]],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def line_is_untrusted(line):
    # The real lineIsUntrusted of hooks/gitsafe.ts, called through node.
    return node_json("(m, l) => m.lineIsUntrusted(l)", line)


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
    if code not in (0, 1):
        return False
    return not lines or not any(node_json("(m, ls) => ls.map(l => m.lineIsUntrusted(l))", lines))


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


def test_a_program_valued_command_scope_match_is_untrusted():
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
        ("lfs.customtransfer.evil.path", "evil"),
        ("lfs.standalonetransferagent", "evil"),
        ("lfs.extension.evil.clean", "sh -c evil"),
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


def test_the_pattern_lives_only_in_gitsafe():
    hooks = ROOT / "plugins" / "groundwork-mods" / "hooks"
    assert GITSAFE.read_text(encoding="utf-8").count("const RUNS_PROGRAMS = '") == 1
    for f in (hooks / "fleet" / "register.tsx", hooks / "git" / "register.tsx"):
        assert "RUNS_PROGRAMS = " not in f.read_text(encoding="utf-8"), f


def test_outside_a_repository_the_check_is_clean(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, lines = check(plain)
    assert code == 1 and lines == [], (code, lines)


def test_a_subfolder_of_a_hostile_repo_is_untrusted(repo):
    git_in(repo, "config", "--local", "filter.evil.clean", "sh -c evil")
    (repo / "src").mkdir()
    assert not trusted(*check(repo / "src"))


def test_the_lines_the_lane_tests_mock_are_what_git_prints(repo):
    git_in(repo, "config", "--local", "filter.evil.clean", "sh -c evil")
    git_in(repo, "config", "--local", "core.sshCommand", "ssh -o ProxyCommand=evil")
    _, lines = check(repo)
    assert "local\tfilter.evil.clean sh -c evil" in lines, lines
    assert "local\tcore.sshcommand ssh -o ProxyCommand=evil" in lines, lines


def test_the_lane_uses_the_shared_trust_check():
    lane = (ROOT / "plugins" / "groundwork-mods" / "hooks" / "git" / "register.tsx").read_text(encoding="utf-8")
    assert "from '../gitsafe'" in lane and "isTrustedFolder(" in lane


def set_local(repo, *pairs):
    for k, v in pairs:
        git_in(repo, "config", "--local", k, v)


EXT = "ext::sh -c touch% marker"
HOSTILE_COMBOS = {
    "ext remote url": [("protocol.ext.allow", "always"), ("remote.origin.url", EXT)],
    "ext remote url alone": [("remote.origin.url", EXT)],
    "ext pushurl": [("remote.origin.pushurl", EXT)],
    "fd remote url": [("remote.origin.url", "fd::3")],
    "insteadOf rewrite": [("url.ext::sh -c touch% marker.insteadOf", "https://example.com/")],
    "pushInsteadOf rewrite": [("url.ext::x.pushInsteadOf", "https://example.com/")],
    "core.gitProxy": [("core.gitProxy", "evil")],
    "gpg.program": [("gpg.program", "evil")],
    "gpg.ssh.program": [("gpg.ssh.program", "evil")],
    "sequence.editor": [("sequence.editor", "evil")],
    "merge driver": [("merge.x.driver", "evil %O %A %B")],
    "remote.x.uploadpack": [("remote.origin.uploadpack", "evil")],
    "remote.x.receivepack": [("remote.origin.receivepack", "evil")],
    "remote.x.vcs": [("remote.origin.vcs", "evil")],
    "remote.x.proxy": [("remote.origin.proxy", "evil")],
    "protocol.ext.allow alone": [("protocol.ext.allow", "always")],
    "protocol.allow alone": [("protocol.allow", "always")],
}


@pytest.mark.parametrize("name", list(HOSTILE_COMBOS))
def test_a_repo_local_network_or_commit_program_setting_is_untrusted(repo, name):
    set_local(repo, *HOSTILE_COMBOS[name])
    code, lines = check(repo)
    assert not trusted(code, lines), (name, code, lines)


TRUSTED_COMBOS = {
    "https remote": [("remote.origin.url", "https://github.com/example/repo.git")],
    "ssh scp-style remote": [("remote.origin.url", "git@github.com:example/repo.git")],
    "ssh url remote": [("remote.origin.url", "ssh://git@host.example/example/repo.git")],
    "local path remote": [("remote.origin.url", "/outside/other/repo"), ("remote.origin.pushurl", "C:/work/other")],
    "gpgsign alone": [("commit.gpgsign", "true")],
}


@pytest.mark.parametrize("name", list(TRUSTED_COMBOS))
def test_a_normal_remote_or_gpgsign_alone_stays_trusted(repo, name):
    set_local(repo, *TRUSTED_COMBOS[name])
    code, lines = check(repo)
    assert trusted(code, lines), (name, code, lines)


def test_the_remote_url_line_is_a_local_scope_line_decided_by_value(repo):
    set_local(repo, ("remote.origin.url", "https://github.com/example/repo.git"))
    _, lines = check(repo)
    assert "local\tremote.origin.url https://github.com/example/repo.git" in lines, lines


# ---- Iteration 3: settings that still make a lane command run a program ----
# Each scenario arms a marker file that is written only if the program ran, runs the lane's own git
# command with the REAL hardenGit argv (core.hooksPath off, fsmonitor off) and asserts both that the
# program really ran and that the trust check calls the folder untrusted.

IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


@pytest.fixture
def based(tmp_path):
    """A repo on main with one commit, so pull, push and commit have something to work on."""
    d = tmp_path / "based"
    d.mkdir()
    git_in(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("one\n", encoding="ascii")
    git_in(d, "add", "-A")
    git_in(d, *IDENT, "commit", "-q", "-m", "base")
    return d


def lane_run(repo, *args):
    """One lane git call: the real hardenGit argv from gitsafe.ts ALONE (no GIT_CONFIG pairs, so the argv's own
    -c flags are what is tested); the exit code does not matter."""
    env = fleet_env()
    for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
        env.pop(k)
    if args and isinstance(args[0], dict):
        # A repo's own .claude/settings.json can set env; ext:: needs it (or protocol.ext.allow, which the pattern names).
        env.update(args[0])
        args = args[1:]
    argv = node_json("(m, a, w) => m.hardenGit(a, w)", ["git", *IDENT, "-C", str(repo), *args], os.name == "nt")
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)


def say_hit(marker):
    return 'echo hit > "' + marker.as_posix() + '"'


def prepare_hook(repo, tmp_path, marker):
    set_local(
        repo,
        ("hook.evil.event", "pre-commit"),
        ("hook.evil.command", say_hit(marker)),
        ("core.hooksPath", NUL),
    )
    (repo / "b.txt").write_text("two\n", encoding="ascii")
    return [("add", "-A"), ("commit", "-q", "-m", "x")]


def prepare_gpg(repo, tmp_path, marker):
    script = tmp_path / "key.sh"
    script.write_bytes(("touch " + marker.as_posix() + "\necho ssh-ed25519 AAAA\n").encode("ascii"))
    set_local(
        repo,
        ("commit.gpgsign", "true"),
        ("gpg.format", "ssh"),
        ("gpg.ssh.defaultKeyCommand", "sh " + script.as_posix()),
    )
    (repo / "b.txt").write_text("two\n", encoding="ascii")
    return [("add", "-A"), ("commit", "-q", "-m", "x")]


def prepare_alias_pull(repo, tmp_path, marker):
    set_local(
        repo,
        ("alias.remote-evil", "!" + say_hit(marker)),
        ("branch.main.remote", "evil::x"),
        ("branch.main.merge", "refs/heads/main"),
    )
    return [("pull", "--ff-only")]


def prepare_alias_push(repo, tmp_path, marker):
    set_local(
        repo,
        ("alias.remote-evil", "!" + say_hit(marker)),
        ("remote.pushDefault", "evil::x"),
        ("push.default", "current"),
    )
    return [("push",)]


def prepare_alternate_refs(repo, tmp_path, marker):
    other = tmp_path / "other"
    other.mkdir()
    git_in(other, "init", "-q", "-b", "main")
    (other / "o.txt").write_text("o\n", encoding="ascii")
    git_in(other, "add", "-A")
    git_in(other, *IDENT, "commit", "-q", "-m", "other")
    info = repo / ".git" / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_bytes((other / ".git" / "objects").as_posix().encode("ascii") + b"\n")
    set_local(
        repo,
        ("core.alternateRefsCommand", say_hit(marker) + "; true"),
        ("remote.origin.url", other.as_posix()),
    )
    return [("fetch", "origin")]


def prepare_remote_name_hole(repo, tmp_path, marker):
    set_local(repo, ("remote.x.url https://a/ y.url", "ext::sh -c touch% " + marker.as_posix()))
    return [({"GIT_ALLOW_PROTOCOL": "ext"}, "fetch", "x.url https://a/ y")]


MARKER_SCENARIOS = {
    "config hook": prepare_hook,
    "gpg ssh defaultKeyCommand": prepare_gpg,
    "alias behind a branch remote helper": prepare_alias_pull,
    "alias behind a pushDefault helper": prepare_alias_push,
    "core.alternateRefsCommand": prepare_alternate_refs,
    "remote name that hides a helper url": prepare_remote_name_hole,
}


@pytest.mark.parametrize("name", list(MARKER_SCENARIOS))
def test_a_program_that_really_runs_in_a_hardened_lane_command_is_untrusted(based, tmp_path, name):
    marker = tmp_path / "marker.txt"
    commands = MARKER_SCENARIOS[name](based, tmp_path, marker)
    for argv in commands:
        lane_run(based, *argv)
    if not marker.exists():
        pytest.skip("this git does not run it here: " + name)
    code, lines = check(based)
    assert not trusted(code, lines), (name, code, lines)


def test_the_remote_name_hole_prints_the_way_the_check_reads_it(based):
    set_local(based, ("remote.x.url https://a/ y.url", "ext::sh -c touch% marker"))
    _, lines = check(based)
    assert "local\tremote.x.url https://a/ y.url ext::sh -c touch% marker" in lines, lines


def make_super_with_sub(tmp_path):
    sub = tmp_path / "sub"
    sup = tmp_path / "super"
    for d in (sub, sup):
        d.mkdir()
        git_in(d, "init", "-q")
    (sub / "a.txt").write_text("one\n", encoding="ascii")
    (sub / ".gitattributes").write_text("a.txt filter=evil\n", encoding="ascii")
    git_in(sub, "add", "-A")
    git_in(sub, *IDENT, "commit", "-q", "-m", "sub")
    added = subprocess.run(
        [GIT, "-c", "protocol.file.allow=always", "-C", str(sup), "submodule", "add", "-q", sub.as_posix(), "sub"],
        env=fleet_env() | {"GIT_CONFIG_COUNT": "0"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if added.returncode != 0:
        pytest.skip("git cannot create a local-path submodule here: " + added.stderr.strip())
    git_in(sup, *IDENT, "commit", "-q", "-m", "super")
    return sup, sup / "sub"


def test_the_lane_status_does_not_run_a_submodule_filter(tmp_path):
    sup, inner = make_super_with_sub(tmp_path)
    marker = tmp_path / "marker-status.txt"
    git_in(inner, "config", "filter.evil.clean", say_hit(marker) + "; cat")
    later = (inner / "a.txt").stat().st_mtime + 60
    os.utime(inner / "a.txt", (later, later))
    # The lane's own status argv, through the real hardenGit.
    argv = node_json(
        "(m, a, w) => m.hardenGit(a, w)",
        ["git", "-c", "core.quotePath=false", "-C", str(sup), "status", "--porcelain=v2", "--branch"],
        os.name == "nt",
    )
    assert "--ignore-submodules=all" in argv
    r = subprocess.run(argv, env=fleet_env(), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists(), "the lane's status ran the submodule's own filter"
    # And the same status without the flag does run it, so the test can fail.
    bare = [a for a in argv if a != "--ignore-submodules=all"]
    subprocess.run(bare, env=fleet_env(), capture_output=True, text=True, timeout=120)
    assert marker.exists(), "without the flag git ran the submodule's own filter"


def test_hardengit_adds_ignore_submodules_to_status_only_once_and_not_to_other_commands():
    win = os.name == "nt"
    one = node_json("(m, a, w) => m.hardenGit(a, w)", ["git", "-C", "/r", "status", "--porcelain=v2", "--branch"], win)
    assert one.count("--ignore-submodules=all") == 1 and "status" in one
    other = node_json("(m, a, w) => m.hardenGit(a, w)", ["git", "-C", "/r", "log", "-3"], win)
    assert "--ignore-submodules=all" not in other
    named = node_json("(m, a, w) => m.hardenGit(a, w)", ["git", "-C", "status", "log"], win)
    assert "--ignore-submodules=all" not in named


HOSTILE_ITER3 = {
    "hook command": [("hook.evil.command", "evil")],
    "hook event": [("hook.evil.event", "pre-commit")],
    "hook enabled": [("hook.evil.enabled", "true")],
    "gpg.format": [("gpg.format", "ssh")],
    "gpg.ssh.defaultKeyCommand": [("gpg.ssh.defaultKeyCommand", "evil")],
    "gpg.ssh.allowedSignersFile": [("gpg.ssh.allowedSignersFile", "x")],
    "alias bang": [("alias.remote-evil", "!evil")],
    "alias plain": [("alias.co", "checkout")],
    "branch remote helper": [("branch.main.remote", "ext::sh -c touch% m")],
    "branch pushremote helper": [("branch.main.pushremote", "fd::3")],
    "pushdefault helper": [("remote.pushDefault", "ext::x")],
    "alternateRefsCommand": [("core.alternateRefsCommand", "evil")],
    "url any key": [("url.https://x/.somekey", "value")],
    "url instead of": [("url.https://x/.insteadOf", "y")],
    "remote name hole url": [("remote.x.url https://a/ y.url", "ext::sh -c touch% m")],
    "remote name hole pushurl": [("remote.x.pushurl https://a/ y.pushurl", "fd::3")],
    "remote name hole branch": [("branch.m.remote https://a/ y.remote", "ext::x")],
    "pushdefault tokens": [("remote.pushDefault", "origin ext::x")],
    "diff.external": [("diff.external", "evil")],
    "core.gitProxy": [("core.gitProxy", "evil")],
    "merge driver": [("merge.x.driver", "evil")],
    "sequence.editor": [("sequence.editor", "evil")],
    "worktreeconfig": [("extensions.worktreeConfig", "true")],
}


@pytest.mark.parametrize("name", list(HOSTILE_ITER3))
def test_iteration_three_settings_are_untrusted(repo, name):
    set_local(repo, *HOSTILE_ITER3[name])
    code, lines = check(repo)
    assert lines, (name, code, lines)
    assert not trusted(code, lines), (name, code, lines)


NORMAL_REMOTES = [
    "https://host.example/x",
    "git@host.example:path/x.git",
    "ssh://h/p",
    "C:/path",
    "/path",
    "http://[::1]/x",
    "https://host.example/a::b",
]


@pytest.mark.parametrize("url", NORMAL_REMOTES)
@pytest.mark.parametrize("key", ["remote.origin.url", "remote.origin.pushurl"])
def test_a_normal_remote_url_of_every_kind_stays_trusted(repo, key, url):
    set_local(repo, (key, url))
    code, lines = check(repo)
    assert trusted(code, lines), (key, url, code, lines)


NORMAL_REPOS = {
    "user settings": [("user.name", "t"), ("user.email", "t@example.com"), ("user.signingkey", "ABC")],
    "core.autocrlf": [("core.autocrlf", "true")],
    "pull.rebase": [("pull.rebase", "true")],
    "branch remote and merge": [("branch.main.remote", "origin"), ("branch.main.merge", "refs/heads/main"), ("branch.main.rebase", "true")],
    "branch remote dot": [("branch.main.remote", ".")],
    "pushdefault name": [("remote.pushDefault", "origin")],
    "pushremote name": [("branch.main.pushremote", "origin")],
    "gpgsign alone": [("commit.gpgsign", "true")],
    "push.default": [("push.default", "simple")],
    "core.ignorecase": [("core.ignorecase", "false"), ("core.filemode", "false")],
}


@pytest.mark.parametrize("name", list(NORMAL_REPOS))
def test_a_normal_repo_setting_stays_trusted(repo, name):
    set_local(repo, *NORMAL_REPOS[name])
    code, lines = check(repo)
    assert trusted(code, lines), (name, code, lines)


def test_a_normal_repo_opened_in_a_subfolder_stays_trusted(repo):
    set_local(repo, ("remote.origin.url", "https://host.example/x"), ("branch.main.remote", "origin"), ("user.name", "t"))
    (repo / "src").mkdir()
    code, lines = check(repo / "src")
    assert trusted(code, lines), (code, lines)


def test_whyuntrusted_names_the_first_offending_setting(repo):
    set_local(repo, ("filter.evil.clean", "sh -c evil"))
    out = node_json(
        "async (m, dir) => { const $ = { plugin: { root: 'C:/x' }, process: { run: async (argv) => ({ exitCode: 0, stdout: 'local\\tfilter.evil.clean sh -c evil\\n', stderr: '' }) } }; const s = new Set(); const t = await m.isTrustedFolder($, dir, s); return [t, m.whyUntrusted(dir)] }",
        str(repo),
    )
    assert out == [False, "filter.evil.clean"]
    failed = node_json(
        "async (m, dir) => { const $ = { plugin: { root: 'C:/x' }, process: { run: async () => { throw new Error('boom') } } }; const s = new Set(); const t = await m.isTrustedFolder($, dir, s); return [t, m.whyUntrusted(dir)] }",
        str(repo) + "-failed",
    )
    assert failed == [False, "the settings check did not finish"]


# ---- Iteration 4: a remote url that points INTO the repo, and free text that looks like a subcommand ----

REAL_TRUST = (
    "async (m, dir, base) => { const cp = require('child_process'); "
    "const $ = { plugin: { root: process.platform === 'win32' ? 'C:/x' : '/x' }, fs: { exists: async p => require('fs').existsSync(p) }, process: { run: async (argv, o) => { "
    "const r = cp.spawnSync(argv[0], argv.slice(1), { cwd: o.cwd, env: Object.assign({}, base, o.env), encoding: 'utf8' }); "
    "return { exitCode: r.status, stdout: r.stdout, stderr: r.stderr } } } }; "
    "const s = new Set(); const t = await m.isTrustedFolder($, dir, s); return [t, m.whyUntrusted(dir)] }"
)


def real_trust(folder, real_env=None):
    """The REAL isTrustedFolder of gitsafe.ts (through node), driving a REAL git. Returns (trusted, reason).
    real_env: a base environment to use instead of the isolated one (the machine's real system config)."""
    base = real_env or {k: v for k, v in fleet_env().items() if not k.startswith("GIT_CONFIG_KEY") and not k.startswith("GIT_CONFIG_VALUE") and k != "GIT_CONFIG_COUNT"}
    return tuple(node_json(REAL_TRUST, str(folder), base))


REAL_LANE = REAL_TRUST.replace(
    "const t = await m.isTrustedFolder($, dir, s);",
    "let t = await m.isTrustedFolder($, dir, s); if (t) t = (await m.hasSubmodules($, dir, s)) === '';",
)
assert REAL_LANE != REAL_TRUST


def lane_trust(folder, real_env=None):
    """What the Git lane's trustedHere does: isTrustedFolder, then hasSubmodules if that said trusted. Real git."""
    base = real_env or {k: v for k, v in fleet_env().items() if not k.startswith("GIT_CONFIG_KEY") and not k.startswith("GIT_CONFIG_VALUE") and k != "GIT_CONFIG_COUNT"}
    return tuple(node_json(REAL_LANE, str(folder), base))


def file_url(path):
    p = Path(path).as_posix()
    return "file:///" + p if not p.startswith("/") else "file://" + p


def inside_urls(repo):
    repo = repo.resolve()
    abs_in = (repo / "inside.git").as_posix()
    out = {
        "dot slash relative": "./inside.git",
        "bare relative": "inside.git",
        "parent relative": "../sibling.git",
        "absolute inside": abs_in,
        "absolute inside with dotdot": (repo / "sub" / ".." / "inside.git").as_posix(),
        "file url inside": file_url(repo / "inside.git"),
    }
    if os.name == "nt":
        out["windows drive path inside"] = abs_in
        out["windows backslash path inside"] = str(repo / "inside.git")
        out["windows upper-case drive inside"] = abs_in[0].upper() + abs_in[1:]
        out["windows lower-case drive inside"] = abs_in[0].lower() + abs_in[1:]
    return out


@pytest.mark.parametrize("key", ["remote.origin.url", "remote.origin.pushurl"])
@pytest.mark.parametrize("name", list(inside_urls(Path(os.getcwd()))))
def test_a_remote_url_inside_the_repo_is_untrusted(repo, key, name):
    url = inside_urls(repo)[name]
    set_local(repo, (key, url))
    ok, why = real_trust(repo)
    assert not ok and why == key, (name, url, ok, why)


@pytest.fixture
def elsewhere(tmp_path):
    d = tmp_path / "elsewhere"
    d.mkdir()
    git_in(d, "init", "-q", "--bare", "other.git")
    return d / "other.git"


@pytest.mark.parametrize("key", ["remote.origin.url", "remote.origin.pushurl"])
@pytest.mark.parametrize(
    "kind",
    ["https", "ssh url", "scp", "scp no user", "ipv6", "outside absolute", "outside file url", "outside backslash"],
)
def test_a_remote_url_outside_the_repo_stays_trusted(repo, elsewhere, key, kind):
    url = {
        "https": "https://github.com/example/repo.git",
        "ssh url": "ssh://git@host.example/example/repo.git",
        "scp": "git@github.com:example/repo.git",
        "scp no user": "host.example:path/repo.git",
        "ipv6": "http://[::1]/x",
        "outside absolute": elsewhere.as_posix(),
        "outside file url": file_url(elsewhere),
        "outside backslash": str(elsewhere),
    }[kind]
    set_local(repo, (key, url))
    ok, why = real_trust(repo)
    assert ok, (kind, url, why)


def test_a_repo_in_a_subfolder_with_an_outside_remote_stays_trusted_and_an_inside_one_does_not(repo, elsewhere):
    (repo / "src").mkdir()
    set_local(repo, ("remote.origin.url", elsewhere.as_posix()))
    assert real_trust(repo / "src")[0]
    set_local(repo, ("remote.origin.pushurl", (repo / "inside.git").resolve().as_posix()))
    assert not real_trust(repo / "src")[0]


def test_an_absolute_inside_url_with_an_unreadable_toplevel_fails_closed(repo):
    out = node_json(
        "async (m, dir) => { const $ = { plugin: { root: 'C:/x' }, process: { run: async (argv) => argv.includes('rev-parse') ? ({ exitCode: 128, stdout: '', stderr: 'no' }) : ({ exitCode: 0, stdout: 'local\\tremote.origin.url /somewhere/x.git\\n', stderr: '' }) } }; const t = await m.isTrustedFolder($, dir, new Set()); return [t, m.whyUntrusted(dir)] }",
        str(repo),
    )
    assert out == [False, "remote.origin.url"]


def test_a_hook_shipped_in_a_bare_repo_inside_the_repo_runs_on_push_and_the_check_refuses_the_folder(based, tmp_path):
    marker = tmp_path / "marker-inside.txt"
    git_in(based, "init", "-q", "--bare", "inside.git")
    hook = based / "inside.git" / "hooks" / "pre-receive"
    hook.write_bytes(("#!/bin/sh\n" + say_hit(marker) + "\nexit 0\n").encode("ascii"))
    hook.chmod(0o755)
    set_local(based, ("remote.origin.url", "./inside.git"))
    # The lane's own push argv, hardened, with the Fleet's env: core.hooksPath=NUL does not reach the receiving side.
    lane_run(based, "push", "origin", "HEAD:refs/heads/x")
    if not marker.exists():
        pytest.skip("this git does not run the receiving hook of a local remote here")
    ok, why = real_trust(based)
    assert not ok and why == "remote.origin.url", (ok, why)


def test_hardengit_leaves_free_text_alone_in_real_git(based):
    for word in ("status", "diff"):
        (based / "a.txt").write_text(word + "\n", encoding="ascii")
        git_in(based, "add", "-A")
        r = lane_run(based, "commit", "-m", word)
        assert r.returncode == 0, (word, r.stdout, r.stderr)
        assert git_in(based, "log", "-1", "--format=%s").stdout.strip() == word


# ---- Iteration 5: submodules, UNC remotes, the real global/system config, hardenGit's own flags ----


def test_a_repo_with_a_real_submodule_is_untrusted(tmp_path):
    sup, _ = make_super_with_sub(tmp_path)
    assert lane_trust(sup) == (False, "submodules")
    # The Fleet's own check (isTrustedFolder alone) does not call the submodule probe.
    assert real_trust(sup) == (True, "")


def test_a_gitmodules_file_alone_makes_the_folder_untrusted(repo):
    (repo / ".gitmodules").write_bytes(b'[submodule "x"]\n\tpath = x\n\turl = https://host.example/x\n')
    assert lane_trust(repo) == (False, "submodules")


def test_a_git_dir_with_a_modules_folder_makes_the_folder_untrusted(repo):
    (repo / ".git" / "modules").mkdir()
    assert lane_trust(repo) == (False, "submodules")


def test_a_gitmodules_file_is_found_from_a_subfolder(repo):
    (repo / ".gitmodules").write_bytes(b"")
    (repo / "src").mkdir()
    assert lane_trust(repo / "src") == (False, "submodules")


@pytest.mark.parametrize("key", ["submodule.recurse", "fetch.recurseSubmodules", "push.recurseSubmodules"])
def test_a_local_submodule_recursion_setting_is_untrusted(repo, key):
    set_local(repo, (key, "true"))
    code, lines = check(repo)
    assert lines and not trusted(code, lines), (key, code, lines)
    ok, why = real_trust(repo)
    assert not ok and why == key.lower(), (key, ok, why)


def test_a_plain_repo_and_a_subfolder_and_a_non_repo_stay_trusted_with_the_submodule_probe(repo, tmp_path):
    assert lane_trust(repo) == (True, "")
    (repo / "src").mkdir()
    assert lane_trust(repo / "src") == (True, "")
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert lane_trust(plain) == (True, "")


def test_a_rev_parse_failure_that_is_not_a_missing_repo_fails_closed():
    mock = (
        "async (m, which) => { const $ = { plugin: { root: 'C:/x' }, fs: { exists: async () => false }, process: { run: async (argv) => "
        "which === 'other' ? { exitCode: 128, stdout: '', stderr: 'fatal: detected dubious ownership' } : which === 'short' ? { exitCode: 0, stdout: '/r\\n', stderr: '' } : which === 'exit1' ? { exitCode: 1, stdout: '', stderr: '' } : { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository (or any of the parent directories): .git' } } }; "
        "const s = new Set(); const r = await m.hasSubmodules($, '/d-' + which, s); return [r, m.whyUntrusted('/d-' + which), s.has('/d-' + which)] }"
    )
    assert node_json(mock, "missing") == ["", "", False]
    for bad in ("other", "short", "exit1"):
        assert node_json(mock, bad) == ["the settings check did not finish", "the settings check did not finish", True], bad


def test_a_hostile_submodule_runs_a_program_through_add_in_the_lane_and_the_folder_is_untrusted(tmp_path):
    sup, inner = make_super_with_sub(tmp_path)
    marker = tmp_path / "marker-submodule-add.txt"
    git_in(inner, "config", "filter.evil.clean", say_hit(marker) + "; cat")
    later = (inner / "a.txt").stat().st_mtime + 60
    os.utime(inner / "a.txt", (later, later))
    lane_run(sup, "add", "-A")
    if not marker.exists():
        pytest.skip("this git does not run a submodule filter on add -A here")
    ok, why = lane_trust(sup)
    assert not ok and why == "submodules", (ok, why)


UNC_URLS = {
    "backslash localhost admin share": "\\\\localhost\\C$\\inside\\inside.git",
    "slash localhost admin share": "//localhost/C$/inside/inside.git",
    "backslash server share": "\\\\server\\share\\repo.git",
    "slash server share": "//server/share/repo.git",
}


@pytest.mark.parametrize("key", ["remote.origin.url", "remote.origin.pushurl"])
@pytest.mark.parametrize("name", list(UNC_URLS))
def test_a_unc_style_remote_is_untrusted(repo, key, name):
    set_local(repo, (key, UNC_URLS[name]))
    ok, why = real_trust(repo)
    assert not ok and why == key, (name, ok, why)


@pytest.mark.skipif(os.name != "nt", reason="admin shares exist on Windows only")
def test_a_hook_in_a_bare_repo_reached_through_a_localhost_admin_share_runs_and_the_check_refuses_the_folder(based, tmp_path):
    marker = tmp_path / "marker-unc.txt"
    git_in(based, "init", "-q", "--bare", "inside.git")
    hook = based / "inside.git" / "hooks" / "pre-receive"
    hook.write_bytes(("#!/bin/sh\n" + say_hit(marker) + "\nexit 0\n").encode("ascii"))
    drive, rest = str(based.resolve() / "inside.git").split(":", 1)
    unc = "\\\\localhost\\" + drive + "$" + rest
    set_local(based, ("remote.origin.url", unc))
    lane_run(based, "push", "origin", "HEAD:refs/heads/x")
    if not marker.exists():
        pytest.skip("this machine does not reach its own admin share here")
    ok, why = real_trust(based)
    assert not ok and why == "remote.origin.url", (ok, why)


def test_system_and_global_lines_never_make_a_clean_repo_untrusted_with_the_real_system_config(repo, tmp_path):
    glob = tmp_path / "global.gitconfig"
    glob.write_bytes(b"[filter \"lfs\"]\n\tclean = git-lfs clean -- %f\n[core]\n\tsshCommand = ssh -F /x\n[credential]\n\thelper = manager\n")
    # The machine's real system config stays (no GIT_CONFIG_NOSYSTEM); only the global file is a known one.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_GLOBAL": str(glob)})
    assert "GIT_CONFIG_NOSYSTEM" not in env
    seen = subprocess.run(hardened("-C", str(repo), "config", "--show-scope", "--get-regexp", runs_programs()), env=env, capture_output=True, text=True, timeout=60)
    assert any(l.startswith("global\t") for l in seen.stdout.splitlines()), seen.stdout  # the global lines really are printed
    assert not any(l.startswith(("local\t", "worktree\t", "command\t")) for l in seen.stdout.splitlines()), seen.stdout
    assert real_trust(repo, env) == (True, "")


def test_the_gitconfig_pairs_reach_a_child_through_runGit():
    # A tiny child that prints its env, not git: runGit passes the two core.* pairs on in the environment.
    body = (
        "async (m) => { const cp = require('child_process'); "
        "const $ = { plugin: { root: 'C:/x' }, process: { run: async (argv, o) => { "
        "const r = cp.spawnSync(process.execPath, ['-e', 'console.log(JSON.stringify(process.env))'], { env: Object.assign({}, process.env, o.env), encoding: 'utf8' }); "
        "return { exitCode: r.status, stdout: r.stdout, stderr: r.stderr } } } }; "
        "const r = await m.runGit($, ['git', 'status'], 5000, '/d'); return JSON.parse(r.stdout) }"
    )
    env = node_json(body)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor" and env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_CONFIG_KEY_1"] == "core.hooksPath" and env["GIT_CONFIG_VALUE_1"] == NUL


def test_the_hardened_argv_alone_stops_a_hooks_path_hook(based, tmp_path):
    marker = tmp_path / "marker-hookspath.txt"
    hooks = tmp_path / "evilhooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_bytes(("#!/bin/sh\n" + say_hit(marker) + "\nexit 0\n").encode("ascii"))
    (hooks / "pre-commit").chmod(0o755)
    set_local(based, ("core.hooksPath", hooks.as_posix()))
    (based / "b.txt").write_text("two\n", encoding="ascii")
    git_in(based, "add", "-A")
    git_in(based, *IDENT, "commit", "-q", "-m", "control")  # no hardening: the hook runs, so the test can fail
    if not marker.exists():
        pytest.skip("this git does not run the hook here")
    marker.unlink()
    (based / "c.txt").write_text("three\n", encoding="ascii")
    git_in(based, "add", "-A")
    r = lane_run(based, "commit", "-q", "-m", "lane")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert not marker.exists(), "the lane's hardened argv let the repo's hooks path run"


def test_the_hardened_argv_alone_stops_an_fsmonitor_program(based, tmp_path):
    marker = tmp_path / "marker-fsmonitor.txt"
    prog = tmp_path / "fsmon.sh"
    prog.write_bytes(("#!/bin/sh\n" + say_hit(marker) + "\nprintf '\\0'\n").encode("ascii"))
    prog.chmod(0o755)
    set_local(based, ("core.fsmonitor", prog.as_posix()))
    env = fleet_env()
    for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
        env.pop(k)
    subprocess.run([GIT, "-C", str(based), "status"], env=env, capture_output=True, text=True, timeout=60)
    if not marker.exists():
        pytest.skip("this git does not run the fsmonitor program here")
    marker.unlink()
    lane_run(based, "status", "--porcelain=v2")
    assert not marker.exists(), "the lane's hardened argv let core.fsmonitor run"


# ---- Live finding 2026-10-26: Claude Code's own command-scope pins close the lane ----

# What Claude Code 2.x adds, as command scope, to every git argv a plugin starts through $.process.run
# (captured live with `git config --show-scope --list` from a probe plugin; git listed this block twice).
# It is not in the child's environment: `cmd /c set` shows no GIT_CONFIG_PARAMETERS, and passing
# GIT_CONFIG_PARAMETERS='' and GIT_CONFIG_COUNT=0 does not remove it.
HOST_PINS = [
    "core.fsmonitor=",
    "core.hookspath=" + (r"\\.\NUL" if os.name == "nt" else "/dev/null"),
    "core.askpass=",
    "protocol.ext.allow=never",
    "submodule.recurse=false",
    "log.showsignature=false",
    "format.pretty=medium",
    "gc.auto=0",
    "maintenance.auto=false",
    *(
        "hook." + h + ".enabled=false"
        for h in (
            "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
            "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
            "pre-push", "pre-receive", "update", "proc-receive", "post-receive", "post-update",
            "reference-transaction", "push-to-checkout", "pre-auto-gc", "post-rewrite", "sendemail-validate",
            "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist",
            "p4-pre-submit", "post-index-change",
        )
    ),
]

REAL_TRUST_UNDER_HOST = REAL_TRUST.replace(
    "cp.spawnSync(argv[0], argv.slice(1),",
    "cp.spawnSync(argv[0], argv[0] === 'git' ? [...pins, ...pins, ...argv.slice(1)] : argv.slice(1),",
).replace("async (m, dir, base) =>", "async (m, dir, base, pins) =>")
assert REAL_TRUST_UNDER_HOST != REAL_TRUST


def test_the_host_pins_are_command_scope_lines_the_pattern_matches(repo):
    pins = [a for p in HOST_PINS for a in ("-c", p)]
    seen = subprocess.run([GIT, *pins, "-C", str(repo), "config", "--show-scope", "--get-regexp", runs_programs()], env=fleet_env(), capture_output=True, text=True, timeout=60)
    keys = [l.split("\t", 1)[1].split()[0] for l in seen.stdout.splitlines() if l.startswith("command\t")]
    assert "core.askpass" in keys and "protocol.ext.allow" in keys and "submodule.recurse" in keys, seen.stdout
    assert "hook.pre-commit.enabled" in keys, seen.stdout


def test_a_clean_repo_stays_trusted_under_the_host_pins(repo):
    # Reproduces the live G4 blocker: in a fresh repo the lane closed with reason core.askpass.
    base = {k: v for k, v in fleet_env().items() if not k.startswith("GIT_CONFIG_KEY") and not k.startswith("GIT_CONFIG_VALUE") and k != "GIT_CONFIG_COUNT"}
    pins = [a for p in HOST_PINS for a in ("-c", p)]
    assert tuple(node_json(REAL_TRUST_UNDER_HOST, str(repo), base, pins)) == (True, "")


@pytest.mark.parametrize(
    "extra, why",
    [
        (["core.askpass=evil.exe"], "core.askpass"),
        (["core.askpass"], "core.askpass"),
        (["hook.evil.command=sh -c evil"], "hook.evil.command"),
        (["hook.pre-commit.enabled=true"], "hook.pre-commit.enabled"),
        (["protocol.ext.allow=always"], "protocol.ext.allow"),
        (["submodule.recurse=true"], "submodule.recurse"),
        (["core.sshCommand=evil"], "core.sshcommand"),
        (["hook.pre-commit.enabled=no"], "hook.pre-commit.enabled"),
    ],
)
def test_a_switch_on_or_a_second_value_next_to_the_host_pins_closes_the_folder(repo, extra, why):
    # The last case switches off too, but a second, different value for a key the host set is never trusted.
    base = {k: v for k, v in fleet_env().items() if not k.startswith("GIT_CONFIG_KEY") and not k.startswith("GIT_CONFIG_VALUE") and k != "GIT_CONFIG_COUNT"}
    pins = [a for p in [*HOST_PINS, *extra] for a in ("-c", p)]
    assert tuple(node_json(REAL_TRUST_UNDER_HOST, str(repo), base, pins)) == (False, why)


def test_a_repo_local_switch_off_value_stays_untrusted(repo):
    # Only command scope is judged by value; local and worktree scope stay strict.
    git_in(repo, "config", "--local", "core.askpass", "")
    git_in(repo, "config", "--local", "hook.pre-commit.enabled", "false")
    assert real_trust(repo) == (False, "core.askpass")
