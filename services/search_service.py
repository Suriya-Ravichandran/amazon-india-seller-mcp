"""Web search, defaulting to a provider that needs no API key.

Providers, in the order most sellers will want them:

* ``duckduckgo`` - **free, no API key**, via the ``ddgs`` package (install with
  ``uv sync --extra realtime``). The default for real results at zero cost.
* ``brave`` - free tier, API key required, higher quality and rate limits
* ``serper`` / ``tavily`` / ``google_cse`` - free credits, API key required
* ``demo`` - deterministic offline results, labelled ``Demo``

Search is genuinely useful for product research: finding competitor brands,
supplier pages, price comparisons, and whether a product idea is being discussed
anywhere outside Amazon.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from config.settings import Settings, get_settings
from services import (
    Confidence,
    DataEnvelope,
    DataType,
    InvalidInputError,
    ProviderNotConfiguredError,
    RateLimitError,
    ServiceError,
    TTLCache,
    deterministic_rng,
)

logger = logging.getLogger(__name__)

KEYLESS_PROVIDERS = {"duckduckgo", "ddg", "demo"}


class SearchResult(BaseModel):
    """One web search hit."""

    title: str
    url: str
    snippet: str | None = None
    source: str | None = None
    position: int | None = None


class SearchService:
    """Web search across free and paid providers behind one interface."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)

    @property
    def provider(self) -> str:
        return self.settings.web_search_provider or "demo"

    def status(self) -> dict[str, Any]:
        """Which provider is active and whether it is usable right now."""
        provider = self.provider
        return {
            "provider": provider,
            "requires_api_key": provider not in KEYLESS_PROVIDERS,
            "api_key_configured": bool(self.settings.web_search_api_key),
            "ddgs_installed": _ddgs_available(),
            "ready": provider in KEYLESS_PROVIDERS
            and (provider == "demo" or _ddgs_available())
            or bool(self.settings.web_search_api_key),
        }

    async def search(self, query: str, max_results: int = 10, region: str = "in-en") -> dict[str, Any]:
        """Run a web search and return normalised results."""
        query = (query or "").strip()
        if len(query) < 2:
            raise InvalidInputError("Invalid query: provide at least 2 characters.")
        if not 1 <= max_results <= 50:
            raise InvalidInputError("max_results must be between 1 and 50.")

        cache_key = f"search::{self.provider}::{query}::{max_results}::{region}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        provider = self.provider
        if provider in {"duckduckgo", "ddg"}:
            results, envelope = await self._search_duckduckgo(query, max_results, region)
        elif provider == "brave":
            results, envelope = await self._search_brave(query, max_results)
        elif provider == "serper":
            results, envelope = await self._search_serper(query, max_results)
        elif provider == "tavily":
            results, envelope = await self._search_tavily(query, max_results)
        elif provider == "google_cse":
            results, envelope = await self._search_google_cse(query, max_results)
        else:
            results, envelope = self._search_demo(query, max_results)

        payload = {
            "query": query,
            "provider": provider,
            "results_count": len(results),
            "results": [result.model_dump() for result in results],
            **envelope.as_dict(),
        }
        self._cache.set(cache_key, payload)
        return payload

    # -- providers -------------------------------------------------------- #
    async def _search_duckduckgo(
        self, query: str, max_results: int, region: str
    ) -> tuple[list[SearchResult], DataEnvelope]:
        """Free, keyless search via the ddgs package."""
        try:
            from ddgs import DDGS  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "The DuckDuckGo search backend is not installed.",
                remediation="Install it with `uv sync --extra realtime`, or set WEB_SEARCH_PROVIDER=demo.",
            ) from exc

        import asyncio  # noqa: PLC0415

        def _run() -> list[dict[str, Any]]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, region=region, max_results=max_results))

        try:
            raw = await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001 - the library raises a wide range of errors
            message = str(exc).lower()
            if "ratelimit" in message or "202" in message or "429" in message:
                raise RateLimitError(
                    "DuckDuckGo is rate limiting this client. Wait a minute and retry, or use a keyed provider."
                ) from exc
            logger.exception("DuckDuckGo search failed")
            raise ServiceError(f"Web search failed: {type(exc).__name__}") from exc

        results = [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("href") or item.get("url") or "",
                snippet=item.get("body") or item.get("description"),
                source="duckduckgo",
                position=index + 1,
            )
            for index, item in enumerate(raw)
        ]
        return results, DataEnvelope(
            source="DuckDuckGo (free, no API key)",
            data_type=DataType.LIVE,
            confidence=Confidence.MEDIUM,
            notes="Live web results. Ranking is DuckDuckGo's and is not Amazon search-volume data.",
        )

    async def _search_brave(self, query: str, max_results: int) -> tuple[list[SearchResult], DataEnvelope]:
        key = self._require_key("Brave Search", "https://api.search.brave.com/app/keys")
        payload = await self._get_json(
            self.settings.web_search_base_url or "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max_results, 20), "country": "IN"},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        items = (payload.get("web") or {}).get("results") or []
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description"),
                source="brave",
                position=index + 1,
            )
            for index, item in enumerate(items[:max_results])
        ]
        return results, _live_envelope("Brave Search API")

    async def _search_serper(self, query: str, max_results: int) -> tuple[list[SearchResult], DataEnvelope]:
        key = self._require_key("Serper", "https://serper.dev")
        payload = await self._post_json(
            self.settings.web_search_base_url or "https://google.serper.dev/search",
            json_body={"q": query, "gl": "in", "num": min(max_results, 20)},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet"),
                source="serper",
                position=index + 1,
            )
            for index, item in enumerate((payload.get("organic") or [])[:max_results])
        ]
        return results, _live_envelope("Serper (Google results)")

    async def _search_tavily(self, query: str, max_results: int) -> tuple[list[SearchResult], DataEnvelope]:
        key = self._require_key("Tavily", "https://tavily.com")
        payload = await self._post_json(
            self.settings.web_search_base_url or "https://api.tavily.com/search",
            json_body={"api_key": key, "query": query, "max_results": max_results},
            headers={"Content-Type": "application/json"},
        )
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content"),
                source="tavily",
                position=index + 1,
            )
            for index, item in enumerate((payload.get("results") or [])[:max_results])
        ]
        return results, _live_envelope("Tavily Search API")

    async def _search_google_cse(self, query: str, max_results: int) -> tuple[list[SearchResult], DataEnvelope]:
        key = self._require_key("Google Programmable Search", "https://programmablesearchengine.google.com")
        if not self.settings.web_search_cx:
            raise ProviderNotConfiguredError(
                "Google Programmable Search needs a search engine id.",
                remediation="Set WEB_SEARCH_CX to your Programmable Search engine id.",
            )
        payload = await self._get_json(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": key, "cx": self.settings.web_search_cx, "q": query, "num": min(max_results, 10), "gl": "in"},
            headers={"Accept": "application/json"},
        )
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet"),
                source="google_cse",
                position=index + 1,
            )
            for index, item in enumerate((payload.get("items") or [])[:max_results])
        ]
        return results, _live_envelope("Google Programmable Search")

    def _search_demo(self, query: str, max_results: int) -> tuple[list[SearchResult], DataEnvelope]:
        """Deterministic offline results so the tool is testable without network access."""
        rng = deterministic_rng("websearch", query)
        templates = [
            ("{q} - Buy Online at Best Prices in India", "https://www.example-marketplace.in/search?q={q}"),
            ("{q} Manufacturers & Suppliers in India", "https://www.example-b2b.in/{q}"),
            ("Best {q} in India 2026 - Buying Guide", "https://www.example-blog.in/best-{q}"),
            ("{q} Wholesale Price List", "https://www.example-wholesale.in/{q}"),
            ("How to choose a {q}", "https://www.example-guide.in/{q}-guide"),
        ]
        slug = query.replace(" ", "-").lower()
        results = [
            SearchResult(
                title=title.format(q=query.title()),
                url=url.format(q=slug),
                snippet=f"Demo search result {index + 1} for '{query}'. Not a real web page.",
                source="demo",
                position=index + 1,
            )
            for index, (title, url) in enumerate(templates[: max(1, min(max_results, len(templates)))])
        ]
        rng.shuffle(results)
        for index, result in enumerate(results):
            result.position = index + 1
        return results, DataEnvelope.demo(
            "Local Demo Provider", "Demo search results. The URLs are placeholders and resolve to nothing."
        )

    # -- http helpers ----------------------------------------------------- #
    def _require_key(self, provider_name: str, signup_url: str) -> str:
        if not self.settings.web_search_api_key:
            raise ProviderNotConfiguredError(
                f"{provider_name} needs an API key.",
                remediation=(
                    f"Set WEB_SEARCH_API_KEY (free tier at {signup_url}), or use the keyless default "
                    "WEB_SEARCH_PROVIDER=duckduckgo."
                ),
            )
        return self.settings.web_search_api_key

    async def _get_json(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceError(f"Web search request failed: {type(exc).__name__}") from exc
        return _json_or_error(response)

    async def _post_json(self, url: str, json_body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                response = await client.post(url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceError(f"Web search request failed: {type(exc).__name__}") from exc
        return _json_or_error(response)


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 429:
        raise RateLimitError("Web search provider rate limit exceeded.")
    if response.status_code in (401, 403):
        raise ProviderNotConfiguredError("The web search provider rejected the configured API key.")
    if response.status_code >= 400:
        raise ServiceError(f"Web search provider returned HTTP {response.status_code}.")
    return response.json()


def _live_envelope(source: str) -> DataEnvelope:
    return DataEnvelope(
        source=source,
        data_type=DataType.LIVE,
        confidence=Confidence.MEDIUM,
        notes="Live web search results, not Amazon marketplace data.",
    )


def _ddgs_available() -> bool:
    try:
        import ddgs  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False
