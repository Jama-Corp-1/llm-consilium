# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Health store** (`~/.config/consilium/health.json`): permanent provider errors (401/402/403/404,
  model gone) mark a member down; 429/5xx/timeouts never do. New CLI commands
  `python -m consilium health [--json]` (live 1-token probe of every keyed member) and
  `python -m consilium check-env` (READY/dormant per provider); `install-service` adds a
  `consilium-health.timer` systemd unit that probes every 30 min.
- `served_by` on each council `per_member` entry (the alias that actually served, when it differs).
- `stats` rows carry `health`, `health_reason` and `timeout`; the result carries `health_probed_at`.
- Optional per-member `timeout` in `proxy/config.yaml` (thinking models get 60 s).
- `ProxyError`: a proxy-level rejection (bad master key) aborts the call instead of being blamed
  on a member.

### Changed
- Providers, env-var groups and the readiness ping are **derived from `proxy/config.yaml`**
  (single source of truth) instead of hand-maintained lists.
- Chair, classifier and the roster pool are **health-aware**: down members are skipped; a failed
  classifier degrades to `general` instead of failing the call.
- `ask` enforces a **per-member call timeout** (default 30 s, or the member's `timeout`); previously
  only the 120 s HTTP ceiling applied.
- `run-proxy.sh` validates keys via `python -m consilium check-env`.
- **Model pool refreshed against live catalogs (2026-11-02).** Removed withdrawn members: Cerebras
  (free tier closed, HTTP 402), GitHub Models (service retired 2026-07-30), Groq `llama-3.3-70b-versatile`,
  SambaNova (until a valid key is configured), `mistral-large-latest`. Added Tier-A members on Groq
  (`gpt-oss-20b`, `qwen3.8-27b`) and Cloudflare Workers AI (`gpt-oss-120b`, `qwen3.8-27b`,
  `glm-4.7-flash`, `llama-4-scout`, `qwen2.5-coder-32b`, `nemotron-3-120b`) and Tier-B `mistral-large-4`.
- Chair and classifier are now **derived from member scores** (strongest `reasoning` / `fast` member of
  the gated set) instead of pinned aliases, so a catalog change can no longer leave the council routing
  through a dead model (previously cost ~12 s of retry/fallback per `ask`). Pins remain optional.
- `provider_family` now denotes the weights lineage (openai/qwen/meta/...) so auto-composition
  diversifies model families, not just API vendors.
- `run-proxy.sh` requires only Groq + Cloudflare + master key; Cerebras is optional.

### Removed
- Proxy-level `router_settings.fallbacks`: the council owns failover; a proxy fallback hid a dead
  member behind a 200 and misattributed `model_used`.
- `scripts/healthcheck.py` (superseded by `python -m consilium health` / `check-env`).

### Fixed
- `compose_council` scored a member lacking the requested capability with its best unrelated score,
  letting a `fast` specialist outrank reasoners in a reasoning council; it now scores 0 and the
  diversity pass only considers capable members.

## [0.1.0] - 2026-07-28

### Added
- Free-LLM **council** with privacy-tiered routing (Tier-A no-train vs Tier-B) over a local LiteLLM proxy.
- Three facades over the council: a user-scope **Claude-Code MCP** (`ask`/`council`/`stats`), a local
  **web chat** UI with a Settings/onboarding screen, and a **Telegram bot** (long-polling).
- Cross-platform `consilium` CLI (`init`/`start`/`status`/`mcp-register`/`doctor`) and console scripts
  `consilium`, `consilium-chat`, `consilium-tg`.
- OSS infrastructure: CI (ruff + pytest on 3.10–3.13), packaging metadata, Dependabot, CodeQL,
  pip-audit, and community health files.
- Strict `mypy` type-checking (blocking CI gate).

[0.1.0]: https://github.com/Jama-Corp-1/llm-consilium/releases/tag/v0.1.0
