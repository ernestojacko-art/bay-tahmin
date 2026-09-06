"""
Null provider.

This is the default provider when no real data source has been wired up
yet (`ACTIVE_PROVIDER=none`). It never returns fabricated football data --
every method raises `ProviderNotConfiguredError` so callers up the stack
are forced to handle "no data source" explicitly (typically by degrading
confidence and telling the user, per spec section 5 / 20).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.exceptions import ProviderNotConfiguredError
from app.providers.base import BaseFootballDataProvider
from app.providers.models import Fixture, H2HRecord, OddsMarket, StandingsEntry, TeamRawDataset

_MESSAGE = (
    "No live football data provider is configured (ACTIVE_PROVIDER=none). "
    "Connect a real provider adapter (see app/providers/base.py) via "
    "environment configuration before requesting live analysis."
)


class NullProvider(BaseFootballDataProvider):
    name = "null"

    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        raise ProviderNotConfiguredError(_MESSAGE)

    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        raise ProviderNotConfiguredError(_MESSAGE)

    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        raise ProviderNotConfiguredError(_MESSAGE)

    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        raise ProviderNotConfiguredError(_MESSAGE)

    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        raise ProviderNotConfiguredError(_MESSAGE)

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        return []

    async def health_check(self) -> bool:
        return False
