from __future__ import annotations

from app.intelligence.surprise_engine import ROUTINE_COMBINATIONS, rank_surprises
from app.schemas.common import DataQuality
from app.schemas.prediction import HalfTimeFullTimeProbabilities
from app.schemas.team import TeamStrengthProfile


def _profile(name: str, adjusted: float) -> TeamStrengthProfile:
    return TeamStrengthProfile(
        team_id=name,
        team_name=name,
        overall=adjusted,
        attack=adjusted,
        defense=adjusted,
        form=adjusted,
        opponent_adjusted=adjusted,
        matches_considered=15,
        data_quality=DataQuality.HIGH,
        weights_used={},
        notes=[],
    )


def test_routine_outcomes_never_ranked_as_surprises():
    # Deliberately give routine combos huge probability to try to force them in.
    matrix = {
        "1/1": 0.5,
        "X/X": 0.2,
        "2/2": 0.1,
        "1/X": 0.05,
        "X/1": 0.05,
        "1/2": 0.03,
        "2/1": 0.03,
        "X/2": 0.02,
        "2/X": 0.02,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.7)
    away = _profile("Away", 0.4)

    surprises = rank_surprises(htft, home, away, model_agreement=0.8, top_n=6)
    combos = {s.combination for s in surprises}
    assert combos.isdisjoint(ROUTINE_COMBINATIONS)


def test_surprise_candidates_have_valid_composite_scores():
    matrix = {
        "1/1": 0.3, "X/X": 0.15, "2/2": 0.1,
        "1/X": 0.1, "X/1": 0.1, "1/2": 0.08,
        "2/1": 0.07, "X/2": 0.05, "2/X": 0.05,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.6)
    away = _profile("Away", 0.55)

    surprises = rank_surprises(htft, home, away, model_agreement=0.6, top_n=3)
    assert len(surprises) <= 3
    for s in surprises:
        assert 0.0 <= s.composite_score <= 1.5
        assert 0.0 <= s.confidence <= 0.85

    # Sorted descending by composite score
    scores = [s.composite_score for s in surprises]
    assert scores == sorted(scores, reverse=True)
