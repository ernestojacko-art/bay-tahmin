"""
Application configuration.

All configuration is environment-driven. No API keys or secrets are ever
hard-coded here. Values below are defaults that can be overridden via
environment variables or a `.env` file (see `.env.example`).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TeamStrengthWeights(BaseSettings):
    """Configurable weighting for the Team Strength Engine."""

    recent_5_weight: float = 0.45
    recent_10_weight: float = 0.35
    recent_20_weight: float = 0.20
    home_away_split_weight: float = 0.30
    opponent_strength_adjustment: float = 0.25
    season_vs_recent_form_weight: float = 0.40  # 0 = pure season, 1 = pure recent form


class Settings(BaseSettings):
    """Central application settings, populated from the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App metadata ---
    app_name: str = "BAY TAHMİN Football Intelligence Engine"
    app_version: str = "0.1.0"
    environment: str = Field(default="development")
    debug: bool = False

    # --- API ---
    api_v1_prefix: str = "/api/v1"

    # --- Data providers ---
    # Which provider implementation to wire up by default. "none" means no
    # live provider is configured yet -- the system must degrade gracefully
    # (low confidence / explicit "insufficient data") rather than invent data.
    # Accepts either ACTIVE_PROVIDER or DATA_PROVIDER as the env var name
    # (DATA_PROVIDER is the name used in newer deployment docs / spec
    # wording; ACTIVE_PROVIDER is kept for backward compatibility with
    # existing deployments and takes precedence if both are set).
    active_provider: str = Field(
        default="5dollarfootballapi",
        validation_alias=AliasChoices("ACTIVE_PROVIDER", "DATA_PROVIDER", "active_provider", "data_provider"),
    )
    football_data_api_key: Optional[str] = Field(default=None)
    football_data_api_base_url: Optional[str] = Field(default=None)
    # Endpoint paths for a generic REST football-data provider. Defaults
    # follow a widely-used convention (fixtures / team statistics /
    # standings / head-to-head) but are fully overridable per vendor
    # without touching adapter code.
    football_data_fixtures_path: str = Field(default="/fixtures")
    football_data_team_stats_path: str = Field(default="/teams/statistics")
    football_data_standings_path: str = Field(default="/standings")
    football_data_h2h_path: str = Field(default="/fixtures/headtohead")
    football_data_request_timeout_seconds: float = Field(default=10.0)
    football_data_max_retries: int = Field(default=2)

    odds_api_key: Optional[str] = Field(default=None)
    odds_api_base_url: Optional[str] = Field(default=None)
    odds_api_path: str = Field(default="/odds")
    odds_api_request_timeout_seconds: float = Field(default=10.0)

    # --- Cache ---
    cache_backend: str = Field(default="memory")  # memory | redis (future)
    cache_default_ttl_seconds: int = 900
    team_strength_cache_ttl_seconds: int = 3600
    match_analysis_cache_ttl_seconds: int = 1800

    # --- Statistical modeling ---
    poisson_max_goals: int = 10
    dixon_coles_rho: float = -0.13
    min_matches_for_reliable_form: int = 5
    min_matches_for_high_confidence: int = 10

    # --- Ensemble weights (must sum to 1.0 across active models) ---
    model_weight_poisson_dixon_coles: float = 0.55
    model_weight_elo_strength: float = 0.30
    model_weight_form_based: float = 0.15

    # --- Confidence / Sanity thresholds ---
    sanity_market_disagreement_threshold: float = 0.35
    sanity_min_confidence_after_contradiction: float = 0.35
    max_public_confidence_label: float = 0.90  # never claim ~100% certainty

    # --- Chat / LLM ---
    llm_provider: str = Field(default="none")  # none | anthropic | openai | custom
    llm_api_key: Optional[str] = Field(default=None)
    llm_model: str = Field(default="claude-sonnet-4-6")
    chat_context_max_turns: int = 12

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_team_strength_weights() -> TeamStrengthWeights:
    return TeamStrengthWeights()
