"""Tarihsel havuzun Supabase'te KALICI saklanması (deploy/uyku havuzu sıfırlamasın).

Etkinleştirme (Render > surpriz-veri-api > Environment):
  SURPRISE_SUPABASE_URL          örn. https://ufxrcsjiyfjqwyazkbeg.supabase.co
  SURPRISE_SUPABASE_SERVICE_KEY  Supabase service_role anahtarı (gizli tutulur)
Ayarlı değilse bu modül hiçbir şey yapmaz; havuz yalnızca bellek/disk ile çalışır.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, fields, is_dataclass
from typing import Any, Dict, List, Optional

import requests

from .models import (
    HistoricalMatch, MarketProfile, MatchOutcome, MatchRecord, Odds, OddsMovement,
)

logger = logging.getLogger("surpriz_veri.history_db")

TABLE = "surprise_history"
BATCH = 200
PAGE = 1000


def _cfg() -> Optional[tuple[str, str]]:
    url = (os.getenv("SURPRISE_SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SURPRISE_SUPABASE_SERVICE_KEY") or ""
    return (url, key) if url and key else None


def is_configured() -> bool:
    return _cfg() is not None


def _headers(key: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {"apikey": key, "Content-Type": "application/json"}
    # Yeni nesil anahtarlar (sb_secret_... / sb_publishable_...) JWT değildir; Authorization
    # başlığında gönderilirse Supabase isteği "anon" rolüne düşürür. Yalnızca eski JWT
    # (service_role) anahtarlarında Bearer başlığı eklenir.
    if not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    h.update(extra or {})
    return h


def _build(cls, data: Any):
    if not isinstance(data, dict):
        return cls()
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _to_payload(match: HistoricalMatch) -> Dict[str, Any]:
    d = asdict(match)
    # Eşleştirme için gereksiz ve büyük olabilecek ham alanlar atılır.
    d["record"]["metadata"] = {}
    d["record"]["team_profile"] = {}
    return d


def _from_payload(d: Dict[str, Any]) -> Optional[HistoricalMatch]:
    try:
        r = d["record"]
        record = MatchRecord(
            match_id=str(r["match_id"]), home_team=r.get("home_team") or "", away_team=r.get("away_team") or "",
            kickoff_time=r.get("kickoff_time"), competition=r.get("competition"),
            opening_odds=_build(Odds, r.get("opening_odds")), closing_odds=_build(Odds, r.get("closing_odds")),
            odds_movement=_build(OddsMovement, r.get("odds_movement")),
            market_profile=_build(MarketProfile, r.get("market_profile")),
            outcome=_build(MatchOutcome, r.get("outcome")),
        )
        return HistoricalMatch(record=record, source=d.get("source"), source_timestamp=d.get("source_timestamp"))
    except Exception:
        return None


def save(matches: List[HistoricalMatch]) -> int:
    cfg = _cfg()
    if not cfg or not matches:
        return 0
    url, key = cfg
    saved = 0
    for i in range(0, len(matches), BATCH):
        chunk = matches[i:i + BATCH]
        rows = [
            {"match_id": str(m.record.match_id), "kickoff_time": m.record.kickoff_time, "payload": _to_payload(m)}
            for m in chunk
        ]
        try:
            resp = requests.post(
                f"{url}/rest/v1/{TABLE}?on_conflict=match_id", json=rows, timeout=30,
                headers=_headers(key, {"Prefer": "resolution=merge-duplicates,return=minimal"}),
            )
            if resp.status_code >= 300:
                logger.warning("Supabase yazma başarısız (%s): %s", resp.status_code, resp.text[:200])
            else:
                saved += len(rows)
        except requests.RequestException as exc:
            logger.warning("Supabase yazma hatası: %s", exc)
    return saved


def load_all(limit: int = 50000) -> List[HistoricalMatch]:
    cfg = _cfg()
    if not cfg:
        return []
    url, key = cfg
    out: List[HistoricalMatch] = []
    offset = 0
    while offset < limit:
        try:
            resp = requests.get(
                f"{url}/rest/v1/{TABLE}?select=payload&order=match_id.asc&limit={PAGE}&offset={offset}",
                headers=_headers(key), timeout=60,
            )
        except requests.RequestException as exc:
            logger.warning("Supabase okuma hatası: %s", exc)
            break
        if resp.status_code >= 300:
            logger.warning("Supabase okuma başarısız (%s): %s", resp.status_code, resp.text[:200])
            break
        rows = resp.json()
        if not rows:
            break
        for row in rows:
            m = _from_payload(row.get("payload") or {})
            if m is not None:
                out.append(m)
        if len(rows) < PAGE:
            break
        offset += PAGE
    return out
