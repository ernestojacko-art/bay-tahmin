"""
Sanity & Contradiction Engine (spec section 10).

Mandatory guardrail module. Checks for internal inconsistencies (wrong
team/match identifiers, home/away mixups, league mismatches, tiny
samples, calibration red flags) and -- most notably -- contradictions
between the model's output and market consensus, e.g. the model calling
a 7.00-8.00 market outsider a 65-75% "banker" favorite.

If a contradiction is found, downstream code must not present the result
with high confidence or "banko/garanti/kesin" (banker/guaranteed/certain)
language, per spec.
"""
from __future__ import annotations

from app.core.config import Settings
from app.providers.models import MatchRawDataset
from app.schemas.prediction import MarketComparison, OneXTwoProbabilities, SanityFlag
from app.schemas.team import TeamStrengthProfile


def check_identity_consistency(dataset: MatchRawDataset) -> list[SanityFlag]:
    flags: list[SanityFlag] = []
    fixture = dataset.fixture
    if fixture.home_team.team_id == fixture.away_team.team_id:
        flags.append(
            SanityFlag(
                code="IDENTICAL_TEAM_IDS",
                severity="critical",
                message="Ev sahibi ve deplasman takım kimlikleri aynı -- bu fikstür analiz edilemez.",
            )
        )
    if dataset.home_team_data.team.team_id != fixture.home_team.team_id:
        flags.append(
            SanityFlag(
                code="HOME_TEAM_ID_MISMATCH",
                severity="critical",
                message="Ev sahibi takım verisi, fikstürün ev sahibi takım kimliğiyle eşleşmiyor.",
            )
        )
    if dataset.away_team_data.team.team_id != fixture.away_team.team_id:
        flags.append(
            SanityFlag(
                code="AWAY_TEAM_ID_MISMATCH",
                severity="critical",
                message="Deplasman takım verisi, fikstürün deplasman takım kimliğiyle eşleşmiyor.",
            )
        )
    home_league = dataset.home_team_data.team.league_id
    away_league = dataset.away_team_data.team.league_id
    if home_league and away_league and home_league != away_league and fixture.league_id not in (
        home_league,
        away_league,
    ):
        flags.append(
            SanityFlag(
                code="LEAGUE_MISMATCH",
                severity="warning",
                message="Ev sahibi/deplasman takımları, fikstürün lig kimliğinden farklı liglerle ilişkilendirilmiş.",
            )
        )
    return flags


def check_sample_size(home: TeamStrengthProfile, away: TeamStrengthProfile) -> list[SanityFlag]:
    flags: list[SanityFlag] = []
    for profile in (home, away):
        if profile.matches_considered < 3:
            flags.append(
                SanityFlag(
                    code="SMALL_SAMPLE_SIZE",
                    severity="warning",
                    message=(
                        f"{profile.team_name}: yalnızca {profile.matches_considered} son maç "
                        "mevcut -- form/güç tahmini istatistiksel olarak zayıf."
                    ),
                )
            )
    return flags


def implied_probabilities_from_odds(selections: dict[str, float]) -> OneXTwoProbabilities | None:
    """Remove bookmaker overround and return normalized implied probabilities."""
    required = {"1", "X", "2"}
    if not required.issubset(selections.keys()):
        return None
    raw = {k: 1 / v for k, v in selections.items() if v > 0}
    total = sum(raw.values())
    if total == 0:
        return None
    return OneXTwoProbabilities(
        home_win=round(raw["1"] / total, 4),
        draw=round(raw["X"] / total, 4),
        away_win=round(raw["2"] / total, 4),
    )


def check_market_contradiction(
    model_probs: OneXTwoProbabilities,
    market_comparison: MarketComparison,
    threshold: float,
) -> list[SanityFlag]:
    flags: list[SanityFlag] = []
    if not market_comparison.market_available or market_comparison.market_implied is None:
        return flags

    market = market_comparison.market_implied
    pairs = [
        ("home_win", model_probs.home_win, market.home_win),
        ("draw", model_probs.draw, market.draw),
        ("away_win", model_probs.away_win, market.away_win),
    ]
    for label, model_p, market_p in pairs:
        divergence = abs(model_p - market_p)
        if divergence >= threshold:
            severity = "critical" if divergence >= threshold + 0.15 else "warning"
            flags.append(
                SanityFlag(
                    code="MODEL_MARKET_DIVERGENCE",
                    severity=severity,
                    message=(
                        f"Model ile piyasa '{label}' konusunda uyuşmuyor: model={model_p:.2f}, "
                        f"piyasa-ima={market_p:.2f} (fark={divergence:.2f}). "
                        "Bu tutarsızlık incelenene kadar (ev/deplasman karışıklığı, kalibrasyon "
                        "ya da gerçek bir piyasa verimsizliği) bu sonuç yüksek güvenli bir banko "
                        "olarak sunulmamalıdır."
                    ),
                )
            )
    return flags


class SanityContradictionEngine:
    def __init__(self, settings: Settings):
        self._settings = settings

    def run_all_checks(
        self,
        dataset: MatchRawDataset,
        home_profile: TeamStrengthProfile,
        away_profile: TeamStrengthProfile,
        model_probs: OneXTwoProbabilities,
        market_comparison: MarketComparison,
    ) -> list[SanityFlag]:
        flags: list[SanityFlag] = []
        flags += check_identity_consistency(dataset)
        flags += check_sample_size(home_profile, away_profile)
        flags += check_market_contradiction(
            model_probs, market_comparison, self._settings.sanity_market_disagreement_threshold
        )
        return flags

    @staticmethod
    def has_critical_flags(flags: list[SanityFlag]) -> bool:
        return any(f.severity == "critical" for f in flags)
