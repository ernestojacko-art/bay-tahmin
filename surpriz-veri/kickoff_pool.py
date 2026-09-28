"""
SÜRPRİZ VERİ - Kickoff Time Pool

Maçların başlama saatlerini ayrı bir kanıt havuzu olarak
değerlendirir.

Önemli:
Kickoff zamanı opening/closing odds veya market profile
ile aynı veri değildir. Ayrı bir kanıt katmanı olarak tutulur.
"""

from datetime import datetime
from typing import Dict, List, Optional

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord


def parse_kickoff_time(
    kickoff_time: Optional[str],
) -> Optional[datetime]:
    """
    ISO formatındaki kickoff zamanını datetime nesnesine çevirir.

    Geçersiz veya eksik zamanlarda None döndürür.
    """

    if not kickoff_time:
        return None

    try:
        value = kickoff_time.strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        return datetime.fromisoformat(value)

    except (ValueError, TypeError):
        return None


def kickoff_minutes(
    kickoff_time: Optional[str],
) -> Optional[int]:
    """
    Kickoff zamanını gün içindeki dakika değerine çevirir.

    Örnek:
        13:30 -> 810
        20:45 -> 1245
    """

    parsed = parse_kickoff_time(kickoff_time)

    if parsed is None:
        return None

    return parsed.hour * 60 + parsed.minute


def circular_time_distance(
    first: int,
    second: int,
) -> int:
    """
    Günün 24 saatlik döngüsünü dikkate alarak iki saat arasındaki
    minimum dakika farkını hesaplar.
    """

    difference = abs(first - second)

    return min(
        difference,
        1440 - difference,
    )


def kickoff_similarity(
    current: MatchRecord,
    historical: HistoricalMatch,
    tolerance_minutes: int = 60,
) -> bool:
    """
    Güncel maç ile tarihsel maçın başlama saatlerini karşılaştırır.

    Varsayılan olarak aynı saat dilimi içinde ±60 dakika kabul edilir.

    Bu değer ileride config üzerinden de parametreleştirilebilir.
    """

    if not CONFIG.use_kickoff_pool:
        return False

    current_minutes = kickoff_minutes(
        current.kickoff_time
    )

    historical_minutes = kickoff_minutes(
        historical.record.kickoff_time
    )

    if current_minutes is None or historical_minutes is None:
        return False

    distance = circular_time_distance(
        current_minutes,
        historical_minutes,
    )

    return distance <= tolerance_minutes


def build_kickoff_pool(
    current: MatchRecord,
    historical_matches: List[HistoricalMatch],
    tolerance_minutes: int = 60,
) -> List[HistoricalMatch]:
    """
    Güncel maçın başlama saatine benzeyen tarihsel
    maçlardan ayrı bir havuz oluşturur.
    """

    pool: List[HistoricalMatch] = []

    for historical in historical_matches:
        if kickoff_similarity(
            current,
            historical,
            tolerance_minutes=tolerance_minutes,
        ):
            pool.append(historical)

    return pool


def kickoff_pool_statistics(
    pool: List[HistoricalMatch],
) -> Dict[str, int]:
    """
    Kickoff Pool içerisindeki HT/FT sonuçlarının
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
