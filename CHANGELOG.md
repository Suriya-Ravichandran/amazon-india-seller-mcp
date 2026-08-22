# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-22

### Changed

- **The project is now an installable distribution.** Install with
  `uvx amazon-india-seller-mcp`, `uv tool install amazon-india-seller-mcp` or
  `pip install amazon-india-seller-mcp` — no clone, and no absolute paths in the
  Claude Desktop config.
- All code moved into the `amazon_india_seller_mcp` package. The previous
  top-level `config`, `database`, `services` and `tools` modules would have
  collided with other distributions in `site-packages`.
- Added an `amazon-india-seller-mcp` console script and a
  `python -m amazon_india_seller_mcp` entry point.

### Added

- A trusted-publishing workflow for PyPI (OIDC, no API token stored in the repo)
  that runs the full suite, checks the distribution metadata, and verifies the
  wheel installs and registers all 24 tools in a clean environment before upload.

### Compatibility

- `python server.py` still works. The root `server.py` is now a shim, so existing
  Claude Desktop configurations that point at it by path need no changes.

## [0.1.0] - 2026-08-22

First public release. An MCP server that turns Claude Desktop into a product research
assistant for beginner Amazon India sellers — 24 tools, working with zero API keys.

### Added

**Core research (8 tools)**

- `research_product` — full opportunity report with a weighted 0–100 score
- `analyze_product_demand` — demand level, trend, seasonality and a launch decision
- `analyze_competition` — competition level, review barrier, brand dominance, gaps
- `calculate_profitability` — referral, closing, fulfilment and GST fees, return
  reserve, margin, ROI, break-even and recommended price
- `search_suppliers` — sourcing research for Parrys, Chennai, Tamil Nadu and India
- `analyze_reviews` — complaints clustered by theme with supplier-level fixes
- `research_keywords` — primary, secondary, long-tail and backend search terms
- `generate_listing` — title, bullets, description, image brief and compliance checks

**Sales and competitor intelligence (6 tools)**

- `calculate_revenue` — revenue from units, BSR curves or purchase badges, as a range
- `analyze_competitors` — per-competitor units, revenue and market share; flags new
  sellers by review count and who clears 300+ units a month
- `analyze_purchase_signals` — aggregates Amazon's own "bought in past month" badges
- `analyze_review_metrics` — the review barrier and which listings are beatable
- `analyze_evergreen` — evergreen vs seasonal scoring from up to 5 years of interest
- `analyze_product_images` — gallery coverage and a seven-slot image plan

**Planning (2 tools)**

- `find_product_opportunities` — screen and rank up to 15 ideas at once
- `plan_product_launch` — order quantity, budget split, reorder point, payback, timeline

**Amazon Ads (3 tools)**

- `suggest_ppc_keywords` — keywords with match type, bid and campaign placement
- `calculate_ppc_bids` — break-even ACOS and CPC, bid ladder, per-order economics
- `plan_ppc_campaign` — three-campaign structure, budget split, weekly routine

**Live data (5 tools)**

- `search_web` — DuckDuckGo (free, no key), Brave, Serper, Tavily, Google CSE
- `scrape_amazon_search` — live search results with purchase badges
- `scrape_amazon_product` — product page with BSR, weight and sales estimate
- `scrape_listing_details` — full listing teardown, graded 0–100
- `scraper_status` — what the live-data layer is configured to do

**Free live data sources, no API keys**

- Google Trends via pytrends for real India search interest and seasonality
- DuckDuckGo web search
- Public amazon.in pages through a guardrailed scraping layer

**Data integrity**

- Every meaningful output carries `source`, `data_type`, `confidence` and
  `last_updated`, where `data_type` is Live, Verified, Estimated, Historical or Demo
- Unparseable values return `null`, never `0` or a guess
- Modelled figures return a range and name the method that produced them
- Supplier names, prices and MOQs are never fabricated
- Demo mode is deterministic and always labelled

**Security**

- SSRF protection: scheme, port and resolved IP validated; every redirect hop
  revalidated; loopback, private, link-local and cloud metadata ranges refused
- Credential redaction filter covering library logging such as httpx request URLs
- Prompt-injection scanning and sanitisation of all scraped and searched content,
  surfaced through a `content_safety` block
- 8 MB response cap, 5-hop redirect limit, validated config file paths

**Scraping guardrails**

- Domain allowlist, robots.txt enforcement, per-host crawl delay and page budget
- Bot challenges are detected and stop the run; bypass is deliberately not implemented
- Per-field parse coverage reporting, and selector overrides via configuration

**Project**

- MIT licence, contribution guide, code of conduct and security policy
- CI on Python 3.11, 3.12 and 3.13, plus a security job
- Documentation: setup guide, scraping guide and a 56-prompt library

### Known limitations

- BSR-to-units curves and PPC conversion benchmarks are reasoned approximations, not
  calibrated against real sales data. They are labelled `Estimated` and return ranges.
- The bundled Amazon fee schedule is approximate. Point `AMAZON_FEE_CONFIG_PATH` at
  your Seller Central rate card for accurate profit figures.
- GST is applied to Amazon's fees but not to the sale price. Since Amazon India prices
  are GST-inclusive, reported margins are optimistic for GST-registered sellers. This
  is the first thing being fixed in 0.2.0.
- Amazon serves bot challenges intermittently, so direct scraping is opportunistic.
  Google Trends and DuckDuckGo are the dependable free sources.
- SP-API and Product Advertising API providers are routed but not implemented; they
  raise a clear error rather than returning fabricated data.

[0.2.0]: https://github.com/Suriya-Ravichandran/amazon-india-seller-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/Suriya-Ravichandran/amazon-india-seller-mcp/releases/tag/v0.1.0
