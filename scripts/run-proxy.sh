#!/usr/bin/env bash
# Load Consilium secrets, validate them, and launch the LiteLLM proxy on localhost.
set -euo pipefail

ENV_FILE="${CONSILIUM_ENV_FILE:-$HOME/.config/consilium/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

required=(GROQ_API_KEY CLOUDFLARE_API_TOKEN CLOUDFLARE_API_BASE LITELLM_MASTER_KEY)
missing=()
for var in "${required[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "ERROR: missing required env vars: ${missing[*]}" >&2
  exit 1
fi

optional=(CEREBRAS_API_KEY MISTRAL_API_KEY NVIDIA_NIM_API_KEY)
active=(); dormant=()
for var in "${optional[@]}"; do
  if [[ -n "${!var:-}" ]]; then active+=("$var"); else dormant+=("$var"); fi
done
echo "Optional providers active: ${active[*]:-none}; dormant: ${dormant[*]:-none}" >&2

if [[ -n "${CONSILIUM_CHECK_ONLY:-}" ]]; then
  echo "OK: all required env vars present (check-only, not launching)"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/proxy/config.yaml"
if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: proxy config not found: $CONFIG" >&2
  exit 1
fi

# Prefer the project venv's litellm; fall back to one on PATH.
LITELLM_BIN="$REPO_ROOT/.venv/bin/litellm"
[[ -x "$LITELLM_BIN" ]] || LITELLM_BIN="litellm"
exec "$LITELLM_BIN" --config "$CONFIG" --host 127.0.0.1 --port 4000
