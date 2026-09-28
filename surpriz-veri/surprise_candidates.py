"""
SÜRPRİZ VERİ - Surprise Candidates

Tarihsel kanıt skorlarından sürpriz adayları üretir.

Sistem yalnızca 1/2 veya 2/1 aramaz.

Tüm HT/MS kombinasyonlarını değerlendirir:

1/1
1/X
1/2
X/1
X/X
X/2
2/1
2/X
2/2

Amaç:

- Tarihsel desteği ölçmek
- Birden fazla havuzdan destek alan sonuçları belirlemek
- Kullanıcının istediği kadar aday döndürebilmek

Bu modül kesin sonuç veya garanti üretmez.
"""

from dataclasses import dataclass
from typing import List, Optional

from .config import CONFIG
from .models import MatchRecord
from .surprise_scoring import (
    CombinedOutcome,
    filter_reliable_evidence,
    score_htft_pools,
)


@dataclass
class SurpriseCandidateResult:
    """
    Güncel maç için tek sürpriz adayı.
    """

    match_id: str

    home_team: str

    away_team: str

    kickoff_time: Optional[str]

    outcome: str

    score: float

    supporting_pools: List[str]

    sample_sizes: dict

    explanation: str


def _normalize_score(
    score: float,
    maximum: float,
) -> float:
    """
    Skoru 0-100 aralığına dönüştürür.
    """

    if maximum <= 0:
        return 0.0

    normalized = (
        score / maximum
    ) * 100.0

    return max(
        0.0,
        min(
            normalized,
            100.0,
        ),
    )


def _build_explanation(
    item: CombinedOutcome,
) -> str:
    """
    Aday için kısa açıklama oluşturur.
    """

    if not item.evidence:
        return (
            "Yeterli tarihsel kanıt bulunamadı."
        )

    parts = []

    for evidence in item.evidence:

        if (
            evidence.frequency <= 0
            or evidence.sample_size <= 0
        ):
            continue

        percentage = (
            evidence.frequency
            * 100.0
        )

        parts.append(
            (
                f"{evidence.pool_name}: "
                f"{percentage:.1f}% "
                f"({evidence.occurrence_count}/"
                f"{evidence.sample_size})"
            )
        )

    if not parts:
        return (
            "Tarihsel destek sınırlı."
        )

    return (
        " | ".join(parts)
    )


def _sample_sizes(
    item: CombinedOutcome,
) -> dict:
    """
    Her kanıt havuzunun örneklem büyüklüğünü döndürür.
    """

    return {
        evidence.pool_name: (
            evidence.sample_size
        )
        for evidence in item.evidence
    }


def generate_candidates(
    current_match: MatchRecord,
    historical_pools,
    limit: Optional[int] = None,
) -> List[SurpriseCandidateResult]:
    """
    Güncel maç için sürpriz adaylarını üretir.

    limit verilmezse CONFIG.default_result_limit kullanılır.

    limit:

        1  -> 1 aday
        5  -> 5 aday
        10 -> 10 aday
        20 -> 20 aday

    Ancak yalnızca yeterli tarihsel desteğe sahip
    sonuçlar döndürülür.

    Sistem zayıf adayları sırf sayı tamamlamak için
    üretmez.
    """

    if limit is None:
        limit = CONFIG.default_result_limit

    try:
        limit = int(limit)
    except (
        TypeError,
        ValueError,
    ):
        limit = CONFIG.default_result_limit

    limit = max(
        1,
        min(
            limit,
            CONFIG.max_result_limit,
        ),
    )

    scored = score_htft_pools(
        historical_pools
    )

    reliable = (
        filter_reliable_evidence(
            scored
        )
    )

    if not reliable:
        return []

    maximum = max(
        (
            item.weighted_score
            for item in reliable
        ),
        default=0.0,
    )

    candidates = []

    for item in reliable:

        score = _normalize_score(
            item.weighted_score,
            maximum,
        )

        candidates.append(
            SurpriseCandidateResult(
                match_id=(
                    current_match.match_id
                ),

                home_team=(
                    current_match.home_team
                ),

                away_team=(
                    current_match.away_team
                ),

                kickoff_time=(
                    current_match.kickoff_time
                ),

                outcome=item.outcome,

                score=round(
                    score,
                    2,
                ),

                supporting_pools=(
                    item.supporting_pools
                ),

                sample_sizes=(
                    _sample_sizes(item)
                ),

                explanation=(
                    _build_explanation(
                        item
                    )
                ),
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.score
        ),
        reverse=True,
    )

    return candidates[
        :limit
    ]
