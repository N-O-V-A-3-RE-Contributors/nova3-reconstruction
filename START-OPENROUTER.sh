#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 JOIN-OPENROUTER.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python JOIN-OPENROUTER.py "$@"
fi
echo 'Python 3.10+ is required. Install it using your OS package manager.' >&2
exit 2
