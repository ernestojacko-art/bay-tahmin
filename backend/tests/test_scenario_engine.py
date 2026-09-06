from __future__ import annotations

from app.intelligence.scenario_engine import build_scenarios
from app.schemas.prediction import ExpectedGoals, OneXTwoProbabilities


def test_build_scenarios_returns_three_distinct_types():
    ox = OneXTwoProbabilities(home_win=0.55, draw=0.25, away_win=0.20)
    eg = ExpectedGoals(home_xg=1.6, away_xg=1.0, total_xg=2.6)
    scenarios = build_scenarios(ox, eg, "Home FC", "Away FC")

    types = {s.scenario_type for s in scenarios}
    assert types == {"favorite", "balanced", "upset"}

    favorite = next(s for s in scenarios if s.scenario_type == "favorite")
    upset = next(s for s in scenarios if s.scenario_type == "upset")
    assert favorite.probability >= upset.probability


def test_tempo_label_reflects_expected_goals():
    ox = OneXTwoProbabilities(home_win=0.4, draw=0.3, away_win=0.3)
    low = build_scenarios(ox, ExpectedGoals(home_xg=0.8, away_xg=0.9, total_xg=1.7), "A", "B")
    high = build_scenarios(ox, ExpectedGoals(home_xg=2.0, away_xg=1.8, total_xg=3.8), "A", "B")
    assert low[0].tempo == "low-scoring"
    assert high[0].tempo == "high-scoring"
