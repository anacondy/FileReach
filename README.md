# 🔎 FileReach

> **Search your entire PC in milliseconds — without ever moving, copying, or deleting a file.**

FileReach is a fast, strictly **read-only** local file search tool, designed for large
drives (tested for libraries of 475 GB+). It indexes your storage once, then answers
queries almost instantly from a calm, beautiful interface. It bundles no destructive
operations: there is no delete, no rename, no copy. Your files are untouchable.

The interface follows a warm "Linen" aesthetic — cream canvas, coral accents, the Inter
typeface, soft shadows, and subtle 3D motion.

---

## 🎯 Project Goals

1. **Instant search over massive local libraries.** A 475 GB+ collection should be searchable
   in milliseconds — after a one-time index, not a full-disk scan every query.
2. **Zero risk to user data.** Strictly read-only: never delete, never duplicate, never move.
   The only writes the app ever makes are to its own index DB and log.
3. **One permission, then frictionless.** A single Administrator prompt grants access to every
   folder; the user is never re-prompted mid-search.
4. **Smart, not just literal.** Fuzzy relevance ranking when there's no exact match; type,
   extension, and date intelligence; OCR-driven search from photos of documents.
5. **Beautiful and calm.** A clean, well-spaced interface (Linen design system) — no clutter,
   no congestion.
6. **Cross-platform & distributable.** One source tree; prebuilt binaries for Windows, macOS,
   and Linux published to the Releases page via CI.

---

## ✅ The four hard rules (enforced in code)

| Rule | How it's guaranteed |
|------|---------------------|
| **Never delete a file** | No `os.remove`, `shutil.rmtree`, or any delete call exists in the source. |
| **Never duplicate a file** | File bytes are never copied — only metadata and (for the viewer) read-only content are read. |
| **Read-only everywhere** | Files are opened `'rb'`/`'r'` only. The only writes are the index DB + log, stored in `%LOCALAPPDATA%\FileReach` (or `~/.filereach`). |
| **Single permission prompt** | `run.py` elevates to Administrator **once** (source); the Windows build embeds a `requireAdministrator` manifest so the `.exe` does the same. |

---

## ✨ Features

- **Paste several names at once** (one per line or comma-separated). Results are
  relevance-ranked; when there's no exact match, the **most related** files appear with a
  `% related` badge (fuzzy matching via `rapidfuzz`, stdlib `difflib` fallback).
- **Search by extension** (`.mp4`, `.pdf`, `.psd`…) and instantly get:
  - exact **count**
  - **total size** (auto-scaled B → KB → MB → GB → TB → PB)
  - **first** and **last** file of that type created on the PC
- **Search by category** — one click: Images, Videos, Audio, Documents, Code, Archives.
- **Scope to a folder** — browse drives/directories and search only inside one.
- **Sort** — best match, A→Z / Z→A, newest/oldest modified or created, largest/smallest.
- **Every result shows** name, full path, size, and date, plus:
  - 📁 **reveal in File Explorer** (file selected)
  - 📋 **copy full path**
- **Built-in read-only viewer:**
  - **Markdown** → rendered
  - **HTML** → rendered live (sandboxed, scripts off) or as source
  - **Code** (`.py .js .jsx .ts .html .css .java …`) → syntax-highlighted
  - **Images** → displayed
  - Navigate results with **← / →**; close with **Esc**.
- **Paste a photo → search.** Drop/paste an image into the side panel; FileReach OCRs the
  text and offers one-click smart searches (detected names, extensions, dates), trying name
  + date combinations to track down renamed files.

---

## 🚀 Getting started

### Option A — Download a prebuilt binary (no Python needed)

1. Go to the **[Releases page](../../releases)**.
2. Download the build for your OS:
   - **Windows:** `FileReach-Windows.zip`
   - **macOS:** `FileReach-Mac.zip`
   - **Linux:** `FileReach-Linux.zip`
3. Unzip and run:
   - **Windows:** double-click `FileReach.exe` (click **Yes** once on the permission prompt).
   - **macOS:** run `./FileReach` (see note below about Gatekeeper).
   - **Linux:** `./FileReach`
4. Your browser opens at `http://127.0.0.1:8765`. Click **Re-index**, pick a drive, done.

> **macOS note:** Builds from GitHub Actions are not code-signed/notarized, so the first run
> needs: *System Settings → Privacy & Security → Open Anyway*. Or, from Terminal:
> `xattr -dr com.apple.quarantine /path/to/FileReach`.

### Option B — Run from source

**Windows:**
1. Install **Python 3.10+** from <https://www.python.org/downloads/>
   (tick **"Add Python to PATH"**).
2. **Double-click `start.bat`** → click **Yes** on the single permission prompt.
3. First run auto-creates a private `.venv` and installs dependencies; the browser opens
   automatically.

**macOS / Linux:**
```bash
cd filereach
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

---

## 🖼️ Enabling OCR (image → text) — optional

OCR uses **Tesseract**. The app runs perfectly without it and activates automatically when
Tesseract is detected.

**One-time install (Windows):**
1. Download the installer from
   <https://github.com/UB-Mannheim/tesseract/wiki> (`tesseract-ocr-w64-setup-…exe`).
2. Install with defaults (`C:\Program Files\Tesseract-OCR`).
3. Restart FileReach. The side panel will read **"Ready — drop a photo"**.

**macOS:** `brew install tesseract`
**Linux:** `sudo apt install tesseract-ocr`

---

## ⚡ Performance on big drives

- The first **index** walks every folder with `os.scandir` (the fastest method) and stores
  metadata in a **SQLite (WAL)** database with indexed columns. For hundreds of GB this is
  typically minutes to ~30 min, depending on drive speed and file count.
- **Searches** after that are SQLite queries — effectively instant (milliseconds).
- **Re-indexing is incremental** — only the selected root is refreshed; `INSERT OR REPLACE`
  updates sizes/dates in place.
- Live progress is shown in the header (`Indexing… 1,204,330 files`).

---

## 🏗️ Building from source

### Build a standalone binary locally
```bash
pip install -r requirements.txt pyinstaller
python make_icon.py            # generates assets/icon.ico (+ .icns on macOS)
pyinstaller filereach.spec --noconfirm
```
Output lands in `dist/FileReach/`.

### CI builds & releases
The workflow in [`.github/workflows/build.yml`](.github/workflows/build.yml) builds all three
platforms on every tag push (`v*`) and attaches the zips to a GitHub Release.

```bash
git tag v1.0.0
git push origin v1.0.0     # triggers Windows + macOS + Linux builds → Releases page
```
You can also run it manually from the **Actions** tab (`workflow_dispatch`) to produce
downloadable artifacts without publishing a release.

---

## 🧱 Architecture

```
filereach/
├── start.bat              # Windows one-click launcher (source)
├── run.py                 # bootstrap: single UAC elevation + venv + deps + launch
├── app.py                 # Flask API server; hosts the UI; frozen-aware
├── engine.py              # read-only indexer + search engine (SQLite)
├── filereach.spec         # PyInstaller spec (cross-platform; Windows uac_admin)
├── make_icon.py           # generates .ico / .icns from assets/icon.png
├── requirements.txt
├── assets/
│   └── icon.png           # source icon (checked in)
├── static/
│   └── index.html         # the Linen-style UI (single file)
└── .github/workflows/
    └── build.yml          # multi-platform CI build & release
```

**Data separation:** your files are never modified. FileReach's own state lives in
`%LOCALAPPDATA%\FileReach\` (Windows) or `~/.filereach/` (macOS/Linux): the index DB and a
small log. The server binds to `127.0.0.1` only — nothing leaves your machine.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Keep the four hard rules sacred: read-only, no
deletes, no duplicates, single permission.

## 📄 License

[MIT](LICENSE) — © 2026 FileReach.
