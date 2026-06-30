# FileReach — Feature Guide

A practical reference for **every feature in FileReach, what it does, and how to use it.**
For install/build steps, see the [README](README.md). For security posture, see the
[Audit](docs/SECURITY_AUDIT_2026-06-30.md).

---

## Search

| Feature | What it does | How to use |
|---|---|---|
| **Name search** | Matches file/folder names anywhere (case-insensitive), fuzzy-ranked. | Type a name, press **Enter**. Paste several at once. |
| **Paste a path** | Searches a host folder directly — **works even if it's not indexed.** | Paste e.g. `C:\Users\iassh\OneDrive\Documents` (optionally followed by a query). |
| **Live fallback** | If a scoped folder isn't indexed, FileReach walks it live so you still get results. | Automatic. |
| **Extension search** | All files of an extension; shows count, total size, first/last created. Not "typed" — it overrides the type chip. | Type `.pdf`, `.mp4`… |
| **Type chips** | Filter by category. When an extension is typed, the chip is ignored and each result shows its category tag instead. | Click a chip. |
| **Folder scope** | Restrict to one folder. | **Folder** → browse → pick, or paste the path. |
| **Debounced** | Rapid typing collapses into one request. | Automatic (300 ms). |

### Sorting
Best match · Name A→Z / Z→A · Newest/oldest modified or created · Largest/smallest.

---

## Logs & feedback (read the terminal — no screenshots needed)

When you run FileReach, the terminal/console shows:

- A **version banner** (version, platform, Python, data folder, log path, OCR status).
- A coloured line per action, e.g. `LIVE search: C:\Users\... q='3'` →
  `Live search done: 3 hits, scanned 4 in 0.0s`.
- `WARN` when a scope isn't indexed (so you know why results may be live), and when a
  cross-origin request is blocked.
- `ERROR` with a traceback on failures.

Everything is also written to `%LOCALAPPDATA%\FileReach\filereach.log`
(macOS/Linux: `~/.filereach/filereach.log`). Open it with any text editor.

---

## Keyboard & focus

| Shortcut | Action |
|---|---|
| **Ctrl + K** (or ⌘K) | Focus the search box. |
| **Any letter / digit / `.`** | Auto-focuses the search box when you're not already typing somewhere. |
| **Enter** | Search now (bypasses debounce). |
| **← / →** | Previous / next file in the viewer. |
| **Esc** | Close viewer / folder picker / OCR panel. |

---

## Result actions

Each result shows name, full path, size, and date.
- 👁️ **View** — open the read-only viewer (when previewable).
- 📁 **path icon** — reveal the file **selected** in File Explorer / Finder.
- 📋 **copy icon** — copy the full path to the clipboard.

---

## Viewer (read-only — never edits files)

| File type | Behaviour |
|---|---|
| **Markdown** (`.md`) | Rendered (headings, lists, code, links with `rel="noopener"`). |
| **HTML** (`.html`) | Rendered live in a **sandboxed** iframe (scripts off for safety), or switch to **Source**. |
| **Code** (`.py .js .jsx .ts .css .java …`) | Syntax-highlighted. |
| **Images** (`.png .jpg .gif .svg …`) | Displayed inline. |

Navigate the result list with **← / →**; the viewer shows position (e.g. `3 / 120`).

---

## Folders & browsing

| Feature | What it does |
|---|---|
| **Browse button** (topbar) | Opens the **system folder dialog** (Chrome/Edge `showDirectoryPicker`) and runs an instant **client-side** search over the picked folder — **no index needed**. Falls back to the in-app browser on Firefox/Safari. |
| **Folder button** (topbar) | In-app drive/folder browser to set a search scope. |
| **In-picker search** | Filter folders by name while browsing. |
| **Folder sizes** | Real recursive size + file count shown next to each folder (loads lazily, cached). |
| **Breadcrumbs** | Click any segment to jump up the path. |

> **Browse limitation:** browsers intentionally hide the absolute path of folders picked via the system dialog. Files from a **Browse**-picked folder are listed/sized client-side; use **Reveal** (works for indexed results) or the **Folder** browser when you need the real path.

---

## Idle state

When you haven't searched yet, the home screen shows **disk usage** for every drive —
total capacity, used, free, and a fill bar — so you see your storage at a glance.

---

## Dark mode

A proper warm-charcoal dark theme that keeps the Linen palette and accents.
- Toggle with the **sun/moon** button in the top bar.
- Remembers your choice (and respects your OS `prefers-color-scheme` on first run).

---

## Indexing & speed

- **Re-index** → pick a drive/folder → FileReach walks it with `os.scandir` (fastest method)
  and stores metadata in **SQLite (WAL)**.
- After indexing, searches are SQLite queries → **milliseconds**.
- **Incremental**: re-indexing a root refreshes only it; sizes/dates update in place
  (`INSERT OR REPLACE`).
- Live progress in the header (`Indexing… 1,204,330 files`).

---

## Image → Search (OCR, optional)

- Open the side panel with the **purple button** (bottom-right).
- **Drop, paste, or upload** a photo of a document/screenshot.
- FileReach extracts the text (Tesseract) and offers **one-click smart searches**
  (detected names, extensions, dates).
- Activates automatically once Tesseract is installed; the app runs fine without it.

**Install Tesseract (one-time):**
- Windows: <https://github.com/UB-Mannheim/tesseract/wiki>
- macOS: `brew install tesseract` · Linux: `sudo apt install tesseract-ocr`

---

## The four hard rules (still sacred)

1. **Never delete** a file — no destructive calls anywhere in the source.
2. **Never duplicate** a file — file bytes are never copied.
3. **Read-only** — files opened `'rb'`/`'r'` only.
4. **One permission** — single Administrator prompt (`run.py` / Windows manifest).
