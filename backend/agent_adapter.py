"""Compatibility adapter: route legacy chat URLs to the Cloud Football Intelligence Engine.

The historical market/Gemini agent is intentionally no longer mounted. The Cloud
Engine is the single source of truth for match predictions and match chat.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

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


def _norm(value: str) -> str:
    """Normalize Turkish team names for robust chat matching."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("ı", "i").replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _team_score(query: str, team: str) -> float:
    q = _norm(query)
    t = _norm(team)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if q in t or t in q:
        return 0.92
    q_tokens, t_tokens = set(q.split()), set(t.split())
    overlap = len(q_tokens & t_tokens) / max(1, min(len(q_tokens), len(t_tokens)))
    return max(overlap, SequenceMatcher(None, q, t).ratio())


def _extract_team_pair(message: str) -> tuple[str, str] | None:
    """Extract a likely home/away pair from natural Turkish match wording."""
    text = str(message or "").strip()
    # Most common forms: "A - B", "A vs B", "A ile B", "A/B".
    patterns = (
        r"([^\n,;:]+?)\s+(?:-|–|—|vs\.?|v\.?|ile|/|x)\s+([^\n,;:?!]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            left = re.sub(r"\b(maçı|macı|maci|maç|mac|karşılaşması|karsilasmasi)\b", "", match.group(1), flags=re.I).strip()
            right = re.sub(r"\b(maçı|macı|maci|maç|mac|karşılaşması|karsilasmasi)\b", "", match.group(2), flags=re.I).strip()
            if left and right:
                return left, right
    return None


async def _resolve_match_id(message: str) -> str | None:
    """Resolve team names in a general chat message to a real 5Dollar fixture ID.

    Only fixtures returned by the existing 5DollarFootballAPI bridge are eligible;
    no match IDs or fixture data are invented. Search today through the next two
    Istanbul-local calendar days so requests such as "yarın" resolve naturally.
    """
    pair = _extract_team_pair(message)
    if not pair:
        return None

    left, right = pair
    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.now(tz).date()
    candidates = []
    for offset in range(0, 3):
        try:
            payload = await five.get_matches((today + timedelta(days=offset)).isoformat())
            candidates.extend(payload.get("data") or [])
        except Exception:
            continue

    best = None
    best_score = 0.0
    for item in candidates:
        home = str(item.get("Team1") or "")
        away = str(item.get("Team2") or "")
        home_left = _team_score(left, home)
        away_right = _team_score(right, away)
        direct = (home_left + away_right) / 2
        reverse = (_team_score(left, away) + _team_score(right, home)) / 2
        score = max(direct, reverse)
        if score > best_score:
            best_score = score
            best = item

    # Require a genuinely convincing two-team match before routing into analysis.
    if not best or best_score < 0.68:
        return None
    return str(best.get("MatchID") or best.get("matchID") or best.get("id") or "") or None


def patch_main(m):
    """Replace historical root chat routes with Cloud Engine orchestrator routes."""
    from app.api.deps import get_chat_orchestrator

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

        # If the frontend does not send match_id, infer it from team names using
        # the existing real fixture feed. This is what turns a general question
        # like "Göztepe - Gaziantep maçı için ne düşünüyorsun?" into match chat.
        if match_id is None:
            match_id = await _resolve_match_id(message)

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
