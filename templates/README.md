# Templates

- `skeleton\`: one empty unit, the folder every holding, company, department and project uses.
  Copy it, rename it, fill it in. The files say "to be filled" where your own words go. Or let
  the `siteoffice-setup` skill build the same tree and fill it from an interview.
- `molenwerk\`: a finished example, a fictional Dutch webshop company. Open it to see what a
  filled-in unit looks like, and render it with the board:

```powershell
# runs in: PowerShell on Windows. cwd: the repo root of this checkout.
py -3 .\plugins\groundwork\board\render_board.py node .\templates\molenwerk
```

```bash
# runs in: bash or zsh on macOS or Linux. cwd: the repo root of this checkout.
python3 ./plugins/groundwork/board/render_board.py node ./templates/molenwerk
```

The board writes `_board/board.html`, one file you open in a browser. It only reads your folders.
