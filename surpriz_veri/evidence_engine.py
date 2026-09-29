"""
SÜRPRİZ VERİ - Evidence Engine

Farklı tarihsel havuzlardan gelen kanıtları bir araya getirir.

Havuzlar:
    - Opening Odds
    - Closing Odds
    - Odds Movement
    - Market Profile
    - Kickoff Time

Önemli:
Bu modül henüz bahis önerisi üretmez.
Görevi, farklı kanıt kaynaklarını ölçülebilir şekilde
birleştirmeye hazırlamaktır.

Opening ve Closing verileri birbirine karıştırılmaz.
"""

from dataclasses import dataclass, field
from typing import Dict, List

from .htft_analyzer import HTFTStat
from .models import Evidence


DEFAULT_POOL_WEIGHTS = {
    "opening": 1.0,
    "closing": 1.0,
    "movement": 1.0,
    "market_profile": 0.75,
    "kickoff": 0.50,
}


@dataclass
class CombinedEvidence:
    """
    Tek bir HT/FT sonucu için farklı havuzlardan gelen
    kanıtların birleşik görünümü.
    """

    outcome: str

    evidences: List[Evidence] = field(
        default_factory=list
    )

    weighted_frequency: float = 0.0
    total_weight: float = 0.0
    supporting_pool_count: int = 0


def evidence_from_stat(
    pool_name: str,
    stat: HTFTStat,
    weight: float,
) -> Evidence:
    """
    HTFTStat nesnesini standart Evidence nesnesine çevirir.
    """

    return Evidence(
        pool_name=pool_name,
        outcome=stat.outcome,
        sample_size=stat.sample_size,
        occurrence_count=stat.occurrence_count,
        frequency=stat.frequency,
        weight=weight,
        details={
            "source": "historical_htft",
        },
    )


def collect_evidence(
    pool_statistics: Dict[str, Dict[str, HTFTStat]],
    outcome: str,
    pool_weights: Dict[str, float] | None = None,
) -> List[Evidence]:
    """
    Belirli bir HT/FT sonucu için tüm havuzlardan gelen
    kanıtları ayrı ayrı toplar.

    Örneğin 1/2 sonucu için:

        opening  -> ayrı evidence
        closing  -> ayrı evidence
        movement -> ayrı evidence
        market   -> ayrı evidence
        kickoff  -> ayrı evidence

    Burada sonuçlar henüz tek değere indirgenmez.
    """

    weights = pool_weights or DEFAULT_POOL_WEIGHTS

    evidences: List[Evidence] = []

    for pool_name, statistics in pool_statistics.items():
        stat = statistics.get(outcome)

        if stat is None:
            continue

        weight = weights.get(
            pool_name,
            1.0,
        )

        evidences.append(
            evidence_from_stat(
                pool_name=pool_name,
                stat=stat,
                weight=weight,
            )
        )

    return evidences


def combine_evidence(
    evidences: List[Evidence],
) -> CombinedEvidence:
    """
    Aynı HT/FT sonucu için farklı kanıtlardan
    ağırlıklı bir frekans oluşturur.

    Bu bir 'tahmin olasılığı' değildir.

    Yalnızca mevcut tarihsel kanıtların birleşik
    istatistiksel görünümüdür.
    """

    if not evidences:
        return CombinedEvidence(
            outcome="",
        )

    outcome = evidences[0].outcome

    weighted_sum = 0.0
    total_weight = 0.0
    supporting_pool_count = 0

    for evidence in evidences:
        if evidence.sample_size <= 0:
            continue

        weighted_sum += (
            evidence.frequency
            * evidence.weight
        )

        total_weight += evidence.weight

        if evidence.occurrence_count > 0:
            supporting_pool_count += 1

    weighted_frequency = (
        weighted_sum / total_weight
        if total_weight > 0
        else 0.0
    )

    return CombinedEvidence(
        outcome=outcome,
        evidences=evidences,
        weighted_frequency=weighted_frequency,
        total_weight=total_weight,
        supporting_pool_count=supporting_pool_count,
    )


def combine_all_outcomes(
    pool_statistics: Dict[str, Dict[str, HTFTStat]],
    outcomes: List[str],
    pool_weights: Dict[str, float] | None = None,
) -> Dict[str, CombinedEvidence]:
    """
    Tüm HT/FT sonuçları için birleşik kanıt görünümü oluşturur.
    """

    result: Dict[str, CombinedEvidence] = {}

    for outcome in outcomes:
        evidences = collect_evidence(
            pool_statistics=pool_statistics,
            outcome=outcome,
            pool_weights=pool_weights,
        )

        result[outcome] = combine_evidence(
            evidences
        )

    return result


def rank_combined_evidence(
    combined: Dict[str, CombinedEvidence],
) -> List[CombinedEvidence]:
    """
    Birleşik tarihsel kanıt görünümünü sıralar.

    Bu sıralama nihai bahis tavsiyesi değildir.
    """

    return sorted(
        combined.values(),
        key=lambda item: (
            item.weighted_frequency,
            item.supporting_pool_count,
        ),
        reverse=True,
    )
