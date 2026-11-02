# Consilium proxy (Phase 0)

Single OpenAI-compatible `/v1` on `127.0.0.1:4000` fronting 3 Tier-A providers.

## 1. Provision keys (once)
Copy the contract and fill real values (all free, no card):
```bash
mkdir -p ~/.config/consilium
cp .env.example ~/.config/consilium/.env
chmod 600 ~/.config/consilium/.env
# edit ~/.config/consilium/.env — see the comments for each console
```

## 2. Run the proxy
```bash
bash scripts/run-proxy.sh          # loads the env file, validates, launches on 127.0.0.1:4000
```
This runs in the foreground — leave it running and use a second shell for the health-check below.
List the registered aliases:
```bash
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:4000/v1/models
```

## 3. Live health-check (in a second shell)
```bash
.venv/bin/python -m consilium health          # add --json for the raw health store
```
Checks `/v1/models` with the master key, then sends a 1-token completion to every active
member and prints a table (`up` / `down` / `unknown`). Results are recorded in
`~/.config/consilium/health.json`, so the council skips members found down. Exit 0 if
nothing is down, 1 if a member is down (dead key, removed model — fix the id in
`proxy/config.yaml`), 2 if the proxy is unreachable or rejects the key. Rate limits,
5xx and timeouts show as `unknown` and never mark a member down.
