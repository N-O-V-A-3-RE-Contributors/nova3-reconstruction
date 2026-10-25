#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3.10+ is required. Install Homebrew (brew.sh), then: brew install python git gh cmake ninja node'
  read -r -p 'Press Enter to exit...'
  exit 1
fi
python3 nova3_start.py "$@"
status=$?
echo
read -r -p 'Worker exited. Press Enter to close this window...'
exit "$status"
