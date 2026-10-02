# photos-mcp

Read-only MCP server for iCloud Photos, all local — no Apple ID credentials stored.

Registered as the **`iphoto`** connector (`iphoto_server.py`) in the Claude desktop app and Claude Code.
It serves only the Columbia photos (tools prefixed `columbia_*`).

| Prefix | Source | File |
|---|---|---|
| `columbia_*` | Folder downloaded with icloudpd, indexed to SQLite | `folder_server.py` |

The folder index (`<folder>/.photos-index.sqlite`) holds date, camera, GPS, an offline place name,
and on-device Apple Vision content labels + OCR text.

## Updating the Columbia photos

```bash
uvx icloudpd --username YOUR_APPLE_ID --directory ~/Pictures/icloud-columbia --folder-structure "{:%Y/%m}" --cookie-directory ~/.pyicloud
uv run python folder_index.py ~/Pictures/icloud-columbia
```

Both are incremental. Apple's sign-in session expires after ~2 months; icloudpd will re-prompt for 2FA.
Or ask Claude to call `refresh_index` after downloading.

## Notes
- `server.py` (Mac Photos.app library via osxphotos) is not part of the connector.
- Desktop config: `~/Library/Application Support/Claude/claude_desktop_config.json` (backup at `.bak`).
