"""
Prediction Engine (spec section 7 / 22).

The single orchestration point that turns a `MatchRawDataset` into a
complete `MatchPrediction`: team strength -> statistical models ->
ensemble -> scenarios -> surprises -> market cross-check -> sanity check
-> confidence. This is the "Football Intelligence Engine" itself; the
Chat Agent is only ever a natural-language voice layered on top of it
(spec section 2, 12, 22 -- the Golden Rule).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings, TeamStrengthWeights
from app.intelligence import statistical_models as stats
from app.intelligence.confidence_engine import build_confidence_report
from app.intelligence.main_scenario import build_main_scenario
from app.intelligence.market_blend import blend_with_market, build_rationale, model_weight
from app.intelligence.market_cross_check import cross_check
from app.intelligence.sanity_engine import SanityContradictionEngine
from app.intelligence.scenario_engine import build_scenarios
from app.intelligence.surprise_engine import rank_surprises
from app.intelligence.team_strength import TeamStrengthEngine
from app.providers.models import MatchRawDataset
from app.schemas.common import DataQuality
from app.schemas.prediction import MatchPrediction


MIN_SPLIT_SAMPLE = 5
MAX_SPLIT_SAMPLE = 10
MAX_OVERALL_SAMPLE = 20


def _by_date(matches):
    return sorted(matches, key=lambda m: m.date, reverse=True)


def balanced_team_data(home_data, away_data):
    """İki takımı AYNI maç sayısıyla kıyaslar (adil karşılaştırma).

    Genel form: ikisinde de en yeni N maç (N = ikisinin de sahip olduğu en küçük sayı, en çok 20).
    İç saha / deplasman: ev sahibinin iç saha maçları ile deplasman takımının deplasman maçları
    aynı sayıda alınır (en çok 10). Bu sayı 5'ten azsa ayrım hiç kullanılmaz.
    """
    n_all = min(len(home_data.recent_matches), len(away_data.recent_matches), MAX_OVERALL_SAMPLE)
    h_home = _by_date(home_data.recent_matches_home)
    a_away = _by_date(away_data.recent_matches_away)
    n_split = min(len(h_home), len(a_away), MAX_SPLIT_SAMPLE)
    use_split = n_split >= MIN_SPLIT_SAMPLE

    def rebuild(data, own_home, own_away):
        return data.model_copy(update={
            "recent_matches": _by_date(data.recent_matches)[:n_all],
            "recent_matches_home": own_home[:n_split] if use_split else [],
            "recent_matches_away": own_away[:n_split] if use_split else [],
        })

    return (
        rebuild(home_data, h_home, _by_date(home_data.recent_matches_away)),
        rebuild(away_data, _by_date(away_data.recent_matches_home), a_away),
    )


class PredictionEngine:
    def __init__(self, settings: Settings, team_strength_weights: TeamStrengthWeights):
        self._settings = settings
        self._team_strength_engine = TeamStrengthEngine(team_strength_weights)
        self._sanity_engine = SanityContradictionEngine(settings)

    def analyze(self, dataset: MatchRawDataset) -> MatchPrediction:
        settings = self._settings

        home_data, away_data = balanced_team_data(dataset.home_team_data, dataset.away_team_data)
        home_profile = self._team_strength_engine.compute_profile(home_data)
        away_profile = self._team_strength_engine.compute_profile(away_data)

        expected_goals = stats.compute_expected_goals(home_profile, away_profile)

        score_matrix = stats.build_score_matrix(
            expected_goals.home_xg,
            expected_goals.away_xg,
            max_goals=settings.poisson_max_goals,
            rho=settings.dixon_coles_rho,
        )
        poisson_1x2 = stats.one_x_two_from_matrix(score_matrix)
        elo_1x2 = stats.elo_style_probabilities(home_profile, away_profile)
        form_1x2 = stats.form_based_probabilities(home_profile, away_profile)

        ensemble = stats.build_ensemble(settings, poisson_1x2, elo_1x2, form_1x2)

        # Market Cross-Check ham model üzerinden hesaplanır (sapma / sürpriz sinyali için).
        market_comparison = cross_check(ensemble.ensemble_1x2, dataset.odds_markets)

        # Nihai 1X2: veri güvenilirliğine göre model + piyasa dengelemesi.
        weight = model_weight(home_profile, away_profile)
        final_1x2 = blend_with_market(ensemble.ensemble_1x2, market_comparison.market_implied, weight)

        btts = stats.btts_probability(score_matrix)
        over_under = stats.over_under_lines(score_matrix)
        double_chance = stats.double_chance_from_1x2(final_1x2)
        draw_no_bet = stats.draw_no_bet_from_1x2(final_1x2)
        asian_handicap = stats.asian_handicap_lines(score_matrix)
        ht_1x2, htft = stats.half_time_full_time(
            expected_goals.home_xg,
            expected_goals.away_xg,
            max_goals=settings.poisson_max_goals,
            rho=settings.dixon_coles_rho,
            ft_override=final_1x2 if market_comparison.market_implied is not None else None,
        )

        rationale = build_rationale(
            home_profile, away_profile, expected_goals, ensemble.ensemble_1x2, final_1x2,
            market_comparison.market_implied, weight,
            h2h=(dataset.h2h.matches if dataset.h2h else []),
        )

        main = build_main_scenario(
            final_1x2, score_matrix, dataset.fixture.home_team.name, dataset.fixture.away_team.name,
        )

        scenarios = build_scenarios(
            final_1x2,
            expected_goals,
            dataset.fixture.home_team.name,
            dataset.fixture.away_team.name,
            market_comparison.market_implied,
            rationale=rationale,
            main_summary=main.summary if main else None,
        )

        # top_n=9: kullanıcı talebi üzerine artık İY/MS'nin TÜM 9 kombinasyonu
        # (1/1, 1/X, 1/2, X/1, X/X, X/2, 2/1, 2/X, 2/2) değerlendirmeye açık --
        # "sadece X/1 veya X/2 ile sınırlı kalınmasın" talebi -- bu da
        # "İY/MS Sürpriz 4'lü" daily-picks motorunun 4 seçimi mümkün olan en
        # geniş kombinasyon/maç çeşitliliğinden seçmesini sağlıyor.
        surprises = rank_surprises(
            htft, final_1x2, home_profile, away_profile, ensemble.model_agreement,
            market_comparison, dataset.odds_markets, top_n=9,
        )

        sanity_flags = self._sanity_engine.run_all_checks(
            dataset, home_profile, away_profile, ensemble.ensemble_1x2, market_comparison
        )

        confidence = build_confidence_report(
            settings,
            final_1x2,
            ensemble.model_agreement,
            home_profile,
            away_profile,
            market_comparison,
            sanity_flags,
        )

        overall_quality = confidence.data_quality
        warnings = list(dataset.missing_fields)
        disclaimers = [
            "[TANI-v4] Bu analiz, istatistiksel modelleme ve mevcut verilerden üretilmiştir. "
            "Garanti bir sonuç değildir ve finansal tavsiye olarak değerlendirilmemelidir.",
        ]
        if overall_quality in (DataQuality.LOW, DataQuality.INSUFFICIENT):
            disclaimers.append(
                "Takımlardan biri veya her ikisi için temel veri sınırlı; bu tahmini "
                "kesin değil, keşfedici olarak değerlendirin."
            )
        if SanityContradictionEngine.has_critical_flags(sanity_flags):
            disclaimers.append(
                "Kritik veri tutarlılığı sorunları tespit edildi -- bu sonuç güvenilir "
                "sayılmadan önce manuel incelemeyi gerektiriyor."
            )

        return MatchPrediction(
            match_id=dataset.fixture.match_id,
            generated_at=datetime.now(timezone.utc),
            home_team=home_profile,
            away_team=away_profile,
            one_x_two=final_1x2,
            double_chance=double_chance,
            draw_no_bet=draw_no_bet,
            expected_goals=expected_goals,
            score_matrix=[s for s in score_matrix if s.probability >= 0.001],
            btts_yes_probability=btts,
            over_under=over_under,
            asian_handicap=asian_handicap,
            half_time_one_x_two=ht_1x2,
            half_time_full_time=htft,
            scenarios=scenarios,
            surprises=surprises,
            model_contributions=ensemble.contributions,
            confidence=confidence,
            sanity_flags=sanity_flags,
            market_comparison=market_comparison,
            data_quality=overall_quality,
            warnings=warnings,
            disclaimers=disclaimers,
            analysis_rationale=rationale,
            main_scenario=main.model_dump() if main else None,
        )
