"""
Composite Football Data Provider.

Lets a production deployment mix and match specialized adapters (a
fixtures/program feed from one vendor, team statistics from another,
match statistics/H2H from a third, odds from a fourth) behind the single
`BaseFootballDataProvider` interface the rest of the app depends on.

Any concern left unconfigured (`None`) raises `ProviderNotConfiguredError`
when called -- it is never silently replaced with fabricated data. Odds
are the one exception: no odds provider configured simply means "no
market data available", which is a valid, expected state (see
`app/intelligence/market_cross_check.py`).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.exceptions import ProviderNotConfiguredError
from app.providers.base import BaseFootballDataProvider
from app.providers.interfaces import (
    FixtureProviderInterface,
    MatchProgramProviderInterface,
    MatchStatisticsProviderInterface,
    OddsProviderInterface,
    TeamStatisticsProviderInterface,
)
from app.providers.models import Fixture, H2HRecord, OddsMarket, StandingsEntry, TeamRawDataset


class CompositeFootballDataProvider(BaseFootballDataProvider):
    """
    Assembles a full `BaseFootballDataProvider` out of independently
    pluggable, single-responsibility adapters.

    Example (wiring done by the deployer, not hard-coded here):

        provider = CompositeFootballDataProvider(
            fixture_provider=MyFixturesApiAdapter(...),
            team_stats_provider=MyStatsApiAdapter(...),
            match_stats_provider=MyStatsApiAdapter(...),   # can be the same instance
            program_provider=MyRssProgramAdapter(...),      # optional
            odds_provider=MyOddsApiAdapter(...),             # optional
        )
    """

    name = "composite"

    def __init__(
        self,
        fixture_provider: Optional[FixtureProviderInterface] = None,
        team_stats_provider: Optional[TeamStatisticsProviderInterface] = None,
        match_stats_provider: Optional[MatchStatisticsProviderInterface] = None,
        program_provider: Optional[MatchProgramProviderInterface] = None,
        odds_provider: Optional[OddsProviderInterface] = None,
    ):
        self._fixture_provider = fixture_provider
        self._team_stats_provider = team_stats_provider
        self._match_stats_provider = match_stats_provider
        self._program_provider = program_provider
        self._odds_provider = odds_provider

    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        if self._fixture_provider is None:
            raise ProviderNotConfiguredError(
                "No fixture provider configured on the composite provider. "
                "Wire a FixtureProviderInterface implementation, or fall back to "
                "the match-program provider via get_scheduled_fixtures()."
            )
        return await self._fixture_provider.get_fixtures(on_date, league_id)

    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        if self._fixture_provider is None:
            raise ProviderNotConfiguredError(
                "No fixture provider configured on the composite provider."
            )
        return await self._fixture_provider.get_fixture(match_id)

    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        if self._team_stats_provider is None:
            raise ProviderNotConfiguredError(
                "No team statistics provider configured on the composite provider."
            )
        return await self._team_stats_provider.get_team_dataset(team_id, league_id)

    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        if self._team_stats_provider is None:
            raise ProviderNotConfiguredError(
                "No team statistics provider configured on the composite provider."
            )
        return await self._team_stats_provider.get_standings(league_id)

    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        if self._match_stats_provider is None:
            raise ProviderNotConfiguredError(
                "No match statistics provider configured on the composite provider."
            )
        return await self._match_stats_provider.get_h2h(team_a_id, team_b_id, limit)

    async def get_scheduled_program(self, league_id: str, days_ahead: int = 7) -> list[Fixture]:
        """Extra convenience method (not part of the base interface) for program/RSS feeds."""
        if self._program_provider is None:
            raise ProviderNotConfiguredError(
                "No match-program provider configured on the composite provider."
            )
        return await self._program_provider.get_scheduled_fixtures(league_id, days_ahead)

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        # Odds remain optional by design -- no configured odds provider is
        # a valid state, not an error.
        if self._odds_provider is None:
            return []
        return await self._odds_provider.get_odds(match_id)

    async def health_check(self) -> bool:
        return self._fixture_provider is not None and self._team_stats_provider is not None
