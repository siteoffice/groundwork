# Changelog

All notable changes. The version is one clock for the whole repository: `groundwork`,
`groundwork-mods` and the marketplace move together.

## 0.1.0 (unreleased)

First public version. Windows tested; macOS untested.

- **`groundwork` plugin.** The `one-loop` skill takes one idea from plan to close with four gates.
  `gw.py` is its command line over the vendored Foreman engine (987 tests pass with it). A local
  receipt, a readable page and a JSON file, is written when you ship at G4, labelled
  self-issued, not signed.
- **Skills.** `siteoffice-setup`, `siteoffice-migrate`, `siteoffice-cleanup`,
  `siteoffice-checkup`, `siteoffice-git` and `handover` moved here from the ZeroNonsense.dev
  skills marketplace.
- **Board.** `render_board.py` writes one static HTML file from a folder tree. It only reads.
  185 tests pass.
- **`groundwork-mods` plugin.** The Setup Doctor (`/setup-doctor`) checks the computer and names
  the skipped session-start state. The agent policy is read-only by default, allows drafts, blocks
  sends, needs a named person to delete, and logs every write with a receipt and an undo note.
  18 tests pass.
- **Templates.** An empty unit skeleton and Molenwerk, a finished example.
- **Docs.** Setup guide, the story page and the questionnaire.

Known limits: the agent policy cannot see a symbolic link that leads out of a folder; the engine
runs the work step in your session and ships no sprint runner; the Slack sweep is an optional
adapter that needs a token.
