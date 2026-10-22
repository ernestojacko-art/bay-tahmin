"""Geçmiş bitmiş maçların (skor + bahis oranları) toplu kaydı: model geliştirme için eğitim/ölçüm verisi."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import accuracy
from app.core import api_gate

logger = logging.getLogger("history")
ISTANBUL = ZoneInfo("Europe/Istanbul")
DAYS_BACK = 120


def _price(markets, name_part: str, label: str):
    for m in markets or []:
        if name_part in (m.market_name or ""):
            for s in m.selections:
                if s.label == label and s.price and s.price > 1:
                    return float(s.price)
    return None


async def collect(analysis_service) -> int:
    provider = analysis_service._provider
    today = datetime.now(ISTANBUL).date()
    have = await accuracy._db("GET", "hist_matches", params={"select": "kickoff", "limit": "50000", "order": "kickoff.desc"})
    done_days = {}
    for r in have or []:
        d = datetime.fromisoformat(r["kickoff"].replace("Z", "+00:00")).astimezone(ISTANBUL).date()
        done_days[d] = done_days.get(d, 0) + 1
    total = 0
    for back in range(1, DAYS_BACK + 1):
        day = today - timedelta(days=back)
        if done_days.get(day, 0) >= 20:   # gün zaten toplanmış
            continue
        try:
            fixtures = await provider.get_fixtures(day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history: %s alınamadı: %s", day, exc)
            continue
        rows = []
        for f in fixtures:
            res = getattr(provider, "result_cache", {}).get(f.match_id)
            odds = provider._odds_cache.get(f.match_id)
            if res is None or not odds:
                continue
            o1, ox, o2 = (_price(odds, "Maç Sonucu 1X2", k) for k in ("1", "X", "2"))
            if not (o1 and ox and o2):
                continue
            rows.append({
                "match_id": f.match_id, "kickoff": f.kickoff.isoformat(), "league": f.league_name,
                "home": f.home_team.name, "away": f.away_team.name, "goals_home": res[0], "goals_away": res[1],
                "o1": o1, "ox": ox, "o2": o2,
                "o_over25": _price(odds, "Alt/Üst Gol (", "Üst 2.5"), "o_under25": _price(odds, "Alt/Üst Gol (", "Alt 2.5"),
                "o_btts_yes": _price(odds, "Karşılıklı Gol", "Var"), "o_btts_no": _price(odds, "Karşılıklı Gol", "Yok"),
            })
        for i in range(0, len(rows), 200):
            try:
                await accuracy._db("POST", "hist_matches", params={"on_conflict": "match_id"}, prefer=True, json_body=rows[i:i + 200])
            except Exception as exc:  # noqa: BLE001
                logger.warning("history: yazılamadı: %s", exc)
        total += len(rows)
        logger.info("history: %s -> %d maç", day, len(rows))
    return total


async def loop(analysis_service) -> None:
    api_gate.background.set(True)
    await asyncio.sleep(180)
    while True:
        try:
            n = await collect(analysis_service)
            logger.info("history: tur bitti, %d maç", n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history döngü hatası: %s", exc)
        await asyncio.sleep(6 * 3600)
