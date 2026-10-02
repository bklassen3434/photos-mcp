"""MCP server for a folder of downloaded iCloud photos (read-only).

Queries the SQLite index built by folder_index.py. Set PHOTOS_FOLDER to the folder.
"""

import io
import os
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage
from pillow_heif import register_heif_opener

import folder_index

register_heif_opener()

FOLDER = Path(os.environ.get("PHOTOS_FOLDER", "~/Pictures/icloud")).expanduser().resolve()

mcp = MCPServer("icloud-photos-folder")


def conn():
    return folder_index.connect(FOLDER)


def _summary(r) -> dict:
    return {
        "path": r["path"],
        "date": r["date"],
        "type": r["kind"],
        "place": r["place"],
        "labels": (r["labels"] or "").split()[:8],
    }


def _get(path: str):
    r = conn().execute("SELECT * FROM photos WHERE path = ?", (path,)).fetchone()
    if not r:
        raise ValueError(f"Not in index: {path}")
    return r


@mcp.tool()
def library_info() -> dict:
    """Overview: counts, date range, photos per year, top places, cameras and content labels."""
    c = conn()
    one = lambda sql: c.execute(sql).fetchone()[0]
    top = lambda sql: {r[0]: r[1] for r in c.execute(sql)}
    label_counts: dict[str, int] = {}
    for (labels,) in c.execute("SELECT labels FROM photos WHERE labels != ''"):
        for label in labels.split():
            label_counts[label] = label_counts.get(label, 0) + 1
    return {
        "folder": str(FOLDER),
        "photos": one("SELECT COUNT(*) FROM photos WHERE kind = 'photo'"),
        "videos": one("SELECT COUNT(*) FROM photos WHERE kind = 'video'"),
        "with_gps": one("SELECT COUNT(*) FROM photos WHERE lat IS NOT NULL"),
        "not_yet_labeled": one("SELECT COUNT(*) FROM photos WHERE kind = 'photo' AND vision_done = 0"),
        "earliest": one("SELECT MIN(date) FROM photos"),
        "latest": one("SELECT MAX(date) FROM photos"),
        "per_year": top("SELECT substr(date, 1, 4), COUNT(*) FROM photos GROUP BY 1 ORDER BY 1"),
        "top_places": top("SELECT place, COUNT(*) FROM photos WHERE place IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 25"),
        "top_cameras": top("SELECT camera, COUNT(*) FROM photos WHERE camera IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
        "top_labels": dict(sorted(label_counts.items(), key=lambda kv: -kv[1])[:60]),
    }


@mcp.tool()
def search_photos(
    text: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    place: str | None = None,
    camera: str | None = None,
    media_type: str = "all",
    has_text: bool = False,
    sort: str = "newest",
    limit: int = 50,
) -> dict:
    """Search photos and videos. All filters are optional and combined with AND.

    Args:
        text: Words that must all appear somewhere in: on-device content labels
            (e.g. "dog", "beach", "food", "snow", "document"; multi-word labels use
            underscores like "blue_sky"), text read from the image (OCR, for
            screenshots/documents), place name, camera model, or filename.
        start_date: ISO date, inclusive (e.g. "2019-06-01").
        end_date: ISO date, inclusive.
        place: Place name substring, e.g. "New York" or "FR" (country code).
        camera: Camera model substring, e.g. "iPhone 12".
        media_type: "all", "photo", or "video".
        has_text: Only images where readable text was found (screenshots, docs, signs).
        sort: "newest", "oldest", or "random".
        limit: Max results (default 50, max 500).
    """
    where, params = [], []
    for term in (text or "").split():
        where.append(
            "(labels LIKE ? OR text LIKE ? OR place LIKE ? OR camera LIKE ? OR filename LIKE ?)"
        )
        params += [f"%{term}%"] * 5
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date < ?")
        params.append((date.fromisoformat(end_date[:10]) + timedelta(days=1)).isoformat())
    if place:
        where.append("place LIKE ?")
        params.append(f"%{place}%")
    if camera:
        where.append("camera LIKE ?")
        params.append(f"%{camera}%")
    if media_type in ("photo", "video"):
        where.append("kind = ?")
        params.append(media_type)
    if has_text:
        where.append("text != ''")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = {"oldest": "date ASC", "random": "RANDOM()"}.get(sort, "date DESC")
    limit = max(1, min(limit, 500))
    c = conn()
    total = c.execute(f"SELECT COUNT(*) FROM photos {clause}", params).fetchone()[0]
    rows = c.execute(f"SELECT * FROM photos {clause} ORDER BY {order} LIMIT ?", params + [limit])
    results = [_summary(r) for r in rows]
    return {"total_matches": total, "returned": len(results), "results": results}


@mcp.tool()
def get_photo_details(path: str) -> dict:
    """All indexed metadata for one file, including every label and any OCR text."""
    r = _get(path)
    return {k: r[k] for k in r.keys() if k not in ("mtime", "vision_done")}


@mcp.tool()
def view_photo(path: str, max_size: int = 1024) -> Image:
    """Return the photo (or a video's thumbnail frame) as an image so you can look at it.

    Args:
        path: File path from search_photos.
        max_size: Longest edge in pixels (default 1024, max 2048).
    """
    r = _get(path)
    size = max(64, min(max_size, 2048))
    if r["kind"] == "video":
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", tmp, path],
                           capture_output=True, timeout=30)
            thumbs = list(Path(tmp).glob("*.png"))
            if not thumbs:
                raise ValueError("Couldn't generate a thumbnail for this video.")
            img = PILImage.open(thumbs[0])
            img.load()
    else:
        img = PILImage.open(path)
    img = img.convert("RGB")
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return Image(data=buf.getvalue(), format="jpeg")


@mcp.tool()
def refresh_index() -> str:
    """Re-scan the folder for new/changed/deleted files (incremental) and label new images."""
    folder_index.build_index(FOLDER)
    n = conn().execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    return f"Index up to date: {n} items."


def main():
    mcp.run()


if __name__ == "__main__":
    main()
