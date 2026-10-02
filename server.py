"""MCP server for iCloud Photos, via the local macOS Photos library (read-only).

Photos.app syncs your iCloud library to disk; this server reads that database
with osxphotos, so no Apple ID credentials are needed.
"""

import io
import os
from datetime import datetime, timedelta
from pathlib import Path

import osxphotos
from mcp.server.mcpserver import Image, MCPServer
from PIL import Image as PILImage
from pillow_heif import register_heif_opener  # installed with osxphotos

register_heif_opener()

mcp = MCPServer("icloud-photos")

_db: osxphotos.PhotosDB | None = None


def db() -> osxphotos.PhotosDB:
    """Load the Photos database once; call refresh_library to reload."""
    global _db
    if _db is None:
        _db = osxphotos.PhotosDB(dbfile=os.environ.get("PHOTOS_LIBRARY"))
    return _db


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _summary(p: osxphotos.PhotoInfo) -> dict:
    return {
        "uuid": p.uuid,
        "filename": p.original_filename,
        "date": p.date.isoformat() if p.date else None,
        "type": "video" if p.ismovie else "photo",
        "title": p.title,
        "place": p.place.name if p.place else None,
        "people": [n for n in p.persons if n != "_UNKNOWN_"],
        "favorite": p.favorite,
    }


def _detail(p: osxphotos.PhotoInfo) -> dict:
    d = _summary(p)
    d.update(
        description=p.description,
        keywords=p.keywords,
        labels=p.labels,
        albums=p.albums,
        location=p.location if p.location != (None, None) else None,
        width=p.width,
        height=p.height,
        hidden=p.hidden,
        screenshot=p.screenshot,
        selfie=p.selfie,
        live_photo=p.live_photo,
        burst=p.burst,
        edited=p.hasadjustments,
        downloaded_locally=not p.ismissing,
        in_icloud=p.incloud,
        camera=p.exif_info.camera_model if p.exif_info else None,
        score=round(p.score.overall, 3) if p.score else None,
    )
    return d


def _get(uuid: str) -> osxphotos.PhotoInfo:
    photos = db().photos(uuid=[uuid])
    if not photos:
        raise ValueError(f"No photo with uuid {uuid}")
    return photos[0]


def _viewable_path(p: osxphotos.PhotoInfo) -> str | None:
    """Best local file to view: edited, then original, then Photos' cached preview."""
    for path in (p.path_edited, p.path):
        if path and Path(path).exists():
            return path
    for path in p.path_derivatives:  # largest first; present even for iCloud-only items
        if Path(path).exists() and not path.endswith((".mov", ".mp4")):
            return path
    return None


@mcp.tool()
def library_info() -> dict:
    """Overview of the Photos library: counts, date range, and top albums/people/places."""
    d = db()
    photos = d.photos(images=True, movies=True)
    dates = sorted(p.date for p in photos if p.date)
    top = lambda counts, n=20: dict(sorted(counts.items(), key=lambda kv: -kv[1])[:n])
    places: dict[str, int] = {}
    for p in photos:
        if p.place and p.place.name:
            places[p.place.name] = places.get(p.place.name, 0) + 1
    return {
        "library_path": d.library_path,
        "photos": sum(1 for p in photos if not p.ismovie),
        "videos": sum(1 for p in photos if p.ismovie),
        "favorites": sum(1 for p in photos if p.favorite),
        "not_downloaded_locally": sum(1 for p in photos if p.ismissing),
        "earliest": dates[0].isoformat() if dates else None,
        "latest": dates[-1].isoformat() if dates else None,
        "top_albums": top(d.albums_as_dict),
        "top_people": top({k: v for k, v in d.persons_as_dict.items() if k != "_UNKNOWN_"}),
        "top_places": top(places),
        "top_keywords": top(d.keywords_as_dict),
    }


@mcp.tool()
def search_photos(
    text: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    person: str | None = None,
    album: str | None = None,
    place: str | None = None,
    label: str | None = None,
    media_type: str = "all",
    favorites_only: bool = False,
    include_hidden: bool = False,
    sort: str = "newest",
    limit: int = 50,
) -> dict:
    """Search photos and videos. All filters are optional and combined with AND.

    Args:
        text: Free text matched against title, description, keywords, AI labels
            (e.g. "dog", "beach"), place, people, albums, and filename.
        start_date: ISO date/datetime, inclusive (e.g. "2024-06-01").
        end_date: ISO date/datetime, inclusive (a bare date covers that whole day).
        person: Name of a recognized person (case-insensitive substring).
        album: Album name (case-insensitive substring).
        place: Place/location name (case-insensitive substring, e.g. "Paris").
        label: Apple's AI-detected object/scene label (case-insensitive substring).
        media_type: "all", "photo", or "video".
        favorites_only: Only return favorites.
        include_hidden: Include photos in the Hidden album.
        sort: "newest", "oldest", or "best" (Apple's aesthetic score).
        limit: Max results (default 50, max 500).
    """
    kwargs = {"images": media_type != "video", "movies": media_type != "photo"}
    if start_date:
        kwargs["from_date"] = _parse_date(start_date)
    if end_date:
        end = _parse_date(end_date)
        if len(end_date) <= 10:
            end += timedelta(days=1)
        kwargs["to_date"] = end
    photos = db().photos(**kwargs)

    def has(needle: str | None, *hay) -> bool:
        if not needle:
            return True
        n = needle.lower()
        return any(n in (h or "").lower() for h in hay)

    results = []
    for p in photos:
        if (p.hidden and not include_hidden) or (favorites_only and not p.favorite):
            continue
        place_name = p.place.name if p.place else None
        if not has(person, *p.persons):
            continue
        if not has(album, *p.albums):
            continue
        if not has(place, place_name):
            continue
        if not has(label, *p.labels):
            continue
        if not has(
            text, p.title, p.description, p.original_filename, place_name,
            *p.keywords, *p.labels, *p.persons, *p.albums,
        ):
            continue
        results.append(p)

    if sort == "best":
        results.sort(key=lambda p: p.score.overall if p.score else 0, reverse=True)
    else:
        results.sort(key=lambda p: p.date or datetime.min, reverse=(sort != "oldest"))
    limit = max(1, min(limit, 500))
    return {
        "total_matches": len(results),
        "returned": min(limit, len(results)),
        "results": [_summary(p) for p in results[:limit]],
    }


@mcp.tool()
def get_photo_details(uuid: str) -> dict:
    """Full metadata for one photo/video: labels, keywords, albums, GPS, camera, etc."""
    return _detail(_get(uuid))


@mcp.tool()
def view_photo(uuid: str, max_size: int = 1024) -> Image:
    """Return the photo as an image so you can look at it.

    Works for iCloud-only photos too (falls back to Photos' cached preview).
    For videos, returns the poster frame.

    Args:
        uuid: Photo uuid from search_photos.
        max_size: Longest edge in pixels (default 1024, max 2048).
    """
    p = _get(uuid)
    path = _viewable_path(p)
    if not path:
        raise ValueError("No local image or preview available for this item.")
    if p.ismovie and not path.endswith((".jpg", ".jpeg", ".heic")):
        raise ValueError("No preview frame available for this video.")
    img = PILImage.open(path)
    img = img.convert("RGB")
    size = max(64, min(max_size, 2048))
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return Image(data=buf.getvalue(), format="jpeg")


@mcp.tool()
def list_albums() -> list[dict]:
    """List all albums with photo counts and folder path."""
    return [
        {"title": a.title, "count": len(a.photos), "folder": "/".join(a.folder_names) or None}
        for a in db().album_info
    ]


@mcp.tool()
def list_people() -> dict:
    """Recognized people (from Photos' People album) with photo counts."""
    people = {k: v for k, v in db().persons_as_dict.items() if k != "_UNKNOWN_"}
    return dict(sorted(people.items(), key=lambda kv: -kv[1]))


@mcp.tool()
def export_photos(uuids: list[str], destination: str, use_edited: bool = True) -> dict:
    """Copy photos/videos to a folder on disk. Originals must be downloaded locally.

    Args:
        uuids: Photo uuids to export.
        destination: Folder path (created if missing). "~" is expanded.
        use_edited: Export the edited version when one exists.
    """
    dest = Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    exported, failed = [], []
    for uuid in uuids:
        try:
            p = _get(uuid)
            if p.ismissing:
                failed.append({"uuid": uuid, "error": "not downloaded from iCloud"})
                continue
            files = p.export(str(dest), edited=use_edited and p.hasadjustments)
            exported.extend(files)
        except Exception as e:
            failed.append({"uuid": uuid, "error": str(e)})
    return {"exported": exported, "failed": failed}


@mcp.tool()
def refresh_library() -> str:
    """Reload the Photos database to pick up newly synced photos."""
    global _db
    _db = None
    return f"Reloaded: {len(db().photos())} items."


def main():
    mcp.run()


if __name__ == "__main__":
    main()
