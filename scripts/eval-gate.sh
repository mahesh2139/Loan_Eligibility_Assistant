#!/usr/bin/env bash
# eval-gate.sh — the release gate. Non-zero exit = release BLOCKED.
# Used manually (Step 3) and by the git pre-push hook (Step 4).
set -e
cd "$(dirname "$0")/.."

if [ -z "$API_KEY" ] && [ -f .env ]; then
  export API_KEY=$(grep -E '^API_KEY=' .env | head -n 1 | cut -d= -f2-)
fi
export API_KEY="${API_KEY:-local-dev-key}"

# Gate precondition: the stable server must be up
if ! curl -sf http://localhost:8001/health > /dev/null; then
  echo "GATE ERROR: v1 server not reachable on :8001 — run 'docker compose up -d' first."
  exit 1
fi

echo "=== Running eval gate (promptfoo) ==="
npx --yes promptfoo@latest eval -c promptfooconfig.yaml --no-cache -o promptfoo-output.json -o promptfoo-output.html

echo "=== GATE PASSED — release may proceed ==="
