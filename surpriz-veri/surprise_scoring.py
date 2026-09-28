"""
SÜRPRİZ VERİ - Surprise Scoring

Farklı tarihsel kanıt havuzlarından gelen HT/MS
sonuçlarını birleştirir.

Havuzlar:

1. Opening Odds
2. Closing Odds
3. Odds Movement
4. Market Profile
5. Kickoff Time

Önemli:

- Havuzlar ayrı tutulur.
- Opening ile Closing birbirine karıştırılmaz.
- Movement ayrı kanıttır.
- Sonuç doğrudan "kesin tahmin" değildir.
- Üretilen skor tarihsel desteği temsil eder.
"""

from dataclasses import dataclass
from typing import Dict, List

from .config import CONFIG
from .htft_analyzer import analyze_htft_pools
from .models import Evidence


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


DEFAULT_POOL_WEIGHTS = {
    "opening": 1.00,
    "closing": 1.00,
    "movement": 1.00,
    "market_profile": 0.75,
    "kickoff": 0.50,
}


@dataclass
class CombinedOutcome:
    """
    Bir HT/MS sonucunun tüm havuzlardan
    gelen birleşik tarihsel desteği.
    """

    outcome: str

    weighted_score: float

    supporting_pools: List[str]

    evidence: List[Evidence]


def _safe_frequency(
    value,
) -> float:
    """
    Frekansı güvenli şekilde float'a çevirir.
    """

    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0

    if value < 0:
        return 0.0

    if value > 1:
        return 1.0

    return value


def _pool_weight(
    pool_name: str,
) -> float:
    """
    Havuz ağırlığını döndürür.

    Config'de havuz kapatılmışsa ağırlık sıfırdır.
    """

    if pool_name == "opening":
        return (
            DEFAULT_POOL_WEIGHTS["opening"]
            if CONFIG.use_opening_pool
            else 0.0
        )

    if pool_name == "closing":
        return (
            DEFAULT_POOL_WEIGHTS["closing"]
            if CONFIG.use_closing_pool
            else 0.0
        )

    if pool_name == "movement":
        return (
            DEFAULT_POOL_WEIGHTS["movement"]
            if CONFIG.use_movement_pool
            else 0.0
        )

    if pool_name == "market_profile":
        return (
            DEFAULT_POOL_WEIGHTS["market_profile"]
            if CONFIG.use_market_profile_pool
            else 0.0
        )

    if pool_name == "kickoff":
        return (
            DEFAULT_POOL_WEIGHTS["kickoff"]
            if CONFIG.use_kickoff_pool
            else 0.0
        )

    return 0.0


def _extract_frequency(
    item,
) -> float:
    """
    htft_analyzer çıktısından frekans değerini
    mümkün olduğunca güvenli şekilde çıkarır.

    Analyzer çıktısı dict veya Evidence olabilir.
    """

    if isinstance(item, Evidence):
        return _safe_frequency(
            item.frequency
        )

    if isinstance(item, dict):
        return _safe_frequency(
            item.get(
                "frequency",
                0.0,
            )
        )

    frequency = getattr(
        item,
        "frequency",
        0.0,
    )

    return _safe_frequency(
        frequency
    )


def _extract_sample_size(
    item,
) -> int:
    """
    Analyzer çıktısından örneklem büyüklüğünü çıkarır.
    """

    if isinstance(item, Evidence):
        return int(
            item.sample_size
        )

    if isinstance(item, dict):
        try:
            return int(
                item.get(
                    "sample_size",
                    0,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return 0

    try:
        return int(
            getattr(
                item,
                "sample_size",
                0,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0


def _extract_occurrence_count(
    item,
) -> int:
    """
    Analyzer çıktısından gerçekleşme sayısını çıkarır.
    """

    if isinstance(item, Evidence):
        return int(
            item.occurrence_count
        )

    if isinstance(item, dict):
        try:
            return int(
                item.get(
                    "occurrence_count",
                    0,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return 0

    try:
        return int(
            getattr(
                item,
                "occurrence_count",
                0,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0


def build_evidence(
    pool_name: str,
    outcome: str,
    item,
) -> Evidence:
    """
    Bir havuzdaki tek HT/MS sonucunu Evidence
    nesnesine dönüştürür.
    """

    sample_size = _extract_sample_size(
        item
    )

    occurrence_count = _extract_occurrence_count(
        item
    )

    frequency = _extract_frequency(
        item
    )

    weight = _pool_weight(
        pool_name
    )

    return Evidence(
        pool_name=pool_name,

        outcome=outcome,

        sample_size=sample_size,

        occurrence_count=occurrence_count,

        frequency=frequency,

        weight=weight,

        details={
            "source": "historical_pool",
        },
    )


def combine_evidence(
    pool_statistics: Dict[str, Dict[str, object]],
) -> List[CombinedOutcome]:
    """
    Tüm havuzların HT/MS kanıtlarını birleştirir.

    Her sonuç için:

        weighted_score =
            Σ(frequency × pool_weight)

    hesaplanır.

    Bu değer kesin olasılık değildir.
    """

    combined: Dict[
        str,
        CombinedOutcome
    ] = {}

    for outcome in HTFT_OUTCOMES:

        evidence_list: List[
            Evidence
        ] = []

        weighted_score = 0.0

        supporting_pools = []

        for pool_name, statistics in (
            pool_statistics.items()
        ):

            if not isinstance(
                statistics,
                dict,
            ):
                continue

            item = statistics.get(
                outcome
            )

            if item is None:
                continue

            evidence = build_evidence(
                pool_name,
                outcome,
                item,
            )

            if evidence.weight <= 0:
                continue

            evidence_list.append(
                evidence
            )

            weighted_score += (
                evidence.frequency
                * evidence.weight
            )

            if (
                evidence.frequency > 0
                and evidence.sample_size > 0
            ):
                supporting_pools.append(
                    pool_name
                )

        combined[outcome] = CombinedOutcome(
            outcome=outcome,

            weighted_score=weighted_score,

            supporting_pools=(
                supporting_pools
            ),

            evidence=evidence_list,
        )

    return sorted(
        combined.values(),
        key=lambda item: (
            item.weighted_score
        ),
        reverse=True,
    )


def score_htft_pools(
    historical_pools,
) -> List[CombinedOutcome]:
    """
    HistoricalPools nesnesini analiz eder ve
    HT/MS sonuçlarını birleşik tarihsel destek
    skoruna göre sıralar.

    Nihai bahis tahmini değildir.
    """

    pool_statistics = (
        analyze_htft_pools(
            historical_pools
        )
    )

    return combine_evidence(
        pool_statistics
    )


def minimum_sample_ok(
    evidence: Evidence,
) -> bool:
    """
    Bir kanıtın minimum tarihsel örneklem
    şartını karşılayıp karşılamadığını kontrol eder.
    """

    return (
        evidence.sample_size
        >= CONFIG.min_historical_samples
    )


def filter_reliable_evidence(
    combined: List[CombinedOutcome],
) -> List[CombinedOutcome]:
    """
    Minimum tarihsel örneklem şartını karşılayan
    HT/MS sonuçlarını bırakır.

    Hiçbir sonucu sırf liste dolsun diye eklemez.
    """

    filtered = []

    for item in combined:

        valid_evidence = [
            evidence
            for evidence in item.evidence
            if minimum_sample_ok(
                evidence
            )
        ]

        if not valid_evidence:
            continue

        filtered.append(
            CombinedOutcome(
                outcome=item.outcome,

                weighted_score=(
                    item.weighted_score
                ),

                supporting_pools=(
                    item.supporting_pools
                ),

                evidence=valid_evidence,
            )
        )

    return filtered
