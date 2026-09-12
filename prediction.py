from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.common import DataQuality, RiskLevel
from app.schemas.team import TeamStrengthProfile


class OneXTwoProbabilities(BaseModel):
    home_win: float
    draw: float
    away_win: float


class DoubleChance(BaseModel):
    """1X2'den türetilmiş çifte şans pazarı — yeni veri gerektirmez."""

    home_or_draw: float  # "1X"
    draw_or_away: float  # "X2"
    home_or_away: float  # "12"


class DrawNoBet(BaseModel):
    """Beraberlik olasılığı çıkarılıp ev/deplasman arasında yeniden normalize edilmiş pazar."""

    home: float
    away: float


class AsianHandicapLine(BaseModel):
    """Skor matrisinden türetilmiş Asya handikapı çizgisi (ev sahibi tarafından)."""

    line: float
    home_cover: float
    away_cover: float
    push: float = 0.0


class ScoreProbability(BaseModel):
    home_goals: int
    away_goals: int
    probability: float


class OverUnderLine(BaseModel):
    line: float
    over: float
    under: float


class HalfTimeFullTimeProbabilities(BaseModel):
    """3x3 HT/FT joint probability matrix, keyed as e.g. '1/1', 'X/2'."""

    matrix: dict[str, float]


class ExpectedGoals(BaseModel):
    home_xg: float
    away_xg: float
    total_xg: float


class ModelContribution(BaseModel):
    model_name: str
    weight: float
    one_x_two: OneXTwoProbabilities


class MatchScenario(BaseModel):
    scenario_type: str  # "favorite" | "balanced" | "upset"
    label: str
    description: str
    probability: float
    expected_goals: ExpectedGoals
    tempo: str  # "low-scoring" | "balanced" | "high-scoring"


class SanityFlag(BaseModel):
    code: str
    severity: str  # "info" | "warning" | "critical"
    message: str


class ConfidenceReport(BaseModel):
    probability_home_favorite: float
    confidence: float
    risk: RiskLevel
    data_quality: DataQuality
    model_agreement: float
    market_agreement: Optional[float] = None
    warnings: list[str] = []


class MarketComparison(BaseModel):
    market_available: bool
    market_implied: Optional[OneXTwoProbabilities] = None
    model_vs_market_divergence: Optional[float] = None
    overround_removed: bool = False
    notes: list[str] = []


class SurpriseCandidate(BaseModel):
    combination: str  # e.g. "X/2"
    description: str
    plausibility: float
    upset_potential: float
    tactical_support: float
    statistical_support: float
    data_quality: float
    confidence: float
    risk: RiskLevel
    composite_score: float


class MatchPrediction(BaseModel):
    match_id: str
    generated_at: datetime

    home_team: TeamStrengthProfile
    away_team: TeamStrengthProfile

    one_x_two: OneXTwoProbabilities
    double_chance: DoubleChance
    draw_no_bet: DrawNoBet
    expected_goals: ExpectedGoals
    score_matrix: list[ScoreProbability]
    btts_yes_probability: float
    over_under: list[OverUnderLine]
    asian_handicap: list[AsianHandicapLine]
    half_time_one_x_two: OneXTwoProbabilities
    half_time_full_time: HalfTimeFullTimeProbabilities

    scenarios: list[MatchScenario]
    surprises: list[SurpriseCandidate]

    model_contributions: list[ModelContribution]
    confidence: ConfidenceReport
    sanity_flags: list[SanityFlag]
    market_comparison: MarketComparison

    data_quality: DataQuality
    warnings: list[str] = []
    disclaimers: list[str] = []
