"""
Confidence Engine (spec section 11).

Aggregates probability, data quality, model agreement, market agreement
(if any), and sanity warnings into a single, honest confidence report.
The system must never claim 100% certainty or a "guaranteed" outcome --
`max_public_confidence_label` in settings enforces a hard ceiling.
"""
from __future__ import annotations

from app.core.config import Settings
from app.schemas.common import DataQuality, RiskLevel
from app.schemas.prediction import ConfidenceReport, MarketComparison, OneXTwoProbabilities, SanityFlag
from app.schemas.team import TeamStrengthProfile

_DQ_SCORE = {
    DataQuality.HIGH: 1.0,
    DataQuality.MEDIUM: 0.72,
    DataQuality.LOW: 0.45,
    DataQuality.INSUFFICIENT: 0.15,
}

_DQ_ORDER = [DataQuality.INSUFFICIENT, DataQuality.LOW, DataQuality.MEDIUM, DataQuality.HIGH]


def _combined_data_quality(home: TeamStrengthProfile, away: TeamStrengthProfile) -> DataQuality:
    idx = min(_DQ_ORDER.index(home.data_quality), _DQ_ORDER.index(away.data_quality))
    return _DQ_ORDER[idx]


def build_confidence_report(
    settings: Settings,
    ensemble_probs: OneXTwoProbabilities,
    model_agreement: float,
    home_profile: TeamStrengthProfile,
    away_profile: TeamStrengthProfile,
    market_comparison: MarketComparison,
    sanity_flags: list[SanityFlag],
) -> ConfidenceReport:
    warnings: list[str] = []

    favorite_prob = max(ensemble_probs.home_win, ensemble_probs.draw, ensemble_probs.away_win)
    dq = _combined_data_quality(home_profile, away_profile)
    dq_score = _DQ_SCORE[dq]

    market_agreement = None
    if market_comparison.market_available and market_comparison.model_vs_market_divergence is not None:
        market_agreement = round(max(0.0, 1 - market_comparison.model_vs_market_divergence), 4)

    base_confidence = (favorite_prob * 0.45) + (dq_score * 0.30) + (model_agreement * 0.25)

    critical_flags = [f for f in sanity_flags if f.severity == "critical"]
    warning_flags = [f for f in sanity_flags if f.severity == "warning"]

    if critical_flags:
        base_confidence = min(base_confidence, settings.sanity_min_confidence_after_contradiction * 0.6)
        warnings.append(
            "Kritik tutarlılık sorunu tespit edildi -- güven düzeyi belirgin şekilde "
            "sınırlandırıldı. Bu sonuç banko/garanti bir seçim olarak sunulmamalı."
        )
    elif warning_flags:
        base_confidence = min(base_confidence, settings.sanity_min_confidence_after_contradiction + 0.2)
        warnings.append(
            "Model çıktısı piyasa konsensüsünden belirgin şekilde ayrışıyor ya da küçük bir "
            "örnekleme dayanıyor. İnceleme yapılana kadar azaltılmış güvenle değerlendirin."
        )

    if dq == DataQuality.INSUFFICIENT:
        warnings.append("Temel veri yetersiz -- bu tahmini yalnızca keşfedici olarak değerlendirin.")

    confidence = min(settings.max_public_confidence_label, max(0.05, base_confidence))

    if confidence >= 0.75:
        risk = RiskLevel.LOW
    elif confidence >= 0.5:
        risk = RiskLevel.MEDIUM
    else:
        risk = RiskLevel.HIGH

    for f in sanity_flags:
        warnings.append(f"[{f.severity.upper()}] {f.message}")

    return ConfidenceReport(
        probability_home_favorite=round(favorite_prob, 4),
        confidence=round(confidence, 4),
        risk=risk,
        data_quality=dq,
        model_agreement=round(model_agreement, 4),
        market_agreement=market_agreement,
        warnings=warnings,
    )
