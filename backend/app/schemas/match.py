from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TeamSummary(BaseModel):
    team_id: str
    name: str


class MatchSummary(BaseModel):
    match_id: str
    league_id: str
    league_name: str | None = None
    kickoff: datetime
    home_team: TeamSummary
    away_team: TeamSummary
    status: str
