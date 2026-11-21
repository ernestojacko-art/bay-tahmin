"""Maç canlandırması için takımların GERÇEK verilerden çıkarılan gol/atak profili (BSD v2).

Her takım için son maçlarında (şut haritası): gol türü dağılımı (korner, duran top, penaltı, kontra, ortadan kafa, paslaşarak...),
golü atan oyuncular (eksik oyuncular hariç) ve maç başına korner/şut/topla oynama. Veri yoksa 'available: false' döner; veri uydurulmaz.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

import bsd_injuries as inj

logger = logging.getLogger("bsd")
BASE = "https://sports.bzzoiro.com/api/v2"
LAST_N = 8
TTL = 12 * 3600
_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_names: dict[int, str] = {}
_sem = asyncio.Semaphore(4)


def _kind(shot: dict[str, Any]) -> str:
    sit = str(shot.get("sit") or "").lower()
    body = str(shot.get("body") or "").lower()
    if sit == "penalty":
        return "penalty"
    if sit == "corner":
        return "corner"
    if sit in ("free-kick", "set-piece"):
        return "set_piece"
    if sit == "fast-break":
        return "fast_break"
    if sit == "assisted":
        return "cross_head" if body == "head" else "combo"
    return "individual"  # 'regular': asistsiz oyun içi şut


async def _get(c: httpx.AsyncClient, path: str, **params) -> Any:
    async with _sem:
        r = await c.get(f"{BASE}{path}", params=params)
    if r.status_code != 200:
        return None
    return r.json()


async def _team_profile(c: httpx.AsyncClient, team_id: int, before: datetime) -> Optional[dict[str, Any]]:
    hit = _cache.get(team_id)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    body = await _get(c, "/events/", team_id=team_id, status="finished", limit=50,
                      date_from=(before - timedelta(days=150)).date().isoformat(), date_to=before.date().isoformat())
    rows = (body or {}).get("results") or []
    rows.sort(key=lambda e: str(e.get("event_date")), reverse=True)
    rows = rows[:LAST_N]
    if not rows:
        return None

    async def one(ev: dict[str, Any]):
        return ev, await _get(c, f"/events/{ev['id']}/stats/")

    results = await asyncio.gather(*[one(e) for e in rows])
    kinds: dict[str, int] = {}
    scorers: dict[int, int] = {}
    goals = 0
    used = 0
    corners = shots = poss_sum = poss_n = 0.0
    for ev, st in results:
        if not isinstance(st, dict):
            continue
        is_home = ev.get("home_team_id") == team_id
        side = "home" if is_home else "away"
        sm = st.get("shotmap") if isinstance(st.get("shotmap"), list) else None
        if sm is None:
            continue
        used += 1
        for s in sm:
            if bool(s.get("home")) != is_home or s.get("type") != "goal":
                continue
            if "own" in str(s.get("gtype") or "").lower():
                continue
            k = _kind(s)
            kinds[k] = kinds.get(k, 0) + 1
            goals += 1
            if s.get("player_id") is not None:
                scorers[int(s["player_id"])] = scorers.get(int(s["player_id"]), 0) + 1
        side_stats = st.get(side) or (st.get("stats") or {}).get(side) or {}
        if isinstance(side_stats, dict):
            if isinstance(side_stats.get("corner_kicks"), (int, float)):
                corners += side_stats["corner_kicks"]
            if isinstance(side_stats.get("total_shots"), (int, float)):
                shots += side_stats["total_shots"]
            if isinstance(side_stats.get("ball_possession"), (int, float)):
                poss_sum += side_stats["ball_possession"]
                poss_n += 1
    if used == 0 or goals == 0:
        data = {"matches": used, "goals": goals, "kinds": kinds, "scorers": [], "corners_pm": None, "shots_pm": None, "possession": None}
        _cache[team_id] = (time.time(), data)
        return data if used else None
    top = sorted(scorers.items(), key=lambda kv: -kv[1])[:8]
    out_scorers = []
    for pid, n in top:
        if pid not in _names:
            p = await _get(c, f"/players/{pid}/")
            name = (p or {}).get("name") or (p or {}).get("short_name")
            if name:
                _names[pid] = str(name)
        if pid in _names:
            out_scorers.append({"name": _names[pid], "goals": n})
    data = {
        "matches": used, "goals": goals, "kinds": kinds, "scorers": out_scorers,
        "corners_pm": round(corners / used, 1) if corners else None,
        "shots_pm": round(shots / used, 1) if shots else None,
        "possession": round(poss_sum / poss_n, 1) if poss_n else None,
    }
    _cache[team_id] = (time.time(), data)
    return data


async def profile(home: str, away: str, kickoff: Optional[str]) -> dict[str, Any]:
    key = os.getenv("BSD_API_KEY")
    if not key:
        return {"available": False, "note": "Veri kaynağı yapılandırılmamış."}
    ko = inj._parse_dt(kickoff) if kickoff else datetime.now(timezone.utc)
    ko = ko or datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=25.0, headers={"Authorization": f"Token {key}"}) as c:
            ids = None
            off = 0
            while ids is None and off < 800:
                body = await _get(c, "/events/", date_from=(ko - timedelta(days=1)).date().isoformat(),
                                  date_to=(ko + timedelta(days=1)).date().isoformat(), limit=200, offset=off)
                rows = (body or {}).get("results") or []
                if not rows:
                    break
                for e in rows:
                    if inj._same_team(home, str(e.get("home_team") or "")) and inj._same_team(away, str(e.get("away_team") or "")):
                        ids = (e.get("home_team_id"), e.get("away_team_id"))
                        break
                off += 200
            if not ids or None in ids:
                return {"available": False, "note": "Bu maç için takım verisi eşleştirilemedi."}
            hp, ap = await asyncio.gather(_team_profile(c, int(ids[0]), ko), _team_profile(c, int(ids[1]), ko))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bsd profil hatası: %s", exc)
        return {"available": False, "note": "Takım verisi şu anda alınamadı."}
    if not hp and not ap:
        return {"available": False, "note": "Takımların son maç verisi bulunamadı."}

    # Eksik (sakat/cezalı) oyuncuları golcü listesinden çıkar.
    try:
        miss = await inj.lookup(home, away, kickoff)
    except Exception:  # noqa: BLE001
        miss = {}

    def drop(prof, plist):
        if not prof:
            return prof
        gone = {inj._norm(p["name"]) for p in (plist or []) if str(p.get("status") or "").lower() in ("injured", "suspended")}
        return {**prof, "scorers": [s for s in prof["scorers"] if inj._norm(s["name"]) not in gone], "excluded": len(gone)}

    return {"available": True, "source": "BSD", "last_n": LAST_N,
            "home": drop(hp, miss.get("home") if miss.get("available") else []),
            "away": drop(ap, miss.get("away") if miss.get("available") else [])}
