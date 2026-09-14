from app.intelligence.market_cross_check import cross_check
from app.providers.models import OddsMarket, OddsSelection
from app.schemas.prediction import OneXTwoProbabilities


def test_bookmaker_suffixed_turkish_1x2_market_is_recognized():
    comparison = cross_check(
        OneXTwoProbabilities(home_win=0.50, draw=0.28, away_win=0.22),
        [OddsMarket(
            market_name="Maç Sonucu 1X2 (Bet 365)",
            selections=[
                OddsSelection(label="1", price=1.80),
                OddsSelection(label="X", price=3.50),
                OddsSelection(label="2", price=4.20),
            ],
        )],
    )

    assert comparison.market_available is True
    assert comparison.market_implied is not None
    assert comparison.market_implied.home_win > comparison.market_implied.away_win
