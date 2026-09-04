"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE runtime agent.

This is the football-first layer used by both general and match chat. It
separates football evidence from bookmaker evidence and asks the language model
to explain, not invent, the numerical analysis.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import five_dollar_bridge as five
from football_intelligence_data import build_match_context

ISTANBUL = ZoneInfo("Europe/Istanbul")
ENGINE_NAME = "BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE"


def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def requested_count(message: str) -> int:
    m = re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b", norm(message))
    return max(1, min(int(m.group(1)), 20)) if m else 5


def wants_iyms(message: str) -> bool:
    return bool(re.search(r"iy\s*/?\s*ms|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu", norm(message)))


def wants_surprise(message: str) -> bool:
    return bool(re.search(r"sürpriz|surpriz", norm(message)))


def resolve_dates(message: str) -> List[date]:
    text = norm(message); today = datetime.now(ISTANBUL).date(); dates: List[date] = []
    if re.search(r"\b(yarın|yarin)\b", text): dates.append(today + timedelta(days=1))
    if re.search(r"\b(bugün|bugun)\b", text): dates.append(today)
    if re.search(r"\b(pazar|pazar günü|pazar gunu)\b", text):
        dates.append(today + timedelta(days=(6 - today.weekday()) % 7))
    if re.search(r"\b(cumartesi|cumartesi günü|cumartesi gunu)\b", text):
        dates.append(today + timedelta(days=(5 - today.weekday()) % 7))
    if re.search(r"\b(öbür gün|obur gun)\b", text): dates.append(today + timedelta(days=2))
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", text)
    if m:
        y = int(m.group(3) or today.year); y += 2000 if y < 100 else 0
        try: dates.append(date(y, int(m.group(2)), int(m.group(1))))
        except ValueError: pass
    return sorted(set(dates or [today]))


def market_probabilities(markets: List[Dict[str, Any]]) -> Dict[str, float]:
    for market in markets:
        mt = norm(market.get("type") or market.get("gameName"))
        if "1x2" not in mt and "maç sonucu" not in mt and "match" not in mt:
            continue
        raw = {}
        for odd in market.get("odds", []):
            value = str(odd.get("value") or "").strip()
            try: price = float(odd.get("odd"))
            except (TypeError, ValueError): continue
            if value and price > 1: raw[value] = 1 / price
        total = sum(raw.values())
        if total: return {k: round(v / total * 100, 2) for k, v in raw.items()}
    return {}


def football_model(context: Dict[str, Any]) -> Dict[str, Any]:
    h = context.get("home", {}); a = context.get("away", {})
    hf = h.get("recent_form", {}); af = a.get("recent_form", {})
    hs = h.get("standing", {}); ass = a.get("standing", {})
    signals = []

    def add(name: str, edge: float, weight: float, evidence: str):
        signals.append({"name": name, "edge": round(max(-1, min(1, edge)), 4), "weight": weight, "evidence": evidence})

    hpp, app = hf.get("points_per_game"), af.get("points_per_game")
    if hpp is not None and app is not None:
        add("Son 10 maç formu", (hpp - app) / 3, .30, f"{hf.get('form','')} vs {af.get('form','')}")
    hgf, agf = hf.get("goals_for_avg"), af.get("goals_for_avg")
    hga, aga = hf.get("goals_against_avg"), af.get("goals_against_avg")
    if hgf is not None and agf is not None:
        add("Gol üretimi", (hgf - agf) / (hgf + agf + 2), .20, f"{hgf} vs {agf} gol/maç")
    if hga is not None and aga is not None:
        add("Savunma", (aga - hga) / (hga + aga + 2), .18, f"Yenen gol {hga} vs {aga}")
    hp, ap = hs.get("position"), ass.get("position")
    if hp and ap:
        add("Lig sıralaması", (float(ap) - float(hp)) / 20, .17, f"{hp}. sıra vs {ap}. sıra")
    hpts, apts = hs.get("points"), ass.get("points")
    if hpts is not None and apts is not None:
        add("Lig puan gücü", (float(hpts) - float(apts)) / 40, .15, f"{hpts} vs {apts} puan")

    total_w = sum(x["weight"] for x in signals)
    edge = sum(x["edge"] * x["weight"] for x in signals) / total_w if total_w else 0.0
    home = 0.333 + .36 * edge
    away = 0.333 - .36 * edge
    draw = max(.05, 1 - home - away)
    probs = {"1": home, "X": draw, "2": away}
    total = sum(probs.values()); probs = {k: v / total * 100 for k, v in probs.items()}
    return {"probabilities": {k: round(v, 2) for k, v in probs.items()}, "edge": round(edge, 4), "signals": signals,
            "data_depth": len(signals), "method": "form + goals + defence + standings ensemble"}


def iyms_projection(model: Dict[str, Any], surprise: bool) -> List[Dict[str, Any]]:
    fp = {k: v / 100 for k, v in model["probabilities"].items()}
    # First-half prior is intentionally conservative: use a draw-heavy prior
    # and tilt it toward the full-match football model. It is NOT bookmaker odds.
    hp = {"1": .31, "X": .38, "2": .31}
    edge = model.get("edge", 0)
    hp["1"] += .12 * edge; hp["2"] -= .12 * edge
    combos = []
    for h in ("1", "X", "2"):
        for f in ("1", "X", "2"):
            value = f"{h}/{f}"
            if surprise and value in {"1/1", "X/X", "2/2"}: continue
            p = hp[h] * fp[f]
            combos.append({"selection": value, "probability": round(p * 100, 2), "odd_model": round(1 / p, 2) if p else None})
    return sorted(combos, key=lambda x: x["probability"], reverse=True)


async def build_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        context = await build_match_context(row)
    except Exception:
        context = {"source": "5DollarFootballAPI", "home": {}, "away": {}}
    model = football_model(context)
    return {"match": row, "context": context, "model": model, "markets": row.get("_markets") or []}


async def analyze_pool(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # League-batched history is cached by football_intelligence_data; bounded
    # concurrency protects the Pro rate window.
    sem = asyncio.Semaphore(3)
    async def one(row):
        async with sem:
            return await build_candidate(row)
    return await asyncio.gather(*(one(r) for r in rows))


def rank(candidates: List[Dict[str, Any]], message: str) -> List[Dict[str, Any]]:
    surprise, iyms = wants_surprise(message), wants_iyms(message)
    ranked = []
    for c in candidates:
        p = c["model"]["probabilities"]
        if iyms:
            for x in iyms_projection(c["model"], surprise):
                ranked.append({**c, "selection": x["selection"], "model_probability": x["probability"], "market": "İY / MS Football Intelligence Projection"})
        else:
            choice = max(p, key=p.get)
            score = p[choice]
            if surprise:
                # Surprise means football model materially disagrees with the
                # market favourite; no fabricated market is created.
                mp = market_probabilities(c["markets"])
                fav = max(mp, key=mp.get) if mp else None
                if fav and choice != fav:
                    score += 12
                else:
                    score -= 8
            ranked.append({**c, "selection": choice, "model_probability": score, "market": "Football Intelligence 1X2"})
    ranked.sort(key=lambda x: (x["model_probability"], x["model"].get("data_depth", 0)), reverse=True)
    out, used = [], set()
    for c in ranked:
        mid = str(c["match"].get("MatchID"))
        if mid in used: continue
        used.add(mid); out.append(c)
        if len(out) >= requested_count(message): break
    return out


def evidence_text(c: Dict[str, Any]) -> str:
    h, a = c["context"].get("home", {}), c["context"].get("away", {})
    return json.dumps({"home_form": h.get("recent_form"), "away_form": a.get("recent_form"),
                       "home_standing": h.get("standing"), "away_standing": a.get("standing"),
                       "model": c["model"], "market_probabilities": market_probabilities(c["markets"])}, ensure_ascii=False)


async def answer(main_module, message: str, history: List[Any] | None = None) -> Dict[str, Any]:
    dates = resolve_dates(message)
    groups = await asyncio.gather(*(five.get_matches(d.isoformat()) for d in dates))
    rows: List[Dict[str, Any]] = []
    for payload in groups:
        for row in payload.get("data") or []:
            row["_markets"] = five._markets_from_odds({"data": {"odds": (next((f.get("odds") for f in payload.get("data", []) if str(f.get("id")) == str(row.get("MatchID"))), {}))}}, live=False) if False else row.get("_markets", [])
            rows.append(row)
    # The fixture bridge keeps odds in its detail route; enrich only the
    # manageable candidate set. We first rank using football history/table data.
    candidates = await analyze_pool(rows)
    selected = rank(candidates, message)
    if not selected:
        return {"reply": "Bu istek için yeterli gerçek futbol verisi bulunamadı; veri uydurmuyorum.", "engine": ENGINE_NAME}

    dossier = []
    for c in selected:
        m = c["match"]
        dossier.append({"match_id": m.get("MatchID"), "match": m.get("Teams"), "date": m.get("Date"),
                        "selection": c["selection"], "model_probability": c["model_probability"],
                        "evidence": json.loads(evidence_text(c)), "market": c["market"]})
    prompt = f"""Sen {ENGINE_NAME} olarak çalışan profesyonel futbol analiz uzmanısın.
KURAL: Futbol modeli kararın ana kaynağıdır. Piyasa yalnızca yardımcı çapraz kontroldür. Eksik veriyi asla uydurma.
Kullanıcı: {message}
Analiz tarihi: {', '.join(d.strftime('%d.%m.%Y') for d in dates)}
Aşağıdaki DOSSIER gerçek 5DollarFootballAPI verisinden ve ondan türetilen futbol istatistiklerinden oluşur:
{json.dumps(dossier, ensure_ascii=False)}
Her seçimin nedenini; form, gol üretimi/savunma, lig gücü/sıralama ve model olasılığı açısından açıkla. İY/MS ise bunun Football Intelligence model projeksiyonu olduğunu belirt. Oranı yalnızca gerçek market verisi mevcutsa yaz. Kupon oluşturma.
Önceki sohbet: {json.dumps(history or [], ensure_ascii=False)[:8000]}
"""
    reply = await main_module.gemini_generate(prompt)
    return {"reply": reply, "engine": ENGINE_NAME, "engine_version": "0.1.0", "dates": [d.isoformat() for d in dates],
            "match_count": len(rows), "analyzed_count": len(candidates), "source": "5DollarFootballAPI + football intelligence"}
