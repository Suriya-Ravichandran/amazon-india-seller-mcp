"""Test suite for the Amazon India Product Research MCP.

Environment defaults are set here, before any settings object is built, so the
suite always runs in demo mode and never writes to the developer's research
history database.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, TypeVar

os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("PERSIST_RESEARCH", "false")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# The suite must not depend on the network: live Google Trends, web search and
# page fetching are all forced off so results stay deterministic.
os.environ["GOOGLE_TRENDS_ENABLED"] = "false"
os.environ["WEB_SEARCH_PROVIDER"] = "demo"
os.environ["BROWSER_ENABLED"] = "false"
os.environ["PRODUCT_DATA_PROVIDER"] = "demo"

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    """Run a coroutine from a synchronous test."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def build_services() -> Any:
    """A fresh service bundle bound to the test settings."""
    from amazon_india_seller_mcp.tools import ServiceBundle

    return ServiceBundle.create()
