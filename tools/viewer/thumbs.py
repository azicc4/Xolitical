"""Thumbnail cache for the event strip.

Uses Pillow for images and ffmpeg for video frames when available; both are
optional. Callers fall back to the original file when no thumbnail can be made.
"""

import shutil
import subprocess
import threading
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow is optional
    Image = None

FFMPEG = shutil.which("ffmpeg")
SIZE = 260


class Thumbs:
    def __init__(self, vault: Path):
        self.dir = vault / ".xolitical" / "cache" / "thumbs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._inflight = {}

    @property
    def available(self):
        return {"images": Image is not None, "videos": FFMPEG is not None}

    def path_for(self, sha):
        return self.dir / f"{sha[:20]}.jpg"

    def ensure(self, sha, src: Path, kind):
        """Return a cached thumbnail path, generating it if needed; None if impossible."""
        out = self.path_for(sha)
        if out.exists():
            return out
        with self._lock:
            ev = self._inflight.get(sha)
            owner = ev is None
            if owner:
                ev = self._inflight[sha] = threading.Event()
        if not owner:
            ev.wait(30)
            return out if out.exists() else None
        try:
            if kind == "image" and Image is not None:
                self._image(src, out)
            elif kind == "video" and FFMPEG:
                self._video(src, out)
        except Exception:
            pass
        finally:
            with self._lock:
                self._inflight.pop(sha, None)
            ev.set()
        return out if out.exists() else None

    def _image(self, src, out):
        with Image.open(src) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((SIZE, SIZE))
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (24, 24, 28))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            tmp = out.with_suffix(".tmp.jpg")
            im.save(tmp, "JPEG", quality=80)
            tmp.replace(out)

    def _video(self, src, out):
        tmp = out.with_suffix(".tmp.jpg")
        for seek in ("1", "0"):
            subprocess.run(
                [FFMPEG, "-y", "-loglevel", "error", "-ss", seek, "-i", str(src),
                 "-frames:v", "1", "-vf", f"scale={SIZE}:-2", str(tmp)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(out)
                return

    def pregenerate(self, jobs):
        """jobs: iterable of (sha, path, kind). Runs in a background thread."""
        jobs = list(jobs)

        def work():
            for sha, path, kind in jobs:
                if path.exists():
                    self.ensure(sha, path, kind)

        threading.Thread(target=work, daemon=True).start()
