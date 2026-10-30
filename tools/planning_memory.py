#!/usr/bin/env python3
"""Independent CLI for the persistent planning memory (#139).

The store lives outside the repository by default and contains no network
access, no model calls and no credentials. Raw turns are only written to the
private local store; this CLI prints JSON summaries, never your transcript to
the repository.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nova3_team.planning_memory import main


if __name__ == "__main__":
    raise SystemExit(main())
