"""Index a folder of downloaded iCloud photos (e.g. from icloudpd) into SQLite.

Extracts date/camera/GPS from file metadata, reverse-geocodes GPS offline, and
labels image content + reads text on-device with Apple's Vision framework.

Usage: uv run python folder_index.py ~/Pictures/icloud [--no-vision]
Re-running is incremental: only new or changed files are processed.
"""

import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

IMAGE_EXTS = {".heic", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".avif"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}
def log(*args):
    print(*args, file=sys.stderr, flush=True)  # stdout is the MCP channel


INDEX_NAME = ".photos-index.sqlite"
# Labels that suggest there's text worth reading (PNGs, mostly screenshots, are always read).
TEXTY_LABELS = {"document", "text", "receipt", "paper", "handwriting", "screenshot", "sign",
                "menu", "poster", "book", "whiteboard", "blackboard", "diagram", "chart"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    path TEXT PRIMARY KEY,
    filename TEXT, kind TEXT, date TEXT,
    lat REAL, lon REAL, place TEXT, camera TEXT,
    width INTEGER, height INTEGER, size INTEGER, mtime REAL,
    labels TEXT, text TEXT, vision_done INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_date ON photos(date);
"""


def connect(folder: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(folder / INDEX_NAME)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _gps_to_deg(values, ref) -> float | None:
    try:
        d, m, s = (float(v) for v in values)
        deg = d + m / 60 + s / 3600
        return -deg if ref in ("S", "W") else deg
    except Exception:
        return None


def read_metadata(path: str) -> dict:
    """Runs in a worker process."""
    st = os.stat(path)
    ext = Path(path).suffix.lower()
    row = {
        "path": path, "filename": Path(path).name,
        "kind": "video" if ext in VIDEO_EXTS else "photo",
        # icloudpd sets mtime to the photo's creation time; EXIF overrides below.
        "date": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "lat": None, "lon": None, "camera": None, "width": None, "height": None,
        "size": st.st_size, "mtime": st.st_mtime,
    }
    if row["kind"] == "photo":
        try:
            from PIL import Image
            from pillow_heif import register_heif_opener
            register_heif_opener()
            with Image.open(path) as img:
                row["width"], row["height"] = img.size
                exif = img.getexif()
                row["camera"] = exif.get(272)
                dt = exif.get_ifd(0x8769).get(36867) or exif.get(306)
                if dt:
                    row["date"] = datetime.strptime(dt.strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
                gps = exif.get_ifd(0x8825)
                if 2 in gps and 4 in gps:
                    row["lat"] = _gps_to_deg(gps[2], gps.get(1))
                    row["lon"] = _gps_to_deg(gps[4], gps.get(3))
        except Exception:
            pass
    return row


def run_vision(path: str) -> tuple[str, str, str]:
    """Runs in a worker process: content labels + OCR for text-heavy images."""
    import Foundation
    import Vision

    try:
        url = Foundation.NSURL.fileURLWithPath_(path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        classify = Vision.VNClassifyImageRequest.alloc().init()
        handler.performRequests_error_([classify], None)
        labels = [o.identifier() for o in (classify.results() or []) if o.confidence() >= 0.3]
        text = ""
        if path.lower().endswith(".png") or TEXTY_LABELS & set(labels):
            ocr = Vision.VNRecognizeTextRequest.alloc().init()
            ocr.setRecognitionLevel_(0)  # accurate
            handler.performRequests_error_([ocr], None)
            text = "\n".join(o.topCandidates_(1)[0].string() for o in (ocr.results() or []))
        return path, " ".join(labels), text
    except Exception:
        return path, "", ""


def geocode(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT path, lat, lon FROM photos WHERE lat IS NOT NULL AND place IS NULL"
    ).fetchall()
    if not rows:
        return
    import reverse_geocoder as rg

    results = rg.search([(r["lat"], r["lon"]) for r in rows], mode=1, verbose=False)
    conn.executemany(
        "UPDATE photos SET place = ? WHERE path = ?",
        [(f"{g['name']}, {g['admin1']}, {g['cc']}", r["path"]) for r, g in zip(rows, results)],
    )
    conn.commit()


def build_index(folder: Path, vision: bool = True, workers: int | None = None) -> None:
    conn = connect(folder)
    known = {r["path"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT path, size, mtime FROM photos")}
    on_disk = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            ext = Path(f).suffix.lower()
            if not f.startswith(".") and (ext in IMAGE_EXTS or ext in VIDEO_EXTS):
                on_disk.append(os.path.join(root, f))

    gone = set(known) - set(on_disk)
    conn.executemany("DELETE FROM photos WHERE path = ?", [(p,) for p in gone])
    todo = [p for p in on_disk if (lambda s: known.get(p) != (s.st_size, s.st_mtime))(os.stat(p))]
    log(f"{len(on_disk)} files, {len(todo)} new/changed, {len(gone)} removed")

    workers = workers or max(2, (os.cpu_count() or 4) - 2)
    with ProcessPoolExecutor(workers) as pool:
        for i, row in enumerate(pool.map(read_metadata, todo, chunksize=64), 1):
            conn.execute(
                """INSERT OR REPLACE INTO photos
                   (path, filename, kind, date, lat, lon, camera, width, height, size, mtime)
                   VALUES (:path, :filename, :kind, :date, :lat, :lon, :camera, :width, :height, :size, :mtime)""",
                row,
            )
            if i % 1000 == 0:
                conn.commit()
                log(f"  metadata {i}/{len(todo)}")
        conn.commit()
        geocode(conn)

        if vision:
            pending = [r["path"] for r in conn.execute(
                "SELECT path FROM photos WHERE kind = 'photo' AND vision_done = 0")]
            log(f"Vision labeling {len(pending)} images...")
            start = time.time()
            for i, (path, labels, text) in enumerate(pool.map(run_vision, pending, chunksize=16), 1):
                conn.execute(
                    "UPDATE photos SET labels = ?, text = ?, vision_done = 1 WHERE path = ?",
                    (labels, text, path),
                )
                if i % 500 == 0:
                    conn.commit()
                    rate = i / (time.time() - start)
                    log(f"  vision {i}/{len(pending)} ({rate:.0f}/s, ~{(len(pending) - i) / rate / 60:.0f} min left)")
            conn.commit()
    log("Done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--no-vision", action="store_true", help="skip content labels/OCR")
    ap.add_argument("--workers", type=int)
    args = ap.parse_args()
    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")
    build_index(folder, vision=not args.no_vision, workers=args.workers)
