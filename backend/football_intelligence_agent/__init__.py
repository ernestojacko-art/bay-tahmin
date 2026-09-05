"""Runtime facade for the Bay Tahmin Football Intelligence Engine."""
from __future__ import annotations

import asyncio
import importlib.util
import re
from datetime import timedelta
from pathlib import Path

_IMPL_NAME = "_bay_tahmin_football_intelligence_agent_impl"
_IMPL_PATH = Path(__file__).resolve().parent.parent / "football_intelligence_agent_v3.py"
_spec = importlib.util.spec_from_file_location(_IMPL_NAME, _IMPL_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load Intelligence Engine implementation: {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

ENGINE = _impl.ENGINE
VERSION = _impl.VERSION
dates = _impl.dates
num = _impl.num
isiy = _impl.isiy
issur = _impl.issur
model = _impl.model
market = _impl.market
day = _impl.day
cand = _impl.cand
choose = _impl.choose
pack = _impl.pack
analyze_match = _impl.analyze_match
match_answer = _impl.match_answer


def _norm(value: object) -> str:
    text = str(value or "").lower().strip()
    text = text.translate(str.maketrans({"ı":"i","ş":"s","ğ":"g","ü":"u","ö":"o","ç":"c"}))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _team_tokens(name: object) -> list[str]:
    return [w for w in _norm(name).split() if len(w) >= 3]


def _team_mentioned(team: object, text: str) -> bool:
    n = _norm(team)
    if not n:
        return False
    if n in text:
        return True
    tokens = _team_tokens(team)
    return bool(tokens) and all(token in text for token in tokens)


def _explicit_match_requested(message: str, row: dict) -> bool:
    text = _norm(message)
    return _team_mentioned(row.get("Team1"), text) and _team_mentioned(row.get("Team2"), text)


async def _rows_for_match_lookup(message: str) -> list[dict]:
    requested = dates(message)
    raw = _norm(message)
    has_explicit_date = any(word in raw for word in ("bugun", "yarin", "cumartesi", "pazar")) or bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", raw))
    if not has_explicit_date:
        base = dates("")[0]
        requested = [base + timedelta(days=i) for i in range(7)]
    groups = await asyncio.gather(*(day(d) for d in requested))
    return [row for group in groups for row in group]


async def answer(main, message, history=None):
    rows = await _rows_for_match_lookup(message)
    candidates = [row for row in rows if _explicit_match_requested(message, row)]
    if len(candidates) == 1:
        return await match_answer(main, int(candidates[0]["MatchID"]), message, history or [])
    if len(candidates) > 1:
        return {"reply":"Aynı takım eşleşmesi için birden fazla gerçek maç bulundu. Tarihi veya organizasyonu belirtirsen doğru karşılaşmayı analiz edebilirim.","engine":ENGINE,"engine_version":VERSION,"match_count":len(candidates),"analyzed_count":0,"source":"5DollarFootballAPI"}
    return await _impl.answer(main, message, history or [])


def patch_main(main):
    """Install Intelligence Engine routes and remove legacy conflicting handlers."""
    from fastapi import HTTPException, Request
    app = main.app
    target_paths = {"/chat", "/matches/{match_id}/chat", "/ai/analyze/{match_id}", "/match/{match_id}", "/mac/{match_id}"}
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in target_paths]

    @app.post("/chat")
    async def intelligence_general_chat(request: Request):
        try: payload = await request.json()
        except Exception: payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message: raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        return await answer(main, message, payload.get("history") or [])

    @app.post("/matches/{match_id}/chat")
    async def intelligence_match_chat(match_id: int, request: Request):
        try: payload = await request.json()
        except Exception: payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message: raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        return await match_answer(main, match_id, message, payload.get("history") or [])

    @app.get("/ai/analyze/{match_id}")
    async def intelligence_analyze_match(match_id: int): return await analyze_match(main, match_id)

    @app.get("/match/{match_id}")
    @app.get("/mac/{match_id}")
    async def intelligence_match_detail(match_id: int):
        detail = await main.get_match_detail(match_id)
        engine_result = await analyze_match(main, match_id)
        detail["analysis"] = engine_result.get("analysis")
        detail["prediction"] = engine_result.get("analysis")
        detail["intelligence_engine"] = {"name": ENGINE, "version": VERSION}
        detail["prediction_cache"] = "intelligence_engine"
        return detail
    return app


__all__ = ["ENGINE", "VERSION", "answer", "analyze_match", "match_answer", "patch_main"]
