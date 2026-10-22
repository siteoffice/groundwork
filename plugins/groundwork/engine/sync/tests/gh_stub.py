"""Stub gh for sync/tests: a stateful fake of the few gh calls git_flow makes.

Usage: gh_stub.py <state.json> <calls.ndjson> <gh arguments...>
Every call appends its arguments as one JSON line to the call log. The state
file (JSON) holds: auth (bool), remote (path of the bare remote), prs (list of
pull request dicts), fail ({"pr create": "stderr text"}), raw_stdout (text
printed instead of the JSON of `pr list`), sleep (seconds to wait first) and
delete_head_on_merge (bool). Stdlib only, never touches the network.
"""
import json
import os
import subprocess
import sys
import time

IDENTITY = {
    "GIT_AUTHOR_NAME": "Stub", "GIT_AUTHOR_EMAIL": "stub@example.com",
    "GIT_COMMITTER_NAME": "Stub", "GIT_COMMITTER_EMAIL": "stub@example.com",
}


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, **IDENTITY})


def flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def find_pr(state, number):
    for pr in state["prs"]:
        if pr["number"] == int(number):
            return pr
    return None


def pr_create(state, args):
    head = flag(args, "--head")
    sha = git(os.getcwd(), "rev-parse", "refs/heads/" + head).stdout.strip()
    number = len(state["prs"]) + 1
    url = f"https://github.example/test/repo/pull/{number}"
    state["prs"].append({
        "number": number, "url": url, "state": "OPEN", "headRefName": head, "headRefOid": sha,
        "baseRefName": flag(args, "--base"), "title": flag(args, "--title"),
        "body": flag(args, "--body"), "reviewDecision": "", "latestReviews": [],
        "mergeCommit": None,
    })
    print(url)
    return 0


def pr_merge(state, args):
    pr = find_pr(state, args[2])
    if pr is None or "--merge" not in args:
        sys.stderr.write("gh_stub: unknown pull request or a merge mode other than --merge\n")
        return 64
    remote = state["remote"]
    tip = git(remote, "rev-parse", "refs/heads/" + pr["headRefName"]).stdout.strip()
    match = flag(args, "--match-head-commit")
    if match and match != tip:
        sys.stderr.write("Head branch was modified. Review and try the merge again.\n")
        return 1
    base = "refs/heads/" + pr["baseRefName"]
    tree = git(remote, "merge-tree", "--write-tree", base, tip)
    if tree.returncode != 0:
        sys.stderr.write("Pull request is not mergeable\n")
        return 1
    message = flag(args, "--subject") or f"Merge pull request #{pr['number']} from {pr['headRefName']}"
    commit = git(remote, "commit-tree", tree.stdout.split()[0], "-p", base, "-p", tip,
                 "-m", message).stdout.strip()
    git(remote, "update-ref", base, commit)
    if state.get("delete_head_on_merge"):
        git(remote, "update-ref", "-d", "refs/heads/" + pr["headRefName"])
    pr["state"] = "MERGED"
    pr["mergeCommit"] = {"oid": commit}
    return 0


def handle_call(state, args):
    name = " ".join(args[:2])
    failure = state.get("fail", {}).get(name)
    if failure is not None:
        sys.stderr.write(failure + "\n")
        return 1
    if name == "auth status":
        if state.get("auth", True):
            print("Logged in to github.example")
            return 0
        sys.stderr.write("You are not logged into any GitHub hosts.\n")
        return 1
    if name == "pr list":
        if state.get("raw_stdout") is not None:
            print(state["raw_stdout"])
            return 0
        head = flag(args, "--head")
        print(json.dumps([pr for pr in state["prs"]
                          if pr["headRefName"] == head and pr["state"] == "OPEN"]))
        return 0
    if name == "pr create":
        return pr_create(state, args)
    if name == "pr view":
        pr = find_pr(state, args[2])
        if pr is None:
            sys.stderr.write("no pull request found\n")
            return 1
        print(json.dumps(pr))
        return 0
    if name == "pr merge":
        return pr_merge(state, args)
    sys.stderr.write(f"gh_stub: unsupported call {name}\n")
    return 64


def main(argv):
    state_path, log_path, args = argv[1], argv[2], argv[3:]
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(args) + "\n")
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
    if state.get("sleep"):
        time.sleep(state["sleep"])
    code = handle_call(state, args)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
