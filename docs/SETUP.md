# Setup & Run Guide

Complete instructions to install the Amazon India Product Research MCP, verify it works, and
connect it to Claude Desktop.

**Time required:** about 10 minutes.
**Cost:** ₹0 — the server runs fully in demo mode with no paid API keys.

Every command in this guide was executed against this project on Windows 11 with Python
3.14.3 and uv 0.12.5.

---

## Contents

1. [What you are setting up](#1-what-you-are-setting-up)
2. [Prerequisites](#2-prerequisites)
3. [Install the project](#3-install-the-project)
4. [Configure the environment](#4-configure-the-environment)
5. [Verify the installation](#5-verify-the-installation)
6. [Connect Claude Desktop](#6-connect-claude-desktop)
7. [Use it](#7-use-it)
8. [Reading the output](#8-reading-the-output)
9. [Command reference](#9-command-reference)
10. [Configuration reference](#10-configuration-reference)
11. [Going beyond demo mode](#11-going-beyond-demo-mode)
12. [Database setup](#12-database-setup)
13. [Troubleshooting](#13-troubleshooting)
14. [Reset and uninstall](#14-reset-and-uninstall)

> Enabling live data? Read the [Live Data & Scraping Guide](SCRAPING.md) alongside
> section 11 of this document.

---

## 1. What you are setting up

An MCP server that gives Claude Desktop **20 Amazon India product research tools**. You will:

```text
install uv  ->  uv sync  ->  copy .env  ->  verify  ->  edit Claude Desktop config  ->  restart Claude
```

You do **not** start the server yourself for normal use. Claude Desktop launches it
automatically on startup and shuts it down when it closes. You only run it manually to test.

---

## 2. Prerequisites

| Requirement | Version | Check with | Notes |
|---|---|---|---|
| Python | 3.11 or newer | `python --version` | 3.14 works; uv can also install Python for you |
| uv | any recent | `uv --version` | Manages the virtualenv and dependencies |
| Claude Desktop | current | — | [claude.ai/download](https://claude.ai/download) — the desktop app, not the browser |
| Git | any | `git --version` | Only if you are cloning the repository |

### Installing uv

**Windows (PowerShell):**

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Any platform, via pip:**

```bash
python -m pip install uv
```

Then confirm:

```bash
uv --version
```

> **If `uv` is "not recognised"** after installing it with pip, your shell has a stale PATH.
> Either close and reopen the terminal, or use `python -m uv` in place of `uv` everywhere in
> this guide — `python -m uv sync`, `python -m uv run pytest`, and so on. Both forms are
> identical in behaviour.

---

## 3. Install the project

```bash
git clone <your-repo-url>
cd amazon-india-seller-mcp
uv sync
```

Already have the folder? Just `cd` into it and run `uv sync`.

`uv sync` reads `pyproject.toml`, resolves against the locked versions in `uv.lock`, creates
`.venv/` in the project folder, and installs everything: `mcp`, `pydantic`,
`pydantic-settings`, `httpx`, `sqlalchemy`, `python-dotenv` and `pytest`.

Expected output ends with a package list:

```text
Installed 40 packages in 1.23s
 + mcp==2.0.0
 + pydantic==2.13.0
 + sqlalchemy==2.0.52
 ...
```

You never need to activate the virtualenv — `uv run <command>` uses it automatically.

### Optional extras (only for live data)

The base install runs every tool in demo mode. Two extras unlock live data, both free:

```bash
uv sync --extra realtime            # Google Trends + DuckDuckGo search (no API keys)
uv sync --extra browser             # Playwright + selectolax for scraping amazon.in
uv run playwright install chromium  # only needed for render=true

uv sync --extra realtime --extra browser   # both at once
```

> **Do not** run `pip install` in this project. uv owns the environment; mixing the two
> causes drift between what is installed and what `uv.lock` records. Use `uv add <package>`
> to add a dependency and `uv remove <package>` to drop one.

---

## 4. Configure the environment

Copy the example file:

```powershell
# Windows (PowerShell or cmd)
copy .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

**The defaults work as-is.** `DEMO_MODE=true` means every tool runs on deterministic sample
data with no API keys. You can skip straight to verification.

The only line worth reviewing now:

```ini
DEMO_MODE=true          # sample data, clearly labelled "Demo" - keep this until you have a real data provider
```

`.env` is gitignored, so your keys never reach the repository. Section 10 documents every
variable.

> Skipping this step is also fine — the server falls back to safe defaults (demo mode,
> SQLite) when no `.env` file exists. Copying it just makes the settings visible and easy to
> edit later.

---

## 5. Verify the installation

Three checks, in increasing order of realism. Run all three.

### 5.1 Run the test suite

```bash
uv run pytest
```

Expected:

```text
............................................................             [100%]
119 passed in 1.22s
```

This covers profit maths, opportunity scoring, demand and seasonality, competition analysis,
revenue and BSR estimation, new-seller classification, evergreen scoring, every scraping
guardrail, HTML parsing, invalid input handling and demo determinism. The suite runs fully
offline and never writes to your research database.

### 5.2 Start the server manually

```bash
uv run server.py
```

Expected:

```text
2026-08-22 19:15:24 | INFO     | amazon_product_mcp | amazon-product-research ready | env=production | demo_mode=False | provider=scraper | trends=True | search=duckduckgo | browser=True | modules=18
2026-08-22 19:15:24 | WARNING  | amazon_product_mcp | DEMO MODE is active: marketplace figures are deterministic samples, not real Amazon data.
2026-08-22 19:15:24 | INFO     | amazon_product_mcp | Database ready at sqlite:///.../amazon_product_mcp.db
```

Then it appears to hang. **That is correct.** An MCP server communicates over stdin/stdout
and is waiting for a client to speak to it. The startup line tells you which data sources are
active.

Press `Ctrl+C` to stop it.

### 5.3 Run the connection self-test

This is the check that actually matters: it launches the server over stdio exactly the way
Claude Desktop does, completes the MCP handshake and makes real tool calls.

```bash
uv run docs/check_connection.py
```

Expected:

```text
PASS  handshake       server 'amazon-product-research', protocol 2025-11-25
PASS  tool listing    all 20 tools registered
PASS  profit call     profit Rs.146.4, margin 36.69%, data_type Estimated
PASS  research call   score 73/100 (Good Opportunity), data_type Demo
PASS  error handling  invalid input rejected cleanly (invalid_input)

All checks passed. The MCP server is ready for Claude Desktop.
```

If all five lines say `PASS`, the server is healthy and any remaining problem is in the
Claude Desktop configuration, not the code.

> A few server log lines about a rejected `selling_price` may appear above the output. That
> is intentional — the last check deliberately sends invalid input, and the server logs the
> rejection to stderr while returning a clean error to the client.

---

## 6. Connect Claude Desktop

### 6.1 Find the two absolute paths you need

Claude Desktop does not inherit your shell, your PATH or your working directory. Every path
in its config must be **absolute**.

```powershell
# Windows (PowerShell) - run from the project folder
"{0}\.venv\Scripts\python.exe" -f (Get-Location).Path
"{0}\server.py" -f (Get-Location).Path
```

```bash
# macOS / Linux - run from the project folder
echo "$PWD/.venv/bin/python"
echo "$PWD/server.py"
```

### 6.2 Open the Claude Desktop config file

Easiest route: **Claude Desktop → Settings → Developer → Edit Config**. That opens the file
and creates it if it does not exist.

Or open it directly:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

### 6.3 Add the server

**Windows** — note the doubled backslashes, which JSON requires:

```json
{
  "mcpServers": {
    "amazon-product-research": {
      "command": "D:\\project\\amazon-india-seller-mcp\\.venv\\Scripts\\python.exe",
      "args": ["D:\\project\\amazon-india-seller-mcp\\server.py"],
      "env": {
        "DEMO_MODE": "true"
      }
    }
  }
}
```

**macOS / Linux:**

```json
{
  "mcpServers": {
    "amazon-product-research": {
      "command": "/Users/you/amazon-india-seller-mcp/.venv/bin/python",
      "args": ["/Users/you/amazon-india-seller-mcp/server.py"],
      "env": {
        "DEMO_MODE": "true"
      }
    }
  }
}
```

**Alternative — let uv resolve the environment** (requires `uv` on the system PATH that GUI
apps see; the direct-interpreter form above is more reliable):

```json
{
  "mcpServers": {
    "amazon-product-research": {
      "command": "uv",
      "args": [
        "--directory", "D:\\project\\amazon-india-seller-mcp",
        "run", "server.py"
      ],
      "env": { "DEMO_MODE": "true" }
    }
  }
}
```

Three rules that cause most failures:

1. **Absolute paths only** — no `~`, no `.`, no relative paths.
2. **Point at the venv interpreter**, not a bare `python`. Bare `python` has none of the
   dependencies installed.
3. **Valid JSON** — no trailing commas, no comments. If you already have other servers under
   `mcpServers`, add this one as a sibling key rather than replacing the block.

A copy-paste starting point with all three variants lives in
[`mcp.json.example`](../mcp.json.example).

### 6.4 Restart Claude Desktop completely

Closing the window is not enough — the app keeps running in the background and holds the old
config.

- **Windows:** right-click the Claude icon in the system tray (bottom-right, possibly under
  the `^` arrow) → **Quit**. Then reopen Claude.
- **macOS:** `Cmd+Q`, or Claude menu → **Quit Claude**. Then reopen.

### 6.5 Confirm the connection

Open the tools menu in the Claude Desktop chat box (the slider/plug icon). You should see
**amazon-product-research** with 8 tools listed.

Then ask Claude:

```text
Calculate the profit for a ₹399 product that costs ₹120, weighing 250g, sold via FBA.
```

Claude should call `calculate_profitability` and report roughly ₹136 profit at about 34%
margin. You are done.

---

## 7. Use it

Prompts that map cleanly onto the tools ([PROMPTS.md](PROMPTS.md) has 56 more, grouped by task):

```text
Screen these ideas and rank them: sink strainer, cable organizer, spice rack.

Research silicone sink strainers on Amazon India and score the opportunity.

Is a silicone sink strainer evergreen or seasonal?

How many units are competitors selling for "cable organizer"?

Are any new sellers succeeding in the kitchen drawer organizer market?

What revenue would a ₹399 product at BSR 3,500 make per month?

Calculate the profit for a ₹399 product that costs ₹120.

Plan a ₹20,000 launch for a ₹399 sink strainer that costs ₹120.

Find suppliers for cable organizers in Chennai or Tamil Nadu.

Find customer complaints about manual soap dispensers.

Generate an Amazon India listing for a reusable silicone food storage bag.

Scrape live Amazon India results for "silicone sink strainer".
```

You can also chain them naturally — *"Research cable organizers, then if the score is above
70, generate a listing for it"* — Claude will call the tools in sequence.

| Tool | Answers |
|---|---|
| `find_product_opportunities` | Which of these ideas is worth my time? (screens up to 15) |
| `research_product` | Is this one idea worth pursuing? (0–100 score) |
| `analyze_product_demand` | Do enough people want it, and is it seasonal? |
| `analyze_evergreen` | Will this still sell all year, or only in one season? |
| `analyze_purchase_signals` | How much is actually being bought right now? |
| `analyze_competition` | How hard is it to break in, and where is the gap? |
| `analyze_competitors` | Who am I up against, and are new sellers winning here? |
| `analyze_review_metrics` | How many reviews do I need to compete? |
| `calculate_profitability` | Do I actually make money at this price? |
| `calculate_revenue` | What monthly revenue and profit would this produce? |
| `plan_product_launch` | How many units do I order, and what does the budget look like? |
| `research_keywords` | What terms should the listing target? |
| `generate_listing` | Write the title, bullets, description and image brief |
| `analyze_product_images` | How good is competitor imagery, and what should mine show? |
| `analyze_reviews` | What do buyers complain about that I can fix? |
| `search_suppliers` | Where do I source it, and how do I vet the supplier? |
| `search_web` | What does the wider web say about this product or supplier? |
| `scrape_amazon_search` | What is actually on the Amazon India search page right now? |
| `scrape_amazon_product` | What are this ASIN's BSR, weight, images and seller? |
| `scraper_status` | Is live data switched on, and what is blocking it? |

**A natural workflow:** screen ideas → check demand and evergreen → check competitors and
new sellers → calculate profit → plan the launch → research keywords → generate the listing.

---

## 8. Reading the output

Every result carries `source`, `data_type`, `confidence` and `last_updated`. Check
`data_type` before you trust a number:

| `data_type` | Meaning | Safe to act on? |
|---|---|---|
| `Live` | Fetched from a configured marketplace API right now | Yes |
| `Verified` | From a source you configured and confirmed (e.g. your own rate card) | Yes |
| `Estimated` | Modelled from inputs and heuristics | Directionally, not literally |
| `Historical` | Previously stored research, may be stale | Check `last_updated` |
| `Demo` | Deterministic sample data | **No — testing only** |

In the default setup, marketplace figures (price, BSR, ratings, reviews, competitors) are
`Demo`, profitability is `Estimated` from the fee schedule, and supplier output is real
public sourcing channels marked `Public Listing`.

Three things this server will never do: invent a supplier, present demo data as live Amazon
data, or promise guaranteed sales or profit.

---

## 9. Command reference

Run all of these from the project root.

| Command | Purpose |
|---|---|
| `uv sync` | Install / update dependencies from the lockfile |
| `uv run pytest` | Run the full test suite (60 tests) |
| `uv run pytest -v` | Same, with each test named |
| `uv run pytest tests/test_profit_calculator.py` | Run one test module |
| `uv run docs/check_connection.py` | End-to-end MCP connection self-test |
| `uv run server.py` | Start the server manually (waits on stdio — expected) |
| `uv add <package>` | Add a dependency and update the lockfile |
| `uv run python -c "from config.settings import get_settings as g; s=g(); print('demo:', s.is_demo, '| provider:', s.product_data_provider, '| db:', s.database_url)"` | Print the key resolved settings (without printing secrets) |

---

## 10. Configuration reference

All settings come from environment variables, read from `.env` or from the `env` block in the
Claude Desktop config. Values set in Claude Desktop's `env` block win over `.env`.

### General

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Environment label |
| `DEBUG` | `false` | Verbose logging |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Data providers

| Variable | Default | Purpose |
|---|---|---|
| `DEMO_MODE` | `true` | Use deterministic demo data labelled `Demo` |
| `PRODUCT_DATA_PROVIDER` | `demo` | `demo`, `sp-api`, `pa-api`, or a third-party API name |
| `PRODUCT_DATA_API_KEY` | empty | Third-party provider key |
| `PRODUCT_DATA_BASE_URL` | empty | Third-party provider base URL |
| `AMAZON_API_KEY` / `AMAZON_API_SECRET` | empty | SP-API / PA-API credentials |
| `GOOGLE_TRENDS_ENABLED` | `false` | Enable a trends provider (none ships with the project) |
| `SUPPLIER_API_KEY` / `SUPPLIER_API_BASE_URL` | empty | Supplier data provider |

### Live data (all free, no API key)

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_TRENDS_ENABLED` | `false` | Real India search interest via pytrends; powers demand, seasonality and evergreen scoring |
| `WEB_SEARCH_PROVIDER` | `demo` | `duckduckgo` (free, keyless), `brave`, `serper`, `tavily`, `google_cse` |
| `WEB_SEARCH_API_KEY` | empty | Only for the keyed providers |
| `WEB_SEARCH_CX` | empty | Google Programmable Search engine id |

### Browser / scraping layer

Read [SCRAPING.md](SCRAPING.md) before enabling any of this.

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_ENABLED` | `false` | Master switch; nothing is fetched while false |
| `BROWSER_ALLOWED_DOMAINS` | empty | Comma-separated hostnames you may fetch; empty blocks everything |
| `BROWSER_RESPECT_ROBOTS` | `true` | Enforce robots.txt |
| `BROWSER_MIN_DELAY_SECONDS` | `5` | Crawl delay per host |
| `BROWSER_MAX_PAGES_PER_RUN` | `20` | Page budget per research run |
| `BROWSER_USER_AGENT` | empty | Identify yourself; blank uses a standard Chromium UA |
| `BROWSER_SELECTORS_PATH` | empty | JSON selector overrides, to fix parsing without code changes |

### Analysis thresholds

| Variable | Default | Purpose |
|---|---|---|
| `MIN_MONTHLY_UNITS_TARGET` | `300` | Volume a competitor must clear to count as a real seller |
| `NEW_SELLER_REVIEW_THRESHOLD` | `50` | At or below this review count, treat a competitor as a new seller |

### Storage and performance

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./amazon_product_mcp.db` | SQLite or PostgreSQL URL |
| `PERSIST_RESEARCH` | `true` | Save research history |
| `DB_ECHO` | `false` | Log every SQL statement |
| `CACHE_ENABLED` | `true` | In-process result caching |
| `CACHE_TTL_SECONDS` | `900` | Cache lifetime |
| `HTTP_TIMEOUT_SECONDS` | `20` | Provider request timeout |

### Fees

| Variable | Default | Purpose |
|---|---|---|
| `AMAZON_FEE_CONFIG_PATH` | empty | Path to a JSON file holding your real Seller Central rate card |

The bundled fee schedule is an approximation labelled `Estimated`. Overriding it with your
own rate card is the single highest-value change you can make to the accuracy of the profit
numbers.

---

## 11. Going beyond demo mode

Demo mode exists so you can learn the workflow for free. Before making real purchase
decisions, replace the estimated inputs in this order.

### Step 0 — Switch on free live data (no API keys, 2 minutes)

```bash
uv sync --extra realtime --extra browser
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

That gives you real Google Trends demand data, real web search, and live amazon.in
prices and purchase badges — for nothing. Amazon serves bot challenges intermittently
and this server stops rather than bypassing them, so treat Amazon scraping as a bonus
on top of dependable Trends data. See [SCRAPING.md](SCRAPING.md).

### Step 1 — Use your real fee card (biggest accuracy win, no API needed)

Export your rate card from Seller Central into a JSON file matching the `FeeSchedule` model
in [`config/settings.py`](../config/settings.py):

```json
{
  "data_type": "Verified",
  "source": "Seller Central rate card, exported 2026-08-01",
  "effective_date": "2026-08-01",
  "referral_fee_rates": { "Home & Kitchen": 0.085, "default": 0.09 },
  "gst_rate_on_fees": 0.18
}
```

Then set `AMAZON_FEE_CONFIG_PATH=D:/path/to/fees.json`. Profit output changes from
`Estimated` to `Verified` immediately.

### Step 2 — Connect real product data

Six of the eight tools currently run on demo marketplace data. Contract an approved
third-party Amazon data API, then:

```ini
DEMO_MODE=false
PRODUCT_DATA_PROVIDER=your-provider-name
PRODUCT_DATA_API_KEY=...
PRODUCT_DATA_BASE_URL=https://api.your-provider.com/v1
```

Auth headers, timeouts, rate-limit handling and error mapping are already wired in
`HttpProductDataProvider` ([`services/amazon_service.py`](../services/amazon_service.py)).
Only the response payload mapping needs adapting to your vendor's schema.

### Step 3 — SP-API / Product Advertising API

`build_provider()` already routes `sp-api` and `pa-api`, and deliberately raises a clear
"not implemented" error rather than silently falling back to fake data. Implement a
`ProductDataProvider` subclass with request signing to enable them.

### Step 4 — Supplier data

Set `SUPPLIER_API_KEY` and `SUPPLIER_API_BASE_URL`. Verification status is passed through
from the provider rather than assumed. Until then, `search_suppliers` deliberately returns
zero supplier records and points at real, publicly verifiable sourcing channels instead.

> Scraping Amazon directly violates their terms of service and is not implemented here.

---

## 12. Database setup

Tables are created automatically on startup. There is nothing to run by hand.

**SQLite (default):** the file `amazon_product_mcp.db` appears in the project root on first
run.

**PostgreSQL:**

```bash
uv add "psycopg[binary]"
```

```ini
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/amazon_mcp
```

JSON payload columns map to `JSONB` on PostgreSQL and `JSON` on SQLite automatically — the
models are portable across both.

Five tables store your research history: `product_research`, `demand_analysis`,
`competition_analysis`, `profit_calculation`, `supplier_research`. Inspect it any time:

```bash
uv run python -c "from database.models import ProductResearch, recent_records; [print(r['created_at'], r['product_name'], r['data_type']) for r in recent_records(ProductResearch, limit=10)]"
```

Storage is best-effort by design: if the database is unavailable, tools keep working and the
failure is logged rather than shown to you. Set `PERSIST_RESEARCH=false` to disable history
entirely.

---

## 13. Troubleshooting

Work top to bottom — the checks are ordered from most to least common cause.

### The server does not appear in Claude Desktop

1. **Validate the JSON.** A trailing comma or unescaped backslash silently kills the whole
   config. Paste it into any JSON validator.
2. **Check the paths exist.** Copy the `command` value and run it with `--version`:
   ```powershell
   & "D:\project\amazon-india-seller-mcp\.venv\Scripts\python.exe" --version
   ```
   If that fails, the path is wrong or `uv sync` was never run.
3. **Confirm you fully quit Claude Desktop** (tray → Quit, not just closing the window).
4. **Read the MCP logs** — they name the exact failure:
   - Windows: `%APPDATA%\Claude\logs\`
   - macOS: `~/Library/Logs/Claude/`

### `spawn python ENOENT` / `spawn uv ENOENT`

Claude Desktop cannot find the executable. GUI apps do not inherit your terminal PATH. Use
the full absolute path to `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python`
(macOS/Linux) instead of a bare command name.

### `ModuleNotFoundError: No module named 'mcp'`

The config points at a system Python rather than the project venv. Run `uv sync`, then set
`command` to the venv interpreter.

### `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`

You are on MCP SDK 2.x, where `FastMCP` was renamed to `MCPServer`. The project already
handles both — this error means something is importing `FastMCP` directly. Re-run `uv sync`
to restore the locked dependency set.

### `uv: command not found` / `'uv' is not recognized`

Reopen your terminal, or use `python -m uv` in place of `uv` for every command.

### The server "hangs" with no output when I run it

That is correct behaviour — it is waiting for an MCP client on stdio. Use
`uv run docs/check_connection.py` to exercise it properly.

### Tools return `provider_not_configured`

`DEMO_MODE=false` without a working provider. Either set `DEMO_MODE=true`, or supply
`PRODUCT_DATA_API_KEY` and `PRODUCT_DATA_BASE_URL`.

### Everything is labelled "Demo"

Expected in the default configuration. See [section 11](#11-going-beyond-demo-mode).

### `search_suppliers` returns no suppliers

Deliberate. Without a supplier API the server refuses to invent supplier names, prices or
MOQs, and returns real public sourcing channels plus a vetting checklist instead. Set
`SUPPLIER_API_KEY` to get supplier records.

### Results never change

Demo mode is deterministic on purpose — the same query always returns the same numbers, so
results are reproducible. Caching (`CACHE_TTL_SECONDS`, default 900s) also holds results
within a session. Set `CACHE_ENABLED=false` while debugging.

### Tests fail after I changed something

```bash
uv run pytest -v          # see which test and which assertion
uv sync                   # restore the exact locked dependency set
```

### Live data errors

| Error | Meaning | Fix |
|---|---|---|
| `browser_disabled` | Scraping layer is off | `BROWSER_ENABLED=true` |
| `domain_not_allowed` | Host is not allowlisted | Add it to `BROWSER_ALLOWED_DOMAINS` |
| `robots_disallowed` | robots.txt forbids the path | Use the official API instead |
| `blocked_by_site` | Amazon served a bot challenge | Stop, raise the delay, or use a licensed API. This server never bypasses challenges |
| `page_budget_exceeded` | Hit the per-run page cap | Raise `BROWSER_MAX_PAGES_PER_RUN` |
| `playwright_not_installed` | `render=true` needs Chromium | `uv sync --extra browser && uv run playwright install chromium` |
| `insufficient_data` | Page fetched, nothing parsed | Check `field_coverage`; update selectors via `BROWSER_SELECTORS_PATH` |

Ask Claude *"check the scraper status"* for a live diagnosis of what is blocking it.

### Google Trends returns nothing

Normal — it is an unofficial, aggressively rate-limited endpoint. The server falls back
to its internal model and labels the result `Estimated` instead of `Live`. Wait a few
minutes, or confirm `uv sync --extra realtime` has been run.

### Getting more detail from any failure

Add to the Claude Desktop `env` block, then fully restart Claude:

```json
"env": { "DEMO_MODE": "true", "DEBUG": "true", "LOG_LEVEL": "DEBUG" }
```

Logs go to stderr, keeping stdout clean for MCP protocol traffic.

---

## 14. Reset and uninstall

**Clear research history:**

```bash
rm amazon_product_mcp.db      # Windows: del amazon_product_mcp.db
```

It is recreated empty on the next start.

**Rebuild the environment from scratch:**

```bash
rm -rf .venv                  # Windows: rmdir /s /q .venv
uv sync
```

**Disconnect from Claude Desktop:** delete the `amazon-product-research` block from
`claude_desktop_config.json` and fully restart Claude Desktop.

**Remove entirely:** delete the project folder. Nothing is installed outside it.

---

## Next steps

- [`../README.md`](../README.md) — features, architecture and tool reference
- [`../.env.example`](../.env.example) — every configuration variable with comments
- [`../mcp.json.example`](../mcp.json.example) — Claude Desktop config variants
- [`check_connection.py`](check_connection.py) — the connection self-test used in section 5.3
- [`SCRAPING.md`](SCRAPING.md) — live data sources, scraping guardrails and compliance
- [`PROMPTS.md`](PROMPTS.md) — copy-paste prompt library and chained workflows

**A reminder before you spend money:** demand, sales and profitability figures are estimates
based on your inputs and the configured fee schedule, not guarantees. Verify fees in Seller
Central, verify every supplier yourself, and confirm category and brand requirements with
Amazon before investing.
