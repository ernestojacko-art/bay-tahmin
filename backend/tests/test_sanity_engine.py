from __future__ import annotations

from app.intelligence.sanity_engine import check_market_contradiction, implied_probabilities_from_odds
from app.schemas.prediction import MarketComparison, OneXTwoProbabilities


def test_implied_probabilities_removes_overround():
    # 1.85 / 3.60 / 4.20 decimal odds -> implied probs before normalization
    # sum to > 1 (the bookmaker margin); after normalization they sum to 1.
    implied = implied_probabilities_from_odds({"1": 1.85, "X": 3.60, "2": 4.20})
    assert implied is not None
    total = implied.home_win + implied.draw + implied.away_win
    assert abs(total - 1.0) < 1e-6


def test_outsider_vs_banker_contradiction_is_flagged():
    # Market sees the home team as a big outsider (odds ~7.50 -> ~13% implied),
    # but the model calls them a 70% "banker" favorite -- this must raise a flag.
    market_odds = {"1": 7.50, "X": 4.50, "2": 1.45}
    implied = implied_probabilities_from_odds(market_odds)
    market_comparison = MarketComparison(market_available=True, market_implied=implied)

    model_probs = OneXTwoProbabilities(home_win=0.70, draw=0.15, away_win=0.15)
    flags = check_market_contradiction(model_probs, market_comparison, threshold=0.35)

    assert len(flags) >= 1
    assert any(f.code == "MODEL_MARKET_DIVERGENCE" for f in flags)
    assert any(f.severity in ("warning", "critical") for f in flags)


def test_no_market_data_produces_no_contradiction_flags():
    market_comparison = MarketComparison(market_available=False)
    model_probs = OneXTwoProbabilities(home_win=0.7, draw=0.15, away_win=0.15)
    flags = check_market_contradiction(model_probs, market_comparison, threshold=0.35)
    assert flags == []
