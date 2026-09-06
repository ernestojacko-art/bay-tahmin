from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_chat_orchestrator
from app.chat.orchestrator import ChatOrchestrator
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator)
) -> ChatResponse:
    return await orchestrator.handle_message(request.session_id, request.message, request.match_id)


@router.post("/matches/{match_id}/chat", response_model=ChatResponse)
async def chat_about_match(
    match_id: str,
    request: ChatRequest,
    orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator),
) -> ChatResponse:
    return await orchestrator.handle_message(request.session_id, request.message, match_id)
