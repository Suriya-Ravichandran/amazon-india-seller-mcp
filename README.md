# Amazon India Product Research MCP

An MCP (Model Context Protocol) server that turns Claude Desktop into a product research
assistant for **beginner Amazon India sellers**. It scores product opportunities, estimates
demand, sizes up competition, calculates real Amazon India profitability, plans sourcing,
mines customer complaints, researches keywords and drafts a full listing.

Runs over **stdio**, so it plugs straight into Claude Desktop.

> **New here? Start with the [Setup & Run Guide](docs/SETUP.md)** — step-by-step installation,
> verification and Claude Desktop configuration, with a troubleshooting section.
> For live data, see the [Live Data & Scraping Guide](docs/SCRAPING.md).

**20 tools. Works with zero API keys** — in demo mode offline, or on real live data
from free sources (Google Trends, DuckDuckGo, public amazon.in pages).

---

## Project Overview

The server is built around one seller profile:

| Criterion | Target |
|---|---|
| Investment | ₹5,000 – ₹20,000 |
| Selling price | ₹199 – ₹699 |
| Weight | under 500 g |
| Profit margin | 30% minimum |
| Demand | daily use, non-seasonal |
| Returns | low return rate |
| Sourcing | easy Indian sourcing |
| Risk | no obvious gating or brand-approval problems |

Every tool scores products against these criteria and penalises the things that sink new
sellers: branded goods, counterfeit risk, fragile items, batteries, complex electronics,
perishables, seasonal products, apparel sizing, heavy items and categories dominated by
strong brands.

### Data integrity comes first

This project refuses to make up marketplace facts. Every meaningful output carries
`source`, `data_type`, `confidence` and `last_updated`, where `data_type` is one of
**Live**, **Verified**, **Estimated**, **Historical** or **Demo**.

- Demo data is always labelled `Demo` and never presented as live Amazon data.
- Demand and monthly sales figures are **modelled estimates**, never measured Amazon sales.
- Amazon fees come from a configurable schedule; the bundled one is labelled `Estimated`.
- **Suppliers are never invented.** Without a supplier API, `search_suppliers` returns an
  empty supplier list plus real, publicly known sourcing channels you can verify yourself.
- No tool ever claims guaranteed profit or guaranteed sales.

---

## Features

- **20 MCP tools** covering the full seller workflow: discovery, demand, competition,
  money, listing, sourcing and live data
- **Free live data, no API keys**: Google Trends search interest, DuckDuckGo web
  search, and public amazon.in pages including "bought in past month" badges
- **Revenue and sales estimation** from BSR curves or Amazon's own purchase badges,
  always as a range with the method stated
- **New-seller detection**: which competitors have low review counts, and which of
  those are already clearing 300+ units/month — the strongest signal a page is winnable
- **Evergreen scoring** from up to 5 years of real search interest, so you avoid
  seasonal dead stock
- 0–100 weighted opportunity scoring, plus batch screening of up to 15 ideas at once
- Amazon India fee maths: referral, closing, FBA / Easy Ship / Self Ship, GST on fees,
  return reserve, break-even and recommended price
- Launch planning: order quantity, budget split, ad budget, reorder point, payback
- Review complaint clustering with concrete supplier-level fixes
- Keyword research, listing draft and a seven-slot image plan
- Compliance-first scraping: robots.txt, allowlist, crawl delay, page budget, and a
  hard stop on bot challenges — **no bot-protection bypass**
- Research history stored in SQLite or PostgreSQL
- Full demo mode: everything works offline, deterministically

---

## Architecture

```text
Amazon-India-Product-Research-MCP/
│
├── server.py                     # MCP entry point (stdio transport) - wiring only
│
├── tools/                        # MCP tool definitions - thin: validate, call service, shape result
│   ├── __init__.py               # ServiceBundle + error-handling decorator
│   ├── product_research.py       # research_product
│   ├── demand_analysis.py        # analyze_product_demand
│   ├── competition.py            # analyze_competition
│   ├── profit_calculator.py      # calculate_profitability
│   ├── supplier_search.py        # search_suppliers
│   ├── review_analysis.py        # analyze_reviews
│   ├── keyword_research.py       # research_keywords
│   ├── listing_generator.py      # generate_listing
│   ├── revenue_calculator.py     # calculate_revenue
│   ├── competitor_analysis.py    # analyze_competitors
│   ├── purchase_signals.py       # analyze_purchase_signals
│   ├── review_metrics.py         # analyze_review_metrics
│   ├── evergreen_analysis.py     # analyze_evergreen
│   ├── product_images.py         # analyze_product_images
│   ├── opportunity_finder.py     # find_product_opportunities
│   ├── launch_planner.py         # plan_product_launch
│   ├── web_search.py             # search_web
│   └── amazon_scraper.py         # scrape_amazon_search / scrape_amazon_product / scraper_status
│
├── services/                     # All business logic
│   ├── __init__.py               # Data envelopes, errors, cache, opportunity scoring
│   ├── amazon_service.py         # Provider abstraction (demo / scraper / API), snapshots, risk, reviews, listings
│   ├── trends_service.py         # Demand, trend direction, seasonality, keywords, live Google Trends
│   ├── supplier_service.py       # Sourcing research (never fabricates suppliers)
│   ├── pricing_service.py        # Fees, profit, margin, ROI, break-even, recommended price
│   ├── revenue_service.py        # Units from BSR/badges, revenue, competitor stage, evergreen scoring
│   ├── search_service.py         # Web search (DuckDuckGo free, Brave/Serper/Tavily/Google CSE)
│   ├── browser_service.py        # Guardrailed fetching: allowlist, robots.txt, delay, budget, block detection
│   └── scraper_service.py        # Amazon India page parsing (search, product, reviews, bestsellers)
│
├── database/                     # Research history
│   ├── __init__.py
│   └── models.py                 # SQLAlchemy models + session handling
│
├── config/                       # Centralised settings
│   ├── __init__.py
│   └── settings.py               # Env-driven settings + configurable fee schedule
│
├── tests/
│   ├── __init__.py
│   ├── test_product_research.py
│   ├── test_demand_analysis.py
│   ├── test_competition.py
│   └── test_profit_calculator.py
│
├── docs/
│   ├── SETUP.md                  # full setup, run and troubleshooting guide
│   ├── SCRAPING.md               # live data sources, guardrails and compliance
│   ├── PROMPTS.md                # copy-paste prompt library for all 20 tools
│   └── check_connection.py       # MCP connection self-test
│
├── .env.example
├── mcp.json.example
├── pyproject.toml                # Dependencies, managed by uv
└── uv.lock
```

Rules the code follows: `server.py` holds no business logic, tools hold no business logic,
services hold all of it.

---

## Installation

Quick version below; the [Setup & Run Guide](docs/SETUP.md) covers every step in detail.

**Requirements:** Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-repo-url>
cd Amazon-India-Product-Research-MCP
uv sync
```

`uv sync` creates `.venv/` and installs the locked dependency set. That is the whole setup.

<details>
<summary>Installing uv</summary>

```bash
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# or via pip
python -m pip install uv
```
</details>

### Virtual environment

`uv sync` manages the virtual environment for you; run commands with `uv run`. If you
prefer to activate it manually:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### Dependencies

Declared in `pyproject.toml` and pinned in `uv.lock`:

`mcp`, `pydantic`, `pydantic-settings`, `httpx`, `sqlalchemy`, `python-dotenv`; `pytest` in
the dev group.

Add or change a dependency with `uv add <package>` / `uv remove <package>` — never edit the
lockfile by hand.

---

## Environment Configuration

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Environment label |
| `DEBUG` | `false` | Verbose logging |
| `DATABASE_URL` | `sqlite:///./amazon_product_mcp.db` | SQLite or PostgreSQL URL |
| `PERSIST_RESEARCH` | `true` | Store research history |
| `AMAZON_API_KEY` / `AMAZON_API_SECRET` | empty | SP-API / PA-API credentials |
| `PRODUCT_DATA_PROVIDER` | `demo` | `demo`, `sp-api`, `pa-api` or a third-party API name |
| `PRODUCT_DATA_API_KEY` / `PRODUCT_DATA_BASE_URL` | empty | Third-party provider access |
| `GOOGLE_TRENDS_ENABLED` | `false` | Enable a trends provider (none ships with the project) |
| `SUPPLIER_API_KEY` / `SUPPLIER_API_BASE_URL` | empty | Supplier data provider |
| `DEMO_MODE` | `true` | Deterministic demo data, clearly labelled |
| `CACHE_ENABLED` / `CACHE_TTL_SECONDS` | `true` / `900` | In-process caching |
| `AMAZON_FEE_CONFIG_PATH` | empty | JSON file with your real Seller Central rate card |

Secrets live only in `.env`, which is gitignored. Nothing is hardcoded in the source.

---

## Database Setup

Tables are created automatically at startup. Nothing to run by hand.

**Development (default):** SQLite at `./amazon_product_mcp.db`.

**PostgreSQL:**

```bash
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/amazon_mcp
```

JSON payload columns map to `JSONB` on PostgreSQL and `JSON` on SQLite automatically.
Install the driver alongside it: `uv add psycopg[binary]`.

Stored models: `ProductResearch`, `DemandAnalysis`, `CompetitionAnalysis`,
`ProfitCalculation`, `SupplierResearch` — each keeping `product_name`, `marketplace`,
`research_data`, `data_source`, `data_type`, `confidence`, `created_at`, `updated_at`.

History storage is best-effort: if the database is unreachable, tools still work and the
failure is logged rather than surfaced.

---

## Running the MCP

```bash
uv run server.py
```

The process speaks the MCP protocol over stdio, so it will sit there silently waiting for a
client — that is correct behaviour. Logs go to stderr, keeping stdout clean for protocol
traffic. Stop it with `Ctrl+C`.

---

## Claude Desktop Configuration

1. Open the Claude Desktop config file:
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
2. Copy the `mcpServers` block from [`mcp.json.example`](mcp.json.example) into it,
   replacing the paths with your own **absolute** paths:

```json
{
  "mcpServers": {
    "amazon-product-research": {
      "command": "/ABSOLUTE/PATH/TO/Amazon-India-Product-Research-MCP/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/Amazon-India-Product-Research-MCP/server.py"],
      "env": { "DEMO_MODE": "true" }
    }
  }
}
```

On Windows use `.venv\\Scripts\\python.exe` and escape backslashes. A `uv --directory ... run
server.py` variant is also included in the example file.

3. **Fully quit and restart Claude Desktop** (close it from the tray/menu bar — reloading the
   window is not enough).
4. Test the connection: the tools appear in Claude Desktop's tool menu, and asking
   *"Calculate the profit for a ₹399 product that costs ₹120"* should trigger
   `calculate_profitability`.

---

## Available MCP Tools

### Discovery

| Tool | What it does |
|---|---|
| `find_product_opportunities` | Screen up to 15 product ideas at once against the beginner criteria and rank them. Start here. |
| `research_product` | Full opportunity report for one idea: category, price band, BSR, weight, rating, reviews, demand, competition, return / gating / brand risk, beginner fit, 0–100 score and recommendation |

### Demand

| Tool | What it does |
|---|---|
| `analyze_product_demand` | Monthly demand, demand level, trend direction, seasonality, confidence and a launch decision |
| `analyze_evergreen` | Evergreen score 0–100 from up to 5 years of real search interest: stability, flatness, demand floor, growth, plus inventory guidance |
| `analyze_purchase_signals` | Aggregates Amazon's own "X bought in past month" badges — the most reliable free sales signal there is |

### Competition

| Tool | What it does |
|---|---|
| `analyze_competition` | Competition level, price and rating averages, review barrier, brand dominance, listing and image quality, weak listings and differentiation openings |
| `analyze_competitors` | Per-competitor units, revenue, market share, market size and concentration. Flags **new sellers** (low reviews) and who clears **300+ units/month**, then gives an entry verdict |
| `analyze_review_metrics` | The review barrier: median and quartile review counts, months to catch up, and which listings are beatable |

### Money

| Tool | What it does |
|---|---|
| `calculate_profitability` | Referral, closing, fulfilment and GST fees, return reserve, total cost, profit, margin, ROI, break-even and recommended price, plus a plain-English explanation |
| `calculate_revenue` | Monthly and annual revenue from units, BSR or a purchase badge — as a range, with the method stated. Add `product_cost` for profit |
| `plan_product_launch` | Order quantity, budget split (inventory / samples / photography / ads / buffer), days of cover, reorder point, affordable ad cost, payback, week-by-week timeline and warnings |

### Listing

| Tool | What it does |
|---|---|
| `research_keywords` | Primary, secondary, long-tail and related keywords, search intent, priority, backend search terms and placement guidance |
| `generate_listing` | SEO title and alternatives, five bullets, description, backend terms, image direction, packaging advice and a compliance checklist |
| `analyze_product_images` | Competitor gallery coverage, thin galleries you can beat, Amazon's image requirements and a seven-slot image plan |
| `analyze_reviews` | Complaints grouped by theme with mention counts and concrete product fixes, plus appreciated features and differentiation angles |

### Sourcing

| Tool | What it does |
|---|---|
| `search_suppliers` | Sourcing research for Parrys / Chennai / Tamil Nadu / India with verification status and a vetting checklist. Never invents suppliers |

### Live data

| Tool | What it does |
|---|---|
| `search_web` | Web search via DuckDuckGo (free, no key) or Brave / Serper / Tavily / Google CSE |
| `scrape_amazon_search` | Live amazon.in search results: ASIN, price, rating, review count, purchase badge, sponsored flag |
| `scrape_amazon_product` | Live product page: BSR, weight, seller, bullets, full image gallery, plus a sales estimate |
| `scraper_status` | What the live-data layer is configured to do, and anything blocking it |

### Opportunity scoring

| Component | Weight |
|---|---|
| Demand | 25% |
| Profitability | 25% |
| Competition | 20% |
| Return risk | 10% |
| Sourcing ease | 10% |
| Beginner friendliness | 10% |

| Score | Recommendation |
|---|---|
| 80–100 | Strong Opportunity |
| 65–79 | Good Opportunity |
| 50–64 | Moderate Opportunity |
| 30–49 | High Risk |
| 0–29 | Avoid |

---

## Example Prompts

```text
Find beginner-friendly Amazon India products under ₹20,000 investment.

Screen these ideas and rank them: sink strainer, cable organizer, spice rack.

Analyze the demand for silicone sink strainers on Amazon India.

Is a silicone sink strainer an evergreen product or seasonal?

How many units are competitors selling for "cable organizer"?

Are any new sellers succeeding in the kitchen drawer organizer market?

What revenue would a ₹399 product at BSR 3,500 make per month?

Calculate the profit for a ₹399 product that costs ₹120.

Plan a ₹20,000 launch for a ₹399 sink strainer that costs ₹120.

Find suppliers for cable organizers in Chennai or Tamil Nadu.

Find customer complaints about manual soap dispensers.

Generate an Amazon India listing for a reusable silicone food storage bag.

Scrape live Amazon India results for "silicone sink strainer".

Check the scraper status.
```

A natural workflow: *screen ideas → check demand and evergreen → check competitors and
new sellers → calculate profit → plan the launch → research keywords → generate the listing.*

**[docs/PROMPTS.md](docs/PROMPTS.md) is the full prompt library** — 56 copy-paste prompts
grouped by task, chained multi-tool workflows, and prompts that make Claude show which
numbers are live versus estimated.

---

## Demo Mode

With `DEMO_MODE=true` (the default) every tool works without a single paid API key.

- Sample data is **deterministic** — the same query always returns the same numbers, so
  results are reproducible and testable.
- Every value is labelled `"data_type": "Demo"`, `"confidence": "Low"`,
  `"source": "Local Demo Provider"`.
- The server logs a warning on startup so nobody forgets which mode they are in.

Demo mode is for learning the workflow and testing the integration. **Never make a purchase
decision on demo numbers.**

---

## Live Data on Free Sources (no API keys)

Everything below is free and needs no API key:

```bash
uv sync --extra realtime --extra browser
uv run playwright install chromium     # only for render=true
```

```ini
APP_ENV=production
DEMO_MODE=false
PRODUCT_DATA_PROVIDER=scraper
GOOGLE_TRENDS_ENABLED=true
WEB_SEARCH_PROVIDER=duckduckgo
BROWSER_ENABLED=true
BROWSER_ALLOWED_DOMAINS=amazon.in
BROWSER_MIN_DELAY_SECONDS=8
```

| Source | Gives you | Reliability |
|---|---|---|
| Google Trends | Real India search interest, seasonality, evergreen scoring | High |
| DuckDuckGo | Live web search for competitors, suppliers, prices | High |
| amazon.in pages | Prices, ASINs, ratings, review counts, purchase badges | Intermittent |

Amazon serves bot challenges to automated traffic. This server **detects and stops**
on them rather than bypassing them, so scraping works opportunistically. Read
[docs/SCRAPING.md](docs/SCRAPING.md) before enabling it — it covers robots.txt vs
Terms of Service, the guardrails, and how to fix selectors without touching code.

---

## Production API Integration

1. **Product data.** Implement a `ProductDataProvider` subclass in
   `services/amazon_service.py` (`search_listings` and `fetch_reviews`), or point
   `PRODUCT_DATA_BASE_URL` / `PRODUCT_DATA_API_KEY` at an approved third-party API and
   adapt `HttpProductDataProvider`'s payload mapping. Then set `DEMO_MODE=false`.
2. **Amazon SP-API / PA-API.** Register as a developer, obtain credentials, and add a
   provider that signs requests with `AMAZON_API_KEY` / `AMAZON_API_SECRET`.
   `build_provider()` already routes `sp-api` and `pa-api` and currently raises a clear
   "not implemented" error rather than silently faking data.
3. **Fees.** Export your Seller Central rate card to JSON matching the `FeeSchedule` model,
   point `AMAZON_FEE_CONFIG_PATH` at it, and set `data_type` to `Verified`.
4. **Suppliers.** Set `SUPPLIER_API_KEY` and `SUPPLIER_API_BASE_URL`; verification status is
   passed through from the provider rather than assumed.

Respect each provider's terms of service. Scraping Amazon directly violates their terms and
is not implemented here.

---

## Testing

```bash
uv run pytest                      # whole suite (119 tests)
uv run pytest -v                   # verbose
uv run pytest tests/test_profit_calculator.py
uv run docs/check_connection.py    # end-to-end MCP connection self-test
```

Coverage includes product research, opportunity scoring bands and weights, demand analysis
and seasonality, competition analysis, the full profit maths (break-even and recommended
price are verified by recomputation), fee-schedule configurability, revenue and BSR-curve
estimation, new-seller and volume-target classification, evergreen scoring, every scraping
guardrail (allowlist, robots, page budget, bot-challenge detection), HTML parsing helpers,
invalid input handling for every tool, and demo-mode determinism.

The suite is fully offline: live Google Trends, web search and page fetching are forced
off so results stay deterministic.

The suite forces demo mode, disables caching and disables history persistence, so it never
touches your research database.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Server missing in Claude Desktop | Use absolute paths in the config, then fully quit and restart Claude Desktop |
| `spawn python ENOENT` | Point `command` at the interpreter inside `.venv` |
| `ModuleNotFoundError` | Run `uv sync`; make sure the config uses the `.venv` interpreter |
| Server "hangs" when run manually | Correct — it is waiting for a client on stdio |
| `provider_not_configured` error | Set `PRODUCT_DATA_API_KEY` / `PRODUCT_DATA_BASE_URL`, or `DEMO_MODE=true` |
| `rate_limit_exceeded` | Wait for the provider window to reset; caching is on by default |
| Everything says "Demo" | Expected in demo mode; set `DEMO_MODE=false` and configure a provider |
| Database errors | Check `DATABASE_URL`; tools keep working without history storage |

Logs go to stderr. Set `DEBUG=true` or `LOG_LEVEL=DEBUG` for detail; in Claude Desktop, use
the MCP log files (`%APPDATA%\Claude\logs` on Windows, `~/Library/Logs/Claude` on macOS).

---

## Security Notes

- Credentials come from environment variables only; `.env` is gitignored and nothing is
  hardcoded.
- Stack traces never reach the MCP client — errors are logged server-side and returned as
  structured, user-safe payloads.
- The database stores research payloads only, no credentials.
- Fee and marketplace figures are configuration, not code, so they can be corrected without
  a code change.
- Nothing in this project scrapes Amazon or bypasses any provider's terms.

---

## Roadmap

- Real SP-API and Product Advertising API providers with request signing
- Historical tracking: price, BSR and rating trends from stored research
- MCP resources exposing saved research history back to Claude
- FBA storage and advertising cost modelling (ACOS-aware break-even)
- Category-level gating and certification (BIS / FSSAI) reference data
- Calibrating the BSR-to-units curves against real seller sales data
- Bestseller-list mining for proven-demand product discovery

---

## Disclaimer

This tool supports research; it does not replace it. Demand, sales and profitability figures
are estimates based on the inputs and the configured fee schedule — not guarantees. Verify
fees in Seller Central, verify every supplier yourself, and confirm category and brand
requirements with Amazon before investing.
