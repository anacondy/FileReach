# FileReach — Security & Robustness Audit

**Report date:** 2026-06-30
**Scope:** `app.py`, `engine.py`, `static/index.html`, `run.py`, `filereach.spec`, `.github/workflows/build.yml`, `start.bat`
**Audited release:** `v1.0.0` (commit `003cd3a`)
**Status:** Findings triaged; **Critical / High issues remediated in this pass** (pending push as `v1.0.1`).

---

## 1. Executive summary

FileReach is a **local-only, read-only file search tool**. The threat model is correspondingly narrow: it binds to `127.0.0.1`, opens files with `'rb'`/`'r'` only, and writes nothing but its own index DB and log. The four product guarantees — **no deletes, no duplicates, read-only, single permission** — hold up under review: there is no destructive call anywhere in the source, and all SQL is parameterized (**no injection surface**).

That said, the `v1.0.0` commit shipped with one **Critical** packaging defect (broken frozen-path resolution → fragile/non-working packaged binaries) and several **High/Medium** hardening gaps (no CSRF protection on the localhost API, a shared SQLite connection across writer/reader threads, an unguarded `int()` cast, and dead code in `api_status`). These are fixed in this pass. Residual risk is low and documented in §7.

**Severity counts:** Critical 1 · High 2 · Medium 4 · Low 6.

---

## 2. Methodology

- Static review of all source + the PyInstaller spec and CI workflow.
- `pyflakes` for unused symbols / dead variables.
- Manual taint tracing of every user-reachable input (`query`, `path`, `ext`, `limit`, `folder`, OCR upload) to its sink (SQL, filesystem, subprocess).
- Automated integration tests against a live Flask test client (CSRF same/cross-origin, malformed input, concurrency guard, folder search).

---

## 3. Critical findings

### 🔴 C1 — Frozen-path resolution absent (broken packaged binaries)
**Where:** `app.py` (Paths section) in `v1.0.0`.
**Problem:** The committed file resolved `STATIC_DIR` purely from `__file__`:

```python
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
```

`__file__` is unreliable inside a PyInstaller bundle. In **onefile** mode the UI is never found (blank page / 404 on `/`). In **onefolder** mode it works *by luck* because `_MEIPASS` happens to equal the dist dir. The CI workflow (`build.yml`) is publishing Windows/macOS/Linux zips built from exactly this code, so the v1.0.0 release is at best fragile and at worst non-functional for end users who download the binary.
**Fix applied:** Added explicit frozen detection — `sys._MEIPASS` when `getattr(sys, "frozen", False)`, else source dir. Bundled resources now resolve correctly in all build modes.

---

## 4. High findings

### 🟠 H1 — No CSRF / cross-origin protection on the localhost API
**Where:** `app.py` (all routes), `v1.0.0`.
**Problem:** The server listens on `127.0.0.1` with no authentication and no origin check. While a user has FileReach running, **any web page in their browser** can issue `GET /api/index?...` / `POST`-style calls to the local port (simple cross-origin requests are *sent* even though responses are CORS-blocked). Impact is bounded by the read-only design but includes: triggering a full-disk re-index (local DoS / CPU+disk thrash), forcing `explorer` pop-ups via `/api/reveal`, and forcing OCR work. A preflighted JSON `POST` is mostly blocked by CORS, but `GET` and form `POST` are not.
**Fix applied:** Added a `@app.before_request` same-origin guard. If an `Origin`/`Referer` header is present and does not match the request's own `Host`, the request is rejected with `403`. Requests with no origin header (direct navigation, the app itself, cURL) pass through unaffected.

### 🟠 H2 — Shared SQLite connection across writer + reader threads
**Where:** `engine.py` (`Indexer.connect`), `v1.0.0`.
**Problem:** A single `sqlite3.Connection(check_same_thread=False)` is shared between the background indexing **writer** thread and the per-request **reader** threads. Under concurrent load this can raise `sqlite3.OperationalError: database table is locked` / `database is locked`, surfacing as intermittent HTTP 500s on search while a re-index runs. There was also **no re-entry guard**, so two rapid `POST /api/index` calls could spawn two writer threads corrupting the same rows.
**Fix applied:**
1. `PRAGMA busy_timeout=6000` — SQLite now waits up to 6 s for a lock instead of failing fast.
2. `Indexer.is_busy()` + an HTTP `409` in `/api/index` — a second index attempt is rejected until the current one finishes or is cancelled.
*(A connection-per-thread or a separate read replica remains the fully robust fix and is listed as a future hardening item — see §7.)*

---

## 5. Medium findings

### 🟡 M1 — Unguarded `int(limit)` → HTTP 500
**Where:** `app.py` (`api_search`), `v1.0.0`. `int(request.args.get("limit", 1000))` raised `ValueError` on `?limit=abc`.
**Fix:** wrapped in `try/except (TypeError, ValueError)` → defaults to 1000.

### 🟡 M2 — Dead/buggy code in `api_status`
**Where:** `app.py`, `v1.0.0`. The handler assigned `indexed_roots` three times, including a `indexer.search_roots() if hasattr(...)` line that referenced a method that **does not exist**. It never crashed only because of the `hasattr` guard, but it was sloppy and did redundant work.
**Fix:** replaced with a single clean grouped query.

### 🟡 M3 — Pillow decompression-bomb handling in OCR
**Where:** `app.py` (`api_ocr`). Pillow's default `MAX_IMAGE_PIXELS` already raises `DecompressionBombError` on pathological images, which is good — but it was not explicitly caught, so a bomb produced an opaque 500.
**Fix:** explicitly set/keep the guard and catch `Image.DecompressionBombError` → clean per-image error. Uploads are already capped at 32 MB by `MAX_CONTENT_LENGTH`.

### 🟡 M4 — Broken extension detection in OCR hint extraction
**Where:** `app.py` (`extract_search_hints`), `v1.0.0`. The filter `any(e in s for s in TYPE_CATEGORIES)` checked membership of a **dot-less** token (`pdf`) against sets of **dotted** extensions (`.pdf`) — always `False`. It worked only because of a hardcoded fallback tuple.
**Fix:** corrected to `any(("." + e) in s for s in TYPE_CATEGORIES.values())`.

---

## 6. Low findings

| ID | Finding | Disposition |
|----|---------|-------------|
| **L1** | Unused imports (`sys`, `json`, `time`, `wraps`, `Response` in `app.py`; `sys` in `engine.py`) | Removed (pyflakes now clean). |
| **L2** | Dead local variables (`base` in `stats()`, `as e` in `read_file_text`) | Removed. |
| **L3** | Flask **development server** used for distribution | *Accepted risk* for a local single-user tool; documented. A production WSGI (Waitress) is a future option. |
| **L4** | Markdown viewer renders `<a target="_blank">` without `rel="noopener"` | Minor; tab-nabbing only against the user's own local page. Future: add `rel="noopener noreferrer"`. |
| **L5** | `kind` column never holds `spreadsheets`/`presentations` (overlap with `documents`, first-match wins) | *By design* — documented inline; the type-chips filter correctly via the category sets directly. |
| **L6** | Sensitive data (file paths, OCR text) written to an unencrypted log in `%LOCALAPPDATA%\FileReach\` | *Accepted risk*; standard for local tools. Future: redact or make logging opt-in. |

---

## 7. Robustness assessment

| Dimension | Rating | Notes |
|-----------|--------|-------|
| **Data safety (no delete/duplicate)** | ✅ Excellent | Verified by grep: zero destructive calls; read-only opens only. |
| **SQL safety** | ✅ Excellent | 100% parameterized; `ORDER BY` and `IN` clauses built from fixed whitelists. |
| **Input validation** | ✅ Good (after fix) | `limit`, `ext`, `type`, paths all validated/sandboxed. |
| **Concurrency** | 🟡 Adequate (after fix) | `busy_timeout` + re-entry guard; per-thread connections are the next step. |
| **Packaging / distribution** | ✅ Good (after fix) | Frozen-path fixed; CI builds all three OSes; macOS unsigned (documented). |
| **Error handling** | ✅ Good | Walk errors counted, not fatal; OCR/upload errors isolated per file. |
| **Network exposure** | ✅ Excellent | `127.0.0.1`-only; now CSRF-hardened. |
| **XSS (UI)** | ✅ Good | Central `esc()` helper; viewer uses `textContent` / sandboxed iframe (scripts off). |

**Accepted residual risk:** multi-user shared machines (another local user could reach the port) and the dev-server choice (L3) are the only items a hardened deployment should revisit.

---

## 8. Remediation summary (this pass)

| Fix | Files | Severity |
|-----|-------|----------|
| Frozen-path resolution (`sys._MEIPASS`) | `app.py` | 🔴 C1 |
| Same-Origin CSRF enforcement | `app.py` | 🟠 H1 |
| `busy_timeout` + `is_busy()` + `409` re-entry guard | `engine.py`, `app.py` | 🟠 H2 |
| `int(limit)` try/except | `app.py` | 🟡 M1 |
| Clean `api_status` | `app.py` | 🟡 M2 |
| Pillow bomb guard + explicit catch | `app.py` | 🟡 M3 |
| OCR extension-detection logic | `app.py` | 🟡 M4 |
| Removed unused imports / dead vars | `app.py`, `engine.py` | ⚪ L1–L2 |
| `include_folders` wired through `SearchEngine.search()` | `engine.py`, `app.py` | (consistency) |
| `.gitattributes` (normalise LF, keep `.bat` CRLF) | `.gitattributes` | (tooling) |

All fixes verified: `pyflakes` clean, full integration test suite passes (same/cross-origin, malformed input, folder search, status grouping).

---

## 9. Recommended next steps

1. **Cut `v1.0.1`** from the fixed code and re-tag so CI republishes working binaries (the `v1.0.0` binaries on the Releases page should be treated as broken — see C1).
2. Consider per-thread SQLite read connections (or a read replica) to fully retire H2.
3. Optional: ship behind Waitress and add a one-time local auth token for defense-in-depth.
4. Add `rel="noopener noreferrer"` to rendered markdown links (L4).

---

*Prepared 2026-06-30. Re-run this audit after any change to `engine.py`, `app.py`, or the build pipeline.*
