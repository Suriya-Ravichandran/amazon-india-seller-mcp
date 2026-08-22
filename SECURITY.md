# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Report it privately through
[GitHub Security Advisories](https://github.com/Suriya-Ravichandran/Amazon-India-Product-Research-MCP/security/advisories/new),
and expect an initial response within a few days.

Useful things to include: what the issue is, how to reproduce it, and what an attacker
could do with it. Never include real API keys or `.env` contents in a report.

## Scope

In scope:

- Credential leakage (keys reaching logs, tool output, MCP responses or the database)
- Stack traces or internal state escaping to the MCP client
- Injection through tool inputs (SQL, path traversal, SSRF via the browser layer)
- Bypasses of the browser guardrails: the domain allowlist, robots.txt enforcement,
  crawl delay or page budget

Out of scope:

- Inaccurate estimates. Modelled figures are labelled `Estimated` by design; if one is
  wrong or misleading, open a normal issue.
- Amazon blocking the scraper. That is expected behaviour, not a vulnerability.
- Requests to add bot-protection bypass. See [docs/SCRAPING.md](docs/SCRAPING.md).

## Handling your own secrets

- Keys live in `.env`, which is gitignored. Only `.env.example` is committed.
- Nothing is hardcoded in the source; every credential comes from an environment variable.
- The database stores research payloads only, never credentials.
- Logs go to stderr and are scrubbed of credentials — if you ever see a key in a log,
  that is a vulnerability worth reporting.
- Review `BROWSER_ALLOWED_DOMAINS` before enabling the browser layer. An empty
  allowlist blocks everything, which is the safe default.
