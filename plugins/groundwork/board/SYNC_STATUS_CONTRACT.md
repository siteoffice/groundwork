# sync_status.js contract

For `siteoffice-sync` to implement (built by the parent plan, not here). The board renders
against this contract from sprint 1; the file itself does not exist yet in this repo.

## The file

`<node>\_board\sync_status.js`, gitignored, written by the sync layer after every sync run (pull,
push, or a no-op check). Exactly one statement:

```js
window.ZND_SYNC_STATUS = {
  "node": "projects/siteoffice",
  "clone": "wilco-laptop",
  "lastPull": "2026-09-11T09:00:00Z",
  "lastPush": "2026-09-11T09:05:00Z",
  "behind": 0,
  "ahead": 0,
  "conflicts": [],
  "state": "ok",
  "message": "Up to date.",
  "writtenAt": "2026-09-11T09:05:12Z"
};
```

| Field | Type | Meaning |
|---|---|---|
| `node` | string | the node's path relative to the holding root |
| `clone` | string | a machine label, whatever `siteoffice-sync` uses to name a clone |
| `lastPull` | string (ISO UTC) or `null` | last successful pull; `null` if never |
| `lastPush` | string (ISO UTC) or `null` | last successful push; `null` if never |
| `behind` | integer | commits the local clone is behind the remote |
| `ahead` | integer | commits the local clone is ahead of the remote |
| `conflicts` | list of strings | paths with an unresolved conflict, empty when none |
| `state` | string | one of `ok`, `behind`, `conflict`, `refused`, `error` |
| `message` | string | one sentence, plain words, no git jargon |
| `writtenAt` | string (ISO UTC) | when this file itself was written |

A JSON twin, `sync_status.json` with the same object, is optional; sprint 2's localhost helper
serves it (architecture note 4.5, option B). Nothing in sprint 1 reads the JSON twin.

## Page behaviour

The sync status panel renders server-side in the `loading` state, the only panel that ever does
(architecture note 5.3). At the end of the body, after every other script, one
`<script src="sync_status.js">` tag, a relative sibling path: the one allowed `<script src>` in
the whole page (architecture note F1, file:// blocks `fetch()`). Script tags run in document
order when neither carries `async` nor `defer`, so by the time the following inline script runs,
`window.ZND_SYNC_STATUS` is already set if the file loaded, or was never touched if it 404'd (a
missing sibling script is a silent failure in a browser, never a thrown error the page has to
catch).

The inline script then swaps the panel's body:

- `window.ZND_SYNC_STATUS` is an object: the `normal` copy, built from its fields (node, clone,
  state, last pull and push times, behind/ahead counts, any conflicts, the message).
- Anything else (the file was absent, empty, or malformed): the `unavailable` copy, the label
  key `no_sync_status` ("No sync status." / "Geen syncstatus."), the truthful answer, never a
  red error.

## What `siteoffice-sync` must never do

Never write anything else into `_board\`; never commit `sync_status.js` (the `.gitignore` entry
`_board/sync_status.js` is already in place); never write the file mid-run, only after the sync
operation has fully finished (a partial write could leave invalid JavaScript that breaks the
page's own script parsing, not just this one panel).
