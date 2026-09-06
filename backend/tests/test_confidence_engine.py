from __future__ import annotations

from app.core.config import get_settings
from app.intelligence.confidence_engine import build_confidence_report
from app.schemas.common import DataQuality
from app.schemas.prediction import MarketComparison, OneXTwoProbabilities, SanityFlag
from app.schemas.team import TeamStrengthProfile


def _profile(name: str, quality: DataQuality) -> TeamStrengthProfile:
    return TeamStrengthProfile(
        team_id=name,
        team_name=name,
        overall=0.6,
        attack=0.6,
        defense=0.6,
        form=0.6,
        opponent_adjusted=0.6,
        matches_considered=15 if quality == DataQuality.HIGH else 2,
        data_quality=quality,
        weights_used={},
        notes=[],
    )


def test_confidence_never_exceeds_configured_ceiling():
    settings = get_settings()
    home = _profile("Home", DataQuality.HIGH)
    away = _profile("Away", DataQuality.HIGH)
    ox = OneXTwoProbabilities(home_win=0.99, draw=0.005, away_win=0.005)
    market = MarketComparison(market_available=False)

    report = build_confidence_report(settings, ox, model_agreement=1.0, home_profile=home,
                                      away_profile=away, market_comparison=market, sanity_flags=[])
    assert report.confidence <= settings.max_public_confidence_label


def test_critical_sanity_flag_caps_confidence_low():
    settings = get_settings()
    home = _profile("Home", DataQuality.HIGH)
    away = _profile("Away", DataQuality.HIGH)
    ox = OneXTwoProbabilities(home_win=0.7, draw=0.15, away_win=0.15)
    market = MarketComparison(market_available=False)
    critical_flag = SanityFlag(code="IDENTICAL_TEAM_IDS", severity="critical", message="bad data")

    report = build_confidence_report(settings, ox, model_agreement=0.8, home_profile=home,
                                      away_profile=away, market_comparison=market,
                                      sanity_flags=[critical_flag])
    assert report.confidence < 0.35
    assert any("Critical" in w for w in report.warnings)


def test_insufficient_data_quality_lowers_confidence_and_warns():
    settings = get_settings()
    home = _profile("Home", DataQuality.INSUFFICIENT)
    away = _profile("Away", DataQuality.INSUFFICIENT)
    ox = OneXTwoProbabilities(home_win=0.4, draw=0.3, away_win=0.3)
    market = MarketComparison(market_available=False)

    report = build_confidence_report(settings, ox, model_agreement=0.5, home_profile=home,
                                      away_profile=away, market_comparison=market, sanity_flags=[])
    assert report.data_quality == DataQuality.INSUFFICIENT
    assert any("Insufficient" in w for w in report.warnings)
