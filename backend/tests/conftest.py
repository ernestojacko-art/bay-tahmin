from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ACTIVE_PROVIDER", "sample-dev-only")

import pytest

from app.core.config import get_settings, get_team_strength_weights
from app.intelligence.prediction_engine import PredictionEngine
from app.providers.sample_provider import SampleDataProvider
from app.services.analysis_service import AnalysisService


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def sample_provider():
    return SampleDataProvider()


@pytest.fixture
def analysis_service(sample_provider, settings):
    return AnalysisService(sample_provider, settings, get_team_strength_weights())


@pytest.fixture
def prediction_engine(settings):
    return PredictionEngine(settings, get_team_strength_weights())


@pytest.fixture
async def sample_dataset(sample_provider):
    from app.intelligence.data_intelligence import DataIntelligenceLayer

    layer = DataIntelligenceLayer(sample_provider)
    return await layer.build_match_dataset("M1")
