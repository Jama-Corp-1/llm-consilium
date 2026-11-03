# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Members no longer return empty answers on hard prompts.** The flat 2048-token output budget
  was smaller than some members need for one answer: probed live, `groq-gpt-oss-20b` returned
  *nothing* (`finish_reason=length`, 2048 completion tokens, zero content) and
  `groq-gpt-oss-120b` was truncated mid-answer. The budget is now 4096 pool-wide and 8192 for
  members that accept a reasoning effort. `max_tokens` is a ceiling and free tiers charge tokens
  actually generated, so unused headroom costs nothing.
- Council voters are asked for an explicit `low` reasoning effort instead of inheriting the
  provider default, which was the cause of the truncation above on the fan-out path.
- `cloudflare-qwen3.8-27b` no longer advertises `reasoning`: it returns an empty answer on a hard
  prompt at 4096 tokens, cannot be given an effort level (Cloudflare's OpenAI shim rejects the
  parameter), and did not return at all within 300 s at 16384. It keeps `code` and `general`.
- A non-boolean `model_info.reasoning_effort` (e.g. the plausible typo `"medium"`) is now a
  `ValueError` naming the member, instead of silently coercing to `true`.
- Pin `mcp>=1.0,<2`: the 2.x SDK renamed `FastMCP` to `MCPServer`, which broke CI installs from an
  unconstrained `mcp>=1.0`; the 2.x migration is a deliberate follow-up, not a pip-drift side effect.

### Added
- **Local prompt classifier** (`council/heuristics.py`): `ask` without an explicit `capability` no
  longer always spends an LLM round-trip deciding what kind of task it is. A pure, dependency-free
  heuristic classifies the prompt and reports a confidence; at or above 0.7 it decides, below it
  the existing LLM classifier runs exactly as before. Measured on the live proxy, short lookups
  went from ~0.85 s to ~0.25-0.45 s end to end, and each saves one free-tier request. The note
  field distinguishes the paths: `auto-routed (local): …` versus `auto-routed: …`.
  Signals, in precedence order: code (fenced block, diff header, traceback, source path, or a
  keyword cluster backed by code punctuation), reasoning (explicit markers in English *and
  Russian*, or a maths operator between operands), brevity (short, unmarked questions in a
  covered script), then `general` below the threshold so an unrecognised prompt defers rather
  than being guessed at. Soft markers (`better`, `faster`, `should we`, …) deliberately report a
  sub-threshold confidence: they hand the decision to the LLM classifier instead of making it.
- **Per-member reasoning effort and token budget**, both from `proxy/config.yaml` as the single
  source of truth: `model_info.reasoning_effort: true` marks a member that accepts the parameter,
  and `model_info.max_tokens` overrides the computed budget for one member. Effort is `low` by
  default and `medium` for reasoning-capability routes and the aggregation chair; `high` is never
  sent (see the probe table in the design doc: it returns an empty answer below an 8192 budget and
  trips Groq's per-minute token limit at 16384).
- `council/budget.py`: one place that resolves `(reasoning_effort, max_tokens)` for a member, used
  by every call path, with the pool-wide budget as a single constant rather than two that can drift.

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
- `ask` enforces a **per-member call timeout** (default 30 s, or the member's `timeout`);
  previously only the 30 s per-attempt HTTP timeout applied.
- `run-proxy.sh` validates keys via `python -m consilium check-env` (starts iff a Tier-A provider
  is ready, plus the master key); Cerebras is no longer required.
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
