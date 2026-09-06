"""
Market Cross-Check Layer (spec section 9).

CRITICAL DESIGN RULE:
  Football Intelligence -> Prediction/probability -> (if market exists) comparison.
  NEVER: no market -> no prediction.

This layer runs strictly *after* the Prediction Engine has already
produced independent probabilities. It never feeds market odds into the
Team Strength Engine, Statistical Modeling Engine, or Scenario Engine --
it only compares the two, once both already exist.
"""
from __future__ import annotations

from app.intelligence.sanity_engine import implied_probabilities_from_odds
from app.providers.models import OddsMarket
from app.schemas.prediction import MarketComparison, OneXTwoProbabilities


def _extract_1x2_selections(markets: list[OddsMarket]) -> dict[str, float] | None:
    for market in markets:
        if market.market_name.upper() in ("1X2", "MATCH ODDS", "MATCH RESULT"):
            return {s.label: s.price for s in market.selections}
    return None


def cross_check(model_probs: OneXTwoProbabilities, odds_markets: list[OddsMarket]) -> MarketComparison:
    if not odds_markets:
        return MarketComparison(
            market_available=False,
            notes=["No market/odds data available -- prediction stands on its own analysis."],
        )

    selections = _extract_1x2_selections(odds_markets)
    if not selections:
        return MarketComparison(
            market_available=False,
            notes=["Odds data present but no 1X2 market could be parsed."],
        )

    implied = implied_probabilities_from_odds(selections)
    if implied is None:
        return MarketComparison(
            market_available=False,
            notes=["1X2 market found but incomplete (missing one of 1/X/2)."],
        )

    divergence = (
        abs(model_probs.home_win - implied.home_win)
        + abs(model_probs.draw - implied.draw)
        + abs(model_probs.away_win - implied.away_win)
    ) / 2  # total variation distance

    notes = [
        "Market comparison is informational only -- it does not alter the "
        "independently-generated prediction probabilities."
    ]
    return MarketComparison(
        market_available=True,
        market_implied=implied,
        model_vs_market_divergence=round(divergence, 4),
        overround_removed=True,
        notes=notes,
    )
