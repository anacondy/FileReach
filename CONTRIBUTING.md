# Contributing to FileReach

Thanks for your interest in improving FileReach! A few ground rules keep the
project trustworthy.

## The non-negotiables

These rules exist to protect users' data. PRs that violate them won't be merged.

1. **Read-only.** Never introduce code that writes to, moves, renames, or deletes a
   user's file. Files must be opened with `'rb'` / `'r'` only. The only writes allowed
   are to FileReach's own data dir (`%LOCALAPPDATA%\FileReach` / `~/.filereach`).
2. **No duplication.** Don't copy file bytes anywhere — not to temp, not to a cache.
   Read metadata and (for the viewer) content on demand.
3. **Single permission.** The app asks for Administrator exactly once (source: `run.py`;
   Windows build: the embedded `requireAdministrator` manifest). No per-folder prompts.

## Development setup

```bash
git clone <repo>
cd filereach
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                    # opens http://127.0.0.1:8765
```

## Before opening a PR

- `python -c "import engine, app"` should succeed with no errors.
- If you change search behaviour, sanity-check with a small test folder:
  ```bash
  python -c "import sys; sys.path.insert(0,'.'); import engine, app; print('ok')"
  ```
- Keep the UI consistent with the Linen design tokens (CSS variables in
  `static/index.html`). No external CDNs/fonts beyond the bundled Inter link — the
  in-app preview is sandboxed with no network access.

## Building

```bash
pip install pyinstaller
python make_icon.py
pyinstaller filereach.spec --noconfirm
```

## Commit style

Small, focused commits with a clear message (e.g. `feat: add .epub to documents`,
`fix: handle long paths on Windows`). conventional-commits style is appreciated.

## Reporting issues

Include: OS, whether you ran the binary or source, the relevant log lines from
`%LOCALAPPDATA%\FileReach\filereach.log` (or `~/.filereach/filereach.log`), and the
exact steps to reproduce.
