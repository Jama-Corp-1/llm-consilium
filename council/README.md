# Council orchestrator (Phase 1)

`ask` (one best-fit model) and `council` (fan-out + adaptive aggregate) over the
Phase-0 proxy, exposed as the `consilium` MCP server.

## Modules

| File | Responsibility |
|---|---|
| `orchestrator.py` | `ask` / `council`: privacy gate, pool selection, routing, health bookkeeping |
| `registry.py` | reads `proxy/config.yaml` (the single source of truth) into `Member`s |
| `privacy.py` | the sensitivity gate and the secret scanner |
| `heuristics.py` | local prompt classifier — `(capability, confidence)`, no network call |
| `router.py` | the LLM classifier fallback and per-capability member ranking |
| `budget.py` | resolves `(reasoning_effort, max_tokens)` for one member — the only producer |
| `compose.py` | vendor-diverse roster composition for `council` |
| `fanout.py` | concurrent member calls with per-member timeouts and sibling cancellation |
| `aggregate.py` | vote / judge / peer-rank / debate aggregation |
| `client.py` | the HTTP call, retries, error taxonomy, served-alias attribution |
| `health.py` | the health store (`~/.config/consilium/health.json`) |
| `usage.py` | the daily usage store and the `stats` rows |
| `anonymize.py` | strips member identity before peer ranking |
| `runlog.py` | the optional `CONSILIUM_LOG=1` calibration log |
| `types.py` | the dataclasses and caller protocols every module above shares |
| `errors.py` | the typed error taxonomy (`MemberCallError` degrades, `ProxyError` aborts) |

Two invariants worth preserving when editing here: `budget.resolve_call_params` is the **only**
place that can put `reasoning_effort` in a payload (it returns `None` unless the member's config
opts in — sending it to a member that rejects it is an HTTP 400 that takes the member down for
that call), and the local classifier must make **no** member call, so it can never affect the
health store.

## Live smoke (proxy must be up)
```bash
bash scripts/run-proxy.sh            # terminal 1
# terminal 2:
set -a; source ~/.config/consilium/.env; set +a
.venv/bin/python scripts/council-smoke.py
```

## Register the MCP tools
See `docs/usage-rule.md` for the `claude mcp add --scope user` command and the
`~/.claude/CLAUDE.md` protocol block.
