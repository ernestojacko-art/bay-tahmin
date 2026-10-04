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
from .htft_analyzer import analyze_multiple_pools
from .historical_matcher import pool_matches
from .models import Evidence


HTFT_OUTCOMES = (
    "1/1", "1/X", "1/2",
    "X/1", "X/X", "X/2",
    "2/1", "2/X", "2/2",
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
    outcome: str
    weighted_score: float
    supporting_pools: List[str]
    evidence: List[Evidence]


def _safe_frequency(value) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _pool_weight(pool_name: str) -> float:
    enabled = {
        "opening": CONFIG.use_opening_pool,
        "closing": CONFIG.use_closing_pool,
        "movement": CONFIG.use_movement_pool,
        "market_profile": CONFIG.use_market_profile_pool,
        "kickoff": CONFIG.use_kickoff_pool,
    }
    return DEFAULT_POOL_WEIGHTS.get(pool_name, 0.0) if enabled.get(pool_name, False) else 0.0


def _extract_frequency(item) -> float:
    if isinstance(item, Evidence):
        return _safe_frequency(item.frequency)
    if isinstance(item, dict):
        return _safe_frequency(item.get("frequency", 0.0))
    return _safe_frequency(getattr(item, "frequency", 0.0))


def _extract_sample_size(item) -> int:
    if isinstance(item, Evidence):
        return int(item.sample_size)
    if isinstance(item, dict):
        try:
            return int(item.get("sample_size", 0))
        except (TypeError, ValueError):
            return 0
    try:
        return int(getattr(item, "sample_size", 0))
    except (TypeError, ValueError):
        return 0


def _extract_occurrence_count(item) -> int:
    if isinstance(item, Evidence):
        return int(item.occurrence_count)
    if isinstance(item, dict):
        try:
            return int(item.get("occurrence_count", 0))
        except (TypeError, ValueError):
            return 0
    try:
        return int(getattr(item, "occurrence_count", 0))
    except (TypeError, ValueError):
        return 0


def build_evidence(pool_name: str, outcome: str, item) -> Evidence:
    sample_size = _extract_sample_size(item)
    occurrence_count = _extract_occurrence_count(item)
    frequency = _extract_frequency(item)
    weight = _pool_weight(pool_name)

    return Evidence(
        pool_name=pool_name,
        outcome=outcome,
        sample_size=sample_size,
        occurrence_count=occurrence_count,
        frequency=frequency,
        weight=weight,
        details={"source": "historical_pool"},
    )


def combine_evidence(pool_statistics: Dict[str, Dict[str, object]]) -> List[CombinedOutcome]:
    combined: Dict[str, CombinedOutcome] = {}

    for outcome in HTFT_OUTCOMES:
        evidence_list: List[Evidence] = []
        weighted_score = 0.0
        supporting_pools = []

        for pool_name, statistics in pool_statistics.items():
            if not isinstance(statistics, dict):
                continue

            item = statistics.get(outcome)
            if item is None:
                continue

            evidence = build_evidence(pool_name, outcome, item)

            if evidence.weight <= 0:
                continue

            evidence_list.append(evidence)
            weighted_score += evidence.frequency * evidence.weight

            if evidence.frequency > 0 and evidence.sample_size > 0:
                supporting_pools.append(pool_name)

        combined[outcome] = CombinedOutcome(
            outcome=outcome,
            weighted_score=weighted_score,
            supporting_pools=supporting_pools,
            evidence=evidence_list,
        )

    return sorted(
        combined.values(),
        key=lambda item: item.weighted_score,
        reverse=True,
    )


def score_htft_pools(historical_pools) -> List[CombinedOutcome]:
    """
    HistoricalPools nesnesini analiz eder ve
    HT/MS sonuçlarını birleşik tarihsel destek
    skoruna göre sıralar.

    Nihai bahis tahmini değildir.
    """
    pool_statistics = analyze_multiple_pools(
        pool_matches(historical_pools)
    )
    return combine_evidence(pool_statistics)


def minimum_sample_ok(evidence: Evidence) -> bool:
    return evidence.sample_size >= CONFIG.min_historical_samples


def filter_reliable_evidence(combined: List[CombinedOutcome]) -> List[CombinedOutcome]:
    """Yetersiz örneklemli kanıtları eler.

    weighted_score ve supporting_pools, elenen kanıtı hâlâ içeriyor
    görünmesin diye yalnızca örneklemi yeterli (valid_evidence) kanıtlar
    üzerinden yeniden hesaplanır. Aksi hâlde bir havuz "destekliyor"
    gibi görünüp örneklem yetersizliğinden skora/örnek boyutlarına hiç
    katkı yapmayabilirdi.
    """

    filtered = []

    for item in combined:
        valid_evidence = [
            evidence
            for evidence in item.evidence
            if minimum_sample_ok(evidence)
        ]

        if not valid_evidence:
            continue

        weighted_score = sum(
            evidence.frequency * evidence.weight
            for evidence in valid_evidence
        )

        supporting_pools = [
            evidence.pool_name
            for evidence in valid_evidence
            if evidence.frequency > 0 and evidence.sample_size > 0
        ]

        filtered.append(
            CombinedOutcome(
                outcome=item.outcome,
                weighted_score=weighted_score,
                supporting_pools=supporting_pools,
                evidence=valid_evidence,
            )
        )

    return filtered
