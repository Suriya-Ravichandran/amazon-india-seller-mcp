"""Connection self-test for the Amazon India Product Research MCP.

Starts ``server.py`` over stdio exactly the way Claude Desktop does, completes the
MCP handshake, lists the tools and makes three real tool calls (including a
deliberately invalid one, to prove errors come back clean).

Run it from the project root:

    uv run docs/check_connection.py

Exit code 0 means the server is healthy and ready to connect to Claude Desktop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = PROJECT_ROOT / "server.py"

EXPECTED_TOOLS = {
    "research_product",
    "analyze_product_demand",
    "analyze_competition",
    "calculate_profitability",
    "search_suppliers",
    "analyze_reviews",
    "research_keywords",
    "generate_listing",
}


def payload(result: Any) -> dict[str, Any]:
    """Extract the JSON payload from a tool result, whichever form it arrives in."""
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def main() -> int:
    if not SERVER_PATH.is_file():
        print(f"FAIL  server.py not found at {SERVER_PATH}")
        return 1

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        env={**os.environ, "DEMO_MODE": "true", "PERSIST_RESEARCH": "false"},
    )

    print(f"Starting server: {sys.executable} {SERVER_PATH}\n")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"PASS  handshake       server '{init.server_info.name}', protocol {init.protocol_version}")

            tools = {tool.name for tool in (await session.list_tools()).tools}
            missing = EXPECTED_TOOLS - tools
            if missing:
                print(f"FAIL  tool listing    missing tools: {', '.join(sorted(missing))}")
                return 1
            print(f"PASS  tool listing    all {len(EXPECTED_TOOLS)} tools registered")

            profit = payload(
                await session.call_tool(
                    "calculate_profitability",
                    {"selling_price": 399, "product_cost": 120, "packaging_cost": 15, "weight_grams": 250},
                )
            )
            if not profit.get("ok"):
                print(f"FAIL  profit call     {profit.get('error')}")
                return 1
            print(
                "PASS  profit call     "
                f"profit Rs.{profit['estimated_profit_per_order']}, "
                f"margin {profit['profit_margin_percent']}%, "
                f"data_type {profit['data_type']}"
            )

            research = payload(
                await session.call_tool("research_product", {"product_name": "silicone sink strainer"})
            )
            if not research.get("ok"):
                print(f"FAIL  research call   {research.get('error')}")
                return 1
            print(
                "PASS  research call   "
                f"score {research['overall_opportunity_score']}/100 "
                f"({research['recommendation']}), data_type {research['data_type']}"
            )

            bad = payload(
                await session.call_tool("calculate_profitability", {"selling_price": -1, "product_cost": 10})
            )
            if bad.get("ok") is not False or bad.get("error", {}).get("code") != "invalid_input":
                print(f"FAIL  error handling  expected a clean invalid_input error, got: {bad}")
                return 1
            print(f"PASS  error handling  invalid input rejected cleanly ({bad['error']['code']})")

    print("\nAll checks passed. The MCP server is ready for Claude Desktop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
