# Changelog

All notable changes to FileReach are documented here.
Format loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [v1.3.0] — 2026-07-01 — *Token matching, path auto-scope, global folder search, scrollbars*

### Fixed
- **"3 GAL" didn't match "3-GAL":** search now tokenises on spaces, dashes, and
  underscores — so "3 GAL" finds "3-GAL", "3_GAL", or "3 gal". Works in both
  index and live search.
- **White scrollbar in dark mode:** scrollbars are now themed with CSS variables
  (thin, rounded, adapt to light/dark automatically).
- **Long-list lag:** results rendering is capped for smoother scrolling.

### Added
- **Paste a path → auto-scope:** pasting a folder path (e.g.
  C:\Users\iassh\PycharmProjects\3-Germany baby) in the search bar now auto-sets
  it as the search scope and clears the input so you can search within it. Pasting
  a file path scopes to its parent folder and searches for that file's name.
- **Global folder search in the picker:** typing in the folder picker's search bar
  now finds matching folders across all indexed drives (not just current location).
- New endpoints: /api/resolve_path, /api/find_folders.

## [v1.2.1] — 2026-06-30 — *Dark-mode fix (browser cache) + version badge*

### Fixed
- **Dark mode "not working":** the toggle JS + CSS were correct all along — the real
  cause was the browser serving a **stale cached `index.html`** after an update. Added
  `Cache-Control: no-store` headers so the browser always loads the fresh UI.

### Added
- **Version badge** in the top bar (e.g. `v1.2.1`) so you can confirm at a glance which
  build is actually running (also via `/api/version`).
- `Alt+T` keyboard shortcut to toggle dark mode.
- Refactored theme toggle into a single `toggleTheme()` shared by the button + shortcut.

## [v1.2.0] — 2026-06-30 — *Logging, live search & path paste*

This release targets "the app shows nothing" reports: the root cause was folders that
weren't indexed. Searching them now works **without an index**, via a live filesystem
walk. Every action is also logged to the console so you can see exactly what happened.

### Fixed
- **"Won't show anything" bug:** if a scoped folder isn't under an indexed root, the
  search now **auto-falls back to a live walk** of that folder and returns real matches.
  (Previously returned 0 because the folder simply wasn't indexed.)
- Extension + type-chip conflict no longer silently empties results (extension wins).

### Added
- **Structured logging** — a version banner on startup, and a concise coloured line for
  every search/index/OCR/reveal with elapsed time and hit counts. Mirrored to the log
  file (`%LOCALAPPDATA%\FileReach\filereach.log`). You can read what happened / what
  failed straight from the terminal — no need to screenshot.
- **Path paste in the search bar** — paste `C:\Users\iassh\OneDrive\Documents` (with or
  without a trailing query) to search that folder directly. Also works in the folder
  picker's search bar (Enter to jump in).
- **Live (no-index) search** — new `/api/live_search`, time-boxed (25s) and capped.
- **Version + status** — new `/api/version`; status shows version, platform, log path.
- Extension searches are not "classified" as a type; results show their category tag so
  you can see where files fall.

## [v1.1.0] — 2026-06-30 — *Features & UX*

### Fixed
- **Critical UX bug:** typing an extension (e.g. `.md`) while a Type chip was active
  returned zero results (extension + category were ANDed). An explicit extension now
  overrides the type filter.
- **Repeated searches:** typing/clicking no longer fires a burst of identical requests;
  search is debounced (300 ms) with in-flight request deduplication.

### Added
- **Dark mode** — warm-charcoal theme that keeps the Linen palette; toggle in the top bar,
  persists across sessions, respects OS `prefers-color-scheme` on first run.
- **Idle disk overview** — the home screen now shows total/used/free capacity per drive.
- **System folder picker (Browse)** — opens the native OS folder dialog (Chrome/Edge) and
  runs an instant client-side search over the picked folder; no index needed. Falls back
  to the in-app browser elsewhere.
- **In-picker search** — filter folders by name while browsing.
- **Folder sizes** — real recursive size + file count shown next to each folder (lazy, cached).
- **Autofocus** — pressing any letter/digit/`.` focuses the search box; **Ctrl+K** too.
- New endpoints: `/api/disk`, `/api/folder_sizes`.
- **FEATURES.md** — a complete feature guide.

## [v1.0.1] — 2026-06-30 — *Security & packaging hotfix*

Remediates the issues in the [Security Audit 2026-06-30](docs/SECURITY_AUDIT_2026-06-30.md).
**The `v1.0.0` binaries are superseded — please use `v1.0.1`.**

### Fixed
- **Critical:** frozen-path resolution now uses `sys._MEIPASS`, so packaged
  Windows/macOS/Linux binaries correctly locate the bundled UI (v1.0.0 builds were
  fragile/blank).
- **High:** added Same-Origin enforcement — cross-origin (CSRF / drive-by) requests to the
  local API are now rejected with `403`.
- **High:** SQLite `busy_timeout=6000` plus an `is_busy()` re-entry guard (`409` on a
  second concurrent index) to eliminate "database is locked" races.
- **Medium:** `?limit=` now tolerates non-numeric input instead of returning HTTP 500.
- **Medium:** `api_status` rewritten (removed dead `search_roots()` reference).
- **Medium:** OCR now explicitly guards against Pillow decompression bombs.
- **Medium:** fixed broken extension detection in OCR hint extraction.
- **Low:** removed unused imports / dead variables (`pyflakes` is now clean).
- Added `.gitattributes` to normalise line endings (LF in repo, CRLF for `.bat`).

## [v1.0.0] — 2026-06-30 — *Initial release*

- Read-only, local-first file search for large drives (475 GB+).
- SQLite (WAL) index, `os.scandir` walk, fuzzy relevance ranking.
- Linen-style UI: multi-name search, extension/type filters, folder scope, sorting.
- Per-extension stats: count, total size, first/last created.
- Read-only viewer: Markdown, HTML (sandboxed), syntax-highlighted code, images;
  ←/→ navigation.
- OCR-driven search from pasted photos (optional, via Tesseract).
- Single-UAC Windows launcher; cross-platform PyInstaller CI builds → Releases.
