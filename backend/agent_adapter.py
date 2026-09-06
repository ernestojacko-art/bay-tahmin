"""Compatibility adapter: route legacy chat URLs to the Cloud Football Intelligence Engine.

The historical market/Gemini agent is intentionally no longer mounted. The Cloud
Engine is the single source of truth for match predictions and match chat.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

import five_dollar_bridge as five


def market_probability(odds):
    raw = {x["value"]: 1 / x["odd"] for x in odds if x.get("odd", 0) > 0}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total else {}


def find_market(item, market_type):
    for market in item.get("markets", []):
        if str(market.get("type")) == market_type:
            return market
    return None


def build_iyms_candidates(item, surprise=False):
    """Compatibility hook used by older runtime code; no longer powers chat."""
    try:
        from iyms_fallback import build
        return build(item, market_probability, find_market, surprise)
    except Exception:
        return []


def patch_main(m):
    """Replace historical root chat routes with Cloud Engine orchestrator routes."""
    from app.api.deps import get_chat_orchestrator

    # sitecustomize can run after another compatibility layer has mounted the old
    # market/Gemini routes. Remove those exact legacy paths before mounting the
    # Cloud Engine handlers.
    m.app.router.routes = [
        route
        for route in m.app.router.routes
        if not (
            isinstance(route, APIRoute)
            and route.path in {"/chat", "/matches/{match_id}/chat"}
            and "POST" in (route.methods or set())
        )
    ]

    async def chat_route(request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        session_id = str(payload.get("session_id") or "bay-tahmin-web")
        match_id = payload.get("match_id")
        result = await get_chat_orchestrator().handle_message(
            session_id, message, str(match_id) if match_id is not None else None
        )
        return result.model_dump(mode="json")

    async def match_chat_route(match_id: int, request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        session_id = str(payload.get("session_id") or f"match-{match_id}")
        result = await get_chat_orchestrator().handle_message(
            session_id, message, str(match_id)
        )
        return result.model_dump(mode="json")

    m.app.add_api_route("/chat", chat_route, methods=["POST"], tags=["chat-compat"])
    m.app.add_api_route(
        "/matches/{match_id}/chat",
        match_chat_route,
        methods=["POST"],
        tags=["chat-compat"],
    )

    # Preserve the existing frontend's fixture/detail helpers.
    m.get_matches = five.get_matches
    m.get_match_detail = five.get_match_detail
    m.get_match_detail_alias = five.get_match_detail
