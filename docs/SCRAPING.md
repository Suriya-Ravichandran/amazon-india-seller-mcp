# Live Data & Scraping Guide

How this server gets real data without paid APIs, what the scraping layer will and
will not do, and what to expect in practice.

---

## The short version

| Source | Cost | API key | Reliability | What it gives you |
|---|---|---|---|---|
| **Google Trends** | Free | None | High | Real search interest, seasonality, evergreen scoring |
| **DuckDuckGo search** | Free | None | High | Live web results: competitors, suppliers, prices |
| **amazon.in scraping** | Free | None | **Intermittent** | Prices, ASINs, ratings, review counts, "bought in past month" |
| Licensed data API | Paid | Yes | High | Everything above, reliably, at scale |

Google Trends and DuckDuckGo are dependable free real-time sources. Amazon scraping
works, then stops working when Amazon challenges the traffic — plan around that.

---

## Enabling free live data

```bash
uv sync --extra realtime --extra browser
uv run playwright install chromium     # only needed for render=true
```

In `.env`:

```ini
APP_ENV=production
DEMO_MODE=false
PRODUCT_DATA_PROVIDER=scraper
GOOGLE_TRENDS_ENABLED=true
WEB_SEARCH_PROVIDER=duckduckgo
BROWSER_ENABLED=true
BROWSER_ALLOWED_DOMAINS=amazon.in
BROWSER_RESPECT_ROBOTS=true
BROWSER_MIN_DELAY_SECONDS=8
```

Check it took effect:

```bash
uv run python -c "import asyncio,json;from tools import ServiceBundle;from tools.amazon_scraper import scraper_status;print(json.dumps(asyncio.run(scraper_status(ServiceBundle.create())),indent=2)[:900])"
```

Or just ask Claude: *"Check the scraper status."*

---

## What the scraping layer will not do

This server does **not** implement bot-protection bypass. Specifically, it does not:

- rotate proxies or IP addresses to avoid rate limits
- spoof or randomise browser fingerprints, or install stealth patches
- solve, submit or work around CAPTCHAs and interstitial challenges
- ignore robots.txt by default

When Amazon serves a challenge, the tool raises a `blocked_by_site` error telling
you to slow down or move to a licensed API. That is the intended behaviour, not a
gap to be filled.

**Why it matters practically:** evasion is what gets an IP range banned and a seller
account flagged. If Amazon data is core to your business, a licensed API is cheaper
than the alternative.

---

## What it does instead

Every fetch passes through these guardrails, in order:

| Guardrail | Setting | Default | Behaviour |
|---|---|---|---|
| Enabled check | `BROWSER_ENABLED` | `false` | Inert until you switch it on |
| Domain allowlist | `BROWSER_ALLOWED_DOMAINS` | empty | Empty means nothing is fetchable |
| robots.txt | `BROWSER_RESPECT_ROBOTS` | `true` | Disallowed paths are refused, not fetched |
| Crawl delay | `BROWSER_MIN_DELAY_SECONDS` | `5` | Per host; raised to the site's own `Crawl-delay` |
| Page budget | `BROWSER_MAX_PAGES_PER_RUN` | `20` | Caps a single research run |
| Block detection | — | always | Challenge or 403/429/503 stops the run |

Honest identification: a standard Chromium user agent, or your own via
`BROWSER_USER_AGENT`. No spoofing beyond looking like the browser it actually is.

---

## robots.txt vs Terms of Service

These are two different things and both matter.

**robots.txt** — amazon.in currently *allows* the paths this server uses:

| Path | Allowed | Used for |
|---|---|---|
| `/s?k=...` | Yes | Search results |
| `/dp/<ASIN>` | Yes | Product pages |
| `/product-reviews/<ASIN>` | Yes | Reviews (usually login-gated in practice) |
| `/gp/bestsellers/...` | Yes | Bestseller lists |

Verify for yourself at any time:

```bash
uv run python -c "import httpx,urllib.robotparser as rp;t=httpx.get('https://www.amazon.in/robots.txt',headers={'User-Agent':'Mozilla/5.0'}).text;p=rp.RobotFileParser();p.parse(t.splitlines());print(p.can_fetch('*','https://www.amazon.in/s?k=test'))"
```

**Amazon's Conditions of Use** separately restrict automated data collection,
regardless of what robots.txt permits. Enabling the scraper is your decision and
your risk. The compliant routes are:

- **Amazon SP-API** — free for sellers, official, covers your own listings and
  some catalogue data
- **Product Advertising API** — free, requires an Associates account in good standing
- **Licensed data APIs** — Rainforest API, Keepa, DataForSEO; paid, they carry the
  compliance burden, and they slot straight into `PRODUCT_DATA_PROVIDER`

---

## What to expect in practice

Observed behaviour while building this, against live amazon.in:

- A search page returns **~60 listings** with ASINs, titles, prices, images and
  sponsored flags parsing reliably.
- **"Bought in past month" badges** appear on roughly 15-25% of listings. This is
  Amazon's own published sales figure and is the single most valuable free signal
  the scraper collects.
- **Ratings and review counts** are inconsistent: Amazon moves them between a
  visible `a-icon-alt` span and a popover JSON blob. The parser tries the CSS
  selector, then falls back to regex, then returns `null`.
- **Bot challenges appear intermittently.** Amazon's Bot Manager returns a 200
  response, ~2 KB, containing only a JavaScript challenge. The server detects this
  and raises `blocked_by_site`.

So: treat the Amazon scraper as an opportunistic bonus, and Google Trends plus your
own supplier quotes as the dependable base.

### Reading `field_coverage`

Every scrape result includes it:

```json
"field_coverage": {"asin": "60/60", "price": "60/60", "rating": "12/60", "review_count": "12/60"}
```

A low ratio means Amazon changed its markup — **not** that the products lack those
values. Fields that cannot be parsed come back `null`, never `0`, so a gap is never
mistaken for a measurement.

---

## Fixing selectors without touching code

Amazon changes its markup regularly. Point `BROWSER_SELECTORS_PATH` at a JSON file
to override any bundled selector:

```json
{
  "amazon.in": {
    "search": {
      "result": "div[data-component-type='s-search-result']",
      "rating": "span[data-cy='reviews-ratings-slot']",
      "review_count": "span[data-cy='reviews-block'] span.a-size-base"
    }
  }
}
```

```ini
BROWSER_SELECTORS_PATH=D:/project/amazon-india-seller-mcp/selectors.json
```

Only the keys you supply are overridden; everything else keeps its default.
To find a current selector, open the page in a browser, inspect the element, and
copy a stable attribute (`data-cy`, `data-hook`, `data-component-type`) rather than
a generated class name.

---

## Being a good citizen

- Leave `BROWSER_MIN_DELAY_SECONDS` at 5 or higher; 8-10 for sustained use.
- Keep `BROWSER_MAX_PAGES_PER_RUN` low. You rarely need more than 2-3 pages.
- Leave caching on (`CACHE_ENABLED=true`) so repeat questions never re-fetch.
- Scrape during off-peak hours in India if you are running many queries.
- **Stop when you get blocked.** Retrying harder is what turns a soft block into a
  hard one.
- Never disable `BROWSER_RESPECT_ROBOTS` without written permission from the site.

---

## Google Trends notes

Free and keyless via `pytrends`, but unofficial:

- Google rate limits aggressively. A failure is normal, and the server falls back
  to its internal model — labelled `Estimated`, never `Live`.
- Values are *relative* search interest (0-100), not absolute searches.
- Search interest leads sales; it does not equal sales.
- India-specific data comes from `geo="IN"`, set by default.

`analyze_evergreen` pulls up to 5 years of history — around 260 weekly points — which
is what makes its seasonality judgement meaningful rather than guessed.

---

## Troubleshooting

| Error | Meaning | Fix |
|---|---|---|
| `browser_disabled` | Layer is off | `BROWSER_ENABLED=true` |
| `domain_not_allowed` | Host not allowlisted | Add it to `BROWSER_ALLOWED_DOMAINS` |
| `robots_disallowed` | robots.txt forbids the path | Use the official API; do not override lightly |
| `blocked_by_site` | Bot challenge or rate limit | Stop, raise the delay, or move to a licensed API |
| `page_budget_exceeded` | Run hit its page cap | Raise `BROWSER_MAX_PAGES_PER_RUN` or narrow the query |
| `playwright_not_installed` | `render=true` needs Chromium | `uv sync --extra browser && uv run playwright install chromium` |
| `html_parser_missing` | selectolax absent | `uv sync --extra browser` |
| `insufficient_data` | Page fetched, nothing parsed | Check `field_coverage`; update selectors |

---

## Related

- [`SETUP.md`](SETUP.md) — installation and Claude Desktop configuration
- [`../README.md`](../README.md) — features and the full tool reference
- [`PROMPTS.md`](PROMPTS.md) — prompt library, including the live-data prompts
- [`../.env.example`](../.env.example) — every setting, with a production profile
