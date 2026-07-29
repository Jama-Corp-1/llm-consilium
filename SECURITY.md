# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

Only the `0.1.x` line receives security fixes.

## Reporting a vulnerability

**Please report vulnerabilities privately — do not open a public issue.**

Use GitHub's private vulnerability reporting: go to the repository's **Security**
tab and click **"Report a vulnerability"**, or open a private advisory directly:

- https://github.com/Jama-Corp-1/llm-consilium/security/advisories/new

This creates a private advisory visible only to the maintainer, so the issue can
be triaged and fixed before any public disclosure. Please include enough detail to
reproduce the problem (affected component/facade, steps, and impact).

## Safety rule: no secrets to free tiers

LLM Consilium routes prompts to **free** cloud LLM providers. As a hard safety rule:

- **Never send secrets, `.env` files, or credentials to any free tier** — not even a
  Tier-A (contractually no-train / no-retention) provider.
- A `sensitive` prompt reaches Tier-A providers only; Tier-B providers (which train
  on, retain, or have undocumented free-tier policies) are never contacted on
  sensitive input.

If you find a way to bypass the privacy gate — e.g. sensitive data reaching a Tier-B
provider, or credentials leaving the machine — treat it as a security vulnerability
and report it privately via the link above.
