"""
SÜRPRİZ VERİ - Odds Profile Matcher

Bir güncel maçın odds profilini tarihsel maçlarla
karşılaştırır.

Önemli:

Opening odds -> yalnızca tarihsel Opening odds
Closing odds -> yalnızca tarihsel Closing odds
Movement    -> yalnızca tarihsel Movement

ile karşılaştırılır.

Hiçbir havuz diğerinin verisini kullanmaz.
"""

from dataclasses import dataclass
from typing import List, Optional

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord


@dataclass
class OddsProfileDistance:
    """
    İki odds profilinin farkı.
    """

    home: Optional[float]
    draw: Optional[float]
    away: Optional[float]

    total: Optional[float]


def _difference(
    current: Optional[float],
    historical: Optional[float],
) -> Optional[float]:
    """
    İki odds değeri arasındaki mutlak fark.
    """

    if (
        current is None
        or historical is None
    ):
        return None

    return abs(
        current - historical
    )


def _average(
    values: List[Optional[float]],
) -> Optional[float]:
    """
    None olmayan değerlerin ortalaması.
    """

    valid = [
        value
        for value in values
        if value is not None
    ]

    if not valid:
        return None

    return sum(valid) / len(valid)


def compare_odds_profiles(
    current_home: Optional[float],
    current_draw: Optional[float],
    current_away: Optional[float],
    historical_home: Optional[float],
    historical_draw: Optional[float],
    historical_away: Optional[float],
) -> OddsProfileDistance:
    """
    İki 1X2 odds profilini karşılaştırır.
    """

    home_diff = _difference(
        current_home,
        historical_home,
    )

    draw_diff = _difference(
        current_draw,
        historical_draw,
    )

    away_diff = _difference(
        current_away,
        historical_away,
    )

    total = _average(
        [
            home_diff,
            draw_diff,
            away_diff,
        ]
    )

    return OddsProfileDistance(
        home=home_diff,
        draw=draw_diff,
        away=away_diff,
        total=total,
    )


def opening_profile_distance(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> OddsProfileDistance:
    """
    Current Opening ile Historical Opening'i
    karşılaştırır.

    Closing kesinlikle kullanılmaz.
    """

    current_odds = (
        current.opening_odds
    )

    historical_odds = (
        historical.record.opening_odds
    )

    return compare_odds_profiles(
        current_odds.home,
        current_odds.draw,
        current_odds.away,

        historical_odds.home,
        historical_odds.draw,
        historical_odds.away,
    )


def closing_profile_distance(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> OddsProfileDistance:
    """
    Current Closing ile Historical Closing'i
    karşılaştırır.

    Opening kesinlikle kullanılmaz.
    """

    current_odds = (
        current.closing_odds
    )

    historical_odds = (
        historical.record.closing_odds
    )

    return compare_odds_profiles(
        current_odds.home,
        current_odds.draw,
        current_odds.away,

        historical_odds.home,
        historical_odds.draw,
        historical_odds.away,
    )


def is_opening_match(
    current: MatchRecord,
    historical: HistoricalMatch,
    tolerance: Optional[float] = None,
) -> bool:
    """
    Güncel Opening odds ile tarihsel Opening odds
    benzer mi?
    """

    tolerance = (
        CONFIG.opening_odds_tolerance
        if tolerance is None
        else tolerance
    )

    distance = (
        opening_profile_distance(
            current,
            historical,
        )
    )

    return (
        distance.total is not None
        and distance.total <= tolerance
    )


def is_closing_match(
    current: MatchRecord,
    historical: HistoricalMatch,
    tolerance: Optional[float] = None,
) -> bool:
    """
    Güncel Closing odds ile tarihsel Closing odds
    benzer mi?
    """

    tolerance = (
        CONFIG.closing_odds_tolerance
        if tolerance is None
        else tolerance
    )

    distance = (
        closing_profile_distance(
            current,
            historical,
        )
    )

    return (
        distance.total is not None
        and distance.total <= tolerance
    )


def find_opening_matches(
    current: MatchRecord,
    historical_matches: List[
        HistoricalMatch
    ],
    tolerance: Optional[float] = None,
) -> List[
    HistoricalMatch
]:
    """
    Opening odds benzerliği bulunan tarihsel
    maçları döndürür.
    """

    matches = []

    for historical in historical_matches:

        if is_opening_match(
            current,
            historical,
            tolerance=tolerance,
        ):
            matches.append(
                historical
            )

    return matches


def find_closing_matches(
    current: MatchRecord,
    historical_matches: List[
        HistoricalMatch
    ],
    tolerance: Optional[float] = None,
) -> List[
    HistoricalMatch
]:
    """
    Closing odds benzerliği bulunan tarihsel
    maçları döndürür.
    """

    matches = []

    for historical in historical_matches:

        if is_closing_match(
            current,
            historical,
            tolerance=tolerance,
        ):
            matches.append(
                historical
            )

    return matches
