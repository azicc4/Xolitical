"""Relational index for a Xolitical vault.

On disk (local to the vault; gitignored with the rest of vault/):  <vault>/.xolitical/
    categories.jsonl   one row per category / subcategory
    events.jsonl       one row per named event (group of paired files)
    files/<0-f>.jsonl  one row per sorted media file, sharded by sha256[0]
    sessions.jsonl     one row per sorting session (for stats)
    tags.json          seed tag vocabulary

Rows are one JSON object per line, sorted by id, so git diffs are readable and
concurrent additions from different collaborators land at scattered lines and
merge cleanly. At runtime everything is loaded into an in-memory SQLite DB for
relational queries; JSONL stays the source of truth.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from scan import normalize_source

SCHEMA = """
CREATE TABLE categories(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT,
    created_by TEXT, created TEXT);
CREATE TABLE events(
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    created_by TEXT, created TEXT, updated TEXT);
CREATE TABLE event_categories(
    event_id TEXT, category_id TEXT, PRIMARY KEY(event_id, category_id));
CREATE TABLE event_tags(
    event_id TEXT, tag TEXT, pos INTEGER, PRIMARY KEY(event_id, tag));
CREATE TABLE files(
    sha256 TEXT PRIMARY KEY, name TEXT, size INTEGER, path TEXT, event_id TEXT,
    source TEXT, source_norm TEXT, platform TEXT, account TEXT, posted TEXT,
    type TEXT, note TEXT, added_by TEXT, sorted_at TEXT, original_name TEXT);
CREATE INDEX files_event ON files(event_id);
CREATE INDEX files_source ON files(source_norm);
CREATE INDEX files_name ON files(name COLLATE NOCASE);
CREATE INDEX ec_cat ON event_categories(category_id);
CREATE TABLE sessions(
    id TEXT PRIMARY KEY, alias TEXT, origin_label TEXT, start TEXT, "end" TEXT,
    sorted INTEGER, active_sec REAL);
"""

FILE_FIELDS = ["sha256", "name", "size", "path", "event", "source", "platform",
               "account", "posted", "type", "note", "added_by", "sorted_at", "original_name"]

DEFAULT_TAGS = ["needs-review"]


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id(prefix, nbytes=6):
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def read_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    os.replace(tmp, path)


def write_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_initialized(vault: Path):
    return (vault / ".xolitical").is_dir()


def init_index_dir(vault: Path, seed_tags=None):
    d = vault / ".xolitical"
    (d / "files").mkdir(parents=True, exist_ok=True)
    for name in ("categories.jsonl", "events.jsonl", "sessions.jsonl"):
        (d / name).touch(exist_ok=True)
    tags = d / "tags.json"
    if not tags.exists():
        write_json(tags, {
            "_comment": "Seed tag vocabulary. Tags used on events are added automatically.",
            "tags": sorted(set(seed_tags or DEFAULT_TAGS)),
        })


class Index:
    def __init__(self, vault: Path):
        self.vault = vault
        self.dir = vault / ".xolitical"
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.seed_tags = []
        self._dirty = set()
        self.load()

    # ------------------------------------------------------------------ io
    def load(self):
        db = self.db
        for r in read_jsonl(self.dir / "categories.jsonl"):
            db.execute("INSERT OR REPLACE INTO categories VALUES(?,?,?,?,?)",
                       (r["id"], r["name"], r.get("parent"), r.get("created_by"), r.get("created")))
        for r in read_jsonl(self.dir / "events.jsonl"):
            db.execute("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?)",
                       (r["id"], r["name"], r.get("created_by"), r.get("created"), r.get("updated")))
            for cid in r.get("categories", []):
                db.execute("INSERT OR IGNORE INTO event_categories VALUES(?,?)", (r["id"], cid))
            for i, t in enumerate(r.get("tags", [])):
                db.execute("INSERT OR IGNORE INTO event_tags VALUES(?,?,?)", (r["id"], t, i))
        files_dir = self.dir / "files"
        if files_dir.is_dir():
            for shard in sorted(files_dir.glob("*.jsonl")):
                for r in read_jsonl(shard):
                    self._insert_file(r)
        for r in read_jsonl(self.dir / "sessions.jsonl"):
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?)",
                       (r["id"], r.get("alias"), r.get("origin_label"), r.get("start"),
                        r.get("end"), r.get("sorted", 0), r.get("active_sec", 0)))
        tags_path = self.dir / "tags.json"
        if tags_path.exists():
            try:
                self.seed_tags = json.loads(tags_path.read_text(encoding="utf-8")).get("tags", [])
            except (OSError, json.JSONDecodeError):
                self.seed_tags = []

    def _insert_file(self, r):
        self.db.execute(
            "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["sha256"], r.get("name"), r.get("size"), r.get("path"), r.get("event"),
             r.get("source", ""), normalize_source(r.get("source", "")), r.get("platform", ""),
             r.get("account", ""), r.get("posted", ""), r.get("type", ""), r.get("note", ""),
             r.get("added_by", ""), r.get("sorted_at", ""), r.get("original_name")))

    def flush(self):
        """Write every dirty table back to disk (atomic per file)."""
        dirty, self._dirty = self._dirty, set()
        if "categories" in dirty:
            rows = [dict(id=r["id"], name=r["name"], parent=r["parent"],
                         created_by=r["created_by"], created=r["created"])
                    for r in self.db.execute("SELECT * FROM categories ORDER BY id")]
            write_jsonl(self.dir / "categories.jsonl", rows)
        if "events" in dirty:
            rows = [self.event_row(r["id"]) for r in self.db.execute("SELECT id FROM events ORDER BY id")]
            write_jsonl(self.dir / "events.jsonl", rows)
        if "sessions" in dirty:
            rows = [dict(r) for r in self.db.execute('SELECT * FROM sessions ORDER BY id')]
            write_jsonl(self.dir / "sessions.jsonl", rows)
        for key in dirty:
            if key.startswith("files:"):
                shard = key.split(":", 1)[1]
                rows = [self.file_row(r) for r in self.db.execute(
                    "SELECT * FROM files WHERE sha256 LIKE ? ORDER BY sha256", (shard + "%",))]
                write_jsonl(self.dir / "files" / f"{shard}.jsonl", rows)

    def mark(self, *keys):
        self._dirty.update(keys)

    # ---------------------------------------------------------- row shapes
    def event_row(self, eid):
        e = self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        if not e:
            return None
        return {
            "id": e["id"],
            "name": e["name"],
            "categories": self.event_category_ids(eid),
            "tags": self.event_tags(eid),
            "created_by": e["created_by"],
            "created": e["created"],
            "updated": e["updated"],
        }

    @staticmethod
    def file_row(r):
        d = dict(r)
        d["event"] = d.pop("event_id")
        d.pop("source_norm", None)
        row = {k: d.get(k) for k in FILE_FIELDS}
        if not row["original_name"]:  # only recorded when the viewer renamed the file
            del row["original_name"]
        return row

    # ----------------------------------------------------------- categories
    def category(self, cid):
        r = self.db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def categories(self):
        """All categories with file counts (a subcategory also counts for its parent)."""
        counts = {r[0]: r[1] for r in self.db.execute("""
            WITH eff AS (
                SELECT ec.event_id, ec.category_id AS cid FROM event_categories ec
                UNION
                SELECT ec.event_id, c.parent FROM event_categories ec
                  JOIN categories c ON c.id = ec.category_id WHERE c.parent IS NOT NULL)
            SELECT eff.cid, COUNT(f.sha256) FROM eff
              JOIN files f ON f.event_id = eff.event_id GROUP BY eff.cid""")}
        return [dict(id=r["id"], name=r["name"], parent=r["parent"], count=counts.get(r["id"], 0))
                for r in self.db.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]

    def children(self, cid):
        return [r[0] for r in self.db.execute("SELECT id FROM categories WHERE parent=?", (cid,))]

    def find_category(self, name, parent):
        r = self.db.execute(
            "SELECT id FROM categories WHERE name=? COLLATE NOCASE AND parent IS ?",
            (name.strip(), parent)).fetchone()
        return r[0] if r else None

    def add_category(self, name, parent, alias):
        name = " ".join(name.split())
        if not name:
            raise ValueError("category name is empty")
        if parent and not self.category(parent):
            raise ValueError("parent category not found")
        if parent and self.category(parent)["parent"]:
            raise ValueError("subcategories cannot have their own subcategories")
        existing = self.find_category(name, parent)
        if existing:
            return existing, False
        cid = new_id("c", 4)
        self.db.execute("INSERT INTO categories VALUES(?,?,?,?,?)",
                        (cid, name, parent, alias, now_iso()))
        self.mark("categories")
        return cid, True

    def normalize_category_ids(self, ids):
        """Drop unknown ids, and parents already implied by a selected child."""
        known = {}
        for cid in ids or []:
            c = self.category(cid)
            if c:
                known[cid] = c
        implied = {c["parent"] for c in known.values() if c["parent"]}
        return sorted(cid for cid in known if cid not in implied)

    def events_with_category(self, cid, include_children=True):
        ids = [cid] + (self.children(cid) if include_children else [])
        q = ",".join("?" * len(ids))
        return [r[0] for r in self.db.execute(
            f"SELECT DISTINCT event_id FROM event_categories WHERE category_id IN ({q})", ids)]

    def files_in_events(self, eids):
        if not eids:
            return 0
        q = ",".join("?" * len(eids))
        return self.db.execute(f"SELECT COUNT(*) FROM files WHERE event_id IN ({q})", eids).fetchone()[0]

    def rename_category(self, cid, name):
        c = self.category(cid)
        name = " ".join(name.split())
        if not c or not name:
            raise ValueError("bad rename")
        other = self.find_category(name, c["parent"])
        if other and other != cid:
            raise ValueError(f"“{name}” already exists here — use merge instead")
        self.db.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
        self.mark("categories")

    def merge_category(self, cid, into):
        """Fold cid into `into` (same level). Children of a merged parent move across."""
        src, dst = self.category(cid), self.category(into)
        if not src or not dst or cid == into:
            raise ValueError("bad merge")
        if bool(src["parent"]) != bool(dst["parent"]):
            raise ValueError("can only merge a category into a category, or a subcategory into a subcategory")
        if not src["parent"]:
            for child in self.children(cid):
                ch = self.category(child)
                twin = self.find_category(ch["name"], into)
                if twin:
                    self._repoint(child, twin)
                    self.db.execute("DELETE FROM categories WHERE id=?", (child,))
                else:
                    self.db.execute("UPDATE categories SET parent=? WHERE id=?", (into, child))
        self._repoint(cid, into)
        self.db.execute("DELETE FROM categories WHERE id=?", (cid,))
        self._renormalize_all()
        self.mark("categories", "events")

    def move_category(self, cid, new_parent):
        c, p = self.category(cid), self.category(new_parent)
        if not c or not c["parent"] or not p or p["parent"]:
            raise ValueError("can only move a subcategory under another category")
        twin = self.find_category(c["name"], new_parent)
        if twin:
            raise ValueError(f"“{c['name']}” already exists under {p['name']} — merge instead")
        self.db.execute("UPDATE categories SET parent=? WHERE id=?", (new_parent, cid))
        self._renormalize_all()
        self.mark("categories", "events")

    def delete_category(self, cid):
        ids = [cid] + self.children(cid)
        q = ",".join("?" * len(ids))
        self.db.execute(f"DELETE FROM event_categories WHERE category_id IN ({q})", ids)
        self.db.execute(f"DELETE FROM categories WHERE id IN ({q})", ids)
        self.mark("categories", "events")
        return ids

    def _repoint(self, old, new):
        for (eid,) in self.db.execute(
                "SELECT event_id FROM event_categories WHERE category_id=?", (old,)).fetchall():
            self.db.execute("INSERT OR IGNORE INTO event_categories VALUES(?,?)", (eid, new))
        self.db.execute("DELETE FROM event_categories WHERE category_id=?", (old,))

    def _renormalize_all(self):
        for (eid,) in self.db.execute("SELECT id FROM events").fetchall():
            self.set_event_categories(eid, self.event_category_ids(eid))

    # --------------------------------------------------------------- events
    def event(self, eid):
        r = self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return dict(r) if r else None

    def event_category_ids(self, eid):
        return [r[0] for r in self.db.execute(
            "SELECT category_id FROM event_categories WHERE event_id=? ORDER BY category_id", (eid,))]

    def event_tags(self, eid):
        return [r[0] for r in self.db.execute(
            "SELECT tag FROM event_tags WHERE event_id=? ORDER BY pos", (eid,))]

    def effective_categories(self, eid):
        """(parents, subs) as category dicts — parents include those implied by subs."""
        subs, parents = [], {}
        for cid in self.event_category_ids(eid):
            c = self.category(cid)
            if not c:
                continue
            if c["parent"]:
                subs.append(c)
                p = self.category(c["parent"])
                if p:
                    parents[p["id"]] = p
            else:
                parents[c["id"]] = c
        key = lambda c: c["name"].lower()
        return sorted(parents.values(), key=key), sorted(subs, key=key)

    def set_event_categories(self, eid, ids):
        self.db.execute("DELETE FROM event_categories WHERE event_id=?", (eid,))
        for cid in self.normalize_category_ids(ids):
            self.db.execute("INSERT INTO event_categories VALUES(?,?)", (eid, cid))

    def set_event_tags(self, eid, tags):
        self.db.execute("DELETE FROM event_tags WHERE event_id=?", (eid,))
        seen = []
        for t in tags:
            if t and t not in seen:
                seen.append(t)
        for i, t in enumerate(seen):
            self.db.execute("INSERT INTO event_tags VALUES(?,?,?)", (eid, t, i))

    def create_event(self, name, category_ids, tags, alias):
        eid = new_id("ev")
        ts = now_iso()
        self.db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (eid, name, alias, ts, ts))
        self.set_event_categories(eid, category_ids)
        self.set_event_tags(eid, tags)
        self.mark("events")
        return eid

    def update_event(self, eid, name=None, category_ids=None, tags=None):
        if name is not None:
            self.db.execute("UPDATE events SET name=? WHERE id=?", (name, eid))
        if category_ids is not None:
            self.set_event_categories(eid, category_ids)
        if tags is not None:
            self.set_event_tags(eid, tags)
        self.db.execute("UPDATE events SET updated=? WHERE id=?", (now_iso(), eid))
        self.mark("events")

    def snapshot_event(self, eid):
        e = self.event(eid)
        if not e:
            return None
        return {"event": e, "categories": self.event_category_ids(eid), "tags": self.event_tags(eid)}

    def restore_event(self, snap):
        e = snap["event"]
        self.db.execute("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?)",
                        (e["id"], e["name"], e["created_by"], e["created"], e["updated"]))
        self.db.execute("DELETE FROM event_categories WHERE event_id=?", (e["id"],))
        for cid in snap["categories"]:
            self.db.execute("INSERT INTO event_categories VALUES(?,?)", (e["id"], cid))
        self.set_event_tags(e["id"], snap["tags"])
        self.mark("events")

    def delete_event(self, eid):
        self.db.execute("DELETE FROM event_categories WHERE event_id=?", (eid,))
        self.db.execute("DELETE FROM event_tags WHERE event_id=?", (eid,))
        self.db.execute("DELETE FROM events WHERE id=?", (eid,))
        self.mark("events")

    def event_files(self, eid):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM files WHERE event_id=? ORDER BY COALESCE(NULLIF(posted,''), sorted_at), sorted_at",
            (eid,))]

    def event_dates(self):
        """id -> (name, created, date) for every event; date = earliest post date."""
        out = {}
        for r in self.db.execute("""
                SELECT e.id, e.name, e.created,
                       MIN(NULLIF(f.posted,'')) AS posted, MIN(f.sorted_at) AS first_sorted
                FROM events e LEFT JOIN files f ON f.event_id = e.id GROUP BY e.id"""):
            date = r["posted"] or (r["first_sorted"] or "")[:10] or (r["created"] or "")[:10]
            out[r["id"]] = (r["name"], r["created"] or "", date)
        return out

    def event_summary(self, eid, max_files=3):
        e = self.event(eid)
        if not e:
            return None
        files = [dict(r) for r in self.db.execute(
            "SELECT sha256, name, type FROM files WHERE event_id=? ORDER BY sorted_at DESC", (eid,))]
        last = self.db.execute("SELECT MAX(sorted_at) FROM files WHERE event_id=?", (eid,)).fetchone()[0]
        return {
            "id": eid,
            "name": e["name"],
            "count": len(files),
            "last": last or e["updated"],
            "files": files[:max_files],
            "categories": self.event_category_ids(eid),
            "tags": self.event_tags(eid),
        }

    def recent_events(self, limit=50):
        ids = [r[0] for r in self.db.execute("""
            SELECT event_id FROM files GROUP BY event_id
            ORDER BY MAX(sorted_at) DESC LIMIT ?""", (limit,))]
        return [self.event_summary(i) for i in ids]

    def event_names(self):
        dates = self.event_dates()
        counts = dict(self.db.execute("SELECT event_id, COUNT(*) FROM files GROUP BY event_id").fetchall())
        lasts = dict(self.db.execute("SELECT event_id, MAX(sorted_at) FROM files GROUP BY event_id").fetchall())
        out = [{"id": eid, "name": n, "date": d, "count": counts.get(eid, 0), "last": lasts.get(eid, "")}
               for eid, (n, _c, d) in dates.items()]
        out.sort(key=lambda x: x["last"] or "", reverse=True)
        return out

    def find_event_by_name(self, name):
        return [r[0] for r in self.db.execute(
            "SELECT id FROM events WHERE name=? COLLATE NOCASE", (name.strip(),))]

    # ----------------------------------------------------------------- tags
    def all_tags(self):
        counts = dict(self.db.execute("SELECT tag, COUNT(*) FROM event_tags GROUP BY tag").fetchall())
        for t in self.seed_tags:
            counts.setdefault(t, 0)
        return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]

    def recent_tags(self, n=10):
        out = []
        for r in self.db.execute("""
                SELECT t.tag FROM event_tags t JOIN events e ON e.id = t.event_id
                ORDER BY e.updated DESC, t.pos DESC"""):
            if r[0] not in out:
                out.append(r[0])
                if len(out) >= n:
                    break
        return out

    # ---------------------------------------------------------------- files
    def file(self, sha):
        r = self.db.execute("SELECT * FROM files WHERE sha256=?", (sha,)).fetchone()
        return dict(r) if r else None

    def find_duplicate(self, sha, source_norm):
        r = self.db.execute("SELECT * FROM files WHERE sha256=?", (sha,)).fetchone()
        if r:
            return "hash", dict(r)
        if source_norm:
            r = self.db.execute("SELECT * FROM files WHERE source_norm=?", (source_norm,)).fetchone()
            if r:
                return "source", dict(r)
        return None, None

    def name_taken(self, name):
        return self.db.execute(
            "SELECT 1 FROM files WHERE name=? COLLATE NOCASE", (name,)).fetchone() is not None

    def add_file(self, row):
        self._insert_file(row)
        self.mark("files:" + row["sha256"][0])

    def remove_file(self, sha):
        self.db.execute("DELETE FROM files WHERE sha256=?", (sha,))
        self.mark("files:" + sha[0])

    def file_count(self):
        return self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def event_count(self):
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ------------------------------------------------------------- sessions
    def upsert_session(self, s):
        self.db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?)",
                        (s["id"], s["alias"], s["origin_label"], s["start"], s["end"],
                         s["sorted"], round(s["active_sec"], 1)))
        self.mark("sessions")

    def delete_session(self, sid):
        self.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        self.mark("sessions")

    def history_stats(self, alias):
        """Average files per session and files per active minute (alias's own history,
        falling back to everyone's when the alias has none)."""
        rows = self.db.execute(
            "SELECT sorted, active_sec FROM sessions WHERE alias=? AND sorted > 0", (alias,)).fetchall()
        if not rows:
            rows = self.db.execute("SELECT sorted, active_sec FROM sessions WHERE sorted > 0").fetchall()
        total = sum(r[0] for r in rows)
        active = sum(r[1] or 0 for r in rows)
        return {
            "sessions": len(rows),
            "avg_per_session": round(total / len(rows), 1) if rows else None,
            "per_minute": round(total / (active / 60), 2) if active >= 30 else None,
        }
