# Changelog

All notable changes to FileReach are documented here.
Format loosely based on [Keep a Changelog](https://keepachangelog.com/).

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
