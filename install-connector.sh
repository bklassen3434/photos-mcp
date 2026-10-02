#!/bin/zsh
# Adds the "iphoto" connector to the Claude desktop app.
# The app rewrites its config on quit, so this quits it first, edits, then reopens.
set -e
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

echo "Quitting Claude..."
osascript -e 'quit app "Claude"'
while [[ "$(osascript -e 'application "Claude" is running')" == "true" ]]; do sleep 1; done
sleep 2

UV="$(command -v uv)"
REPO="$(cd "$(dirname "$0")" && pwd)"
PHOTOS_FOLDER="${PHOTOS_FOLDER:-$HOME/Pictures/icloud}"

cp "$CONFIG" "$CONFIG.bak"
python3 - "$CONFIG" "$UV" "$REPO" "$PHOTOS_FOLDER" <<'EOF'
import json, sys
path, uv, repo, folder = sys.argv[1:]
config = json.load(open(path))
config.setdefault("mcpServers", {})["iphoto"] = {
    "command": uv,
    "args": ["run", "--directory", repo, "iphoto_server.py"],
    "env": {"PHOTOS_FOLDER": folder},
}
json.dump(config, open(path, "w"), indent=2)
print("Connectors:", ", ".join(config["mcpServers"]))
EOF

echo "Reopening Claude..."
open -a Claude
