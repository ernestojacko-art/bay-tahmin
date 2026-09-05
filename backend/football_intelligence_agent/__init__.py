"""Runtime facade for the Bay Tahmin Football Intelligence Engine."""
from __future__ import annotations
import asyncio, importlib.util, re
from datetime import timedelta
from pathlib import Path

_IMPL_PATH = Path(__file__).resolve().parent.parent / "football_intelligence_agent_v6.py"
spec = importlib.util.spec_from_file_location("_bay_tahmin_engine_impl", _IMPL_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Unable to load Intelligence Engine: {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_impl)
ENGINE, VERSION = _impl.ENGINE, _impl.VERSION
dates, num, isiy, issur = _impl.dates, _impl.num, _impl.isiy, _impl.issur
market, day, cand = _impl.market, _impl.day, _impl.cand
model, analyze_match, match_answer = _impl.model, _impl.analyze_match, _impl.match_answer
resolve_finished_match = _impl.resolve_finished_match
performance_summary = _impl.performance_summary


def _norm(value: object) -> str:
    text = str(value or "").lower().strip()
    text = text.translate(str.maketrans({"ı":"i","ş":"s","ğ":"g","ü":"u","ö":"o","ç":"c"}))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()

def _team_mentioned(team: object, text: str) -> bool:
    n = _norm(team)
    if not n: return False
    if n in text: return True
    tokens = [w for w in n.split() if len(w) >= 3]
    return bool(tokens) and all(w in text for w in tokens)

def _explicit_match_requested(message: str, row: dict) -> bool:
    text = _norm(message)
    return _team_mentioned(row.get("Team1"), text) and _team_mentioned(row.get("Team2"), text)

async def _rows_for_match_lookup(message: str) -> list[dict]:
    requested = dates(message)
    raw = _norm(message)
    explicit = any(x in raw for x in ("bugun", "yarin", "cumartesi", "pazar")) or bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", raw))
    if not explicit:
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
    from fastapi import HTTPException, Request
    app = main.app
    target = {"/chat","/matches/{match_id}/chat","/ai/analyze/{match_id}","/match/{match_id}","/mac/{match_id}","/ai/performance","/ai/resolve/{match_id}","/ai/backtest"}
    app.router.routes[:] = [r for r in app.router.routes if getattr(r,"path",None) not in target]
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
    @app.get("/ai/performance")
    async def intelligence_performance(): return performance_summary()
    @app.post("/ai/resolve/{match_id}")
    async def intelligence_resolve(match_id: int):
        detail = await main.get_match_detail(match_id)
        return {"resolved": await resolve_finished_match(detail), "match_id": match_id}
    @app.post("/ai/backtest")
    async def intelligence_backtest(request: Request):
        try: payload = await request.json()
        except Exception: payload = {}
        try: league_id = int(payload.get("league_id"))
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="league_id zorunlu ve sayısal olmalı.")
        limit = max(1, min(int(payload.get("limit") or 50), 250))
        from backtest_engine import run_historical_backtest
        return await run_historical_backtest(league_id, season=payload.get("season"), start_time=payload.get("start_time"), end_time=payload.get("end_time"), limit=limit)
    @app.get("/match/{match_id}")
    @app.get("/mac/{match_id}")
    async def intelligence_match_detail(match_id: int):
        detail = await main.get_match_detail(match_id)
        engine_result = await analyze_match(main, match_id)
        detail["analysis"] = engine_result.get("analysis")
        detail["prediction"] = engine_result.get("analysis")
        detail["intelligence_engine"] = {"name":ENGINE,"version":VERSION}
        detail["prediction_cache"] = "intelligence_engine"
        return detail
    return app

__all__ = ["ENGINE","VERSION","answer","analyze_match","match_answer","patch_main","performance_summary"]
