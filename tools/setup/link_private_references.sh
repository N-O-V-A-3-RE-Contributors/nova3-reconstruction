#!/usr/bin/env bash
# Attach a worker worktree to the maintainer's private reference data.
#
# `analysis/`, `research/` and `.cache/` are gitignored: they hold the original
# ELFs, the OBB indices and the materialized 2.2 GiB asset cache. An isolated
# worktree therefore starts without them, and the oracle suite fails for reasons
# that have nothing to do with the code being tested:
#
#   tools/compare/zip_headers.py        -> needs research/assets/*.obb.index.json
#   tools/compare/texture_path_source.py-> needs .cache/assets/premium-main.obb
#
# This script creates read-only symlinks into the primary checkout so
# `NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh` can run unmodified. It never
# copies or modifies reference data, and it refuses to replace an existing
# non-empty directory or a file.
set -uo pipefail
cd "$(dirname "$0")/../.."

PRIMARY="${NOVA3_PRIMARY_CHECKOUT:-/home/jason/Projects/nova3_re}"

link() { # link <relative-path>
    local rel="$1" src="$PRIMARY/$1" dst="./$1"
    if [ ! -e "$src" ]; then
        echo "skip:  $rel (absent in $PRIMARY)"
        return 0
    fi
    if [ -L "$dst" ]; then
        echo "ok:    $rel -> $(readlink "$dst")"
        return 0
    fi
    if [ -e "$dst" ]; then
        if [ -d "$dst" ] && [ -z "$(ls -A "$dst" 2>/dev/null)" ]; then
            rmdir "$dst" || { echo "fail:  cannot replace empty dir $rel" >&2; return 1; }
        else
            echo "keep:  $rel already has local content (not replacing)" >&2
            return 0
        fi
    fi
    mkdir -p "$(dirname "$dst")"
    ln -s "$src" "$dst" && echo "link:  $rel -> $src"
}

status=0
link research/assets || status=1
link .cache/assets   || status=1
# `analysis/` is normally already provided by the harness; link it only when the
# worktree has neither the directory nor a symlink.
if [ -e analysis ] || [ -L analysis ]; then
    echo "ok:    analysis already present"
else
    link analysis || status=1
fi

echo
echo "verify with:"
echo "  .venv/bin/python tools/compare/zip_headers.py"
echo "  NOVA3_RESEARCH_ORACLES=1 bash tools/check_all.sh"
exit "$status"
