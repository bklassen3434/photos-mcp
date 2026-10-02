"""The "iphoto" MCP server: iCloud photos downloaded to a folder.

Tools come from folder_server.py, exposed with a columbia_ prefix.
"""

from mcp.server.mcpserver import MCPServer

import folder_server

mcp = MCPServer("iphoto")

for name in ["library_info", "search_photos", "get_photo_details", "view_photo", "refresh_index"]:
    fn = getattr(folder_server, name)
    mcp.add_tool(fn, name=f"columbia_{name}",
                 description=f"[Columbia iCloud photos] {fn.__doc__}")

if __name__ == "__main__":
    mcp.run()
