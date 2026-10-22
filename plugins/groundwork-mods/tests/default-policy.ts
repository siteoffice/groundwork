// A literal copy of policy/agent-policy.json: plugin tests can import code files only, not JSON.
// Keep it equal to that file; change both together.
export const shipped = {
  "version": 1,
  "mode": "read-only",
  "draftFolders": [
    "drafts"
  ],
  "neverWrite": [
    ".groundwork"
  ],
  "send": {
    "commands": [
      "git push",
      "gh pr create",
      "gh pr merge",
      "gh pr comment",
      "gh issue create",
      "gh issue comment",
      "gh release create",
      "npm publish",
      "twine upload",
      "curl -x post",
      "curl --request post",
      "invoke-restmethod",
      "-method post",
      "sendmail",
      "scp ",
      "rsync "
    ],
    "toolWords": [
      "send",
      "post",
      "publish",
      "reply",
      "tweet",
      "email",
      "mail",
      "schedule",
      "forward",
      "submit",
      "broadcast",
      "notify",
      "invite"
    ]
  },
  "draftWords": [
    "draft"
  ],
  "delete": {
    "commands": [
      "rm ",
      "rmdir",
      "del ",
      "erase ",
      "remove-item",
      "git clean",
      "git branch -d",
      "git reset --hard",
      "shred",
      "unlink"
    ],
    "toolWords": [
      "delete",
      "remove",
      "trash",
      "destroy",
      "purge",
      "drop",
      "archive"
    ],
    "approvers": [],
    "approvalFile": ".groundwork/approvals.json"
  },
  "readOnlyCommands": [
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git rev-parse",
    "git ls-files",
    "git remote -v",
    "ls",
    "dir",
    "cat",
    "type",
    "pwd",
    "echo",
    "head",
    "tail",
    "wc",
    "grep",
    "rg",
    "findstr",
    "get-childitem",
    "get-content",
    "get-location",
    "select-string",
    "test-path",
    "which",
    "where",
    "python --version",
    "py -3 --version",
    "node --version"
  ],
  "readWords": [
    "get",
    "list",
    "read",
    "search",
    "query",
    "fetch",
    "find",
    "show",
    "describe",
    "view",
    "lookup",
    "count"
  ],
  "log": ".groundwork/agent-log.ndjson",
  "undoFolder": ".groundwork/undo"
}
