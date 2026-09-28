"""
SÜRPRİZ VERİ - Odds Profile Matcher Tests

Opening ve Closing odds havuzlarının
birbirine karışmadığını test eder.

Bu testler mevcut Bay Tahmin sistemine dokunmaz.
"""

from .models import (
    HistoricalMatch,
    MatchRecord,
    Odds,
    OddsMovement,
    MarketProfile,
)

from .odds_profile_matcher import (
    opening_profile_distance,
    closing_profile_distance,
    is_opening_match,
    is_closing_match,
)


def make_current_match() -> MatchRecord:
    """
    Test amacıyla güncel maç oluşturur.
    """

    return MatchRecord(
        match_id="current-1",

        home_team="Current Home",

        away_team="Current Away",

        kickoff_time="2026-09-28T20:00:00Z",

        competition="Test League",

        opening_odds=Odds(
            home=2.10,
            draw=3.40,
            away=3.20,
        ),

        closing_odds=Odds(
            home=1.85,
            draw=3.60,
            away=4.20,
        ),

        odds_movement=OddsMovement(
            home=-0.25,
            draw=0.20,
            away=1.00,
        ),

        market_profile=MarketProfile(
            available_markets=[
                "1x2",
                "btts",
                "goal_line",
                "1x2_half",
            ],
            market_count=4,
            ht_ft_available=True,
            first_half_available=True,
            second_half_available=False,
            over_under_available=True,
            btts_available=True,
            all_markets_open=True,
        ),
    )


def make_historical_match() -> HistoricalMatch:
    """
    Opening odds güncel maçla aynı,
    Closing odds ise farklı olan tarihsel maç.
    """

    record = MatchRecord(
        match_id="historical-1",

        home_team="Historical Home",

        away_team="Historical Away",

        kickoff_time="2025-09-28T20:00:00Z",

        competition="Test League",

        opening_odds=Odds(
            home=2.10,
            draw=3.40,
            away=3.20,
        ),

        closing_odds=Odds(
            home=2.50,
            draw=3.10,
            away=2.80,
        ),

        odds_movement=OddsMovement(
            home=0.40,
            draw=-0.30,
            away=-0.40,
        ),

        market_profile=MarketProfile(
            available_markets=[
                "1x2",
                "btts",
            ],
            market_count=2,
            ht_ft_available=True,
            first_half_available=False,
            second_half_available=False,
            over_under_available=False,
            btts_available=True,
            all_markets_open=True,
        ),
    )

    return HistoricalMatch(
        record=record,
        source="test",
        source_timestamp=None,
    )


def test_opening_profile_matches_opening_only():
    """
    Opening odds aynı olduğu için
    Opening havuzunda eşleşme olmalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    assert is_opening_match(
        current,
        historical,
        tolerance=0.01,
    )


def test_closing_profile_does_not_use_opening():
    """
    Opening odds aynı olsa bile Closing odds farklıysa
    Closing havuzunda eşleşme olmamalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    assert not is_closing_match(
        current,
        historical,
        tolerance=0.01,
    )


def test_opening_distance_is_zero():
    """
    Aynı Opening profilinin mesafesi sıfır olmalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    distance = opening_profile_distance(
        current,
        historical,
    )

    assert distance.home == 0.0

    assert distance.draw == 0.0

    assert distance.away == 0.0

    assert distance.total == 0.0


def test_closing_distance_is_not_zero():
    """
    Closing profilleri farklı olduğu için
    mesafe sıfır olmamalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    distance = closing_profile_distance(
        current,
        historical,
    )

    assert distance.total is not None

    assert distance.total > 0.0
