"""TOKENROTATE MCP server — exposes rotation planning as an MCP tool for Cognis.Studio."""
from __future__ import annotations

import json
import sys


def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-tokenrotate[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "error: MCP extra not installed. Run: pip install 'cognis-tokenrotate[mcp]'",
            file=sys.stderr,
        )
        return 1

    from tokenrotate.core import build_plan, load_inventory, summarize

    app = FastMCP("tokenrotate")

    @app.tool()
    def tokenrotate_scan(inventory_path: str) -> str:
        """Plan + track secret rotation across providers from an inventory JSON file.

        Args:
            inventory_path: Path to the inventory JSON file.

        Returns JSON with rotation plan and summary; or a JSON error object on failure.
        """
        if not inventory_path or not inventory_path.strip():
            return json.dumps({"error": "inventory_path must be a non-empty string"})
        try:
            inv = load_inventory(inventory_path.strip())
            plan = build_plan(inv)
            summary = summarize(plan)
            return json.dumps(
                {"plan": plan.to_dict(), "summary": summary},
                indent=2,
            )
        except FileNotFoundError:
            return json.dumps({"error": "inventory file not found: %s" % inventory_path})
        except (ValueError, json.JSONDecodeError) as exc:
            return json.dumps({"error": "invalid inventory: %s" % exc})
        except OSError as exc:
            return json.dumps({"error": "could not read inventory: %s" % exc})

    try:
        app.run()
    except Exception as exc:  # pragma: no cover
        print("error: MCP server exited unexpectedly: %s" % exc, file=sys.stderr)
        return 1
    return 0
