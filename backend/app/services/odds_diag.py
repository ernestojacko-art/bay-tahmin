"""Tek seferlik tanılama: aynı maç için oranın hangi uç noktadan/hangi şirketten nasıl geldiğini karşılaştırır.

Sonuç Supabase `analysis_cache` tablosunda `diag:odds` anahtarına yazılır (gizli bilgi içermez).
Amaç: ana sitedeki "Bet 365" oranı ile Sürpriz Veri'deki açılış/kapanış oranlarının neden farklı çıktığını tespit etmek.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import accuracy
from app.core.major_leagues import is_major_league
from app.services.fixture_selection import istanbul_today

logger = logging.getLogger("odds_diag")
KEY = "diag:odds:v2"


def _short(obj, n=2500):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _one_x_two(odds_block):
    """Yanıt içinden 1x2 bloğunu (aşamalarıyla) bulup döndürür."""
    if isinstance(odds_block, dict):
        for k in ("1x2", "match_odds", "1X2"):
            if k in odds_block:
                return {k: odds_block[k]}
    return None


async def run(provider) -> None:
    try:
        rows = await accuracy._db("GET", "analysis_cache", params={"key": f"eq.{KEY}", "select": "key"})
        if rows:
            return
    except Exception:  # noqa: BLE001
        pass
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "fixtures": []}
    try:
        target = istanbul_today() + timedelta(days=1)
        fixtures = await provider.get_fixtures(target, status="scheduled")
        picked = [f for f in fixtures if is_major_league(f.league_name)][:3] or fixtures[:3]
        try:
            out["bookmakers"] = _short(await provider._request("bookmakers", {"lang": "en"}), 1500)
        except Exception as exc:  # noqa: BLE001
            out["bookmakers"] = f"hata: {exc}"
        for f in picked:
            mid = f.match_id
            item: dict = {"match_id": mid, "match": f"{f.home_team.name} - {f.away_team.name}", "league": f.league_name,
                          "kickoff": f.kickoff.isoformat()}
            calls = {
                "A_detail_include_odds": (f"fixtures/{mid}", {"include": "odds", "lang": "en"}),
                "B_odds_default(ana site)": (f"fixtures/{mid}/odds", {"lang": "en"}),
                "C_odds_bet365(Surpriz)": (f"fixtures/{mid}/odds", {"bookmakers": "bet365", "lang": "en"}),
            }
            for name, (path, params) in calls.items():
                try:
                    payload = await provider._request(path, params)
                    data = payload.get("data") if isinstance(payload, dict) else payload
                    if name.startswith("A"):
                        odds = data.get("odds") if isinstance(data, dict) else None
                        item[name] = _short({"1x2": _one_x_two(odds), "odds_keys": list(odds)[:15] if isinstance(odds, dict) else type(odds).__name__})
                    elif name.startswith("D"):
                        pts = data if isinstance(data, list) else (data.get("history") if isinstance(data, dict) else None)
                        item[name] = _short({"n": len(pts) if isinstance(pts, list) else None,
                                             "first": pts[0] if pts else None, "last": pts[-1] if pts else None})
                    else:
                        books = data.get("bookmakers") if isinstance(data, dict) else None
                        summ = []
                        for b in (books if isinstance(books, list) else []):
                            if not isinstance(b, dict):
                                continue
                            bo = b.get("odds") if isinstance(b.get("odds"), dict) else {}
                            summ.append({"name": b.get("name") or b.get("slug"), "keys": [k for k in b if k != "odds"],
                                         "1x2": _one_x_two(bo)})
                        item[name] = _short({"n_books": len(summ), "books": summ}, 3500)
                except Exception as exc:  # noqa: BLE001
                    item[name] = f"hata: {type(exc).__name__}: {str(exc)[:200]}"
            out["fixtures"].append(item)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    try:
        await accuracy._db("POST", "analysis_cache", params={"on_conflict": "key"}, prefer=True,
                           json_body={"key": KEY, "payload": out, "updated_at": datetime.now(timezone.utc).isoformat()})
        logger.info("odds_diag kaydedildi")
    except Exception as exc:  # noqa: BLE001
        logger.warning("odds_diag yazılamadı: %s", exc)
