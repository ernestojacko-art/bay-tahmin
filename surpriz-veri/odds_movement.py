"""
SÜRPRİZ VERİ - Odds Movement Pool

Opening → Closing oran hareketini ayrı bir kanıt havuzu
olarak değerlendirir.

Önemli:
- Opening Pool ile aynı şey değildir.
- Closing Pool ile aynı şey değildir.
- Hareket ayrıca analiz edilir.
"""

from typing import Dict, List, Optional

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord, OddsMovement


def calculate_movement(
    opening,
    closing,
) -> OddsMovement:
    """
    Opening oranlardan Closing oranlara gerçekleşen hareketi hesaplar.

    Hareket:
        closing - opening
    """

    home = None
    draw = None
    away = None

    if opening.home is not None and closing.home is not None:
        home = closing.home - opening.home

    if opening.draw is not None and closing.draw is not None:
        draw = closing.draw - opening.draw

    if opening.away is not None and closing.away is not None:
        away = closing.away - opening.away

    return OddsMovement(
        home=home,
        draw=draw,
        away=away,
    )


def _is_valid_movement(
    movement: OddsMovement,
) -> bool:
    """Hareket verisinin kullanılabilir olup olmadığını kontrol eder."""

    return all(
        value is not None
        for value in (
            movement.home,
            movement.draw,
            movement.away,
        )
    )


def movement_distance(
    current: OddsMovement,
    historical: OddsMovement,
) -> float:
    """
    İki opening→closing hareket profili arasındaki mesafeyi hesaplar.
    """

    if not _is_valid_movement(current):
        return float("inf")

    if not _is_valid_movement(historical):
        return float("inf")

    return (
        abs(current.home - historical.home)
        + abs(current.draw - historical.draw)
        + abs(current.away - historical.away)
    )


def is_movement_match(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> bool:
    """
    Tarihsel hareket profilinin güncel maçın hareket profiline
    yeterince yakın olup olmadığını kontrol eder.
    """

    if not CONFIG.use_movement_pool:
        return False

    current_movement = current.odds_movement

    historical_movement = historical.record.odds_movement

    # Eğer movement kayıtlı değilse historical opening/closing
    # değerlerinden yeniden hesaplanır.
    if not _is_valid_movement(historical_movement):
        historical_movement = calculate_movement(
            historical.record.opening_odds,
            historical.record.closing_odds,
        )

    if not _is_valid_movement(current_movement):
        current_movement = calculate_movement(
            current.opening_odds,
            current.closing_odds,
        )

    if not _is_valid_movement(current_movement):
        return False

    if not _is_valid_movement(historical_movement):
        return False

    tolerance = CONFIG.movement_tolerance

    return (
        abs(
            current_movement.home
            - historical_movement.home
        ) <= tolerance
        and
        abs(
            current_movement.draw
            - historical_movement.draw
        ) <= tolerance
        and
        abs(
            current_movement.away
            - historical_movement.away
        ) <= tolerance
    )


def build_movement_pool(
    current: MatchRecord,
    historical_matches: List[HistoricalMatch],
) -> List[HistoricalMatch]:
    """
    Güncel maçın opening→closing hareketine benzeyen
    tarihsel maçlardan ayrı bir havuz oluşturur.
    """

    pool: List[HistoricalMatch] = []

    for historical in historical_matches:
        if is_movement_match(current, historical):
            pool.append(historical)

    return pool


def movement_pool_statistics(
    pool: List[HistoricalMatch],
) -> Dict[str, int]:
    """
    Movement Pool içindeki tarihsel HT/FT sonuçlarının
    ham adetlerini döndürür.
    """

    statistics: Dict[str, int] = {}

    for historical in pool:
        outcome = historical.record.outcome.ht_ft

        if not outcome:
            continue

        statistics[outcome] = (
            statistics.get(outcome, 0) + 1
        )

    return statistics
