"""Runtime facade for the Bay Tahmin Football Intelligence Engine."""
from __future__ import annotations
import asyncio, importlib.util, re, unicodedata
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
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"}))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _team_mentioned(team: object, text: str) -> bool:
    n = _norm(team)
    if not n:
        return False
    if n in text:
        return True
    tokens = [w for w in n.split() if len(w) >= 3]
    return bool(tokens) and all(w in text for w in tokens)


def _explicit_match_requested(message: str, row: dict) -> bool:
    text = _norm(message)
    return _team_mentioned(row.get("Team1"), text) and _team_mentioned(row.get("Team2"), text)


def _is_iyms_request(message: str) -> bool:
    text = _norm(message).replace(" ", "")
    return bool(re.search(r"iy/?ms|ilkyari/?macsonucu|ilkyarimacsonucu", text))


def _is_surprise_request(message: str) -> bool:
    return "surpriz" in _norm(message)


def _surprise_reply(rows: list[dict], message: str) -> dict | None:
    """Market availability must never block a model-only HT/FT intelligence request."""
    if not (_is_iyms_request(message) and _is_surprise_request(message)):
        return None
    if not rows:
        return None
    try:
        analyzed = awaitable = None
    except Exception:
        return None
    return None


async def _model_only_iyms_surprises(message: str, rows: list[dict]) -> dict:
    count = num(message)
    candidates = []
    # For HT/FT surprise questions, the statistical engine is authoritative;
    # bookmaker market presence is only optional cross-check information.
    for row in rows:
        try:
            result = await cand(row, track=False)
            m = result.get("model") or {}
            probs = (m.get("iyms") or {}).get("probabilities") or {}
            for selection, probability in probs.items():
                if selection in {"1/1", "X/X", "2/2"}:
                    continue
                candidates.append((float(probability), result, selection))
        except Exception:
            continue
    candidates.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    selected = []
    for probability, result, selection in candidates:
        match = result.get("match") or {}
        match_id = str(match.get("MatchID") or "")
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        selected.append((probability, result, selection))
        if len(selected) >= count:
            break
    if not selected:
        return {
            "reply": "İY/MS sürpriz analizi için doğrulanabilir maç verisi bulunamadı; veri yokken tahmin uydurmuyorum.",
            "engine": ENGINE,
            "engine_version": VERSION,
            "analyzed_count": 0,
            "source": "5DollarFootballAPI + transparent statistical ensemble",
        }
    lines = [f"{ENGINE} — bağımsız İY/MS sürpriz analizi\n"]
    for i, (probability, result, selection) in enumerate(selected, 1):
        match = result.get("match") or {}
        name = match.get("Teams") or f"{match.get('Team1', '')} - {match.get('Team2', '')}"
        kickoff = match.get("KickoffUTC") or match.get("DateTime") or ""
        lines.append(f"{i}. {name} — İY/MS {selection} — model olasılığı %{probability:.2f}" + (f" — {kickoff}" if kickoff else ""))
    lines.append("\nNot: 1/1, X/X ve 2/2 düz sonuçlar sürpriz adayına alınmadı. Piyasa varsa yalnızca çapraz kontrol olarak değerlendirilir; açık market bulunmaması model analizini engellemez.")
    return {
        "reply": "\n".join(lines),
        "engine": ENGINE,
        "engine_version": VERSION,
        "dates": [d.isoformat() for d in dates(message)],
        "match_count": len(rows),
        "analyzed_count": len({str((r.get('match') or {}).get('MatchID')) for _, r, _ in selected}),
        "source": "5DollarFootballAPI + transparent statistical ensemble",
    }


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
    surprise_result = await _model_only_iyms_surprises(message, rows)
    if surprise_result is not None:
        return surprise_result
    candidates = [row for row in rows if _explicit_match_requested(message, row)]
    if len(candidates) == 1:
        return await match_answer(main, int(candidates[0]["MatchID"]), message, history or [])
    if len(candidates) > 1:
        return {"reply": "Aynı takım eşleşmesi için birden fazla gerçek maç bulundu. Tarihi veya organizasyonu belirtirsen doğru karşılaşmayı analiz edebilirim.", "engine": ENGINE, "engine_version": VERSION, "match_count": len(candidates), "analyzed_count": 0, "source": "5DollarFootballAPI"}
    return await _impl.answer(main, message, history or [])


def patch_main(main):
    from fastapi import HTTPException, Request
    app = main.app
    target = {"/chat", "/matches/{match_id}/chat", "/ai/analyze/{match_id}", "/match/{match_id}", "/mac/{match_id}", "/ai/performance", "/ai/resolve/{match_id}", "/ai/backtest"}
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in target]
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
        detail["intelligence_engine"] = {"name": ENGINE, "version": VERSION}
        detail["prediction_cache"] = "intelligence_engine"
        return detail
    return app


__all__ = ["ENGINE", "VERSION", "answer", "analyze_match", "match_answer", "patch_main", "performance_summary"]