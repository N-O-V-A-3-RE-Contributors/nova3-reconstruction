#!/bin/bash
# NOVA3 worker -- macOS launcher (double-click). First run: setup + your API key.
cd "$(dirname "$0")" || exit 1
./START-LINUX.sh "$@"
status=$?
echo
read -r -p 'Press Enter to close...'
exit "$status"
