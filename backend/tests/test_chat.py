from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ACTIVE_PROVIDER", "sample-dev-only")

import uuid

import pytest

from app.chat.context_memory import ContextMemoryStore
from app.chat.football_expert import FootballExpertAgent
from app.chat.llm_client import LLMClient
from app.chat.orchestrator import ChatOrchestrator
from app.core.config import get_settings
from app.services.analysis_service import AnalysisService


@pytest.fixture
def orchestrator(sample_provider):
    settings = get_settings()
    analysis_service = AnalysisService(sample_provider, settings)
    llm_client = LLMClient(settings)  # llm_provider=none by default -> always falls back
    football_expert = FootballExpertAgent(llm_client)
    context_store = ContextMemoryStore()
    return ChatOrchestrator(
        settings=settings,
        analysis_service=analysis_service,
        football_expert=football_expert,
        llm_client=llm_client,
        context_store=context_store,
    )


@pytest.mark.asyncio
async def test_general_football_question_does_not_touch_prediction_engine(orchestrator):
    session_id = str(uuid.uuid4())
    response = await orchestrator.handle_message(session_id, "Pressing sistemleri nasıl çalışır?", None)
    assert response.intent == "general_football"
    assert response.used_prediction_engine is False
    assert len(response.reply) > 0


@pytest.mark.asyncio
async def test_match_question_uses_prediction_engine_and_is_grounded(orchestrator):
    session_id = str(uuid.uuid4())
    response = await orchestrator.handle_message(session_id, "Bu maçta en çok neye güveniyorsun?", "M1")
    assert response.intent == "match_analysis"
    assert response.used_prediction_engine is True
    assert response.grounded_in_analysis is True
    assert response.match_id == "M1"


@pytest.mark.asyncio
async def test_context_memory_allows_followup_without_repeating_match_name(orchestrator):
    session_id = str(uuid.uuid4())
    first = await orchestrator.handle_message(session_id, "Bu maçı analiz eder misin?", "M1")
    assert first.used_prediction_engine is True

    # Follow-up without match_id -- must reuse bound context, not fail.
    second = await orchestrator.handle_message(session_id, "Peki ilk yarı?", None)
    assert second.intent == "match_analysis"
    assert second.match_id == "M1"
    assert "yarı" in second.reply.lower() or "%" in second.reply


@pytest.mark.asyncio
async def test_unknown_match_id_produces_structured_fallback_not_crash(orchestrator):
    session_id = str(uuid.uuid4())
    response = await orchestrator.handle_message(session_id, "Bu maçı analiz et", "DOES_NOT_EXIST")
    assert response.intent == "fallback"
    assert response.used_prediction_engine is False
    assert len(response.reply) > 0
