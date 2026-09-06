"""
Base provider interface.

Spec section 15 (Provider / Adapter Mimarisi): external football data
sources must never be embedded directly into business logic. Every real
integration (a paid football-data API, an odds API, etc.) implements this
interface. Business logic (the Data Intelligence Layer and everything
above it) only ever depends on `BaseFootballDataProvider`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from app.providers.models import (
    Fixture,
    H2HRecord,
    OddsMarket,
    StandingsEntry,
    TeamRawDataset,
)


class BaseFootballDataProvider(ABC):
    """
    Adapter interface for a football data source.

    Implementations should raise `app.core.exceptions.ProviderError` on
    upstream failure. Returning `None` / an empty list is the correct way
    to signal "this data point is genuinely unavailable" -- it must NOT be
    used to signal a transient failure, which should raise instead.
    """

    name: str = "base"

    @abstractmethod
    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        """Return fixtures scheduled for a given date, optionally filtered by league."""

    @abstractmethod
    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        """Return a single fixture by id, or None if not found."""

    @abstractmethod
    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        """
        Return the normalized raw dataset for a team: recent matches
        (overall / home / away splits), current standing, and fixture
        congestion, to whatever extent the underlying source supports it.
        """

    @abstractmethod
    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        """Return the current league table for a league."""

    @abstractmethod
    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        """Return recent head-to-head matches between two teams, if available."""

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        """
        Return available betting markets for a match.

        Optional by design (spec section 9 / 15): odds are never a
        required input to the Prediction Engine. The default
        implementation returns an empty list ("no market data available"),
        which downstream code must treat as a valid, non-error state.
        """
        return []

    async def health_check(self) -> bool:
        """Lightweight liveness probe for this provider. Override if needed."""
        return True
