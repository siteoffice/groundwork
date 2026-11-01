# groundwork

Public repo (MIT). A folder structure, one loop skill, a board and two plugins for working with AI
agents. Claude Code first; the skills are plain Markdown and portable.

## Layout

- `.claude-plugin\marketplace.json`: the marketplace, two plugins, one version clock.
- `plugins\groundwork`: skills, engine (vendored by `scripts\sync-engine.ps1`), board, receipts.
- `plugins\groundwork-mods`: Setup Doctor, agent policy pack, and the Fleet and the Git lane behind switches (off by default).
- `templates\`: folder skeleton, AGENTS.md and GLOSSARY.md starters, Molenwerk example.
- `docs\`: setup guide, kit story, questionnaire, CHANGELOG.
- `assets\screenshots\`: real screenshots with alt text.
- `scripts\`: `check-public-payload.ps1` (payload gate), `bump-version.ps1` (version clock) and the sync script.

## Rules

- Nothing private ships. Run `scripts\check-public-payload.ps1` before any commit; it must exit 0.
- Scripts are pure ASCII. No em dashes.
- No binaries. BurnMon is linked, never copied.
- Every change that reaches a user is a semver bump on the groundwork clock, through the publish
  script, never by hand-editing manifests.
- Release: `pwsh -NoProfile -File .\scripts\bump-version.ps1 -Bump <level> -Apply`, then the commit and tag it prints.
- The README follows the public repo standard and is updated in the same change as the feature.
