"""Runtime facade for the Bay Tahmin Football Intelligence Engine.

The implementation lives in the sibling football_intelligence_agent.py file. This
package facade exists because Python resolves a package before a same-name module;
it lets main.py import a stable patch_main symbol while preserving the existing
engine implementation unchanged.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_IMPL_NAME = "_bay_tahmin_football_intelligence_agent_impl"
_IMPL_PATH = Path(__file__).resolve().parent.parent / "football_intelligence_agent.py"

_spec = importlib.util.spec_from_file_location(_IMPL_NAME, _IMPL_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Unable to load Intelligence Engine implementation: {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_spec)
sys.modules[_IMPL_NAME] = _impl
_spec.loader.exec_module(_impl)

# Re-export the engine API used by the application.
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
answer = _impl.answer
analyze_match = _impl.analyze_match
match_answer = _impl.match_answer


def patch_main(main):
    """Replace legacy market-only chat/analyse handlers with the Intelligence Engine."""
    from fastapi import HTTPException, Request

    app = main.app
    target_paths = {"/chat", "/matches/{match_id}/chat", "/ai/analyze/{match_id}", "/match/{match_id}", "/mac/{match_id}"}
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in target_paths]

    @app.post("/chat")
    async def intelligence_general_chat(request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        return await answer(main, message, payload.get("history") or [])

    @app.post("/matches/{match_id}/chat")
    async def intelligence_match_chat(match_id: int, request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        message = str(payload.get("message") or payload.get("question") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
        return await match_answer(main, match_id, message, payload.get("history") or [])

    @app.get("/ai/analyze/{match_id}")
    async def intelligence_analyze_match(match_id: int):
        return await analyze_match(main, match_id)

    @app.get("/match/{match_id}")
    @app.get("/mac/{match_id}")
    async def intelligence_match_detail(match_id: int):
        # The frontend already opens /match/{id}. Make that existing entry point
        # execute the same Intelligence Engine instead of requiring a second UI call.
        detail = await main.get_match_detail(match_id)
        engine_result = await analyze_match(main, match_id)
        detail["prediction"] = engine_result.get("analysis")
        detail["intelligence_engine"] = {"name": ENGINE, "version": VERSION}
        detail["prediction_cache"] = "intelligence_engine"
        return detail

    return app


__all__ = [
    "ENGINE", "VERSION", "answer", "analyze_match", "match_answer", "patch_main",
]
