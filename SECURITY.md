# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Report it privately through
[GitHub Security Advisories](https://github.com/Suriya-Ravichandran/amazon-india-seller-mcp/security/advisories/new),
and expect an initial response within a few days.

Useful things to include: what the issue is, how to reproduce it, and what an attacker
could do with it. Never include real API keys or `.env` contents in a report.

---

## Threat model

This server does three things that carry real risk, and each has an explicit control:

1. It **fetches attacker-influenced URLs** (search results, redirects, page assets).
2. It **holds API credentials** and passes some of them in query strings.
3. It **feeds third-party web content into an LLM's context**, where text can be
   mistaken for instructions.

It runs locally over stdio under the user's own account. It is not a network service
and does not listen on a port.

---

## Controls

### SSRF protection

Implemented in [`services/security.py`](services/security.py), enforced by
[`services/browser_service.py`](services/browser_service.py).

| Check | Behaviour |
|---|---|
| Scheme | Only `http` and `https`. `file://`, `ftp://`, `gopher://`, `data:` and `javascript:` are refused |
| Port | Only 80 and 443 |
| Credentials in URL | `https://user:pass@host/` is refused |
| Literal IPs | Loopback, private, link-local, reserved, multicast and unspecified ranges are refused |
| DNS resolution | The hostname is resolved and **every** returned address is checked, so a public name pointing at `127.0.0.1` is caught |
| IPv6 tricks | `::1`, and IPv4-mapped or 6to4 addresses that map onto blocked ranges, are refused |
| Redirects | Followed **manually**, up to 5 hops, revalidating scheme, port, IP and the operator allowlist at each hop |
| Cloud metadata | `169.254.169.254` is blocked by the link-local rule |

The redirect handling is the part that matters most: `httpx`'s own
`follow_redirects=True` checks nothing after the first URL, so an allowlisted host
could redirect the fetch onto internal infrastructure. Every hop is revalidated here.

The Playwright path is covered too — a route handler applies the same checks to every
request the page makes, so a rendered page cannot pull a subresource from a blocked
address.

### Credential protection

- Credentials come only from environment variables. `.env` is gitignored; only
  `.env.example` is committed.
- A `RedactingFilter` is attached to the root logger by `configure_logging()`. It
  redacts the **formatted** message, so it also covers library logging — `httpx` logs
  full request URLs at INFO level, and Google Programmable Search requires the API key
  in the query string.
- Redacted: `api_key=`, `key=`, `token=`, `secret=`, `password=`, `auth=` parameters,
  `Authorization: Bearer`, `X-Subscription-Token`, `X-API-KEY`, and any configured key
  value appearing verbatim anywhere in a record.
- Tool output never contains a key. `scraper_status` reports `api_key_configured: true`
  rather than the value.
- The database stores research payloads only, never credentials.

### Untrusted content and prompt injection

Scraped titles, bullets, descriptions and **customer reviews** are written by third
parties and land directly in an LLM's context. Anyone can publish an Amazon listing.

Every scraped or searched field is:

1. **Sanitised** — control characters, zero-width characters and bidirectional
   overrides are stripped, so text cannot be hidden from the human reading it while
   still reaching the model. Long fields are truncated.
2. **Scanned** for instruction-shaped content: instruction overrides, role hijacking,
   system-prompt probing, delimiter injection (`</system>`, `[INST]`, `<|im_start|>`),
   tool coercion and exfiltration attempts.
3. **Labelled** — results carry a `content_safety` block stating the content is
   third-party data, listing any suspicious fields, and setting a `warning` when
   something was found.

The server's MCP instructions tell the model that anything with a `content_safety`
block is data, never instructions, and that it must never repeat credentials.

This reduces risk; it does not eliminate it. Treat any instruction that appears to come
from a product listing as hostile.

### Resource limits

| Limit | Value |
|---|---|
| Response body | 8 MB, streamed with the cap enforced mid-stream and via `Content-Length` |
| Redirect hops | 5 |
| Config file size | 1 MB |
| Untrusted text field | 5,000 characters |
| Pages per research run | `BROWSER_MAX_PAGES_PER_RUN`, default 20 |
| Request timeout | `BROWSER_TIMEOUT_SECONDS`, default 30 |

### Config file paths

`AMAZON_FEE_CONFIG_PATH` and `BROWSER_SELECTORS_PATH` are resolved, confirmed to be
regular files, and size-checked before being read. A malformed or oversized file logs a
warning and falls back to the bundled defaults rather than crashing the server.

### Error handling

`ServiceError` messages are written for end users and are safe to surface. Every other
exception is logged in full server-side and replaced with a generic message, so a stack
trace never reaches the MCP client.

### Database

SQLAlchemy ORM with parameterised queries throughout; no string-built SQL. Persistence
is best-effort — a database failure is logged and swallowed rather than breaking a tool
call. Set `PERSIST_RESEARCH=false` to store nothing.

---

## Scope

In scope:

- Credential leakage into logs, tool output, MCP responses or the database
- Stack traces or internal state escaping to the MCP client
- SSRF: any way to make the browser layer reach a private, loopback or metadata address
- Bypasses of the domain allowlist, robots.txt enforcement, crawl delay or page budget
- Prompt injection that survives sanitisation and is not flagged
- Path traversal or arbitrary file read through a config setting
- SQL injection or unsafe deserialisation

Out of scope:

- Inaccurate estimates. Modelled figures are labelled `Estimated` by design; if one is
  wrong or misleading, open a normal issue.
- Amazon blocking the scraper. That is expected behaviour, not a vulnerability.
- Requests to add bot-protection bypass. See [docs/SCRAPING.md](docs/SCRAPING.md).
- Anything requiring an attacker who already controls the machine or the `.env` file.

---

## Hardening your own deployment

- Keep `BROWSER_ENABLED=false` unless you are actively scraping.
- Keep `BROWSER_ALLOWED_DOMAINS` as narrow as possible; empty blocks everything.
- Never set `BROWSER_RESPECT_ROBOTS=false` without written permission from the site.
- Restrict `.env` permissions: `chmod 600 .env` on macOS/Linux.
- Set `PERSIST_RESEARCH=false` if you do not want research history on disk.
- Rotate any API key that has ever appeared in a shared log or screenshot.
- Keep dependencies current: `uv lock --upgrade && uv sync --all-extras && uv run pytest`.

---

## Verifying the controls

The security controls are covered by [`tests/test_security.py`](tests/test_security.py):

```bash
uv run pytest tests/test_security.py -v
```

Those tests assert that private and metadata addresses are refused, non-HTTP schemes
and unexpected ports are rejected, credentials are redacted from log records, injection
patterns are detected while ordinary product text is not, invisible characters are
stripped, and unsafe config paths are refused.
