import asyncio
import json
import os
import re
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai

app = FastAPI(title="Bay Tahmin Expert API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

NOSYAPI_BASE_URL = os.getenv("NOSYAPI_BASE_URL", "https://www.nosyapi.com/apiv2/service").rstrip("/")
NOSYAPI_KEY = os.getenv("NOSYAPI_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

EXPERT_CORE = """
Sen Bay Tahmin'sin. Gerçek İddaa programı ve gerçek açık market verileriyle çalışan futbol analiz ajanısın.

KESİN KURALLAR:
- Yalnızca verilen gerçek API verilerini kullan; form, xG, sakatlık, oyuncu, hava veya istatistik uydurma.
- Bir tahminin marketi gerçekten açık değilse o tahmini ASLA verme.
- Kapalı, sıfır oranlı veya veri içinde bulunmayan bir seçeneği açık market gibi gösterme.
- Bir maçta sadece gerçekten açık olan seçenekler arasından seçim yap.
- Kullanıcı sayı verdiyse aynı MatchID'yi tekrarlama; yeterli gerçek aday yoksa sayı doldurma.
- Piyasa olasılığı yalnızca seçimin bulunduğu gerçek marketin açık seçeneklerinden hesaplanır.
- "Karma kombinasyon" isteğinde amaç en düşük oranlı 7 seçimi kopyalamak değildir. Açık marketler arasından farklı market türlerini, piyasa gücünü, veri uyumunu ve riski birlikte değerlendir.
- İY/MS isteğinde yalnızca gerçek İY/MS marketindeki seçenekleri kullan. 1/1 ve 2/2 düz favorileri sürpriz olarak adlandırma.
- Sürpriz İY/MS için X/1, X/2, 1/X, 2/X, 1/2, 2/1 gibi ilk yarı ile maç sonucunun farklı olduğu gerçek seçenekleri değerlendir.
- Güven puanı başarı garantisi değildir.
- Türkçe, net ve profesyonel cevap ver.
"""

@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "mode": "expert"}

@app.get("/health")
def health():
    return {"status": "healthy", "nosyapi_configured": bool(NOSYAPI_KEY), "gemini_configured": bool(GEMINI_API_KEY), "gemini_model": GEMINI_MODEL, "analysis_cache_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)}

async def nosy_get(path: str, params: dict):
    if not NOSYAPI_KEY:
        raise HTTPException(status_code=500, detail="NOSYAPI_KEY environment variable bulunamadı.")
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(f"{NOSYAPI_BASE_URL}/{path.lstrip('/')}", params={**params, "apiKey": NOSYAPI_KEY})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"NOSYAPI bağlantı hatası: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"NOSYAPI isteği başarısız oldu ({response.status_code}): {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="NOSYAPI geçerli JSON döndürmedi.") from exc

@app.get("/matches")
async def get_matches(date: str | None = None):
    return await nosy_get("bettable-matches", {"type": 1, **({"date": date} if date else {})})

@app.get("/mac/{match_id}")
async def get_match_detail(match_id: int):
    return await nosy_get("bettable-matches/details", {"matchID": match_id})

@app.get("/match/{match_id}")
async def get_match_detail_alias(match_id: int):
    return await get_match_detail(match_id)

@app.get("/leagues")
def get_leagues():
    return []

def compact_data(value, max_chars=60000):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:max_chars]
    except Exception:
        return str(value)[:max_chars]

def extract_rows(payload):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "matches", "results", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        for value in payload.values():
            if isinstance(value, dict):
                rows = extract_rows(value)
                if rows:
                    return rows
    return []

def match_identity(row):
    return str(row.get("MatchID") or row.get("matchID") or row.get("id") or row.get("Id") or "")

def dedupe_rows(rows):
    seen, unique = set(), []
    for row in rows:
        key = match_identity(row)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique

def match_name(row):
    return str(row.get("Teams") or f"{row.get('Team1', '')} - {row.get('Team2', '')}").strip(" -")

def match_date(row):
    return str(row.get("Date") or row.get("date") or "")

def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())

def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)

def extract_markets(detail):
    """NOSYAPI detayındaki yalnızca gerçek, pozitif oranı bulunan açık marketleri çıkarır."""
    if detail is None:
        return []
    markets = []
    seen = set()
    for item in walk_dicts(detail):
        name = item.get("gameName") or item.get("GameName") or item.get("name")
        odds = item.get("odds") or item.get("Odds") or item.get("gameOdds")
        if not name or not isinstance(odds, list):
            continue
        clean = []
        for odd in odds:
            if not isinstance(odd, dict):
                continue
            value = odd.get("value")
            price = odd.get("odd")
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if value not in (None, "") and price > 0:
                clean.append({"value": str(value).strip(), "odd": price})
        if not clean:
            continue
        key = normalize_text(name)
        if key in seen:
            continue
        seen.add(key)
        markets.append({"gameName": str(name).strip(), "type": str(item.get("type") or ""), "odds": clean})
    return markets

def find_iyms_market(detail):
    candidates = []
    for market in extract_markets(detail):
        name = normalize_text(market["gameName"])
        compact = name.replace(" ", "")
        if (("ilk yarı" in name and "maç sonucu" in name) or "ilk yarı/maç sonucu" in name or "ilk yarı-maç sonucu" in name or "iy/ms" in compact or "iyms" in compact):
            candidates.append(market)
    return max(candidates, key=lambda x: len(x["odds"])) if candidates else None

def market_probability(odds):
    raw = {x["value"]: 1.0 / x["odd"] for x in odds if x.get("odd", 0) > 0}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()} if total else {}

def is_straight_result(value):
    compact = normalize_text(value).replace(" ", "")
    return compact in {"1/1", "2/2", "1-1", "2-2", "1:1", "2:2"}

def is_surprise_result(value):
    compact = normalize_text(value).replace(" ", "")
    return compact in {"x/1", "x/2", "1/x", "2/x", "1/2", "2/1", "x-1", "x-2", "1-x", "2-x", "1:2", "2:1"}

def parse_iyms_market(market):
    probs = market_probability(market["odds"])
    options = [{"value": x["value"], "odd": x["odd"], "probability": round(probs.get(x["value"], 0) * 100, 2), "surprise": is_surprise_result(x["value"]) and not is_straight_result(x["value"])} for x in market["odds"]]
    surprise = sorted([x for x in options if x["surprise"]], key=lambda x: x["probability"], reverse=True)
    return {"market_name": market["gameName"], "options": options, "surprise_options": surprise}

def slim_match(row):
    keys = ("MatchID", "Date", "Time", "DateTime", "Country", "League", "Teams", "Team1", "Team2", "BetCount")
    return {key: row[key] for key in keys if key in row and row[key] not in (None, "")}

def market_payload(markets, limit=160):
    """Gemini'ye yalnızca açık marketleri ve gerçek seçeneklerini verir."""
    preferred_words = ("maç sonucu", "karşılıklı gol", "alt/üst", "ilk yarı", "iy/ms", "ilk yarı/maç sonucu")
    ordered = sorted(markets, key=lambda m: (0 if any(w in normalize_text(m["gameName"]) for w in preferred_words) else 1, m["gameName"]))
    return ordered[:limit]

async def cache_get(match_id: int):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    headers = {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{SUPABASE_URL}/rest/v1/ai_predictions", headers=headers, params={"match_key": f"eq.{match_id}", "select": "analysis", "limit": "1"})
        if response.status_code == 200:
            rows = response.json()
            if rows and rows[0].get("analysis"):
                raw = rows[0]["analysis"]
                return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    return None

async def cache_put(match_id: int, analysis: dict):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return
    headers = {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    body = {"match_key": str(match_id), "match_id": match_id, "analysis": json.dumps(analysis, ensure_ascii=False)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{SUPABASE_URL}/rest/v1/ai_predictions", headers=headers, params={"on_conflict": "match_key"}, json=body)
    except httpx.HTTPError:
        pass

async def gemini_generate(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable bulunamadı.")
    def generate_sync():
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            return (response.text or "").strip()
        finally:
            client.close()
    try:
        text = await asyncio.to_thread(generate_sync)
        if not text:
            raise HTTPException(status_code=502, detail="Gemini boş yanıt döndürdü.")
        return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini üretim hatası: {exc}") from exc

@app.get("/ai/analyze/{match_id}")
async def analyze_match_with_ai(match_id: int):
    cached = await cache_get(match_id)
    if cached is not None:
        return {"analysis": cached, "source": "cache"}
    match_data = await get_match_detail(match_id)
    prompt = f"""{EXPERT_CORE}
Bu seçili maç için kapsamlı uzman analizi üret. Yanıtı geçerli JSON olarak ver.
Alanlar: mac_ozeti, takimlarin_durumu, olasi_senaryo, ms_tahmini, kg_tahmini, alt_ust_tahmini, ilk_yari_tahmini, ht_ft_tahmini, surpriz_ihtimali, en_guvenilir_tahminler, risk_seviyesi, tahmin_gerekcesi.
Her tahmin nesnesinde tahmin, guven ve risk alanları kullan. Tahmin alanını iç içe nesne yapma.
SEÇİLİ MAÇ GERÇEK API VERİSİ:
{compact_data(match_data)}"""
    raw = await gemini_generate(prompt)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        analysis = json.loads(cleaned)
    except json.JSONDecodeError:
        analysis = {"mac_ozeti": cleaned, "tahmin_gerekcesi": cleaned}
    await cache_put(match_id, analysis)
    return {"analysis": analysis, "source": "gemini"}

async def get_chat_match_context(match_id: int):
    return await get_match_detail(match_id)

@app.post("/matches/{match_id}/chat")
async def chat_about_match(match_id: int, request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    history = payload.get("history") or []
    match_data = await get_chat_match_context(match_id)
    prompt = f"""{EXPERT_CORE}
MAÇ ÖZEL MODU.
GERÇEK MAÇ API VERİSİ:
{compact_data(match_data)}
ÖNCEKİ SOHBET:
{compact_data(history, 12000)}
KULLANICI SORUSU:
{message}
Yalnızca gerçek API verisindeki açık marketleri kullan."""
    return {"reply": await gemini_generate(prompt)}

async def get_today_expert_pool():
    today = datetime.now().date().isoformat()
    rows = dedupe_rows(extract_rows(await get_matches(today)))
    if len(rows) < 5:
        current = dedupe_rows(extract_rows(await get_matches()))
        known = {match_identity(x) for x in rows if match_identity(x)}
        for row in current:
            key = match_identity(row)
            if key and key in known:
                continue
            if match_date(row) and match_date(row) != today:
                continue
            rows.append(row)
            if key:
                known.add(key)
    return dedupe_rows(rows)

async def inspect_match(row):
    key = match_identity(row)
    if not key:
        return None
    try:
        detail = await get_match_detail(int(key))
    except HTTPException:
        return None
    markets = extract_markets(detail)
    iyms = find_iyms_market(detail)
    parsed_iyms = parse_iyms_market(iyms) if iyms else None
    return {
        "match": slim_match(row),
        "markets": market_payload(markets),
        "iyms_market_open": bool(parsed_iyms),
        "iyms": parsed_iyms,
    }

async def build_market_aware_pool(rows):
    inspected = await asyncio.gather(*(inspect_match(row) for row in dedupe_rows(rows)))
    return [x for x in inspected if x]

def wants_iyms(message):
    text = normalize_text(message)
    return bool(re.search(r"iy\s*/?\s*ms|i[yı]\s*[/\\-]\s*m[sş]|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu", text))

def requested_count(message):
    m = re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b", normalize_text(message))
    return int(m.group(1)) if m else None

def parse_json_response(raw):
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None

def validate_selections(result, pool, requested):
    if not isinstance(result, dict) or not isinstance(result.get("selections"), list):
        return None
    by_match = {str(x["match"]["MatchID"]): x for x in pool if x.get("match", {}).get("MatchID") is not None}
    valid = []
    used = set()
    for item in result["selections"]:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("match_id") or "")
        market_name = normalize_text(item.get("market") or "")
        selection = str(item.get("selection") or "").strip()
        if not mid or mid not in by_match or mid in used or not market_name or not selection:
            continue
        match = by_match[mid]
        found_market = None
        for market in match.get("markets", []):
            if normalize_text(market.get("gameName")) == market_name:
                found_market = market
                break
        if not found_market:
            continue
        found_option = next((o for o in found_market.get("odds", []) if normalize_text(o.get("value")) == normalize_text(selection)), None)
        if not found_option:
            continue
        valid.append({"match_id": mid, "match": match_name(match["match"]), "market": found_market["gameName"], "selection": found_option["value"], "odd": found_option["odd"], "confidence": item.get("confidence", ""), "risk": item.get("risk", ""), "reason": item.get("reason", "")})
        used.add(mid)
    if requested and len(valid) < requested:
        return None
    return valid

def format_validated(result, requested):
    intro = result.get("intro") if isinstance(result, dict) else None
    lines = [intro or "Bugünün gerçek açık marketleri karşılaştırılarak oluşturulan Bay Tahmin analizi:"]
    for i, x in enumerate(result["valid_selections"], 1):
        lines.append(f"\n### {i}. {x['match']} (MatchID: {x['match_id']})")
        lines.append(f"- **Market:** {x['market']}")
        lines.append(f"- **Tahmin:** {x['selection']}")
        lines.append(f"- **Gerçek Oran:** {x['odd']}")
        if x.get("confidence"):
            lines.append(f"- **Güven:** {x['confidence']}")
        if x.get("risk"):
            lines.append(f"- **Risk:** {x['risk']}")
        if x.get("reason"):
            lines.append(f"- **Veri gerekçesi:** {x['reason']}")
    return "\n".join(lines)

@app.post("/chat")
async def general_chat(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    history = payload.get("history") or []
    requested = requested_count(message)
    pool = await build_market_aware_pool(await get_today_expert_pool())

    if wants_iyms(message):
        pool = [x for x in pool if x.get("iyms_market_open")]

    if not pool:
        return {"reply": "Bugünün gerçek bülteninde bu isteği karşılayacak doğrulanmış açık market adayı bulunamadı. Marketi kapalı bir seçeneği tahmin gibi göstermiyorum."}

    prompt = f"""{EXPERT_CORE}
Aşağıdaki pool SADECE gerçek İddaa detay API'sinden alınmış açık marketleri içerir. Bir market veya seçenek pool içinde yoksa KAPALI/YOK kabul et.

KULLANICI İSTEĞİ:
{message}

BUGÜNÜN DOĞRULANMIŞ MARKET HAVUZU:
{compact_data(pool)}

ÖNCEKİ SOHBET:
{compact_data(history, 10000)}

GÖREV:
- Kullanıcının istediği sayıda farklı maç seç.
- "Karma kombinasyon" ise sadece oranı en düşük olanları kopyalama; farklı açık market türlerini ve veri uyumunu değerlendir.
- Her seçim için market adı ve seçim değeri pool'daki gerçek metinle birebir eşleşmeli.
- Gerçek markette olmayan hiçbir seçimi üretme.
- Kullanıcı özel olarak İY/MS veya sürpriz istiyorsa yalnızca gerçek İY/MS marketini kullan; 1/1 ve 2/2'yi sürpriz sayma.
- Güven ve gerekçe yalnızca pool'daki verilere dayanmalı.
- Yeterli aday yoksa daha fazla seçim uydurma.

SADECE şu JSON biçiminde cevap ver:
{{"intro":"kısa açıklama","selections":[{{"match_id":"...","market":"pool'daki tam market adı","selection":"pool'daki tam seçim değeri","confidence":"...","risk":"...","reason":"..."}}]}}
"""

    parsed = parse_json_response(await gemini_generate(prompt))
    valid = validate_selections(parsed, pool, requested) if parsed else None
    if valid is None:
        # Doğrulama başarısızsa kapalı marketi kullanıcıya göstermek yerine güvenli şekilde tekrar iste.
        retry = f"""{EXPERT_CORE}
Yalnızca aşağıdaki doğrulanmış açık marketlerden seçim yap. Her market ve seçim birebir eşleşmek zorunda.
İSTEK: {message}
HAVUZ: {compact_data(pool)}
JSON: {{\"intro\":\"...\",\"selections\":[{{\"match_id\":\"...\",\"market\":\"tam market adı\",\"selection\":\"tam seçim\",\"confidence\":\"...\",\"risk\":\"...\",\"reason\":\"...\"}}]}}"""
        parsed = parse_json_response(await gemini_generate(retry))
        valid = validate_selections(parsed, pool, requested) if parsed else None
    if valid is None:
        return {"reply": "Bay Tahmin bu isteği gerçek açık marketlerle doğrulayamadı; kapalı veya bulunmayan bir bahsi tahmin gibi göstermiyorum. Lütfen isteği tekrar deneyin."}
    parsed["valid_selections"] = valid
    return {"reply": format_validated(parsed, requested)}
