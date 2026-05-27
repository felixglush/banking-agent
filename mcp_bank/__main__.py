"""Launch the ``bank`` MCP server over stdio.

The Temporal worker spawns this via ``MCPServerStdio(name="bank", params=...)``
(see docs/build-plan.md §Stage 4). Stdio is the only transport at v0.1.

    uv run python -m mcp_bank
"""

from __future__ import annotations

from mcp_bank.server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
