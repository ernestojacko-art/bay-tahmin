"""Tek bir 5DollarFootballAPI kapısı: provider ve bridge AYNI hız sınırını paylaşır.

Hesabın gerçek sınırı dakikada ~10 istek (loglardan doğrulandı). İki ayrı
sınırlayıcı (bridge + provider) birbirinden habersiz çalıştığında toplam hız
sınırı aşıyor ve 429 yağıyordu. Burada tek sıra vardır; 429'da aralık büyür,
başarıda yavaşça tabana iner.
"""
from __future__ import annotations

import asyncio
import contextvars
import os

_BASE = float(os.getenv("FIVE_DOLLAR_MIN_INTERVAL_SECONDS", "5.5"))
_state = {"interval": _BASE, "next": 0.0, "cooldown": 0.0, "ok": 0}
_lock: asyncio.Lock | None = None
# Arka plan işleri (önceden hesaplama, Spor Toto bülteni) bu bayrakla çalışır; kullanıcı
# isteği (maç analizi tıklaması) bekliyorsa arka plan istekleri geri çekilir.
background: contextvars.ContextVar[bool] = contextvars.ContextVar("api_gate_background", default=False)
# Çok düşük öncelik: ölçüm/veri toplama işleri (geçmiş maç toplama, geriye dönük test). Hem kullanıcıya hem
# diğer arka plan işlerine (önceden analiz) yol verir.
low: contextvars.ContextVar[bool] = contextvars.ContextVar("api_gate_low", default=False)
_fg_waiting = 0
_bg_waiting = 0


async def acquire() -> None:
    global _lock, _fg_waiting, _bg_waiting
    if _lock is None:
        _lock = asyncio.Lock()
    if low.get():
        while _fg_waiting > 0 or _bg_waiting > 0:
            await asyncio.sleep(1.0)
        await _take()
        return
    if background.get():
        _bg_waiting += 1
        try:
            while _fg_waiting > 0:
                await asyncio.sleep(0.5)
            await _take()
        finally:
            _bg_waiting -= 1
        return
    _fg_waiting += 1
    try:
        await _take()
    finally:
        _fg_waiting -= 1


async def _take() -> None:
    async with _lock:
        loop = asyncio.get_running_loop()
        wait = max(_state["next"], _state["cooldown"]) - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _state["next"] = loop.time() + _state["interval"]


def on_rate_limited(delay: float) -> None:
    loop = asyncio.get_running_loop()
    _state["cooldown"] = max(_state["cooldown"], loop.time() + delay)
    _state["interval"] = min(12.0, _state["interval"] * 1.4)
    _state["ok"] = 0


def on_ok() -> None:
    _state["ok"] += 1
    if _state["ok"] >= 15 and _state["interval"] > _BASE:
        _state["interval"] = max(_BASE, _state["interval"] * 0.9)
        _state["ok"] = 0
