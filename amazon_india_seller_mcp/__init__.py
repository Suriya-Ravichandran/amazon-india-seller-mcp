"""Amazon India Product Research MCP server.

An MCP server that turns Claude Desktop into a product research assistant for
beginner Amazon India sellers.

Run it directly::

    amazon-india-seller-mcp

or point Claude Desktop at ``python -m amazon_india_seller_mcp``.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__", "create_server", "main"]


def __getattr__(name: str):
    """Expose the server lazily so importing the package stays cheap."""
    if name in {"create_server", "main"}:
        from amazon_india_seller_mcp.server import create_server, main

        return {"create_server": create_server, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
