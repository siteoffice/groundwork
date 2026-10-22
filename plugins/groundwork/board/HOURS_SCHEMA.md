# Time registration, WBSO-grade (architecture note section 11)

One page: the ndjson schema the hours panel and `export-hours` read, the correction rule, and
the two Dutch rules the copy leans on. Written by a skill (`log-time-from-sessions` in sprint 2,
a manual `log time` chat command today); the board never writes it.

## Storage

`03_logs\time\YYYY-MM.ndjson` per node with `settings.timeRegistration` true. One JSON object per
line, append-only, never edited in place. Reader: `board\hours.py`.

| Field | Type | Meaning |
|---|---|---|
| `date` | string, `YYYY-MM-DD` | the day the work happened |
| `person` | string | who logged it |
| `node` | string | the node path the hours belong to |
| `hours` | number, decimal | quarter-hour granularity recommended, not enforced; negative for a correction |
| `so` | bool | marks S&O (WBSO) hours |
| `tag` | string | free-form, e.g. `build`, `review` |
| `note` | string | free text; required on a correction line, giving the reason |
| `loggedOn` | string, `YYYY-MM-DD` | the day the entry was written (checked against `date` for the 10-working-day rule) |

## Correction rule

A mistake is never edited in place. A correction is a new line with a negative `hours` and a
`note` explaining why. The running total for that date, person and node is the sum of every line
that matches.

## Synthetic example

```
{"date":"2026-09-10","person":"wilco","node":"projects/siteoffice","hours":3.5,"so":true,"tag":"build","note":"board renderer, theme move","loggedOn":"2026-09-10"}
{"date":"2026-09-10","person":"wilco","node":"projects/siteoffice","hours":-1.0,"so":true,"tag":"build","note":"correction, over-logged by an hour","loggedOn":"2026-09-12"}
```

## The two rules the hours panel and export lean on

- **WBSO S&O-administratie, the 10-working-day rule (VERIFY, see `04_assets\_grill_state.md`)**:
  hours per person, per day, per project, recorded within 10 working days of the work. Working
  days are Monday to Friday; v1 carries no holiday calendar, so a public holiday still counts as
  a working day in this reckoning. `unlogged_days` in `board\hours.py` flags a working day older
  than 10 working days with no entry at all.
- **Urencriterium, 1,225 hours a calendar year (VERIFY, see `04_assets\_grill_state.md`)**: the
  bar the hours panel's per-person progress bar is measured against for the
  zelfstandigenaftrek, and, on top of it, the startersaftrek (open VERIFY: its status after 2026).

Neither rule changes the schema or the reader's behaviour if the primary source disagrees; only
the copy shown to the reader would change. Both stay provisional until the RVO leidraad and the
belastingdienst.nl page are read directly (register: `04_assets\_grill_state.md`).

## What this does not do

No timer, no calendar sync, no hour approval, no invoicing link. Entries are written by a skill
or a manual chat command; the board only reads and exports them.
