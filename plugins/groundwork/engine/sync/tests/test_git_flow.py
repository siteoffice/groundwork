"""Tests for sync/git_flow.py and for git_ops.commit(only_paths=...). Stdlib
unittest, run through pytest. Every test builds a throwaway clone plus a bare
remote inside a TemporaryDirectory (zero network), the same pattern as
test_git_ops.py. Nothing here patches os.name.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock

HERE = pathlib.Path(__file__).resolve().parent
SYNC_DIR = HERE.parent
for _path in (str(HERE), str(SYNC_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import git_flow  # noqa: E402
import git_ops  # noqa: E402
from test_git_ops import _init_repo_pair, _run, _second_clone  # noqa: E402


def _no_owner(repo_dir, path):
    return None


class TestCommitOnlyPaths(unittest.TestCase):
    def test_only_paths_commits_just_the_listed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            (clone / "b.md").write_text("b\n", encoding="utf-8")
            result = git_ops.commit(clone, "add a", "wilco", _no_owner, only_paths=["a.md"])
            self.assertEqual(result["committed"], ["a.md"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(in_head, ["a.md"])
            self.assertIn("b.md", _run(clone, "status", "--porcelain").stdout)

    def test_a_glob_named_only_path_commits_just_that_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "a.txt").write_text("line\n", encoding="utf-8")
            _run(clone, "add", "--", "a.txt")
            _run(clone, "commit", "-m", "seed a")
            (clone / "a.txt").write_text("changed\n", encoding="utf-8")
            (clone / "[ab].txt").write_text("glob\n", encoding="utf-8")
            result = git_ops.commit(clone, "add glob", "wilco", _no_owner, only_paths=["[ab].txt"])
            self.assertEqual(result["committed"], ["[ab].txt"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(in_head, ["[ab].txt"])
            self.assertIn(" M a.txt", _run(clone, "status", "--porcelain").stdout)
            self.assertEqual(_run(clone, "diff", "--cached", "--name-only").stdout.split(), [])

    def test_none_behaves_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            (clone / "b.md").write_text("b\n", encoding="utf-8")
            result = git_ops.commit(clone, "add both", "wilco", _no_owner, only_paths=None)
            self.assertEqual(sorted(result["committed"]), ["a.md", "b.md"])

    def test_a_listed_path_with_no_change_is_ignored_and_the_owner_rule_still_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "mine.md").write_text("m\n", encoding="utf-8")
            (clone / "theirs.md").write_text("t\n", encoding="utf-8")
            resolve = lambda repo, path: "someone-else" if path == "theirs.md" else None  # noqa: E731
            result = git_ops.commit(
                clone, "add", "wilco", resolve, only_paths=["mine.md", "theirs.md", "untouched.md"],
            )
            self.assertEqual(result["committed"], ["mine.md"])
            self.assertEqual([r["path"] for r in result["refused"]], ["theirs.md"])


class TestGitSettings(unittest.TestCase):
    def _node(self, tmp, manifest=None, raw=None):
        node = pathlib.Path(tmp)
        if raw is not None:
            (node / "siteoffice.json").write_text(raw, encoding="utf-8")
        elif manifest is not None:
            (node / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
        return node

    def test_no_manifest_gives_the_defaults_and_no_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, notes = git_flow.load_git_settings(self._node(tmp))
        self.assertEqual(settings, {
            "profile": "code", "main": "main", "protected": ["main", "master"], "checks": [],
            "checkTimeoutSeconds": 600, "denylist": ".git-denylist", "pr": {"approvals": 0},
        })
        self.assertEqual(notes, [])

    def test_valid_values_are_read(self):
        block = {
            "profile": "code", "main": "trunk", "protected": ["trunk"], "checks": ["a", "b"],
            "checkTimeoutSeconds": 30, "denylist": "conf/deny.txt", "pr": {"approvals": 2},
        }
        with tempfile.TemporaryDirectory() as tmp:
            settings, notes = git_flow.load_git_settings(
                self._node(tmp, {"settings": {"git": block}}))
        self.assertEqual(settings, block)
        self.assertEqual(notes, [])

    def test_each_invalid_value_falls_back_to_its_default_with_a_note(self):
        cases = [
            ("profile", "nonsense"), ("main", 5), ("main", "bad name"), ("protected", "main"),
            ("protected", [1]), ("checks", "pytest"), ("checks", [""]),
            ("checkTimeoutSeconds", 0), ("checkTimeoutSeconds", True), ("checkTimeoutSeconds", "9"),
            ("denylist", "/etc/deny"), ("denylist", "..\\deny"), ("denylist", "C:\\deny"),
            ("denylist", ""), ("pr", "yes"),
        ]
        defaults, _ = git_flow.load_git_settings(pathlib.Path(tempfile.gettempdir()) / "no-such-node")
        for key, value in cases:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                settings, notes = git_flow.load_git_settings(
                    self._node(tmp, {"settings": {"git": {key: value}}}))
                self.assertEqual(settings, defaults)
                self.assertEqual(len(notes), 1)
                self.assertIn(f"settings.git.{key}", notes[0])

    def test_pr_approvals_is_validated_and_stored_only(self):
        for value in (-1, 1.5, True, "1"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                settings, notes = git_flow.load_git_settings(
                    self._node(tmp, {"settings": {"git": {"pr": {"approvals": value}}}}))
                self.assertEqual(settings["pr"], {"approvals": 0})
                self.assertIn("settings.git.pr.approvals", notes[0])

    def test_docs_profile_is_not_honoured_this_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, notes = git_flow.load_git_settings(
                self._node(tmp, {"settings": {"git": {"profile": "docs"}}}))
        self.assertEqual(settings["profile"], "code")
        self.assertIn("docs", notes[0])

    def test_git_block_that_is_not_an_object_and_broken_manifests_never_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings, notes = git_flow.load_git_settings(
                self._node(tmp, {"settings": {"git": "on"}}))
            self.assertEqual(settings["main"], "main")
            self.assertIn("settings.git", notes[0])
        for raw in ("{not json", "[]", "null", "\ufeff{"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                settings, notes = git_flow.load_git_settings(self._node(tmp, raw=raw))
                self.assertEqual(settings["protected"], ["main", "master"])
                self.assertEqual(notes, [])

    def test_manifest_with_a_utf8_bom_is_read(self):
        body = json.dumps({"settings": {"git": {"main": "trunk"}}}).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            node = pathlib.Path(tmp)
            (node / "siteoffice.json").write_bytes(b"\xef\xbb\xbf" + body)
            settings, notes = git_flow.load_git_settings(node)
        self.assertEqual(settings["main"], "trunk")
        self.assertEqual(notes, [])

    def test_deeply_nested_manifest_falls_back_to_the_defaults_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = pathlib.Path(tmp)
            (node / "siteoffice.json").write_bytes(b"[" * 100000)
            settings, notes = git_flow.load_git_settings(node)
        self.assertEqual(settings["main"], "main")
        self.assertEqual(notes, [])


class TestGitSettingsParentWalk(unittest.TestCase):
    def _tree(self, tmp, root_git, child_git, child_extra=None):
        root = pathlib.Path(tmp) / "root"
        child = root / "child"
        child.mkdir(parents=True)
        (root / "siteoffice.json").write_text(
            json.dumps({"settings": {"git": root_git}}), encoding="utf-8")
        manifest = {"parent": "..", "settings": {"git": child_git}}
        manifest.update(child_extra or {})
        (child / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
        return child

    def test_root_keys_are_inherited_and_own_keys_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = self._tree(
                tmp, {"main": "trunk", "checks": ["root-check"], "pr": {"approvals": 1}},
                {"checks": ["own-check"]})
            settings, notes = git_flow.load_git_settings(child)
        self.assertEqual(settings["main"], "trunk")
        self.assertEqual(settings["checks"], ["own-check"])
        self.assertEqual(settings["pr"], {"approvals": 1})
        self.assertEqual(len(notes), 1)  # the root's checks come from outside the repo: ignored
        self.assertIn("inherited settings.git.checks were ignored", notes[0])

    def test_pr_block_merges_key_by_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = self._tree(tmp, {"pr": {"approvals": 3}}, {"pr": {}})
            settings, _notes = git_flow.load_git_settings(child)
        self.assertEqual(settings["pr"], {"approvals": 3})

    def test_a_node_without_a_parent_key_ignores_ancestors(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = self._tree(tmp, {"main": "trunk"}, {})
            manifest = json.loads((child / "siteoffice.json").read_text(encoding="utf-8"))
            del manifest["parent"]
            (child / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
            settings, _notes = git_flow.load_git_settings(child)
        self.assertEqual(settings["main"], "main")

    def test_a_manifest_that_is_a_json_list_never_crashes_the_walk(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = self._tree(tmp, {"main": "trunk"}, {})
            (child / "siteoffice.json").write_text("[]", encoding="utf-8")
            settings, notes = git_flow.load_git_settings(child)
        self.assertEqual(settings["main"], "main")
        self.assertEqual(notes, [])

    def test_a_relative_node_dir_still_inherits_from_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = self._tree(tmp, {"main": "trunk"}, {})
            old_cwd = os.getcwd()
            os.chdir(str(child))
            try:
                settings, notes = git_flow.load_git_settings(".")
            finally:
                os.chdir(old_cwd)
        self.assertEqual(settings["main"], "trunk")
        self.assertEqual(notes, [])


class TestGitFlowNote(unittest.TestCase):
    def test_schema_note_names_every_settings_git_key(self):
        note = (SYNC_DIR / "GIT_FLOW.md").read_text(encoding="utf-8")
        for key in ("profile", "main", "protected", "checks", "checkTimeoutSeconds",
                    "denylist", "pr.approvals"):
            self.assertIn(key, note)


# Planted secrets are assembled at run time so this file never holds a literal
# key-shaped string (secret scanners, and this repo's own scan, stay quiet).
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
PASSWORD_LINE = 'password = "' + "hunter2hunter2" + '"'
SK_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"
GH_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"


def _write(clone, rel, text):
    path = clone / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestContentHits(unittest.TestCase):
    def test_each_planted_secret_shape_is_found_in_an_untracked_file(self):
        planted = {
            "aws-access-key": f"key id {AWS_KEY} here\n",
            "pem-private-key": PEM_HEADER + "\nMIIB\n",
            "secret-assignment": PASSWORD_LINE + "\n",
            "sk-key": f"OPENAI={SK_KEY}\n",
            "github-token": f"tok {GH_TOKEN}\n",
            "slack-token": f"hook {SLACK_TOKEN}\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for rule, text in planted.items():
                with self.subTest(rule=rule):
                    _write(clone, "notes/planted.txt", text)
                    self.assertEqual(git_flow.content_hits(clone, "notes/planted.txt"), [rule])

    def test_look_alikes_pass(self):
        harmless = [
            'password = "short"\n',
            "# rotate the api key monthly, see the runbook\n",
            "the token is stored elsewhere\n",
            "sk-short\n",
            "AKIA1234\n",
            'api_key = ""\n',
            "ghp_tooshort\n",
            "xoxb-\n",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for text in harmless:
                with self.subTest(text=text):
                    _write(clone, "notes/harmless.txt", text)
                    self.assertEqual(git_flow.content_hits(clone, "notes/harmless.txt"), [])

    def test_a_quoted_json_or_yaml_style_assignment_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "c.json", '{"api_key": "' + "x" * 16 + '"}\n')
            self.assertEqual(git_flow.content_hits(clone, "c.json"), ["secret-assignment"])

    def test_only_added_lines_of_a_tracked_file_are_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "old.txt", f"line one\nkey {AWS_KEY}\n")
            _run(clone, "add", "old.txt")
            _run(clone, "commit", "-m", "an old commit that already holds a key")
            # adding a harmless line leaves the committed key alone: not a hit
            _write(clone, "old.txt", f"line one\nkey {AWS_KEY}\nharmless\n")
            self.assertEqual(git_flow.content_hits(clone, "old.txt"), [])
            # removing the key is not a hit either
            _write(clone, "old.txt", "line one\n")
            self.assertEqual(git_flow.content_hits(clone, "old.txt"), [])
            # adding a key line is
            _write(clone, "old.txt", f"line one\nnew {AWS_KEY}\n")
            self.assertEqual(git_flow.content_hits(clone, "old.txt"), ["aws-access-key"])

    def test_binary_and_non_utf8_files_never_crash_the_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "blob.bin").write_bytes(bytes(range(256)) * 4)
            (clone / "latin1.txt").write_bytes("caf\xe9 key\n".encode("latin-1"))
            self.assertEqual(git_flow.content_hits(clone, "blob.bin"), [])
            self.assertEqual(git_flow.content_hits(clone, "latin1.txt"), [])

    def test_a_failed_diff_is_reported_as_scan_failed_never_as_clean(self):
        real = subprocess.run

        def nonzero(cmd, *args, **kwargs):
            if "diff" in cmd:
                return subprocess.CompletedProcess(cmd, 124, b"", b"timed out")
            return real(cmd, *args, **kwargs)

        def timed_out(cmd, *args, **kwargs):
            if "diff" in cmd:
                raise subprocess.TimeoutExpired(cmd, 1)
            return real(cmd, *args, **kwargs)

        def no_output(cmd, *args, **kwargs):
            if "diff" in cmd:
                return subprocess.CompletedProcess(cmd, 0, None, b"")
            return real(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "hello.md", "one\n")
            _run(clone, "add", "hello.md")
            _run(clone, "commit", "-m", "track hello")
            _write(clone, "hello.md", "changed\n")
            for name, fake in (("nonzero", nonzero), ("timeout", timed_out), ("no-output", no_output)):
                with self.subTest(failure=name):
                    with mock.patch.object(subprocess, "run", side_effect=fake):
                        self.assertEqual(git_flow.content_hits(clone, "hello.md"), ["scan-failed"])

    def test_a_tracked_modified_non_utf8_file_never_crashes_and_still_finds_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "note.txt", "one\n")
            _run(clone, "add", "note.txt")
            _run(clone, "commit", "-m", "track note")
            # an added Latin-1 line alone: scanned, clean, no exception
            (clone / "note.txt").write_bytes(b"one\ncaf\xe9 note\n")
            self.assertEqual(git_flow.content_hits(clone, "note.txt"), [])
            # the same file with an added ASCII secret line: still detected
            (clone / "note.txt").write_bytes(
                b"one\ncaf\xe9 note\n" + ("id " + AWS_KEY + "\n").encode("ascii"))
            self.assertEqual(git_flow.content_hits(clone, "note.txt"), ["aws-access-key"])

    def test_a_quoted_value_holding_the_other_quote_or_an_escaped_quote_is_found(self):
        tail = "ss" + "12345678901234"
        values = {
            "single quote inside double": 'password = "pa' + "'" + tail + '"',
            "escaped double quote": 'password = "pa' + "\\" + '"' + tail + '"',
            "double quote inside single": "key='a" + '"' + "bcdefghijklmnop'",
            "escaped single quote": "token = 'pa" + "\\" + "'" + tail + "'",
        }
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for name, line in values.items():
                with self.subTest(case=name):
                    _write(clone, "q.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "q.txt"), ["secret-assignment"])
                    self.assertEqual(git_flow.redact(line), "[redacted]")

    def test_redact_leaves_no_part_of_a_quote_bearing_value_in_running_text(self):
        line = 'password = "pa' + "'" + "ss" + '12345678901234"'
        self.assertEqual(git_flow.redact("x " + line + " y"), "x [redacted] y")

    def test_short_quote_bearing_values_and_comment_look_alikes_pass(self):
        harmless = [
            'password = "it' + "'" + 's"\n',
            "key = 'say " + '"hi"' + "'\n",
            'password = "a' + "\\" + '"b"\n',
            "# the key is 'stored in the vault', rotate monthly\n",
            "# password 'pa" + '"' + "ss' is never stored\n",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for text in harmless:
                with self.subTest(text=text):
                    _write(clone, "h.txt", text)
                    self.assertEqual(git_flow.content_hits(clone, "h.txt"), [])

    def test_a_very_long_line_scans_in_linear_time(self):
        # Ruling R3: the assignment pattern once backtracked quadratically.
        shapes = [
            "a" * 100000, "key" * 33333, "token_" * 16666, "k" * 50000 + "ey=" + "b" * 50000,
            'key="' + "'" * 100000, "key='" + '"' * 100000,
            'key="' + "\\" * 100000, 'key="' + '\\"' * 50000, 'key="' * 20000,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in shapes:
                with self.subTest(head=line[:8]):
                    _write(clone, "long.txt", line + "\n")
                    started = time.monotonic()
                    hits = git_flow.content_hits(clone, "long.txt")
                    cleaned = git_flow.redact(line)
                    self.assertLess(time.monotonic() - started, 2.0)
                    self.assertEqual(hits, [])
                    self.assertEqual(cleaned, line)


class TestRedact(unittest.TestCase):
    def test_every_planted_secret_is_replaced_and_other_text_survives(self):
        text = "\n".join([
            f"a {AWS_KEY} b", PEM_HEADER, PASSWORD_LINE, SK_KEY, GH_TOKEN, SLACK_TOKEN, "plain text",
        ])
        cleaned = git_flow.redact(text)
        for secret in (AWS_KEY, "hunter2hunter2", SK_KEY, GH_TOKEN, SLACK_TOKEN, "RSA PRIVATE KEY"):
            self.assertNotIn(secret, cleaned)
        self.assertIn("[redacted]", cleaned)
        self.assertIn("plain text", cleaned)


class TestPathRulesAndDenylist(unittest.TestCase):
    def test_builtin_path_rules(self):
        for path, rule in [(".env", ".env"), ("app/.env", ".env"), ("keys/server.pem", "*.pem"),
                           ("id_rsa", "id_rsa*"), ("home/id_rsa.pub", "id_rsa*"),
                           ("APP/.ENV", ".env")]:
            with self.subTest(path=path):
                self.assertEqual(git_flow.path_rule(path), rule)
        for path in ("notes/a.md", "env.md", "pem.txt", "my.pem.md"):
            with self.subTest(path=path):
                self.assertIsNone(git_flow.path_rule(path))

    def test_denylist_globs_match_full_paths_and_bare_names(self):
        globs = ["secrets/*", "*.key", "private/notes.md"]
        self.assertEqual(git_flow.denylist_match("secrets/a/b.txt", globs), "secrets/*")
        self.assertEqual(git_flow.denylist_match("deep/dir/server.key", globs), "*.key")
        self.assertEqual(git_flow.denylist_match("private/notes.md", globs), "private/notes.md")
        self.assertIsNone(git_flow.denylist_match("public/notes.md", globs))
        self.assertEqual(git_flow.path_rule("secrets/x.txt", globs), "denylist:secrets/*")

    def test_missing_denylist_file_means_empty_and_comments_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            self.assertEqual(git_flow.load_denylist(repo, ".git-denylist"), [])
            (repo / ".git-denylist").write_text(
                "# private things\n\n  secrets/*  \n*.key\n", encoding="utf-8")
            self.assertEqual(git_flow.load_denylist(repo, ".git-denylist"), ["secrets/*", "*.key"])
            (repo / "bad.txt").write_bytes(b"\xff\xfe\x00")
            with self.assertRaises(git_flow.DenylistError) as ctx:
                git_flow.load_denylist(repo, "bad.txt")
            self.assertIn("bad.txt", str(ctx.exception))

    def test_unreadable_denylist_raises_and_never_echoes_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "adir").mkdir()
            with self.assertRaises(git_flow.DenylistError):
                git_flow.load_denylist(repo, "adir")
            (repo / "wide.txt").write_bytes("secrets/*\n".encode("utf-16"))
            with self.assertRaises(git_flow.DenylistError) as ctx:
                git_flow.load_denylist(repo, "wide.txt")
            self.assertIn("wide.txt", str(ctx.exception))
            self.assertNotIn("secrets", str(ctx.exception))
            self.assertTrue(issubclass(git_flow.DenylistError, ValueError))

    def test_denylist_bom_and_crlf_are_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            bom = b"\xef\xbb\xbf"
            (repo / "bom.txt").write_bytes(bom + b"secrets/*\r\n*.key\r\n")
            globs = git_flow.load_denylist(repo, "bom.txt")
            self.assertEqual(globs, ["secrets/*", "*.key"])
            self.assertEqual(git_flow.denylist_match("secrets/a.txt", globs), "secrets/*")
            (repo / "crlf.txt").write_bytes(b"# c\r\nvault/*\r\n\r\n*.key\r\n")
            self.assertEqual(git_flow.load_denylist(repo, "crlf.txt"), ["vault/*", "*.key"])

    def test_directory_style_and_dot_slash_entries(self):
        self.assertEqual(git_flow.denylist_match("secrets/a.txt", ["secrets/"]), "secrets/")
        self.assertEqual(git_flow.denylist_match("secrets/sub/b.txt", ["secrets/"]), "secrets/")
        self.assertIsNone(git_flow.denylist_match("secretsx/a.txt", ["secrets/"]))
        self.assertIsNone(git_flow.denylist_match("other/a.txt", ["secrets/"]))
        self.assertEqual(git_flow.denylist_match("secrets/a.txt", ["./secrets/*"]), "./secrets/*")
        self.assertEqual(git_flow.denylist_match("./secrets/a.txt", ["secrets/*"]), "secrets/*")
        self.assertEqual(git_flow.denylist_match("./secrets/a.txt", ["./secrets/"]), "./secrets/")
        self.assertEqual(git_flow.path_rule("./secrets/a.txt", ["secrets/"]), "denylist:secrets/")

    def test_scan_added_lines_returns_path_and_rule_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, ".env", "A=1\n")
            _write(clone, "notes/leak.txt", f"x {AWS_KEY}\n")
            _write(clone, "notes/fine.md", "hello\n")
            _write(clone, "vault/x.txt", "plain\n")
            hits = git_flow.scan_added_lines(
                clone, [".env", "notes/leak.txt", "notes/fine.md", "vault/x.txt"], ["vault/*"])
            self.assertEqual(hits, [
                (".env", ".env"), ("notes/leak.txt", "aws-access-key"), ("vault/x.txt", "denylist:vault/*"),
            ])
            for _path, rule in hits:
                self.assertNotIn(AWS_KEY, rule)


class TestProtectedGuardAndNames(unittest.TestCase):
    def test_protected_branch_is_refused_with_the_exact_sentence(self):
        with self.assertRaises(git_flow.Refusal) as caught:
            git_flow.assert_not_protected("main", ["main", "master"])
        self.assertEqual(
            caught.exception.sentence,
            "`main` is protected; run `start <slug>` to work on a branch of your own.")
        with self.assertRaises(git_flow.Refusal):
            git_flow.assert_not_protected("master", ["main", "master"])

    def test_an_unprotected_branch_and_a_custom_list(self):
        git_flow.assert_not_protected("work/wilco/fix-1", ["main", "master"])
        git_flow.assert_not_protected("main", ["release"])
        with self.assertRaises(git_flow.Refusal) as caught:
            git_flow.assert_not_protected("release", ["release"])
        self.assertTrue(caught.exception.sentence.startswith("`release` is protected;"))

    def test_slug_rule(self):
        for good in ("a", "fix-1", "0abc", "a" * 41):
            with self.subTest(good=good):
                self.assertTrue(git_flow.valid_slug(good))
        for bad in ("", "-x", "Fix", "a b", "a_b", "a" * 42, "a\n", "../x", "a/b", None, 5):
            with self.subTest(bad=bad):
                self.assertFalse(git_flow.valid_slug(bad))

    def test_owner_slug_and_branch_name(self):
        self.assertEqual(git_flow.owner_slug("Wilco de Tree"), "wilco-de-tree")
        self.assertEqual(git_flow.owner_slug("A_b.c"), "a-b-c")
        self.assertEqual(git_flow.owner_slug(""), "unknown")
        self.assertEqual(git_flow.branch_name("Wilco de Tree", "fix-1"), "work/wilco-de-tree/fix-1")

    def test_this_owner_prefers_owner_local_json_then_git_user_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            self.assertEqual(git_flow.this_owner(clone), "Test Clone")
            (clone / "sync").mkdir()
            (clone / "sync" / "owner.local.json").write_text(
                json.dumps({"owner": "clone-a"}), encoding="utf-8")
            self.assertEqual(git_flow.this_owner(clone), "clone-a")
            (clone / "sync" / "owner.local.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(git_flow.this_owner(clone), "Test Clone")


RECEIPT_FIELDS = {
    "receipt_id", "run_id", "ts", "profile", "step", "branch", "harness", "model", "sha_before",
    "sha_after", "duration_ms", "exit_code", "outcome", "refusal", "checks", "stderr_tail",
}


def _receipt_lines(repo):
    path = pathlib.Path(repo) / "_log" / "runs" / "git-flow.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _inner(outcome, **extra):
    def inner(settings):
        return git_flow.make_result("probe", outcome, "would probe", "why" if outcome != "pass" else "", **extra)
    return inner


class TestStepFramework(unittest.TestCase):
    def test_a_preview_returns_no_receipt_and_writes_no_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            result = git_flow._run_step("probe", clone, False, None, _inner("pass"))
            self.assertEqual(result["outcome"], "pass")
            self.assertIsNone(result["receipt"])
            self.assertFalse(result["receipt_written"])
            self.assertEqual(_receipt_lines(clone), [])
            self.assertEqual(set(result), {"step", "outcome", "preview", "refusal", "amber",
                                           "receipt", "receipt_written"})

    def test_apply_writes_one_line_with_every_field_and_the_exit_code_of_the_outcome(self):
        for outcome, code in (("pass", 0), ("refused", 2), ("blocked", 3)):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _init_repo_pair(pathlib.Path(tmp))
                checks = [{"command": "c", "exit": 0, "seconds": 0.1}]
                result = git_flow._run_step("probe", clone, True, None, _inner(outcome, checks=checks))
                lines = _receipt_lines(clone)
                self.assertEqual(len(lines), 1)
                self.assertEqual(set(lines[0]), RECEIPT_FIELDS)
                self.assertEqual(lines[0], result["receipt"])
                self.assertTrue(result["receipt_written"])
                self.assertEqual(lines[0]["exit_code"], code)
                self.assertEqual(lines[0]["outcome"], outcome)
                self.assertEqual(lines[0]["step"], "probe")
                self.assertEqual(lines[0]["profile"], "code")
                self.assertEqual(lines[0]["branch"], "main")
                self.assertEqual(lines[0]["checks"], checks)
                self.assertEqual(lines[0]["harness"], "unknown")
                self.assertEqual(lines[0]["model"], "unknown")
                self.assertEqual(len(lines[0]["receipt_id"]), 32)
                self.assertTrue(lines[0]["ts"].endswith("Z"))
                self.assertIsInstance(lines[0]["duration_ms"], int)

    def test_sha_before_and_after_match_without_a_commit_and_differ_with_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            git_flow._run_step("probe", clone, True, None, _inner("refused"))

            def committing(settings):
                (clone / "x.md").write_text("x\n", encoding="utf-8")
                _run(clone, "add", "x.md")
                _run(clone, "commit", "-m", "x")
                return git_flow.make_result("probe", "pass", "did")

            git_flow._run_step("probe", clone, True, None, committing)
            first, second = _receipt_lines(clone)
            self.assertEqual((first["sha_before"], first["sha_after"]), (head, head))
            self.assertEqual(second["sha_before"], head)
            self.assertNotEqual(second["sha_after"], head)
            self.assertEqual(second["sha_after"], _run(clone, "rev-parse", "HEAD").stdout.strip())

    def test_an_unwritable_log_path_does_not_fail_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "_log").write_text("a file where the log folder should be", encoding="utf-8")
            result = git_flow._run_step("probe", clone, True, None, _inner("pass"))
            self.assertEqual(result["outcome"], "pass")
            self.assertFalse(result["receipt_written"])
            self.assertEqual(set(result["receipt"]), RECEIPT_FIELDS)

    def test_a_directory_that_is_not_a_git_repo_is_refused_and_inner_never_runs(self):
        calls = []

        def inner(settings):
            calls.append(1)
            return git_flow.make_result("probe", "pass", "")

        with tempfile.TemporaryDirectory() as tmp:
            result = git_flow._run_step("probe", pathlib.Path(tmp), False, None, inner)
        self.assertEqual(result["outcome"], "refused")
        self.assertIn("not a git repository", result["refusal"])
        self.assertEqual(calls, [])

    def test_invalid_settings_become_an_amber_note_on_the_result_and_the_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "siteoffice.json").write_text(
                json.dumps({"settings": {"git": {"main": 5}}}), encoding="utf-8")
            result = git_flow._run_step("probe", clone, False, None, _inner("pass"))
            self.assertIn("settings.git.main", result["amber"])
            self.assertIn("amber: settings.git.main", result["preview"])
            self.assertEqual(result["outcome"], "pass")


class TestHarnessAndModel(unittest.TestCase):
    def _env(self, **values):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SITEOFFICE_HARNESS", "SITEOFFICE_MODEL")}
        env.update(values)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_arguments_win_then_environment_then_the_literal_unknown(self):
        with self._env(SITEOFFICE_HARNESS="env-harness", SITEOFFICE_MODEL="env-model"):
            explicit = git_flow.RunContext(harness="arg-harness", model="arg-model")
            fallback = git_flow.RunContext()
        self.assertEqual((explicit.harness, explicit.model), ("arg-harness", "arg-model"))
        self.assertEqual((fallback.harness, fallback.model), ("env-harness", "env-model"))
        with self._env():
            nothing = git_flow.RunContext()
        self.assertEqual((nothing.harness, nothing.model), ("unknown", "unknown"))

    def test_an_empty_environment_value_is_unknown_not_guessed(self):
        with self._env(SITEOFFICE_HARNESS="", SITEOFFICE_MODEL=""):
            ctx = git_flow.RunContext()
        self.assertEqual((ctx.harness, ctx.model), ("unknown", "unknown"))

    def test_the_receipt_carries_them_and_run_id_is_stable_per_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            ctx = git_flow.RunContext(harness="claude-code", model="test-model")
            git_flow._run_step("probe", clone, True, ctx, _inner("pass"))
            git_flow._run_step("probe", clone, True, ctx, _inner("pass"))
            first, second = _receipt_lines(clone)
            self.assertEqual((first["harness"], first["model"]), ("claude-code", "test-model"))
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(first["run_id"], ctx.run_id)
            self.assertNotEqual(first["receipt_id"], second["receipt_id"])
            self.assertNotEqual(git_flow.RunContext().run_id, ctx.run_id)


class TestReceiptRedaction(unittest.TestCase):
    def _receipt(self, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))

            def inner(settings):
                return git_flow.make_result(
                    "probe", "blocked", "p", extra.pop("refusal", "why"), **extra)

            result = git_flow._run_step("probe", clone, True, None, inner)
            raw_log = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
            return result, _receipt_lines(clone)[0], raw_log

    def test_a_planted_key_in_stderr_is_redacted_in_stderr_tail(self):
        stderr = f"boom\ntoken leaked: {AWS_KEY}\n{PASSWORD_LINE}\n"
        result, receipt, raw_log = self._receipt(stderr=stderr)
        self.assertIn("[redacted]", receipt["stderr_tail"])
        self.assertIn("boom", receipt["stderr_tail"])
        for secret in (AWS_KEY, "hunter2hunter2"):
            self.assertNotIn(secret, raw_log)
            self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("stderr", result)

    def test_stderr_tail_keeps_the_last_20_lines(self):
        stderr = "\n".join(f"line {n}" for n in range(1, 31))
        _result, receipt, _raw = self._receipt(stderr=stderr)
        lines = receipt["stderr_tail"].splitlines()
        self.assertEqual(lines[0], "line 11")
        self.assertEqual(lines[-1], "line 30")
        self.assertEqual(len(lines), 20)

    def test_stderr_tail_is_capped_to_the_last_2000_characters(self):
        _result, receipt, _raw = self._receipt(stderr="a" * 3000 + "b" * 1500)
        self.assertEqual(len(receipt["stderr_tail"]), 2000)
        self.assertTrue(receipt["stderr_tail"].endswith("b" * 1500))

    def test_a_secret_split_by_the_cap_is_still_redacted(self):
        # redaction runs before the cut, so a key straddling the 2000-character
        # boundary cannot survive as an unrecognisable fragment of itself
        # the key starts at index 10 and ends at index 30 of a 2020-character
        # text, so the cut (last 2000 characters) clips its first 10 characters:
        # a cut-then-redact order would leave the tail of the key behind
        stderr = "x" * 10 + AWS_KEY + "x" * 1990
        _result, receipt, _raw = self._receipt(stderr=stderr)
        self.assertNotIn("AKIA", receipt["stderr_tail"])
        self.assertNotIn("EXAMPLE", receipt["stderr_tail"])
        self.assertNotIn(AWS_KEY[10:], receipt["stderr_tail"])
        self.assertEqual(len(receipt["stderr_tail"]), 2000)

    def test_a_secret_on_the_line_the_20_line_cut_drops_never_reaches_the_tail(self):
        # line 10 of 30 is the last line the cut drops, line 11 the first it
        # keeps. No rule matches across a line break, so no secret can straddle
        # the line cut; this locks in that both neighbours come out clean.
        lines = [f"line {n}" for n in range(1, 31)]
        lines[9] = f"line 10 {AWS_KEY}"
        lines[10] = f"line 11 {AWS_KEY}"
        _result, receipt, raw_log = self._receipt(stderr="\n".join(lines))
        tail = receipt["stderr_tail"].splitlines()
        self.assertEqual(len(tail), 20)
        self.assertEqual(tail[0], "line 11 [redacted]")
        self.assertNotIn("line 10", receipt["stderr_tail"])
        self.assertNotIn("AKIA", raw_log)
        self.assertNotIn("EXAMPLE", raw_log)

    def test_a_refusal_carrying_a_secret_is_redacted_in_result_and_receipt(self):
        result, receipt, raw_log = self._receipt(refusal=f"git said: {AWS_KEY}")
        self.assertNotIn(AWS_KEY, result["refusal"])
        self.assertNotIn(AWS_KEY, receipt["refusal"])
        self.assertNotIn(AWS_KEY, raw_log)
        self.assertIn("[redacted]", receipt["refusal"])

    def test_a_planted_secret_in_a_checks_command_is_redacted_on_disk(self):
        checks = [{"command": f"deploy --token {AWS_KEY}", "exit": 1, "seconds": 0.5}]
        _result, receipt, raw_log = self._receipt(checks=checks)
        self.assertNotIn(AWS_KEY, raw_log)
        self.assertNotIn("AKIA", raw_log)
        self.assertIn("[redacted]", receipt["checks"][0]["command"])
        self.assertIn("deploy --token", receipt["checks"][0]["command"])
        self.assertEqual(receipt["checks"][0]["exit"], 1)
        self.assertEqual(receipt["checks"][0]["seconds"], 0.5)

    def test_a_none_refusal_still_writes_a_receipt(self):
        result, receipt, _raw = self._receipt(refusal=None)
        self.assertEqual(receipt["refusal"], "")
        self.assertEqual(result["refusal"], "")
        self.assertTrue(result["receipt_written"])

    def test_a_missing_refusal_key_still_writes_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))

            def inner(settings):
                result = git_flow.make_result("probe", "pass", "p")
                del result["refusal"]
                return result

            result = git_flow._run_step("probe", clone, True, None, inner)
            self.assertTrue(result["receipt_written"])
            self.assertEqual(_receipt_lines(clone)[0]["refusal"], "")


def _make_flow_repo(tmp, git=None, extra=None):
    """A throwaway clone (plus bare remote) whose siteoffice.json carries the
    given settings.git block; the manifest and a .gitignore for the receipt log
    are committed and pushed, so the tree starts clean like a real clone."""
    clone, remote = _init_repo_pair(pathlib.Path(tmp))
    manifest = {"schemaVersion": 2, "type": "project", "name": "t"}
    if git is not None:
        manifest["settings"] = {"git": git}
    manifest.update(extra or {})
    (clone / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Bytes, not write_text: write_text writes CRLF on Windows, and git then sees the file
    # as changed once a test switches the system autocrlf off.
    (clone / ".gitignore").write_bytes(b"_log/runs/git-flow.ndjson\n")
    _run(clone, "add", "siteoffice.json", ".gitignore")
    _run(clone, "commit", "-m", "add manifest")
    _run(clone, "push", "origin", "main")
    return clone, remote


def _branches(clone):
    return _run(clone, "branch", "--format=%(refname:short)").stdout.split()


class TestStartHappyPath(unittest.TestCase):
    def test_a_preview_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.start(clone, "fix-1")
            self.assertEqual(result["outcome"], "pass")
            self.assertEqual(result["branch"], "work/test-clone/fix-1")
            self.assertIn("work/test-clone/fix-1", result["preview"])
            self.assertEqual(_branches(clone), ["main"])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_receipt_lines(clone), [])

    def test_apply_checks_out_main_pulls_and_creates_the_owner_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, remote = _make_flow_repo(tmp)
            other = _second_clone(pathlib.Path(tmp), remote, "other")
            (other / "from_other.md").write_text("hi\n", encoding="utf-8")
            _run(other, "add", "from_other.md")
            _run(other, "commit", "-m", "other work")
            _run(other, "push", "origin", "main")
            _run(clone, "checkout", "-b", "scratch")  # start must go back to main first
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                             "work/test-clone/fix-1")
            self.assertTrue((clone / "from_other.md").exists())  # the pull happened
            lines = _receipt_lines(clone)
            self.assertEqual([(l["step"], l["outcome"], l["exit_code"]) for l in lines],
                             [("start", "pass", 0)])
            self.assertEqual(lines[0]["branch"], "work/test-clone/fix-1")

    def test_the_owner_part_is_lower_cased_and_sanitised(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "config", "user.name", "Wilco De_Tree")
            result = git_flow.start(clone, "x1", apply=True)
            self.assertEqual(result["branch"], "work/wilco-de-tree/x1")

    def test_a_branch_git_cannot_create_is_blocked_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            # a ref named "work" makes "work/<owner>/<slug>" impossible to create
            (clone / ".git" / "refs" / "heads" / "work").write_text(head + "\n", encoding="ascii")
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("work/test-clone/fix-1", result["refusal"])
            self.assertEqual(_receipt_lines(clone)[0]["exit_code"], 3)

    def test_an_invalid_slug_is_refused_before_any_git_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            for bad in ("Bad Slug", "../x", "", "-lead", "a" * 42):
                result = git_flow.start(clone, bad, apply=True)
                self.assertEqual(result["outcome"], "refused", bad)
                self.assertIn("slug", result["refusal"])
            self.assertEqual(_branches(clone), ["main"])
            self.assertEqual([l["exit_code"] for l in _receipt_lines(clone)], [2] * 5)
            self.assertEqual(git_flow.start(clone, "Bad Slug")["outcome"], "refused")

    def test_a_main_git_cannot_check_out_is_refused_and_creates_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"main": "no-such-main"})
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("no-such-main", result["refusal"])
            self.assertEqual(_branches(clone), ["main"])


class TestStartWhenMainIsHeldByAnotherWorktree(unittest.TestCase):
    """The layout of 2026-10-07: main lives only in a deploy worktree, the main
    checkout is detached, so `git checkout main` is impossible. start then fetches
    and branches from origin/main without ever checking main out."""

    def _held(self, tmp):
        clone, remote = _make_flow_repo(tmp)
        _run(clone, "checkout", "--detach")
        deploy = pathlib.Path(tmp) / "deploy-main"
        _run(clone, "worktree", "add", str(deploy), "main")
        return clone, remote, deploy

    def _branch_exists(self, clone, branch):
        return _run(clone, "branch", "--list", branch).stdout.strip() != ""

    def test_a_preview_names_origin_main_and_the_holding_worktree_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote, _deploy = self._held(tmp)
            head = _head(clone)
            result = git_flow.start(clone, "fix-1")
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertIn("origin/main", result["preview"])
            self.assertIn("deploy-main", result["preview"])
            self.assertEqual(_head(clone), head)
            self.assertFalse(self._branch_exists(clone, "work/test-clone/fix-1"))
            self.assertEqual(_receipt_lines(clone), [])

    def test_apply_branches_from_the_fetched_origin_main_and_leaves_the_deploy_worktree_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, remote, deploy = self._held(tmp)
            other = _second_clone(pathlib.Path(tmp), remote, "other")
            (other / "from_other.md").write_text("hi\n", encoding="utf-8")
            _run(other, "add", "from_other.md")
            _run(other, "commit", "-m", "other work")
            _run(other, "push", "origin", "main")
            deploy_head = _head(deploy)
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                             "work/test-clone/fix-1")
            self.assertEqual(_head(clone), _run(clone, "rev-parse", "origin/main").stdout.strip())
            self.assertTrue((clone / "from_other.md").exists())  # the fetch happened
            self.assertEqual(_head(deploy), deploy_head)  # main was never checked out or moved
            self.assertFalse((deploy / "from_other.md").exists())
            lines = _receipt_lines(clone)
            self.assertEqual([(l["step"], l["outcome"], l["exit_code"]) for l in lines],
                             [("start", "pass", 0)])
            self.assertEqual(lines[0]["branch"], "work/test-clone/fix-1")

    def test_a_failed_fetch_is_refused_and_creates_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote, _deploy = self._held(tmp)
            _run(clone, "remote", "set-url", "origin", str(pathlib.Path(tmp) / "no-such-remote.git"))
            head = _head(clone)
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("origin", result["refusal"])
            self.assertFalse(self._branch_exists(clone, "work/test-clone/fix-1"))
            self.assertEqual(_head(clone), head)


def _head(clone):
    return _run(clone, "rev-parse", "HEAD").stdout.strip()


def _status(clone):
    return _run(clone, "status", "--porcelain=v1", "-uall").stdout


class TestStartRefusals(unittest.TestCase):
    def test_a_bad_slug_is_refused_and_nothing_is_created(self):
        for slug in ("", "Fix", "-x", "a b", "a" * 42, "../x", None):
            with self.subTest(slug=slug), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                result = git_flow.start(clone, slug, apply=True)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn("not a valid slug", result["refusal"])
                self.assertEqual(_branches(clone), ["main"])
                self.assertEqual(_receipt_lines(clone)[0]["exit_code"], 2)

    def test_a_dirty_tree_is_refused_naming_the_first_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "wip.md").write_text("wip\n", encoding="utf-8")
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("`wip.md`", result["refusal"])
            self.assertEqual(_branches(clone), ["main"])

    def test_a_modified_tracked_file_also_counts_as_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "hello.md").write_text("changed\n", encoding="utf-8")
            self.assertEqual(git_flow.start(clone, "fix-1")["outcome"], "refused")

    def test_paths_under_log_are_ignored_for_the_dirty_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "_log").mkdir()
            (clone / "_log" / "sync-receipt-x.json").write_text("{}", encoding="utf-8")
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])

    def test_an_existing_branch_of_that_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "branch", "work/test-clone/fix-1")
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("`work/test-clone/fix-1` already exists", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")

    def test_a_refusal_in_preview_mode_is_the_same_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "wip.md").write_text("wip\n", encoding="utf-8")
            result = git_flow.start(clone, "fix-1")
            self.assertEqual(result["outcome"], "refused")
            self.assertEqual(_receipt_lines(clone), [])

    def test_a_dirty_start_from_a_side_branch_changes_nothing_in_either_mode(self):
        # The refusal must come before `git checkout main`: uncommitted work
        # must never be carried onto main, and HEAD must stay on the side branch.
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _run(clone, "checkout", "-b", "scratch")
                (clone / "hello.md").write_text("changed\n", encoding="utf-8")
                (clone / "wip.md").write_text("wip\n", encoding="utf-8")
                head, branches, status = _head(clone), _branches(clone), _status(clone)
                result = git_flow.start(clone, "fix-1", apply=apply)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn("2 uncommitted change(s)", result["refusal"])
                self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "scratch")
                self.assertEqual(_head(clone), head)
                self.assertEqual(_branches(clone), branches)
                self.assertEqual(_status(clone), status)
                self.assertEqual((clone / "hello.md").read_text(encoding="utf-8"), "changed\n")

    def test_an_existing_branch_is_refused_in_preview_and_leaves_the_repo_alone(self):
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _run(clone, "branch", "work/test-clone/fix-1")
                _run(clone, "checkout", "-b", "scratch")
                head, branches = _head(clone), _branches(clone)
                result = git_flow.start(clone, "fix-1", apply=apply)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn("already exists", result["refusal"])
                self.assertEqual(_run(clone, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "scratch")
                self.assertEqual(_head(clone), head)
                self.assertEqual(_branches(clone), branches)
                self.assertEqual(_status(clone), "")


class TestStartPullFailures(unittest.TestCase):
    def test_a_pull_conflict_is_refused_keeps_the_incoming_copy_and_makes_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone_a, remote = _init_repo_pair(pathlib.Path(tmp))
            clone_b = _second_clone(pathlib.Path(tmp), remote, "clone_b")
            (clone_a / "hello.md").write_text("hello from A\n", encoding="utf-8")
            _run(clone_a, "add", "hello.md")
            _run(clone_a, "commit", "-m", "A edits hello")
            _run(clone_a, "push", "origin", "main")
            (clone_b / "hello.md").write_text("hello from B\n", encoding="utf-8")
            _run(clone_b, "add", "hello.md")
            _run(clone_b, "commit", "-m", "B edits hello, conflicting")
            result = git_flow.start(clone_b, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("hello.md", result["refusal"])
            self.assertIn("No branch was made", result["refusal"])
            self.assertTrue((clone_b / "hello.incoming.md").exists())
            self.assertEqual(_branches(clone_b), ["main"])
            self.assertEqual(_receipt_lines(clone_b)[0]["outcome"], "refused")

    def test_a_pull_error_is_refused_and_makes_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "solo"
            repo.mkdir()
            _run(repo, "init", "-b", "main")
            _run(repo, "config", "user.email", "t@example.com")
            _run(repo, "config", "user.name", "Test Clone")
            (repo / "a.md").write_text("a\n", encoding="utf-8")
            _run(repo, "add", "a.md")
            _run(repo, "commit", "-m", "initial")
            result = git_flow.start(repo, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("no remote configured", result["refusal"])
            self.assertEqual(_branches(repo), ["main"])

    def test_a_main_branch_that_does_not_exist_is_refused_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"main": "trunk"})
            result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("`trunk`", result["refusal"])
            self.assertEqual(_branches(clone), ["main"])


def _staged(clone):
    return _run(clone, "diff", "--cached", "--name-only").stdout.split()


class TestSaveGuard(unittest.TestCase):
    def test_save_on_main_is_refused_with_the_sentence_and_nothing_is_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertEqual(
                result["refusal"],
                "`main` is protected; run `start <slug>` to work on a branch of your own.")
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            receipt = _receipt_lines(clone)[0]
            self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("refused", 2))
            self.assertEqual(receipt["sha_before"], receipt["sha_after"])

    def test_save_on_master_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "checkout", "-b", "master")
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(clone)
            self.assertEqual(result["outcome"], "refused")
            self.assertTrue(result["refusal"].startswith("`master` is protected;"))

    def test_a_custom_protected_list_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"protected": ["release"]})
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            on_main = git_flow.save(clone)  # main is protected whatever the list says
            self.assertEqual(on_main["outcome"], "refused")
            self.assertTrue(on_main["refusal"].startswith("`main` is protected;"))
            _run(clone, "checkout", "-b", "release")
            on_release = git_flow.save(clone)
            self.assertEqual(on_release["outcome"], "refused")
            self.assertTrue(on_release["refusal"].startswith("`release` is protected;"))

    def test_a_detached_head_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "checkout", "--detach")
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("detached", result["refusal"])
            self.assertIn("start <slug>", result["refusal"])
            self.assertEqual(_staged(clone), [])


def _cp(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestCurrentBranchState(unittest.TestCase):
    def test_a_branch_a_detached_head_and_an_unborn_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            self.assertEqual(git_flow._current_branch_state(clone), ("branch", "main"))
            _run(clone, "checkout", "--detach")
            self.assertEqual(git_flow._current_branch_state(clone), ("detached", ""))
            unborn = pathlib.Path(tmp) / "unborn"
            unborn.mkdir()
            _run(unborn, "init", "-b", "main")
            self.assertEqual(git_flow._current_branch_state(unborn), ("branch", "main"))

    def test_a_same_named_tag_does_not_change_the_branch_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "tag", "main")
            self.assertEqual(git_flow._current_branch_state(clone), ("branch", "main"))

    def test_a_failed_timed_out_or_odd_git_answer_is_unknown(self):
        for answer in (_cp(128, "", "fatal"), _cp(124, "", "timed out"), _cp(2, "main\n"),
                       _cp(0, ""), _cp(0, "\n"), _cp(0, "refs/tags/x\n"), _cp(0, "refs/heads/\n")):
            with mock.patch.object(git_ops, "run_git", return_value=answer):
                self.assertEqual(git_flow._current_branch_state("."), ("unknown", ""), answer)

    def test_only_a_quiet_return_code_one_is_detached(self):
        with mock.patch.object(git_ops, "run_git", return_value=_cp(1)):
            self.assertEqual(git_flow._current_branch_state("."), ("detached", ""))


class TestSaveGuardFailsClosed(unittest.TestCase):
    def _dirty(self, clone):
        (clone / "notes").mkdir()
        (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")

    def test_a_tag_named_like_the_branch_cannot_bypass_the_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "tag", "main")
            self._dirty(clone)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertEqual(
                result["refusal"],
                "`main` is protected; run `start <slug>` to work on a branch of your own.")
            self.assertEqual(_receipt_lines(clone)[0]["branch"], "main")
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)

    def test_an_unknown_branch_is_refused_and_nothing_changes(self):
        for state in (("unknown", ""), ("branch", "")):
            with tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                self._dirty(clone)
                head = _run(clone, "rev-parse", "HEAD").stdout.strip()
                with mock.patch.object(git_flow, "_current_branch_state", return_value=state):
                    result = git_flow.save(clone, apply=True)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn("Cannot tell which branch you are on", result["refusal"])
                self.assertNotIn("detached", result["refusal"])
                self.assertEqual(_staged(clone), [])
                self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
                receipt = _receipt_lines(clone)[0]
                self.assertEqual((receipt["outcome"], receipt["exit_code"], receipt["branch"]),
                                 ("refused", 2, ""))

    def test_a_failing_or_empty_git_answer_refuses_through_the_real_helper(self):
        real = git_ops.run_git

        def fake(answer):
            def run(repo_dir, args, *rest, **kwargs):
                if args[:1] == ["symbolic-ref"]:
                    return answer
                return real(repo_dir, args, *rest, **kwargs)
            return run

        for answer in (_cp(128, "", "fatal: boom"), _cp(0, "")):
            with tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                self._dirty(clone)
                head = _run(clone, "rev-parse", "HEAD").stdout.strip()
                with mock.patch.object(git_ops, "run_git", side_effect=fake(answer)):
                    result = git_flow.save(clone, apply=True)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn("Cannot tell which branch you are on", result["refusal"])
                self.assertEqual(_staged(clone), [])
                self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)

    def test_an_unborn_protected_branch_is_refused_as_protected_not_detached(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "unborn"
            repo.mkdir()
            _run(repo, "init", "-b", "main")
            (repo / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(repo, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertEqual(
                result["refusal"],
                "`main` is protected; run `start <slug>` to work on a branch of your own.")
            self.assertNotIn("detached", result["refusal"])
            self.assertEqual(_receipt_lines(repo)[0]["branch"], "main")
            self.assertEqual(_staged(repo), [])

    def test_an_unborn_unprotected_branch_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "unborn"
            repo.mkdir()
            _run(repo, "init", "-b", "work-x")
            (repo / "notes").mkdir()
            (repo / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(repo)
            self.assertNotEqual(result["outcome"], "refused", result["refusal"])
            self.assertEqual(result["branch"], "work-x")

    def test_a_preview_on_main_refuses_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            self._dirty(clone)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            status = _run(clone, "status", "--porcelain").stdout
            result = git_flow.save(clone, apply=False)
            self.assertEqual(result["outcome"], "refused")
            self.assertTrue(result["refusal"].startswith("`main` is protected;"))
            self.assertFalse(result["receipt_written"])
            self.assertEqual(_receipt_lines(clone), [])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_run(clone, "status", "--porcelain").stdout, status)

    def test_save_on_an_unprotected_branch_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "checkout", "-b", "work/test/x")
            self._dirty(clone)
            result = git_flow.save(clone)
            self.assertNotEqual(result["outcome"], "refused")
            self.assertEqual(result["branch"], "work/test/x")


class TestMessageBuilder(unittest.TestCase):
    def test_type_inference(self):
        self.assertEqual(git_flow.infer_type(["notes/a.md", "README.MD"]), "docs")
        self.assertEqual(git_flow.infer_type(["sync/tests/test_x.py", "board/tests/t/y.json"]), "test")
        self.assertEqual(git_flow.infer_type(["sync/a.py", "notes/b.md"]), "chore")
        self.assertEqual(git_flow.infer_type(["tests/a.md"]), "docs")  # docs wins over test
        self.assertEqual(git_flow.infer_type(["sync/tests/a.py", "sync/b.py"]), "chore")

    def test_scope_inference(self):
        self.assertEqual(git_flow.infer_scope(["sync/a.py", "sync/tests/b.py"]), "sync")
        self.assertEqual(git_flow.infer_scope(["Board/a.py"]), "board")
        self.assertIsNone(git_flow.infer_scope(["sync/a.py", "board/b.py"]))
        self.assertIsNone(git_flow.infer_scope(["hello.md"]))  # a root file has no folder
        self.assertIsNone(git_flow.infer_scope(["sync/a.py", "b.py"]))

    def test_each_field_error_names_the_field(self):
        good = ("chore", "sync", "update 2 file(s)")
        self.assertIsNone(git_flow.validate_message_fields(*good))
        self.assertIn("type", git_flow.validate_message_fields("wip", "sync", "x"))
        self.assertIn("type", git_flow.validate_message_fields("", "sync", "x"))
        self.assertIn("scope", git_flow.validate_message_fields("chore", None, "x"))
        self.assertIn("scope", git_flow.validate_message_fields("chore", "Bad Scope", "x"))
        self.assertIn("subject", git_flow.validate_message_fields("chore", "sync", ""))
        self.assertIn("subject", git_flow.validate_message_fields("chore", "sync", "   "))
        self.assertIn("50", git_flow.validate_message_fields("chore", "sync", "x" * 51))
        self.assertIsNone(git_flow.validate_message_fields("chore", "sync", "x" * 50))
        self.assertIn("period", git_flow.validate_message_fields("chore", "sync", "add a thing."))
        self.assertIn("one line", git_flow.validate_message_fields("chore", "sync", "a\nb"))

    def test_build_message_shape_default_subject_and_diff_stat_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "hello.md").write_text("hello\nmore\n", encoding="utf-8")  # tracked, modified
            (clone / "fresh.md").write_text("new\n", encoding="utf-8")  # untracked
            message, error = git_flow.build_message(clone, ["hello.md", "fresh.md"], None, "notes", None)
            self.assertIsNone(error)
            header, blank, body = message.split("\n", 2)
            self.assertEqual(header, "docs(notes): update 2 file(s)")
            self.assertEqual(blank, "")
            self.assertIn("hello.md", body)
            self.assertIn("fresh.md | new file", body)

    def test_build_message_honours_explicit_fields_and_reports_the_bad_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "hello.md").write_text("x\n", encoding="utf-8")
            message, error = git_flow.build_message(clone, ["hello.md"], "fix", "core", "handle empty input")
            self.assertIsNone(error)
            self.assertTrue(message.startswith("fix(core): handle empty input\n\n"))
            for args, field in [(("wip", "core", "x"), "type"), (("fix", None, "x"), "scope"),
                                (("fix", "core", "x" * 51), "subject")]:
                with self.subTest(field=field):
                    message, error = git_flow.build_message(clone, ["hello.md"], *args)
                    self.assertIsNone(message)
                    self.assertIn(field, error)

    def test_a_scope_that_cannot_be_inferred_says_to_pass_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "hello.md").write_text("x\n", encoding="utf-8")
            message, error = git_flow.build_message(clone, ["hello.md"])
            self.assertIsNone(message)
            self.assertIn("--scope", error)

    def test_exact_error_sentences_for_type_scope_and_subject_limits(self):
        check = git_flow.validate_message_fields
        self.assertEqual(check("chore", "sync", "x" * 51), "Commit subject is 51 characters; the limit is 50.")
        self.assertEqual(check("chore", "sync", "add a thing."), "Commit subject must not end with a period.")
        self.assertEqual(check("chore", "sync", "a\r\nb"), "Commit subject must be one line.")
        self.assertEqual(check("chore", "sync", " "), "Commit subject is required.")
        self.assertTrue(check("wip", "sync", "x").startswith("Commit type `wip` is not valid; use one of: feat, fix,"))
        self.assertEqual(check("chore", "Sync", "x"),
                         "Commit scope `Sync` is not valid; use lower-case letters, digits, . _ / and - only.")

    def test_scope_with_newline_or_space_or_leading_symbol_is_refused(self):
        for bad in ("sync\n", "sy nc", "sync\nx", "-sync", ".sync", "sync()", "sync:"):
            with self.subTest(scope=bad):
                self.assertIn("scope", git_flow.validate_message_fields("chore", bad, "x"))
        for good in ("sync", "board.v2", "a/b", "my-app_2"):
            with self.subTest(scope=good):
                self.assertIsNone(git_flow.validate_message_fields("chore", good, "x"))

    def test_empty_path_list_defaults(self):
        self.assertEqual(git_flow.infer_type([]), "chore")
        self.assertIsNone(git_flow.infer_scope([]))
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            message, error = git_flow.build_message(clone, [], None, "sync", None)
            self.assertIsNone(error)
            self.assertEqual(message, "chore(sync): update 0 file(s)")
            self.assertEqual(git_flow.diff_stat_body(clone, []), "")

    def test_build_message_subject_boundary_and_stripping(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            message, error = git_flow.build_message(clone, [], "fix", "core", "x" * 50)
            self.assertIsNone(error)
            self.assertEqual(message, "fix(core): " + "x" * 50)
            message, error = git_flow.build_message(clone, [], "fix", "core", "x" * 51)
            self.assertIsNone(message)
            self.assertEqual(error, "Commit subject is 51 characters; the limit is 50.")
            message, error = git_flow.build_message(clone, [], "fix", "core", "  trim me \n")
            self.assertIsNone(error)
            self.assertEqual(message, "fix(core): trim me")
            message, error = git_flow.build_message(clone, [], "fix", "core", "ends with period.")
            self.assertIsNone(message)
            self.assertIn("period", error)

    def test_diff_stat_body_is_capped_at_twenty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            names = [f"n{i:02d}.md" for i in range(25)]
            for name in names:
                (clone / name).write_text("x\n", encoding="utf-8")
            body = git_flow.diff_stat_body(clone, names)
            self.assertEqual(len(body.splitlines()), 20)
            self.assertEqual(body.splitlines()[0], " n00.md | new file")


class TestDiffStatBodyFixRound1(unittest.TestCase):
    """Pathspec syntax in a filename is literal, and new-versus-tracked is
    decided by HEAD, never by the index."""

    def test_glob_characters_in_a_filename_are_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "a1.md").write_text("one\n", encoding="utf-8")
            _run(clone, "add", "a1.md")
            _run(clone, "commit", "-m", "add a1")
            (clone / "a1.md").write_text("one\ntwo\n", encoding="utf-8")  # tracked, modified
            (clone / "a[1].md").write_text("x\n", encoding="utf-8")  # untracked, glob-like name
            body = git_flow.diff_stat_body(clone, ["a[1].md"])
            self.assertEqual(body, " a[1].md | new file")
            body = git_flow.diff_stat_body(clone, ["a1.md", "a[1].md"])
            self.assertIn(" a[1].md | new file", body)
            self.assertIn("a1.md |", body)
            self.assertEqual(len(body.splitlines()), 3)  # a1.md stat, summary, new file

    def test_tracked_glob_named_file_reports_its_own_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "a1.md").write_text("one\n", encoding="utf-8")
            (clone / "a[1].md").write_text("x\n", encoding="utf-8")
            _run(clone, "add", "a1.md", "a[1].md")
            _run(clone, "commit", "-m", "add both")
            (clone / "a[1].md").write_text("x\ny\n", encoding="utf-8")
            (clone / "a1.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
            body = git_flow.diff_stat_body(clone, ["a[1].md"])
            self.assertIn("a[1].md | 1 +", body)
            self.assertNotIn("a1.md |", body)
            self.assertIn("1 file changed", body)

    def test_pathspec_magic_in_a_path_string_is_not_interpreted(self):
        # No file is created: these names are not legal on Windows, and the
        # point is only that git never reads them as pathspec magic.
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "hello.md").write_text("hello\nmore\n", encoding="utf-8")  # tracked, modified
            for weird in (":/", ":(top)hello.md", ":(exclude)c.md", ":(glob)*.md"):
                with self.subTest(path=weird):
                    body = git_flow.diff_stat_body(clone, [weird])
                    self.assertEqual(body, f" {weird} | new file")
                    self.assertEqual(len(body.splitlines()), 1)  # no stat of hello.md leaked in

    def test_staged_deletion_is_not_labelled_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "b.md").write_text("bee\n", encoding="utf-8")
            _run(clone, "add", "b.md")
            _run(clone, "commit", "-m", "add b")
            (clone / "b.md").unlink()
            unstaged = git_flow.diff_stat_body(clone, ["b.md"])
            self.assertIn("b.md | 1 -", unstaged)
            self.assertNotIn("new file", unstaged)
            _run(clone, "add", "-A")  # the deletion is now staged
            staged = git_flow.diff_stat_body(clone, ["b.md"])
            self.assertIn("b.md | 1 -", staged)
            self.assertNotIn("new file", staged)
            self.assertEqual(staged, unstaged)

    def test_staged_deletion_by_git_rm_is_not_labelled_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _run(clone, "rm", "-f", "hello.md")
            body = git_flow.diff_stat_body(clone, ["hello.md"])
            self.assertIn("hello.md | 1 -", body)
            self.assertNotIn("new file", body)

    def test_unborn_head_makes_every_path_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "fresh"
            repo.mkdir()
            _run(repo, "init", "-b", "main")
            (repo / "one.md").write_text("1\n", encoding="utf-8")
            (repo / "two.md").write_text("2\n", encoding="utf-8")
            _run(repo, "add", "two.md")  # staged in the index, still no HEAD
            body = git_flow.diff_stat_body(repo, ["one.md", "two.md"])
            self.assertEqual(body.splitlines(), [" one.md | new file", " two.md | new file"])

    def test_new_file_already_staged_is_still_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            (clone / "fresh.md").write_text("new\n", encoding="utf-8")
            _run(clone, "add", "fresh.md")
            body = git_flow.diff_stat_body(clone, ["fresh.md"])
            self.assertEqual(body, " fresh.md | new file")


def _on_branch(clone, slug="fix-1"):
    result = git_flow.start(clone, slug, apply=True)
    assert result["outcome"] == "pass", result["refusal"]
    return result["branch"]


class TestSaveCommits(unittest.TestCase):
    def test_a_clean_change_is_committed_as_a_conventional_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            (clone / "notes" / "b.md").write_text("b\n", encoding="utf-8")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            subject = _run(clone, "log", "-1", "--pretty=%s").stdout.strip()
            self.assertEqual(subject, "docs(notes): update 2 file(s)")
            self.assertRegex(subject, r"^[a-z]+\([a-z0-9._/-]+\): .{1,50}$")
            body = _run(clone, "log", "-1", "--pretty=%b").stdout
            self.assertIn("notes/a.md", body)
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(sorted(in_head), ["notes/a.md", "notes/b.md"])
            self.assertTrue(result["sha"])
            receipt = _receipt_lines(clone)[-1]
            self.assertEqual((receipt["step"], receipt["outcome"]), ("save", "pass"))
            self.assertNotEqual(receipt["sha_before"], receipt["sha_after"])

    def test_explicit_type_scope_and_subject_are_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(clone, "fix", "core", "handle empty input", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_run(clone, "log", "-1", "--pretty=%s").stdout.strip(),
                             "fix(core): handle empty input")

    def test_a_preview_commits_and_stages_nothing_and_shows_the_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone)
            self.assertEqual(result["outcome"], "pass")
            self.assertIn("docs(notes): update 1 file(s)", result["preview"])
            self.assertIn("notes/a.md", result["preview"])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(len(_receipt_lines(clone)), 1)  # only the start receipt

    def test_a_clean_tree_is_refused_as_nothing_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("Nothing to save", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            receipt = _receipt_lines(clone)[-1]
            self.assertEqual(receipt["sha_before"], receipt["sha_after"])

    def test_only_log_files_changed_is_also_nothing_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "_log").mkdir(exist_ok=True)
            (clone / "_log" / "sync-receipt-x.json").write_text("{}", encoding="utf-8")
            result = git_flow.save(clone)
            self.assertIn("Nothing to save", result["refusal"])

    def test_a_log_file_is_left_out_of_the_commit_and_stays_in_the_working_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            (clone / "_log").mkdir(exist_ok=True)
            (clone / "_log" / "sync-receipt-x.json").write_text("{}", encoding="utf-8")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(in_head, ["notes/a.md"])
            self.assertTrue((clone / "_log" / "sync-receipt-x.json").exists())
            self.assertIn("_log/sync-receipt-x.json", _run(clone, "status", "--porcelain", "-uall").stdout)

    def test_pending_sync_receipts_ride_along_with_a_save_that_has_other_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            (clone / "_log").mkdir(exist_ok=True)
            old = "_log/sync-receipt-2026-10-01T10-00-00Z.json"
            (clone / old).write_text("{}", encoding="utf-8")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(sorted(in_head), sorted(["notes/a.md", old]))
            body = _run(clone, "log", "-1", "--format=%B").stdout
            self.assertNotIn("sync-receipt", body)
            left = _run(clone, "status", "--porcelain", "-uall").stdout.splitlines()
            self.assertEqual(len(left), 1, left)
            self.assertRegex(left[0], r"^\?\? _log/sync-receipt-[0-9TZ-]+\.json$")
            self.assertNotIn(old, left[0])

    def test_a_real_shaped_sync_receipt_alone_is_still_nothing_to_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "_log").mkdir(exist_ok=True)
            (clone / "_log" / "sync-receipt-2026-10-01T10-00-00Z.json").write_text("{}", encoding="utf-8")
            result = git_flow.save(clone)
            self.assertIn("Nothing to save", result["refusal"])

    def test_a_bad_type_scope_or_subject_is_refused_naming_the_field_and_stages_nothing(self):
        cases = [
            (("wip", "core", "fine"), "type"),
            (("fix", "Bad Scope", "fine"), "scope"),
            (("fix", "core", "x" * 51), "subject"),
            (("fix", "core", "ends with a period."), "period"),
            (("fix", "core", ""), "subject"),
        ]
        for (ctype, scope, subject), word in cases:
            with self.subTest(word=word), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                (clone / "a.md").write_text("a\n", encoding="utf-8")
                head = _run(clone, "rev-parse", "HEAD").stdout.strip()
                result = git_flow.save(clone, ctype, scope, subject, apply=True)
                self.assertEqual(result["outcome"], "refused")
                self.assertIn(word, result["refusal"])
                self.assertEqual(_staged(clone), [])
                self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)

    def test_a_root_file_with_no_scope_is_refused_naming_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            result = git_flow.save(clone)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("--scope", result["refusal"])

    def test_a_deleted_tracked_file_and_a_non_ascii_name_are_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "hello.md").unlink()
            name = "notiti" + chr(0xEB) + "n.md"
            (clone / "docs").mkdir()
            (clone / "docs" / name).write_text("dutch note\n", encoding="utf-8")
            result = git_flow.save(clone, "docs", "notes", "prune and add", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            in_head = git_ops.run_git(clone, ["-c", "core.quotepath=false", "show", "--name-only",
                                              "--pretty=format:", "HEAD"]).stdout.split("\n")
            self.assertIn("hello.md", in_head)
            self.assertIn("docs/" + name, in_head)

    def _assert_failed_save_unstaged_only_its_paths(self, clone, result, head, kept):
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("git commit failed", result["refusal"])
        self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 3)
        if head:
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(_staged(clone), [kept] if kept else [])
        self.assertNotIn("could not unstage", result["refusal"])

    def _stage_a_path_save_ignores(self, clone):
        """A path the user staged that save never lists (under _log/)."""
        (clone / "_log").mkdir(exist_ok=True)
        (clone / "_log" / "keep.json").write_text("{}", encoding="utf-8")
        _run(clone, "add", "_log/keep.json")
        return "_log/keep.json"

    def test_a_failing_commit_is_blocked_with_the_git_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            hook = clone / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            kept = self._stage_a_path_save_ignores(clone)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone, apply=True)
            self._assert_failed_save_unstaged_only_its_paths(clone, result, head, kept)

    def test_a_failing_commit_with_no_git_identity_unstages_its_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            kept = self._stage_a_path_save_ignores(clone)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            nothing = pathlib.Path(tmp) / "no-such-gitconfig"
            isolated = {"GIT_CONFIG_GLOBAL": str(nothing), "GIT_CONFIG_SYSTEM": str(nothing)}
            with mock.patch.dict(os.environ, isolated):
                for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                             "GIT_COMMITTER_EMAIL", "EMAIL"):
                    os.environ.pop(name, None)
                _run(clone, "config", "--unset", "user.name")
                _run(clone, "config", "--unset", "user.email")
                _run(clone, "config", "user.useConfigOnly", "true")
                result = git_flow.save(clone, apply=True)
            self.assertIn("identity", result["refusal"].lower())
            self._assert_failed_save_unstaged_only_its_paths(clone, result, head, kept)

    def test_a_failing_commit_on_an_unborn_head_unstages_its_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "unborn"
            repo.mkdir()
            _run(repo, "init", "-b", "work-x")
            _run(repo, "config", "user.email", "test@example.com")
            _run(repo, "config", "user.name", "Test Clone")
            hook = repo / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (repo / "notes").mkdir()
            (repo / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            kept = self._stage_a_path_save_ignores(repo)
            result = git_flow.save(repo, apply=True)
            self._assert_failed_save_unstaged_only_its_paths(repo, result, "", kept)
            self.assertEqual(subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=str(repo),
                                            capture_output=True).returncode, 1)

    def test_a_failed_unstage_is_named_in_the_refusal_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            real = git_ops.run_git

            def failing(repo_dir, args, *rest, **kw):
                if "reset" in args or "rm" in args:
                    return subprocess.CompletedProcess(["git", *args], 1, "", "boom")
                if "commit" in args:
                    return subprocess.CompletedProcess(["git", *args], 1, "", "hook said no")
                return real(repo_dir, args, *rest, **kw)

            with mock.patch.object(git_ops, "run_git", side_effect=failing):
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("git commit failed", result["refusal"])
            self.assertIn("could not unstage notes/a.md", result["refusal"])

    def test_the_value_error_backstop_also_unstages_its_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()

            def stage_then_raise(repo_dir, *_args, **_kw):
                _run(repo_dir, "add", "--", "notes/a.md")
                raise ValueError("embedded null byte")

            with mock.patch.object(git_ops, "commit", side_effect=stage_then_raise):
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("not acceptable to git", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)

    def test_a_subject_with_a_nul_is_refused_not_a_crash_and_nothing_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            result = git_flow.save(clone, "fix", "core", "bad" + chr(0) + "subject", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("subject", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 2)

    def test_the_commit_is_given_exactly_the_listed_paths_and_the_built_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            with mock.patch.object(git_ops, "commit", wraps=git_ops.commit) as spy:
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(spy.call_args.kwargs["only_paths"], ["notes/a.md"])
            self.assertTrue(spy.call_args.args[1].startswith("docs(notes): update 1 file(s)"))


class TestSaveOwnerCheck(unittest.TestCase):
    OWNERS = {"owners": {"theirs": "someone-else", "mine": "Test Clone"}}

    def _repo(self, tmp):
        clone, _remote = _make_flow_repo(tmp, extra=self.OWNERS)
        _on_branch(clone)
        return clone

    def test_an_owner_failing_path_is_named_and_left_out_while_the_rest_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "mine/a.md", "a\n")
            _write(clone, "theirs/b.md", "b\n")
            with mock.patch.object(git_ops, "commit", wraps=git_ops.commit) as spy:
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(spy.call_args.kwargs["only_paths"], ["mine/a.md"])
            self.assertEqual([item["path"] for item in result["left_out"]], ["theirs/b.md"])
            self.assertIn("`theirs/b.md` is owned by someone-else", result["left_out"][0]["reason"])
            self.assertIn("theirs/b.md", result["preview"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(in_head, ["mine/a.md"])
            self.assertIn("theirs/b.md", _run(clone, "status", "--porcelain", "--untracked-files=all").stdout)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "log", "-1", "--pretty=%s").stdout.strip(),
                             "docs(mine): update 1 file(s)")
            self.assertNotIn("theirs/b.md", _run(clone, "log", "-1", "--pretty=%B").stdout)
            self.assertEqual(_receipt_lines(clone)[-1]["outcome"], "pass")

    def test_a_modified_tracked_owner_failing_path_stays_modified_and_unstaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "theirs/b.md", "b\n")
            _run(clone, "add", "--", "theirs/b.md")
            _run(clone, "commit", "-m", "seed theirs")
            _write(clone, "theirs/b.md", "b changed\n")
            _write(clone, "mine/a.md", "a\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertIn(" M theirs/b.md", _run(clone, "status", "--porcelain").stdout)

    def test_when_every_path_fails_the_owner_check_nothing_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "theirs/b.md", "b\n")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            with mock.patch.object(git_ops, "commit") as spy:
                result = git_flow.save(clone, apply=True)
            spy.assert_not_called()
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("every changed path was left out", result["refusal"])
            self.assertIn("`theirs/b.md`", result["refusal"])
            self.assertEqual([item["path"] for item in result["left_out"]], ["theirs/b.md"])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_receipt_lines(clone)[-1]["outcome"], "refused")

    def test_the_preview_of_an_all_left_out_save_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "theirs/b.md", "b\n")
            result = git_flow.save(clone)
            self.assertEqual(result["outcome"], "refused")
            self.assertEqual([r["step"] for r in _receipt_lines(clone)], ["start"])
            self.assertEqual(_staged(clone), [])

    def test_the_scope_is_inferred_from_the_kept_paths_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "mine/a.md", "a\n")
            _write(clone, "theirs/b.py", "b = 1\n")
            result = git_flow.save(clone)
            self.assertIn("docs(mine): update 1 file(s)", result["preview"])
            self.assertNotIn("theirs/b.py", result["preview"].split("Message:")[1])

    def test_a_landed_commit_with_a_refused_path_is_a_pass_with_an_amber_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "mine/a.md", "a\n")
            _write(clone, "mine/b.md", "b\n")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            real = git_ops.commit

            def stricter(repo_dir, message, this_owner, resolve_owner, *args, **kw):
                def resolver(root, path):
                    return "someone-else" if path == "mine/b.md" else resolve_owner(root, path)
                return real(repo_dir, message, this_owner, resolver, *args, **kw)

            with mock.patch.object(git_ops, "commit", side_effect=stricter):
                result = git_flow.save(clone, apply=True)
            self.assertNotEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertIn("mine/b.md", result["amber"])
            self.assertIn("owned by someone-else", result["amber"])
            self.assertEqual(result["refusal"], "")
            self.assertTrue(result["sha"])
            self.assertEqual(_receipt_lines(clone)[-1]["outcome"], "pass")


class TestSaveDenylist(unittest.TestCase):
    def _repo(self, tmp, denylist_text, git=None, extra=None):
        clone, _remote = _make_flow_repo(tmp, git=git, extra=extra)
        _write(clone, ".git-denylist", denylist_text)
        _run(clone, "add", ".git-denylist")
        _run(clone, "commit", "-m", "add denylist")
        _on_branch(clone)
        return clone

    def test_a_denylist_path_is_named_and_left_out_while_the_rest_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, "# never commit\nvault/*\n*.dump\n")
            _write(clone, "notes/a.md", "a\n")
            _write(clone, "vault/x.txt", "plain\n")
            _write(clone, "notes/db.dump", "rows\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            left = {item["path"]: item["reason"] for item in result["left_out"]}
            self.assertEqual(sorted(left), ["notes/db.dump", "vault/x.txt"])
            self.assertIn("`vault/*`", left["vault/x.txt"])
            self.assertIn("`*.dump`", left["notes/db.dump"])
            self.assertIn("Left out: ", result["preview"])
            in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
            self.assertEqual(in_head, ["notes/a.md"])
            status = _run(clone, "status", "--porcelain", "--untracked-files=all").stdout
            self.assertIn("vault/x.txt", status)
            self.assertEqual(_staged(clone), [])
            body = _run(clone, "log", "-1", "--pretty=%B").stdout
            self.assertNotIn("vault/x.txt", body)
            self.assertNotIn("db.dump", body)

    def test_a_modified_tracked_denylist_path_stays_modified_and_unstaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _write(clone, ".git-denylist", "vault/*\n")
            _write(clone, "vault/x.txt", "plain\n")
            _run(clone, "add", "--", ".git-denylist", "vault/x.txt")
            _run(clone, "commit", "-m", "seed vault")
            _on_branch(clone)
            _write(clone, "vault/x.txt", "changed\n")
            _write(clone, "notes/a.md", "a\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertIn(" M vault/x.txt", _run(clone, "status", "--porcelain").stdout)

    def test_when_only_denylist_paths_remain_nothing_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, "vault/*\n")
            _write(clone, "vault/x.txt", "plain\n")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            with mock.patch.object(git_ops, "commit") as spy:
                result = git_flow.save(clone, apply=True)
            spy.assert_not_called()
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("`vault/x.txt`", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_receipt_lines(clone)[-1]["outcome"], "refused")

    def test_a_path_failing_both_checks_is_listed_once_with_the_owner_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, "theirs/*\n", extra={"owners": {"theirs": "someone-else"}})
            _write(clone, "theirs/b.md", "b\n")
            _write(clone, "notes/a.md", "a\n")
            result = git_flow.save(clone)
            self.assertEqual([item["path"] for item in result["left_out"]], ["theirs/b.md"])
            self.assertIn("is owned by someone-else", result["left_out"][0]["reason"])
            self.assertEqual(result["preview"].count("theirs/b.md"), 1)

    def test_the_denylist_file_location_comes_from_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"denylist": "conf/deny.txt"})
            _write(clone, "conf/deny.txt", "*.dump\n")
            _run(clone, "add", "conf/deny.txt")
            _run(clone, "commit", "-m", "deny list")
            _on_branch(clone)
            _write(clone, "notes/db.dump", "rows\n")
            _write(clone, "notes/a.md", "a\n")
            result = git_flow.save(clone)
            self.assertEqual([i["path"] for i in result["left_out"]], ["notes/db.dump"])

    def test_a_missing_denylist_file_means_nothing_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "notes/a.md", "a\n")
            self.assertEqual(git_flow.save(clone)["left_out"], [])

    def _assert_unreadable_refuses(self, clone):
        head = _run(clone, "rev-parse", "HEAD").stdout.strip()
        _write(clone, "notes/a.md", "a\n")
        preview = git_flow.save(clone)
        self.assertEqual(preview["outcome"], "refused")
        self.assertIn(".git-denylist", preview["refusal"])
        with mock.patch.object(git_ops, "commit") as spy:
            result = git_flow.save(clone, apply=True)
        spy.assert_not_called()
        self.assertEqual(result["outcome"], "refused")
        self.assertIn(".git-denylist", result["refusal"])
        self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(_staged(clone), [])
        last = _receipt_lines(clone)[-1]
        self.assertEqual(last["outcome"], "refused")
        self.assertEqual(last["exit_code"], 2)

    def test_a_denylist_that_is_a_directory_refuses_with_nothing_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / ".git-denylist").mkdir()
            _write(clone, ".git-denylist/keep.txt", "x\n")
            _run(clone, "add", "--", ".git-denylist/keep.txt")
            _run(clone, "commit", "-m", "denylist dir")
            _on_branch(clone)
            self._assert_unreadable_refuses(clone)

    def test_a_utf16_denylist_refuses_without_echoing_its_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / ".git-denylist").write_bytes("vault/*\n".encode("utf-16"))
            _run(clone, "add", "--", ".git-denylist")
            _run(clone, "commit", "-m", "utf16 denylist")
            _on_branch(clone)
            self._assert_unreadable_refuses(clone)
            self.assertNotIn("vault", git_flow.save(clone)["refusal"])


class TestSaveSecretsScan(unittest.TestCase):
    def _repo(self, tmp):
        clone, _remote = _make_flow_repo(tmp)
        _on_branch(clone)
        return clone

    def _work_status(self, clone):
        # the receipt log under _log/ is untracked by design; ignore it
        return sorted(l for l in _status(clone).splitlines() if "_log/" not in l)

    def _assert_refused_cleanly(self, clone, result, secret_text, rule, path):
        self.assertEqual(result["outcome"], "refused")
        self.assertIn(f"`{path}`", result["refusal"])
        self.assertIn(rule, result["refusal"])
        self.assertEqual(_staged(clone), [])
        blob = json.dumps(result)
        log_path = clone / "_log" / "runs" / "git-flow.ndjson"
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        for needle in secret_text:
            self.assertNotIn(needle, blob)
            self.assertNotIn(needle, log)
            self.assertNotIn(needle, result["preview"])

    def test_a_planted_aws_key_refuses_the_whole_save_and_leaves_the_clean_file_unstaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            _write(clone, "notes/clean.md", "clean\n")
            _write(clone, "notes/leak.txt", f"aws {AWS_KEY}\n")
            result = git_flow.save(clone, apply=True)
            self._assert_refused_cleanly(clone, result, [AWS_KEY, "IOSFODNN7"], "aws-access-key", "notes/leak.txt")
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(self._work_status(clone), ["?? notes/clean.md", "?? notes/leak.txt"])
            receipt = _receipt_lines(clone)[-1]
            self.assertEqual((receipt["step"], receipt["outcome"], receipt["exit_code"]), ("save", "refused", 2))
            self.assertEqual(receipt["sha_before"], receipt["sha_after"])

    def test_the_preview_refuses_too_and_stages_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "notes/leak.txt", f"aws {AWS_KEY}\n")
            result = git_flow.save(clone)
            self._assert_refused_cleanly(clone, result, [AWS_KEY, "IOSFODNN7"], "aws-access-key", "notes/leak.txt")

    def test_a_pem_header_and_a_password_line_each_refuse_the_save(self):
        cases = [
            ("keys.txt", PEM_HEADER + "\nMIIB\n", "pem-private-key", ["PRIVATE KEY"]),
            ("app/config.py", PASSWORD_LINE + "\n", "secret-assignment", ["hunter2hunter2"]),
        ]
        for rel, text, rule, needles in cases:
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                _write(clone, rel, text)
                result = git_flow.save(clone, "chore", "misc", "add a file", apply=True)
                self._assert_refused_cleanly(clone, result, needles, rule, rel)

    def test_a_dot_env_file_is_refused_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "app/.env", "A=1\n")
            result = git_flow.save(clone, "chore", "app", "add env", apply=True)
            self._assert_refused_cleanly(clone, result, [], ".env", "app/.env")

    def test_a_dot_env_file_is_refused_without_its_content_being_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "app/.env", "A=1\n")
            with mock.patch.object(git_flow, "_added_lines") as spy:
                result = git_flow.save(clone, "chore", "app", "add env", apply=True)
            spy.assert_not_called()
            self.assertEqual(result["outcome"], "refused")

    def test_a_secret_added_to_an_already_tracked_file_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "hello.md", f"hello\nkey {AWS_KEY}\n")
            result = git_flow.save(clone, "docs", "hello", "edit hello")
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("aws-access-key", result["refusal"])

    def test_a_color_always_git_config_cannot_hide_an_added_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _run(clone, "config", "color.ui", "always")
            _run(clone, "config", "color.diff", "always")
            _write(clone, "hello.md", f"hello\nkey {AWS_KEY}\n")
            result = git_flow.save(clone, "docs", "hello", "edit hello", apply=True)
            self._assert_refused_cleanly(clone, result, [AWS_KEY, "IOSFODNN7"], "aws-access-key", "hello.md")

    def test_look_alikes_pass_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "notes/a.md",
                   'password = "short"\n# rotate the api key monthly\nthe token is stored elsewhere\n')
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(self._work_status(clone), [])

    def test_removing_a_committed_secret_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "notes/old.txt", f"line\nkey {AWS_KEY}\n")
            _run(clone, "add", "notes/old.txt")
            _run(clone, "commit", "-m", "an old commit that holds a key")
            _write(clone, "notes/old.txt", "line\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])

    def test_a_failed_scan_refuses_rather_than_passing(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "hello.md", "changed\n")
            real = subprocess.run

            def fake(cmd, *args, **kwargs):
                if "-U0" in cmd:
                    return subprocess.CompletedProcess(cmd, 124, b"", b"timed out")
                return real(cmd, *args, **kwargs)

            with mock.patch.object(subprocess, "run", side_effect=fake):
                result = git_flow.save(clone, "docs", "hello", "edit hello", apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertIn("scan-failed", result["refusal"])
            self.assertIn("could not run", result["refusal"])
            self.assertIn("`hello.md`", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(self._work_status(clone), [" M hello.md"])

    # Fix round 1: content the diff could hide, or a pathspec that reads as tracked.
    def _commit_files(self, clone, files):
        for rel, text in files.items():
            _write(clone, rel, text)
        _run(clone, "add", "--", *files)
        _run(clone, "commit", "-m", "seed " + ",".join(files))
        return _run(clone, "rev-parse", "HEAD").stdout.strip()

    def _assert_hidden_secret_refused(self, clone, head, path):
        result = git_flow.save(clone, "chore", "misc", "add a line", apply=True)
        self._assert_refused_cleanly(clone, result, [AWS_KEY, "IOSFODNN7"], "aws-access-key", path)
        self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(_staged(clone), [])

    def test_a_no_diff_or_binary_gitattribute_cannot_hide_an_added_secret(self):
        for attr in ("-diff", "binary"):
            with self.subTest(attr=attr), tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                head = self._commit_files(clone, {"data.txt": "line\n", ".gitattributes": f"data.txt {attr}\n"})
                _write(clone, "data.txt", f"line\naws {AWS_KEY}\n")
                self._assert_hidden_secret_refused(clone, head, "data.txt")

    def test_a_diff_external_git_config_cannot_hide_an_added_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = self._commit_files(clone, {"data.txt": "line\n"})
            _run(clone, "config", "diff.external", "echo")
            _write(clone, "data.txt", f"line\naws {AWS_KEY}\n")
            self._assert_hidden_secret_refused(clone, head, "data.txt")

    def test_a_textconv_driver_cannot_hide_an_added_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = self._commit_files(clone, {"data.txt": "line\n", ".gitattributes": "*.txt diff=hide\n"})
            _run(clone, "config", "diff.hide.textconv", "true")
            _write(clone, "data.txt", f"line\naws {AWS_KEY}\n")
            self._assert_hidden_secret_refused(clone, head, "data.txt")

    def test_an_untracked_glob_named_file_is_not_mistaken_for_a_tracked_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = self._commit_files(clone, {"a.txt": "line\n"})
            _write(clone, "[ab].txt", f"aws {AWS_KEY}\n")
            self._assert_hidden_secret_refused(clone, head, "[ab].txt")

    # Fix round 2: separators inside an added line, UTF-16 content, glob paths at commit time.
    def test_a_line_separator_inside_a_tracked_added_line_cannot_hide_a_secret(self):
        seps = ["\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"]
        failures = []
        for sep in seps:
            with tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                head = self._commit_files(clone, {"data.txt": "line\n"})
                (clone / "data.txt").write_bytes(f"line\nprefix{sep}aws {AWS_KEY}\n".encode("utf-8"))
                result = git_flow.save(clone, "chore", "misc", "add a line", apply=True)
                still_head = _run(clone, "rev-parse", "HEAD").stdout.strip() == head
                if result["outcome"] != "refused" or "aws-access-key" not in result["refusal"] or not still_head:
                    failures.append(repr(sep))
        self.assertEqual(failures, [], "secret slipped past for separators: " + ", ".join(failures))

    def _assert_utf16_refused(self, clone, head, path):
        result = git_flow.save(clone, "chore", "misc", "add a line", apply=True)
        self._assert_refused_cleanly(clone, result, [AWS_KEY, "IOSFODNN7"], "aws-access-key", path)
        self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)

    def test_a_utf16_untracked_file_cannot_hide_a_secret(self):
        for codec, bom in (("utf-16-le", "\ufeff"), ("utf-16-be", "\ufeff"), ("utf-16-le", "")):
            with tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                head = _run(clone, "rev-parse", "HEAD").stdout.strip()
                (clone / "u16.txt").write_bytes((bom + f"line\naws {AWS_KEY}\n").encode(codec))
                with self.subTest(codec=codec, bom=bool(bom)):
                    self._assert_utf16_refused(clone, head, "u16.txt")

    def test_a_utf16_tracked_file_cannot_hide_an_added_secret(self):
        for codec, bom in (("utf-16-le", "\ufeff"), ("utf-16-be", "\ufeff"), ("utf-16-le", "")):
            with tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                (clone / "u16.txt").write_bytes((bom + "line\n").encode(codec))
                _run(clone, "add", "--", "u16.txt")
                _run(clone, "commit", "-m", "seed u16")
                head = _run(clone, "rev-parse", "HEAD").stdout.strip()
                (clone / "u16.txt").write_bytes((bom + f"line\naws {AWS_KEY}\n").encode(codec))
                with self.subTest(codec=codec, bom=bool(bom)):
                    self._assert_utf16_refused(clone, head, "u16.txt")

    def test_a_clean_utf16_file_still_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            (clone / "u16.txt").write_bytes("\ufeffhello there\nsecond line\n".encode("utf-16-le"))
            result = git_flow.save(clone, "chore", "misc", "add u16", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(self._work_status(clone), [])

    def _assert_only_glob_file_committed(self, clone, result, head, left_path):
        self.assertEqual(result["outcome"], "pass", result["refusal"])
        in_head = _run(clone, "show", "--name-only", "--pretty=format:", "HEAD").stdout.split()
        self.assertEqual(in_head, ["[ab].txt"] if left_path == "a.txt" else ["[tm]/x.txt"])
        self.assertEqual(_staged(clone), [])
        self.assertIn(f" M {left_path}", self._work_status(clone))
        self.assertNotEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
        for sha in _run(clone, "rev-list", "HEAD").stdout.split():
            self.assertNotIn(AWS_KEY, _run(clone, "show", sha).stdout)

    def test_a_denylisted_path_is_not_swept_into_the_commit_by_a_glob_named_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = self._commit_files(clone, {"a.txt": "line\n", ".git-denylist": "a.txt\n"})
            _write(clone, "a.txt", f"line\naws {AWS_KEY}\n")
            _write(clone, "[ab].txt", "clean\n")
            result = git_flow.save(clone, "chore", "misc", "add glob file", apply=True)
            self._assert_only_glob_file_committed(clone, result, head, "a.txt")

    def test_an_owner_refused_path_is_not_swept_into_the_commit_by_a_glob_named_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, extra={"owners": {"t": "someone-else"}})
            _on_branch(clone)
            head = self._commit_files(clone, {"t/x.txt": "line\n"})
            _write(clone, "t/x.txt", f"line\naws {AWS_KEY}\n")
            _write(clone, "[tm]/x.txt", "clean\n")
            result = git_flow.save(clone, "chore", "misc", "add glob file", apply=True)
            self._assert_only_glob_file_committed(clone, result, head, "t/x.txt")


def _script(tmp, name, body):
    """A tiny python script under tmp and the shell command that runs it with
    this interpreter (quoted, so a path with spaces works under cmd and sh)."""
    path = pathlib.Path(tmp) / name
    path.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def _pid_alive(pid):
    if git_flow.IS_WINDOWS:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TestCheckStep(unittest.TestCase):
    def _repo(self, tmp, checks, timeout=None):
        git = {"checks": checks}
        if timeout is not None:
            git["checkTimeoutSeconds"] = timeout
        clone, _remote = _make_flow_repo(tmp, git=git)
        return clone

    def test_a_green_list_passes_with_one_record_per_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _script(tmp, "ok.py", "print('fine')\n")
            clone = self._repo(tmp, [ok, ok])
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(len(result["checks"]), 2)
            for record in result["checks"]:
                self.assertEqual(set(record), {"command", "exit", "seconds"})
                self.assertEqual(record["exit"], 0)
                self.assertIsInstance(record["seconds"], float)
            self.assertEqual(result["amber"], "")
            receipt = _receipt_lines(clone)[0]
            self.assertEqual(receipt["checks"], result["checks"])
            self.assertEqual((receipt["step"], receipt["exit_code"]), ("check", 0))

    def test_a_failing_command_is_blocked_with_its_exit_code_and_stops_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _script(tmp, "ok.py", "pass\n")
            bad = _script(tmp, "bad.py", "import sys\nsys.stderr.write('nope\\n')\nsys.exit(3)\n")
            clone = self._repo(tmp, [ok, bad, ok])
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertEqual([r["exit"] for r in result["checks"]], [0, 3])
            self.assertIn("exit 3", result["refusal"])
            receipt = _receipt_lines(clone)[0]
            self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("blocked", 3))
            self.assertEqual(receipt["stderr_tail"], "nope")

    def test_a_timeout_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            slow = _script(tmp, "slow.py", "import time\ntime.sleep(30)\n")
            clone = self._repo(tmp, [slow], timeout=1)
            started = time.monotonic()
            result = git_flow.check(clone, apply=True)
            self.assertLess(time.monotonic() - started, 20)
            self.assertEqual(result["outcome"], "blocked")
            self.assertEqual(result["checks"][0]["exit"], git_ops.TIMEOUT_RETURNCODE)
            self.assertIn("timed out", result["refusal"])

    def test_a_check_that_reads_stdin_does_not_block_on_the_parents_open_stdin(self):
        # The parent's fd 0 is swapped for the read end of a pipe whose write
        # end stays open, so a check that inherits stdin would block until the
        # timeout. The check must get an empty stdin instead.
        with tempfile.TemporaryDirectory() as tmp:
            reader = _script(tmp, "reader.py", "import sys\nsys.stdin.read()\n")
            clone = self._repo(tmp, [reader], timeout=5)
            read_end, write_end = os.pipe()
            saved = os.dup(0)
            try:
                os.dup2(read_end, 0)
                started = time.monotonic()
                result = git_flow.check(clone, apply=True)
                elapsed = time.monotonic() - started
            finally:
                os.dup2(saved, 0)
                os.close(saved)
                os.close(read_end)
                os.close(write_end)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(result["checks"][0]["exit"], 0)
            self.assertLess(elapsed, 4)

    def test_a_timeout_kills_the_process_tree_and_stops_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = pathlib.Path(tmp) / "pid.txt"
            slow = _script(
                tmp, "slow.py",
                f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n",
            )
            after = _script(tmp, "after.py", "pass\n")
            clone = self._repo(tmp, [slow, after], timeout=2)
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertEqual(len(result["checks"]), 1)
            pid = int(pid_file.read_text(encoding="utf-8"))
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.25)
            self.assertFalse(_pid_alive(pid), "the check's child process outlived the timeout")

    def test_an_empty_list_passes_with_the_amber_note_and_a_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, [])
            bare = git_flow.check(clone)
            self.assertEqual(bare["outcome"], "pass")
            self.assertEqual(bare["amber"], "0 checks configured")
            self.assertIn("amber: 0 checks configured", bare["preview"])
            self.assertIn("No test setup found", bare["preview"])
            (clone / "tests").mkdir()
            (clone / "package.json").write_text("{}", encoding="utf-8")
            suggested = git_flow.check(clone, apply=True)
            self.assertIn("python -m pytest -q", suggested["preview"])
            self.assertIn("npm test", suggested["preview"])
            self.assertEqual(_receipt_lines(clone)[0]["checks"], [])
            self.assertEqual(suggested["checks"], [])

    def test_a_preview_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = pathlib.Path(tmp) / "ran.txt"
            touch = _script(tmp, "touch.py", f"open({str(marker)!r}, 'w').write('x')\n")
            clone = self._repo(tmp, [touch])
            result = git_flow.check(clone)
            self.assertEqual(result["outcome"], "pass")
            self.assertFalse(marker.exists())
            self.assertIn(touch, result["preview"])
            self.assertEqual(_receipt_lines(clone), [])

    def test_a_command_that_does_not_exist_is_blocked_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, ["definitely-not-a-real-command-zq"])
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertNotEqual(result["checks"][0]["exit"], 0)
            self.assertIn("failed with exit", result["refusal"])

    def test_a_command_that_cannot_start_is_blocked_with_a_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, ["anything"])
            with mock.patch.object(git_flow, "run_check", side_effect=OSError("no such directory")):
                result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("could not be started", result["refusal"])
            self.assertIn("no such directory", result["refusal"])
            self.assertEqual([r["command"] for r in result["checks"]], ["anything"])
            receipt = _receipt_lines(clone)[0]
            self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("blocked", 3))

    def test_secrets_in_stderr_and_in_the_command_are_redacted(self):
        key = "AKIA" + "Q" * 16
        with tempfile.TemporaryDirectory() as tmp:
            bad = _script(
                tmp, "leak.py", f"import sys\nsys.stderr.write('boom {key}\\n')\nsys.exit(1)\n",
            ) + f" --note {key}"
            clone = self._repo(tmp, [bad])
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertNotIn(key, json.dumps(result))
            receipt_text = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
            self.assertNotIn(key, receipt_text)
            self.assertIn("boom", _receipt_lines(clone)[0]["stderr_tail"])

    def test_a_step_returning_checks_none_still_writes_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, [])
            result = git_flow._run_step("probe", clone, True, None, _inner("pass", checks=None))
            self.assertTrue(result["receipt_written"])
            self.assertEqual(_receipt_lines(clone)[0]["checks"], [])


def _cli(argv):
    """git_flow.main(argv) with stdout captured: (exit code, printed text)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = git_flow.main(argv)
    return code, buffer.getvalue()


@contextlib.contextmanager
def _cwd(path):
    """Run the block with path as the current directory, then restore it."""
    before = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(before)


def _planted_secret():
    return "AKIA" + "Q" * 16  # built at runtime so this file never holds a matching literal


class TestCli(unittest.TestCase):
    def test_exit_codes_are_0_pass_2_refused_3_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            code, out = _cli(["check", "--repo", str(clone)])
            self.assertEqual(code, 0)
            self.assertIn("[check] pass", out)
            self.assertIn("0 checks configured", out)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            code, out = _cli(["save", "--repo", str(clone), "--apply"])
            self.assertEqual(code, 2)
            self.assertIn("[save] refused", out)
            self.assertIn("`main` is protected", out)
            self.assertIn("receipt ", out)
        with tempfile.TemporaryDirectory() as tmp:
            bad = _script(tmp, "bad.py", "import sys\nsys.exit(1)\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [bad]})
            code, out = _cli(["check", "--repo", str(clone), "--apply"])
            self.assertEqual(code, 3)
            self.assertIn("[check] blocked", out)

    def test_start_defaults_to_a_preview_and_apply_acts(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            code, out = _cli(["start", "fix-1", "--repo", str(clone)])
            self.assertEqual(code, 0)
            self.assertEqual(_branches(clone), ["main"])
            self.assertNotIn("receipt", out)
            code, out = _cli(["start", "fix-1", "--repo", str(clone), "--apply"])
            self.assertEqual(code, 0)
            self.assertEqual(_branches(clone), ["main", "work/test-clone/fix-1"])

    def test_a_save_preview_changes_nothing_and_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "notes").mkdir()
            (clone / "notes" / "a.md").write_text("a\n", encoding="utf-8")
            head = _run(clone, "rev-parse", "HEAD").stdout.strip()
            status = _run(clone, "status", "--porcelain").stdout
            receipts = len(_receipt_lines(clone))
            code, out = _cli(["save", "--repo", str(clone)])
            self.assertEqual(code, 0)
            self.assertIn("[save] pass", out)
            self.assertNotIn("receipt", out)
            self.assertEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual(_run(clone, "status", "--porcelain").stdout, status)
            self.assertEqual(len(_receipt_lines(clone)), receipts)

    def test_save_options_reach_the_commit_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "a.md").write_text("a\n", encoding="utf-8")
            code, _out = _cli(["save", "--repo", str(clone), "--apply", "--type", "fix",
                               "--scope", "core", "--subject", "handle empty input"])
            self.assertEqual(code, 0)
            self.assertEqual(_run(clone, "log", "-1", "--pretty=%s").stdout.strip(),
                             "fix(core): handle empty input")

    def test_harness_and_model_flags_land_in_the_receipt_and_run_id_is_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _cli(["check", "--repo", str(clone), "--apply", "--harness", "claude-code", "--model", "m-1"])
            _cli(["check", "--repo", str(clone), "--apply"])
            first, second = _receipt_lines(clone)
            self.assertEqual((first["harness"], first["model"]), ("claude-code", "m-1"))
            self.assertNotEqual(first["run_id"], second["run_id"])  # one run_id per invocation

    def test_harness_and_model_fall_back_to_the_environment_then_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("SITEOFFICE_HARNESS", "SITEOFFICE_MODEL")}
            with mock.patch.dict(os.environ, env, clear=True):
                _cli(["check", "--repo", str(clone), "--apply"])
                os.environ["SITEOFFICE_HARNESS"] = "env-harness"
                os.environ["SITEOFFICE_MODEL"] = "env-model"
                _cli(["check", "--repo", str(clone), "--apply"])
            unset, from_env = _receipt_lines(clone)
            self.assertEqual((unset["harness"], unset["model"]), ("unknown", "unknown"))
            self.assertEqual((from_env["harness"], from_env["model"]), ("env-harness", "env-model"))

    def test_find_repo_walks_up_to_the_nearest_siteoffice_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            (root / "a" / "b").mkdir(parents=True)
            (root / "a" / "siteoffice.json").write_text("{}", encoding="utf-8")
            self.assertEqual(git_flow.find_repo(root / "a" / "b"), root / "a")
            self.assertEqual(git_flow.find_repo(root / "a"), root / "a")

    def test_find_repo_returns_none_when_there_is_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(git_flow.find_repo(pathlib.Path(tmp) / "nowhere"))

    def test_repo_defaults_to_the_nearest_manifest_root_from_a_nested_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            (clone / "deep" / "er").mkdir(parents=True)
            with _cwd(clone / "deep" / "er"):
                code, out = _cli(["start", "fix-2", "--apply"])
            self.assertEqual(code, 0, out)
            self.assertIn("work/test-clone/fix-2", _branches(clone))
            self.assertEqual(_receipt_lines(clone)[-1]["step"], "start")

    def test_no_manifest_is_a_refusal_with_a_sentence_and_the_cwd_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = pathlib.Path(tmp) / "empty"
            empty.mkdir()
            with _cwd(empty):
                code, out = _cli(["save", "--apply"])
            self.assertEqual(code, 2)
            self.assertIn("[save] refused", out)
            self.assertIn("siteoffice.json", out)
            self.assertIn("--repo", out)
            self.assertEqual(list(empty.iterdir()), [])  # nothing created, no receipt

    def test_a_repo_that_is_not_a_directory_is_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _cli(["check", "--repo", str(pathlib.Path(tmp) / "missing")])
            self.assertEqual(code, 2)
            self.assertIn("is not a directory", out)

    def test_a_usage_error_exits_2_and_prints_the_usage_error_prefix_to_stderr(self):
        for argv in ([], ["start"], ["nonsense"], ["check", "--nope"]):
            with self.subTest(argv=argv):
                errors, printed = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(printed):
                    with self.assertRaises(SystemExit) as raised:
                        git_flow.main(argv)
                self.assertEqual(raised.exception.code, 2)
                self.assertTrue(errors.getvalue().startswith("usage error: "), errors.getvalue())
                self.assertEqual(printed.getvalue(), "")

    def test_an_unexpected_exception_becomes_a_blocked_result_without_a_traceback(self):
        secret = _planted_secret()
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            boom = RuntimeError(f"owners table broke near {secret}\nsecond line")
            with mock.patch.object(git_flow, "check", side_effect=boom):
                code, out = _cli(["check", "--repo", str(clone), "--apply"])
            self.assertEqual(code, 3)
            self.assertIn("[check] blocked", out)
            self.assertIn("RuntimeError", out)
            self.assertNotIn("Traceback", out)
            self.assertNotIn(secret, out)
            self.assertIn("second line", out.split("Refusal: ", 1)[1].split("\n")[0])  # one line
            (line,) = _receipt_lines(clone)
            self.assertEqual((line["step"], line["outcome"], line["exit_code"]), ("check", "blocked", 3))
            self.assertIn("RuntimeError", line["refusal"])
            self.assertNotIn(secret, json.dumps(line))
            self.assertIn("receipt ", out)

    def test_an_unexpected_exception_in_a_preview_writes_no_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            with mock.patch.object(git_flow, "start", side_effect=ValueError("bad manifest")):
                code, out = _cli(["start", "fix-1", "--repo", str(clone)])
            self.assertEqual(code, 3)
            self.assertIn("ValueError", out)
            self.assertNotIn("receipt", out)
            self.assertEqual(_receipt_lines(clone), [])

    def test_git_missing_from_path_is_blocked_and_a_failed_receipt_never_fails_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            missing = FileNotFoundError(2, "No such file or directory: 'git'")
            with mock.patch.object(git_ops, "run_git", side_effect=missing):
                code, out = _cli(["save", "--repo", str(clone), "--apply"])
            self.assertEqual(code, 3)
            self.assertIn("[save] blocked", out)
            self.assertIn("FileNotFoundError", out)
            self.assertNotIn("Traceback", out)
            self.assertEqual(_receipt_lines(clone), [])

    def test_printed_text_never_holds_a_secret_even_if_a_result_field_does(self):
        secret = _planted_secret()
        leaky = git_flow.make_result("check", "pass", f"key {secret}", amber=f"amber {secret}")
        leaky["receipt_written"] = False
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            with mock.patch.object(git_flow, "check", return_value=leaky):
                code, out = _cli(["check", "--repo", str(clone)])
        self.assertEqual(code, 0)
        self.assertNotIn(secret, out)
        self.assertIn("[redacted]", out)

    def test_the_script_runs_as_a_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            done = subprocess.run(
                [sys.executable, str(SYNC_DIR / "git_flow.py"), "check", "--repo", str(clone)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("0 checks configured", done.stdout)

    def test_exit_codes_and_streams_through_a_real_subprocess(self):
        program = str(SYNC_DIR / "git_flow.py")

        def run(*args, cwd=None):
            return subprocess.run(
                [sys.executable, program, *args], capture_output=True, text=True,
                encoding="utf-8", cwd=cwd,
            )

        with tempfile.TemporaryDirectory() as tmp:
            bad = _script(tmp, "bad.py", "import sys\nsys.exit(1)\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [bad]})
            blocked = run("check", "--repo", str(clone), "--apply", "--harness", "h-1", "--model", "m-9")
            self.assertEqual(blocked.returncode, 3, blocked.stderr)
            self.assertIn("[check] blocked", blocked.stdout)
            self.assertEqual(blocked.stderr, "")
            (line,) = _receipt_lines(clone)
            self.assertEqual((line["harness"], line["model"], line["exit_code"]), ("h-1", "m-9", 3))
            (clone / "n.md").write_text("n\n", encoding="utf-8")
            refused = run("save", "--apply", cwd=str(clone))  # --repo found from the cwd
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("[save] refused", refused.stdout)
            self.assertIn("`main` is protected", refused.stdout)
            usage = run("start", cwd=str(clone))
            self.assertEqual(usage.returncode, 2)
            self.assertTrue(usage.stderr.startswith("usage error: "), usage.stderr)
            self.assertEqual(usage.stdout, "")
        with tempfile.TemporaryDirectory() as tmp:
            no_manifest = run("check", cwd=tmp)
            self.assertEqual(no_manifest.returncode, 2)
            self.assertIn("siteoffice.json", no_manifest.stdout)


def _receipt_files(top):
    """{path: line count} of every git-flow receipt file under top (outside .git)."""
    return {str(p): len(p.read_text(encoding="utf-8").splitlines())
            for p in pathlib.Path(top).rglob("git-flow.ndjson") if ".git" not in p.parts}


class TestCliGitRootGuard(unittest.TestCase):
    """The chosen directory must BE the git top-level: git reports changed paths
    relative to the top-level, so the denylist, owner and scan steps are only
    correct there. A nested manifest node is refused before anything runs."""

    PROGRAM = str(SYNC_DIR / "git_flow.py")

    def _nested(self, tmp):
        """Root manifest (owners map, feature branch) plus dept/siteoffice.json
        with dept/.git-denylist, and changed files under dept."""
        clone, _remote = _make_flow_repo(tmp, extra={"owners": {"dept/owned": "someone-else"}})
        _on_branch(clone)
        dept = clone / "dept"
        (dept / "notes").mkdir(parents=True)
        (dept / "owned").mkdir()
        (dept / "siteoffice.json").write_text(
            json.dumps({"schemaVersion": 2, "type": "department", "name": "d", "parent": "../siteoffice.json"}),
            encoding="utf-8")
        (dept / ".git-denylist").write_text("notes/*\ndept/notes/*\n", encoding="utf-8")
        (dept / "ok.txt").write_text("ok\n", encoding="utf-8")
        (dept / "notes" / "x.txt").write_text("x\n", encoding="utf-8")
        (dept / "owned" / "f.txt").write_text("f\n", encoding="utf-8")
        return clone, dept

    def _run(self, *args, cwd=None):
        return subprocess.run([sys.executable, self.PROGRAM, *args], capture_output=True, text=True,
                              encoding="utf-8", cwd=None if cwd is None else str(cwd))

    def _assert_refused_untouched(self, done, clone, top_text, head, status, receipts):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("refused", done.stdout)
        self.assertIn(top_text, done.stdout)
        self.assertIn("git root", done.stdout)
        self.assertIn("--repo", done.stdout)
        self.assertNotIn("receipt", done.stdout)
        self.assertEqual(_head(clone), head)
        self.assertEqual(_staged(clone), [])
        self.assertEqual(_status(clone), status)
        self.assertEqual(_receipt_files(clone), receipts)  # the fixture's own start receipt only

    def test_a_nested_manifest_node_is_refused_for_every_step_in_preview_and_apply(self):
        for step_args in (["save"], ["save", "--apply"], ["start", "s-2"], ["start", "s-2", "--apply"],
                          ["check"], ["check", "--apply"]):
            with self.subTest(step=step_args):
                with tempfile.TemporaryDirectory() as tmp:
                    clone, dept = self._nested(tmp)
                    head, status, receipts = _head(clone), _status(clone), _receipt_files(clone)
                    done = self._run(*step_args, cwd=dept)
                    top = os.path.realpath(str(clone))
                    self._assert_refused_untouched(done, clone, pathlib.Path(top).name, head, status, receipts)
                    self.assertEqual(len(done.stdout.strip().splitlines()), 2, done.stdout)  # heading + one sentence
                    self.assertEqual(_branches(clone), ["main", "work/test-clone/fix-1"])

    def test_the_sentence_names_the_git_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, dept = self._nested(tmp)
            done = self._run("save", "--apply", cwd=dept)
            top = _run(clone, "rev-parse", "--show-toplevel").stdout.strip()
            self.assertIn(top.replace("\\", "/"), done.stdout.replace("\\", "/"))

    def test_a_repo_that_is_a_plain_subdirectory_of_the_git_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            (clone / "plain").mkdir()
            (clone / "plain" / "a.md").write_text("a\n", encoding="utf-8")
            head, status, receipts = _head(clone), _status(clone), _receipt_files(clone)
            done = self._run("save", "--apply", "--repo", str(clone / "plain"))
            self._assert_refused_untouched(done, clone, clone.name, head, status, receipts)

    def test_in_process_refusal_writes_no_receipt_and_leaves_the_tree_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, dept = self._nested(tmp)
            head, status, receipts = _head(clone), _status(clone), _receipt_files(clone)
            code, out = _cli(["save", "--apply", "--repo", str(dept)])
            self.assertEqual(code, 2, out)
            self.assertIn("[save] refused", out)
            self.assertEqual(_head(clone), head)
            self.assertEqual(_status(clone), status)
            self.assertEqual(_receipt_files(clone), receipts)

    def test_the_git_top_level_itself_still_works_in_every_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            spellings = [str(clone), str(clone) + os.sep, str(clone) + "/", str(clone / "." / "."),
                         str(clone / "plain" / "..")]
            if git_flow.IS_WINDOWS:
                spellings += [str(clone).upper(), str(clone).lower()]
            (clone / "plain").mkdir()
            for index, spelling in enumerate(spellings):
                with self.subTest(spelling=spelling):
                    (clone / "notes").mkdir(exist_ok=True)
                    (clone / "notes" / f"n{index}.md").write_text("n\n", encoding="utf-8")
                    done = self._run("save", "--apply", "--repo", spelling)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertIn("[save] pass", done.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            done = self._run("check", cwd=clone)  # discovery from the root itself
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_non_git_repo_directory_keeps_its_existing_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = pathlib.Path(tmp) / "plain"
            plain.mkdir()
            done = self._run("save", "--repo", str(plain))
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("[save] refused", done.stdout)
            self.assertNotIn("git root", done.stdout)
            self.assertIn("not a git repository", done.stdout)

    def test_an_undetermined_top_level_is_not_treated_as_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, dept = self._nested(tmp)
            self.assertIsNone(git_flow._git_toplevel(pathlib.Path(tmp) / "nowhere"))
            with mock.patch.object(git_flow.git_ops, "run_git", side_effect=FileNotFoundError("git")):
                self.assertIsNone(git_flow._git_toplevel(dept))
            bad = subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr="")
            with mock.patch.object(git_flow.git_ops, "run_git", return_value=bad):
                self.assertIsNone(git_flow._git_toplevel(dept))
            self.assertTrue(git_flow._same_directory(clone, str(clone) + os.sep))
            self.assertFalse(git_flow._same_directory(dept, clone))


class TestReceiptsEndToEnd(unittest.TestCase):
    def test_the_repo_gitignore_lists_the_receipt_log(self):
        lines = (SYNC_DIR.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("_log/runs/git-flow.ndjson", lines)

    def test_a_failing_check_that_prints_a_key_is_redacted_in_stderr_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaky = _script(
                tmp, "leaky.py",
                f"import sys\nsys.stderr.write('boom ' + {AWS_KEY!r} + '\\n')\nsys.exit(4)\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [leaky]})
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            receipt = _receipt_lines(clone)[0]
            self.assertEqual(receipt["checks"][0]["exit"], 4)
            self.assertIn("boom", receipt["stderr_tail"])
            self.assertIn("[redacted]", receipt["stderr_tail"])
            raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
            self.assertNotIn(AWS_KEY, raw)
            self.assertNotIn(AWS_KEY, json.dumps(result))

    def test_start_save_check_write_one_line_each_with_every_field_and_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _script(tmp, "ok.py", "pass\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [ok]})
            ctx = git_flow.RunContext(harness="claude-code", model="test-model")
            started = git_flow.start(clone, "e2e", apply=True, ctx=ctx)
            self.assertEqual(started["outcome"], "pass", started["refusal"])
            _write(clone, "notes/a.md", "a\n")
            saved = git_flow.save(clone, apply=True, ctx=ctx)
            self.assertEqual(saved["outcome"], "pass", saved["refusal"])
            checked = git_flow.check(clone, apply=True, ctx=ctx)
            self.assertEqual(checked["outcome"], "pass", checked["refusal"])
            _write(clone, "notes/leak.txt", f"aws {AWS_KEY}\n")
            refused = git_flow.save(clone, apply=True, ctx=ctx)
            self.assertEqual(refused["outcome"], "refused")

            lines = _receipt_lines(clone)
            self.assertEqual([l["step"] for l in lines], ["start", "save", "check", "save"])
            self.assertEqual([l["outcome"] for l in lines], ["pass", "pass", "pass", "refused"])
            for line in lines:
                self.assertEqual(set(line), RECEIPT_FIELDS)
                self.assertEqual(line["run_id"], ctx.run_id)
                self.assertEqual((line["harness"], line["model"]), ("claude-code", "test-model"))
            self.assertEqual(len({l["receipt_id"] for l in lines}), 4)
            self.assertEqual(lines[0]["branch"], "work/test-clone/e2e")
            self.assertNotEqual(lines[1]["sha_before"], lines[1]["sha_after"])  # save moved HEAD
            self.assertEqual(lines[3]["sha_before"], lines[3]["sha_after"])  # refusal did not
            self.assertEqual(lines[2]["checks"][0]["exit"], 0)
            raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
            self.assertEqual(len(raw.splitlines()), 4)
            self.assertNotIn(AWS_KEY, raw)
            self.assertTrue(raw.isascii())

    def test_no_planted_secret_reaches_the_raw_log_even_when_a_refusal_was_about_it(self):
        secrets = [AWS_KEY, PEM_HEADER, "hunter2hunter2", SK_KEY, GH_TOKEN, SLACK_TOKEN]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            git_flow.start(clone, "leaks", apply=True)
            for index, secret in enumerate(secrets):
                _write(clone, f"notes/leak{index}.txt", f"value: {secret}\n")
            _write(clone, "notes/pw.txt", PASSWORD_LINE + "\n")
            refused = git_flow.save(clone, apply=True)
            self.assertEqual(refused["outcome"], "refused")
            self.assertTrue(refused["receipt_written"])
            raw = (clone / "_log" / "runs" / "git-flow.ndjson").read_bytes()
            self.assertEqual(len(raw.splitlines()), 2)  # start and the refused save
            for secret in secrets:
                self.assertNotIn(secret.encode("ascii"), raw)
            for line in _receipt_lines(clone):
                self.assertEqual(set(line), RECEIPT_FIELDS)

    def test_previews_write_no_receipt_line_and_no_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _script(tmp, "ok.py", "pass\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [ok]})
            previewed = git_flow.start(clone, "prev")
            self.assertEqual(previewed["outcome"], "pass", previewed["refusal"])
            self.assertFalse(previewed["receipt_written"])
            self.assertFalse((clone / "_log").exists())
            git_flow.start(clone, "prev", apply=True)
            _write(clone, "notes/p.md", "p\n")
            self.assertFalse(git_flow.save(clone)["receipt_written"])
            self.assertFalse(git_flow.check(clone)["receipt_written"])
            self.assertEqual(len(_receipt_lines(clone)), 1)  # only the applied start
            self.assertEqual(_run(clone, "log", "-1", "--format=%s").stdout.strip(), "add manifest")
            self.assertIn("notes/p.md", git_ops.list_changed_paths(clone))

    def test_a_receipt_write_failure_does_not_fail_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _script(tmp, "ok.py", "pass\n")
            clone, _remote = _make_flow_repo(tmp, git={"checks": [ok]})
            # A directory where the log file belongs makes every append fail.
            (clone / "_log" / "runs" / "git-flow.ndjson").mkdir(parents=True)
            started = git_flow.start(clone, "nolog", apply=True)
            self.assertEqual(started["outcome"], "pass", started["refusal"])
            self.assertFalse(started["receipt_written"])
            _write(clone, "notes/a.md", "a\n")
            head_before = _run(clone, "rev-parse", "HEAD").stdout.strip()
            saved = git_flow.save(clone, apply=True)
            self.assertEqual(saved["outcome"], "pass", saved["refusal"])
            self.assertFalse(saved["receipt_written"])
            self.assertNotEqual(_run(clone, "rev-parse", "HEAD").stdout.strip(), head_before)
            checked = git_flow.check(clone, apply=True)
            self.assertEqual(checked["outcome"], "pass", checked["refusal"])
            self.assertFalse(checked["receipt_written"])

    def test_after_a_passing_save_the_tree_is_clean_and_start_is_not_refused_as_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            # The repo's real ignore rules, copied from its committed .gitignore.
            (clone / ".gitignore").write_bytes((SYNC_DIR.parent / ".gitignore").read_bytes())
            _run(clone, "add", ".gitignore")
            _run(clone, "commit", "-m", "real ignore rules")
            self.assertEqual(git_flow.start(clone, "one", apply=True)["outcome"], "pass")
            self.assertTrue((clone / "_log" / "runs" / "git-flow.ndjson").exists())
            ignored = _run(clone, "check-ignore", "-q", "_log/runs/git-flow.ndjson")
            self.assertEqual(ignored.returncode, 0)
            _write(clone, "notes/a.md", "a\n")
            saved = git_flow.save(clone, apply=True)
            self.assertEqual(saved["outcome"], "pass", saved["refusal"])
            committed = _run(clone, "show", "--name-only", "--format=", "HEAD").stdout
            self.assertNotIn("git-flow.ndjson", committed)
            # git_ops.commit's own per-commit receipt (_log/sync-receipt-*.json, tracked
            # by design in this repo) is all that is left; the ndjson log is not listed.
            status = _run(clone, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
            self.assertEqual(len(status), 1, status)
            self.assertRegex(status[0], r"^\?\? _log/sync-receipt-[0-9TZ-]+\.json$")
            again = git_flow.start(clone, "two", apply=True)
            self.assertEqual(again["outcome"], "pass", again["refusal"])
            self.assertEqual(len(_receipt_lines(clone)), 3)

    def test_the_log_is_never_listed_or_committed_even_with_no_ignore_rule_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _run(clone, "rm", "-q", ".gitignore")
            _run(clone, "commit", "-m", "drop ignore rules")
            self.assertEqual(git_flow.start(clone, "bare", apply=True)["outcome"], "pass")
            self.assertTrue((clone / "_log" / "runs" / "git-flow.ndjson").exists())
            self.assertIn("_log/", _run(clone, "status", "--porcelain").stdout)  # git itself sees it
            self.assertEqual(git_flow._relevant_paths(clone), [])
            _write(clone, "notes/a.md", "a\n")
            preview = git_flow.save(clone)
            self.assertEqual(preview["outcome"], "pass", preview["refusal"])
            self.assertNotIn("git-flow.ndjson", preview["preview"])
            saved = git_flow.save(clone, apply=True)
            self.assertEqual(saved["outcome"], "pass", saved["refusal"])
            committed = _run(clone, "show", "--name-only", "--format=", "HEAD").stdout
            self.assertNotIn("_log", committed)
            again = git_flow.start(clone, "bare2", apply=True)
            self.assertEqual(again["outcome"], "pass", again["refusal"])


_HYGIENE_ROW_FILES = (  # exactly the files this row changes (spec criterion 5)
    ".gitignore",
    "sync/GIT_FLOW.md",
    "sync/git_flow.py",
    "sync/tests/test_git_flow.py",
    "sync/git_ops.py",
)
_HYGIENE_ASCII_SUFFIXES = (".py", ".ps1", ".sh", ".bat", ".cmd")
_HYGIENE_ASCII_NAMES = ("GIT_FLOW.md", ".gitignore")


def _hygiene_findings(name, data):
    """Return 'name:line: message' strings for every hygiene violation in the
    bytes `data` of file `name`. Never includes the offending text, only the
    line number and the code point."""
    if b"\x00" in data:
        return []  # binary file: not a text file this rule covers
    ascii_only = name.endswith(_HYGIENE_ASCII_SUFFIXES) or os.path.basename(name) in _HYGIENE_ASCII_NAMES
    em_dash = chr(0x2014).encode("utf-8")
    findings = []
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if em_dash in raw:
            findings.append("%s:%d: em dash U+2014" % (name, number))
        if ascii_only:
            for char in raw.decode("utf-8", "replace"):
                if ord(char) > 127:
                    findings.append("%s:%d: non-ASCII U+%04X in a file that must stay ASCII"
                                    % (name, number, ord(char)))
                    break
    return findings


class TestHygiene(unittest.TestCase):
    """Success criterion 5: no em dash (U+2014) in any file this row changes,
    and the scripts stay pure ASCII (a non-ASCII .py breaks PowerShell 5.1
    launchers on this laptop)."""

    ROOT = SYNC_DIR.parent

    def test_the_checker_flags_an_em_dash_with_file_and_line_only(self):
        data = ("ok line\nbad " + chr(0x2014) + " line\n").encode("utf-8")
        findings = _hygiene_findings("sync/x.md", data)
        self.assertEqual(findings, ["sync/x.md:2: em dash U+2014"])

    def test_the_checker_flags_non_ascii_in_a_script_but_allows_it_elsewhere(self):
        data = ("caf" + chr(0xE9) + "\n").encode("utf-8")
        self.assertEqual(_hygiene_findings("sync/x.py", data),
                         ["sync/x.py:1: non-ASCII U+00E9 in a file that must stay ASCII"])
        self.assertEqual(_hygiene_findings("sync/notes.txt", data), [])

    def test_the_checker_reports_an_em_dash_in_a_script_on_both_counts(self):
        data = ("x = 1  # " + chr(0x2014) + "\n").encode("utf-8")
        self.assertEqual(len(_hygiene_findings("sync/x.py", data)), 2)

    def test_every_listed_row_file_exists(self):
        for name in _HYGIENE_ROW_FILES:
            with self.subTest(path=name):
                self.assertTrue((self.ROOT / name).is_file(), "missing row file: " + name)

    def test_no_em_dash_and_scripts_stay_ascii_in_every_changed_file(self):
        for name in _HYGIENE_ROW_FILES:
            with self.subTest(path=name):
                findings = _hygiene_findings(name, (self.ROOT / name).read_bytes())
                self.assertEqual(findings, [], "; ".join(findings))


# --- final fix wave: policy files, protected main, message scan, renames -----


def _planted_aws():
    return "AKIA" + "R" * 16  # built at runtime, never a literal here


def _commit_files(clone, ref="HEAD"):
    return _run(clone, "show", "--name-only", "--no-renames", "--pretty=format:", ref).stdout.split()


class TestProtectedMainCannotBeUnguarded(unittest.TestCase):
    SENTENCE = "`main` is protected; run `start <slug>` to work on a branch of your own."

    def test_a_manifest_edit_that_empties_protected_is_refused_on_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _write(clone, "notes/a.md", "a\n")
            manifest = json.loads((clone / "siteoffice.json").read_text(encoding="utf-8"))
            manifest["settings"] = {"git": {"protected": []}}
            (clone / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
            head = _head(clone)
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "main").stdout.strip(), head)

    def test_an_empty_protected_list_in_the_committed_manifest_still_guards_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"protected": []})
            _write(clone, "notes/a.md", "a\n")
            head = _head(clone)
            for apply in (False, True):
                result = git_flow.save(clone, apply=apply)
                self.assertEqual(result["outcome"], "refused")
                self.assertEqual(result["refusal"], self.SENTENCE)
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_a_custom_main_is_protected_even_when_it_is_not_in_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"main": "trunk", "protected": ["release"]})
            _run(clone, "checkout", "-b", "trunk")
            _write(clone, "notes/a.md", "a\n")
            result = git_flow.save(clone)
            self.assertEqual(result["outcome"], "refused")
            self.assertTrue(result["refusal"].startswith("`trunk` is protected;"))

    def test_the_guard_ignores_case_only_when_the_windows_flag_is_set(self):
        for name in ("Main", "MAIN", "Master"):
            with self.subTest(name=name, windows=True), mock.patch.object(git_flow, "IS_WINDOWS", True):
                with self.assertRaises(git_flow.Refusal) as caught:
                    git_flow.assert_not_protected(name, ["main", "master"])
                self.assertEqual(
                    caught.exception.sentence,
                    f"`{name}` is protected; run `start <slug>` to work on a branch of your own.")
            with self.subTest(name=name, windows=False), mock.patch.object(git_flow, "IS_WINDOWS", False):
                git_flow.assert_not_protected(name, ["main", "master"])
        with mock.patch.object(git_flow, "IS_WINDOWS", True):
            git_flow.assert_not_protected("work/x/Main", ["main"])
            with self.assertRaises(git_flow.Refusal):
                git_flow.assert_not_protected("release", ["RELEASE"])

    def test_save_refuses_a_case_alias_of_main_when_the_windows_flag_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "notes/a.md", "a\n")
            head = _head(clone)
            with mock.patch.object(git_flow, "IS_WINDOWS", True), \
                    mock.patch.object(git_flow, "_current_branch_state", return_value=("branch", "Main")):
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused")
            self.assertTrue(result["refusal"].startswith("`Main` is protected;"))
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    @unittest.skipUnless(git_flow.IS_WINDOWS, "needs a case-insensitive file system")
    def test_a_loose_Main_ref_shadowing_the_packed_main_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            main_sha = _head(clone)
            _run(clone, "pack-refs", "--all")
            _run(clone, "checkout", "-b", "Main")
            self.assertEqual(_run(clone, "symbolic-ref", "HEAD").stdout.strip(), "refs/heads/Main")
            _write(clone, "notes/a.md", "a\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertEqual(_run(clone, "rev-parse", "main").stdout.strip(), main_sha)
            self.assertEqual(_head(clone), main_sha)
            self.assertEqual(_staged(clone), [])


class TestSavePolicyFiles(unittest.TestCase):
    """A save whose change set holds a policy file is refused whole: a policy
    change needs a deliberate commit by a human, not an agent save."""

    MARKER = "POLICY-CONTENT-MARKER-77"
    OWNERS = {"owners": {"theirs": "someone-else"}}

    def _repo(self, tmp, git=None, extra=None, seed=None):
        clone, _remote = _make_flow_repo(tmp, git=git, extra=extra)
        _on_branch(clone)
        for rel, text in (seed or {}).items():
            _write(clone, rel, text)
            _run(clone, "add", "--", rel)
        if seed:
            _run(clone, "commit", "-m", "seed")
        return clone

    def _assert_refused(self, clone, result, head, names):
        self.assertEqual(result["outcome"], "refused", result["refusal"])
        for name in names:
            self.assertIn(f"`{name}`", result["refusal"])
        self.assertIn("deliberate commit by a human", result["refusal"])
        self.assertIn("not an agent save", result["refusal"])
        self.assertNotIn(self.MARKER, result["refusal"] + result["preview"] + result["amber"])
        self.assertEqual(_head(clone), head)
        self.assertEqual(_staged(clone), [])

    def _both_modes(self, clone, names):
        head = _head(clone)
        preview = git_flow.save(clone)
        self._assert_refused(clone, preview, head, names)
        self.assertIsNone(preview["receipt"])
        applied = git_flow.save(clone, apply=True)
        self._assert_refused(clone, applied, head, names)
        receipt = _receipt_lines(clone)[-1]
        self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("refused", 2))
        self.assertNotIn(self.MARKER, json.dumps(receipt))
        return applied

    def test_an_edited_root_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "notes/a.md", "a\n")
            manifest = json.loads((clone / "siteoffice.json").read_text(encoding="utf-8"))
            manifest["settings"] = {"git": {"protected": []}, "note": self.MARKER}
            (clone / "siteoffice.json").write_text(json.dumps(manifest), encoding="utf-8")
            self._both_modes(clone, ["siteoffice.json"])
            self.assertNotIn("notes/a.md", _run(clone, "log", "-1", "--name-only").stdout)

    def test_a_planted_child_manifest_cannot_lift_an_owner_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, extra=self.OWNERS, seed={"theirs/f.txt": "v1\n"})
            _write(clone, "theirs/f.txt", "v2 by the wrong owner\n")
            _write(clone, "theirs/siteoffice.json", "{}")
            self._both_modes(clone, ["theirs/siteoffice.json"])
            self.assertEqual(_run(clone, "show", "HEAD:theirs/f.txt").stdout, "v1\n")

    def test_emptying_the_owners_map_in_the_same_save_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, extra=self.OWNERS, seed={"theirs/f.txt": "v1\n"})
            _write(clone, "theirs/f.txt", "v2 by the wrong owner\n")
            (clone / "siteoffice.json").write_text(json.dumps({"owners": {}}), encoding="utf-8")
            self._both_modes(clone, ["siteoffice.json"])
            self.assertEqual(_run(clone, "show", "HEAD:theirs/f.txt").stdout, "v1\n")

    def test_an_edited_denylist_is_refused_default_and_configured_paths(self):
        for git, rel in ((None, ".git-denylist"), ({"denylist": "conf/deny.txt"}, "conf/deny.txt")):
            with self.subTest(denylist=rel), tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp, git=git, seed={rel: "vault/*\n"})
                _write(clone, rel, f"# {self.MARKER}\n")
                _write(clone, "vault/x.txt", "plain\n")
                self._both_modes(clone, [rel])

    def test_an_edited_gitignore_is_refused_at_any_depth(self):
        for rel in (".gitignore", "sub/.gitignore"):
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                _write(clone, rel, f"# {self.MARKER}\nalice/siteoffice.json\n")
                _write(clone, "notes/a.md", "a\n")
                self._both_modes(clone, [rel])

    def test_a_deleted_manifest_and_a_case_variant_name_are_policy_files_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            (clone / "siteoffice.json").unlink()
            _write(clone, "notes/a.md", "a\n")
            self._both_modes(clone, ["siteoffice.json"])
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "sub/SiteOffice.JSON", "{}")
            self._both_modes(clone, ["sub/SiteOffice.JSON"])

    def test_every_policy_file_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, seed={".git-denylist": "a\n"})
            _write(clone, ".git-denylist", "b\n")
            _write(clone, "x/siteoffice.json", "{}")
            _write(clone, ".gitignore", "z\n")
            self._both_modes(clone, [".git-denylist", "x/siteoffice.json", ".gitignore"])

    def test_names_that_only_look_like_policy_files_and_log_paths_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            _write(clone, "siteoffice.json.bak", "x\n")
            _write(clone, "my-siteoffice.json.txt", "x\n")
            _write(clone, "notes/gitignore", "x\n")
            _write(clone, "_log/extra/siteoffice.json", "{}")
            result = git_flow.save(clone, scope="notes", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(
                sorted(_commit_files(clone)),
                ["my-siteoffice.json.txt", "notes/gitignore", "siteoffice.json.bak"])

    def test_the_policy_check_runs_before_the_owner_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, extra=self.OWNERS)
            _write(clone, "theirs/f.txt", "x\n")  # would be left out by the owner filter
            _write(clone, "siteoffice.json", "{}")
            result = git_flow.save(clone)
            self.assertIn("deliberate commit by a human", result["refusal"])
            self.assertNotIn("left out", result["refusal"])


class TestSaveMessageScan(unittest.TestCase):
    def _repo(self, tmp):
        clone, _remote = _make_flow_repo(tmp)
        _on_branch(clone)
        _write(clone, "notes/a.md", "a\n")
        return clone

    def _assert_clean_refusal(self, clone, result, head, field, rule, secret):
        self.assertEqual(result["outcome"], "refused", result)
        self.assertIn(f"commit {field}", result["refusal"])
        self.assertIn(f"rule: {rule}", result["refusal"])
        self.assertIn("Nothing was staged", result["refusal"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(_head(clone), head)
        self.assertEqual(_staged(clone), [])
        self.assertNotIn(secret, _run(clone, "log", "--all", "--format=%B").stdout)

    def test_a_secret_in_the_subject_is_refused_naming_the_field_and_the_rule(self):
        secret = _planted_aws()
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                clone = self._repo(tmp)
                head = _head(clone)
                result = git_flow.save(clone, scope="notes", subject="add " + secret, apply=apply)
                self._assert_clean_refusal(clone, result, head, "subject", "aws-access-key", secret)
                if apply:
                    lines = (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8")
                    self.assertNotIn(secret, lines)
                    self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 2)

    def test_a_lower_case_sk_token_in_the_scope_is_refused(self):
        token = "sk-" + "a" * 24
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = _head(clone)
            result = git_flow.save(clone, scope=token, subject="add a note", apply=True)
            self._assert_clean_refusal(clone, result, head, "scope", "sk-key", token)

    def test_a_secret_shaped_inferred_scope_is_refused_too(self):
        # The directory that becomes the scope is part of the file name, so the
        # file-name scan (which runs first) refuses it; the message scan stays behind it.
        token = "sk-" + "b" * 24
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, token + "/a.md", "a\n")
            head = _head(clone)
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused", result)
            self.assertIn("a file name (rule: sk-key)", result["refusal"])
            self.assertNotIn(token, json.dumps(result))
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])
            self.assertNotIn(token, _run(clone, "log", "--all", "--format=%B").stdout)

    def test_the_cli_refuses_a_secret_subject_without_printing_it(self):
        secret = _planted_aws()
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = _head(clone)
            code, out = _cli(["save", "--apply", "--repo", str(clone), "--scope", "notes",
                              "--subject", "add " + secret])
            self.assertEqual(code, 2, out)
            self.assertNotIn(secret, out)
            self.assertIn("commit subject", out)
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_a_clean_subject_and_scope_still_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            result = git_flow.save(clone, scope="notes", subject="add a note", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])

    def test_the_message_scan_reuses_the_file_scan_rules(self):
        self.assertEqual(git_flow.text_hits("add " + _planted_aws()), ["aws-access-key"])
        self.assertEqual(git_flow.text_hits("sk-" + "c" * 24), ["sk-key"])
        self.assertEqual(git_flow.text_hits("add a note"), [])
        self.assertEqual(git_flow.text_hits("A\x00K\x00I\x00A\x00" + "R\x00" * 16), ["aws-access-key"])


EXIT_OF = {"pass": 0, "refused": 2, "blocked": 3}


class TestSaveFailureNeverLeavesStagedPaths(unittest.TestCase):
    def _repo(self, tmp, count=3):
        clone, _remote = _make_flow_repo(tmp)
        _on_branch(clone)
        for index in range(count):
            _write(clone, f"notes/n{index}.md", "n\n")
        return clone

    def test_an_oserror_after_git_add_is_blocked_with_a_sentence_and_nothing_staged(self):
        def failing(repo_dir, message, *args, **kwargs):
            git_ops.run_git(repo_dir, ["--literal-pathspecs", "add", "--", *kwargs["only_paths"]])
            raise OSError(206, "The filename or extension is too long")

        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = _head(clone)
            with mock.patch.object(git_ops, "commit", side_effect=failing):
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertTrue(result["refusal"].strip())
            self.assertIn("nothing was committed", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_head(clone), head)
            receipt = _receipt_lines(clone)[-1]
            self.assertEqual((receipt["outcome"], receipt["exit_code"]), ("blocked", 3))
            self.assertTrue(receipt["refusal"])

    def test_an_oserror_while_building_the_message_is_blocked_before_anything_is_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp)
            head = _head(clone)
            with mock.patch.object(git_flow, "diff_stat_body", side_effect=OSError(206, "too long")):
                result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "blocked")
            self.assertIn("nothing was staged", result["refusal"])
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], 3)

    def test_unstage_works_in_batches_when_one_long_command_line_would_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, count=0)
            paths = [f"notes/n{index:03d}.md" for index in range(60)]
            for path in paths:
                _write(clone, path, "n\n")
            _run(clone, "add", "--", *paths)
            self.assertEqual(len(_staged(clone)), 60)
            real = git_ops.run_git

            def limited(repo_dir, args, *rest, **kwargs):
                if "reset" in args and len(args) > 30:
                    raise OSError(206, "The filename or extension is too long")
                return real(repo_dir, args, *rest, **kwargs)

            with mock.patch.object(git_ops, "run_git", side_effect=limited):
                note = git_flow._unstage(clone, paths)
            self.assertEqual(note, "")
            self.assertEqual(_staged(clone), [])

    def test_a_failing_unstage_never_raises(self):
        with mock.patch.object(git_ops, "run_git", side_effect=OSError(5, "denied")):
            note = git_flow._unstage(pathlib.Path(tempfile.gettempdir()), ["a", "b"])
        self.assertIn("could not unstage", note)

    @unittest.skipUnless(git_flow.IS_WINDOWS, "the command line limit is a Windows one")
    def test_312_untracked_files_of_100_characters_leave_nothing_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._repo(tmp, count=0)
            for index in range(312):
                _write(clone, f"d/f{index:04d}_" + "a" * 88 + ".txt", "ok\n")
            head = _head(clone)
            result = git_flow.save(clone, apply=True)
            self.assertNotEqual(result["outcome"], "pass")
            self.assertTrue(result["refusal"].strip())
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_receipt_lines(clone)[-1]["exit_code"], EXIT_OF[result["outcome"]])


class TestRenames(unittest.TestCase):
    def test_list_changed_paths_lists_a_rename_as_a_delete_and_an_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _write(clone, "old.txt", "some longer body so git sees a rename\n" * 5)
            _run(clone, "add", "--", "old.txt")
            _run(clone, "commit", "-m", "seed")
            _run(clone, "mv", "old.txt", "new.txt")
            self.assertEqual(
                sorted(git_ops.list_changed_paths(clone, split_renames=True)), ["new.txt", "old.txt"])
            self.assertEqual(git_ops.list_changed_paths(clone), ["new.txt"])  # the default view is unchanged

    def test_save_after_git_mv_commits_the_deletion_and_the_addition(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "notes/old.md", "some longer body so git sees a rename\n" * 5)
            _run(clone, "add", "--", "notes/old.md")
            _run(clone, "commit", "-m", "seed")
            _run(clone, "mv", "notes/old.md", "notes/new.md")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(sorted(_commit_files(clone)), ["notes/new.md", "notes/old.md"])
            tree = _run(clone, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
            self.assertIn("notes/new.md", tree)
            self.assertNotIn("notes/old.md", tree)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "status", "--porcelain", "-uno").stdout, "")

    def test_the_old_path_of_a_rename_is_vetted_by_the_owner_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, extra={"owners": {"theirs": "someone-else"}})
            _on_branch(clone)
            _write(clone, "theirs/b.md", "some longer body so git sees a rename\n" * 5)
            _run(clone, "add", "--", "theirs/b.md")
            _run(clone, "commit", "-m", "seed")
            (clone / "mine").mkdir()
            _run(clone, "mv", "theirs/b.md", "mine/b.md")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual([item["path"] for item in result["left_out"]], ["theirs/b.md"])
            self.assertEqual(_commit_files(clone), ["mine/b.md"])
            self.assertIn("theirs/b.md", _run(clone, "ls-tree", "-r", "--name-only", "HEAD").stdout)


class TestCommitOfDeletedPaths(unittest.TestCase):
    """git_ops.commit no longer runs `git add` on a path gone from the working tree."""

    def _seed(self, tmp):
        clone, _remote = _init_repo_pair(pathlib.Path(tmp))
        (clone / "old.txt").write_text("some longer body so git sees a rename\n" * 5, encoding="utf-8")
        _run(clone, "add", "--", "old.txt")
        _run(clone, "commit", "-m", "seed")
        return clone

    def test_an_unstaged_deletion_is_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._seed(tmp)
            (clone / "old.txt").unlink()
            result = git_ops.commit(clone, "drop old", "wilco", _no_owner, only_paths=["old.txt"])
            self.assertEqual(result["committed"], ["old.txt"])
            self.assertEqual(_run(clone, "ls-tree", "--name-only", "HEAD").stdout.split(), ["hello.md"])
            self.assertEqual(_run(clone, "status", "--porcelain", "-uno").stdout, "")

    def test_a_staged_deletion_and_its_new_path_are_committed_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = self._seed(tmp)
            _run(clone, "mv", "old.txt", "new.txt")
            result = git_ops.commit(
                clone, "rename", "wilco", _no_owner, only_paths=["old.txt", "new.txt"])
            self.assertEqual(sorted(result["committed"]), ["new.txt", "old.txt"])
            self.assertEqual(sorted(_run(clone, "ls-tree", "--name-only", "HEAD").stdout.split()),
                             ["hello.md", "new.txt"])
            self.assertEqual(_run(clone, "status", "--porcelain", "-uno").stdout, "")


class TestLibraryGitRootGuard(unittest.TestCase):
    """The nested-root guard lives in _run_step, so the library API is covered."""

    def test_every_step_refuses_a_nested_directory_before_settings_or_receipt(self):
        calls = [
            ("save", lambda repo, apply: git_flow.save(repo, apply=apply)),
            ("start", lambda repo, apply: git_flow.start(repo, "s-2", apply=apply)),
            ("check", lambda repo, apply: git_flow.check(repo, apply=apply)),
        ]
        for name, call in calls:
            for apply in (False, True):
                with self.subTest(step=name, apply=apply), tempfile.TemporaryDirectory() as tmp:
                    clone, dept = TestCliGitRootGuard._nested(self, tmp)
                    head, status, receipts = _head(clone), _status(clone), _receipt_files(clone)
                    with mock.patch.object(git_flow, "load_git_settings") as loaded:
                        result = call(dept, apply)
                    loaded.assert_not_called()
                    self.assertEqual(result["outcome"], "refused")
                    self.assertIn("git root", result["refusal"])
                    self.assertIn("--repo", result["refusal"])
                    self.assertIsNone(result["receipt"])
                    self.assertFalse(result["receipt_written"])
                    self.assertEqual(_head(clone), head)
                    self.assertEqual(_staged(clone), [])
                    self.assertEqual(_status(clone), status)
                    self.assertEqual(_receipt_files(clone), receipts)
                    self.assertFalse((dept / "_log").exists())

    def test_the_git_root_itself_is_not_refused_by_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            result = git_flow.check(clone)
            self.assertEqual(result["outcome"], "pass")

    def test_the_cli_checks_the_root_once_and_prints_one_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, dept = TestCliGitRootGuard._nested(self, tmp)
            with mock.patch.object(git_flow, "_git_toplevel", wraps=git_flow._git_toplevel) as spy:
                code, out = _cli(["save", "--apply", "--repo", str(dept)])
            self.assertEqual(spy.call_count, 1)
            self.assertEqual(code, 2, out)
            self.assertEqual(len(out.strip().splitlines()), 2, out)
            self.assertEqual(out.count("git root"), 1)


class TestNoUnusedImports(unittest.TestCase):
    def test_typing_dict_is_not_imported(self):
        self.assertFalse(hasattr(git_flow, "Dict"))


# --- iteration 2: inherited settings, denylist union, assignment forms, file names ---


def _parent_repo(tmp, parent_git, child_git=None, seed=None):
    """A flow repo whose manifest inherits from a manifest OUTSIDE the repo (the
    clone's parent folder), the way the real repos inherit from C:\\work. The
    parent block is written after the repo is committed, like an edit of the
    parent the repo's own history never sees."""
    extra = {"parent": ".."}
    clone, _remote = _make_flow_repo(tmp, git=child_git, extra=extra)
    for rel, text in (seed or {}).items():
        _write(clone, rel, text)
        _run(clone, "add", "--", rel)
    if seed:
        _run(clone, "commit", "-m", "seed")
    (pathlib.Path(tmp) / "siteoffice.json").write_text(
        json.dumps({"settings": {"git": parent_git}}), encoding="utf-8")
    return clone


class TestInheritedSettingsCannotUnguardMain(unittest.TestCase):
    def test_a_parent_manifest_that_repoints_main_and_empties_protected_still_guards_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, {"main": "trunk", "protected": []})
            settings, _notes = git_flow.load_git_settings(clone)
            self.assertEqual((settings["main"], settings["protected"]), ("trunk", []))
            _write(clone, "notes/a.md", "a\n")
            head = _head(clone)
            for apply in (False, True):
                result = git_flow.save(clone, apply=apply)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertTrue(result["refusal"].startswith("`main` is protected;"), result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])
            self.assertEqual(_run(clone, "rev-parse", "main").stdout.strip(), head)

    def test_master_is_always_protected_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, {"main": "trunk", "protected": ["release"]})
            _run(clone, "checkout", "-b", "master")
            _write(clone, "notes/a.md", "a\n")
            head = _head(clone)
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertTrue(result["refusal"].startswith("`master` is protected;"), result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_the_effective_protected_set_is_the_union(self):
        settings = {"main": "trunk", "protected": ["release"]}
        self.assertEqual(git_flow.effective_protected(settings), {"main", "master", "trunk", "release"})
        settings = {"main": "main", "protected": []}
        self.assertEqual(git_flow.effective_protected(settings), {"main", "master"})

    def test_the_literal_defaults_follow_the_windows_flag_for_case(self):
        settings = {"main": "trunk", "protected": []}
        for name in ("Main", "MASTER"):
            with self.subTest(name=name, windows=True), mock.patch.object(git_flow, "IS_WINDOWS", True):
                with self.assertRaises(git_flow.Refusal):
                    git_flow.assert_not_protected(name, git_flow.effective_protected(settings))
            with self.subTest(name=name, windows=False), mock.patch.object(git_flow, "IS_WINDOWS", False):
                git_flow.assert_not_protected(name, git_flow.effective_protected(settings))


class TestInheritedDenylistCannotBeRepointed(unittest.TestCase):
    SEED = {".git-denylist": "private/*\n"}

    def _changes(self, clone):
        _write(clone, "notes/a.md", "a\n")
        _write(clone, "private/plan.txt", "the plan\n")

    def test_a_parent_repointing_the_denylist_to_a_missing_file_still_leaves_out_the_default_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, {"denylist": "nope.txt"}, seed=self.SEED)
            _on_branch(clone)
            self._changes(clone)
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_commit_files(clone), ["notes/a.md"])
            self.assertEqual([item["path"] for item in result["left_out"]], ["private/plan.txt"])
            self.assertNotIn("private/plan.txt", _run(clone, "ls-tree", "-r", "--name-only", "HEAD").stdout)

    def test_the_default_denylist_file_stays_a_policy_file_when_the_path_is_repointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, {"denylist": "nope.txt"}, seed=self.SEED)
            _on_branch(clone)
            _write(clone, ".git-denylist", "# emptied\n")
            _write(clone, "private/plan.txt", "the plan\n")
            head = _head(clone)
            for apply in (False, True):
                result = git_flow.save(clone, apply=apply)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertIn("`.git-denylist`", result["refusal"])
                self.assertIn("deliberate commit by a human", result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_the_configured_denylist_and_the_default_both_apply_and_both_are_policy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"denylist": "conf/deny.txt"})
            for rel, text in ((".git-denylist", "private/*\n"), ("conf/deny.txt", "secret-notes/*\n")):
                _write(clone, rel, text)
                _run(clone, "add", "--", rel)
            _run(clone, "commit", "-m", "seed")
            _on_branch(clone)
            _write(clone, "notes/a.md", "a\n")
            _write(clone, "private/plan.txt", "x\n")
            _write(clone, "secret-notes/n.txt", "x\n")
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(_commit_files(clone), ["notes/a.md"])
            self.assertEqual(
                sorted(item["path"] for item in result["left_out"]), ["private/plan.txt", "secret-notes/n.txt"])
            for rel in (".git-denylist", "conf/deny.txt"):
                with self.subTest(policy=rel):
                    _write(clone, rel, "# edited\n")
                    _write(clone, "notes/b.md", "b\n")
                    refused = git_flow.save(clone)
                    self.assertEqual(refused["outcome"], "refused")
                    self.assertIn(f"`{rel}`", refused["refusal"])
                    _run(clone, "checkout", "--", rel)

    def test_an_unreadable_default_denylist_refuses_even_when_another_path_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp, git={"denylist": "conf/deny.txt"})
            _write(clone, "conf/deny.txt", "x/*\n")
            _run(clone, "add", "--", "conf/deny.txt")
            _run(clone, "commit", "-m", "seed")
            _on_branch(clone)
            (clone / ".git-denylist").mkdir()
            _write(clone, "notes/a.md", "a\n")
            head = _head(clone)
            result = git_flow.save(clone, apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertIn("denylist unreadable: .git-denylist", result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_the_union_helpers_list_each_path_once_and_a_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _write(clone, ".git-denylist", "a/*\n")
            self.assertEqual(git_flow.denylist_paths({"denylist": ".git-denylist"}), [".git-denylist"])
            self.assertEqual(
                git_flow.denylist_paths({"denylist": "nope.txt"}), [".git-denylist", "nope.txt"])
            self.assertEqual(git_flow.load_denylists(clone, [".git-denylist", "nope.txt"]), ["a/*"])


_VALUE = "".join(["hO", "rs", "eB", "at", "te", "ry", "St", "ap", "le"])  # 18 characters, built at runtime
_PREFIXES = ("b", "f", "r", "u", "rb", "br", "fr", "rf", "B", "F", "R", "U", "Rb", "bR", "FR", "rF")


def _assignment_forms():
    v = _VALUE
    forms = {
        "annotation": f'TOKEN: str = "{v}"',
        "annotation, generic type": f'key: Optional[str] = "{v}"',
        "annotation, dotted type, tight": f"token:typing.Final[str]='{v}'",
        "bytes prefix": f'SECRET_KEY = b"{v}"',
        "f prefix": f'password = f"{v}"',
        "walrus": f'if (api_key := "{v}"):',
        "walrus, single quote": f"while (secret:='{v}'):",
        "triple double quote": f'password = """{v}"""',
        "triple single quote": f"password = '''{v}'''",
        "triple quote opened, value on the same line": f'password = """{v}',
        "prefix and triple quote": f'password = r"""{v}"""',
        "annotation and prefix": f'TOKEN: bytes = rb"{v}"',
    }
    for prefix in _PREFIXES:
        forms[f"prefix {prefix}"] = f'password = {prefix}"{v}"'
        forms[f"prefix {prefix}, single quote"] = f"secret = {prefix}'{v}'"
    return forms


class TestSecretAssignmentForms(unittest.TestCase):
    def test_every_form_is_found_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for name, line in _assignment_forms().items():
                with self.subTest(form=name):
                    _write(clone, "f.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "f.txt"), ["secret-assignment"])
                    self.assertEqual(git_flow.text_hits(line), ["secret-assignment"])
                    self.assertNotIn(_VALUE, git_flow.redact(line))
                    self.assertNotIn(_VALUE, git_flow.redact("x " + line + " y"))
                    self.assertIn("[redacted]", git_flow.redact(line))

    def test_short_values_comments_and_look_alikes_still_pass(self):
        v = _VALUE
        harmless = [
            'TOKEN: str = "short"', 'password = b"abc"', 'password = f"abc"', "key = 'abc'",
            'key = "abc"', 'if (api_key := "abc"):', 'password = """short"""', "password = '''short'''",
            '# password = "short"', "# the token: str is kept in the vault, not in a file",
            "key: str", "password: Optional[str] = None", 'password = os.environ["APP_PASSWORD"]',
            'password = compute("a", "b")', 'password = b"' + "a" * 11 + '"',
            f"# password = {v}", f"the key: {v}", f"key: str = {v}", f"password = x{v}",
            f'password = xb"{v[:5]}"', 'password: "it\'s"',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in harmless:
                with self.subTest(line=line[:30]):
                    _write(clone, "h.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "h.txt"), [])
                    self.assertEqual(git_flow.redact(line), line)

    def test_the_new_pieces_stay_linear(self):
        shapes = [
            ":" * 100000, "key" + ":" * 100000, "key: " * 20000, "key:" * 25000, "key:" + "a" * 100000,
            "key = " + "b" * 100000, "key = " + "rb" * 50000, "key = " + "b" * 100000 + '"',
            "key: a" + " " * 100000, "key" + " " * 100000 + ":", "key: " + "a " * 50000,
            "key:=" * 20000, "key := " * 14000, 'key = """' * 11111, 'key = """' + "a" * 11,
            "key = '''" * 11111, 'key = """' + '"' * 100000, 'key = """' + "\\" * 100000,
            'key = """' + 'a"' * 50000, "key: " + "a[" * 50000, "key: " + "a." * 50000,
            "key:a" * 20000, ("password = b" + "b" * 50) * 1500, "a" * 50000 + ":" + "b" * 50000,
            "token: " + "a.b[c]" * 15000,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in shapes:
                with self.subTest(head=line[:14], size=len(line)):
                    _write(clone, "long.txt", line + "\n")
                    started = time.monotonic()
                    git_flow.content_hits(clone, "long.txt")
                    git_flow.redact(line)
                    git_flow.text_hits(line)
                    self.assertLess(time.monotonic() - started, 2.0)

    def test_each_form_refuses_the_save_and_the_value_reaches_no_output(self):
        saved = ("b", "rb", "F", "rF")  # the scan is proven for every prefix above; the save path for four
        for name, line in _assignment_forms().items():
            if name.startswith("prefix ") and name.split()[1].rstrip(",") not in saved:
                continue
            with self.subTest(form=name), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                _write(clone, "notes/cfg.txt", line + "\n")
                head = _head(clone)
                result = git_flow.save(clone, scope="notes", subject="add config", apply=True)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertIn("notes/cfg.txt", result["refusal"])
                self.assertIn("secret-assignment", result["refusal"])
                self.assertNotIn(_VALUE, json.dumps(result))
                self.assertNotIn(_VALUE, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), [])

    def test_the_cli_refuses_an_annotated_assignment_without_printing_the_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "notes/cfg.py", f'TOKEN: str = "{_VALUE}"\n')
            head = _head(clone)
            code, out = _cli(["save", "--apply", "--repo", str(clone), "--scope", "notes"])
            self.assertEqual(code, 2, out)
            self.assertNotIn(_VALUE, out)
            self.assertIn("secret-assignment", out)
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_a_failing_check_stderr_cannot_leak_a_new_form(self):
        # redact() is what _tail and the CLI print through: one form per group.
        for line in (f'TOKEN: str = "{_VALUE}"', f'password = f"{_VALUE}"', f'if (api_key := "{_VALUE}"):',
                     f'password = """{_VALUE}"""'):
            with self.subTest(line=line[:12]):
                self.assertNotIn(_VALUE, git_flow._tail("boom\n" + line + "\nend"))


class TestSecretShapedFileNames(unittest.TestCase):
    def _names(self):
        return {
            "aws": "AKIA" + "Z" * 16 + ".txt",
            "github": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + ".txt",
        }

    def test_a_secret_shaped_file_name_refuses_the_save_naming_the_field_and_rule_only(self):
        for kind, name in self._names().items():
            key = name[:-4]
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                _write(clone, "sync/" + name, "plain\n")
                head = _head(clone)
                for apply in (False, True):
                    result = git_flow.save(clone, scope="sync", apply=apply)
                    self.assertEqual(result["outcome"], "refused", result)
                    self.assertIn("a file name (rule: ", result["refusal"])
                    self.assertIn("rule: ", result["refusal"])
                    self.assertIn("file name", result["refusal"])
                    self.assertIn("renamed", result["refusal"])
                    self.assertIn("Nothing was staged", result["refusal"])
                    self.assertNotIn(key, json.dumps(result))
                    self.assertEqual(result["preview"], "")
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), [])
                self.assertNotIn(key, _run(clone, "log", "--all", "--format=%B").stdout)
                self.assertNotIn(key, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))

    def test_the_cli_prints_no_part_of_the_name(self):
        name = self._names()["aws"]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "sync/" + name, "plain\n")
            head = _head(clone)
            for apply in (False, True):
                code, out = _cli(["save", "--scope", "sync", "--repo", str(clone)] + (["--apply"] if apply else []))
                self.assertEqual(code, 2, out)
                self.assertIn("a file name (rule: ", out)
                self.assertNotIn(name[:-4], out)
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_a_clean_file_name_beside_the_body_scan_still_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "sync/notes.txt", "plain\n")
            result = git_flow.save(clone, scope="sync", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])

    def test_the_scan_covers_the_whole_built_message(self):
        name = self._names()["aws"]
        message = "chore(sync): update 1 file(s)\n\n sync/" + name + " | new file"
        self.assertEqual(git_flow.text_hits(message), ["aws-access-key"])
        self.assertEqual(git_flow.message_hits(message), [("body", "aws-access-key")])
        self.assertEqual(git_flow.message_hits("chore(sync): update 1 file(s)\n\n sync/a.txt | new file"), [])
        self.assertEqual(
            git_flow.message_hits("chore(" + "sk-" + "a" * 24 + "): x"), [("scope", "sk-key")])
        self.assertEqual(
            git_flow.message_hits("chore(sync): add " + name[:-4]), [("subject", "aws-access-key")])


class TestGitFlowNoteIteration2(unittest.TestCase):
    def test_the_note_states_the_new_rules(self):
        note = (SYNC_DIR / "GIT_FLOW.md").read_text(encoding="utf-8")
        for phrase in ("always protected", "union", "annotation", "walrus", "triple", "secret-shaped file name"):
            self.assertIn(phrase, note)


# --- iteration 3: every file name, wider assignment forms, checks only from inside the repo ---


def _name_key():
    return "AKIA" + "M" * 16  # built at runtime, never a literal here


def _lists_of_names(clone):
    return git_ops.list_changed_paths(clone, split_renames=True)


def _assert_nothing_leaked(test, clone, key, *outputs):
    """The key (whole, or its distinctive tail) is in no output, the receipt log,
    git log or HEAD tree."""
    for piece in (key, key[4:]):
        for out in outputs:
            test.assertNotIn(piece, out)
        test.assertNotIn(piece, _run(clone, "log", "--all", "--format=%B").stdout)
        test.assertNotIn(piece, _run(clone, "ls-tree", "-r", "--name-only", "HEAD").stdout)
        log = clone / "_log" / "runs" / "git-flow.ndjson"
        if log.exists():
            test.assertNotIn(piece, log.read_text(encoding="utf-8"))


class TestEveryFileNameIsScanned(unittest.TestCase):
    """Finding C1: the commit body lists at most 20 names, so a key-shaped name
    past the twentieth was committed. Every path name is scanned now."""

    def _many(self, clone, total, index, key):
        for j in range(total - 1):
            _write(clone, f"notes/b{j:02d}.md", f"note {j}\n")
        name = f"notes/a_{key}.md" if index == 0 else f"notes/b{index - 1:02d}_{key}.md"
        _write(clone, name, "plain\n")
        self.assertEqual(_lists_of_names(clone).index(name), index)
        return name

    def _assert_refused(self, clone, result, head, key):
        self.assertEqual(result["outcome"], "refused", result["refusal"])
        self.assertIn("a file name (rule: aws-access-key)", result["refusal"])
        self.assertIn("Nothing was staged", result["refusal"])
        self.assertEqual(result["preview"], "")
        self.assertEqual(_head(clone), head)
        self.assertEqual(_staged(clone), [])
        _assert_nothing_leaked(self, clone, key, json.dumps(result))

    def test_a_key_shaped_name_at_any_position_of_24_or_60_files_is_refused(self):
        key = _name_key()
        for total in (24, 60):
            for index in (0, 19, 20, total - 1):
                with self.subTest(total=total, index=index), tempfile.TemporaryDirectory() as tmp:
                    clone, _remote = _make_flow_repo(tmp)
                    _on_branch(clone)
                    self._many(clone, total, index, key)
                    head = _head(clone)
                    for apply in (False, True):
                        result = git_flow.save(clone, scope="notes", apply=apply)
                        self._assert_refused(clone, result, head, key)

    def test_the_cli_refuses_the_same_and_prints_no_part_of_the_name(self):
        key = _name_key()
        for index in (0, 20, 23):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                self._many(clone, 24, index, key)
                head = _head(clone)
                for apply in (False, True):
                    code, out = _cli(["save", "--scope", "notes", "--repo", str(clone)]
                                     + (["--apply"] if apply else []))
                    self.assertEqual(code, 2, out)
                    self.assertIn("a file name (rule: aws-access-key)", out)
                    self.assertEqual(_head(clone), head)
                    self.assertEqual(_staged(clone), [])
                    _assert_nothing_leaked(self, clone, key, out)

    def test_a_key_shaped_directory_component_is_refused(self):
        key = _name_key()
        for rel in (f"notes/{key}/a.md", f"notes/z_{key}/deep/a.md"):
            with self.subTest(rel=rel[:8]), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                for j in range(24):
                    _write(clone, f"notes/b{j:02d}.md", f"note {j}\n")
                _write(clone, rel, "plain\n")
                head = _head(clone)
                for apply in (False, True):
                    self._assert_refused(clone, git_flow.save(clone, scope="notes", apply=apply), head, key)

    def test_a_renamed_to_key_shaped_name_is_refused(self):
        key = _name_key()
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, "notes/old.md", "some longer body so git sees a rename\n" * 5)
            _run(clone, "add", "--", "notes/old.md")
            _run(clone, "commit", "-m", "seed")
            for j in range(24):
                _write(clone, f"notes/b{j:02d}.md", f"note {j}\n")
            _run(clone, "mv", "notes/old.md", f"notes/z_{key}.md")
            head, staged = _head(clone), _staged(clone)
            for apply in (False, True):
                result = git_flow.save(clone, scope="notes", apply=apply)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertIn("a file name (rule: aws-access-key)", result["refusal"])
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), staged)
                _assert_nothing_leaked(self, clone, key, json.dumps(result))

    def test_a_key_shaped_old_side_of_a_rename_or_a_deletion_is_refused_too(self):
        key = _name_key()
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, f"notes/{key}.md", "some longer body so git sees a rename\n" * 5)
            _run(clone, "add", "--", f"notes/{key}.md")
            _run(clone, "commit", "-m", "seed")
            _run(clone, "mv", f"notes/{key}.md", "notes/plain.md")
            head = _head(clone)
            result = git_flow.save(clone, scope="notes", apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertIn("a file name (rule: aws-access-key)", result["refusal"])
            self.assertEqual(_head(clone), head)
            self.assertNotIn(key, json.dumps(result))

    def test_a_secret_shaped_name_with_secret_content_is_refused_as_a_name_and_never_named(self):
        key = _name_key()
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            _write(clone, f"notes/{key}.md", PASSWORD_LINE + "\n")
            result = git_flow.save(clone, scope="notes", apply=True)
            self.assertEqual(result["outcome"], "refused", result["refusal"])
            self.assertIn("a file name (rule: aws-access-key)", result["refusal"])
            self.assertNotIn("hunter2", json.dumps(result))
            _assert_nothing_leaked(self, clone, key, json.dumps(result))

    def test_many_clean_names_still_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            _on_branch(clone)
            for j in range(60):
                _write(clone, f"notes/b{j:02d}.md", f"note {j}\n")
            result = git_flow.save(clone, scope="notes", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual(len(_commit_files(clone)), 60)

    def test_name_hits_uses_the_content_rules_and_returns_rule_ids_only(self):
        key = _name_key()
        sk = "sk-" + "n" * 24
        gh = GH_TOKEN
        self.assertEqual(git_flow.name_hits([f"notes/{key}.md"]), ["aws-access-key"])
        self.assertEqual(git_flow.name_hits(["a/b.md", f"x/{sk}.md", f"y/{gh}.md"]), ["sk-key", "github-token"])
        self.assertEqual(git_flow.name_hits([f"{key}/a.md", f"b/{key}.md"]), ["aws-access-key"])
        self.assertEqual(git_flow.name_hits(["notes/a.md", "sync/git_flow.py", "AKIA1234.md"]), [])
        self.assertEqual(git_flow.name_hits([]), [])


def _wider_forms():
    v = _VALUE
    return {
        "union with none": f'api_key: str | None = "{v}"',
        "dict of str": f'password: Dict[str, str] = "{v}"',
        "union of str": f'password: Union[str, None] = "{v}"',
        "annotated": f'x_token: Annotated[str, Doc] = "{v}"',
        "quoted forward reference": f'token: "str" = "{v}"',
        "typescript optional": f'password?: string = "{v}"',
        "typescript union": f"password: string | undefined = '{v}'",
        "generic in angle brackets": f'secret: Record<string, string> = "{v}"',
        "callable": f'token: Callable[[int], str] = "{v}"',
        "annotation with prefix": f'password: str | None = f"{v}"',
        "annotation, tabs": f'password:\tstr\t=\t"{v}"',
        "subscript, double quotes": f'os.environ["API_KEY"] = "{v}"',
        "subscript, single quotes": f"os.environ['API_KEY'] = '{v}'",
        "subscript, password": f'config["password"] = "{v}"',
        "subscript, single quoted password": f"config['password'] = '{v}'",
        "subscript, lower case secret": f'settings["db_secret"] = "{v}"',
        "subscript, nested": f"self.cfg['db']['Auth-Token'] = '{v}'",
        "subscript, tight": f'cfg["token"]="{v}"',
        "subscript with prefix": f'config["password"] = b"{v}"',
    }


def _wider_look_alikes():
    v = _VALUE
    return [
        'api_key: str | None = "short"', 'password: Dict[str, str] = "abc"', 'token: "str" = "short"',
        'password?: string = ""', "password: str", "password: Optional[str] = None",
        "token: str = get_token()", "api_key: str | None = os.environ.get('K')",
        "# password: str = 'short'", "# password = \"short\"", 'key = "abc"',
        'os.environ["HOME"] = "' + v + '"', "os.environ['PATH'] = '" + v + "'",
        'config["name"] = "' + v + '"', 'config["password"] = "short"',
        'config["password"] = os.environ["APP_PASSWORD"]', 'config["password"] == "' + v + '"',
        'os.environ["API_KEY"] = os.getenv("X")', 'settings["password"] = None',
        '{"token": "x", "note": "' + v + '"}', "def f(password: str, note: str = 'short'):",
        f"# the token: str is {v}", "the key: str | None is kept in the vault",
    ]


class TestWiderAssignmentForms(unittest.TestCase):
    """Findings I1 and I2: annotations with spaces, pipes, commas, quotes and
    brackets, and subscript targets, no longer step around the assignment rule."""

    def test_every_wider_form_is_found_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for name, line in _wider_forms().items():
                with self.subTest(form=name):
                    _write(clone, "f.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "f.txt"), ["secret-assignment"])
                    self.assertEqual(git_flow.text_hits(line), ["secret-assignment"])
                    self.assertNotIn(_VALUE, git_flow.redact(line))
                    self.assertNotIn(_VALUE, git_flow.redact("x " + line + " y"))
                    self.assertIn("[redacted]", git_flow.redact(line))

    def test_look_alikes_still_pass_and_redact_leaves_them_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in _wider_look_alikes():
                with self.subTest(line=line[:34]):
                    _write(clone, "h.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "h.txt"), [])
                    self.assertEqual(git_flow.redact(line), line)

    def test_the_wider_pieces_stay_linear(self):
        shapes = _adversarial_shapes()
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in shapes:
                with self.subTest(head=line[:14], size=len(line)):
                    _write(clone, "long.txt", line + "\n")
                    started = time.monotonic()
                    git_flow.content_hits(clone, "long.txt")
                    git_flow.redact(line)
                    git_flow.text_hits(line)
                    self.assertLess(time.monotonic() - started, 2.0)

    def test_a_wider_form_refuses_the_save_and_the_value_reaches_no_output(self):
        for name in ("union with none", "typescript optional", "subscript, double quotes",
                     "subscript, single quoted password"):
            line = _wider_forms()[name]
            with self.subTest(form=name), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                _write(clone, "notes/cfg.txt", line + "\n")
                head = _head(clone)
                for apply in (False, True):
                    result = git_flow.save(clone, scope="notes", subject="add config", apply=apply)
                    self.assertEqual(result["outcome"], "refused", result["refusal"])
                    self.assertIn("secret-assignment", result["refusal"])
                    self.assertNotIn(_VALUE, json.dumps(result))
                self.assertNotIn(_VALUE, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), [])

    def test_the_cli_refuses_an_annotation_and_a_subscript_without_printing_the_value(self):
        for line in (_wider_forms()["union with none"], _wider_forms()["subscript, double quotes"]):
            with self.subTest(line=line[:16]), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                _write(clone, "notes/cfg.py", line + "\n")
                head = _head(clone)
                code, out = _cli(["save", "--apply", "--repo", str(clone), "--scope", "notes"])
                self.assertEqual(code, 2, out)
                self.assertNotIn(_VALUE, out)
                self.assertIn("secret-assignment", out)
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), [])


def _adversarial_shapes():
    return [
        " " * 100000, "|" * 100000, "," * 100000, "[" * 100000, '"' * 100000, "'" * 100000,
        "key: " * 20000, "key:" * 25000, "key?:" * 20000, "key=" * 25000,
        "key:" + " " * 100000, "key:" + "|" * 100000, "key:" + "," * 100000,
        "key: " + "[" * 100000, "key:" + '"' * 100000, "key:" + "'" * 100000,
        "key: " + "a " * 50000, "key: " + "a," * 50000, "key: " + "a|" * 50000,
        "key: " + "str, " * 20000, 'key: "' * 20000, "key\"]" * 20000, "token" + "]" * 100000,
        "key" + '"' * 100000 + "]", 'key["' * 20000, "key: " + "a[" * 50000 + "=",
        "key:" + "a" * 100000 + "=", ("key: str, " * 10000) + '="' + "a" * 5,
        "key: " + " " * 100000 + '= "' + "a" * 11 + '"', "password: " + "b" * 199 + " = '" + "c" * 11 + "'",
        ("password: " + "a " * 100 + "=") * 300, ("key: a=") * 25000, 'key: "a" ' * 12000,
    ]


class TestChecksComeOnlyFromManifestsInsideTheRepo(unittest.TestCase):
    """Finding I3: a siteoffice.json above the repo could supply `checks`, which
    `check --apply` runs through a shell."""

    def _marker_repo(self, tmp, child_git=None):
        marker_cmd = _script(tmp, "mark.py", "open('marker', 'w').write('x')\n")
        clone = _parent_repo(tmp, {"checks": [marker_cmd]}, child_git=child_git)
        return clone, marker_cmd

    def test_checks_inherited_from_a_manifest_above_the_repo_do_not_run(self):
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                clone, marker_cmd = self._marker_repo(tmp)
                result = git_flow.check(clone, apply=apply)
                self.assertFalse((clone / "marker").exists())
                self.assertEqual(result["outcome"], "pass", result["refusal"])
                self.assertEqual(result["checks"], [])
                self.assertIn("0 checks configured", result["amber"])
                self.assertIn("inherited settings.git.checks were ignored", result["amber"])
                self.assertIn("amber: ", result["preview"])
                self.assertNotIn("mark.py", json.dumps(result))
                self.assertNotIn(marker_cmd, json.dumps(result))

    def test_the_cli_does_not_run_them_and_reports_the_amber_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _cmd = self._marker_repo(tmp)
            code, out = _cli(["check", "--apply", "--repo", str(clone)])
            self.assertEqual(code, 0, out)
            self.assertFalse((clone / "marker").exists())
            self.assertIn("inherited settings.git.checks were ignored", out)
            self.assertNotIn("mark.py", out)
            receipt = _receipt_lines(clone)[-1]
            self.assertEqual(receipt["checks"], [])
            self.assertNotIn("mark.py", json.dumps(receipt))

    def test_the_repos_own_checks_still_run_and_the_inherited_ones_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = _script(tmp, "own.py", "open('own-marker', 'w').write('x')\n")
            clone, _cmd = self._marker_repo(tmp, child_git={"checks": [own]})
            result = git_flow.check(clone, apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertEqual([record["exit"] for record in result["checks"]], [0])
            self.assertTrue((clone / "own-marker").exists())
            self.assertFalse((clone / "marker").exists())
            self.assertIn("inherited settings.git.checks were ignored", result["amber"])

    def test_no_note_when_the_inherited_block_has_no_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, {"main": "trunk", "checks": []})
            _settings, notes = git_flow.load_git_settings(clone)
            self.assertEqual(notes, [])

    def test_every_other_inherited_key_still_applies(self):
        parent = {
            "profile": "code", "main": "trunk", "protected": ["release"], "checks": ["echo hi"],
            "checkTimeoutSeconds": 42, "denylist": "conf/deny.txt", "pr": {"approvals": 2},
        }
        with tempfile.TemporaryDirectory() as tmp:
            clone = _parent_repo(tmp, parent)
            settings, notes = git_flow.load_git_settings(clone)
        self.assertEqual(settings["main"], "trunk")
        self.assertEqual(settings["protected"], ["release"])
        self.assertEqual(settings["checkTimeoutSeconds"], 42)
        self.assertEqual(settings["denylist"], "conf/deny.txt")
        self.assertEqual(settings["pr"], {"approvals": 2})
        self.assertEqual(settings["profile"], "code")
        self.assertEqual(settings["checks"], [])
        self.assertEqual(len(notes), 1)
        self.assertNotIn("echo hi", notes[0])

    def test_a_root_manifest_inside_the_repo_still_supplies_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "root"
            child = root / "child"
            child.mkdir(parents=True)
            (root / "siteoffice.json").write_text(
                json.dumps({"settings": {"git": {"checks": ["root-check"]}}}), encoding="utf-8")
            (child / "siteoffice.json").write_text(json.dumps({"parent": ".."}), encoding="utf-8")
            inside, inside_notes = git_flow.load_git_settings(child, repo_dir=root)
            outside, outside_notes = git_flow.load_git_settings(child)
        self.assertEqual((inside["checks"], inside_notes), (["root-check"], []))
        self.assertEqual(outside["checks"], [])
        self.assertEqual(len(outside_notes), 1)

    def test_the_inside_test_uses_real_paths_and_folds_case_only_on_windows(self):
        with mock.patch.object(os.path, "realpath", side_effect=lambda p: p):
            node = pathlib.Path("/x/Repo/sub")
            repo = pathlib.Path("/x/repo")
            with mock.patch.object(git_flow, "IS_WINDOWS", True):
                self.assertTrue(git_flow._manifest_inside(node, repo))
            with mock.patch.object(git_flow, "IS_WINDOWS", False):
                self.assertFalse(git_flow._manifest_inside(node, repo))
            self.assertTrue(git_flow._manifest_inside(pathlib.Path("/x/repo/a/b"), pathlib.Path("/x/repo")))
            self.assertTrue(git_flow._manifest_inside(pathlib.Path("/x/repo"), pathlib.Path("/x/repo")))
            self.assertFalse(git_flow._manifest_inside(pathlib.Path("/x"), pathlib.Path("/x/repo")))
            self.assertFalse(git_flow._manifest_inside(pathlib.Path("/x/repo2"), pathlib.Path("/x/repo")))

    def test_a_manifest_that_is_a_link_to_a_file_outside_the_repo_supplies_no_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            outside = pathlib.Path(tmp) / "outside.json"
            outside.write_text(json.dumps({"settings": {"git": {"checks": ["evil"]}}}), encoding="utf-8")
            try:
                os.symlink(str(outside), str(repo / "siteoffice.json"))
            except (OSError, NotImplementedError):
                self.skipTest("this account cannot create symbolic links")
            settings, notes = git_flow.load_git_settings(repo)
        self.assertEqual(settings["checks"], [])
        self.assertEqual(len(notes), 1)


class TestGitFlowNoteIteration3(unittest.TestCase):
    def test_the_note_states_the_new_rules(self):
        note = (SYNC_DIR / "GIT_FLOW.md").read_text(encoding="utf-8")
        for phrase in ("every file name", "Checks come only from manifests inside the repo",
                       "subscript", "type annotation with spaces"):
            self.assertIn(phrase, note)


# --- iteration 4 (the owner's six decisions D1, D2, D4a, D4b) ---------------

_ORDINARY_NAMES = (
    "task-list-for-the-quarterly-review-2026.md",
    "risk-assessment-2026-09-29-siteoffice-git.md",
    "desk-research-notes-for-the-board-2026.md",
)


class TestPatternBoundariesIteration4(unittest.TestCase):
    """D1: gh[pousr]_ and xox[baprs]- carry no leading word boundary any more
    (so TOKEN_ghp_... and KEY_xoxb-... are hit); sk- keeps its boundary."""

    def _shaped(self):
        return {
            "github-token": f"TOKEN_{GH_TOKEN}",
            "slack-token": f"KEY_{SLACK_TOKEN}",
        }

    def test_a_token_glued_to_a_name_is_found_in_content_names_and_redact(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for rule, text in self._shaped().items():
                with self.subTest(rule=rule):
                    _write(clone, "notes/glued.txt", text + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "notes/glued.txt"), [rule])
                    self.assertEqual(git_flow.name_hits([f"notes/{text}.txt"]), [rule])
                    self.assertEqual(git_flow.text_hits(text), [rule])
                    secret = GH_TOKEN if rule == "github-token" else SLACK_TOKEN
                    cleaned = git_flow.redact("see " + text + " end")
                    self.assertNotIn(secret, cleaned)
                    self.assertIn("[redacted]", cleaned)

    def test_the_sk_rule_keeps_its_word_boundary(self):
        glued = "KEY_" + SK_KEY
        self.assertEqual(git_flow.text_hits(glued), [])
        self.assertEqual(git_flow.name_hits([glued + ".txt"]), [])
        self.assertEqual(git_flow.redact(glued), glued)
        self.assertEqual(git_flow.text_hits("KEY=" + SK_KEY), ["sk-key"])

    def test_ordinary_names_and_short_prefixes_still_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for name in _ORDINARY_NAMES:
                with self.subTest(name=name):
                    self.assertEqual(git_flow.name_hits([name, "notes/" + name]), [])
                    self.assertEqual(git_flow.text_hits(name), [])
                    self.assertEqual(git_flow.redact(name), name)
                    _write(clone, "notes/" + name, name + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "notes/" + name), [])
            for text in ("xoxb-", "xoxb-123", "xoxb-123456789", "KEY_xoxb-", "TOKEN_ghp_tooshort",
                         "ghp_" + "a" * 35, "TOKEN_ghp_" + "a" * 35):
                with self.subTest(text=text):
                    self.assertEqual(git_flow.text_hits(text), [])
                    self.assertEqual(git_flow.name_hits([text + ".md"]), [])
                    self.assertEqual(git_flow.redact(text), text)

    def test_save_refuses_a_glued_token_file_name_without_naming_it(self):
        for rule, text in self._shaped().items():
            secret = GH_TOKEN if rule == "github-token" else SLACK_TOKEN
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as tmp:
                clone, _remote = _make_flow_repo(tmp)
                _on_branch(clone)
                _write(clone, "notes/" + text + ".txt", "hello\n")
                head = _head(clone)
                result = git_flow.save(clone, scope="notes", subject="add note", apply=True)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertIn(f"a file name (rule: {rule})", result["refusal"])
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn(secret, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))
                self.assertEqual(_head(clone), head)
                self.assertEqual(_staged(clone), [])

    def test_the_boundaryless_rules_stay_linear(self):
        shapes = [
            "ghp_" * 25000, "gh" * 50000, "xox" * 33333, "xoxb-" * 20000, "xoxb-1" * 16666,
            "xoxb-" + "-" * 9, ("xoxb-" + "a" * 9 + " ") * 9000, ("ghp_" + "a" * 35 + " ") * 2700,
            "x" * 100000, "g" * 100000,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in shapes:
                with self.subTest(head=line[:8], size=len(line)):
                    _write(clone, "long.txt", line + "\n")
                    started = time.monotonic()
                    git_flow.content_hits(clone, "long.txt")
                    git_flow.redact(line)
                    git_flow.name_hits([line])
                    self.assertLess(time.monotonic() - started, 2.0)


class TestStartPullStateIteration4(unittest.TestCase):
    """D2: a pull result whose state is "error" is refused even when opOk is True."""

    def _pulled(self):
        return {"state": "error", "opOk": True, "conflicts": [], "detail": "PLANTED-DETAIL-TEXT"}

    def test_a_pull_state_error_with_opok_true_is_refused_and_makes_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            head = _head(clone)
            with mock.patch.object(git_ops, "pull", return_value=self._pulled()):
                result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "refused", result)
            self.assertIn("No branch was made", result["refusal"])
            self.assertIn("error", result["refusal"])
            self.assertNotIn("PLANTED-DETAIL-TEXT", json.dumps(result))
            self.assertEqual(_branches(clone), ["main"])
            self.assertEqual(_head(clone), head)
            self.assertEqual(_receipt_lines(clone)[0]["outcome"], "refused")

    def test_the_cli_exits_2_and_makes_no_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            with mock.patch.object(git_ops, "pull", return_value=self._pulled()):
                code, out = _cli(["start", "fix-1", "--apply", "--repo", str(clone)])
            self.assertEqual(code, 2, out)
            self.assertNotIn("PLANTED-DETAIL-TEXT", out)
            self.assertEqual(_branches(clone), ["main"])

    def test_a_clean_pull_state_still_creates_the_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _make_flow_repo(tmp)
            clean = {"state": "clean", "opOk": True, "conflicts": []}
            with mock.patch.object(git_ops, "pull", return_value=clean):
                result = git_flow.start(clone, "fix-1", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])
            self.assertIn(result["branch"], _branches(clone))


def _link_text_for(key):
    """A link target path whose last segment is key-shaped; built at runtime."""
    return "C:\\some\\where\\" + key + ".txt"


class TestUntrackedSymlinkTextIteration4(unittest.TestCase):
    """D4a: the text of an untracked symlink (its target path) is scanned as an
    added line."""

    def test_the_link_text_is_scanned_with_islink_and_readlink_mocked(self):
        target = _link_text_for(AWS_KEY)

        def fake_islink(path):
            return os.path.basename(str(path)) == "l.txt"

        def fake_readlink(path):
            if os.path.basename(str(path)) != "l.txt":
                raise OSError("not a link")
            return target

        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "notes/l.txt", "harmless\n")
            _write(clone, "notes/plain.txt", "harmless\n")
            with mock.patch.object(os.path, "islink", side_effect=fake_islink), \
                    mock.patch.object(os, "readlink", side_effect=fake_readlink):
                lines = git_flow._added_lines(clone, "notes/l.txt")
                hits = git_flow.content_hits(clone, "notes/l.txt")
                plain = git_flow.content_hits(clone, "notes/plain.txt")
            self.assertIn(target, lines)
            self.assertIn("harmless", lines)
            self.assertEqual(hits, ["aws-access-key"])
            self.assertEqual(plain, [])

    def test_a_link_whose_text_cannot_be_read_is_a_failed_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            _write(clone, "notes/l.txt", "harmless\n")
            with mock.patch.object(os.path, "islink", side_effect=lambda p: os.path.basename(str(p)) == "l.txt"), \
                    mock.patch.object(os, "readlink", side_effect=OSError("gone")):
                self.assertEqual(git_flow.content_hits(clone, "notes/l.txt"), ["scan-failed"])

    def test_a_link_name_that_is_key_shaped_is_still_caught_by_name_hits(self):
        self.assertEqual(git_flow.name_hits(["notes/" + AWS_KEY + ".txt"]), ["aws-access-key"])

    def _link(self, clone, name, target):
        try:
            os.symlink(str(target), str(clone / "notes" / name))
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symbolic links")

    def test_a_real_symlink_with_a_key_shaped_target_refuses_the_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = pathlib.Path(tmp) / (AWS_KEY + ".txt")
            outside.write_text("harmless\n", encoding="utf-8")
            (pathlib.Path(tmp) / "work").mkdir()
            clone, _remote = _make_flow_repo(pathlib.Path(tmp) / "work")
            _on_branch(clone)
            (clone / "notes").mkdir()
            self._link(clone, "link.txt", outside)
            head = _head(clone)
            for apply in (False, True):
                result = git_flow.save(clone, scope="notes", subject="add link", apply=apply)
                self.assertEqual(result["outcome"], "refused", result["refusal"])
                self.assertIn("notes/link.txt", result["refusal"].replace("\\", "/"))
                self.assertIn("aws-access-key", result["refusal"])
                self.assertNotIn(AWS_KEY, json.dumps(result))
            code, out = _cli(["save", "--apply", "--repo", str(clone), "--scope", "notes"])
            self.assertEqual(code, 2, out)
            self.assertNotIn(AWS_KEY, out)
            self.assertNotIn(AWS_KEY, (clone / "_log" / "runs" / "git-flow.ndjson").read_text(encoding="utf-8"))
            self.assertEqual(_head(clone), head)
            self.assertEqual(_staged(clone), [])

    def test_a_real_clean_symlink_still_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = pathlib.Path(tmp) / "readme-target.txt"
            outside.write_text("harmless\n", encoding="utf-8")
            (pathlib.Path(tmp) / "work").mkdir()
            clone, _remote = _make_flow_repo(pathlib.Path(tmp) / "work")
            _on_branch(clone)
            (clone / "notes").mkdir()
            self._link(clone, "link.txt", outside)
            result = git_flow.save(clone, scope="notes", subject="add link", apply=True)
            self.assertEqual(result["outcome"], "pass", result["refusal"])


_ANNOTATED = f'secret: Annotated[str, Doc("the secret")] = "{_VALUE}"'


class TestAnnotationParenthesesIteration4(unittest.TestCase):
    """D4b: the annotation class of the quoted-assignment rule allows ( and )."""

    def test_an_annotation_with_call_parentheses_is_found_and_redacted(self):
        forms = [
            _ANNOTATED,
            f'api_key: Annotated[str, Field(min_len)] = "{_VALUE}"',
            f"password: Optional[SecretStr((3))] = '{_VALUE}'",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in forms:
                with self.subTest(line=line[:30]):
                    _write(clone, "f.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "f.txt"), ["secret-assignment"])
                    self.assertNotIn(_VALUE, git_flow.redact(line))
                    self.assertNotIn(_VALUE, git_flow.redact("x " + line + " y"))
                    self.assertIn("[redacted]", git_flow.redact(line))

    def test_look_alikes_still_pass(self):
        look_alikes = _wider_look_alikes() + [
            'secret: Annotated[str, Doc("the secret")] = "short"',
            'secret: Annotated[str, Doc("the secret")]',
            'password: str = get_password("' + _VALUE + '")',
            'token: Annotated[str, Doc("x")] = get_token()',
            "# password: Annotated[str, Doc('x')] = 'short'",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in look_alikes:
                with self.subTest(line=line[:34]):
                    _write(clone, "h.txt", line + "\n")
                    self.assertEqual(git_flow.content_hits(clone, "h.txt"), [])
                    self.assertEqual(git_flow.redact(line), line)

    def test_the_parenthesis_shapes_stay_linear(self):
        shapes = [
            "(" * 100000, ")" * 100000, '("' * 50000, "()" * 50000,
            "key: " + "(" * 199 * 500, ("key: " + "(" * 199) * 500, ("key: " + ")" * 199) * 500,
            "key: " + '("' * 50000, "key:" + "(" * 100000, "key: " + "(" * 100000 + '= "' + "a" * 11 + '"',
            ("password: " + "(a)" * 60 + "=") * 500, 'key: (("' * 15000,
        ]
        worst = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            clone, _remote = _init_repo_pair(pathlib.Path(tmp))
            for line in shapes:
                with self.subTest(head=line[:14], size=len(line)):
                    _write(clone, "long.txt", line + "\n")
                    for call in (lambda: git_flow.content_hits(clone, "long.txt"),
                                 lambda: git_flow.redact(line),
                                 lambda: git_flow.name_hits([line])):
                        started = time.monotonic()
                        call()
                        took = time.monotonic() - started
                        worst = max(worst, took)
                        self.assertLess(took, 2.0)
        print("worst paren shape seconds: %.3f" % worst, file=sys.stderr)


class TestGitFlowNoteIteration4(unittest.TestCase):
    def test_the_note_states_the_iteration_4_rules(self):
        note = (SYNC_DIR / "GIT_FLOW.md").read_text(encoding="utf-8")
        for phrase in ("only `sk-` keeps a word boundary", "pull state", "symlink", "parentheses"):
            self.assertIn(phrase, note)
