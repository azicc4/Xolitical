#!/usr/bin/env python3
"""Xolitical triage server.

Local web app for sorting the /unsorted media backlog into the Obsidian vault.
Python standard library only — no pip installs.

Usage:
    python tools/triage/server.py [--no-open]

Then open http://localhost:8484 (opens automatically unless --no-open).
"""

import json
import hashlib
import mimetypes
import os
import re
import shutil
import sys
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlsplit, parse_qs, unquote

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = Path(__file__).resolve().parent / "ui"
VAULT = ROOT / "vault"
ASSETS = VAULT / "_assets"
MEDIA_NOTES = VAULT / "media"
DATA_DIR = ROOT / "data"
INDEX_FILE = DATA_DIR / "index.json"
TAGS_FILE = DATA_DIR / "tags.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".jfif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}

# Twitter-downloader style names: account-tweetID-hash-YYYYMMDD_HHMMSS(.ext)
TWITTER_FULL = re.compile(
    r"^(?P<account>[A-Za-z0-9_]{1,32})-(?P<tweet_id>\d{8,25})-"
    r"(?P<hash>[A-Za-z0-9_-]{3,})-(?P<ymd>\d{8})_(?P<hms>\d{6})"
)
# Looser fallback: account-tweetID-anything
TWITTER_SHORT = re.compile(r"^(?P<account>[A-Za-z0-9_]{1,32})-(?P<tweet_id>\d{8,25})\b")


def load_config():
    cfg = {"alias": "anonymous", "unsorted_dir": "unsorted", "port": 8484}
    for name in ("config.json", "config.example.json"):
        p = ROOT / name
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
                break
            except (json.JSONDecodeError, OSError) as e:
                print(f"warning: could not read {name}: {e}")
    return cfg


CONFIG = load_config()
UNSORTED = (ROOT / CONFIG["unsorted_dir"]).resolve()
DUPES_DIR = UNSORTED / "_duplicates"


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def media_type_for(ext):
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".pdf":
        return "pdf"
    return "other"


def guess_meta(path: Path):
    """Prefill metadata from filename pattern and parent folder."""
    ext = path.suffix.lower()
    meta = {
        "type": media_type_for(ext),
        "platform": "",
        "account": "",
        "source": "",
        "date_posted": "",
    }
    m = TWITTER_FULL.match(path.stem) or TWITTER_SHORT.match(path.stem)
    if m:
        g = m.groupdict()
        meta["platform"] = "twitter"
        meta["account"] = g["account"]
        meta["source"] = f"https://x.com/{g['account']}/status/{g['tweet_id']}"
        meta["tweet_id"] = g["tweet_id"]
        if g.get("ymd"):
            y, mo, d = g["ymd"][:4], g["ymd"][4:6], g["ymd"][6:8]
            meta["date_posted"] = f"{y}-{mo}-{d}"
    else:
        parent = path.parent
        if parent != UNSORTED and parent.name != "_duplicates":
            meta["account"] = parent.name
        if ext == ".pdf":
            meta["platform"] = "substack"
        elif meta["account"]:
            meta["platform"] = "twitter"
    return meta


def scan_queue():
    items = []
    if not UNSORTED.exists():
        return items
    for p in sorted(UNSORTED.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(UNSORTED)
        if rel.parts and rel.parts[0] == "_duplicates":
            continue
        if p.name.startswith(".") or p.name.lower() in ("thumbs.db", "desktop.ini"):
            continue
        items.append({
            "path": rel.as_posix(),
            "name": p.name,
            "size": p.stat().st_size,
            **guess_meta(p),
        })
    return items


def safe_unsorted_path(rel):
    p = (UNSORTED / unquote(rel)).resolve()
    if UNSORTED not in p.parents and p != UNSORTED:
        raise PermissionError("path escapes unsorted directory")
    return p


SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(s, fallback="misc"):
    s = SLUG_RE.sub("-", (s or "").strip()).strip("-.")
    return s or fallback


def yaml_str(s):
    """JSON string literals are valid YAML scalars — free correct escaping."""
    return json.dumps(s or "", ensure_ascii=False)


def build_note(meta, file_rel, sha, alias):
    lines = ["---"]
    lines.append(f"title: {yaml_str(meta.get('title'))}")
    lines.append(f"source: {yaml_str(meta.get('source'))}")
    lines.append(f"platform: {slug(meta.get('platform'), 'other')}")
    lines.append(f"type: {slug(meta.get('type'), 'other')}")
    lines.append(f"account: {yaml_str(meta.get('account'))}")
    lines.append(f"file: {yaml_str(file_rel)}")
    lines.append(f"sha256: {yaml_str(sha)}")
    lines.append(f"date_posted: {meta.get('date_posted') or ''}")
    lines.append(f"date_added: {date.today().isoformat()}")
    lines.append(f"added_by: {yaml_str(alias)}")
    tags = [slug(t, "") for t in meta.get("tags", []) if slug(t, "")]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {t}" for t in tags)
    else:
        lines.append("tags: []")
    lines.append("papers: []")
    lines.append("---")
    lines.append("")
    desc = (meta.get("description") or "").strip()
    if desc:
        lines.append(desc)
        lines.append("")
    lines.append(f"![[{file_rel}]]")
    lines.append("")
    return "\n".join(lines)


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


def do_save(body):
    src = safe_unsorted_path(body["path"])
    if not src.is_file():
        return {"status": "error", "error": "file no longer exists"}, 404

    sha = sha256_file(src)
    source_norm = normalize_source(body.get("source", ""))
    index = read_json(INDEX_FILE, {"by_source": {}, "by_hash": {}})

    if not body.get("force"):
        match = None
        if source_norm and source_norm in index["by_source"]:
            match = {"key": "source", "note": index["by_source"][source_norm]}
        elif sha in index["by_hash"]:
            match = {"key": "hash", "note": index["by_hash"][sha]}
        if match:
            return {"status": "duplicate", "match": match}, 200

    platform = slug(body.get("platform"), "other")
    account = slug(body.get("account"), "misc")
    dest_dir = ASSETS / platform / account
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest_dir / src.name, extra=sha[:8])
    file_rel = dest.relative_to(VAULT).as_posix()

    note_id = body.get("tweet_id") or ""
    m = re.search(r"/status/(\d+)", body.get("source", ""))
    if m:
        note_id = m.group(1)
    note_stem = slug(f"{platform}-{account}-{note_id or sha[:10]}")
    MEDIA_NOTES.mkdir(parents=True, exist_ok=True)
    note_path = unique_path(MEDIA_NOTES / f"{note_stem}.md")

    note_path.write_text(
        build_note(body, file_rel, sha, CONFIG.get("alias", "anonymous")),
        encoding="utf-8",
    )
    shutil.move(str(src), str(dest))

    note_rel = note_path.relative_to(VAULT).as_posix()
    if source_norm:
        index["by_source"][source_norm] = note_rel
    index["by_hash"][sha] = note_rel
    write_json(INDEX_FILE, index)

    new_tags = [slug(t, "") for t in body.get("tags", []) if slug(t, "")]
    if new_tags:
        tags_doc = read_json(TAGS_FILE, {"tags": []})
        known = set(tags_doc.get("tags", []))
        added = [t for t in new_tags if t not in known]
        if added:
            tags_doc.setdefault("tags", []).extend(added)
            write_json(TAGS_FILE, tags_doc)

    return {"status": "saved", "note": note_rel, "file": file_rel}, 200


def do_mark_duplicate(body):
    src = safe_unsorted_path(body["path"])
    if not src.is_file():
        return {"status": "error", "error": "file no longer exists"}, 404
    DUPES_DIR.mkdir(parents=True, exist_ok=True)
    dest = unique_path(DUPES_DIR / src.name)
    shutil.move(str(src), str(dest))
    return {"status": "moved", "to": dest.relative_to(UNSORTED).as_posix()}, 200


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route == "/api/queue":
                self.send_json({"items": scan_queue()})
            elif route == "/api/config":
                tags_doc = read_json(TAGS_FILE, {"tags": []})
                self.send_json({
                    "alias": CONFIG.get("alias", "anonymous"),
                    "tags": tags_doc.get("tags", []),
                    "unsorted_dir": str(UNSORTED),
                })
            elif route == "/file":
                rel = parse_qs(parsed.query).get("p", [""])[0]
                self.serve_media(safe_unsorted_path(rel))
            else:
                self.serve_ui(route)
        except PermissionError:
            self.send_json({"error": "forbidden"}, 403)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # keep the server alive; report to client
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self.read_body()
            if parsed.path == "/api/save":
                obj, status = do_save(body)
            elif parsed.path == "/api/duplicate":
                obj, status = do_mark_duplicate(body)
            else:
                obj, status = {"error": "not found"}, 404
            self.send_json(obj, status)
        except PermissionError:
            self.send_json({"error": "forbidden"}, 403)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def serve_ui(self, route):
        rel = "index.html" if route in ("/", "") else route.lstrip("/")
        p = (UI_DIR / rel).resolve()
        if UI_DIR not in p.parents or not p.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, path: Path):
        if not path.is_file():
            self.send_json({"error": "not found"}, 404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    port = int(CONFIG.get("port", 8484))
    UNSORTED.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Xolitical triage: {url}")
    print(f"  unsorted: {UNSORTED}")
    print(f"  vault:    {VAULT}")
    print(f"  alias:    {CONFIG.get('alias')}  (set in config.json)")
    if "--no-open" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
