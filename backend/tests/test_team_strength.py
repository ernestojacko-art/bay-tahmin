from __future__ import annotations

import pytest

from app.core.config import get_team_strength_weights
from app.intelligence.team_strength import TeamStrengthEngine
from app.providers.models import TeamRawDataset, TeamRef


@pytest.mark.asyncio
async def test_profile_generated_from_sample_data(sample_provider):
    dataset = await sample_provider.get_team_dataset("T100")
    engine = TeamStrengthEngine(get_team_strength_weights())
    profile = engine.compute_profile(dataset)

    assert profile.team_id == "T100"
    assert 0.0 <= profile.overall <= 1.0
    assert 0.0 <= profile.attack <= 1.0
    assert 0.0 <= profile.defense <= 1.0
    assert profile.matches_considered == len(dataset.recent_matches)


def test_profile_handles_empty_dataset_gracefully():
    engine = TeamStrengthEngine(get_team_strength_weights())
    empty_dataset = TeamRawDataset(team=TeamRef(team_id="X1", name="Empty FC"))
    profile = engine.compute_profile(empty_dataset)

    # Must not crash and must not fabricate confident numbers.
    assert profile.matches_considered == 0
    assert profile.data_quality.value == "insufficient"
    assert profile.overall == 0.5 * 0.35 + 0.5 * 0.35 + 0.5 * 0.30  # neutral priors combined
    assert any("Insufficient" in n for n in profile.notes)
