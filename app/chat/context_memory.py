"""
Conversation / Match Context Memory (spec section 13).

Keeps enough state per session that a user can ask follow-up questions
("Peki ilk yarı?", "Sürpriz ihtimali ne?") without repeating the match
name. Context is intentionally simple (in-memory, TTL-based) -- swapping
in Redis/DB-backed storage for multi-instance deployments only requires
replacing this module's storage, not its interface.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from app.schemas.prediction import MatchPrediction

_CONTEXT_TTL_SECONDS = 60 * 60 * 2  # 2 hours of inactivity


@dataclass
class ConversationTurn:
    role: str  # "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class MatchContext:
    active_match_id: Optional[str] = None
    active_date: Optional[str] = None
    home_team_name: Optional[str] = None
    away_team_name: Optional[str] = None
    latest_analysis: Optional[MatchPrediction] = None
    previous_intent: Optional[str] = None
    last_surprise_query: Optional[str] = None
    selected_scenario: Optional[str] = None
    selected_prediction_field: Optional[str] = None
    recent_turns: list[ConversationTurn] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()

    def add_turn(self, role: str, content: str, max_turns: int) -> None:
        self.recent_turns.append(ConversationTurn(role=role, content=content))
        if len(self.recent_turns) > max_turns:
            self.recent_turns = self.recent_turns[-max_turns:]
        self.touch()

    def bind_match(self, match_id: str, home_name: str, away_name: str) -> None:
        self.active_match_id = match_id
        self.home_team_name = home_name
        self.away_team_name = away_name
        self.touch()

    def set_analysis(self, prediction: MatchPrediction) -> None:
        self.latest_analysis = prediction
        self.touch()


class ContextMemoryStore:
    """Process-wide session -> MatchContext store."""

    def __init__(self, ttl_seconds: int = _CONTEXT_TTL_SECONDS):
        self._sessions: dict[str, MatchContext] = {}
        self._ttl = ttl_seconds

    def get_or_create(self, session_id: str) -> MatchContext:
        self._evict_expired()
        ctx = self._sessions.get(session_id)
        if ctx is None:
            ctx = MatchContext()
            self._sessions[session_id] = ctx
        return ctx

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [sid for sid, ctx in self._sessions.items() if now - ctx.last_active > self._ttl]
        for sid in expired:
            del self._sessions[sid]

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


context_memory_store = ContextMemoryStore()
