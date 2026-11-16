"""The engine runs on Python 3.9, so no Python file may use an API that needs a newer one.

Path.write_text(newline=...) was added in Python 3.10 and Path.read_text(newline=...) in
3.13; on 3.9 both raise TypeError. open(..., newline=...) is fine on every version, and so
is Path.write_bytes. This test reads the source with ast, so it fails on any Python, not
only on 3.9, and names the file and line.
"""
import ast
import pathlib

FOREMAN = pathlib.Path(__file__).resolve().parents[2]
BANNED = {"write_text": "3.10", "read_text": "3.13"}


def _hits():
    found = []
    for path in sorted(FOREMAN.rglob("*.py")):
        if any(part in ("__pycache__", ".pytest_cache") for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as err:  # a file that does not parse is another test's problem
            found.append("%s: does not parse (%s)" % (path.relative_to(FOREMAN), err))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in BANNED and any(k.arg == "newline" for k in node.keywords):
                found.append("%s:%d: .%s(newline=...) needs Python %s; use open(..., newline=...) or write_bytes"
                             % (path.relative_to(FOREMAN), node.lineno, node.func.attr, BANNED[node.func.attr]))
    return found


def test_no_file_uses_a_path_text_api_newer_than_python_3_9():
    hits = _hits()
    assert not hits, "Python 3.9 floor broken:\n" + "\n".join(hits)
