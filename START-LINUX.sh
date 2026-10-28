#!/bin/sh
cd "$(dirname "$0")" || exit 1
case "${1:-}" in
  --observer|--observatory)
    shift
    OBSERVER="${HOME}/.local/bin/nova3-observer"
    if [ ! -x "$OBSERVER" ]; then
      printf 'NOVA3 Observatory not installed. Run: bash tools/observatory/install.sh\n' >&2
      exit 2
    fi
    exec "$OBSERVER" "$@"
    ;;
esac
exec python3 nova3_easy.py "$@"
