#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 nova3_easy.py "$@"
status=$?
echo
read -r -p 'Press Enter to close...'
exit "$status"
