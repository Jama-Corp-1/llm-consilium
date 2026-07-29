# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
