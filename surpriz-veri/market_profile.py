"""
SÜRPRİZ VERİ - Market Profile Pool

Maçta açık olan bahis marketlerinin yapısını ayrı bir
kanıt havuzu olarak değerlendirir.

Önemli:
Market profili, opening/closing odds havuzlarından ayrıdır.
"""

from collections import Counter
from typing import Dict, List, Tuple

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord, MarketProfile


def normalize_market_name(name: str) -> str:
    """
    Market isimlerini karşılaştırılabilir hale getirir.
    """

    return (
        str(name)
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def market_signature(profile: MarketProfile) -> Tuple[str, ...]:
    """
    Bir maçın açık marketlerinden karşılaştırılabilir
    bir market imzası üretir.
    """

    markets = [
        normalize_market_name(market)
        for market in profile.available_markets
    ]

    return tuple(sorted(set(markets)))


def market_similarity(
    current: MarketProfile,
    historical: MarketProfile,
) -> float:
    """
    İki market profilinin Jaccard benzerliğini hesaplar.

    Sonuç:
        0.0 = hiç benzerlik yok
        1.0 = tamamen aynı market yapısı
    """

    current_set = set(market_signature(current))
    historical_set = set(market_signature(historical))

    if not current_set or not historical_set:
        return 0.0

    intersection = len(
        current_set.intersection(historical_set)
    )

    union = len(
        current_set.union(historical_set)
    )

    if union == 0:
        return 0.0

    return intersection / union


def is_market_profile_match(
    current: MatchRecord,
    historical: HistoricalMatch,
) -> bool:
    """
    Tarihsel maçın market profilinin güncel maça
    yeterince benzeyip benzemediğini kontrol eder.

    Market profili odds değerlerinden bağımsızdır.
    """

    if not CONFIG.use_market_profile_pool:
        return False

    current_profile = current.market_profile
    historical_profile = historical.record.market_profile

    similarity = market_similarity(
        current_profile,
        historical_profile,
    )

    # Tamamen rastgele bir eşleşme kabul etmemek için
    # temel benzerlik eşiği kullanılır.
    return similarity >= 0.50


def build_market_profile_pool(
    current: MatchRecord,
    historical_matches: List[HistoricalMatch],
) -> List[HistoricalMatch]:
    """
    Güncel maçın market yapısına benzeyen tarihsel
    maçlardan ayrı bir havuz oluşturur.
    """

    pool: List[HistoricalMatch] = []

    for historical in historical_matches:
        if is_market_profile_match(
            current,
            historical,
        ):
            pool.append(historical)

    return pool


def market_profile_statistics(
    pool: List[HistoricalMatch],
) -> Dict[str, int]:
    """
    Market Profile Pool içerisindeki HT/FT sonuçlarının
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


def market_frequency(
    pool: List[HistoricalMatch],
) -> Counter:
    """
    Havuzdaki marketlerin ne sıklıkta görüldüğünü hesaplar.
    """

    counter: Counter = Counter()

    for historical in pool:
        markets = historical.record.market_profile.available_markets

        for market in markets:
            counter[normalize_market_name(market)] += 1

    return counter
