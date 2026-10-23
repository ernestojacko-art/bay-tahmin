"""Günün tüm maçlarının analizini arka planda önceden hesaplar ve Supabase'e kaydeder.

Kullanıcı bir maça tıkladığında analiz hazır olur (veri sağlayıcının dakikada ~10
istek sınırı yüzünden canlı hesaplama dakikalar sürüyordu). İstekler arka plan
önceliğiyle çalışır: kullanıcı tıklaması her zaman öne geçer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import accuracy
from app.core import api_gate

logger = logging.getLogger("warm")
ISTANBUL = ZoneInfo("Europe/Istanbul")
CYCLE_SECONDS = 15 * 60
FRESH_SECONDS = 5 * 3600  # analysis_service.STORE_MAX_AGE_SECONDS (6s) altında kal


async def _fresh_keys() -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=FRESH_SECONDS)).isoformat()
    rows = await accuracy._db("GET", "analysis_cache", params={
        "select": "key", "updated_at": f"gte.{cutoff}", "limit": "5000"})
    return {r["key"] for r in rows or []}


async def warm_loop(analysis_service) -> None:
    api_gate.background.set(True)
    provider = analysis_service._provider
    await asyncio.sleep(45)
    while True:
        try:
            fresh = await _fresh_keys()
            today = datetime.now(ISTANBUL).date()
            done = 0
            for offset in (0, 1):
                day: date = today + timedelta(days=offset)
                try:
                    fixtures = await provider.get_fixtures(day)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("warm: %s fikstürü alınamadı: %s", day, exc)
                    continue
                now = datetime.now(timezone.utc)
                todo = [f for f in fixtures
                        if f.kickoff > now and f.match_id in getattr(provider, "open_odds_match_ids", set())
                        and f"{provider.name}:{f.match_id}:dataset" not in fresh]
                todo.sort(key=lambda f: f.kickoff)
                from app.services import season_service
                for lid in dict.fromkeys(f.league_id for f in todo):
                    try:
                        await season_service.get_season(provider, lid, wait=None, bg=True)
                    except Exception as exc:  # noqa: BLE001
                        logger.info("warm: sezon verisi alınamadı (%s): %s", lid, exc)
                for f in todo:
                    try:
                        await analysis_service.get_dataset(f.match_id)
                        await analysis_service.analyze_match(f.match_id, include_evidence=False)
                        done += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.info("warm: %s analiz edilemedi: %s", f.match_id, exc)
            logger.info("warm: döngü bitti, %d maç hazırlandı", done)
        except Exception as exc:  # noqa: BLE001
            logger.warning("warm: döngü hatası: %s", exc)
        await asyncio.sleep(CYCLE_SECONDS)
