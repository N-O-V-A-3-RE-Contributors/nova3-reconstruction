#!/bin/sh
cd "$(dirname "$0")" || exit 1
case "${1:-}" in
  --control|--observer|--observatory)
    shift
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
exec python3 nova3_easy.py "$@"
