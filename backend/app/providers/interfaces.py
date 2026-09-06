"""
Narrow, single-responsibility provider interfaces.

`BaseFootballDataProvider` (see `base.py`) is the single interface the
rest of the application depends on. These narrower interfaces exist so a
production deployment can source different *kinds* of data from
different vendors -- e.g. fixtures from one API, detailed team/match
statistics from another, and a match-program/RSS feed from a third --
without touching `DataIntelligenceLayer`, the Prediction Engine, or any
other business-logic module.

`CompositeFootballDataProvider` (see `composite_provider.py`) implements
`BaseFootballDataProvider` by delegating each concern to whichever of
these narrow adapters has been configured, and raises
`ProviderNotConfiguredError` (never fabricated data) for any concern
that has no adapter wired up.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from app.providers.models import Fixture, H2HRecord, OddsMarket, StandingsEntry, TeamRawDataset


class FixtureProviderInterface(ABC):
    """Supplies fixture/schedule data only (kickoff times, teams, status)."""

    name: str = "fixture-provider-base"

    @abstractmethod
    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        ...

    @abstractmethod
    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        ...


class TeamStatisticsProviderInterface(ABC):
    """Supplies team-level statistics: recent matches, form, standings."""

    name: str = "team-statistics-provider-base"

    @abstractmethod
    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        ...

    @abstractmethod
    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        ...


class MatchStatisticsProviderInterface(ABC):
    """Supplies match-level statistics not covered by fixtures: H2H, in-depth match stats."""

    name: str = "match-statistics-provider-base"

    @abstractmethod
    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        ...


class MatchProgramProviderInterface(ABC):
    """
    Supplies match-program / fixture-list style feeds (e.g. an RSS or
    calendar feed of upcoming fixtures for a league or competition).
    Distinct from `FixtureProviderInterface` because program feeds often
    come from lighter-weight sources than full statistics APIs and may
    only provide schedule information, no live status.
    """

    name: str = "match-program-provider-base"

    @abstractmethod
    async def get_scheduled_fixtures(self, league_id: str, days_ahead: int = 7) -> list[Fixture]:
        ...


class OddsProviderInterface(ABC):
    """Supplies market/odds data only. Always optional (see Golden Rule)."""

    name: str = "odds-provider-base"

    @abstractmethod
    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        ...
