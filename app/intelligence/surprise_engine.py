"""
Surprise Intelligence Engine (spec section 8).

A "surprise" is not simply the least-likely outcome -- it's a football-
plausible HT/FT combination that diverges from consensus expectation.
Routine 1/1, X/X, 2/2 combinations are explicitly excluded from being
ranked as surprises (spec: "Rutin 1/1, X/X ve 2/2 sonuçlarını otomatik
olarak sürpriz diye sıralama").

CRITICAL DESIGN RULE (spec section 20): a "surprise" is fundamentally
defined as a MARKET/MODEL disagreement -- not merely internal model
uncertainty. If no betting market exists yet for a fixture, there is
nothing for the model to diverge FROM, so that fixture cannot be scored
as a surprise at all (rank_surprises returns an empty list for it).
This also means raw ensemble disagreement (model_agreement) is no longer
used as a reward signal on its own -- disagreement caused by sparse data
is noise, not insight, and rewarding it previously caused obscure/
low-liquidity fixtures to dominate surprise rankings.

Each candidate is scored across six independent dimensions and combined
into a composite score, rather than ranked purely by raw probability.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import DataQuality, RiskLevel
from app.schemas.prediction import HalfTimeFullTimeProbabilities, MarketComparison, SurpriseCandidate
from app.schemas.team import TeamStrengthProfile

ROUTINE_COMBINATIONS = {"1/1", "X/X", "2/2"}

_DESCRIPTIONS = {
    "1/X": "İlk yarı ev sahibi önde, ancak maç beraberlikle sonuçlanıyor.",
    "1/2": "İlk yarı ev sahibi önde, ancak deplasman takımı geri dönüp maçı kazanıyor.",
    "X/1": "İlk yarı berabere, ikinci yarıda ev sahibi öne geçiyor.",
    "X/2": "İlk yarı berabere, ikinci yarıda deplasman takımı kontrolü ele alıyor.",
    "2/1": "İlk yarı deplasman önde, ancak ev sahibi maçı çeviriyor.",
    "2/X": "İlk yarı deplasman önde, ancak maç beraberlikle bitiyor.",
}


@dataclass
class _Weights:
    plausibility: float = 0.20
    market_divergence: float = 0.30
    tactical_support: float = 0.15
    statistical_support: float = 0.10
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
    one_x_two: OneXTwoProbabilities,
    home_profile: TeamStrengthProfile,
    away_profile: TeamStrengthProfile,
    model_agreement: float,
    market_comparison: MarketComparison,
    top_n: int = 3,
) -> list[SurpriseCandidate]:
    # No open betting market -> no "market disagreement" can be measured,
    # so this fixture cannot be ranked as a surprise (spec section 20/27:
    # never present a claim the data cannot support). The match's normal
    # prediction/analysis remains available elsewhere; it simply will not
    # appear in surprise rankings.
    if not market_comparison.market_available or market_comparison.market_implied is None:
        return []

    weights = _Weights()
    dq_score = min(
        _data_quality_to_score(home_profile.data_quality),
        _data_quality_to_score(away_profile.data_quality),
    )
    # Fixtures with genuinely insufficient sample size (spec section 26)
    # should not headline high-confidence surprise picks at all.
    if dq_score < 0.4:
        return []

    market_implied = market_comparison.market_implied
    # Model's own FINAL-TIME (1X2) probability per side, used to measure
    # genuine model-vs-market disagreement on the *same* marginal outcome
    # -- comparing this against the market's marginal 1X2, not against the
    # much smaller joint HT/FT cell probability (that would be a scale
    # mismatch: a 9-cell joint probability is never directly comparable to
    # a 3-way marginal one).
    model_ft_prob = {"1": one_x_two.home_win, "X": one_x_two.draw, "2": one_x_two.away_win}
    market_ft_prob = {"1": market_implied.home_win, "X": market_implied.draw, "2": market_implied.away_win}
    strength_gap = abs(home_profile.opponent_adjusted - away_profile.opponent_adjusted)

    candidates: list[SurpriseCandidate] = []
    for combo, probability in htft.matrix.items():
        if combo in ROUTINE_COMBINATIONS:
            continue

        # Plausibility: probability itself, but compressed so extremely
        # unlikely combinations don't dominate just for being numerically
        # nonzero.
        plausibility = min(1.0, probability * 6)

        ht_side, ft_side = combo.split("/")
        # Real market/model disagreement on the final-result side of this
        # combination -- this is the actual definition of "surprise" per
        # spec section 20: how much more (or less) likely the model thinks
        # this final result is, versus what the market implies.
        market_divergence = min(1.0, max(0.0, model_ft_prob[ft_side] - market_ft_prob[ft_side]) * 2.5)

        # Tactical/match-scenario support: teams with meaningfully different
        # attack/defense shapes make swing-scenarios more tactically credible.
        attack_defense_asymmetry = abs(
            (home_profile.attack - home_profile.defense) - (away_profile.attack - away_profile.defense)
        )
        tactical_support = min(1.0, 0.4 + attack_defense_asymmetry)

        # Statistical model support: ensemble agreement is now a MILD,
        # capped modifier -- not a primary driver -- so sparse-data noise
        # can no longer manufacture an artificially high surprise score.
        statistical_support = min(1.0, max(0.2, 0.5 + (1 - model_agreement) * 0.3))

        composite = (
            plausibility * weights.plausibility
            + market_divergence * weights.market_divergence
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
                description=_DESCRIPTIONS.get(combo, f"İlk yarı/maç sonucu değişimi: {combo}"),
                plausibility=round(plausibility, 4),
                upset_potential=round(market_divergence, 4),
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
