import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import five_dollar_bridge as five
from accuracy import accuracy_snapshot, resolve_pending

ISTANBUL = ZoneInfo("Europe/Istanbul")


def patch_main(m):
    @m.app.get("/admin/program/7days")
    async def admin_program_7days():
        today = datetime.now(ISTANBUL).date()
        days = [today + timedelta(days=i) for i in range(7)]
        results = await asyncio.gather(*(five.get_matches(d.isoformat()) for d in days), return_exceptions=True)
        out = []
        for day, result in zip(days, results):
            if isinstance(result, Exception):
                out.append({"date": day.isoformat(), "data": [], "error": str(result), "cache": {"hit": False}})
            else:
                out.append({
                    "date": day.isoformat(),
                    "data": result.get("data", []),
                    "count": len(result.get("data", [])),
                    "cache": result.get("cache", {"hit": False}),
                })
        return {"days": out, "source": "5dollarfootballapi", "timezone": "Europe/Istanbul"}

    @m.app.get("/admin/cache/status")
    async def admin_cache_status():
        now = datetime.now(ISTANBUL).date()
        keys = []
        for i in range(7):
            key = str(now + timedelta(days=i))
            entry = five._MATCH_CACHE.get(key)
            keys.append({"date": key, "cached": bool(entry), "age_seconds": round(datetime.now().timestamp() - entry[0], 1) if entry else None})
        return {
            "match_cache": {"ttl_seconds": five._MATCH_CACHE_TTL_SECONDS, "stale_ttl_seconds": five._MATCH_STALE_TTL_SECONDS, "days": keys},
            "fixture_detail_cache": {"ttl_seconds": five._FIXTURE_DETAIL_TTL_SECONDS, "entries": len(five._FIXTURE_DETAIL_CACHE)},
            "provider": "5dollarfootballapi",
        }

    @m.app.get("/admin/accuracy")
    async def admin_accuracy(resolve: bool = True):
        resolved_now = await resolve_pending(8) if resolve else 0
        return {"resolved_now": resolved_now, **(await accuracy_snapshot(30))}

    @m.app.post("/admin/accuracy/resolve")
    async def admin_accuracy_resolve():
        resolved_now = await resolve_pending(8)
        return {"resolved_now": resolved_now, **(await accuracy_snapshot(30))}
