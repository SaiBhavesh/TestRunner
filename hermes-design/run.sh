#!/usr/bin/env bash
# Serve the TestRunner pages locally.
# Usage: ./run.sh [port]      (default port 8000)
# Then open http://localhost:8000/index.html

set -euo pipefail

PORT="${1:-8000}"
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to run the server." >&2
  exit 1
fi

echo "TestRunner running at http://localhost:${PORT}/index.html"
echo "Operator console:     http://localhost:${PORT}/console.html"
echo "Proof of MCP use:     http://localhost:${PORT}/proof.html"
echo "Press Ctrl+C to stop."

exec python3 -m http.server "$PORT" --bind 127.0.0.1
