"""BSD (sports.bzzoiro.com) ücretsiz API'sinden maç başına sakat/cezalı/şüpheli oyuncu listesi.

- Veri yalnızca BSD'nin verdiği kadarıdır; boş liste = "kayıtlı eksik oyuncu yok", veri yok demek olabilir (kaynak not eder).
- Maç eşleştirmesi takım adı + tarihle yapılır; iki takım da güvenle eşleşmezse veri GÖSTERİLMEZ.
- Maç başlamadan önceki son durum `bsd_unavailable_log` tablosuna kaydedilir (ileride isabet etkisini ölçmek için).
- Tahmin yüzdelerini DEĞİŞTİRMEZ.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

import accuracy

logger = logging.getLogger("bsd")
BASE = "https://sports.bzzoiro.com"
WINDOW_BEFORE_DAYS = 1
WINDOW_AFTER_DAYS = 4
CACHE_SECONDS = 30 * 60
LOOP_SECONDS = 3 * 3600

_cache: dict[str, Any] = {"ts": 0.0, "events": []}
_lock = asyncio.Lock()


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|fk|sk|as|a s|a\.s|spor kulubu|kulubu|club|afc|cf|sc|ac|ss)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _same_team(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if (x in y or y in x) and min(len(x), len(y)) >= 5:
        return True
    return SequenceMatcher(None, x, y).ratio() >= 0.86


def _team_name(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or v.get("short_name") or "")
    return str(v or "")


def _parse_dt(raw: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _players(v: Any) -> list[dict[str, Any]]:
    out = []
    for p in v or []:
        if isinstance(p, dict) and p.get("name"):
            out.append({"name": str(p["name"]), "status": p.get("status"), "reason": p.get("reason"),
                        "expected_return": p.get("expected_return")})
    return out


async def _fetch_events(d_from: date, d_to: date) -> list[dict[str, Any]]:
    key = os.getenv("BSD_API_KEY")
    if not key:
        return []
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25.0, headers={"Authorization": f"Token {key}"}) as client:
        offset = 0
        for _ in range(10):
            r = await client.get(f"{BASE}/api/events/", params={
                "date_from": d_from.isoformat(), "date_to": d_to.isoformat(), "limit": 200, "offset": offset})
            if r.status_code != 200:
                logger.warning("bsd: HTTP %s", r.status_code)
                break
            rows = r.json().get("results") or []
            out.extend(rows)
            if len(rows) < 200:
                break
            offset += 200
    return out


async def events(force: bool = False) -> list[dict[str, Any]]:
    async with _lock:
        if not force and _cache["events"] and time.time() - _cache["ts"] < CACHE_SECONDS:
            return _cache["events"]
        today = datetime.now(timezone.utc).date()
        try:
            ev = await _fetch_events(today - timedelta(days=WINDOW_BEFORE_DAYS), today + timedelta(days=WINDOW_AFTER_DAYS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bsd: çekilemedi: %s", exc)
            return _cache["events"]
        if ev:
            _cache["events"], _cache["ts"] = ev, time.time()
        return _cache["events"]


async def lookup(home: str, away: str, kickoff: Optional[str]) -> dict[str, Any]:
    ko = _parse_dt(kickoff) if kickoff else None
    best = None
    for e in await events():
        eh, ea = _team_name(e.get("home_team")), _team_name(e.get("away_team"))
        if not (_same_team(home, eh) and _same_team(away, ea)):
            continue
        edt = _parse_dt(e.get("event_date"))
        if ko and edt and abs((edt - ko).total_seconds()) > 36 * 3600:
            continue
        best = e
        break
    if best is None:
        return {"available": False, "note": "Bu maç için eksik oyuncu verisi bulunamadı."}
    up = best.get("unavailable_players")
    if not isinstance(up, dict):
        return {"available": False, "note": "Kaynak bu maç için henüz eksik oyuncu listesi vermedi."}
    return {"available": True, "source": "BSD", "home": _players(up.get("home")), "away": _players(up.get("away")),
            "as_of": datetime.fromtimestamp(_cache["ts"], timezone.utc).isoformat() if _cache["ts"] else None}


async def record_upcoming() -> int:
    """Başlamamış maçların son eksik oyuncu durumunu kaydeder (başladıktan sonra dondurulur)."""
    now = datetime.now(timezone.utc)
    rows = []
    for e in await events(force=True):
        up = e.get("unavailable_players")
        edt = _parse_dt(e.get("event_date"))
        if not isinstance(up, dict) or edt is None or edt <= now:
            continue
        lg = e.get("league")
        rows.append({
            "event_id": str(e.get("id")), "kickoff": edt.isoformat(),
            "league": str(lg.get("name") if isinstance(lg, dict) else lg or ""),
            "home_team": _team_name(e.get("home_team")), "away_team": _team_name(e.get("away_team")),
            "home_unavailable": _players(up.get("home")), "away_unavailable": _players(up.get("away")),
            "updated_at": now.isoformat(),
        })
    for i in range(0, len(rows), 100):
        await accuracy._db("POST", "bsd_unavailable_log", params={"on_conflict": "event_id"}, json_body=rows[i:i + 100], prefer=True)
    return len(rows)


async def loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            n = await record_upcoming()
            logger.info("bsd: %d maçın eksik oyuncu durumu kaydedildi", n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bsd: kayıt başarısız: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)
