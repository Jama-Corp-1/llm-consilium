# LLM Consilium

[![CI](https://github.com/Jama-Corp-1/llm-consilium/actions/workflows/ci.yml/badge.svg)](https://github.com/Jama-Corp-1/llm-consilium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**A privacy-tiered council of *free* cloud LLMs, on tap inside Claude Code.**
Private code never reaches a provider that trains on it.

Claude Code stays the primary reasoner. When it wants a second opinion, a diverse
cross-check, or to parallelise a bounded question, it consults the council: the
same prompt fans out to several free models from different vendors (diverse errors),
and the answers are aggregated. Every call is gated by data-sensitivity so private
code never lands on a provider whose free tier trains on your prompts.

## Why this one

Free-council + MCP wrappers are a crowded space. The edge here is **privacy-tiering
by each provider's free-tier train-policy** — Tier-A (contractually no-train / no-retention)
vs Tier-B (trains, retains, or undocumented) — enforced as a **first-class gate on every
single call**, not an afterthought. A `sensitive` prompt (the default) can only reach
Tier-A providers; a Tier-B model is never contacted on sensitive input, even if you name
it explicitly.

## Providers

| Tier | Providers | Use |
|------|-----------|-----|
| **A** (no-train) | Groq, Cloudflare Workers AI (+ Cerebras on a paid plan) | any prompt (incl. private code) |
| **B** (trains / undocumented) | Mistral, NVIDIA NIM | `public` prompts only |

The tier follows the **inference provider**, not the origin of the model weights (an
open-weights model served by a Tier-B provider is still Tier-B). A provider you have no
key for stays **dormant** — key-presence activates it, so you only run what you've configured.

## Install

Cross-platform (Linux / macOS / Windows). Copy-paste, top to bottom:

```bash
git clone https://github.com/Jama-Corp-1/llm-consilium && cd llm-consilium
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt                      # litellm[proxy], httpx, pyyaml, mcp, ...
python -m consilium init                             # enter the free keys you have -> readiness table
python -m consilium start                            # background proxy (or: install-service for autostart)
python -m consilium mcp-register                     # wire into Claude Code (restart Claude Code after)
python -m consilium doctor                           # verify keys / proxy / MCP registration
```

Notes:

- `init` prompts for each provider's free API key (Enter to skip one) and writes them to
  `~/.config/consilium/.env` (never committed), then prints a live readiness table.
- `start` launches the LiteLLM proxy in the background; `python -m consilium start --foreground`
  runs it attached, and `python -m consilium install-service` sets up autostart (systemd `--user`
  on Linux; prints the equivalent for macOS / Windows).
- `python -m consilium stop` / `status` control and inspect the proxy.
- **Restart Claude Code** after `mcp-register` so it loads the user-scope MCP server.

CLI reference (`python -m consilium <command>`):

| Command | What it does |
|---------|--------------|
| `init` | prompt for provider keys, write `~/.config/consilium/.env`, print readiness |
| `start [--foreground]` / `stop` / `status` | run, stop, inspect the LiteLLM proxy |
| `mcp-register` | register the user-scope MCP server with Claude Code |
| `install-service` | systemd `--user` autostart for the proxy + the 30-min health timer (Linux) |
| `doctor` | keys, proxy, MCP registration, and a live health table |
| `check-env` | READY/dormant per provider (derived from `proxy/config.yaml`); exit 0 iff a Tier-A provider is ready |
| `health [--json]` | live-probe every keyed member through the proxy and record it in the health store; exit 1 if a member is down |

### Health

Every member's state (`up` / `down` / `unknown`) lives in `~/.config/consilium/health.json`,
shared by all facades. A member is marked **down** only by a permanent provider error — 401/402/403
auth or billing, 404 or "model gone" — seen on a real call or by `python -m consilium health`
(which sends each member a 1-token probe). A 429, a 5xx or a timeout **never** marks a member
down; it stays `unknown`/`up`. Down members are skipped when composing a roster (until the mark
expires or a later success clears it). `install-service` also installs a systemd timer that runs
the probe 2 min after boot and every 30 min. `stats` shows each member's `health` (+
`health_reason`) and the last probe time as `health_probed_at`. Proxy-level
`router_settings.fallbacks` were removed: the council already owns failover, and a proxy fallback
hid a dead member behind a successful answer (and misattributed `model_used`).

## Usage

Once registered, Claude Code sees three MCP tools:

- **`ask(prompt, model?, capability?, sensitivity?)`** — one best-fit free model for a quick
  routed second opinion or a cheap bulk step. Omit `model`/`capability` to auto-route; or pin
  `capability` = `reasoning` | `code` | `fast` | `general`, or `model` to a specific member.
- **`council(prompt, sensitivity?, members?, size?, mode?)`** — fan out to a diverse roster and
  aggregate. Use for high-stakes cross-checks where diverse errors matter (costs more free-tier
  quota than `ask`). `size` overrides the adaptive 3–5 roster; `members` pins an exact roster.
  `mode`:
  - *omit* — auto (majority vote for closed-form answers, else chair synthesis)
  - `"vote"` — force majority vote
  - `"judge"` — force chair synthesis
  - `"peer-rank"` — members rank each other's anonymized answers; the winner is returned verbatim
  - `"debate"` — stance-steered debate (members critique and revise under for/against/neutral
    stances until they converge, then the chair synthesizes); strongest for contentious
    questions, most free-tier calls
- **`stats()`** — today's per-member usage (requests, tokens) vs daily caps, plus each
  member's `health` / `health_reason` / `timeout` and the last probe time `health_probed_at`.

**Sensitivity** on every call: `"sensitive"` (the **default** — Tier-A no-train providers only)
or `"public"` (adds Tier-B). Use `"public"` only for generic / already-published questions.

**Optional calibration log:** set `CONSILIUM_LOG=1` to append a privacy-safe JSONL record of
each run to `~/.config/consilium/runs.jsonl` (for tuning routing; off by default).

## Web UI (Consilium Chat)

A local-first browser chat over the free-LLM council. It keeps persistent threads,
runs `ask` / `council` with mode / sensitivity / model controls, shows live council
fan-out progress, and carries a sidebar for provider status / limits / cost plus
in-browser key + proxy control.

**Install:**

```bash
pip install -r requirements.txt                      # adds fastapi + uvicorn
```

**Prereqs** — provider keys and a running proxy. Either set them up from the CLI:

```bash
python -m consilium init                             # enter free keys -> readiness table
python -m consilium service start                    # start the LiteLLM proxy
```

…or enter keys and start the proxy from the web UI itself.

**Run:**

```bash
python -m consilium_chat                              # then open http://127.0.0.1:8080
```

**Config** — host/port via `--host` / `--port` flags or the `CONSILIUM_CHAT_HOST` /
`CONSILIUM_CHAT_PORT` env vars (also `CONSILIUM_CHAT_DB`, `CONSILIUM_CHAT_TURNS`,
`CONSILIUM_CHAT_BUDGET`).

**Security** — it binds `127.0.0.1` by default (local-only). To reach a headless VM,
prefer an SSH tunnel (`ssh -L 8080:127.0.0.1:8080 vm`); binding to a LAN / public
address exposes the council and the key-management UI, so do so only behind trusted
network controls. Secrets stay in `~/.config/consilium/.env` (0600) — never in the
chat DB or logs.

## Telegram bot

A from-anywhere facade over the free-LLM council — a Telegram bot that runs on your VM
via **long-polling** (no webhook, no public URL, zero inbound exposure), reachable from
any device. It keeps multiple switchable conversation sessions per chat, each with its
own config; runs `ask` / `council` with live council progress; and gates access behind an
allowlist with owner-approved pairing.

**Setup** — create a bot via **@BotFather**, get the token, then put it plus your numeric
Telegram user id in `~/.config/consilium/.env`:

```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_OWNER_ID=123456789
```

(Find your id via @userinfobot.) Then:

```bash
pip install -r requirements.txt
```

**Prereqs** — provider keys and a running proxy, as for the web UI. Either set them up from
the CLI (`python -m consilium init` + `python -m consilium service start`) or from the web UI.

**Run:**

```bash
python -m consilium_tg                                # long-polling; Ctrl-C to stop
```

**Usage:**

- `/start` — greet and register the chat.
- Plain text → `ask` / `council` per your `/settings`.
- `/ask <q>` / `/council <q>` — one-off, ignoring the session tool.
- `/settings` — per-session inline menu: tool, sensitivity / tier, council mode, **model
  roster** (☑ / ☐), size, footer — all changeable mid-conversation.
- `/sessions` — switch / new / rename / delete; `/new` starts a fresh session.
- Owner only: `/approve <id>`, `/deny <id>`, `/pending`. When a new user messages the bot,
  the owner gets an inline Approve / Deny prompt.

**Privacy note** — unlike the local web UI, **Telegram is a third party: all prompts and
answers transit its servers.** The council's privacy gate still governs *which LLMs* see a
prompt (default `sensitive` → Tier-A only). Access is allowlisted; the bot token stays in
`~/.config/consilium/.env` (0600) and is never logged.

**Always-on** — the `scripts/consilium-tg.service` systemd `--user` unit keeps the bot
running and restarts it on failure. It uses absolute paths to this checkout
(`/opt/claude-projects/llm-consilium`) and is ordered after `consilium-proxy.service`; edit
`WorkingDirectory`/`ExecStart` if your checkout lives elsewhere. Copy it to
`~/.config/systemd/user/`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now consilium-tg
loginctl enable-linger "$USER"   # start at boot without an active login
```

## Architecture

Three layers:

1. **Compute** — a single **LiteLLM proxy** exposing one OpenAI-compatible `/v1` on
   `127.0.0.1:4000`. One config, all providers, all keys.
2. **Council** — a thin **orchestrator**: takes `{prompt, sensitivity}`, applies the privacy
   gate, fans out to K members through the proxy (per-member rate semaphores + timeout + quorum),
   and aggregates (vote / judge / peer-rank / debate).
3. **Claude integration** — a **user-scope MCP server** exposing `ask` / `council` / `stats`,
   available in every Claude Code project on the machine.

```
Claude Code ──MCP──▶ orchestrator (privacy gate → fan-out → aggregate) ──▶ LiteLLM proxy ──▶ free providers
```

## Privacy model

- A **`sensitive`** prompt (the default) routes to **Tier-A only** — providers whose free tier
  contractually does not train on or retain your data. Private repo code is fine here.
- A **`public`** prompt may additionally use **Tier-B** (providers that train / retain / are
  undocumented). Reserve it for generic, non-sensitive, or already-published questions.
- **Secrets / `.env` / credentials are never sent to any free tier** — not even Tier-A. The gate
  refuses obvious secrets, but strip them yourself first.
- **Graceful degradation:** providers without a key stay dormant; a rate-limited or dead member
  is dropped from the council (never crashes it), and `ask` falls back to the next-best model. A
  `note` / `mode` field on the result records what happened.

## Credits & prior art

Design ideas were **reimplemented, not copied** — no verbatim third-party code — and are credited
as a courtesy:

- **ai-council-mcp** (MIT) — anonymized synthesis + code-name debiasing before the judge merges.
- **FreeLLMAPI** (MIT) — the "Fusion" judge prompt (rewrite standalone, reason don't average) and
  provider-diversity fan-out.
- **PAL / zen** (Apache-2.0) — stance-steering (for/against/neutral) and the "stance ≠ license to
  lie" honesty guardrail in debate mode.
- **DUH** (AGPL, design only) — Jaccard convergence early-exit and confidence-as-adversarial-rigor.

The **privacy-tiering by provider train-policy** — this project's core differentiator — is our own.

**License:** [MIT](./LICENSE).
