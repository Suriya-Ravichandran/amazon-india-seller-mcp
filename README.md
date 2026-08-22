# Amazon India Product Research MCP

An MCP (Model Context Protocol) server that turns Claude Desktop into a product research
assistant for **beginner Amazon India sellers**. It scores product opportunities, estimates
demand, sizes up competition, calculates real Amazon India profitability, plans sourcing,
mines customer complaints, researches keywords and drafts a full listing.

Runs over **stdio**, so it plugs straight into Claude Desktop.

> **New here? Start with the [Setup & Run Guide](docs/SETUP.md)** — step-by-step installation,
> verification and Claude Desktop configuration, with a troubleshooting section.

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

- 8 MCP tools covering the full beginner research workflow
- 0–100 weighted opportunity scoring with a clear recommendation band
- Amazon India fee maths: referral, closing, FBA / Easy Ship / Self Ship, GST on fees,
  return reserve, break-even and recommended price
- Beginner product filter with explicit penalties for risky product traits
- Review complaint clustering with concrete supplier-level fixes
- Keyword research and a compliance-checked listing draft
- Research history stored in SQLite or PostgreSQL
- Full demo mode: everything works with **no paid API keys**, deterministically

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
│   └── listing_generator.py      # generate_listing
│
├── services/                     # All business logic
│   ├── __init__.py               # Data envelopes, errors, cache, opportunity scoring
│   ├── amazon_service.py         # Provider abstraction, snapshots, risk, competition, reviews, listings
│   ├── trends_service.py         # Demand, trend direction, seasonality, keywords
│   ├── supplier_service.py       # Sourcing research (never fabricates suppliers)
│   └── pricing_service.py        # Fees, profit, margin, ROI, break-even, recommended price
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

| Tool | What it does |
|---|---|
| `research_product` | Full opportunity report: category, price band, BSR, weight, rating, reviews, demand, competition, return / gating / brand risk, beginner fit, 0–100 score and recommendation |
| `analyze_product_demand` | Monthly demand, demand level, trend direction, seasonality, confidence and a launch decision |
| `analyze_competition` | Competition level, price and rating averages, review barrier, brand dominance, listing and image quality, weak listings, bundle / differentiation / keyword opportunities |
| `calculate_profitability` | Referral, closing, fulfilment and GST fees, return reserve, total cost, profit, margin, ROI, break-even and recommended price, plus a plain-English explanation |
| `search_suppliers` | Sourcing research for Parrys / Chennai / Tamil Nadu / India with verification status and a supplier vetting checklist |
| `analyze_reviews` | Complaints grouped by theme with mention counts and concrete product fixes, plus appreciated features and differentiation angles |
| `research_keywords` | Primary, secondary, long-tail and related keywords, search intent, priority, backend search terms and placement guidance |
| `generate_listing` | SEO title and alternatives, five bullets, description, backend terms, image direction, packaging advice and a compliance checklist |

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

Analyze the demand for silicone sink strainers on Amazon India.

Calculate the profit for a ₹399 product that costs ₹120.

Find suppliers for cable organizers in Chennai or Tamil Nadu.

Analyze the competition for kitchen drawer organizers.

Find customer complaints about manual soap dispensers.

Research keywords for a reusable silicone food storage bag.

Generate an Amazon India listing for a reusable silicone food storage bag.
```

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
uv run pytest                      # whole suite (60 tests)
uv run pytest -v                   # verbose
uv run pytest tests/test_profit_calculator.py
uv run docs/check_connection.py    # end-to-end MCP connection self-test
```

Coverage includes product research, opportunity scoring bands and weights, demand analysis
and seasonality, competition analysis, the full profit maths (break-even and recommended
price are verified by recomputation), fee-schedule configurability, invalid input handling
for every tool, and demo-mode determinism.

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
- A live search-trends provider behind `GOOGLE_TRENDS_ENABLED`
- Batch product screening (research a list of ideas and rank them)
- Historical tracking: price, BSR and rating trends from stored research
- MCP resources exposing saved research history back to Claude
- FBA storage and advertising cost modelling (ACOS-aware break-even)
- Category-level gating and certification (BIS / FSSAI) reference data

---

## Disclaimer

This tool supports research; it does not replace it. Demand, sales and profitability figures
are estimates based on the inputs and the configured fee schedule — not guarantees. Verify
fees in Seller Central, verify every supplier yourself, and confirm category and brand
requirements with Amazon before investing.
