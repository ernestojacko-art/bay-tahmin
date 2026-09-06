from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.common import DataQuality


class TeamStrengthProfile(BaseModel):
    """
    Explainable multi-factor strength profile for a team.

    No single number is treated as "the" team strength -- callers should
    read `overall` alongside the components that produced it.
    """

    team_id: str
    team_name: str

    overall: float  # 0-1 normalized composite
    attack: float
    defense: float
    form: float
    home_strength: Optional[float] = None
    away_strength: Optional[float] = None
    opponent_adjusted: float

    matches_considered: int
    data_quality: DataQuality

    weights_used: dict[str, float]
    notes: list[str] = []
