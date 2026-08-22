"""Polite web fetching for the scraping tools.

Two fetch paths share one set of guardrails:

* ``httpx`` for plain HTML (fast, cheap)
* Playwright + Chromium for pages that need JavaScript rendering

Guardrails, all enforced before a request leaves the process:

* **Domain allowlist** - ``BROWSER_ALLOWED_DOMAINS`` must name the host. Empty
  means nothing is fetchable, so the layer is inert until switched on.
* **robots.txt** - fetched once per host, cached, and honoured. A disallowed
  path is refused, not fetched.
* **Crawl delay** - at least ``BROWSER_MIN_DELAY_SECONDS`` between requests to
  the same host, raised to the host's own ``Crawl-delay`` when it declares one.
* **Page budget** - ``BROWSER_MAX_PAGES_PER_RUN`` caps a single research run.
* **Honest identification** - a real Chromium user agent, or your own via
  ``BROWSER_USER_AGENT``.
* **SSRF protection** - scheme, port and resolved IP are checked before any
  request, and every redirect hop is revalidated, so a redirect cannot walk the
  fetch onto loopback, a private range or a cloud metadata endpoint.
* **Response size cap** - bodies are streamed with a hard byte limit.

What this module deliberately does **not** do: rotate proxies, spoof or
randomise browser fingerprints, install stealth patches, or solve CAPTCHAs.
When a site blocks or challenges the request, :class:`BlockedError` is raised so
the caller stops and the operator can switch to a licensed data API. Working
around a site's bot protection is out of scope by design.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from config.settings import Settings, get_settings
from services import Confidence, DataEnvelope, DataType, ServiceError, TTLCache
from services.security import (
    MAX_RESPONSE_BYTES,
    fetch_with_validated_redirects,
    validate_public_url,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Markers that mean "the site is challenging or blocking automated access".
BLOCK_MARKERS = (
    "enter the characters you see below",
    "type the characters you see in this image",
    "to discuss automated access to amazon data please contact",
    "sorry, we just need to make sure you're not a robot",
    "captcha",
    "access denied",
    "request blocked",
    "unusual traffic from your computer",
    # Akamai / Amazon Bot Manager interstitial: a 200 response carrying only a
    # JavaScript challenge instead of content.
    "triggerinterstitialchallenge",
    "bm-verify",
    "validatecaptcha",
    "/errors/validatecaptcha",
)

# A challenge page is small and content-free; a real page never is.
CHALLENGE_MAX_BYTES = 12_000


class BrowserDisabledError(ServiceError):
    code = "browser_disabled"
    remediation = (
        "Set BROWSER_ENABLED=true and list the hostnames you are permitted to fetch in "
        "BROWSER_ALLOWED_DOMAINS (comma separated)."
    )


class DomainNotAllowedError(ServiceError):
    code = "domain_not_allowed"
    remediation = "Add the hostname to BROWSER_ALLOWED_DOMAINS if you are permitted to fetch it."


class RobotsDisallowedError(ServiceError):
    code = "robots_disallowed"
    remediation = (
        "The site's robots.txt disallows this path. Use the site's official API instead. "
        "Set BROWSER_RESPECT_ROBOTS=false only if you have written permission from the site owner."
    )


class BlockedError(ServiceError):
    code = "blocked_by_site"
    remediation = (
        "The site served a bot challenge or rate-limit page. Stop and slow down: raise "
        "BROWSER_MIN_DELAY_SECONDS, or switch to a licensed data API (Rainforest, Keepa, "
        "Amazon SP-API). This server does not attempt to bypass bot protection."
    )


class PlaywrightMissingError(ServiceError):
    code = "playwright_not_installed"
    remediation = (
        "Install the browser extra: `uv sync --extra browser` then `uv run playwright install chromium`."
    )


class PageBudgetExceededError(ServiceError):
    code = "page_budget_exceeded"
    remediation = "Raise BROWSER_MAX_PAGES_PER_RUN, or narrow the request to fewer pages."


@dataclass
class FetchResult:
    """One fetched page plus how it was obtained."""

    url: str
    status_code: int
    html: str
    engine: str
    fetched_at: float = field(default_factory=time.time)

    @property
    def envelope(self) -> DataEnvelope:
        return DataEnvelope(
            source=f"{urlparse(self.url).netloc} (fetched via {self.engine})",
            data_type=DataType.LIVE,
            confidence=Confidence.MEDIUM,
            notes=(
                "Fetched live from the public page. Page markup changes without notice, so parsed "
                "fields can silently go missing - treat gaps as unknown rather than zero."
            ),
        )


class BrowserService:
    """Guardrailed page fetching, shared by every scraping tool."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request_at: dict[str, float] = {}
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)
        self._pages_fetched = 0
        self._lock = asyncio.Lock()

    # -- state ------------------------------------------------------------ #
    @property
    def user_agent(self) -> str:
        return self.settings.browser_user_agent or DEFAULT_USER_AGENT

    @property
    def pages_fetched(self) -> int:
        return self._pages_fetched

    def reset_budget(self) -> None:
        """Start a new research run with a fresh page budget."""
        self._pages_fetched = 0

    def status(self) -> dict[str, Any]:
        """Current configuration of the browser layer, for diagnostics."""
        return {
            "browser_enabled": self.settings.browser_enabled,
            "allowed_domains": sorted(self.settings.allowed_domains),
            "respect_robots": self.settings.browser_respect_robots,
            "min_delay_seconds": self.settings.browser_min_delay_seconds,
            "max_pages_per_run": self.settings.browser_max_pages_per_run,
            "pages_fetched_this_run": self._pages_fetched,
            "playwright_available": _playwright_available(),
            "user_agent": self.user_agent,
            "bot_protection_bypass": "not implemented by design",
            "ssrf_protection": "scheme, port and resolved IP checked; every redirect hop revalidated",
            "max_response_bytes": MAX_RESPONSE_BYTES,
        }

    # -- guardrails ------------------------------------------------------- #
    def _check_enabled(self) -> None:
        if not self.settings.browser_enabled:
            raise BrowserDisabledError("The browser/scraping layer is disabled.")

    def _check_domain(self, url: str) -> str:
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
        if not host:
            raise ServiceError(f"Invalid URL: {url}")
        allowed = self.settings.allowed_domains
        if not allowed:
            raise DomainNotAllowedError("No domains are allowlisted, so no page may be fetched.")
        if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
            raise DomainNotAllowedError(f"Domain '{host}' is not in BROWSER_ALLOWED_DOMAINS.")
        return host

    async def _robots_for(self, url: str) -> robotparser.RobotFileParser | None:
        """Fetch and cache robots.txt for the URL's host."""
        host = urlparse(url).netloc.lower()
        if host in self._robots:
            return self._robots[host]

        robots_url = urljoin(f"{urlparse(url).scheme}://{host}", "/robots.txt")
        parser: robotparser.RobotFileParser | None = None
        try:
            # robots.txt is fetched from the same host, so it needs the same
            # SSRF checks and redirect validation as any other request.
            async with httpx.AsyncClient(timeout=15.0) as client:
                response, _, body = await fetch_with_validated_redirects(
                    client,
                    robots_url,
                    headers={"User-Agent": self.user_agent},
                    domain_check=self._check_domain,
                    max_bytes=512 * 1024,
                )
                response_text = body.decode("utf-8", errors="replace")
            if response.status_code == 200:
                parser = robotparser.RobotFileParser()
                parser.parse(response_text.splitlines())
            else:
                logger.info("No robots.txt at %s (HTTP %s)", robots_url, response.status_code)
        except (httpx.HTTPError, ServiceError) as exc:
            logger.warning("Could not fetch robots.txt from %s: %s", robots_url, type(exc).__name__)

        self._robots[host] = parser
        return parser

    async def _check_robots(self, url: str) -> None:
        if not self.settings.browser_respect_robots:
            logger.warning("BROWSER_RESPECT_ROBOTS is false - robots.txt is being ignored for %s", url)
            return
        parser = await self._robots_for(url)
        if parser is None:
            return  # No robots.txt published: nothing to disallow.
        if not parser.can_fetch(self.user_agent, url) or not parser.can_fetch("*", url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")

    async def _respect_crawl_delay(self, url: str, host: str) -> None:
        """Wait out the crawl delay for this host before the next request."""
        delay = self.settings.browser_min_delay_seconds
        parser = await self._robots_for(url) if self.settings.browser_respect_robots else None
        if parser is not None:
            declared = parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
            if declared:
                delay = max(delay, float(declared))

        last = self._last_request_at.get(host)
        if last is not None:
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                logger.debug("Waiting %.1fs before the next request to %s", wait, host)
                await asyncio.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _check_budget(self) -> None:
        if self._pages_fetched >= self.settings.browser_max_pages_per_run:
            raise PageBudgetExceededError(
                f"Page budget of {self.settings.browser_max_pages_per_run} reached for this run."
            )

    @staticmethod
    def _check_not_blocked(url: str, status_code: int, html: str) -> None:
        """Detect a block or challenge and stop - never work around it."""
        if status_code in (403, 429, 503):
            raise BlockedError(f"{urlparse(url).netloc} returned HTTP {status_code} (blocked or rate limited).")
        sample = html[:8000].lower()
        for marker in BLOCK_MARKERS:
            if marker in sample:
                raise BlockedError(
                    f"{urlparse(url).netloc} served a bot challenge instead of content "
                    f"(matched '{marker}'). This server does not attempt to solve or bypass such challenges."
                )
        if len(html) < CHALLENGE_MAX_BYTES and "data-asin" not in html and "<title>" not in sample:
            raise BlockedError(
                f"{urlparse(url).netloc} returned a {len(html)} byte page with no content - "
                "almost always an anti-bot interstitial."
            )

    # -- fetching --------------------------------------------------------- #
    async def fetch(self, url: str, render: bool = False, use_cache: bool = True) -> FetchResult:
        """Fetch one page through every guardrail.

        Set ``render=True`` to drive Chromium via Playwright for pages whose
        content is built by JavaScript.
        """
        self._check_enabled()
        validate_public_url(url)          # scheme, port, and the resolved IP
        host = self._check_domain(url)    # operator allowlist

        cache_key = f"fetch::{url}::{render}"
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for %s", url)
                return cached

        await self._check_robots(url)
        self._check_budget()

        async with self._lock:
            await self._respect_crawl_delay(url, host)
            result = await (self._fetch_rendered(url) if render else self._fetch_plain(url))
            self._pages_fetched += 1

        self._check_not_blocked(url, result.status_code, result.html)
        self._cache.set(cache_key, result)
        return result

    async def _fetch_plain(self, url: str) -> FetchResult:
        """Fetch with httpx, validating every redirect hop and capping the body."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.browser_timeout_seconds) as client:
                response, final_url, body = await fetch_with_validated_redirects(
                    client,
                    url,
                    headers=headers,
                    domain_check=self._check_domain,
                    max_bytes=MAX_RESPONSE_BYTES,
                )
        except httpx.HTTPError as exc:
            raise ServiceError(f"Could not fetch {url}: {type(exc).__name__}") from exc

        return FetchResult(
            url=final_url,
            status_code=response.status_code,
            html=body.decode(response.encoding or "utf-8", errors="replace"),
            engine="httpx",
        )

    async def _fetch_rendered(self, url: str) -> FetchResult:
        """Render the page in headless Chromium via Playwright."""
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise PlaywrightMissingError("Playwright is not installed.") from exc

        timeout_ms = int(self.settings.browser_timeout_seconds * 1000)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.browser_headless)
            try:
                context = await browser.new_context(
                    user_agent=self.user_agent,
                    locale="en-IN",
                    viewport={"width": 1366, "height": 900},
                    java_script_enabled=True,
                )
                page = await context.new_page()

                # Chromium would otherwise follow redirects and load subresources
                # anywhere. Every request the page makes is checked against the
                # same allowlist and SSRF rules as a direct fetch.
                async def _gate(route: Any, request: Any) -> None:
                    try:
                        validate_public_url(request.url, resolve=False)
                        self._check_domain(request.url)
                    except ServiceError:
                        logger.debug("Blocked in-page request to %s", request.url)
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", _gate)
                response = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                html = await page.content()
                if len(html) > MAX_RESPONSE_BYTES:
                    raise ServiceError("Rendered page exceeded the response size cap.")
                status = response.status if response else 0
            finally:
                await browser.close()
        return FetchResult(url=url, status_code=status, html=html, engine="playwright-chromium")


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False
