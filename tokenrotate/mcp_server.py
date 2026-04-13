"""TOKENROTATE MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from tokenrotate.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-tokenrotate[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-tokenrotate[mcp]'")
        return 1
    app = FastMCP("tokenrotate")

    @app.tool()
    def tokenrotate_scan(target: str) -> str:
        """Plan + track secret rotation across providers from an inventory. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
