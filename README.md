# photos-mcp

Read-only MCP server for iCloud Photos, all local — no Apple ID credentials stored.

Registered as the **`iphoto`** connector (`iphoto_server.py`) in the Claude desktop app and Claude Code.
It serves a folder of photos downloaded with [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader), indexed to SQLite (`folder_server.py`).

Tools: `library_info`, `search_photos`, `get_photo_details`, `view_photo`, `refresh_index`.

## Setup

```bash
./install-connector.sh   # set PHOTOS_FOLDER first to use a folder other than ~/Pictures/icloud
```

The folder index (`<folder>/.photos-index.sqlite`) holds date, camera, GPS, an offline place name,
and on-device Apple Vision content labels + OCR text.

## Updating the photos

```bash
uvx icloudpd --username YOUR_APPLE_ID --directory ~/Pictures/icloud --folder-structure "{:%Y/%m}" --cookie-directory ~/.pyicloud
uv run python folder_index.py ~/Pictures/icloud
```

Both are incremental. Apple's sign-in session expires after ~2 months; icloudpd will re-prompt for 2FA.
Or ask Claude to call `refresh_index` after downloading.

## Notes
- `server.py` (Mac Photos.app library via osxphotos) is not part of the connector.
- Desktop config: `~/Library/Application Support/Claude/claude_desktop_config.json` (backup at `.bak`).
