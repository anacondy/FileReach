"""
FileReach engine — read-only filesystem indexer, search, and live search.

Design rules:
  * NEVER delete any file.    -> no os.remove / shutil.rmtree anywhere.
  * NEVER duplicate any file. -> file bytes are never copied; only metadata/content read.
  * Read-only access.         -> files opened with mode 'rb' / 'r' only.
  * As fast as possible.      -> os.scandir walk + SQLite (WAL) index, indexed columns,
                                 in-memory fuzzy scoring, background incremental re-index,
                                 plus a live (no-index) search fallback.

Core search has zero external dependencies. `rapidfuzz` is optional (faster fuzzy
matching); `difflib` is the stdlib fallback.
"""

import os
import re
import math
import time
import sqlite3
import threading
import platform
import shutil
import subprocess
from datetime import datetime

VERSION = "1.3.0"

# ----------------------------------------------------------------------------- #
#  File-type categories
#
#  Overlap note: "spreadsheets"/"presentations" intentionally overlap "documents".
#  _kind_for() classifies by the FIRST match (documents), so the `kind` column is
#  one of: images, videos, audio, documents, code, archives, fonts, other, folder.
# ----------------------------------------------------------------------------- #
TYPE_CATEGORIES = {
    "images": {
        ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".webp",
        ".tiff", ".tif", ".svg", ".heic", ".heif", ".raw", ".cr2", ".cr3",
        ".nef", ".arw", ".dng", ".orf", ".rw2", ".ico", ".psd", ".ai", ".jp2",
    },
    "videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
        ".mpg", ".mpeg", ".mpe", ".3gp", ".3g2", ".ts", ".vob", ".ogv", ".rm", ".rmvb",
    },
    "audio": {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".oga", ".m4a", ".wma",
        ".opus", ".aiff", ".aif", ".alac", ".amr", ".mid", ".midi",
    },
    "documents": {
        ".pdf", ".doc", ".docx", ".txt", ".md", ".markdown", ".rtf", ".odt",
        ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".epub", ".pages", ".numbers",
        ".key", ".odp", ".ods", ".tex", ".mobi", ".azw", ".azw3",
    },
    "code": {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss",
        ".sass", ".less", ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs",
        ".rb", ".go", ".rs", ".php", ".swift", ".kt", ".kts", ".scala", ".clj",
        ".sql", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".conf", ".sh", ".bash", ".zsh", ".bat", ".ps1", ".vue", ".svelte",
        ".dart", ".lua", ".r", ".pl", ".vim", ".gradle", ".makefile", ".asm",
    },
    "archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".tgz",
        ".tbz2", ".lz", ".cab", ".dmg",
    },
    "fonts": {".ttf", ".otf", ".woff", ".woff2", ".eot"},
    "spreadsheets": {".xls", ".xlsx", ".ods", ".csv", ".numbers"},
    "presentations": {".ppt", ".pptx", ".odp", ".key"},
}

TEXT_VIEWABLE = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss",
    ".sass", ".less", ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs",
    ".rb", ".go", ".rs", ".php", ".swift", ".kt", ".sql", ".sh", ".bash",
    ".zsh", ".bat", ".ps1", ".vue", ".svelte", ".dart", ".lua", ".r", ".pl",
    ".svg", ".gitignore", ".env", ".dockerfile", ".makefile",
}

IMAGE_VIEWABLE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico"}
RENDERABLE_HTML = {".html", ".htm"}

# ----------------------------------------------------------------------------- #
#  Optional fuzzy matching
# ----------------------------------------------------------------------------- #
try:
    from rapidfuzz import fuzz as _rf_fuzz
    _HAS_RAPIDFUZZ = True
    def _score(a, b):
        return _rf_fuzz.token_sort_ratio(a, b)
except Exception:
    import difflib
    _HAS_RAPIDFUZZ = False
    def _score(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


# ----------------------------------------------------------------------------- #
#  Helpers
# ----------------------------------------------------------------------------- #
def human_size(n):
    if n is None:
        return "0 B"
    n = float(n)
    if n < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]
    i = int(math.floor(math.log(max(n, 1), 1024)))
    i = min(i, len(units) - 1)
    s = n / (1024 ** i)
    if s >= 100:
        return f"{s:.0f} {units[i]}"
    if s >= 10:
        return f"{s:.1f} {units[i]}"
    return f"{s:.2f} {units[i]}"


def human_date(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "—"


def human_date_short(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return "—"


def is_windows():
    return platform.system() == "Windows"


def long_path(path):
    """Enable access to very long paths (>260 chars) on Windows."""
    if is_windows():
        abs_path = os.path.abspath(path)
        if abs_path.startswith("\\\\?\\"):
            return abs_path
        return "\\\\?\\" + abs_path
    return path


def looks_like_path(s):
    """True if `s` looks like a host path the user pasted (Win/Unix/UNC/home)."""
    if not s:
        return False
    s = s.strip().strip('"').strip("'")
    if re.match(r"^[A-Za-z]:[\\/]", s):      # C:\... or C:/...
        return True
    if s.startswith("\\\\"):                  # UNC \\server\share
        return True
    if s.startswith("/") or s.startswith("~/") or s == "~":
        return True
    return False


# ----------------------------------------------------------------------------- #
#  Indexer
# ----------------------------------------------------------------------------- #
class Indexer:
    """Walks the filesystem and stores metadata in SQLite. Background-capable."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.RLock()
        self.conn = None
        self.status = {
            "state": "idle", "root": None, "indexed": 0, "dirs": 0, "errors": 0,
            "started_at": 0, "finished_at": 0, "current": "", "message": "",
        }
        self._stop = threading.Event()

    def connect(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA busy_timeout=6000")
        self._init_schema()
        return self.conn

    def _init_schema(self):
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE, name TEXT, name_lower TEXT, ext TEXT,
                size INTEGER, created REAL, modified REAL, is_dir INTEGER,
                root TEXT, kind TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_name_lower ON files(name_lower);
            CREATE INDEX IF NOT EXISTS idx_ext ON files(ext);
            CREATE INDEX IF NOT EXISTS idx_kind ON files(kind);
            CREATE INDEX IF NOT EXISTS idx_isdir ON files(is_dir);
            CREATE INDEX IF NOT EXISTS idx_modified ON files(modified);
            CREATE INDEX IF NOT EXISTS idx_created ON files(created);
            """
        )
        c.commit()

    def _kind_for(self, ext):
        for kind, exts in TYPE_CATEGORIES.items():
            if ext in exts:
                return kind
        return "other"

    def is_busy(self):
        with self.lock:
            return self.status["state"] == "indexing"

    def is_indexed(self, path):
        """Is `path` (or a parent of it) covered by an existing index root?"""
        if not path:
            return False
        try:
            norm = os.path.abspath(path).replace("\\", "/").rstrip("/").lower()
            rows = self.conn.execute("SELECT DISTINCT root FROM files").fetchall()
            for r in rows:
                root = (r["root"] or "").replace("\\", "/").rstrip("/").lower()
                if not root:
                    continue
                if norm == root or norm.startswith(root + "/"):
                    return True
            return False
        except Exception:
            return False

    def get_status(self):
        with self.lock:
            d = dict(self.status)
        if d["state"] in ("done", "cancelled", "error") and d["finished_at"]:
            d["elapsed"] = round(d["finished_at"] - d["started_at"], 1)
        elif d["state"] == "indexing":
            d["elapsed"] = round(time.time() - d["started_at"], 1)
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM files WHERE is_dir=0"
            ).fetchone()
            d["total_files"] = row["n"]
            d["total_size"] = row["s"]
            d["total_size_h"] = human_size(row["s"])
        except Exception:
            d["total_files"] = 0
            d["total_size"] = 0
            d["total_size_h"] = "0 B"
        return d

    def _walk(self, root):
        norm_root = os.path.abspath(root)
        stack = [norm_root]
        while stack and not self._stop.is_set():
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    children = list(it)
            except (OSError, PermissionError):
                with self.lock:
                    self.status["errors"] += 1
                continue
            for entry in children:
                if self._stop.is_set():
                    break
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir:
                        st = entry.stat(follow_symlinks=False)
                        yield (entry.path, entry.name, entry.name.lower(), "",
                               0, st.st_ctime, st.st_mtime, 1, norm_root, "folder")
                        stack.append(entry.path)
                        with self.lock:
                            self.status["dirs"] += 1
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        ext = os.path.splitext(entry.name)[1].lower()
                        kind = self._kind_for(ext)
                        yield (entry.path, entry.name, entry.name.lower(), ext,
                               st.st_size, st.st_ctime, st.st_mtime, 0, norm_root, kind)
                        with self.lock:
                            self.status["indexed"] += 1
                            if self.status["indexed"] % 250 == 0:
                                self.status["current"] = entry.path
                except (OSError, PermissionError):
                    with self.lock:
                        self.status["errors"] += 1
                    continue

    def index(self, root, incremental=True):
        root = os.path.abspath(root)
        if not os.path.exists(root):
            self.status["message"] = f"Path not found: {root}"
            return False
        self._stop.clear()
        with self.lock:
            self.status.update({
                "state": "indexing", "root": root, "indexed": 0, "dirs": 0, "errors": 0,
                "started_at": time.time(), "finished_at": 0, "current": root,
                "message": "Indexing…",
            })

        def _run():
            try:
                BATCH = 5000
                buf = []
                with self.conn:
                    if incremental:
                        self.conn.execute("DELETE FROM files WHERE root = ?", (root,))
                    sql = ("INSERT OR REPLACE INTO files "
                           "(path,name,name_lower,ext,size,created,modified,is_dir,root,kind) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)")
                    cur = self.conn.cursor()
                    for row in self._walk(root):
                        buf.append(row)
                        if len(buf) >= BATCH:
                            cur.executemany(sql, buf)
                            self.conn.commit()
                            buf.clear()
                    if buf:
                        cur.executemany(sql, buf)
                        self.conn.commit()
                with self.lock:
                    if self._stop.is_set():
                        self.status["state"] = "cancelled"
                        self.status["message"] = "Indexing cancelled."
                    else:
                        self.status["state"] = "done"
                        self.status["message"] = "Index complete."
                    self.status["finished_at"] = time.time()
                    self.status["current"] = ""
            except Exception as e:
                with self.lock:
                    self.status["state"] = "error"
                    self.status["message"] = f"Error: {e}"
                    self.status["finished_at"] = time.time()

        threading.Thread(target=_run, daemon=True, name="fr-index").start()
        return True

    def cancel(self):
        self._stop.set()


# ----------------------------------------------------------------------------- #
#  Relevance ranking (shared by index search + live search)
# ----------------------------------------------------------------------------- #
def rank_results(results, terms):
    """Score each result by best fuzzy match against query terms; sort desc.
    Tokenises terms so '3 GAL' ranks '3-GAL' highly."""
    all_toks = []
    for g in terms:
        all_toks.extend([t for t in re.split(r"[\s\-_]+", g.lower()) if t])
    joined = " ".join(all_toks)
    for r in results:
        name_l = r["name"].lower()
        base = _score(joined, name_l)
        best_term = 0.0
        for t in all_toks:
            if t in name_l:
                best_term = max(best_term, 95.0 if name_l.startswith(t) else 80.0)
        if all_toks and all(t in name_l for t in all_toks):
            best_term = max(best_term, 92.0)  # all tokens present -> strong match
        r["_score"] = round(max(base, best_term), 1)
    results.sort(key=lambda r: (-r["_score"], r["name"].lower()))
    return results


# ----------------------------------------------------------------------------- #
#  Search engine (over the index)
# ----------------------------------------------------------------------------- #
class SearchEngine:
    def __init__(self, conn):
        self.conn = conn

    def _build(self, query=None, ext=None, ftype=None, folder=None, folders_only=False):
        where = ["is_dir = 1"] if folders_only else ["is_dir = 0"]
        params = []
        if not folders_only and ext:
            e = ext.strip().lower()
            if not e.startswith("."):
                e = "." + e
            where.append("ext = ?")
            params.append(e)
        if not folders_only and ftype and ftype in TYPE_CATEGORIES:
            exts = sorted(TYPE_CATEGORIES[ftype])
            ph = ",".join("?" * len(exts))
            where.append(f"ext IN ({ph})")
            params.extend(exts)
        if folder:
            f = os.path.abspath(folder).replace("\\", "/")
            where.append("(replace(path,'\\','/') = ? OR replace(path,'\\','/') LIKE ?)")
            params.extend([f, f.rstrip("/") + "/%"])
        # name query: groups (newline/comma/semicolon) are OR'd;
        # within a group, tokens (space/dash/underscore) are AND'd.
        # So "3 GAL" matches "3-GAL" and "3_GAL", and "report, notes"
        # matches either name.
        name_terms = []
        if query:
            groups = re.split(r"[\n,;]+", query)
            name_terms = [g.strip() for g in groups if g.strip()]
        if name_terms:
            ors = []
            for g in name_terms:
                toks = [t for t in re.split(r"[\s\-_]+", g.lower()) if t]
                if not toks:
                    continue
                ands = " AND ".join("(name_lower LIKE ? OR path LIKE ?)" for _ in toks)
                for t in toks:
                    params.extend([f"%{t}%", f"%{t}%"])
                ors.append(f"({ands})")
            if ors:
                where.append("(" + " OR ".join(ors) + ")")
        return " AND ".join(where), params, name_terms

    def search(self, query=None, ext=None, ftype=None, folder=None,
               sort="relevance", limit=1000, include_folders=False):
        where, params, name_terms = self._build(
            query, ext, ftype, folder, folders_only=bool(include_folders)
        )
        order = self._order_for(sort, has_query=bool(name_terms))
        sql = (f"SELECT path,name,ext,size,created,modified,kind FROM files "
               f"WHERE {where} ORDER BY {order} LIMIT ?")
        rows = self.conn.execute(sql, list(params) + [int(limit)]).fetchall()
        results = [dict(r) for r in rows]
        if name_terms and query:
            results = rank_results(results, name_terms)
        return {
            "results": results, "count": len(results), "query": query, "ext": ext,
            "ftype": ftype, "folder": folder, "sort": sort, "fuzzy_used": _HAS_RAPIDFUZZ,
            "source": "index",
        }

    def _order_for(self, sort, has_query):
        m = {
            "name-asc": "name COLLATE NOCASE ASC", "name-desc": "name COLLATE NOCASE DESC",
            "size-desc": "size DESC", "size-asc": "size ASC",
            "modified-desc": "modified DESC", "modified-asc": "modified ASC",
            "created-desc": "created DESC", "created-asc": "created ASC",
            "relevance": "name COLLATE NOCASE ASC",
        }
        return m.get(sort, m["name-asc"])

    def stats(self, ext=None, ftype=None, folder=None):
        where, params, _ = self._build(ext=ext, ftype=ftype, folder=folder)
        row = self.conn.execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(size),0) s, "
            f"MIN(created) mn, MAX(created) mx FROM files WHERE {where}",
            params,
        ).fetchone()
        n, s, mn, mx = row["n"], row["s"], row["mn"], row["mx"]
        return {
            "count": n, "total_size": s, "total_size_h": human_size(s),
            "first_created": mn, "first_created_h": human_date(mn),
            "last_created": mx, "last_created_h": human_date(mx),
        }

    def overview(self):
        rows = self.conn.execute(
            "SELECT ext, COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
            "WHERE is_dir=0 GROUP BY ext ORDER BY n DESC LIMIT 40"
        ).fetchall()
        kind_rows = self.conn.execute(
            "SELECT kind, COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
            "WHERE is_dir=0 GROUP BY kind ORDER BY n DESC"
        ).fetchall()
        return {
            "extensions": [
                {"ext": r["ext"] or "(none)", "count": r["n"], "size_h": human_size(r["s"])}
                for r in rows
            ],
            "kinds": [
                {"kind": r["kind"], "count": r["n"], "size_h": human_size(r["s"])}
                for r in kind_rows
            ],
        }

    def indexed_roots(self):
        rows = self.conn.execute(
            "SELECT root, COUNT(*) n FROM files GROUP BY root ORDER BY root"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_all(self):
        return self.conn.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]


# ----------------------------------------------------------------------------- #
#  Live search — no index needed. Walks a path on demand (read-only),
#  time-boxed + capped. Used when a folder isn't indexed or a raw path is pasted.
# ----------------------------------------------------------------------------- #
def live_search(path, query=None, ext=None, ftype=None, limit=1500, timeout=25.0):
    path = os.path.abspath(path)
    started = time.time()
    if not os.path.isdir(path):
        return {"results": [], "count": 0, "scanned": 0, "truncated": False,
                "timed_out": False, "path": path, "source": "live",
                "note": "not a directory"}

    terms = []
    if query:
        terms = [t.strip().lower() for t in re.split(r"[\n,;]+", query) if t.strip()]
    if ext:
        ext = ext.strip().lower()
        if not ext.startswith("."):
            ext = "." + ext
    allowed_exts = TYPE_CATEGORIES[ftype] if (ftype and ftype in TYPE_CATEGORIES) else None
    # tokenise groups: OR across groups, AND within (so "3 GAL" -> 3-GAL)
    groups = []
    for g in terms:
        groups.append([t for t in re.split(r"[\s\-_]+", g) if t])

    def _name_matches(name_l, path_l):
        if not groups:
            return True
        for toks in groups:
            if all(t in name_l or t in path_l for t in toks):
                return True
        return False

    results = []
    scanned = 0
    truncated = False
    timed_out = False
    stack = [path]

    while stack:
        if time.time() - started > timeout:
            timed_out = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                children = list(it)
        except (OSError, PermissionError):
            continue
        for entry in children:
            if len(results) >= limit:
                truncated = True
                break
            if scanned and scanned % 4000 == 0 and time.time() - started > timeout:
                timed_out = True
                break
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    scanned += 1
                    name = entry.name
                    name_l = name.lower()
                    e = os.path.splitext(name)[1].lower()
                    if ext and e != ext:
                        continue
                    if allowed_exts is not None and e not in allowed_exts:
                        continue
                    if not _name_matches(name_l, entry.path.lower()):
                        continue
                    st = entry.stat(follow_symlinks=False)
                    results.append({
                        "path": entry.path, "name": name, "ext": e, "size": st.st_size,
                        "created": st.st_ctime, "modified": st.st_mtime,
                        "kind": _kind_for_static(e),
                        "size_h": human_size(st.st_size),
                        "created_h": human_date_short(st.st_ctime),
                        "modified_h": human_date_short(st.st_mtime),
                        "is_image": e in IMAGE_VIEWABLE,
                        "is_viewable_text": e in TEXT_VIEWABLE,
                        "is_renderable": e in RENDERABLE_HTML,
                    })
            except (OSError, PermissionError):
                continue
        if truncated or timed_out:
            break

    if terms and results:
        results = rank_results(results, terms)

    return {
        "results": results, "count": len(results), "scanned": scanned,
        "truncated": truncated, "timed_out": timed_out, "path": path,
        "source": "live", "elapsed": round(time.time() - started, 2),
    }


def _kind_for_static(ext):
    for kind, exts in TYPE_CATEGORIES.items():
        if ext in exts:
            return kind
    return "other"


# --------------------------------------------------------------------------- #
#  Find folders by name across the whole index (global folder search)
# --------------------------------------------------------------------------- #
def find_folders(conn, query, limit=200):
    q = (query or "").strip().lower()
    if not q:
        return []
    toks = [t for t in re.split(r"[\s\-_]+", q) if t]
    where_parts = ["is_dir = 1"]
    params = []
    for t in toks:
        where_parts.append("name_lower LIKE ?")
        params.append(f"%{t}%")
    where = " AND ".join(where_parts)
    rows = conn.execute(
        f"SELECT path, name FROM files WHERE {where} "
        f"ORDER BY LENGTH(name) ASC LIMIT ?", params + [int(limit)]
    ).fetchall()
    return [{"path": r["path"], "name": r["name"]} for r in rows]


# --------------------------------------------------------------------------- #
#  Filesystem browse / reveal / read helpers (read-only)
# --------------------------------------------------------------------------- #
def list_drives():
    if is_windows():
        import string
        drives = []
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if os.path.exists(d):
                drives.append(d)
        return drives or ["C:\\"]
    return ["/"]


def list_dirs(path):
    if not path or path in ("/", ""):
        if is_windows():
            return [{"name": d, "path": d} for d in list_drives()], ""
        path = "/"
    try:
        out = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        out.append({"name": entry.name, "path": entry.path})
                except (OSError, PermissionError):
                    continue
        out.sort(key=lambda d: d["name"].lower())
        return out, os.path.abspath(path)
    except (OSError, PermissionError):
        return [], os.path.abspath(path)


def reveal_in_explorer(path):
    path = os.path.abspath(path)
    if is_windows():
        subprocess.Popen(["explorer", "/select,", path])
        return True
    if platform.system() == "Darwin":
        subprocess.Popen(["open", "-R", path])
        return True
    subprocess.Popen(["xdg-open", os.path.dirname(path) or path])
    return True


def read_file_text(path, limit=512_000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(limit)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
            except Exception:
                text = data.decode("utf-8", errors="replace")
        return text, size
    except (OSError, PermissionError):
        return None, 0


# --------------------------------------------------------------------------- #
#  Disk usage + folder sizes (read-only)
# --------------------------------------------------------------------------- #
_FOLDER_SIZE_CACHE = {}


def disk_info():
    out = []
    for d in list_drives():
        try:
            u = shutil.disk_usage(d)
            out.append({
                "drive": d, "total": u.total, "used": u.used, "free": u.free,
                "total_h": human_size(u.total), "used_h": human_size(u.used),
                "free_h": human_size(u.free),
                "percent": round(u.used / u.total * 100, 1) if u.total else 0,
            })
        except (OSError, PermissionError, ValueError):
            continue
    return out


def folder_size(path, ttl=120):
    key = os.path.abspath(path)
    now = time.time()
    c = _FOLDER_SIZE_CACHE.get(key)
    if c and now - c[1] < ttl:
        return {"size": c[0], "files": c[2], "size_h": human_size(c[0]), "cached": True}
    total, nfiles = 0, 0
    try:
        for root, _dirs, files in os.walk(key):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    nfiles += 1
                except OSError:
                    continue
    except OSError:
        pass
    _FOLDER_SIZE_CACHE[key] = (total, now, nfiles)
    return {"size": total, "files": nfiles, "size_h": human_size(total), "cached": False}
