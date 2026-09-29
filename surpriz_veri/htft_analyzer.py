"""
SÜRPRİZ VERİ - HT/FT Analyzer

Tarihsel havuzlarda gerçekleşmiş İY/MS sonuçlarını analiz eder.

Sistem yalnızca 1/2 veya 2/1 aramaz.
Tüm 9 HT/FT kombinasyonunu tarar:

1/1
1/X
1/2
X/1
X/X
X/2
2/1
2/X
2/2
"""

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List

from .models import HistoricalMatch


HTFT_OUTCOMES = (
    "1/1",
    "1/X",
    "1/2",
    "X/1",
    "X/X",
    "X/2",
    "2/1",
    "2/X",
    "2/2",
)


@dataclass
class HTFTStat:
    """
    Tek bir HT/FT sonucunun havuz içerisindeki istatistiği.
    """

    outcome: str
    sample_size: int
    occurrence_count: int

    frequency: float = 0.0


def count_htft_outcomes(
    matches: List[HistoricalMatch],
) -> Counter:
    """
    Tarihsel maçların gerçekleşmiş HT/FT sonuçlarını sayar.
    """

    counter = Counter()

    for historical in matches:
        outcome = historical.record.outcome.ht_ft

        if not outcome:
            continue

        normalized = outcome.strip().upper()

        if normalized in HTFT_OUTCOMES:
            counter[normalized] += 1

    return counter


def analyze_htft_pool(
    matches: List[HistoricalMatch],
) -> Dict[str, HTFTStat]:
    """
    Bir tarihsel havuzdaki tüm HT/FT kombinasyonlarını analiz eder.

    Hiç gerçekleşmemiş kombinasyonlar da 0 adet olarak tutulur.
    Böylece sonuçlar arasında karşılaştırma yapılabilir.
    """

    counts = count_htft_outcomes(matches)

    sample_size = sum(counts.values())

    statistics: Dict[str, HTFTStat] = {}

    for outcome in HTFT_OUTCOMES:
        occurrence_count = counts.get(
            outcome,
            0,
        )

        frequency = (
            occurrence_count / sample_size
            if sample_size > 0
            else 0.0
        )

        statistics[outcome] = HTFTStat(
            outcome=outcome,
            sample_size=sample_size,
            occurrence_count=occurrence_count,
            frequency=frequency,
        )

    return statistics


def analyze_multiple_pools(
    pools: Dict[str, List[HistoricalMatch]],
) -> Dict[str, Dict[str, HTFTStat]]:
    """
    Opening, Closing, Movement, Market Profile ve Kickoff
    gibi birden fazla havuzu ayrı ayrı analiz eder.

    Havuzların sonuçları burada birleştirilmez.
    """

    result: Dict[str, Dict[str, HTFTStat]] = {}

    for pool_name, matches in pools.items():
        result[pool_name] = analyze_htft_pool(
            matches
        )

    return result


def rank_htft_by_frequency(
    statistics: Dict[str, HTFTStat],
) -> List[HTFTStat]:
    """
    HT/FT sonuçlarını tarihsel görülme sıklığına göre sıralar.

    Bu yalnızca istatistiksel sıralamadır.
    Nihai tahmin veya güven skoru değildir.
    """

    return sorted(
        statistics.values(),
        key=lambda item: (
            item.frequency,
            item.occurrence_count,
        ),
        reverse=True,
    )


def surprise_frequency(
    stat: HTFTStat,
) -> float:
    """
    Bir HT/FT sonucunun havuz içerisindeki görülme sıklığını döndürür.

    Bu değer ileride evidence engine tarafından başka kanıtlarla
    birlikte değerlendirilecektir.
    """

    return stat.frequency
