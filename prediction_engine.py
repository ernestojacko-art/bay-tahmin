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
from app.intelligence.market_cross_check import cross_check
from app.intelligence.sanity_engine import SanityContradictionEngine
from app.intelligence.scenario_engine import build_scenarios
from app.intelligence.surprise_engine import rank_surprises
from app.intelligence.team_strength import TeamStrengthEngine
from app.providers.models import MatchRawDataset
from app.schemas.common import DataQuality
from app.schemas.prediction import MatchPrediction


class PredictionEngine:
    def __init__(self, settings: Settings, team_strength_weights: TeamStrengthWeights):
        self._settings = settings
        self._team_strength_engine = TeamStrengthEngine(team_strength_weights)
        self._sanity_engine = SanityContradictionEngine(settings)

    def analyze(self, dataset: MatchRawDataset) -> MatchPrediction:
        settings = self._settings

        home_profile = self._team_strength_engine.compute_profile(dataset.home_team_data)
        away_profile = self._team_strength_engine.compute_profile(dataset.away_team_data)

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

        btts = stats.btts_probability(score_matrix)
        over_under = stats.over_under_lines(score_matrix)
        double_chance = stats.double_chance_from_1x2(ensemble.ensemble_1x2)
        draw_no_bet = stats.draw_no_bet_from_1x2(ensemble.ensemble_1x2)
        asian_handicap = stats.asian_handicap_lines(score_matrix)
        ht_1x2, htft = stats.half_time_full_time(
            expected_goals.home_xg,
            expected_goals.away_xg,
            max_goals=settings.poisson_max_goals,
            rho=settings.dixon_coles_rho,
        )

        scenarios = build_scenarios(
            ensemble.ensemble_1x2,
            expected_goals,
            dataset.fixture.home_team.name,
            dataset.fixture.away_team.name,
        )

        # Market Cross-Check must run BEFORE surprise ranking: a "surprise"
        # is defined as market/model disagreement, so rank_surprises needs
        # this result as an input, not the other way around.
        market_comparison = cross_check(ensemble.ensemble_1x2, dataset.odds_markets)

        surprises = rank_surprises(
            htft, ensemble.ensemble_1x2, home_profile, away_profile, ensemble.model_agreement, market_comparison
        )

        sanity_flags = self._sanity_engine.run_all_checks(
            dataset, home_profile, away_profile, ensemble.ensemble_1x2, market_comparison
        )

        confidence = build_confidence_report(
            settings,
            ensemble.ensemble_1x2,
            ensemble.model_agreement,
            home_profile,
            away_profile,
            market_comparison,
            sanity_flags,
        )

        overall_quality = confidence.data_quality
        warnings = list(dataset.missing_fields)
        disclaimers = [
            "Bu analiz, istatistiksel modelleme ve mevcut verilerden üretilmiştir. "
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
            one_x_two=ensemble.ensemble_1x2,
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
        )
