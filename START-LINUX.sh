#!/usr/bin/env bash
set -e
cd "$(dirname "$(realpath "$0")")"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3.10+ is required. Install your distribution python3, git, gh, cmake, ninja and C++ compiler packages.'
  exit 1
fi
exec python3 nova3_start.py "$@"
