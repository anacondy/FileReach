# FileReach — Feature Guide

A practical reference for **every feature in FileReach, what it does, and how to use it.**
For install/build steps, see the [README](README.md). For security posture, see the
[Audit](docs/SECURITY_AUDIT_2026-06-30.md).

---

## Search

| Feature | What it does | How to use |
|---|---|---|
| **Name search** | Matches file/folder names anywhere in the path (case-insensitive). Fuzzy-ranked by relevance. | Type a name and press **Enter**. Paste **several** at once (one per line or comma). |
| **Extension search** | Finds every file of an extension; shows **count**, **total size**, **first** and **last** created of that type. | Type the extension: `.pdf`, `.mp4`, `.psd`… |
| **Type chips** | One-click filters: Images, Videos, Audio, Documents, Code, Archives. | Click a chip. Click again to clear. |
| **Extension overrides type** | If you type `.md` while a type chip is on, the extension wins (no more empty results from conflicting filters). | Just type the extension — it works. |
| **Relevance / fuzzy** | When there's no exact match, the most-related files appear with a `% related` badge (uses `rapidfuzz`). | Automatic. |
| **Folder scope** | Restrict a search to one folder (must be indexed, or under an indexed root). | Click **Folder** → browse → **Search this folder only**. |
| **Debounced search** | Rapid typing/clicks collapse into a single request (stops the repeated calls). | Automatic — 300 ms after you stop. |

### Sorting
Best match · Name A→Z / Z→A · Newest/oldest modified · Newest/oldest created · Largest/smallest.
Change from the **Sort** dropdown.

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
