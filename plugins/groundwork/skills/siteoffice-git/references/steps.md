# siteoffice-git steps: previews, refusals, next moves

Source of truth for the engine behaviour: `sync\GIT_FLOW.md` in the Siteoffice repo. The Dutch text is
drafted, not checked by a native speaker.

Each step runs as `git_flow.ps1 <step>` (preview) and `git_flow.ps1 <step> --apply` (act).

## English

### start

- Preview: names the branch `work/<owner>/<slug>` it will create from the configured main branch, after a pull.
- Refuses: the pull ended in `error` or `conflict`, or failed; the directory is not the git root.
- Next move: fix the pull state by hand (the person), then run `start <slug>` again.

### save

- Preview: lists every changed path it would commit and the commit message it will use.
- Subject rule: `--subject` is at most 50 characters, one line, with no closing period. A longer subject is refused with "Commit subject is N characters; the limit is 50." Put the rest in the files, not the subject.
- Refuses: on a protected branch (main, master); a path breaks an owner rule; a path is on the denylist; content or a file name looks like a secret; a policy file changed (`siteoffice.json`, the denylist file, any `.gitignore`).
- Next move: move to a work branch with `start`, or take the named path out of the change, or ask the person to commit the policy change by hand. Then run `save` again.

### check

- Preview: lists the commands from `settings.git.checks` it will run, or says no checks are configured.
- Blocks: a check exits non-zero or times out.
- Next move: fix the failing check, `save` again, then `check` again. A green receipt is needed for the current commit before `propose`.

### propose

- Preview: the branch it will push and the pull request title and body.
- Title: `propose --title TEXT` sets the pull request title. Default: the newest commit subject. When a pull request is already open, the title is not changed and the preview says so.
- Refuses: detached or protected HEAD; not on a `work/<owner>/<slug>` branch; dirty tree; no commit on top of main; "Run check on this commit first."
- Blocks: `gh` missing ("Install GitHub CLI: winget install --id GitHub.cli, then run gh auth login.") or not logged in ("Run gh auth login.").
- Next move: run `check` for this commit, or install and log in to `gh`, then `propose` again.

### land

- Preview: the pull request it will merge, the checks it will run on the merge result, and the cleanup it will do.
- Merge subject: `land --subject TEXT` sets the merge commit subject. Default: the subject `gh` writes.
- Refuses: dirty tree; no open pull request; "Push first: run propose."; changes requested; fewer approvals than `pr.approvals`; merge conflicts (paths named).
- Blocks: a check on the merge result is red or times out.
- Next move: `propose` again after new commits, resolve the named conflicts, or get the approval. Then `land` again.

### abort

- Preview: the stash it will make (if any) and the new branch name `parked/<owner>/<slug>`.
- Refuses: not on a `work/<owner>/<slug>` branch.
- Next move: none needed. The parked branch, commits and remote branch are untouched. Start again with `start <slug>`.

## Nederlands

### start

- Voorbeeld: noemt de branch `work/<owner>/<slug>` die het maakt vanaf de ingestelde hoofdbranch, na een pull.
- Weigert: de pull eindigde in `error` of `conflict`, of mislukte; de map is niet de git-root.
- Volgende stap: los de pull-toestand met de hand op (de persoon) en voer `start <slug>` opnieuw uit.

### save

- Voorbeeld: toont elk gewijzigd pad dat het vastlegt en de commitmelding.
- Onderwerpregel: `--subject` is hoogstens 50 tekens, een regel, zonder punt aan het eind. Een langer onderwerp wordt geweigerd met "Commit subject is N characters; the limit is 50." Zet de rest in de bestanden, niet in het onderwerp.
- Weigert: op een beschermde branch (main, master); een pad breekt een eigenaarsregel; een pad staat op de denylist; inhoud of bestandsnaam lijkt op een geheim; een beleidsbestand is gewijzigd (`siteoffice.json`, het denylist-bestand, elke `.gitignore`).
- Volgende stap: ga naar een werkbranch met `start`, haal het genoemde pad uit de wijziging, of vraag de persoon de beleidswijziging met de hand vast te leggen. Voer daarna `save` opnieuw uit.

### check

- Voorbeeld: toont de opdrachten uit `settings.git.checks` die het draait, of meldt dat er geen checks zijn ingesteld.
- Geblokkeerd: een check geeft een fout of loopt af.
- Volgende stap: herstel de check, `save` opnieuw, daarna `check` opnieuw. Voor `propose` is een groene uitslag voor de huidige commit nodig.

### propose

- Voorbeeld: de branch die het pusht en de titel en tekst van de pull request.
- Titel: `propose --title TEXT` zet de titel van de pull request. Standaard: het onderwerp van de nieuwste commit. Staat er al een pull request open, dan blijft de titel ongewijzigd en meldt het voorbeeld dat.
- Weigert: losgekoppelde of beschermde HEAD; niet op een `work/<owner>/<slug>` branch; onopgeslagen wijzigingen; geen commit boven main; "Run check on this commit first."
- Geblokkeerd: `gh` ontbreekt of is niet ingelogd.
- Volgende stap: voer `check` uit voor deze commit, of installeer `gh` en log in, en voer `propose` opnieuw uit.

### land

- Voorbeeld: de pull request die het samenvoegt, de checks op het samengevoegde resultaat en de opruimacties.
- Onderwerp samenvoegcommit: `land --subject TEXT` zet het onderwerp van de samenvoegcommit. Standaard: het onderwerp dat `gh` schrijft.
- Weigert: onopgeslagen wijzigingen; geen open pull request; "Push first: run propose."; wijzigingen gevraagd; te weinig goedkeuringen; samenvoegconflicten (paden genoemd).
- Geblokkeerd: een check op het samengevoegde resultaat is rood of loopt af.
- Volgende stap: `propose` opnieuw na nieuwe commits, los de genoemde conflicten op, of haal de goedkeuring op. Voer daarna `land` opnieuw uit.

### abort

- Voorbeeld: de stash die het maakt (als die er is) en de nieuwe branchnaam `parked/<owner>/<slug>`.
- Weigert: niet op een `work/<owner>/<slug>` branch.
- Volgende stap: niets nodig. De geparkeerde branch, de commits en de remote branch blijven onaangeroerd. Begin opnieuw met `start <slug>`.
