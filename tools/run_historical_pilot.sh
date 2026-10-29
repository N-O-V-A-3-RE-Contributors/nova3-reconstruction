#!/usr/bin/env bash
# Owner-only offline experiment; arguments and pins must be explicit.
# Select the allowlisted C++03 no-argument hypothesis with --source; the
# unchanged C++17 context candidate is recognized and blocked before tools run.
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"
umask 077
exec python3 -m nova3_team.historical_compile_pilot "$@"
