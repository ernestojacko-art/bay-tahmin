"""
Data Intelligence Layer (spec section 5).

Responsible for collecting whatever data is available for a fixture,
normalizing it into `MatchRawDataset`, and explicitly recording what is
missing. This module NEVER invents a value for a missing field -- it
records the gap in `missing_fields` so the Confidence Engine can lower
confidence accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import MatchNotFoundError
from app.providers.base import BaseFootballDataProvider
from app.providers.models import MatchRawDataset, TeamRawDataset


REQUIRED_FIELDS_FOR_HIGH_QUALITY = (
    "recent_matches",
    "recent_matches_home",
    "recent_matches_away",
    "standing",
)


@dataclass
class DataCompletenessReport:
    missing_fields: list[str]
    completeness_ratio: float  # 0..1


def _assess_team_completeness(dataset: TeamRawDataset, min_matches: int) -> list[str]:
    missing: list[str] = []
    if len(dataset.recent_matches) < min_matches:
        missing.append(f"{dataset.team.name}: recent_matches < {min_matches}")
    if not dataset.recent_matches_home:
        missing.append(f"{dataset.team.name}: no home-specific recent matches")
    if not dataset.recent_matches_away:
        missing.append(f"{dataset.team.name}: no away-specific recent matches")
    if dataset.standing is None:
        missing.append(f"{dataset.team.name}: no league standing available")
    if dataset.fixture_congestion is None:
        missing.append(f"{dataset.team.name}: no fixture congestion data")

    xg_present = sum(1 for m in dataset.recent_matches if m.xg_for is not None)
    if dataset.recent_matches and xg_present / len(dataset.recent_matches) < 0.5:
        missing.append(f"{dataset.team.name}: xG data sparse or unavailable")

    return missing


class DataIntelligenceLayer:
    """Collects, normalizes, and quality-scores data for a fixture."""

    def __init__(self, provider: BaseFootballDataProvider, min_matches_for_reliable_form: int = 5):
        self._provider = provider
        self._min_matches = min_matches_for_reliable_form

    async def build_match_dataset(self, match_id: str) -> MatchRawDataset:
        fixture = await self._provider.get_fixture(match_id)
        if fixture is None:
            raise MatchNotFoundError(f"Fixture '{match_id}' was not found by provider "
                                      f"'{self._provider.name}'.")

        home_data = await self._provider.get_team_dataset(
            fixture.home_team.team_id, fixture.league_id
        )
        away_data = await self._provider.get_team_dataset(
            fixture.away_team.team_id, fixture.league_id
        )
        h2h = await self._provider.get_h2h(fixture.home_team.team_id, fixture.away_team.team_id)
        odds = await self._provider.get_odds(match_id)

        missing = _assess_team_completeness(home_data, self._min_matches)
        missing += _assess_team_completeness(away_data, self._min_matches)
        if h2h is None or not h2h.matches:
            missing.append("no head-to-head history available")
        if not odds:
            missing.append("no market/odds data available (not required for prediction)")

        return MatchRawDataset(
            fixture=fixture,
            home_team_data=home_data,
            away_team_data=away_data,
            h2h=h2h,
            odds_markets=odds,
            data_sources=[self._provider.name],
            missing_fields=missing,
        )

    def completeness_report(self, dataset: MatchRawDataset) -> DataCompletenessReport:
        # Weight "hard" missing data (recent matches, standings) more than
        # "soft" gaps (xG sparsity, missing odds -- odds are optional by design).
        hard_missing = [
            m
            for m in dataset.missing_fields
            if "odds" not in m and "xG" not in m
        ]
        # crude but explainable completeness heuristic
        total_checks = 10  # 5 checks per team, 2 teams
        ratio = max(0.0, 1.0 - (len(hard_missing) / total_checks))
        return DataCompletenessReport(missing_fields=dataset.missing_fields, completeness_ratio=ratio)
