import hashlib
import json
import os
import time
from datetime import datetime

import httpx

BASE = os.getenv("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io").rstrip("/")
KEY = os.getenv("API_FOOTBALL_KEY") or os.getenv("APIFOOTBALL_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Persistent cache defaults. API-Football itself recommends caching by data freshness.
FIXTURES_TTL = int(os.getenv("API_FOOTBALL_FIXTURES_CACHE_TTL", "900"))
DETAIL_TTL = int(os.getenv("API_FOOTBALL_DETAIL_CACHE_TTL", "10800"))
LIVE_TTL = int(os.getenv("API_FOOTBALL_LIVE_CACHE_TTL", "30"))

_MEMORY = {}


def _cache_key(endpoint, params):
    raw = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _memory_get(key):
    item = _MEMORY.get(key)
    if item and item[0] > time.time():
        return item[1]
    if item:
        _MEMORY.pop(key, None)
    return None


def _memory_put(key, value, ttl):
    _MEMORY[key] = (time.time() + ttl, value)
    return value


def _supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def _persistent_get(key):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    try:
        response = httpx.get(
            f"{SUPABASE_URL}/rest/v1/api_football_cache",
            headers=_supabase_headers(),
            params={"cache_key": f"eq.{key}", "select": "response,expires_at", "limit": 1},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return None
        row = rows[0]
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
                if expires <= time.time():
                    return None
            except (ValueError, TypeError):
                return None
        return row.get("response")
    except Exception:
        # Cache failure must never take the API-Football service down.
        return None


def _persistent_put(key, endpoint, params, response, ttl):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    try:
        now = time.time()
        payload = {
            "cache_key": key,
            "endpoint": endpoint,
            "params": params,
            "response": response,
            "fetched_at": datetime.fromtimestamp(now, tz=datetime.now().astimezone().tzinfo).isoformat(),
            "expires_at": datetime.fromtimestamp(now + ttl, tz=datetime.now().astimezone().tzinfo).isoformat(),
            "updated_at": datetime.fromtimestamp(now, tz=datetime.now().astimezone().tzinfo).isoformat(),
        }
        httpx.post(
            f"{SUPABASE_URL}/rest/v1/api_football_cache",
            headers={**_supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
            timeout=10,
        ).raise_for_status()
    except Exception:
        # Cache persistence is best-effort; live API data remains authoritative.
        return


def _cached_api(path, params, ttl):
    key = _cache_key(path, params)

    value = _memory_get(key)
    if value is not None:
        return value, True

    value = _persistent_get(key)
    if value is not None:
        _memory_put(key, value, min(ttl, 300))
        return value, True

    if not KEY:
        raise RuntimeError("API_FOOTBALL_KEY environment variable bulunamadı.")

    response = httpx.get(
        f"{BASE}/{path.lstrip('/')}",
        headers={"x-apisports-key": KEY, "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"]))

    _memory_put(key, payload, ttl)
    _persistent_put(key, path, params, payload, ttl)
    return payload, False


def _map(f):
    fx = f.get("fixture", {})
    t = f.get("teams", {})
    l = f.get("league", {})
    g = f.get("goals", {})
    s = fx.get("status", {})
    sm = {
        "NS": "scheduled", "TBD": "scheduled", "1H": "live", "2H": "live",
        "HT": "halftime", "ET": "live", "P": "live", "LIVE": "live",
        "FT": "finished", "AET": "finished", "PEN": "finished",
        "PST": "postponed", "CANC": "canceled", "ABD": "canceled",
    }
    return {
        "id": str(fx.get("id")),
        "kickoff": fx.get("date"),
        "status": sm.get(s.get("short"), "scheduled"),
        "minute": s.get("elapsed"),
        "league": {
            "id": str(l.get("id")), "name": l.get("name") or "",
            "country": l.get("country") or "", "logoUrl": l.get("logo"),
        },
        "homeTeam": {
            "id": str(t.get("home", {}).get("id")),
            "name": t.get("home", {}).get("name") or "",
            "shortName": t.get("home", {}).get("code"),
            "logoUrl": t.get("home", {}).get("logo"),
        },
        "awayTeam": {
            "id": str(t.get("away", {}).get("id")),
            "name": t.get("away", {}).get("name") or "",
            "shortName": t.get("away", {}).get("code"),
            "logoUrl": t.get("away", {}).get("logo"),
        },
        "score": {
            "home": g.get("home"), "away": g.get("away"),
            "halftimeHome": (g.get("halftime") or {}).get("home") if isinstance(g.get("halftime"), dict) else None,
            "halftimeAway": (g.get("halftime") or {}).get("away") if isinstance(g.get("halftime"), dict) else None,
        },
    }


def patch_main(m):
    async def get_matches(date=None):
        d = date or datetime.now().date().isoformat()
        payload, cached = _cached_api("fixtures", {"date": d}, FIXTURES_TTL)
        rows = [_map(x) for x in payload.get("response", [])]
        return {
            "data": rows,
            "source": "supabase-cache" if cached else "api-football",
            "cache": {"hit": cached, "ttl": FIXTURES_TTL},
        }

    async def get_match_detail(match_id: int):
        params = {"id": match_id}
        payload, cached = _cached_api("fixtures", params, DETAIL_TTL)
        fixtures = payload.get("response", [])
        if not fixtures:
            raise m.HTTPException(404, "Maç bulunamadı.")

        fixture = fixtures[0]
        base = {
            "fixture": fixture,
            "match": _map(fixture),
            "source": "supabase-cache" if cached else "api-football",
            "cache": {"hit": cached, "ttl": DETAIL_TTL},
        }

        # Prediction and odds have their own persistent cache keys.
        try:
            pr, pr_cached = _cached_api("predictions", {"fixture": match_id}, 3600)
            rows = pr.get("response", [])
            base["prediction"] = rows[0].get("predictions") if rows else None
            base["prediction_cache"] = "supabase-cache" if pr_cached else "api-football"
        except Exception:
            base["prediction"] = None
            base["prediction_cache"] = "unavailable"

        try:
            od, od_cached = _cached_api("odds", {"fixture": match_id}, 10800)
            markets = []
            for bookmaker_row in od.get("response", []):
                bookmaker = bookmaker_row.get("bookmaker", {})
                bname = bookmaker.get("name", "Bookmaker")
                for bet in bookmaker.get("bets", []) or []:
                    values = []
                    for value in bet.get("values", []) or []:
                        try:
                            odd = float(value.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        if value.get("value") and odd > 0:
                            values.append({"value": str(value["value"]), "odd": odd})
                    if values:
                        markets.append({
                            "gameName": f"{bet.get('name', '')} ({bname})",
                            "type": str(bet.get("id") or ""),
                            "odds": values,
                        })
            base["markets"] = markets
            base["odds_cache"] = "supabase-cache" if od_cached else "api-football"
        except Exception:
            base["markets"] = []
            base["odds_cache"] = "unavailable"

        return base

    if KEY:
        m.get_matches = get_matches
        m.get_match_detail = get_match_detail
        m.get_match_detail_alias = get_match_detail

        async def inspect_match(row):
            key = str(row.get("id") or "")
            if not key:
                return None
            try:
                detail = await get_match_detail(int(key))
                return {
                    "match": row,
                    "markets": detail.get("markets", []),
                    "prediction": detail.get("prediction"),
                }
            except Exception:
                return None

        m.inspect_match = inspect_match
        m.app.router.routes = [
            r for r in m.app.router.routes
            if getattr(r, "path", None) not in ["/matches", "/mac/{match_id}", "/match/{match_id}"]
        ]
        m.app.add_api_route("/matches", get_matches, methods=["GET"])
        m.app.add_api_route("/mac/{match_id}", get_match_detail, methods=["GET"])
        m.app.add_api_route("/match/{match_id}", get_match_detail, methods=["GET"])
