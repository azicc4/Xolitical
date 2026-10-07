#!/usr/bin/env python3
"""Xolitical viewer — sort media from an origin (inbox) folder into an Obsidian vault.

Python standard library only (Pillow / ffmpeg are used for thumbnails if present).

Usage:
    python tools/viewer/server.py [--no-open] [--port N]
    python tools/viewer/server.py --rebuild [VAULT]   # regenerate every note from the index
    python tools/viewer/server.py --stop              # stop a running viewer
    python tools/viewer/server.py --fix-extensions [VAULT]  # .jfif etc. -> .jpg (rename only, hash-checked)

Starting the viewer while one is already running restarts it on the current code.

Then open http://localhost:8484 (opens automatically unless --no-open).
"""

import json
import mimetypes
import os
import re
import string
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session as sess  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = Path(__file__).resolve().parent / "ui"
DEFAULT_VAULT = ROOT / "vault"

mimetypes.add_type("video/mp4", ".m4v")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/jpeg", ".jfif")


def load_config():
    cfg = {"port": 8484}
    p = ROOT / "config.json"
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: could not read config.json: {e}")
    return cfg


CONFIG = load_config()
CURRENT = {"session": None}


def need_session():
    s = CURRENT["session"]
    if not s:
        raise LookupError("no active session")
    return s


def list_dirs(path):
    """Folder browser for the start screen."""
    if not path:
        roots = []
        if os.name == "nt":
            drives = getattr(os, "listdrives", None)
            roots = drives() if drives else [f"{d}:\\" for d in string.ascii_uppercase
                                              if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/"]
        home = Path.home()
        shortcuts = [str(home), str(home / "Downloads"), str(home / "Documents"), str(DEFAULT_VAULT)]
        return {"path": "", "parent": None,
                "dirs": [{"name": r, "path": r} for r in roots + [s for s in shortcuts if os.path.isdir(s)]]}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise FileNotFoundError(f"not a folder: {path}")
    p = p.resolve()
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            try:
                if e.is_dir() and not e.name.startswith((".", "$")):
                    dirs.append({"name": e.name, "path": str(Path(e.path))})
            except OSError:
                continue
    except PermissionError:
        pass
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": dirs}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ helpers
    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def guarded(self, fn):
        try:
            fn()
        except LookupError as e:
            self.send_json({"status": "error", "error": str(e)}, 409)
        except PermissionError:
            self.send_json({"status": "error", "error": "forbidden"}, 403)
        except FileNotFoundError as e:
            self.send_json({"status": "error", "error": str(e)}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # keep the server alive; report to the client
            import traceback
            traceback.print_exc()
            self.send_json({"status": "error", "error": f"{type(e).__name__}: {e}"}, 500)

    # --------------------------------------------------------------- GET
    def do_GET(self):
        if not self.from_this_app():
            self.send_json({"status": "error", "error": "forbidden"}, 403)
            return
        parsed = urlparse(self.path)
        route, q = parsed.path, parse_qs(parsed.query)
        arg = lambda k: q.get(k, [""])[0]

        def run():
            if route == "/api/state":
                st = sess.load_state()
                alias = st.get("alias") or CONFIG.get("alias", "")
                s = CURRENT["session"]
                self.send_json({"alias": alias, "recent": st.get("recent", []),
                                "default_vault": str(DEFAULT_VAULT),
                                "active": s.bootstrap() if s else None})
            elif route == "/api/stats":
                s = need_session()
                with s.lock:
                    self.send_json({"stats": s.stats(refresh=True), "categories": s.index.categories()})
            elif route == "/api/event":
                s = need_session()
                with s.lock:
                    self.send_json(s.event_detail(arg("id")))
            elif route == "/api/same_post":
                s = need_session()
                with s.lock:
                    self.send_json({"event": s.same_post(arg("source"))})
            elif route == "/file":
                s = need_session()
                self.serve_file(s.origin_file(arg("p")))
            elif route == "/thumb":
                s = need_session()
                with s.lock:
                    row = s.index.file(arg("sha"))
                if not row:
                    raise FileNotFoundError("unknown file")
                src = s.media_path(row)
                t = s.thumbs.ensure(row["sha256"], src, row["type"])
                if t:
                    self.serve_file(t, cache=True)
                elif row["type"] == "image" and src.exists():
                    self.serve_file(src, cache=True)
                else:
                    raise FileNotFoundError("no thumbnail")
            else:
                self.serve_ui(route)

        self.guarded(run)

    def from_this_app(self):
        """Reject requests a web page on another site could forge (all POSTs move
        files or rewrite notes): the Host must be this machine and POSTs must be
        JSON, which browsers won't send cross-site without a CORS preflight."""
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"localhost:{port}", f"127.0.0.1:{port}"):
            return False
        if self.command == "POST":
            return self.headers.get("Content-Type", "").startswith("application/json")
        return True

    # -------------------------------------------------------------- POST
    def do_POST(self):
        route = urlparse(self.path).path
        if not self.from_this_app():
            self.send_json({"status": "error", "error": "forbidden"}, 403)
            return

        def run():
            body = self.read_body()
            if route == "/api/shutdown":  # used by the launcher to restart on new code
                s = CURRENT["session"]
                if s:
                    s.end()
                CURRENT["session"] = None
                self.send_json({"status": "ok"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif route == "/api/browse":
                self.send_json(list_dirs(body.get("path", "")))
            elif route == "/api/inspect":
                self.send_json(sess.inspect(body.get("origin"), body.get("vault")))
            elif route == "/api/init":
                path = sess.initialize(body["vault"])
                self.send_json({"status": "ok", "vault": path})
            elif route == "/api/begin":
                info = sess.inspect(body.get("origin"), body.get("vault"))
                if not info["ok"]:
                    self.send_json({"status": "error", "error": info["origin"].get("error")
                                    or info["vault"].get("error") or "vault is not initialized",
                                    "inspect": info}, 400)
                    return
                old = CURRENT["session"]
                if old:
                    old.end()
                CURRENT["session"] = sess.Session(body["origin"], body["vault"], body.get("alias") or "anonymous")
                self.send_json({"status": "ok", **CURRENT["session"].bootstrap()})
            elif route == "/api/end":
                s = CURRENT["session"]
                if s:
                    s.end()
                CURRENT["session"] = None
                self.send_json({"status": "ok"})
            else:
                s = need_session()
                if route == "/api/save":
                    self.send_json(s.save(body))
                elif route == "/api/undo":
                    self.send_json(s.undo())
                elif route == "/api/duplicate":
                    self.send_json(s.mark_duplicate(body["path"]))
                elif route == "/api/category":
                    self.send_json(s.category_op(body))
                elif route == "/api/prefetch":
                    s.prefetch(body.get("paths", []))
                    self.send_json({"status": "ok"})
                else:
                    self.send_json({"status": "error", "error": "not found"}, 404)

        self.guarded(run)

    # ------------------------------------------------------------ static
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
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, path: Path, cache=False):
        if not path.is_file():
            raise FileNotFoundError("not found")
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
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
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


class Server(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second process bind a port that is already
    # in use; disable it there so a busy port fails loudly instead.
    allow_reuse_address = os.name != "nt"


def viewer_running(port):
    """True if a Xolitical viewer is already answering on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "default_vault" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def stop_running(port, timeout=4):
    """Ask a viewer running on this port to shut down cleanly (it saves its
    session first). Returns True once nothing is answering on the port."""
    if not viewer_running(port):
        return True
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass  # older viewers have no shutdown endpoint; the launcher stops those itself
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not viewer_running(port):
            return True
        time.sleep(0.25)
    return False


def main():
    args = sys.argv[1:]
    if "--fix-extensions" in args:
        i = args.index("--fix-extensions")
        vault = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else str(DEFAULT_VAULT)
        rep = sess.fix_extensions(vault)
        for line in rep["renamed"]:
            print("renamed  ", line)
        for line in rep["failed"]:
            print("FAILED   ", line)
        print(f"{len(rep['renamed'])} renamed, {len(rep['failed'])} failed, "
              f"{len(rep['missing'])} indexed files not on this machine")
        return
    if "--rebuild" in args:
        i = args.index("--rebuild")
        vault = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else str(DEFAULT_VAULT)
        out = sess.rebuild(vault)
        print(f"rebuilt {out['events']} event notes and {out['categories']} category notes in {vault}")
        if out["orphans"]:
            print("notes whose id is no longer in the index (left untouched):")
            for o in out["orphans"]:
                print("  ", o)
        return
    port = int(CONFIG.get("port", 8484))
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    if "--state" in args:  # alternate local-state file (used for testing)
        sess.STATE_FILE = Path(args[args.index("--state") + 1])
    url = f"http://localhost:{port}"
    open_browser = "--no-open" not in args
    if "--stop" in args:
        stopped = stop_running(port)
        print("Stopped the running viewer." if stopped else f"A viewer on port {port} did not stop.")
        return
    # Starting always replaces a viewer that's already running, so the latest
    # code is what serves the page. Progress is safe: every save is on disk.
    if viewer_running(port):
        print("Restarting: stopping the viewer that is already running...")
        if not stop_running(port):
            print(f"An older viewer on port {port} would not stop. Close its window and try again.")
            sys.exit(1)
    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"Port {port} is already used by another program ({e}).")
        print('Choose a different port in config.json, e.g. {"port": 8490}')
        sys.exit(1)
    print(f"Xolitical viewer: {url}")
    print("Close this window (or press Ctrl+C) to stop the viewer.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()  # returns after /api/shutdown
    except KeyboardInterrupt:
        s = CURRENT["session"]
        if s:
            s.end()
        print("\nbye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
