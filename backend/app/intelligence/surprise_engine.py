"""
Surprise Intelligence Engine (spec section 8).

A "surprise" is not simply the least-likely outcome -- it's a football-
plausible HT/FT combination that diverges from consensus expectation.
Routine 1/1, X/X, 2/2 combinations are explicitly excluded from being
ranked as surprises (spec: "Rutin 1/1, X/X ve 2/2 sonuçlarını otomatik
olarak sürpriz diye sıralama").

Each candidate is scored across six independent dimensions and combined
into a composite score, rather than ranked purely by raw probability.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import DataQuality, RiskLevel
from app.schemas.prediction import HalfTimeFullTimeProbabilities, SurpriseCandidate
from app.schemas.team import TeamStrengthProfile

ROUTINE_COMBINATIONS = {"1/1", "X/X", "2/2"}

_DESCRIPTIONS = {
    "1/X": "Home team leads at half-time but the game is pegged back to a draw.",
    "1/2": "Home team leads at half-time but the away side completes a comeback win.",
    "X/1": "Level at half-time, home team pulls away in the second half.",
    "X/2": "Level at half-time, away team takes control after the break.",
    "2/1": "Away team leads at half-time but the home side turns it around.",
    "2/X": "Away team leads at half-time but the game finishes level.",
}


@dataclass
class _Weights:
    plausibility: float = 0.20
    upset_potential: float = 0.20
    tactical_support: float = 0.15
    statistical_support: float = 0.20
    data_quality: float = 0.15
    risk_penalty: float = 0.10


def _risk_level(composite: float, data_quality_score: float) -> RiskLevel:
    if data_quality_score < 0.4 or composite < 0.08:
        return RiskLevel.HIGH
    if composite < 0.18:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _data_quality_to_score(dq: DataQuality) -> float:
    return {
        DataQuality.HIGH: 1.0,
        DataQuality.MEDIUM: 0.7,
        DataQuality.LOW: 0.4,
        DataQuality.INSUFFICIENT: 0.15,
    }[dq]


def rank_surprises(
    htft: HalfTimeFullTimeProbabilities,
    home_profile: TeamStrengthProfile,
    away_profile: TeamStrengthProfile,
    model_agreement: float,
    top_n: int = 3,
) -> list[SurpriseCandidate]:
    weights = _Weights()
    dq_score = min(
        _data_quality_to_score(home_profile.data_quality),
        _data_quality_to_score(away_profile.data_quality),
    )

    strength_gap = abs(home_profile.opponent_adjusted - away_profile.opponent_adjusted)

    candidates: list[SurpriseCandidate] = []
    for combo, probability in htft.matrix.items():
        if combo in ROUTINE_COMBINATIONS:
            continue

        # Plausibility: probability itself, but compressed so extremely
        # unlikely combinations don't dominate just for being numerically
        # nonzero.
        plausibility = min(1.0, probability * 6)

        # Upset potential: higher when the team that "comes from behind"
        # (second leg of the combo) is the weaker side on paper.
        ht_side, ft_side = combo.split("/")
        comes_from_behind_is_home = ft_side == "1" and ht_side != "1"
        comes_from_behind_is_away = ft_side == "2" and ht_side != "2"
        if comes_from_behind_is_home:
            upset_potential = min(1.0, max(0.0, 0.5 + (away_profile.opponent_adjusted - home_profile.opponent_adjusted)))
        elif comes_from_behind_is_away:
            upset_potential = min(1.0, max(0.0, 0.5 + (home_profile.opponent_adjusted - away_profile.opponent_adjusted)))
        else:
            # X/1, X/2, 1/X, 2/X: momentum-shift combos; potential scales with strength gap
            upset_potential = min(1.0, 0.35 + strength_gap)

        # Tactical/match-scenario support: teams with meaningfully different
        # attack/defense shapes make swing-scenarios more tactically credible.
        attack_defense_asymmetry = abs(
            (home_profile.attack - home_profile.defense) - (away_profile.attack - away_profile.defense)
        )
        tactical_support = min(1.0, 0.4 + attack_defense_asymmetry)

        # Statistical model support: how much the ensemble models agree
        # this fixture is competitive/swingy (low agreement -> higher
        # surprise credibility, since disagreement often reflects genuine
        # match volatility rather than pure model noise -- but this is
        # capped, since a HIGH surprise score should never rest on
        # unreliable modeling alone; data_quality is scored separately).
        statistical_support = min(1.0, max(0.1, 1 - model_agreement + 0.3))

        composite = (
            plausibility * weights.plausibility
            + upset_potential * weights.upset_potential
            + tactical_support * weights.tactical_support
            + statistical_support * weights.statistical_support
            + dq_score * weights.data_quality
        )
        risk = _risk_level(composite, dq_score)
        if risk == RiskLevel.HIGH:
            composite *= 1 - weights.risk_penalty

        confidence = round(min(0.85, composite + dq_score * 0.15), 4)  # never over-claim certainty

        candidates.append(
            SurpriseCandidate(
                combination=combo,
                description=_DESCRIPTIONS.get(combo, f"Half-time/full-time swing: {combo}"),
                plausibility=round(plausibility, 4),
                upset_potential=round(upset_potential, 4),
                tactical_support=round(tactical_support, 4),
                statistical_support=round(statistical_support, 4),
                data_quality=round(dq_score, 4),
                confidence=confidence,
                risk=risk,
                composite_score=round(composite, 4),
            )
        )

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    return candidates[:top_n]
