"""The "iphoto" MCP server: iCloud photos downloaded to a folder.

Tools come from folder_server.py.
"""

from mcp.server.mcpserver import MCPServer

import folder_server

mcp = MCPServer("iphoto")

for name in ["library_info", "search_photos", "get_photo_details", "view_photo", "refresh_index"]:
    fn = getattr(folder_server, name)
    mcp.add_tool(fn, name=name, description=fn.__doc__)

if __name__ == "__main__":
    mcp.run()
