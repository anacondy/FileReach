"""
FileReach — Flask API server + static UI host.

Run with:  python app.py
Then open: http://127.0.0.1:8765

Security posture:
  * READ-ONLY with respect to your files (opens 'rb'/'r' only).
  * Binds to 127.0.0.1 only (never exposed to the network).
  * Same-Origin enforcement: cross-origin requests (CSRF / drive-by) are rejected.
  * The only writes are this app's own index DB + log in the user data dir.
"""

import os
import sys
import io
import re
import platform
import threading
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, abort

from engine import (
    Indexer, SearchEngine, list_drives, list_dirs, reveal_in_explorer,
    read_file_text, TYPE_CATEGORIES, TEXT_VIEWABLE, IMAGE_VIEWABLE,
    RENDERABLE_HTML, human_size, human_date, human_date_short, is_windows,
    disk_info, folder_size,
)

PORT = int(os.environ.get("FILEREACH_PORT", "8765"))

# --------------------------------------------------------------------------- #
#  Paths — frozen-aware so PyInstaller builds locate the bundled UI.
#  When frozen, bundled resources (static/) live under sys._MEIPASS; all our
#  *writable* data stays in the user data dir, never next to the binary.
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
                if os.path.isdir(tdir):
                    exe = os.path.join(tdir, "tesseract.exe")
                    if os.path.isfile(exe):
                        candidates.append(exe)
        chosen = candidates[0] if candidates else None
        if chosen:
            pytesseract.pytesseract.tesseract_cmd = chosen
        pytesseract.get_tesseract_version()  # probe
        OCR.update({"available": True, "pytesseract": pytesseract, "path": chosen})
        log(f"OCR ready (Tesseract {pytesseract.get_tesseract_version()})")
    except Exception as e:
        log(f"OCR unavailable: {e}")
        OCR["available"] = False


def log(msg):
    try:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  App + singletons
# --------------------------------------------------------------------------- #
app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB upload cap (OCR)

indexer = Indexer(DB_PATH)
indexer.connect()
search = SearchEngine(indexer.conn)


def json_error(msg, code=400):
    return jsonify({"error": msg}), code


# --------------------------------------------------------------------------- #
#  Same-Origin enforcement — blocks CSRF / drive-by requests from other sites.
#  Browsers send `Origin` (or `Referer`) on cross-origin calls; if it is present
#  and does not match our own Host, we reject. Requests with no Origin header
#  (direct navigation, cURL, the app itself) are allowed through.
# --------------------------------------------------------------------------- #
@app.before_request
def _enforce_same_origin():
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return None
    host = request.headers.get("Host")
    if not host:
        return None
    for scheme in ("http://", "https://"):
        if origin.startswith(scheme + host):
            return None
        # Referer may carry a path after the host
        rest = origin[len(scheme):]
        if rest.startswith(host + "/") or rest == host:
            return None
    log(f"Blocked cross-origin request from {origin}")
    return jsonify({"error": "cross-origin request blocked"}), 403


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/status")
def api_status():
    st = indexer.get_status()
    st["ocr_available"] = OCR["available"]
    st["ocr_path"] = OCR["path"]
    st["platform"] = platform.system()
    rows = indexer.conn.execute(
        "SELECT root, COUNT(*) n FROM files GROUP BY root ORDER BY root"
    ).fetchall()
    st["indexed_roots"] = [dict(r) for r in rows]
    return jsonify(st)


@app.route("/api/index", methods=["POST"])
def api_index():
    if indexer.is_busy():
        return json_error("Already indexing — cancel the current run first", 409)
    data = request.get_json(silent=True) or {}
    root = (data.get("root") or "").strip()
    incremental = data.get("incremental", True)
    if not root:
        return json_error("Missing 'root' path")
    if not os.path.exists(root):
        return json_error(f"Path not found: {root}")
    ok = indexer.index(root, incremental=bool(incremental))
    return jsonify({"started": ok, "status": indexer.get_status()})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    indexer.cancel()
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
    include_folders = request.args.get("folders") in ("1", "true", "yes")

    if not (q or ext or ftype or include_folders):
        return json_error("Provide a query, extension, or type")

    res = search.search(
        query=q or None, ext=ext or None, ftype=ftype or None,
        folder=folder or None, sort=sort, limit=limit,
        include_folders=include_folders,
    )
    for r in res["results"]:
        r["size_h"] = human_size(r["size"])
        r["created_h"] = human_date_short(r["created"])
        r["modified_h"] = human_date_short(r["modified"])
        r["is_viewable_text"] = (r["ext"] or "") in TEXT_VIEWABLE
        r["is_image"] = (r["ext"] or "") in IMAGE_VIEWABLE
        r["is_renderable"] = (r["ext"] or "") in RENDERABLE_HTML
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


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return json_error("Invalid path")
    try:
        reveal_in_explorer(path)
        return jsonify({"ok": True})
    except Exception as e:
        return json_error(str(e), 500)


@app.route("/api/file")
def api_file():
    """Return metadata + (for viewable text) the content. Read-only."""
    path = request.args.get("path", "").strip()
    if not path:
        return json_error("Missing path")
    if not os.path.exists(path):
        return json_error("File not found", 404)
    try:
        st = os.stat(path)
    except OSError as e:
        return json_error(str(e), 500)

    ext = os.path.splitext(path)[1].lower()
    meta = {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "ext": ext,
        "size": st.st_size,
        "size_h": human_size(st.st_size),
        "created": st.st_ctime,
        "modified": st.st_mtime,
        "created_h": human_date(st.st_ctime),
        "modified_h": human_date(st.st_mtime),
        "is_image": ext in IMAGE_VIEWABLE,
        "is_text": ext in TEXT_VIEWABLE,
        "is_renderable": ext in RENDERABLE_HTML,
        "content": None,
    }
    if ext in TEXT_VIEWABLE and st.st_size < 5_000_000:
        text, _ = read_file_text(path, limit=2_000_000)
        meta["content"] = text
    return jsonify(meta)


@app.route("/api/raw")
def api_raw():
    """Serve a raw (image) file to the viewer. Read-only."""
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
    """Accept one or more images; return extracted text + suggested searches."""
    if not OCR["available"]:
        return jsonify({
            "available": False,
            "message": (
                "OCR is not installed. Install Tesseract (one-time): "
                "https://github.com/UB-Mannheim/tesseract/wiki then restart FileReach."
            ),
        }), 200

    files = request.files.getlist("images")
    if not files:
        return json_error("No images uploaded")

    from PIL import Image  # type: ignore
    # Pillow guards against decompression bombs by default (MAX_IMAGE_PIXELS);
    # keep that protection on and surface bombs as a clean error.
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, 89_000_000)
    pytesseract = OCR["pytesseract"]

    full_text, pages = [], []
    for f in files:
        try:
            f.stream.seek(0)
            img = Image.open(io.BytesIO(f.read()))
            img.load()  # force decode now so bombs raise here, not in tesseract
            text = pytesseract.image_to_string(img)
        except Image.DecompressionBombError:
            pages.append({"name": f.filename, "error": "image too large (bomb guard)"})
            continue
        except Exception as e:
            pages.append({"name": f.filename, "error": str(e)})
            continue
        text = text.strip()
        full_text.append(text)
        pages.append({"name": f.filename, "text": text})

    combined = "\n\n".join(t for t in full_text if t).strip()
    return jsonify({
        "available": True,
        "text": combined,
        "pages": pages,
        "suggestions": extract_search_hints(combined),
    })


def extract_search_hints(text):
    """
    Pull likely file names, extensions, and dates out of OCR text so the UI can
    offer one-click 'smart' searches (permutations + relevance fallback).
    """
    if not text:
        return {"names": [], "extensions": [], "dates": [], "queries": []}

    all_ext_sets = list(TYPE_CATEGORIES.values())
    exts = sorted(set(re.findall(r"\.([a-zA-Z0-9]{2,5})\b", text.lower())))
    exts = [e for e in exts
            if any(("." + e) in s for s in all_ext_sets)
            or e in ("pdf", "txt", "md", "doc", "docx", "xls", "xlsx",
                     "ppt", "pptx", "csv", "mp3", "mp4", "mkv", "zip",
                     "rar", "7z", "exe", "psd", "ai")]

    date_pats = [
        r"\b(\d{4}-\d{1,2}-\d{1,2})\b",
        r"\b(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\b",
        r"\b(\d{1,2}-\d{1,2}-\d{2,4})\b",
    ]
    dates = sorted({d for p in date_pats for d in re.findall(p, text)})

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text)
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "have", "your",
        "you", "are", "was", "were", "but", "not", "all", "can", "will", "been",
        "into", "they", "them", "their", "there", "here", "when", "where", "which",
        "what", "who", "how", "why", "date", "time", "name", "file", "document",
        "page", "image", "photo", "picture",
    }
    counts = {}
    for t in tokens:
        if t.lower() in stop:
            continue
        counts[t.lower()] = counts.get(t.lower(), 0) + 1
    names = sorted(counts, key=lambda k: (counts[k], len(k)), reverse=True)[:8]

    return {
        "names": names,
        "extensions": ["." + e for e in exts],
        "dates": dates,
        "queries": list(names[:5]),
    }


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
    """Drive capacity / used / free. Shown in the idle state."""
    return jsonify({"disks": disk_info()})


@app.route("/api/folder_sizes")
def api_folder_sizes():
    """Real recursive size for a set of folders (pipe-separated paths).
    Used by the folder picker to show sizes next to each folder name."""
    raw = request.args.get("paths", "")
    paths = [p for p in raw.split("|") if p and os.path.isdir(p)]
    out = {}
    for p in paths:
        out[p] = folder_size(p)
    return jsonify(out)


# --------------------------------------------------------------------------- #
#  Entrypoint
# --------------------------------------------------------------------------- #
def main():
    init_ocr()
    log("FileReach starting")
    print("\n" + "=" * 60)
    print("  FileReach is running.")
    print("  Open this URL in your browser:")
    print(f"    ->  http://127.0.0.1:{PORT}")
    print("  Keep this window open while you search.")
    print("  Press Ctrl+C to stop.")
    print("=" * 60 + "\n")
    try:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    except Exception:
        pass
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
