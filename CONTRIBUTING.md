# Contributing

Thanks for your interest. This project helps beginner Amazon India sellers research
products honestly — contributions that improve accuracy, coverage or clarity are all
welcome.

## Getting set up

```bash
git clone https://github.com/Suriya-Ravichandran/amazon-india-seller-mcp.git
cd amazon-india-seller-mcp
uv sync --all-extras
uv run pytest
```

[docs/SETUP.md](docs/SETUP.md) has the full walkthrough.

## The one rule that matters

**Never invent data, and never let an estimate look like a measurement.**

This project is used to make spending decisions. Every meaningful output carries
`source`, `data_type` (Live / Verified / Estimated / Historical / Demo), `confidence`
and `last_updated`. A contribution that drops or fakes those will not be merged.

In practice:

- A value that could not be obtained is `null` — never `0`, never a plausible guess.
- Modelled numbers return a **range** and say which method produced them.
- Demo data is labelled `Demo` and is never presented as real marketplace data.
- Supplier names, prices and MOQs are never fabricated.
- No tool promises guaranteed sales or profit.

## Architecture

Keep the layering intact:

All code lives in the `amazon_india_seller_mcp/` package — top-level names like
`tools` or `config` would collide with other distributions once published.

| Layer | Responsibility |
|---|---|
| `amazon_india_seller_mcp/server.py` | Wiring only — no business logic |
| `amazon_india_seller_mcp/tools/` | Validate input, call a service, shape the result, handle errors |
| `amazon_india_seller_mcp/services/` | All business logic |
| `amazon_india_seller_mcp/database/` | SQLAlchemy models and session handling |
| `amazon_india_seller_mcp/config/` | Environment-driven settings |

The root `server.py` is a compatibility shim, so older Claude Desktop configs that
point at it by path keep working.

A new tool is usually a thin file in `tools/` plus logic in an existing service. If a
tool file starts growing calculations, move them into a service.

## Adding a tool

1. Create `amazon_india_seller_mcp/tools/your_tool.py` with a Pydantic input model,
   an `@tool_handler` decorated async function, and a `register(mcp, services)` function.
2. Put the real logic in a service under `amazon_india_seller_mcp/services/`.
3. Add the module to `TOOL_MODULES` in `amazon_india_seller_mcp/server.py`.
4. Add the tool name to `EXPECTED_TOOLS` in `docs/check_connection.py`.
5. Write tests, including at least one invalid-input case.
6. Document it in the README tool table and add a prompt to `docs/PROMPTS.md`.

## Code style

- Type hints throughout; `async` where the work is I/O bound.
- Pydantic for input validation.
- Docstrings that explain *why*, not just what.
- Keep functions small and named for what they do.
- Match the surrounding code — comment density, naming, structure.

## Tests

```bash
uv run pytest                 # all of it
uv run pytest -v              # named
uv run pytest tests/test_ads_and_listing.py
```

The suite must stay **fully offline**. Live Google Trends, web search and page
fetching are forced off in `tests/__init__.py`. If you need network-shaped behaviour,
use a fixture — `tests/test_scraping_guardrails.py` shows the pattern with fixed HTML.

CI runs the suite on Python 3.11, 3.12 and 3.13, then starts the MCP server and checks
every tool registers.

## Scraping contributions

The browser layer deliberately does **not** bypass bot protection. Pull requests adding
proxy rotation, fingerprint spoofing, stealth patches or CAPTCHA solving will be
declined — see [docs/SCRAPING.md](docs/SCRAPING.md) for the reasoning.

Very welcome instead:

- Selector fixes when Amazon changes its markup (or better fallbacks)
- Additional parse targets on robots-permitted paths
- Improvements to block detection and error messages
- Better `field_coverage` reporting

## Calibration data

The BSR-to-units curves in `services/revenue_service.py` and the conversion-rate and
CPC benchmarks in `services/ads_service.py` are reasoned approximations, not fitted to
real data. If you have real Amazon India sales figures to calibrate against, that is
one of the most valuable contributions you could make. Open an issue first so we can
talk about how to use it without exposing your account data.

## Pull requests

- Branch from `main`, one focused change per PR.
- Run `uv run pytest` before pushing.
- Explain *why* in the description, not just what changed.
- Update the docs in the same PR.

## Reporting bugs

Include: what you asked, what you expected, what happened, the `data_type` of any
suspicious number, and whether you were in demo or live mode. Never paste API keys or
`.env` contents into an issue.

## Licence

By contributing, you agree that your contributions are licensed under the
[MIT Licence](LICENSE).
