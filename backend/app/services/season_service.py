"""Sezon puan tablosu ve takım sezon istatistikleri.

Liginin sezon maçları sağlayıcıdan (sayfalı) alınır, sezon başlangıcı maç tarihlerindeki en son uzun
boşluktan (sezon arası) bulunur; genel / iç saha / dış saha puan tablosu bu maçlardan hesaplanır.
Sonuç Supabase'e kalıcı yazılır (sunucu uyusa da hazır kalır).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import accuracy

logger = logging.getLogger("season")
TTL_SECONDS = 12 * 3600
MEM_TTL_SECONDS = 3600
SEASON_GAP_DAYS = 45

_mem: dict[str, tuple[float, dict]] = {}
_tasks: dict[str, asyncio.Task] = {}


def _kick(raw: dict) -> datetime | None:
    v = raw.get("kickoff_utc") or raw.get("kickoff")
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _season_rows(rows: list[dict]) -> list[dict]:
    dated = [(k, r) for r in rows if (k := _kick(r)) is not None]
    dated.sort(key=lambda x: x[0])
    if not dated:
        return []
    start = 0
    for i in range(len(dated) - 1, 0, -1):
        if (dated[i][0] - dated[i - 1][0]) > timedelta(days=SEASON_GAP_DAYS):
            start = i
            break
    return [r for _, r in dated[start:]]


def _blank() -> dict:
    return {"p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0}


def build_table(rows: list[dict]) -> dict:
    teams: dict[str, dict] = {}
    for r in sorted(_season_rows(rows), key=lambda x: _kick(x) or datetime.min.replace(tzinfo=timezone.utc)):
        t = r.get("teams") or {}
        h, a, g = t.get("home") or {}, t.get("away") or {}, r.get("goals") or {}
        hg, ag = g.get("home"), g.get("away")
        if hg is None or ag is None or h.get("id") is None or a.get("id") is None:
            continue
        hg, ag = int(hg), int(ag)
        for side, name in ((h, h.get("name")), (a, a.get("name"))):
            teams.setdefault(str(side["id"]), {"id": str(side["id"]), "name": str(name or "?"), "all": _blank(),
                                               "home": _blank(), "away": _blank(), "form": []})
        for team, venue, gf, ga in ((teams[str(h["id"])], "home", hg, ag), (teams[str(a["id"])], "away", ag, hg)):
            res = "w" if gf > ga else "d" if gf == ga else "l"
            for key in ("all", venue):
                b = team[key]
                b["p"] += 1; b[res] += 1; b["gf"] += gf; b["ga"] += ga
            team["form"].append(res.upper().replace("W", "G").replace("D", "B").replace("L", "M"))
    table = []
    for team in teams.values():
        b = team["all"]
        table.append({**team, "pts": b["w"] * 3 + b["d"], "gd": b["gf"] - b["ga"], "form": team["form"][-5:]})
    table.sort(key=lambda x: (x["pts"], x["gd"], x["all"]["gf"]), reverse=True)
    for i, row in enumerate(table, 1):
        row["pos"] = i
    return {"table": table, "generated_at": datetime.now(timezone.utc).isoformat()}


async def _compute(provider, league_id: str) -> dict:
    rows = await provider.fetch_league_finished(league_id)
    data = build_table(rows)
    try:
        await accuracy._db("POST", "analysis_cache", params={"on_conflict": "key"}, prefer=True, json_body={
            "key": f"season:{league_id}", "payload": data, "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:  # noqa: BLE001
        logger.debug("sezon verisi yazılamadı: %s", exc)
    _mem[league_id] = (time.time(), data)
    return data


async def _load_saved(league_id: str) -> dict | None:
    try:
        rows = await accuracy._db("GET", "analysis_cache", params={"key": f"eq.season:{league_id}", "select": "payload,updated_at"})
        if not rows:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["updated_at"].replace("Z", "+00:00"))).total_seconds()
        return rows[0]["payload"] if age <= TTL_SECONDS else None
    except Exception:  # noqa: BLE001
        return None


async def get_season(provider, league_id: str, wait: float | None = 6.0, bg: bool = False) -> dict | None:
    """Hazırsa hemen döner; değilse arka planda hesaplatır ve `wait` saniye bekler (None -> hazırlanıyor)."""
    if not league_id or not hasattr(provider, "fetch_league_finished"):
        return None
    hit = _mem.get(league_id)
    if hit and time.time() - hit[0] < MEM_TTL_SECONDS:
        return hit[1]
    saved = await _load_saved(league_id)
    if saved is not None:
        _mem[league_id] = (time.time(), saved)
        return saved
    task = _tasks.get(league_id)
    if task is None or task.done():
        async def run():
            from app.core import api_gate
            api_gate.background.set(bg)  # kullanıcı sayfası için çağrılırsa öncelikli
            return await _compute(provider, league_id)
        task = asyncio.create_task(run())
        _tasks[league_id] = task
    if wait is None:
        return await task
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
