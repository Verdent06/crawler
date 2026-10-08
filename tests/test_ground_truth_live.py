"""Replay the UI settings against the real sites and diff against the ground truth."""

from __future__ import annotations

import pytest

from scripts.diff_live import diff_live


@pytest.mark.live
@pytest.mark.parametrize("name", ["asu", "a2gov"])
def test_live_site_matches_ground_truth(name: str):
    assert diff_live(name) == []
