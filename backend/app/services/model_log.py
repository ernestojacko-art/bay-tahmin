"""Model kayıt defteri: her analizin olasılıkları, bahis oranları ve (maç bitince) gerçek skor
kalıcı olarak saklanır. Model başarısı bu kayıtlardan ölçülür; geçmiş maçlar için geriye dönük
test (backtest) de aynı tabloya yazılır.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import accuracy
from app.core import api_gate

logger = logging.getLogger("model_log")
ISTANBUL = ZoneInfo("Europe/Istanbul")
BACKTEST_DAYS = 10          # kaç gün geriye gidilir
BACKTEST_PER_CYCLE = 120    # bir turda en fazla bu kadar maç (hız sınırı: ~3 istek/maç)
CYCLE_SECONDS = 30 * 60


def _price(dataset, label: str):
    for m in dataset.odds_markets:
        if "Maç Sonucu 1X2" in (m.market_name or ""):
            for s in m.selections:
                if s.label == label and s.price and s.price > 1:
                    return float(s.price)
    return None


async def log_prediction(prediction, dataset, source: str = "live") -> None:
    """Best-effort: hata fırlatmaz."""
    try:
        fx = dataset.fixture
        ox, mc = prediction.one_x_two, prediction.market_comparison
        mi = mc.market_implied if mc and mc.market_available else None
        main = next((l for l in prediction.over_under if abs(l.line - 2.5) < 1e-6), None)
        row = {
            "match_id": str(fx.match_id), "source": source, "kickoff": fx.kickoff.isoformat(),
            "league": fx.league_name, "home": prediction.home_team.team_name, "away": prediction.away_team.team_name,
            "p1": ox.home_win, "px": ox.draw, "p2": ox.away_win,
            "m1": mi.home_win if mi else None, "mx": mi.draw if mi else None, "m2": mi.away_win if mi else None,
            "o1": _price(dataset, "1"), "ox": _price(dataset, "X"), "o2": _price(dataset, "2"),
            "p_over25": main.over if main else None, "p_btts": prediction.btts_yes_probability,
            "confidence": prediction.confidence.confidence,
        }
        await accuracy._db("POST", "model_log", params={"on_conflict": "match_id"}, prefer=True, json_body=row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("model_log yazılamadı: %s", exc)


async def _logged_ids(since_iso: str) -> set[str]:
    rows = await accuracy._db("GET", "model_log", params={"select": "match_id", "kickoff": f"gte.{since_iso}", "limit": "5000"})
    return {r["match_id"] for r in rows or []}


async def resolve_results(provider, limit: int = 40) -> int:
    """Bitmiş ama skoru işlenmemiş kayıtların skorunu sağlayıcıdan alır."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    rows = await accuracy._db("GET", "model_log", params={
        "select": "match_id", "resolved_at": "is.null", "kickoff": f"lt.{cutoff}", "order": "kickoff.asc", "limit": str(limit)})
    done = 0
    for r in rows or []:
        try:
            payload = await provider._request(f"fixtures/{r['match_id']}", {"lang": "en"})
            raw = (payload or {}).get("data") or {}
            g = raw.get("goals") or {}
            if str(raw.get("status") or "").lower() not in {"finished", "ft", "aet", "pen"} or g.get("home") is None or g.get("away") is None:
                continue
            await accuracy._db("PATCH", "model_log", params={"match_id": f"eq.{r['match_id']}"}, json_body={
                "goals_home": int(g["home"]), "goals_away": int(g["away"]), "resolved_at": datetime.now(timezone.utc).isoformat()})
            done += 1
        except Exception as exc:  # noqa: BLE001
            logger.info("sonuç alınamadı %s: %s", r["match_id"], exc)
    return done


async def backtest_cycle(analysis_service) -> int:
    """Son günlerin bitmiş maçlarını (maç öncesi verilerle) analiz edip skorla birlikte kaydeder."""
    provider = analysis_service._provider
    today = datetime.now(ISTANBUL).date()
    since = (datetime.now(timezone.utc) - timedelta(days=BACKTEST_DAYS + 1)).isoformat()
    logged = await _logged_ids(since)
    done = 0
    for back in range(1, BACKTEST_DAYS + 1):
        day = today - timedelta(days=back)
        try:
            fixtures = await provider.get_fixtures(day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest: %s alınamadı: %s", day, exc)
            continue
        opened = getattr(provider, "open_odds_match_ids", set())
        for f in fixtures:
            if done >= BACKTEST_PER_CYCLE:
                return done
            if f.match_id in logged or f.match_id not in opened:
                continue
            try:
                dataset = await analysis_service.get_dataset(f.match_id)
                prediction = analysis_service._prediction_engine.analyze(dataset)
                await log_prediction(prediction, dataset, source="backtest")
                logged.add(f.match_id)
                done += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("backtest: %s atlandı: %s", f.match_id, exc)
    return done


async def loop(analysis_service) -> None:
    api_gate.background.set(True)
    await asyncio.sleep(120)
    while True:
        try:
            n = await backtest_cycle(analysis_service)
            r = await resolve_results(analysis_service._provider)
            logger.info("model_log: %d backtest kaydı, %d sonuç işlendi", n, r)
        except Exception as exc:  # noqa: BLE001
            logger.warning("model_log döngü hatası: %s", exc)
        await asyncio.sleep(CYCLE_SECONDS)


def _outcome(gh: int, ga: int) -> int:
    return 0 if gh > ga else 1 if gh == ga else 2


async def report() -> dict:
    rows = await accuracy._db("GET", "model_log", params={"select": "*", "resolved_at": "not.is.null", "limit": "5000"})
    n = len(rows or [])
    out: dict = {"resolved_matches": n}
    if not n:
        return out

    def logloss(p, k):
        return -math.log(max(1e-6, p[k]))

    def brier(p, k):
        return sum((p[i] - (1.0 if i == k else 0.0)) ** 2 for i in range(3))

    m_rows = [r for r in rows if r["m1"] is not None]
    acc_model = acc_mkt = ll_model = ll_mkt = br_model = br_mkt = 0.0
    for r in m_rows:
        k = _outcome(r["goals_home"], r["goals_away"])
        pm = [r["p1"], r["px"], r["p2"]]; pk = [r["m1"], r["mx"], r["m2"]]
        acc_model += pm.index(max(pm)) == k; acc_mkt += pk.index(max(pk)) == k
        ll_model += logloss(pm, k); ll_mkt += logloss(pk, k); br_model += brier(pm, k); br_mkt += brier(pk, k)
    c = len(m_rows) or 1
    out["1x2"] = {"matches": len(m_rows),
                  "accuracy_model": round(acc_model / c, 3), "accuracy_market": round(acc_mkt / c, 3),
                  "logloss_model": round(ll_model / c, 4), "logloss_market": round(ll_mkt / c, 4),
                  "brier_model": round(br_model / c, 4), "brier_market": round(br_mkt / c, 4),
                  "note": "Düşük logloss/brier daha iyi. Model piyasadan kötüyse piyasa oranı temel alınmalı."}
    # Kalibrasyon: modelin ev/ber/dep olasılığı ile gerçekleşme oranı.
    bins = {}
    for r in rows:
        k = _outcome(r["goals_home"], r["goals_away"])
        for i, p in enumerate((r["p1"], r["px"], r["p2"])):
            b = min(9, int(p * 10)); s = bins.setdefault(b, [0, 0.0, 0]); s[0] += 1; s[1] += p; s[2] += (i == k)
    out["calibration_1x2"] = [{"band": f"%{b*10}-{b*10+10}", "n": s[0], "avg_predicted": round(s[1]/s[0], 3), "actual": round(s[2]/s[0], 3)} for b, s in sorted(bins.items())]
    ov = [r for r in rows if r["p_over25"] is not None]
    if ov:
        hit = sum(((r["goals_home"] + r["goals_away"]) > 2.5) == (r["p_over25"] >= 0.5) for r in ov)
        out["over25"] = {"matches": len(ov), "accuracy": round(hit / len(ov), 3),
                         "base_rate_over": round(sum((r["goals_home"] + r["goals_away"]) > 2.5 for r in ov) / len(ov), 3)}
    bt = [r for r in rows if r["p_btts"] is not None]
    if bt:
        hit = sum(((r["goals_home"] > 0 and r["goals_away"] > 0)) == (r["p_btts"] >= 0.5) for r in bt)
        out["btts"] = {"matches": len(bt), "accuracy": round(hit / len(bt), 3),
                       "base_rate_yes": round(sum(r["goals_home"] > 0 and r["goals_away"] > 0 for r in bt) / len(bt), 3)}
    return out
