#!/bin/sh
# NOVA3 worker -- Linux launcher (DeepSeek Harness loop).
# First run: installs this user's setup and asks for your own API key.
# Later runs: continues the work where it left off.
# The previous contributor worker (Codex/Claude/Gemini via nova3_easy.py) is
# still available as: ./START-LINUX.sh --legacy-workflow
set -e
cd "$(dirname "$0")" || exit 1
case "${1:-}" in
  --legacy-workflow)
    shift
    exec python3 nova3_easy.py "$@"
    ;;
  --control|--observer|--observatory)
    CONTROL="${HOME}/.local/bin/nova3-control"
    LEGACY="${HOME}/.local/bin/nova3-observer"
    if [ -x "$CONTROL" ]; then
      exec "$CONTROL" "$@"
    elif [ -x "$LEGACY" ]; then
      exec "$LEGACY" "$@"
    fi
    printf 'NOVA3 Control Center not installed. Run: bash tools/observatory/install.sh\n' >&2
    exit 2
    ;;
esac
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. Install it (python.org or your package manager), then retry." >&2
  exit 2
fi
exec "$PY" tools/dsh_loop/install.py --workspace "$(pwd)" --then-run "$@"
