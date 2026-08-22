"""Tests for the scraping guardrails, parsers and search service.

These never touch the network: every test either exercises a guardrail that
refuses before any request is made, or parses fixed HTML.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from services.browser_service import (
    BLOCK_MARKERS,
    BlockedError,
    BrowserDisabledError,
    BrowserService,
    DomainNotAllowedError,
    PageBudgetExceededError,
)
from services.scraper_service import (
    AmazonScraperService,
    _bought,
    _category_ranks,
    _price,
    _rating_fallback,
    _reviews_fallback,
    _weight_grams,
)
from tests import build_services, run
from tools.amazon_scraper import scrape_amazon_search, scraper_status


@pytest.fixture(scope="module")
def services():
    return build_services()


def browser(**overrides) -> BrowserService:
    settings = Settings(browser_enabled=True, browser_allowed_domains="amazon.in", cache_enabled=False, **overrides)
    return BrowserService(settings)


# -- guardrails ------------------------------------------------------------- #
def test_disabled_browser_refuses_every_fetch():
    service = BrowserService(Settings(browser_enabled=False))
    with pytest.raises(BrowserDisabledError):
        run(service.fetch("https://www.amazon.in/s?k=test"))


def test_empty_allowlist_blocks_everything():
    service = BrowserService(Settings(browser_enabled=True, browser_allowed_domains=""))
    with pytest.raises(DomainNotAllowedError):
        run(service.fetch("https://www.amazon.in/s?k=test"))


def test_domain_outside_the_allowlist_is_refused():
    with pytest.raises(DomainNotAllowedError):
        run(browser().fetch("https://example.com/anything"))


def test_subdomains_of_an_allowed_domain_are_permitted():
    service = browser()
    assert service._check_domain("https://www.amazon.in/dp/B0TEST1234") == "amazon.in"
    with pytest.raises(DomainNotAllowedError):
        service._check_domain("https://amazon.in.evil.example/dp/x")


def test_page_budget_is_enforced():
    service = browser(browser_max_pages_per_run=2)
    service._pages_fetched = 2
    with pytest.raises(PageBudgetExceededError):
        service._check_budget()
    service.reset_budget()
    service._check_budget()  # must not raise after a reset


@pytest.mark.parametrize("status", [403, 429, 503])
def test_block_status_codes_stop_the_run(status):
    with pytest.raises(BlockedError):
        BrowserService._check_not_blocked("https://www.amazon.in/s", status, "<html>ok</html>")


@pytest.mark.parametrize("marker", ["captcha", "triggerinterstitialchallenge", "bm-verify"])
def test_bot_challenge_pages_are_detected_not_bypassed(marker):
    html = "<html><body>" + marker + " " + "x" * 20_000 + "</body></html>"
    with pytest.raises(BlockedError) as excinfo:
        BrowserService._check_not_blocked("https://www.amazon.in/s", 200, html)
    assert "does not attempt" in str(excinfo.value) or "bot challenge" in str(excinfo.value)


def test_tiny_contentless_response_is_treated_as_a_challenge():
    with pytest.raises(BlockedError):
        BrowserService._check_not_blocked("https://www.amazon.in/s", 200, "<html><script>var i=1;</script></html>")


def test_normal_page_passes_the_block_check():
    html = "<html><title>Results</title><body>" + "<div data-asin='B0TEST1234'>item</div>" * 50 + "</body></html>"
    BrowserService._check_not_blocked("https://www.amazon.in/s", 200, html)  # must not raise


def test_block_markers_cover_the_known_challenge_types():
    assert "captcha" in BLOCK_MARKERS
    assert "bm-verify" in BLOCK_MARKERS


def test_status_reports_that_bypass_is_not_implemented():
    assert browser().status()["bot_protection_bypass"] == "not implemented by design"


# -- url building ----------------------------------------------------------- #
def test_urls_are_built_for_robots_permitted_paths():
    assert AmazonScraperService.search_url("sink strainer") == "https://www.amazon.in/s?k=sink+strainer"
    assert AmazonScraperService.product_url("B0TEST1234") == "https://www.amazon.in/dp/B0TEST1234"
    assert "product-reviews/B0TEST1234" in AmazonScraperService.reviews_url("B0TEST1234")


@pytest.mark.parametrize("asin", ["short", "toolongasin123", "b0test1234!"])
def test_invalid_asins_are_rejected(asin):
    with pytest.raises(Exception):
        AmazonScraperService.product_url(asin)


# -- parsing helpers -------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500+ bought in past month", 500),
        ("1K+ bought in past month", 1_000),
        ("2K+ bought in past month", 2_000),
        ("10,000+ bought in past month", 10_000),
        ("no badge here", None),
        (None, None),
    ],
)
def test_bought_badge_parsing(text, expected):
    assert _bought(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("₹1,299.00", 1299.0), ("Rs. 249", 249.0), ("399", 399.0), ("", None), (None, None)],
)
def test_price_parsing(text, expected):
    assert _price(text) == expected


def test_rating_fallback_reads_a_popover_blob():
    html = '<div data-a-popover="{&quot;popoverLabel&quot;:&quot;4.2 out of 5 stars, rating details&quot;}"></div>'
    assert _rating_fallback(html.replace("&quot;", '"')) == 4.2
    assert _rating_fallback("<div>no rating</div>") is None


def test_rating_fallback_rejects_impossible_values():
    assert _rating_fallback("<span>9.9 out of 5 stars</span>") is None


def test_review_count_fallback():
    assert _reviews_fallback('<a aria-label="1,234 ratings"></a>') == 1234
    assert _reviews_fallback("<div>nothing</div>") is None


def test_category_rank_parsing():
    ranks = _category_ranks("Best Sellers Rank: #1,234 in Home & Kitchen (See Top 100) #56 in Sink Strainers")
    assert ranks[0] == {"rank": 1234, "category": "Home & Kitchen"}
    assert any(row["rank"] == 56 for row in ranks)


def test_weight_parsing_normalises_to_grams():
    assert _weight_grams("Item Weight: 250 g") == 250.0
    assert _weight_grams("Item Weight : 1.2 kg") == 1200.0
    assert _weight_grams("no weight listed") is None


# -- tool level ------------------------------------------------------------- #
def test_scrape_tool_returns_a_clean_error_when_disabled(services):
    report = run(scrape_amazon_search(services, "sink strainer"))
    assert report["ok"] is False
    assert report["error"]["code"] in {"browser_disabled", "domain_not_allowed"}
    assert report["error"]["remediation"]
    assert "Traceback" not in report["error"]["message"]


def test_scraper_status_lists_blockers(services):
    report = run(scraper_status(services))
    assert report["ok"] is True
    assert report["ready_to_scrape"] is False
    assert report["blockers"]
    assert report["compliance"]["bot_protection_bypass"] == "not implemented by design"
    assert report["compliance"]["robots_txt"] == "enforced"


# -- search service --------------------------------------------------------- #
def test_demo_search_is_labelled_and_deterministic(services):
    from tools.web_search import search_web

    first = run(search_web(services, "silicone sink strainer", max_results=3))
    second = run(search_web(build_services(), "silicone sink strainer", max_results=3))
    assert first["ok"] is True
    assert first["data_type"] == "Demo"
    assert [row["url"] for row in first["results"]] == [row["url"] for row in second["results"]]


def test_search_rejects_bad_input(services):
    from tools.web_search import search_web

    assert run(search_web(services, "x"))["ok"] is False
    assert run(search_web(services, "valid query", max_results=999))["ok"] is False
