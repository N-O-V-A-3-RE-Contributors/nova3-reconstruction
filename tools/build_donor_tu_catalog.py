#!/usr/bin/env python3
"""Offline donor TU worksheet CLI; proprietary inputs stay owner-local."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nova3_team.donor_tu_catalog import main


if __name__ == "__main__":
    sys.exit(main())
