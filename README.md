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
| `health [--json]` | live-probe every keyed member through the proxy and record it in the health store; exit 0 = all probed members up/unknown, 1 = a member is down, 2 = proxy unreachable, master key missing or rejected |

### Health

Every member's state (`up` / `down` / `unknown`) lives in `~/.config/consilium/health.json`,
shared by all facades. A member is marked **down** only by a permanent provider error — 401/402/403
auth or billing, 404 or "model gone" — seen on a real call or by `python -m consilium health`
(which sends each member a 1-token probe). A 429, a 5xx or a timeout **never** marks a member
down; it stays `unknown`/`up`. Down members are skipped when composing a roster (until the mark
expires or a later success clears it). `install-service` also installs a systemd timer that runs
the probe 2 min after boot and every 30 min; the `consilium-health.service` oneshot
intentionally shows as **failed** in `systemctl --user` while a member is down (exit 1), so a
dead member is visible without opening the health store. `stats` shows each member's `health` (+
`health_reason`) and the last probe time as `health_probed_at`. Proxy-level
`router_settings.fallbacks` were removed: the council already owns failover, and a proxy fallback
hid a dead member behind a successful answer (and misattributed `model_used`).

## Usage

Once registered, Claude Code sees three MCP tools:

- **`ask(prompt, model?, capability?, sensitivity?)`** — one best-fit free model for a quick
  routed second opinion or a cheap bulk step. Omit `model`/`capability` to auto-route; or pin
  `capability` = `reasoning` | `code` | `fast` | `general`, or `model` to a specific member.
  Auto-routing is **local-first**: a heuristic classifies the prompt with no network call, and
  only an ambiguous prompt costs the extra LLM round-trip (see
  [Routing without a round-trip](#routing-without-a-round-trip)).
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

## Routing without a round-trip

`ask` has to know what kind of task it is holding before it can pick a member. Asking a model
costs a request and the better part of a second — on a question like *"what is the capital of
Portugal?"* that is most of the total latency. So a local heuristic (`council/heuristics.py`,
pure, no dependencies) classifies the prompt first and reports a confidence; at or above `0.7`
it decides, and below it the LLM classifier runs exactly as before. Measured end to end against
the live proxy:

| Prompt | local heuristic | LLM classifier |
|---|---|---|
| "What is the capital of Portugal?" | **0.45 s** | 0.88 s |
| "Which HTTP status code means Too Many Requests?" | **0.24 s** | 0.84 s |

Signals, in precedence order: **code** (fenced block, diff header, traceback, source path, or a
keyword cluster backed by code punctuation) → **reasoning** (explicit markers in English *and
Russian*, or a maths operator between operands) → **brevity** (a short, unmarked question in a
script the markers cover) → **`general`** below the threshold.

Two deliberate choices are worth knowing, because both trade a little speed for not being wrong:

- An unrecognised prompt is **not guessed at**. It falls below the threshold and defers, so the
  heuristic's failure mode is "costs what it used to", never "answered by the wrong member".
- Soft markers — `better`, `faster`, `should we`, `scales` — report a **sub-threshold**
  confidence on purpose. *"Which of these two schemas scales better?"* is a reasoning question;
  *"can you make this sentence better?"* is not, and a single adjective cannot tell them apart.
  So they hand the decision to the LLM classifier rather than make it.

The `note` field on every result says which path ran: `auto-routed (local): code -> …` versus
`auto-routed: code -> …`. Pass an explicit `capability` to skip classification altogether.

## Reasoning models, effort and token budgets

Several of the strongest free models *think* before answering, and those thinking tokens come
out of the same `max_tokens` budget as the answer. Get the budget wrong and the model spends it
all reasoning, returns `finish_reason=length` with **zero content**, and bills you for the call.
Probed live on 2026-11-03 with one hard reasoning prompt:

| Member | effort | `max_tokens` | finish | completion tokens | answer |
|---|---|---|---|---|---|
| `groq-gpt-oss-120b` | *(none sent)* | 2048 | `length` | 2048 | truncated |
| `groq-gpt-oss-120b` | `low` | 4096 | `stop` | 2227 | complete |
| `groq-gpt-oss-120b` | `medium` | 4096 | `stop` | 2980 | complete |
| `groq-gpt-oss-120b` | `high` | 2048 / 4096 | `length` | — | **empty at both** |
| `groq-gpt-oss-120b` | `high` | 8192 | `stop` | 7979 | complete |
| `groq-gpt-oss-120b` | `high` | 16384 | — | — | **HTTP 429** (per-minute token cap) |
| `groq-gpt-oss-20b` | *(none sent)* | 2048 | `length` | 2048 | **empty** |
| `groq-gpt-oss-20b` | *(none sent)* | 4096 | `stop` | 1851 | complete |
| `cloudflare-nemotron-120b` | n/a | 2048 | `length` | — | truncated |
| `cloudflare-nemotron-120b` | n/a | 4096 | `stop` | — | complete |
| `cloudflare-glm-4.7-flash` | n/a | 2048 / 4096 / 8192 | `length` | 8192 at the largest | **empty at all three** |
| `cloudflare-glm-4.7-flash` | n/a | 16384 | `stop` | 8906 | complete |
| `cloudflare-qwen3.8-27b` | n/a | 4096 | `length` | 4096 | **empty** |
| `cloudflare-qwen-coder-32b` | n/a | 4096 | `stop` | 618 | complete |

A dash means the run recorded only a total-token count, not a completion-token count.

What that buys, and what the council does with it:

- **`high` effort is never sent.** It costs 2.7× the tokens of `medium` for an answer of the same
  length, needs 8192 just to return *anything*, and trips a rate limit at the budget where it is
  comfortable. The council sends `low` by default and `medium` for reasoning routes and the
  aggregation chair.
- **`reasoning_effort` support is a property of the member, not the model.** The same
  `gpt-oss-120b` weights accept it on Groq and reject it with HTTP 400
  `litellm.UnsupportedParamsError` behind Cloudflare's OpenAI-compatible shim; Mistral rejects it
  too. So it is opt-in per member via `model_info.reasoning_effort: true` — never inferred from a
  model id, a vendor or a weights family. Sending it blindly would turn nine working members into
  failing ones.
- **The budget is 4096 pool-wide, 8192 for effort-capable members**, overridable per member with
  `model_info.max_tokens`. `max_tokens` is a ceiling and free tiers charge tokens actually
  generated, so headroom a model does not use is free.
- **A thinking model with no effort control and no affordable budget is better off not pretending.**
  `cloudflare-glm-4.7-flash` needs ~8906 tokens for one hard answer against a 10 000 tokens/day
  cap — 89% of its day for a single question — so it stays on the pool default and is simply
  dropped from hard prompts. `cloudflare-qwen3.8-27b` lost its `reasoning` score for the same
  reason: empty at 4096, and no answer at all within 300 s at 16384.

A dead or truncating member degrades the council (its vote is dropped), it never fails the call.

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
