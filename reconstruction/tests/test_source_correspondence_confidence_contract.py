"""Blocked source-correspondence heuristic regression contract (task #262).

Correspondence status and confidence labels describe source-correspondence
heuristics, not behavioural equivalence or instruction-window matches. They
are not reconstruction proof.

The required tools/source_ref/build_source_correspondence.py is absent from
this checkout. Without its match_symbol API and implementation, synthetic
rows, definition entries and a fake Bodies interface cannot be constructed
honestly. Skip explicitly while it is absent; refuse a passing placeholder
if it becomes available. The requested classification assertions remain
unimplemented and must replace this prerequisite guard once source is present.
No private source, game binaries, network or subprocess is required here.
"""

from pathlib import Path

import pytest


MATCHER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "tools/source_ref/build_source_correspondence.py"
)


def test_confidence_contract_requires_matcher_source():
    if not MATCHER_SOURCE.is_file():
        pytest.skip(
            "Required tools/source_ref/build_source_correspondence.py is absent; "
            "source-correspondence confidence assertions are blocked."
        )
    pytest.fail(
        "Matcher source is now available: replace this prerequisite guard with "
        "the synthetic match_symbol classification and confidence assertions "
        "required by task #262."
    )
