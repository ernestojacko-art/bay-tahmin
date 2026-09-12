from __future__ import annotations

from app.intelligence.surprise_engine import ROUTINE_COMBINATIONS, rank_surprises
from app.schemas.common import DataQuality
from app.schemas.prediction import HalfTimeFullTimeProbabilities, MarketComparison, OneXTwoProbabilities
from app.schemas.team import TeamStrengthProfile


def _profile(name: str, adjusted: float, matches_considered: int = 15, quality: DataQuality = DataQuality.HIGH) -> TeamStrengthProfile:
    return TeamStrengthProfile(
        team_id=name,
        team_name=name,
        overall=adjusted,
        attack=adjusted,
        defense=adjusted,
        form=adjusted,
        opponent_adjusted=adjusted,
        matches_considered=matches_considered,
        data_quality=quality,
        weights_used={},
        notes=[],
    )


def _market(home=0.4, draw=0.3, away=0.3) -> MarketComparison:
    return MarketComparison(
        market_available=True,
        market_implied=OneXTwoProbabilities(home_win=home, draw=draw, away_win=away),
        model_vs_market_divergence=0.1,
        overround_removed=True,
    )


_NO_MARKET = MarketComparison(market_available=False, notes=["No market/odds data available."])


def _model_1x2(home=0.55, draw=0.25, away=0.20) -> OneXTwoProbabilities:
    return OneXTwoProbabilities(home_win=home, draw=draw, away_win=away)


class _FakeOddsMarket:
    def __init__(self, market_name):
        self.market_name = market_name


# 5DollarFootballAPI'nin gerçek İlk Yarı Maç Sonucu market adı (bkz. five_dollar_provider.py names dict).
_HT_MARKET = [_FakeOddsMarket("İlk Yarı Maç Sonucu (Bet 365)")]
_NO_HT_MARKET: list = []


def test_routine_outcomes_never_ranked_as_surprises():
    # Deliberately give routine combos huge probability to try to force them in.
    matrix = {
        "1/1": 0.5,
        "X/X": 0.2,
        "2/2": 0.1,
        "1/X": 0.05,
        "X/1": 0.05,
        "1/2": 0.03,
        "2/1": 0.03,
        "X/2": 0.02,
        "2/X": 0.02,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.7)
    away = _profile("Away", 0.4)

    surprises = rank_surprises(
        htft, _model_1x2(), home, away, model_agreement=0.8, market_comparison=_market(),
        odds_markets=_HT_MARKET, top_n=6,
    )
    combos = {s.combination for s in surprises}
    assert combos.isdisjoint(ROUTINE_COMBINATIONS)


def test_surprise_candidates_have_valid_composite_scores():
    matrix = {
        "1/1": 0.3, "X/X": 0.15, "2/2": 0.1,
        "1/X": 0.1, "X/1": 0.1, "1/2": 0.08,
        "2/1": 0.07, "X/2": 0.05, "2/X": 0.05,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.6)
    away = _profile("Away", 0.55)

    surprises = rank_surprises(
        htft, _model_1x2(), home, away, model_agreement=0.6, market_comparison=_market(),
        odds_markets=_HT_MARKET, top_n=3,
    )
    assert len(surprises) <= 3
    for s in surprises:
        assert 0.0 <= s.composite_score <= 1.5
        assert 0.0 <= s.confidence <= 0.85

    # Sorted descending by composite score
    scores = [s.composite_score for s in surprises]
    assert scores == sorted(scores, reverse=True)


def test_no_market_data_means_no_surprise_candidates():
    """
    Regresyon testi: canlı ortamda tespit edilen gerçek bir hatayı kapsar --
    bahis piyasası henüz açılmamış bir maç, önceden yanlışlıkla yüksek
    güvenli bir 'sürpriz' olarak listelenebiliyordu. Piyasa yoksa
    market/model uyuşmazlığı da tanımsızdır, dolayısıyla o maç için hiçbir
    sürpriz adayı üretilmemelidir.
    """
    matrix = {
        "1/1": 0.3, "X/X": 0.15, "2/2": 0.1,
        "1/X": 0.1, "X/1": 0.1, "1/2": 0.08,
        "2/1": 0.07, "X/2": 0.05, "2/X": 0.05,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.6)
    away = _profile("Away", 0.55)

    surprises = rank_surprises(
        htft, _model_1x2(), home, away, model_agreement=0.6, market_comparison=_NO_MARKET,
        odds_markets=_HT_MARKET, top_n=3,
    )
    assert surprises == []


def test_low_sample_size_teams_are_excluded_from_surprise_ranking():
    """
    Regresyon testi: yetersiz maç geçmişi olan (örn. rezerv/2. takım)
    ekiplerin, düşük veri kalitesine rağmen yüksek güvenli sürpriz
    adayı olarak öne çıkması engellenmeli (spec §26).
    """
    matrix = {
        "1/1": 0.3, "X/X": 0.15, "2/2": 0.1,
        "1/X": 0.1, "X/1": 0.1, "1/2": 0.08,
        "2/1": 0.07, "X/2": 0.05, "2/X": 0.05,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home II", 0.6, matches_considered=4, quality=DataQuality.LOW)
    away = _profile("Away II", 0.55, matches_considered=4, quality=DataQuality.LOW)

    surprises = rank_surprises(
        htft, _model_1x2(), home, away, model_agreement=0.6, market_comparison=_market(),
        odds_markets=_HT_MARKET, top_n=3,
    )
    assert surprises == []


def test_no_half_time_market_means_no_ht_ft_surprise_candidates():
    """
    Regresyon testi: canlı ortamda tespit edilen gerçek bir hatayı kapsar --
    5DollarFootballAPI'de MS (1X2) piyasası açık olsa bile, ayrı bir İlk
    Yarı Maç Sonucu piyasası yoksa İY/MS kombinasyonu için gerçek bir piyasa
    referansı yoktur. Bu durumda hiçbir İY/MS sürprizi üretilmemelidir.
    """
    matrix = {
        "1/1": 0.3, "X/X": 0.15, "2/2": 0.1,
        "1/X": 0.1, "X/1": 0.1, "1/2": 0.08,
        "2/1": 0.07, "X/2": 0.05, "2/X": 0.05,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.6)
    away = _profile("Away", 0.55)

    surprises = rank_surprises(
        htft, _model_1x2(), home, away, model_agreement=0.6, market_comparison=_market(),
        odds_markets=_NO_HT_MARKET, top_n=3,
    )
    assert surprises == []


def test_market_divergence_drives_the_score_not_internal_noise():
    """
    Regresyon testi: canlı ortamda tespit edilen ölçek uyuşmazlığı hatasını
    kapsar -- market_divergence, İY/MS'nin ortak (joint) olasılığını değil,
    modelin MS (1X2) olasılığını piyasanın MS olasılığıyla karşılaştırmalı.
    Model piyasadan gerçekten ayrışıyorsa skor yüksek, aynı fikirdeyse düşük
    olmalı.
    """
    matrix = {
        "1/1": 0.15, "X/X": 0.1, "2/2": 0.05,
        "1/X": 0.1, "X/1": 0.2, "1/2": 0.1,
        "2/1": 0.1, "X/2": 0.1, "2/X": 0.1,
    }
    htft = HalfTimeFullTimeProbabilities(matrix=matrix)
    home = _profile("Home", 0.6)
    away = _profile("Away", 0.5)

    model_strong_home = _model_1x2(home=0.70, draw=0.15, away=0.15)
    market_agrees = _market(home=0.70, draw=0.15, away=0.15)
    market_disagrees = _market(home=0.20, draw=0.30, away=0.50)

    agree_result = {
        s.combination: s
        for s in rank_surprises(
            htft, model_strong_home, home, away, model_agreement=0.6, market_comparison=market_agrees,
            odds_markets=_HT_MARKET, top_n=9,
        )
    }
    disagree_result = {
        s.combination: s
        for s in rank_surprises(
            htft, model_strong_home, home, away, model_agreement=0.6, market_comparison=market_disagrees,
            odds_markets=_HT_MARKET, top_n=9,
        )
    }
    assert disagree_result["X/1"].composite_score > agree_result["X/1"].composite_score
    assert disagree_result["X/1"].upset_potential > agree_result["X/1"].upset_potential
