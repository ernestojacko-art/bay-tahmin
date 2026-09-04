import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

import five_dollar_bridge as five


ISTANBUL = ZoneInfo("Europe/Istanbul")
MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
    "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
    "kasim": 11, "aralık": 12, "aralik": 12,
}


def today_local() -> date:
    return datetime.now(ISTANBUL).date()


def resolve_requested_date(message: str) -> tuple[date, str]:
    """Resolve Turkish relative/calendar date phrases before any data request."""
    text = re.sub(r"\s+", " ", str(message or "").strip().lower())
    today = today_local()

    if re.search(r"\b(öbür gün|obur gun|öbürgun|oburgun)\b", text):
        return today + timedelta(days=2), "öbür gün"
    if re.search(r"\b(yarın|yarin)\b", text):
        return today + timedelta(days=1), "yarın"
    if re.search(r"\b(bugün|bugun|bugünkü|bugunku)\b", text):
        return today, "bugün"

    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day), m.group(0)
        except ValueError:
            pass

    for name, month in MONTHS.items():
        m = re.search(rf"\b(\d{{1,2}})\s+{re.escape(name)}(?:\s+(\d{{4}}))?\b", text)
        if m:
            year = int(m.group(2)) if m.group(2) else today.year
            try:
                return date(year, month, int(m.group(1))), m.group(0)
            except ValueError:
                pass

    return today, "bugün"


def local_day_window(target: date) -> tuple[int, int]:
    start = datetime.combine(target, datetime.min.time(), tzinfo=ISTANBUL)
    end = start + timedelta(days=1)
    return int(start.astimezone(timezone.utc).timestamp()), int(end.astimezone(timezone.utc).timestamp())


async def get_matches_for_local_date(target: date):
    start, end = local_day_window(target)
    payload = await five._get("fixtures", {
        "start_time": start,
        "end_time": end,
        "status": "all",
        "lang": "en",
    })
    rows = [five._fixture_row(x) for x in (payload.get("data") or [])]
    # Hard post-filter: kickoff must belong to the requested Istanbul calendar date.
    filtered = []
    seen = set()
    for row in rows:
        match_id = str(row.get("MatchID") or "")
        kickoff = row.get("KickoffUTC") or row.get("Date") or ""
        try:
            local_date = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")).astimezone(ISTANBUL).date()
        except ValueError:
            continue
        if local_date != target or not match_id or match_id in seen:
            continue
        seen.add(match_id)
        filtered.append(row)
    return filtered


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def requested_count(message: str) -> int:
    m = re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b", _norm(message))
    return max(1, min(int(m.group(1)), 20)) if m else 5


def wants_surprise(message: str) -> bool:
    return bool(re.search(r"\b(sürpriz|surpriz)\b", _norm(message)))


def wants_iyms(message: str) -> bool:
    text = _norm(message)
    return bool(re.search(r"iy\s*/?\s*ms|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu", text))


def market_probability(odds):
    raw = {x["value"]: 1.0 / x["odd"] for x in odds if x.get("odd", 0) > 0}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()} if total else {}


def rank_market(market):
    probs = market_probability(market.get("odds", []))
    ranked = sorted(
        ((o["value"], o["odd"], probs.get(o["value"], 0.0)) for o in market.get("odds", [])),
        key=lambda x: x[2], reverse=True,
    )
    return ranked


def market_score(market, message):
    ranked = rank_market(market)
    if not ranked:
        return None
    top_value, top_odd, top_prob = ranked[0]
    name = _norm(market.get("gameName"))
    if wants_iyms(message) and "ilk yarı maç sonucu" not in name and "iy/ms" not in name:
        return None
    if wants_surprise(message):
        alternatives = [x for x in ranked if _norm(x[0]) not in {"1", "x", "2"}]
        chosen = alternatives[0] if alternatives else None
        if not chosen:
            return None
        return chosen[2] * 100
    preferred = 0
    if "maç sonucu" in name or "1x2" in name:
        preferred = 8
    elif "karşılıklı gol" in name or "kg" in name:
        preferred = 6
    elif "alt/üst gol" in name:
        preferred = 5
    return top_prob * 100 + preferred


async def inspect_pool(rows):
    import asyncio
    async def inspect(row):
        try:
            detail = await five.get_match_detail(int(row["MatchID"]))
            return {"match": row, "markets": detail.get("markets", []), "detail": detail}
        except Exception:
            return None
    results = await asyncio.gather(*(inspect(r) for r in rows))
    return [x for x in results if x]


def choose_best(pool, message, count):
    candidates = []
    for item in pool:
        best = None
        for market in item.get("markets", []):
            score = market_score(market, message)
            if score is None:
                continue
            ranked = rank_market(market)
            if not ranked:
                continue
            if wants_surprise(message):
                alt = [x for x in ranked if _norm(x[0]) not in {"1", "x", "2"}]
                if not alt:
                    continue
                selection, odd, probability = alt[0]
            else:
                selection, odd, probability = ranked[0]
            candidate = {
                "match_id": str(item["match"]["MatchID"]),
                "match": item["match"].get("Teams") or "Maç",
                "kickoff": item["match"].get("KickoffUTC") or item["match"].get("Date"),
                "market": market.get("gameName"),
                "selection": selection,
                "odd": odd,
                "market_probability": round(probability * 100, 2),
                "score": round(score, 2),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        if best:
            candidates.append(best)
    candidates.sort(key=lambda x: (x["score"], x["market_probability"]), reverse=True)
    selected, used = [], set()
    for c in candidates:
        if c["match_id"] in used:
            continue
        selected.append(c)
        used.add(c["match_id"])
        if len(selected) >= count:
            break
    return selected


def build_prompt(message, target, label, selections, pool_size):
    return f"""Sen Bay Tahmin'sin ve gelişmiş Football AI Agent mantığıyla çalışıyorsun.

KATI VERİ KURALI:
- Veri kaynağı yalnızca 5DollarFootballAPI'dir.
- İstenen gün: {target.isoformat()} ({label})
- Bu istekte analiz havuzu yalnızca bu tarihin {pool_size} gerçek maçından oluşuyor.
- Başka tarihten maç eklemek kesinlikle yasaktır.
- Bir market/seçim veri içinde yoksa üretme.
- Güven puanı garanti değildir; açık market ve piyasa olasılığına dayalı değerlendirmedir.
- Kullanıcı sayı istediyse farklı maçlardan en fazla o sayıda seçim döndür.

KULLANICI İSTEĞİ:
{message}

ÖN SIRALAMADAN GEÇEN ADAYLAR:
{selections}

Görevin adayları profesyonelce açıkla ve sıralamayı koru. Türkçe cevap ver. Tarihi özellikle belirt."""


async def general_chat(request: Request, main_module):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    history = payload.get("history") or []
    target, label = resolve_requested_date(message)
    rows = await get_matches_for_local_date(target)
    if not rows:
        return {"reply": f"{target.strftime('%d.%m.%Y')} tarihinde 5DollarFootballAPI'den doğrulanmış maç bulunamadı.", "date": target.isoformat(), "source": "5dollarfootballapi"}

    pool = await inspect_pool(rows)
    count = requested_count(message)
    selections = choose_best(pool, message, count)
    if not selections:
        return {"reply": f"{target.strftime('%d.%m.%Y')} tarihindeki {len(rows)} maç içinde isteğini karşılayan doğrulanmış açık market bulunamadı.", "date": target.isoformat(), "source": "5dollarfootballapi"}

    prompt = build_prompt(message, target, label, selections, len(rows))
    if history:
        prompt += f"\nÖNCEKİ SOHBET BAĞLAMI:\n{main_module.compact_data(history, 8000)}"
    reply = await main_module.gemini_generate(prompt)
    return {
        "reply": reply,
        "date": target.isoformat(),
        "date_label": label,
        "match_count": len(rows),
        "analyzed_count": len(pool),
        "source": "5dollarfootballapi",
        "agent": "FootballAgent / FootballAgentOrchestrator / FootballChatAgent",
    }


def patch_main(m):
    """Replace the legacy /chat route with the date-aware Agent adapter."""
    import sys
    from fastapi.routing import APIRoute

    m.app.router.routes = [
        r for r in m.app.router.routes
        if not (isinstance(r, APIRoute) and r.path == "/chat" and "POST" in (r.methods or set()))
    ]

    async def route(request: Request):
        return await general_chat(request, m)

    m.app.add_api_route("/chat", route, methods=["POST"])
    # Keep the existing bridge functions available to all legacy endpoints.
    m.get_matches = five.get_matches
    m.get_match_detail = five.get_match_detail
    m.get_match_detail_alias = five.get_match_detail
    m.inspect_match = lambda row: None
    sys.modules[m.__name__].get_matches = five.get_matches
    sys.modules[m.__name__].get_match_detail = five.get_match_detail
