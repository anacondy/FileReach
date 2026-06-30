"""
FileReach — Flask API server + static UI host.

Run with:  python app.py        Then open: http://127.0.0.1:8765

Observability:
  * Every search / index / OCR is logged to BOTH the console (pretty, with elapsed
    time and result counts) and to the log file in the user data dir.
  * A version banner prints on startup.
  * When the index returns nothing but a folder is in scope, the UI automatically
    falls back to a live (no-index) filesystem walk.

Security posture:
  * READ-ONLY re: your files (opens 'rb'/'r' only). Binds to 127.0.0.1 only.
  * Same-Origin enforcement blocks CSRF / drive-by requests.
  * The only writes are this app's own index DB + log in the user data dir.
"""

import os
import sys
import io
import re
import time
import platform
import threading
import traceback
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, abort, g

from engine import (
    Indexer, SearchEngine, list_drives, list_dirs, reveal_in_explorer,
    read_file_text, TYPE_CATEGORIES, TEXT_VIEWABLE, IMAGE_VIEWABLE,
    RENDERABLE_HTML, human_size, human_date, human_date_short, is_windows,
    disk_info, folder_size, live_search, looks_like_path, find_folders, VERSION,
)

PORT = int(os.environ.get("FILEREACH_PORT", "8765"))

# --------------------------------------------------------------------------- #
#  Paths — frozen-aware so PyInstaller builds locate the bundled UI.
# --------------------------------------------------------------------------- #
if getattr(sys, "frozen", False):
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")

if is_windows():
    _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    DATA_DIR = os.path.join(_base, "FileReach")
else:
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".filereach")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "index.db")
LOG_PATH = os.path.join(DATA_DIR, "filereach.log")

# --------------------------------------------------------------------------- #
#  Structured logger — console (pretty) + file (durable)
# --------------------------------------------------------------------------- #
# Minimal ANSI colours; harmless on terminals that don't support them.
_C = {
    "RST": "\033[0m", "DIM": "\033[2m", "B": "\033[1m",
    "GRN": "\033[32m", "YEL": "\033[33m", "RED": "\033[31m",
    "CYN": "\033[36m", "GRY": "\033[90m", "MAG": "\033[35m",
}


class AppLog:
    """Thread-safe logger that prints to the console AND appends to a file."""

    def __init__(self, path, version):
        self.path = path
        self.version = version
        self._lock = threading.Lock()

    def _ts(self):
        return datetime.now().strftime("%H:%M:%S")

    def _write(self, level, msg, color, console=True):
        line = f"[{self._ts()}] [{level:<5}] {msg}"
        with self._lock:
            if console:
                cline = f"{color}{line}{_C['RST']}"
                try:
                    print(cline, flush=True)
                except Exception:
                    print(line, flush=True)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def info(self, m):    self._write("INFO", m, _C["CYN"])
    def ok(self, m):     self._write("OK", m, _C["GRN"])
    def warn(self, m):   self._write("WARN", m, _C["YEL"])
    def error(self, m):  self._write("ERROR", m, _C["RED"])
    def dim(self, m):    self._write("DIM", m, _C["GRY"], console=True)

    def banner(self, lines):
        bar = "═" * 58
        with self._lock:
            for i, ln in enumerate(lines):
                color = _C["B"] if i == 0 or i == len(lines) - 1 else _C["CYN"]
                print(f"{color}{ln}{_C['RST']}", flush=True)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write("\n" + bar + "\n" + "\n".join(lines) + "\n" + bar + "\n")
            except Exception:
                pass


log = AppLog(LOG_PATH, VERSION)


def log_exception(where, exc):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[:4])
    log.error(f"{where}: {exc}\n{tb}")


# --------------------------------------------------------------------------- #
#  OCR (Tesseract) — optional, graceful fallback
# --------------------------------------------------------------------------- #
OCR = {"available": False, "pytesseract": None, "path": None}


def init_ocr():
    try:
        import pytesseract  # type: ignore
        candidates = []
        if is_windows():
            pf = os.environ.get("ProgramFiles", r"C:\Program Files")
            pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            for base in (pf, pf86):
                tdir = os.path.join(base, "Tesseract-OCR")
                if os.path.isdir(tdir) and os.path.isfile(os.path.join(tdir, "tesseract.exe")):
                    candidates.append(os.path.join(tdir, "tesseract.exe"))
        chosen = candidates[0] if candidates else None
        if chosen:
            pytesseract.pytesseract.tesseract_cmd = chosen
        ver = pytesseract.get_tesseract_version()
        OCR.update({"available": True, "pytesseract": pytesseract, "path": chosen})
        log.ok(f"OCR ready — Tesseract {ver}" + (f" @ {chosen}" if chosen else " (PATH)"))
    except Exception as e:
        log.warn(f"OCR unavailable: {e}  (image→text feature disabled; core search works)")
        OCR["available"] = False


# --------------------------------------------------------------------------- #
#  App + singletons
# --------------------------------------------------------------------------- #
app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

indexer = Indexer(DB_PATH)
indexer.connect()
search = SearchEngine(indexer.conn)

_indexed_before = search.count_all()


def json_error(msg, code=400):
    return jsonify({"error": msg}), code


# --------------------------------------------------------------------------- #
#  Same-Origin enforcement (CSRF guard)
# --------------------------------------------------------------------------- #
@app.before_request
def _enforce_same_origin():
    if request.path.startswith("/api/"):
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if origin:
            host = request.headers.get("Host")
            if host:
                ok = False
                for scheme in ("http://", "https://"):
                    if origin.startswith(scheme + host):
                        ok = True
                        break
                    rest = origin[len(scheme):]
                    if rest.startswith(host + "/") or rest == host:
                        ok = True
                        break
                if not ok:
                    log.warn(f"Blocked cross-origin request from {origin}")
                    return jsonify({"error": "cross-origin request blocked"}), 403
    g.req_start = time.time()


@app.after_request
def _access_log(resp):
    try:
        if request.path.startswith("/api/") and request.method == "GET":
            ms = int((time.time() - getattr(g, "req_start", time.time())) * 1000)
            q = request.args.get("q")
            ext = request.args.get("ext")
            ftype = request.args.get("type")
            folder = request.args.get("folder")
            parts = []
            if q: parts.append(f"q={q!r}")
            if ext: parts.append(f"ext={ext}")
            if ftype: parts.append(f"type={ftype}")
            if folder: parts.append(f"folder={folder}")
            detail = (" " + " ".join(parts)) if parts else ""
            count = ""
            try:
                if request.path in ("/api/search", "/api/live_search") and resp.is_json:
                    count = f" → {resp.get_json().get('count', '?')} hits"
            except Exception:
                pass
            log.dim(f"{request.method} {request.path}{detail} {resp.status_code} {ms}ms{count}")
    except Exception:
        pass
    # ---- Cache-busting: never let the browser serve a stale UI ----
    # This is what fixes "I updated the files but the old UI still shows".
    if request.path == "/" or request.path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    elif request.path.startswith("/api/status") or request.path.startswith("/api/version"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/version")
def api_version():
    return jsonify({
        "version": VERSION,
        "platform": platform.system(),
        "python": platform.python_version(),
        "data_dir": DATA_DIR,
        "log_path": LOG_PATH,
        "ocr_available": OCR["available"],
        "fuzzy_used": True,
    })


@app.route("/api/status")
def api_status():
    st = indexer.get_status()
    st["version"] = VERSION
    st["ocr_available"] = OCR["available"]
    st["ocr_path"] = OCR["path"]
    st["platform"] = platform.system()
    st["log_path"] = LOG_PATH
    rows = indexer.conn.execute(
        "SELECT root, COUNT(*) n FROM files GROUP BY root ORDER BY root"
    ).fetchall()
    st["indexed_roots"] = [dict(r) for r in rows]
    return jsonify(st)


@app.route("/api/index", methods=["POST"])
def api_index():
    if indexer.is_busy():
        log.warn("Index rejected — already indexing")
        return json_error("Already indexing — cancel the current run first", 409)
    data = request.get_json(silent=True) or {}
    root = (data.get("root") or "").strip()
    incremental = data.get("incremental", True)
    if not root:
        return json_error("Missing 'root' path")
    if not os.path.exists(root):
        log.error(f"Index request for missing path: {root}")
        return json_error(f"Path not found: {root}")
    log.info(f"Index started: {root} (incremental={bool(incremental)})")
    ok = indexer.index(root, incremental=bool(incremental))
    return jsonify({"started": ok, "status": indexer.get_status()})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    indexer.cancel()
    log.warn("Index cancel requested")
    return jsonify({"ok": True})


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    ext = request.args.get("ext", "").strip()
    ftype = request.args.get("type", "").strip().lower()
    folder = request.args.get("folder", "").strip()
    sort = request.args.get("sort", "relevance")
    try:
        limit = min(int(request.args.get("limit", 1000)), 5000)
    except (TypeError, ValueError):
        limit = 1000
    if not (q or ext or ftype):
        return json_error("Provide a query, extension, or type")

    res = search.search(
        query=q or None, ext=ext or None, ftype=ftype or None,
        folder=folder or None, sort=sort, limit=limit,
    )

    # Tell the UI whether the scope is indexed, so it can decide on a live fallback.
    indexed_scope = True if not folder else indexer.is_indexed(folder)
    if not indexed_scope:
        log.warn(f"Scope '{folder}' is NOT under an indexed root — UI may live-search.")

    for r in res["results"]:
        r["size_h"] = human_size(r["size"])
        r["created_h"] = human_date_short(r["created"])
        r["modified_h"] = human_date_short(r["modified"])
        r["is_viewable_text"] = (r["ext"] or "") in TEXT_VIEWABLE
        r["is_image"] = (r["ext"] or "") in IMAGE_VIEWABLE
        r["is_renderable"] = (r["ext"] or "") in RENDERABLE_HTML

    res["scope_indexed"] = indexed_scope
    return jsonify(res)


@app.route("/api/live_search")
def api_live():
    """Live (no-index) walk. Used for pasted paths and as a fallback."""
    path = request.args.get("path", "").strip()
    q = request.args.get("q", "").strip()
    ext = request.args.get("ext", "").strip()
    ftype = request.args.get("type", "").strip().lower()
    try:
        limit = min(int(request.args.get("limit", 1500)), 5000)
    except (TypeError, ValueError):
        limit = 1500
    if not path:
        return json_error("Missing 'path'")
    if not os.path.isdir(path):
        return json_error(f"Not a folder: {path}")
    log.info(f"LIVE search: {path}  q={q!r} ext={ext} type={ftype}")
    res = live_search(path, query=q or None, ext=ext or None,
                      ftype=ftype or None, limit=limit, timeout=25.0)
    for r in res["results"]:
        r["is_viewable_text"] = (r["ext"] or "") in TEXT_VIEWABLE
        r["is_image"] = (r["ext"] or "") in IMAGE_VIEWABLE
        r["is_renderable"] = (r["ext"] or "") in RENDERABLE_HTML
    tag = ""
    if res["timed_out"]:
        tag = " (timed out — partial)"
        log.warn(f"Live search timed out at {res.get('elapsed')}s, scanned {res['scanned']}")
    elif res["truncated"]:
        tag = " (capped)"
    log.ok(f"Live search done: {res['count']} hits, scanned {res['scanned']} "
           f"in {res.get('elapsed')}s{tag}")
    return jsonify(res)


@app.route("/api/stats")
def api_stats():
    ext = request.args.get("ext", "").strip()
    ftype = request.args.get("type", "").strip().lower()
    folder = request.args.get("folder", "").strip()
    if not (ext or ftype):
        return json_error("Provide an extension or type")
    st = search.stats(ext=ext or None, ftype=ftype or None, folder=folder or None)
    return jsonify(st)


@app.route("/api/overview")
def api_overview():
    return jsonify(search.overview())


@app.route("/api/drives")
def api_drives():
    return jsonify({"drives": list_drives()})


@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "")
    dirs, real = list_dirs(path)
    parent = ""
    if real and real not in ("/", ""):
        p = os.path.dirname(real)
        if p and p != real:
            parent = p
    return jsonify({"current": real, "parent": parent, "dirs": dirs})


@app.route("/api/find_folders")
def api_find_folders():
    """Global folder search across ALL indexed roots (so you can find a folder
    by name regardless of where you're browsing)."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"folders": [], "indexed": search.count_all() > 0})
    folders = find_folders(indexer.conn, q)
    return jsonify({"folders": folders, "count": len(folders),
                    "indexed": search.count_all() > 0})


@app.route("/api/resolve_path")
def api_resolve_path():
    """Tell the UI whether a pasted path is a folder or a file (or doesn't exist)."""
    path = request.args.get("path", "").strip().strip('"').strip("'")
    if not path:
        return jsonify({"exists": False})
    abspath = os.path.abspath(path)
    if os.path.isdir(abspath):
        return jsonify({"exists": True, "is_dir": True, "is_file": False,
                        "path": abspath, "name": os.path.basename(abspath)})
    if os.path.isfile(abspath):
        return jsonify({"exists": True, "is_dir": False, "is_file": True,
                        "path": abspath, "parent": os.path.dirname(abspath),
                        "name": os.path.basename(abspath)})
    return jsonify({"exists": False, "path": abspath})


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return json_error("Invalid path")
    try:
        reveal_in_explorer(path)
        log.dim(f"Reveal: {path}")
        return jsonify({"ok": True})
    except Exception as e:
        log_exception("reveal", e)
        return json_error(str(e), 500)


@app.route("/api/file")
def api_file():
    path = request.args.get("path", "").strip()
    if not path:
        return json_error("Missing path")
    if not os.path.exists(path):
        return json_error("File not found", 404)
    try:
        st = os.stat(path)
    except OSError as e:
        log_exception("stat", e)
        return json_error(str(e), 500)
    ext = os.path.splitext(path)[1].lower()
    meta = {
        "path": os.path.abspath(path), "name": os.path.basename(path), "ext": ext,
        "size": st.st_size, "size_h": human_size(st.st_size),
        "created": st.st_ctime, "modified": st.st_mtime,
        "created_h": human_date(st.st_ctime), "modified_h": human_date(st.st_mtime),
        "is_image": ext in IMAGE_VIEWABLE, "is_text": ext in TEXT_VIEWABLE,
        "is_renderable": ext in RENDERABLE_HTML, "content": None,
    }
    if ext in TEXT_VIEWABLE and st.st_size < 5_000_000:
        text, _ = read_file_text(path, limit=2_000_000)
        meta["content"] = text
    return jsonify(meta)


@app.route("/api/raw")
def api_raw():
    path = request.args.get("path", "").strip()
    if not path or not os.path.exists(path):
        abort(404)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "svg": "image/svg+xml", "ico": "image/x-icon",
    }.get(ext, "application/octet-stream")
    try:
        directory, fname = os.path.split(os.path.abspath(path))
        return send_from_directory(directory, fname, mimetype=mime)
    except Exception:
        abort(404)


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if not OCR["available"]:
        return jsonify({
            "available": False,
            "message": ("OCR is not installed. Install Tesseract (one-time): "
                        "https://github.com/UB-Mannheim/tesseract/wiki then restart FileReach."),
        }), 200
    files = request.files.getlist("images")
    if not files:
        return json_error("No images uploaded")
    from PIL import Image  # type: ignore
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, 89_000_000)
    pytesseract = OCR["pytesseract"]
    full_text, pages = [], []
    for f in files:
        try:
            f.stream.seek(0)
            img = Image.open(io.BytesIO(f.read()))
            img.load()
            text = pytesseract.image_to_string(img).strip()
            full_text.append(text)
            pages.append({"name": f.filename, "text": text})
        except Image.DecompressionBombError:
            pages.append({"name": f.filename, "error": "image too large (bomb guard)"})
        except Exception as e:
            pages.append({"name": f.filename, "error": str(e)})
    combined = "\n\n".join(t for t in full_text if t).strip()
    log.ok(f"OCR: {len(files)} image(s) → {len(combined)} chars of text")
    return jsonify({
        "available": True, "text": combined, "pages": pages,
        "suggestions": extract_search_hints(combined),
    })


def extract_search_hints(text):
    if not text:
        return {"names": [], "extensions": [], "dates": [], "queries": []}
    all_ext_sets = list(TYPE_CATEGORIES.values())
    exts = sorted(set(re.findall(r"\.([a-zA-Z0-9]{2,5})\b", text.lower())))
    exts = [e for e in exts
            if any(("." + e) in s for s in all_ext_sets)
            or e in ("pdf", "txt", "md", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                     "csv", "mp3", "mp4", "mkv", "zip", "rar", "7z", "exe", "psd", "ai")]
    date_pats = [r"\b(\d{4}-\d{1,2}-\d{1,2})\b",
                 r"\b(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\b",
                 r"\b(\d{1,2}-\d{1,2}-\d{2,4})\b"]
    dates = sorted({d for p in date_pats for d in re.findall(p, text)})
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text)
    stop = {"the", "and", "for", "with", "this", "that", "from", "have", "your", "you",
            "are", "was", "were", "but", "not", "all", "can", "will", "been", "into",
            "they", "them", "their", "there", "here", "when", "where", "which", "what",
            "who", "how", "why", "date", "time", "name", "file", "document", "page",
            "image", "photo", "picture"}
    counts = {}
    for t in tokens:
        if t.lower() in stop:
            continue
        counts[t.lower()] = counts.get(t.lower(), 0) + 1
    names = sorted(counts, key=lambda k: (counts[k], len(k)), reverse=True)[:8]
    return {"names": names, "extensions": ["." + e for e in exts], "dates": dates,
            "queries": list(names[:5])}


@app.route("/api/types")
def api_types():
    return jsonify({
        "categories": {k: sorted(v) for k, v in TYPE_CATEGORIES.items()},
        "viewable_text": sorted(TEXT_VIEWABLE),
        "images": sorted(IMAGE_VIEWABLE),
        "renderable": sorted(RENDERABLE_HTML),
    })


@app.route("/api/disk")
def api_disk():
    return jsonify({"disks": disk_info()})


@app.route("/api/folder_sizes")
def api_folder_sizes():
    raw = request.args.get("paths", "")
    paths = [p for p in raw.split("|") if p and os.path.isdir(p)]
    return jsonify({p: folder_size(p) for p in paths})


@app.errorhandler(404)
def _h404(e):
    return jsonify({"error": "not found", "path": request.path}), 404


@app.errorhandler(500)
def _h500(e):
    log_exception(f"500 on {request.path}", e)
    return jsonify({"error": "internal error", "detail": str(e)}), 500


# --------------------------------------------------------------------------- #
#  Entrypoint
# --------------------------------------------------------------------------- #
def main():
    init_ocr()
    log.banner([
        "",
        "  ╔══════════════════════════════════════════════════╗",
        "  ║          FileReach  ·  read-only search           ║",
       f"  ║          version {VERSION:<34}     ║",
        "  ╚══════════════════════════════════════════════════╝",
        "",
        f"  Platform     : {platform.system()} {platform.release()}",
        f"  Python       : {platform.python_version()}",
        f"  Data folder  : {DATA_DIR}",
        f"  Log file     : {LOG_PATH}",
        f"  Index DB     : {DB_PATH}  ({_indexed_before:,} files already indexed)",
        f"  OCR          : {'ready (Tesseract)' if OCR['available'] else 'not installed — image→text disabled'}",
        f"  Fuzzy match  : {'rapidfuzz' if True else 'difflib'}",
        f"  Web UI       : http://127.0.0.1:{PORT}",
        "",
        "  Tip: paste a path like  C:\\Users\\iassh\\Documents  in the search box",
        "       to search that folder directly (works even without an index).",
        "",
    ])
    try:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    except Exception:
        pass
    try:
        # disable Flask's own noisy logger so our pretty one is the single source.
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        log.warn("Stopped by user (Ctrl+C)")
    except Exception as e:
        log_exception("server", e)


if __name__ == "__main__":
    main()
