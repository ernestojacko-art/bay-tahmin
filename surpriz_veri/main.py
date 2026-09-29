"""
SÜRPRİZ VERİ - Main

Bağımsız Sürpriz Veri analiz motorunun giriş noktası.

Bu modül mevcut Bay Tahmin uygulamasına müdahale etmez.
"""

from typing import Dict, List

from .evidence_engine import (
    CombinedEvidence,
    combine_all_outcomes,
)
from .historical_matcher import (
    HistoricalPools,
    build_historical_pools,
    pool_matches,
)
from .htft_analyzer import (
    HTFT_OUTCOMES,
    analyze_multiple_pools,
)
from .models import HistoricalMatch, MatchRecord


def analyze_match(
    current_match: MatchRecord,
    historical_matches: List[HistoricalMatch],
    kickoff_tolerance_minutes: int = 60,
) -> Dict[str, object]:
    """
    Bir güncel maçı tarihsel veriler üzerinden analiz eder.

    Analiz aşamaları:

    1. Tarihsel havuzları oluştur.
    2. Her havuzu ayrı ayrı analiz et.
    3. HT/FT sonuçlarını tüm havuzlarda incele.
    4. Kanıtları ayrı ayrı koru.
    5. Birleşik kanıt görünümünü oluştur.

    Henüz canlı veri çekmez ve nihai bahis tavsiyesi üretmez.
    """

    pools: HistoricalPools = build_historical_pools(
        current=current_match,
        historical_matches=historical_matches,
        kickoff_tolerance_minutes=kickoff_tolerance_minutes,
    )

    pool_data = pool_matches(pools)

    pool_statistics = analyze_multiple_pools(
        pool_data
    )

    combined_evidence: Dict[
        str,
        CombinedEvidence,
    ] = combine_all_outcomes(
        pool_statistics=pool_statistics,
        outcomes=list(HTFT_OUTCOMES),
    )

    return {
        "match_id": current_match.match_id,
        "match": {
            "home_team": current_match.home_team,
            "away_team": current_match.away_team,
            "kickoff_time": current_match.kickoff_time,
        },
        "pool_sizes": pools.sizes(),
        "pool_statistics": pool_statistics,
        "combined_evidence": combined_evidence,
    }


def summarize_pool_sizes(
    result: Dict[str, object],
) -> Dict[str, int]:
    """
    Analiz sonucundaki tarihsel havuz büyüklüklerini
    kolay okunabilir şekilde döndürür.
    """

    pool_sizes = result.get(
        "pool_sizes",
        {},
    )

    if not isinstance(pool_sizes, dict):
        return {}

    return {
        str(name): int(size)
        for name, size in pool_sizes.items()
    }


if __name__ == "__main__":
    print(
        "Sürpriz Veri bağımsız analiz motoru hazır."
    )
    print(
        "Canlı veri bağlantısı bir sonraki aşamada eklenecek."
    )
