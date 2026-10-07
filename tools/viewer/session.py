"""A sorting session: one origin (inbox) folder feeding one vault."""

import json
import os
import re
import threading
import time
from pathlib import Path

import scan
import vaultgen
from index import Index, init_index_dir, is_initialized, new_id, now_iso
from thumbs import Thumbs

IDLE_GAP = 300  # seconds; longer pauses between saves don't count as active time
LINK_UNSAFE = re.compile(r"[#^\[\]|]+")

# Per-machine memory (alias, recent origin/vault pairs). Never committed.
STATE_FILE = Path(os.environ.get("XOLITICAL_STATE") or Path.home() / ".xolitical" / "state.json")


# ------------------------------------------------------------ local state
def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def pair_key(origin, vault):
    return f"{Path(origin).resolve()}|{Path(vault).resolve()}"


def remember_pair(origin, vault, alias, last_end=None):
    state = load_state()
    state["alias"] = alias
    key = pair_key(origin, vault)
    recent = [r for r in state.get("recent", []) if pair_key(r["origin"], r["vault"]) != key]
    prev = next((r for r in state.get("recent", []) if pair_key(r["origin"], r["vault"]) == key), {})
    entry = {"origin": str(Path(origin).resolve()), "vault": str(Path(vault).resolve()),
             "last_used": now_iso(), "last_end": last_end if last_end is not None else prev.get("last_end")}
    state["recent"] = [entry] + recent[:9]
    save_state(state)


def last_end_for(origin, vault):
    key = pair_key(origin, vault)
    for r in load_state().get("recent", []):
        if pair_key(r["origin"], r["vault"]) == key:
            return r.get("last_end")
    return None


# ------------------------------------------------------------ validation
def inspect(origin, vault):
    """Check an origin/vault pair before a session begins."""
    out = {"origin": {}, "vault": {}, "ok": False}
    o = Path(origin).expanduser() if origin else None
    v = Path(vault).expanduser() if vault else None
    if not o or not o.is_dir():
        out["origin"]["error"] = "Origin folder not found."
    if not v or not v.is_dir():
        out["vault"]["error"] = "Vault folder not found."
    if out["origin"].get("error") or out["vault"].get("error"):
        return out
    o, v = o.resolve(), v.resolve()
    media = v / vaultgen.MEDIA_DIR
    if o == v or o == media or media in o.parents or v in o.parents:
        out["origin"]["error"] = ("The origin is inside the vault. Pick the folder your downloads land in "
                                  "(e.g. in Downloads), not a folder inside the vault.")
        return out
    items = scan.scan_origin(o, exclude=[v])
    last_end = last_end_for(o, v)
    out["origin"] = {
        "path": str(o),
        "count": len(items),
        "new_since_last": sum(1 for it in items if last_end and it["created"] > last_end) if last_end else None,
    }
    out["vault"] = {"path": str(v), "initialized": is_initialized(v)}
    if out["vault"]["initialized"]:
        idx = Index(v)
        out["vault"].update(files=idx.file_count(), events=idx.event_count(),
                            categories=len(idx.categories()))
    out["ok"] = out["vault"]["initialized"]
    return out


def initialize(vault, seed_tags=None):
    v = Path(vault).expanduser().resolve()
    v.mkdir(parents=True, exist_ok=True)
    init_index_dir(v, seed_tags)
    vaultgen.scaffold(v)
    return str(v)


# --------------------------------------------------------------- session
class Session:
    def __init__(self, origin, vault, alias):
        self.origin = Path(origin).expanduser().resolve()
        self.vault = Path(vault).expanduser().resolve()
        self.alias = alias.strip() or "anonymous"
        self.lock = threading.RLock()
        self.index = Index(self.vault)
        vaultgen.scaffold(self.vault)  # keeps the gallery stylesheet current in older vaults
        self.gen = vaultgen.VaultGen(self.vault, self.index)
        self.thumbs = Thumbs(self.vault)
        self.hasher = scan.Hasher()
        self.queue = {it["path"]: it for it in scan.scan_origin(self.origin, exclude=[self.vault])}
        self.queue_order = list(self.queue)
        last_end = last_end_for(self.origin, self.vault)
        self.new_since_last = (sum(1 for it in self.queue.values() if it["created"] > last_end)
                               if last_end else None)
        self.undo_stack = []
        self.started = time.time()
        self.last_activity = self.started
        self.row = {"id": new_id("s", 5), "alias": self.alias, "origin_label": self.origin.name,
                    "start": now_iso(), "end": now_iso(), "sorted": 0, "active_sec": 0.0}
        self.history = self.index.history_stats(self.alias)
        remember_pair(self.origin, self.vault, self.alias)
        self._pregen_strip()

    # ------------------------------------------------------------ helpers
    def media_path(self, f):
        return self.vault / f["path"]

    def _pregen_strip(self):
        jobs = []
        for ev in self.index.recent_events(50):
            for f in ev["files"]:
                row = self.index.file(f["sha256"])
                if row:
                    jobs.append((row["sha256"], self.media_path(row), row["type"]))
        self.thumbs.pregenerate(jobs)

    def origin_file(self, rel):
        return scan.safe_join(self.origin, rel)

    def stats(self, refresh=False):
        if refresh:
            self.history = self.index.history_stats(self.alias)
        return {
            "sorted_total": self.index.file_count(),
            "sorted_session": self.row["sorted"],
            "remaining": len(self.queue),
            "new_since_last": self.new_since_last,
            **self.history,
        }

    def bootstrap(self):
        return {
            "alias": self.alias,
            "origin": str(self.origin),
            "vault": str(self.vault),
            "queue": [self.queue[p] for p in self.queue_order if p in self.queue],
            "categories": self.index.categories(),
            "tags": {"all": self.index.all_tags(), "recent": self.index.recent_tags()},
            "events": {"recent": self.index.recent_events(50), "names": self.index.event_names()},
            "stats": self.stats(),
            "thumbs": self.thumbs.available,
        }

    def after_change(self):
        return {
            "categories": self.index.categories(),
            "tags": {"all": self.index.all_tags(), "recent": self.index.recent_tags()},
            "events": {"recent": self.index.recent_events(50)},
            "stats": self.stats(),
        }

    def prefetch(self, paths):
        files = []
        for rel in paths[:4]:
            try:
                p = self.origin_file(rel)
            except PermissionError:
                continue
            if p.is_file():
                files.append(p)
        self.hasher.prefetch(files)

    # --------------------------------------------------------------- save
    def save(self, body):
        with self.lock:
            rel = body["path"]
            src = self.origin_file(rel)
            if not src.is_file():
                return {"status": "error", "error": "file no longer exists in the origin"}
            name = " ".join((body.get("event_name") or "").split())
            if not name:
                return {"status": "error", "error": "an event name is required"}
            sha = self.hasher.get(src)
            source = (body.get("source") or "").strip()
            existing = self.index.file(sha)
            if existing:  # identical bytes are always a duplicate; never stored twice
                ev = self.index.event(existing["event_id"]) or {}
                return {"status": "duplicate", "key": "hash",
                        "match": {"name": existing["name"], "event": ev.get("name", "?"),
                                  "event_id": existing["event_id"]}}
            if not body.get("force"):
                # Same post URL but different bytes: usually another image from the
                # same tweet/post, so offer to pair rather than calling it a duplicate.
                _key, match = self.index.find_duplicate(sha, scan.normalize_source(source))
                if match and match["event_id"] != body.get("event_id"):
                    ev = self.index.event(match["event_id"]) or {}
                    return {"status": "same_source",
                            "match": {"name": match["name"], "event": ev.get("name", "?"),
                                      "event_id": match["event_id"]}}

            tags = [t for t in (vaultgen.norm_tag(t) for t in body.get("tags", [])) if t]
            cats = body.get("categories", [])
            eid = body.get("event_id")
            snapshot = None
            if eid:
                snapshot = self.index.snapshot_event(eid)
                if not snapshot:
                    return {"status": "error", "error": "paired event no longer exists"}
                self.index.update_event(eid, name=name, category_ids=cats, tags=tags)
            else:
                eid = self.index.create_event(name, cats, tags, self.alias)

            item = self.queue.get(rel) or scan.guess_meta(src, self.origin)
            platform = vaultgen.safe_name(body.get("platform") or item.get("platform") or "other").lower()
            posted = (body.get("posted") or "").strip()
            month = (posted or item.get("created_iso") or now_iso())[:7]
            # .jfif etc. -> .jpg (name only; the bytes never change), decided by content
            fname = LINK_UNSAFE.sub("-", scan.normalized_name(src.name, src))
            if self.index.name_taken(fname):
                fname = f"{Path(fname).stem}-{sha[:8]}{Path(fname).suffix}"
            dest = scan.unique_path(self.vault / vaultgen.MEDIA_DIR / platform / month / fname, extra=sha[:8])
            try:
                scan.move_file(src, dest)
            except OSError as e:
                self._rollback_event(eid, snapshot)
                return {"status": "error", "error": f"could not move file: {e}"}
            if scan.sha256_file(dest) != sha:  # proof the vault copy is byte-identical
                try:
                    scan.move_file(dest, src)
                finally:
                    self._rollback_event(eid, snapshot)
                return {"status": "error", "error": "file changed while moving; it was put back in the origin"}

            self.index.add_file({
                "sha256": sha, "name": dest.name, "size": dest.stat().st_size,
                "path": dest.relative_to(self.vault).as_posix(), "event": eid,
                "source": source, "platform": platform,
                "account": (body.get("account") or "").strip(), "posted": posted,
                "type": body.get("type") or item.get("type") or "other",
                "note": (body.get("note") or "").strip(), "added_by": self.alias,
                "sorted_at": now_iso(),
                "original_name": src.name if dest.name != src.name else None,
            })
            now = time.time()
            gap = now - self.last_activity
            if gap < IDLE_GAP:
                self.row["active_sec"] += gap
            self.last_activity = now
            self.row["sorted"] += 1
            self.row["end"] = now_iso()
            self.index.upsert_session(self.row)
            self.index.flush()
            note = self.gen.write_event(eid)
            self.queue.pop(rel, None)
            remember_pair(self.origin, self.vault, self.alias, last_end=now)
            row = self.index.file(sha)
            self.thumbs.pregenerate([(sha, self.media_path(row), row["type"])])
            self.undo_stack.append({"sha": sha, "event_id": eid, "snapshot": snapshot,
                                    "origin_rel": rel, "item": item, "body": body})
            return {"status": "saved", "note": note, "event_id": eid,
                    "event_entry": next((e for e in self.index.event_names() if e["id"] == eid), None),
                    **self.after_change()}

    def _rollback_event(self, eid, snapshot):
        if snapshot:
            self.index.restore_event(snapshot)
        else:
            self.index.delete_event(eid)
        self.index._dirty.discard("events")

    # --------------------------------------------------------------- undo
    def undo(self):
        with self.lock:
            if not self.undo_stack:
                return {"status": "error", "error": "nothing to undo in this session"}
            op = self.undo_stack.pop()
            row = self.index.file(op["sha"])
            if not row:
                return {"status": "error", "error": "that file is no longer in the index"}
            back = scan.unique_path(self.origin / op["origin_rel"])
            try:
                scan.move_file(self.media_path(row), back)
            except OSError as e:
                self.undo_stack.append(op)
                return {"status": "error", "error": f"could not move file back: {e}"}
            self.index.remove_file(op["sha"])
            eid = op["event_id"]
            if op["snapshot"]:
                self.index.restore_event(op["snapshot"])
                self.index.flush()
                self.gen.write_event(eid)
            else:
                self.index.delete_event(eid)
                self.index.flush()
                self.gen.remove_note(eid)
            self.row["sorted"] = max(0, self.row["sorted"] - 1)
            if self.row["sorted"]:
                self.index.upsert_session(self.row)
            else:
                self.index.delete_session(self.row["id"])
            self.index.flush()
            rel = back.relative_to(self.origin).as_posix()
            item = dict(op["item"], path=rel, name=back.name)
            self.queue[rel] = item
            body = op["body"]
            return {"status": "undone", "item": item, "event_deleted": not op["snapshot"],
                    "form": {"event_id": eid if op["snapshot"] else None,
                             "event_name": body.get("event_name", ""),
                             "categories": body.get("categories", []),
                             "tags": body.get("tags", []), "note": body.get("note", ""),
                             "source": body.get("source", ""), "account": body.get("account", ""),
                             "posted": body.get("posted", ""), "platform": body.get("platform", ""),
                             "type": body.get("type", "")},
                    "events": {"recent": self.index.recent_events(50), "names": self.index.event_names()},
                    **{k: v for k, v in self.after_change().items() if k != "events"}}

    # ------------------------------------------------------------ duplicate
    def mark_duplicate(self, rel):
        with self.lock:
            src = self.origin_file(rel)
            if not src.is_file():
                return {"status": "error", "error": "file no longer exists"}
            dest = scan.unique_path(self.origin / "_duplicates" / src.name)
            scan.move_file(src, dest)
            self.queue.pop(rel, None)
            return {"status": "moved", "to": dest.relative_to(self.origin).as_posix(),
                    "stats": self.stats()}

    # ------------------------------------------------------------- events
    def same_post(self, source):
        """The event already holding a file from this post URL, if any."""
        norm = scan.normalize_source(source)
        if not norm:
            return None
        r = self.index.db.execute(
            "SELECT event_id FROM files WHERE source_norm=? LIMIT 1", (norm,)).fetchone()
        return self.index.event_summary(r[0]) if r else None

    def event_detail(self, eid):
        s = self.index.event_summary(eid)
        if not s:
            return {"status": "error", "error": "event not found"}
        return {"status": "ok", "event": s}

    # --------------------------------------------------------- categories
    def category_op(self, body):
        """add | rename | merge | move | delete. Destructive ops support dry_run."""
        op = body.get("op")
        idx = self.index
        with self.lock:
            if op == "add":
                cid, created = idx.add_category(body.get("name", ""), body.get("parent") or None, self.alias)
                if created:
                    idx.flush()
                    self.gen.write_category(cid)
                    parent = idx.category(cid)["parent"]
                    if parent:
                        self.gen.write_category(parent)
                return {"status": "ok", "id": cid, "created": created, "categories": idx.categories()}

            cid = body.get("id")
            c = idx.category(cid)
            if not c:
                return {"status": "error", "error": "category not found"}
            kind = "subcategory" if c["parent"] else "category"
            events = idx.events_with_category(cid)
            impact = {"events": len(events), "files": idx.files_in_events(events),
                      "category_notes": 1 + (0 if c["parent"] else len(idx.children(cid)))}

            if op == "rename":
                new = " ".join((body.get("name") or "").split())
                desc = f"Rename {kind} “{c['name']}” → “{new}”"
            elif op == "merge":
                into = idx.category(body.get("into"))
                if not into:
                    return {"status": "error", "error": "merge target not found"}
                desc = f"Merge {kind} “{c['name']}” into “{into['name']}”"
            elif op == "move":
                into = idx.category(body.get("parent"))
                if not into:
                    return {"status": "error", "error": "target category not found"}
                desc = f"Move subcategory “{c['name']}” under “{into['name']}”"
            elif op == "delete":
                desc = f"Delete {kind} “{c['name']}”" + ("" if c["parent"] else " and all its subcategories")
            else:
                return {"status": "error", "error": f"unknown op {op}"}

            if body.get("dry_run"):
                return {"status": "preview", "description": desc, "impact": impact}

            try:
                old_paths = {k: self.gen.note_paths.get(k)
                             for k in [cid] + idx.children(cid) + ([c["parent"]] if c["parent"] else [])}
                if op == "rename":
                    idx.rename_category(cid, body["name"])
                    touched = [cid] + idx.children(cid)
                    removed = []
                elif op == "merge":
                    into_id = body["into"]
                    idx.merge_category(cid, into_id)
                    removed = [k for k in old_paths if not idx.category(k)]
                    touched = [into_id] + idx.children(into_id)
                elif op == "move":
                    old_parent = c["parent"]
                    idx.move_category(cid, body["parent"])
                    touched = [cid, old_parent, body["parent"]]
                    removed = []
                else:  # delete
                    removed = idx.delete_category(cid)
                    touched = [c["parent"]] if c["parent"] else []
            except ValueError as e:
                return {"status": "error", "error": str(e)}

            idx.flush()
            for k in removed:
                self.gen.remove_note(k)
            touched = [k for k in dict.fromkeys(touched) if k and idx.category(k)]
            touched.sort(key=lambda k: bool(idx.category(k)["parent"]))
            for k in touched:
                self.gen.write_category(k)
            if op in ("rename", "merge"):  # a parent rename changes every child's note path
                for k in list(touched):
                    for ch in idx.children(k):
                        if ch not in touched:
                            self.gen.write_category(ch)
            paths = self.gen.event_paths()
            for eid in events:
                if eid in paths:
                    self.gen.write_event(eid, paths)
            return {"status": "ok", "description": desc, "impact": impact,
                    "categories": idx.categories(),
                    "events": {"recent": idx.recent_events(50)}}

    def end(self):
        with self.lock:
            if self.row["sorted"]:
                self.row["end"] = now_iso()
                self.index.upsert_session(self.row)
                self.index.flush()


def rebuild(vault):
    v = Path(vault).expanduser().resolve()
    vaultgen.scaffold(v)
    idx = Index(v)
    gen = vaultgen.VaultGen(v, idx)
    return gen.rebuild_all()


def fix_extensions(vault):
    """One-time fix for media already in the vault: give .jfif (and mislabeled)
    images an extension Obsidian can display. Renames only; each file's SHA-256 is
    checked after the rename. Safe to re-run. On a collaborator's machine it also
    renames local copies that still carry the old name the shared index recorded."""
    v = Path(vault).expanduser().resolve()
    vaultgen.scaffold(v)
    idx = Index(v)
    gen = vaultgen.VaultGen(v, idx)
    report = {"renamed": [], "failed": [], "missing": []}
    mapping, events = {}, set()
    for r in [dict(x) for x in idx.db.execute("SELECT * FROM files ORDER BY sha256")]:
        sha, cur = r["sha256"], v / r["path"]
        if not cur.exists():
            old_local = cur.with_name(r["original_name"]) if r["original_name"] else None
            if old_local and old_local.exists() and scan.sha256_file(old_local) == sha:
                scan.move_file(old_local, cur)
                report["renamed"].append(f"{old_local.name} -> {cur.name} (local copy)")
            else:
                report["missing"].append(r["path"])
            continue
        new = LINK_UNSAFE.sub("-", scan.normalized_name(r["name"], cur))
        if new == r["name"]:
            continue
        if idx.name_taken(new):
            new = f"{Path(new).stem}-{sha[:8]}{Path(new).suffix}"
        dest = scan.unique_path(cur.with_name(new), extra=sha[:8])
        try:
            scan.move_file(cur, dest)
        except OSError as e:
            report["failed"].append(f"{r['name']}: {e}")
            continue
        if scan.sha256_file(dest) != sha:
            scan.move_file(dest, cur)
            report["failed"].append(f"{r['name']}: content changed during rename; left as is")
            continue
        idx.db.execute("UPDATE files SET name=?, path=?, original_name=? WHERE sha256=?",
                       (dest.name, dest.relative_to(v).as_posix(), r["original_name"] or r["name"], sha))
        idx.mark("files:" + sha[0])
        mapping[r["name"]] = dest.name
        events.add(r["event_id"])
        report["renamed"].append(f"{r['name']} -> {dest.name}")
    idx.flush()
    gen.rewrite_names(mapping)  # references in hand-written text below the end marker
    paths = gen.event_paths()
    for eid in events:
        if eid in paths:
            gen.write_event(eid, paths)
    return report
