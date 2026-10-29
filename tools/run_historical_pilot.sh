#!/usr/bin/env bash
# Owner-only offline experiment; arguments and pins must be explicit.
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"
umask 077
exec python3 -m nova3_team.historical_compile_pilot "$@"
