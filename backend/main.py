import asyncio
import json
import os
import re
from datetime import datetime, timedelta

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
Sen Bay Tahmin'sin. Basit bir sohbet botu değil, gerçek İddaa programı ve gerçek market verileriyle çalışan futbol analiz ajanısın.

TEMEL KURAL:
- Yalnızca verilen gerçek API verilerini kullan. Veride olmayan form, xG, sakatlık, oyuncu, hava veya istatistiği uydurma.
- Bir market kullanıcı tarafından açıkça isteniyorsa, o marketin GERÇEKTEN AÇIK olup olmadığını kontrol et.
- İstenen market açık değilse, o maç için sanki market açıkmış gibi tahmin sunma. Kullanıcıya alternatif market uydurma.
- Özellikle İY/MS isteğinde yalnızca gerçek İY/MS (İlk Yarı/Maç Sonucu) marketi bulunan maçları kullan.
- İY/MS marketi bulunmayan maçı İY/MS listesine kesinlikle sokma.
- İY/MS isteğinde 1/1 ve 2/2 gibi düz favori senaryolarını "sürpriz" diye adlandırma. Sürpriz adayları esas olarak X/1, X/2, 1/X, 2/X, 1/2 ve 2/1 gibi ilk yarı ile maç sonunun farklı olduğu senaryolardan seç.
- Kullanıcı "sürpriz olasılığı yüksek" diyorsa önce gerçek İY/MS marketindeki sürpriz senaryoların piyasa olasılığını ve oranını karşılaştır; sadece en güçlü favorileri listeleme.
- Piyasa oranından olasılık çıkarırken oranı 1/oran olarak değerlendir ve mümkünse aynı marketteki tüm açık sonuçlara göre normalize et. Güven puanı başarı garantisi değildir.
- Sürpriz adayını seçerken yalnızca yüksek oranlı olmasına bakma. Daha düşük oranlı ve piyasa tarafından daha olası görülen sürpriz senaryoları öne al; aşırı yüksek oranlı seçenekleri "kuvvetle muhtemel" diye sunma.
- Aynı maç veya MatchID iki kez listelenemez.
- Kullanıcı sayı verdiyse ve yeterli gerçek aday varsa tam o sayıda farklı aday ver. Yeterli gerçek aday yoksa uydurma; mevcut sayıyı dürüstçe bildir.
- Her adayda maç, gerçek marketteki tahmin, gerçek oran, piyasa olasılığı, güven, risk ve kısa veri gerekçesi ver.
- İlgili market açık değilse model projeksiyonunu gerçek İddaa seçeneği gibi gösterme.
- Teknik iç ayrıntıları kullanıcıya mazeret olarak anlatma.
- Türkçe, net ve profesyonel ol.
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

def compact_data(value, max_chars=50000):
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
    seen = set()
    unique = []
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
    markets = []
    for item in walk_dicts(detail):
        name = item.get("gameName") or item.get("GameName") or item.get("name")
        odds = item.get("odds") or item.get("Odds") or item.get("gameOdds")
        if name and isinstance(odds, list):
            clean_odds = []
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
                    clean_odds.append({"value": str(value).strip(), "odd": price})
            if clean_odds:
                markets.append({"gameName": str(name), "type": str(item.get("type") or ""), "odds": clean_odds})
    return markets

def find_iyms_market(detail):
    candidates = []
    for market in extract_markets(detail):
        name = normalize_text(market["gameName"])
        compact = name.replace(" ", "")
        is_iyms = (
            ("ilk yarı" in name and "maç sonucu" in name)
            or "ilk yarı/maç sonucu" in name
            or "ilk yarı-maç sonucu" in name
            or "iy/ms" in compact
            or "iyms" in compact
        )
        if is_iyms:
            candidates.append(market)
    if not candidates:
        return None
    # En dolu gerçek marketi tercih et.
    return max(candidates, key=lambda x: len(x["odds"]))

def market_probability(odds):
    raw = {x["value"]: 1.0 / x["odd"] for x in odds if x.get("odd", 0) > 0}
    total = sum(raw.values())
    if not total:
        return {}
    return {key: value / total for key, value in raw.items()}

def is_straight_result(value):
    compact = normalize_text(value).replace(" ", "")
    return compact in {"1/1", "2/2", "1-1", "2-2", "1:1", "2:2"}

def is_surprise_result(value):
    compact = normalize_text(value).replace(" ", "")
    return compact in {"x/1", "x/2", "1/x", "2/x", "1/2", "2/1", "x-1", "x-2", "1-x", "2-x", "1:2", "2:1"}

def parse_iyms_market(market):
    probs = market_probability(market["odds"])
    options = []
    for item in market["odds"]:
        value = item["value"]
        options.append({"value": value, "odd": item["odd"], "probability": round(probs.get(value, 0) * 100, 2), "surprise": is_surprise_result(value) and not is_straight_result(value)})
    surprise = [x for x in options if x["surprise"]]
    surprise.sort(key=lambda x: x["probability"], reverse=True)
    return {"market_name": market["gameName"], "options": options, "surprise_options": surprise}

def slim_match(row):
    preferred = ("MatchID", "Date", "Time", "DateTime", "Country", "League", "Teams", "Team1", "Team2", "BetCount", "HomeWin", "Draw", "AwayWin", "Under15", "Over15", "Under25", "Over25", "Under35", "Over35")
    return {key: row[key] for key in preferred if key in row and row[key] not in (None, "")}

async def cache_get(match_id: int):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    headers = {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{SUPABASE_URL}/rest/v1/ai_predictions", headers=headers, params={"match_key": f"eq.{match_id}", "select": "analysis,created_at", "limit": "1"})
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
Bu seçili maç için kapsamlı uzman analizi üret.
Yanıtı SADECE geçerli JSON olarak ver.
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
    try:
        return await get_match_detail(match_id)
    except HTTPException as detail_error:
        try:
            listing = await get_matches()
            rows = extract_rows(listing)
            found = next((row for row in rows if match_identity(row) == str(match_id)), None)
            if found is not None:
                return found
        except HTTPException:
            pass
        raise detail_error

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
Şu anda MAÇ ÖZEL MODUNDASIN.
SEÇİLİ MAÇ GERÇEK API VERİSİ:
{compact_data(match_data)}
ÖNCEKİ SOHBET:
{compact_data(history, 12000)}
KULLANICI SORUSU:
{message}"""
    return {"reply": await gemini_generate(prompt)}

async def get_today_expert_pool():
    today = datetime.now().date().isoformat()
    dated = await get_matches(today)
    rows = dedupe_rows(extract_rows(dated))
    if len(rows) < 5:
        current = await get_matches()
        current_rows = dedupe_rows(extract_rows(current))
        known = {match_identity(x) for x in rows if match_identity(x)}
        for row in current_rows:
            key = match_identity(row)
            row_date = match_date(row)
            if key and key in known:
                continue
            if row_date and row_date != today:
                continue
            rows.append(row)
            if key:
                known.add(key)
    return dedupe_rows(rows)

async def build_market_aware_pool(rows):
    """Her maçın tam market detayını kontrol eder; özellikle İY/MS için yalnızca marketi gerçekten açık maçları geçirir."""
    rows = dedupe_rows(rows)

    async def inspect(row):
        key = match_identity(row)
        if not key:
            return None
        try:
            detail = await get_match_detail(int(key))
        except HTTPException:
            detail = None
        iyms = find_iyms_market(detail) if detail is not None else None
        parsed = parse_iyms_market(iyms) if iyms else None
        return {"match": slim_match(row), "iyms_market_open": bool(parsed), "iyms": parsed}

    inspected = await asyncio.gather(*(inspect(row) for row in rows))
    return [x for x in inspected if x]

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
    today_rows = await get_today_expert_pool()

    wants_iyms = bool(re.search(r"iy\s*/?\s*ms|i[yı]\s*[/\\-]\s*m[sş]|ilk\s*yari\s*/?\s*mac\s*sonucu|ilk\s*yari.*mac\s*sonucu", normalize_text(message)))
    if wants_iyms:
        market_pool = await build_market_aware_pool(today_rows)
        eligible = [x for x in market_pool if x["iyms_market_open"] and x.get("iyms", {}).get("surprise_options")]
        # En güçlü gerçek sürpriz seçenekleri önce çıkar; Gemini bunların arasından uzman filtresi yapacak.
        eligible.sort(key=lambda x: max((o["probability"] for o in x["iyms"]["surprise_options"]), default=0), reverse=True)
        pool = eligible
        instruction = """
BU İSTEK İY/MS VE SÜRPRİZ ODAKLI.
SADECE aşağıdaki pool içinde iyms_market_open=true olan maçları kullan.
Her maçın iyms.options alanı İddaa'nın GERÇEK açık İY/MS marketinden alınmıştır.
Kullanıcı sürpriz istiyor: 1/1 ve 2/2 DÜZ senaryolarını sürpriz kabul etme.
Önceliği X/1, X/2, 1/X, 2/X, 1/2, 2/1 gibi ilk yarı ile maç sonunun farklı olduğu gerçek market seçeneklerine ver.
Her maç için önce en olası sürpriz senaryoyu seç. Piyasa olasılığı çok düşük olan yüksek oranlı senaryoyu sırf sürpriz diye öne çıkarma.
"""
    else:
        pool = await build_market_aware_pool(today_rows)
        instruction = """
Kullanıcının istediği marketi önce gerçek detay marketlerinde bul.
Market gerçekten açık değilse o maç için ilgili market tahmini üretme. Doğrudan açık olan marketleri tercih et.
"""

    requested = None
    match_count = re.search(r"\b(\d{1,2})\s*(?:maç|adet|tane)\b", normalize_text(message))
    if match_count:
        requested = int(match_count.group(1))

    prompt = f"""{EXPERT_CORE}
GENEL BAY TAHMİN ANALİZİ.

{instruction}

GERÇEK BUGÜNÜN ADAY HAVUZU:
{compact_data(pool, 60000)}

KULLANICI İSTEĞİ:
{message}

ÖNCEKİ SOHBET:
{compact_data(history, 10000)}

ÇIKTI KURALLARI:
- Kullanıcı sayı verdiyse ve yeterli gerçek aday varsa tam o sayıda FARKLI maç ver.
- İY/MS isteğinde markette gerçekten bulunmayan hiçbir maçı listeleme.
- Sürpriz istenen bir soruda düz 1/1 veya 2/2'yi sürpriz diye sunma.
- Her sonuçta gerçek marketteki tahmin değerini ve gerçek oranını yaz.
- Piyasa olasılığını oranlardan hesapla; uydurma istatistik ekleme.
- Güven puanı, piyasa olasılığından bağımsız bir uzman değerlendirmesi olabilir ama veri dışı gerekçe kullanma.
- Yeterli aday yoksa eksik sayıyı doldurmak için başka marketi veya marketi kapalı maçı kullanma.
"""
    return {"reply": await gemini_generate(prompt)}
