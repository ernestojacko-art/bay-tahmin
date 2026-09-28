"""
SÜRPRİZ VERİ - Movement Profile Matcher

Güncel maçın Opening -> Closing odds hareketini
tarihsel maçların Opening -> Closing hareketleriyle
karşılaştırır.

Önemli:

Opening odds başka bir havuzdur.
Closing odds başka bir havuzdur.
Movement başka bir havuzdur.

Bu modül yalnızca Movement havuzunu kullanır.

Mevcut Bay Tahmin sistemine dokunmaz.
"""

from dataclasses import dataclass
from typing import List, Optional

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord


@dataclass
class MovementProfileDistance:
    """
    İki odds movement profilinin farkı.
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
    İki movement değeri arasındaki mutlak fark.
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
    None olmayan değerlerin ortalamasını hesaplar.
    """

    valid = [
        value
        for value in values
        if value is not None
    ]

    if not valid:
        return None

    return sum(valid) / len(valid)


def compare_movement_profiles(
    current_home: Optional[float],
    current_draw: Optional[float],
    current_away: Optional[float],

    historical_home: Optional[float],
    historical_draw: Optional[float],
    historical_away: Optional[float],
) -> MovementProfileDistance:
    """
    İki Opening -> Closing movement profilini
    karşılaştırır.
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

    return MovementProfileDistance(
        home=home_diff,
        draw=draw_diff,
        away=away_diff,
        total=total,
    )


def movement_profile_distance(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> MovementProfileDistance:
    """
    Current Movement ile Historical Movement'i
    karşılaştırır.

    Opening veya Closing odds doğrudan
    karşılaştırmaya dahil edilmez.
    """

    current_movement = (
        current.odds_movement
    )

    historical_movement = (
        historical.record.odds_movement
    )

    return compare_movement_profiles(
        current_movement.home,
        current_movement.draw,
        current_movement.away,

        historical_movement.home,
        historical_movement.draw,
        historical_movement.away,
    )


def is_movement_match(
    current: MatchRecord,
    historical: HistoricalMatch,
    tolerance: Optional[float] = None,
) -> bool:
    """
    Güncel Movement ile tarihsel Movement
    benzer mi?
    """

    tolerance = (
        CONFIG.movement_tolerance
        if tolerance is None
        else tolerance
    )

    distance = movement_profile_distance(
        current,
        historical,
    )

    return (
        distance.total is not None
        and distance.total <= tolerance
    )


def find_movement_matches(
    current: MatchRecord,
    historical_matches: List[
        HistoricalMatch
    ],
    tolerance: Optional[float] = None,
) -> List[HistoricalMatch]:
    """
    Movement profili benzer olan tarihsel
    maçları döndürür.
    """

    matches = []

    for historical in historical_matches:

        if is_movement_match(
            current,
            historical,
            tolerance=tolerance,
        ):
            matches.append(
                historical
            )

    return matches
