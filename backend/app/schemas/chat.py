from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-generated conversation/session identifier")
    message: str
    match_id: Optional[str] = Field(default=None, description="If set, binds this turn to a specific match context")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: Literal["general_football", "match_analysis", "clarification", "fallback", "fixture_list"]
    match_id: Optional[str] = None
    used_prediction_engine: bool = False
    grounded_in_analysis: bool = False
