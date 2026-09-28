"""
SÜRPRİZ VERİ - Movement Profile Matcher Tests

Opening -> Closing hareket havuzunun
ayrı çalıştığını test eder.

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from .models import (
    HistoricalMatch,
    MatchRecord,
    Odds,
    OddsMovement,
    MarketProfile,
)

from .movement_profile_matcher import (
    movement_profile_distance,
    is_movement_match,
)


def make_current_match() -> MatchRecord:
    """
    Test amacıyla güncel maç oluşturur.
    """

    return MatchRecord(
        match_id="current-movement-1",

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
    Tarihsel maç.

    Opening ve Closing farklı olsa da
    Movement değerleri güncel maçla aynıdır.
    """

    record = MatchRecord(
        match_id="historical-movement-1",

        home_team="Historical Home",

        away_team="Historical Away",

        kickoff_time="2025-09-28T20:00:00Z",

        competition="Test League",

        opening_odds=Odds(
            home=2.40,
            draw=3.10,
            away=2.90,
        ),

        closing_odds=Odds(
            home=2.15,
            draw=3.30,
            away=3.90,
        ),

        odds_movement=OddsMovement(
            home=-0.25,
            draw=0.20,
            away=1.00,
        ),

        market_profile=MarketProfile(
            available_markets=[
                "1x2",
            ],
            market_count=1,
            ht_ft_available=True,
            first_half_available=False,
            second_half_available=False,
            over_under_available=False,
            btts_available=False,
            all_markets_open=True,
        ),
    )

    return HistoricalMatch(
        record=record,
        source="test",
        source_timestamp=None,
    )


def test_movement_profile_matches():
    """
    Movement değerleri aynı olduğu için
    eşleşme gerçekleşmeli.
    """

    current = make_current_match()

    historical = make_historical_match()

    assert is_movement_match(
        current,
        historical,
        tolerance=0.01,
    )


def test_movement_distance_is_zero():
    """
    Aynı Movement profili için
    mesafe sıfır olmalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    distance = movement_profile_distance(
        current,
        historical,
    )

    assert distance.home == 0.0

    assert distance.draw == 0.0

    assert distance.away == 0.0

    assert distance.total == 0.0


def test_movement_is_independent_from_opening():
    """
    Opening odds tamamen farklı olsa bile
    Movement aynıysa eşleşme gerçekleşmeli.
    """

    current = make_current_match()

    historical = make_historical_match()

    assert current.opening_odds.home != (
        historical.record.opening_odds.home
    )

    assert is_movement_match(
        current,
        historical,
        tolerance=0.01,
    )


def test_movement_is_independent_from_closing():
    """
    Closing odds tamamen farklı olsa bile
    Movement aynıysa eşleşme gerçekleşmeli.
    """

    current = make_current_match()

    historical = make_historical_match()

    assert current.closing_odds.home != (
        historical.record.closing_odds.home
    )

    assert is_movement_match(
        current,
        historical,
        tolerance=0.01,
    )


def test_different_movement_does_not_match():
    """
    Movement farklıysa ve fark tolerasyonu aşıyorsa
    eşleşme olmamalı.
    """

    current = make_current_match()

    historical = make_historical_match()

    historical.record.odds_movement = OddsMovement(
        home=0.50,
        draw=-0.50,
        away=-0.80,
    )

    assert not is_movement_match(
        current,
        historical,
        tolerance=0.01,
    )
