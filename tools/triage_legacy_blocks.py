#!/usr/bin/env python3
"""Run the offline legacy-source triage from a checkout, without installation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nova3_team.legacy_triage import main

if __name__ == "__main__":
    raise SystemExit(main())
