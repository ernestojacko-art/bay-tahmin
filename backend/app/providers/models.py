"""
Standardized data-transfer objects that every provider adapter must map its
raw responses into. This is the "standard model" referenced in spec
section 5 (Data Intelligence Layer) -- business logic never talks to a raw
provider payload directly, only to these normalized shapes.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MatchResult(str, Enum):
    HOME_WIN = "1"
    DRAW = "X"
    AWAY_WIN = "2"


class TeamRef(BaseModel):
    team_id: str
    name: str
    league_id: Optional[str] = None


class RecentMatch(BaseModel):
    """A single completed match, from the perspective of a reference team."""

    match_id: str
    date: date
    is_home: bool
    opponent: TeamRef
    goals_for: int
    goals_against: int
    xg_for: Optional[float] = None
    xg_against: Optional[float] = None
    shots_for: Optional[int] = None
    shots_on_target_for: Optional[int] = None
    shots_against: Optional[int] = None
    shots_on_target_against: Optional[int] = None
    result: MatchResult
    opponent_strength_hint: Optional[float] = None  # e.g. opponent's league position/rating


class StandingsEntry(BaseModel):
    team: TeamRef
    position: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int


class H2HRecord(BaseModel):
    matches: list[RecentMatch] = Field(default_factory=list)


class FixtureStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"


class Fixture(BaseModel):
    match_id: str
    league_id: str
    league_name: Optional[str] = None
    kickoff: datetime
    home_team: TeamRef
    away_team: TeamRef
    status: FixtureStatus = FixtureStatus.SCHEDULED
    round: Optional[str] = None


class OddsSelection(BaseModel):
    label: str  # e.g. "1", "X", "2", "Over 2.5", "BTTS Yes"
    price: float  # decimal odds


class OddsMarket(BaseModel):
    market_name: str  # e.g. "1X2", "Over/Under 2.5", "BTTS", "HT/FT"
    selections: list[OddsSelection]
    bookmaker: Optional[str] = None
    as_of: Optional[datetime] = None


class FixtureCongestion(BaseModel):
    matches_last_14_days: int = 0
    days_since_last_match: Optional[int] = None
    next_match_days_away: Optional[int] = None


class TeamRawDataset(BaseModel):
    """
    Raw-but-normalized data gathered for a single team, before Team
    Strength Engine processing. Any field may be `None` if the provider
    could not supply it -- callers must never fabricate a replacement.
    """

    team: TeamRef
    recent_matches: list[RecentMatch] = Field(default_factory=list)
    recent_matches_home: list[RecentMatch] = Field(default_factory=list)
    recent_matches_away: list[RecentMatch] = Field(default_factory=list)
    standing: Optional[StandingsEntry] = None
    fixture_congestion: Optional[FixtureCongestion] = None


class MatchRawDataset(BaseModel):
    """Everything the Data Intelligence Layer could gather for one fixture."""

    fixture: Fixture
    home_team_data: TeamRawDataset
    away_team_data: TeamRawDataset
    h2h: Optional[H2HRecord] = None
    odds_markets: list[OddsMarket] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
