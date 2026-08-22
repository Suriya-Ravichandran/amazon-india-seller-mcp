"""Compatibility shim: ``python server.py`` still starts the MCP server.

The code moved into the ``amazon_india_seller_mcp`` package so the project can
be published and installed, but Claude Desktop configurations that point at this
file by path keep working unchanged.

Prefer one of these for new setups::

    amazon-india-seller-mcp          # installed console script
    python -m amazon_india_seller_mcp
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from a source checkout without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from amazon_india_seller_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
