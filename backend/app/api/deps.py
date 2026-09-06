"""Shared FastAPI dependency providers."""
from __future__ import annotations

from functools import lru_cache

from app.chat.context_memory import ContextMemoryStore, context_memory_store
from app.chat.football_expert import FootballExpertAgent
from app.chat.llm_client import LLMClient
from app.chat.orchestrator import ChatOrchestrator
from app.core.config import Settings, get_settings, get_team_strength_weights
from app.providers.base import BaseFootballDataProvider
from app.providers.registry import get_active_provider
from app.services.analysis_service import AnalysisService


def get_provider() -> BaseFootballDataProvider:
    return get_active_provider()


def get_analysis_service() -> AnalysisService:
    return AnalysisService(get_provider(), get_settings(), get_team_strength_weights())


@lru_cache
def _llm_client_singleton() -> LLMClient:
    return LLMClient(get_settings())


def get_llm_client() -> LLMClient:
    return _llm_client_singleton()


def get_football_expert() -> FootballExpertAgent:
    return FootballExpertAgent(get_llm_client())


def get_context_store() -> ContextMemoryStore:
    return context_memory_store


def get_chat_orchestrator() -> ChatOrchestrator:
    settings: Settings = get_settings()
    return ChatOrchestrator(
        settings=settings,
        analysis_service=get_analysis_service(),
        football_expert=get_football_expert(),
        llm_client=get_llm_client(),
        context_store=get_context_store(),
    )
