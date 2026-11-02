# Consilium usage rule — append to `~/.claude/CLAUDE.md`

Paste the block below into `~/.claude/CLAUDE.md` so every project knows when/how to
consult the council.

```markdown
## Free-LLM council (consilium MCP)

A user-scope MCP server exposes three tools (`ask`, `council`, `stats`) backed by a
privacy-gated pool of free Tier-A models (proxy at 127.0.0.1:4000). The council is a **second opinion, not the
driver** — you (Claude) remain the primary reasoner.

- `ask(prompt, model?, capability?, sensitivity?)` — one best-fit model. Default
  auto-routes (classifies the task). Use for a quick routed second opinion, a cheap
  bulk step, or a strength-specific call (pass `capability` = reasoning|code|fast|general,
  or `model` for a specific member).
- `council(prompt, sensitivity?, members?, size?)` — fan out to a diverse, auto-composed
  set of models and aggregate. `members` pins an exact roster (list of aliases); `size`
  overrides the adaptive 3-5 council size. Tier-B members are dropped on `sensitive`
  even if named. Use for high-stakes cross-checks (costs more free-tier RPD).
  The result includes `confidence` (high/medium/low) — the chair's confidence the
  synthesized answer is correct.
  Pass `mode="peer-rank"` to have members rank each other's anonymized answers (winner
  verbatim, self-votes excluded); `mode="judge"`/`"vote"` force those; omit for auto.
  `mode="debate"` runs a stance-steered debate that converges then synthesizes (most calls).
- `stats()` — today's per-member usage (requests, tokens) vs daily caps; use to check
  headroom before a heavy `council` call.
- `stats` rows also carry `health` (up/down/unknown) + `health_reason`; `health_probed_at`
  is the last probe time — down members are skipped automatically.
- A council `per_member` entry's `served_by` is the alias that actually served when it
  differs from `alias`; normally null.
- **Privacy:** always set `sensitivity`. Default `sensitive` (Tier-A only). Use
  `public` only for generic/published questions. **Never** send secrets/.env/credentials
  to any free tier — the gate refuses obvious secrets, but strip them yourself first.
- **Rate hygiene:** prefer `ask`; reserve `council` for when a cross-check is worth it.
- **Rate limits degrade gracefully:** if a free model is rate-limited, `ask` falls back
  to the next-best model automatically, and `council` drops the limited voter (and falls
  back to another judge, or the best single answer). A `note`/`mode` field records what
  happened. Only if *every* eligible model is exhausted does a call error out.
```

## Registering the MCP server (once, global)

Requires the proxy running and keys in `~/.config/consilium/.env`.

```bash
python -m consilium mcp-register   # run from the cloned repo; paths are derived automatically
```
The server reads `LITELLM_MASTER_KEY` from `~/.config/consilium/.env`; it needs the
proxy up on 127.0.0.1:4000.
