"""Origin-folder scanning, metadata guessing, hashing, and safe file moves."""

import hashlib
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif",
              ".jfif", ".jfi", ".jpe", ".pjpeg"}
# Extensions that are JPEG under another name; Obsidian can't display them.
JPEG_ALIASES = {".jfif", ".jfi", ".jpe", ".pjpeg"}
# What each sniffed format accepts as a correct extension.
KIND_EXTS = {"jpg": {".jpg", ".jpeg"}, "png": {".png"}, "gif": {".gif"}, "webp": {".webp"}}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
DOC_EXTS = {".pdf", ".txt"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | DOC_EXTS

SKIP_DIRS = {"_duplicates", ".xolitical", ".obsidian", ".git", "__pycache__"}
SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}

# Twitter-downloader style names: account-tweetID-hash-YYYYMMDD_HHMMSS(.ext)
TWITTER_FULL = re.compile(
    r"^(?P<account>[A-Za-z0-9_]{1,32})-(?P<tweet_id>\d{8,25})-"
    r"(?P<hash>[A-Za-z0-9_-]{3,})-(?P<ymd>\d{8})_(?P<hms>\d{6})"
)
# Looser fallback: account-tweetID-anything
TWITTER_SHORT = re.compile(r"^(?P<account>[A-Za-z0-9_]{1,32})-(?P<tweet_id>\d{8,25})\b")
# Any embedded timestamp, used only as a sort tie-breaker
ANY_STAMP = re.compile(r"(?<!\d)(\d{8})[_-]?(\d{6})(?!\d)")


def media_type_for(ext):
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".pdf":
        return "pdf"
    if ext == ".txt":
        return "text"
    return "other"


def sniff_image(path):
    """The real image format from the file's first bytes, or None if unrecognized."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def decodes(path):
    """True if Pillow can decode the image (also True when Pillow isn't installed)."""
    try:
        from PIL import Image
    except ImportError:
        return True
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def normalized_name(name, path):
    """Filename with an extension Obsidian can display, decided by the file's content.

    `.jfif` and other JPEG aliases become `.jpg`; an image whose bytes contradict its
    extension (e.g. a PNG saved as .jpg) gets the right one. Only the name changes,
    never the bytes. Unrecognized or undecodable content keeps its original name.
    """
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in IMAGE_EXTS:
        return name
    kind = sniff_image(path)
    if not kind or ext in KIND_EXTS[kind]:
        return name
    if ext not in JPEG_ALIASES and ext not in {e for exts in KIND_EXTS.values() for e in exts}:
        return name  # e.g. .bmp/.avif: we don't second-guess formats we can't sniff
    if not decodes(path):
        return name
    return f"{stem}.{kind}"


def normalize_source(url):
    """Canonical form used as a dedup key."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower()
    host = parts.netloc.lower().removeprefix("www.").removeprefix("mobile.")
    if host in ("twitter.com", "fxtwitter.com", "vxtwitter.com", "nitter.net"):
        host = "x.com"
    path = parts.path.rstrip("/")
    if host == "x.com":
        return f"https://{host}{path}"  # drop ?s=20 style tracking params
    if host in ("youtu.be",):
        return f"https://www.youtube.com/watch?v={path.lstrip('/')}"
    if host in ("youtube.com", "m.youtube.com"):
        host = "www.youtube.com"
    query = f"?{parts.query}" if parts.query else ""
    return f"https://{host}{path}{query}"


def guess_meta(path: Path, root: Path):
    """Prefill metadata from the filename pattern and parent folder."""
    ext = path.suffix.lower()
    meta = {
        "type": media_type_for(ext),
        "platform": "",
        "account": "",
        "source": "",
        "posted": "",
    }
    m = TWITTER_FULL.match(path.stem) or TWITTER_SHORT.match(path.stem)
    if m:
        g = m.groupdict()
        meta["platform"] = "twitter"
        meta["account"] = g["account"]
        meta["source"] = f"https://x.com/{g['account']}/status/{g['tweet_id']}"
        if g.get("ymd"):
            y, mo, d = g["ymd"][:4], g["ymd"][4:6], g["ymd"][6:8]
            meta["posted"] = f"{y}-{mo}-{d}"
    else:
        parent = path.parent
        if parent != root:
            meta["account"] = parent.name
        if ext == ".pdf":
            meta["platform"] = "substack"
        elif meta["account"]:
            meta["platform"] = "twitter"
    return meta


def birthtime(st):
    """File creation time (Windows / macOS); falls back to mtime elsewhere."""
    bt = getattr(st, "st_birthtime", None)
    if bt:
        return bt
    if os.name == "nt":
        return st.st_ctime
    return st.st_mtime


def _stamp_key(name):
    m = ANY_STAMP.search(name)
    return (m.group(1) + m.group(2)) if m else ""


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def scan_origin(root: Path, exclude=()):
    """All media files under root (recursive), newest creation time first.

    Folders in `exclude` (e.g. a vault that happens to sit inside the origin) are skipped.
    """
    excluded = {os.path.normcase(str(Path(p).resolve())) for p in exclude}
    items = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            name = e.name
            if name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if name not in SKIP_DIRS and os.path.normcase(e.path) not in excluded:
                        stack.append(Path(e.path))
                    continue
                if not e.is_file():
                    continue
            except OSError:
                continue
            if name.lower() in SKIP_FILES:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in MEDIA_EXTS:
                continue
            p = Path(e.path)
            try:
                st = p.stat()
            except OSError:
                continue
            created = birthtime(st)
            items.append({
                "path": p.relative_to(root).as_posix(),
                "name": name,
                "size": st.st_size,
                "created": created,
                "created_iso": iso(created),
                **guess_meta(p, root),
                "_key": (created, _stamp_key(name), p.relative_to(root).as_posix()),
            })
    items.sort(key=lambda it: it.pop("_key"), reverse=True)
    return items


def safe_join(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise PermissionError("path escapes its root folder")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Hasher:
    """Hash cache keyed by (path, size, mtime) with background prefetch."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._pending = set()

    @staticmethod
    def _key(path: Path):
        st = path.stat()
        return (str(path), st.st_size, st.st_mtime_ns)

    def get(self, path: Path):
        key = self._key(path)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        digest = sha256_file(path)
        with self._lock:
            self._cache[key] = digest
        return digest

    def prefetch(self, paths):
        for p in paths:
            with self._lock:
                if str(p) in self._pending:
                    continue
                self._pending.add(str(p))

            def work(p=p):
                try:
                    self.get(p)
                except OSError:
                    pass
                finally:
                    with self._lock:
                        self._pending.discard(str(p))

            threading.Thread(target=work, daemon=True).start()


def unique_path(path: Path, extra=""):
    """Return path, or a de-collided variant if it already exists."""
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    if extra:
        cand = path.with_name(f"{stem}-{extra}{ext}")
        if not cand.exists():
            return cand
        stem = f"{stem}-{extra}"
    n = 2
    while True:
        cand = path.with_name(f"{stem}-{n}{ext}")
        if not cand.exists():
            return cand
        n += 1


def move_file(src: Path, dest: Path):
    """Move without ever leaving zero or two copies behind.

    Same drive: an atomic rename. Across drives: copy, verify size, then delete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Windows refuses to rename a file another handle has open (a video still
    # streaming to the preview, a background hash). Such locks are brief: retry.
    for _ in range(20):
        try:
            os.rename(src, dest)
            return
        except PermissionError:
            time.sleep(0.15)
        except OSError:
            break  # different volume — fall back to copy
    tmp = dest.with_name(dest.name + ".partial")
    shutil.copy2(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink(missing_ok=True)
        raise OSError(f"copy of {src.name} is incomplete; original left in place")
    os.replace(tmp, dest)
    try:
        os.remove(src)
    except OSError:
        dest.unlink(missing_ok=True)  # couldn't remove the original: undo the copy
        raise
