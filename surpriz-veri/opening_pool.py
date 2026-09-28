"""
SÜRPRİZ VERİ - Opening Odds Pool

Bu modül yalnızca AÇILIŞ ORANLARI havuzunu yönetir.

Önemli:
- Closing odds burada kullanılmaz.
- Odds movement burada kullanılmaz.
- Bu havuz diğer havuzlardan bağımsızdır.
"""

from typing import Dict, List

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord, Odds


def _is_valid_odds(odds: Odds) -> bool:
    """Üçlü maç sonucu oranlarının kullanılabilir olup olmadığını kontrol eder."""

    return all(
        value is not None and value > 1.0
        for value in odds.values()
    )


def odds_distance(current: Odds, historical: Odds) -> float:
    """
    Güncel açılış oranları ile tarihsel açılış oranları
    arasındaki toplam mesafeyi hesaplar.

    Bu fonksiyon exact-match istemez.
    Tolerans CONFIG üzerinden yönetilir.
    """

    if not _is_valid_odds(current):
        return float("inf")

    if not _is_valid_odds(historical):
        return float("inf")

    distances = [
        abs(current.home - historical.home),
        abs(current.draw - historical.draw),
        abs(current.away - historical.away),
    ]

    return sum(distances)


def is_opening_match(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> bool:
    """
    Tarihsel maçın güncel maçın opening odds profiline
    yeterince yakın olup olmadığını belirler.
    """

    if not CONFIG.use_opening_pool:
        return False

    current_odds = current.opening_odds
    historical_odds = historical.record.opening_odds

    if not _is_valid_odds(current_odds):
        return False

    if not _is_valid_odds(historical_odds):
        return False

    tolerance = CONFIG.opening_odds_tolerance

    return (
        abs(current_odds.home - historical_odds.home) <= tolerance
        and
        abs(current_odds.draw - historical_odds.draw) <= tolerance
        and
        abs(current_odds.away - historical_odds.away) <= tolerance
    )


def build_opening_pool(
    current: MatchRecord,
    historical_matches: List[HistoricalMatch],
) -> List[HistoricalMatch]:
    """
    Güncel maç için opening-odds benzerlik havuzunu oluşturur.

    Yalnızca tarihsel OPENING odds değerleri karşılaştırılır.
    """

    pool: List[HistoricalMatch] = []

    for historical in historical_matches:
        if is_opening_match(current, historical):
            pool.append(historical)

    return pool


def opening_pool_statistics(
    pool: List[HistoricalMatch],
) -> Dict[str, int]:
    """
    Opening pool içindeki tarihsel HT/FT sonuçlarının
    ham adetlerini döndürür.
    """

    statistics: Dict[str, int] = {}

    for historical in pool:
        outcome = historical.record.outcome.ht_ft

        if not outcome:
            continue

        statistics[outcome] = statistics.get(outcome, 0) + 1

    return statistics
