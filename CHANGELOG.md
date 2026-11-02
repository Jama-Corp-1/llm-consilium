# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
