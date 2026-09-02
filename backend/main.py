import asyncio
import json
import os
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
Sen Bay Tahmin'sin: basit bir chatbot değil, gerçek maç programı ve gerçek iddaa verisi üzerinden çalışan profesyonel futbol analiz uzmanısın.

ANALİZ KURALLARI:
- Yalnızca sana verilen gerçek API verilerini temel al. Veri içinde olmayan takım formu, sakatlık, xG, hava, oyuncu veya istatistiği varmış gibi uydurma.
- Her tahmini tek bir sinyale göre verme. Mevcut verileri birlikte değerlendir: maç oranları, gol marketleri, ilk yarı marketleri, KG marketleri, BetCount, favori dengesi, takım/lig bilgileri ve verilen diğer gerçek marketler.
- İstenen market doğrudan veride varsa öncelikle o marketin gerçek oranını ve aynı maçtaki ilişkili marketleri kullan.
- İstenen market doğrudan veride yoksa, mevcut gerçek sinyallerden uzman projeksiyonu çıkar. Bu durumda bunun bir model projeksiyonu olduğunu belirt; uydurma oran yazma.
- İlk Yarı 1.5 Üst gibi bir tahminde özellikle ilk yarı Alt/Üst marketlerini, ilk yarı sonuçlarını ve ilgili gol marketlerini kontrol et. Sadece MS oranından ilk yarı sonucu çıkarma.
- KG tahmininde KG marketi varsa onu ve toplam gol/maç sonucu dengesini birlikte değerlendir.
- MS tahmininde 1X2 oranlarını, beraberlik oranını ve ilgili gol marketlerini birlikte değerlendir.
- Alt/Üst tahmininde mevcut toplam gol marketlerini ve bunların birbirleriyle tutarlılığını karşılaştır.
- HT/FT tahmininde ilk yarı ve maç sonucu sinyallerinin birbiriyle tutarlı olmasını ara.
- Oranı düşük diye otomatik olarak "güvenli" deme. Piyasa favorisi ile analitik güveni birbirinden ayır.
- Bir tahmin için veri çelişkiliyse zorla güçlü tahmin üretme; güveni düşür ve riski yükselt.
- Aynı maçı bir sonuç içinde iki kez listeleme. Kullanıcı kaç farklı maç istediyse, yeterli gerçek aday varsa tam o sayıda FARKLI maç ver.
- Her adayda: Maç | Tahmin | Güven (0-10) | Risk | Kısa veri gerekçesi.
- Güven skoru başarı garantisi değildir. 8.5+ güçlü, 7.0-8.4 değerlendirilebilir, 6.0-6.9 riskli, 6 altı zayıf kabul edilir.
- Kullanıcı kombinasyon isterse resmi kupon oluşturma; önerilen tahmin listesi olarak sun.
- Kullanıcıya veritabanı, endpoint, backend, panel veya sistem limiti gibi iç teknik mazeretler anlatma. Gerçek program verisi varsa onun üzerinden analiz yap.
- Veri gerçekten yetersizse bunu açıkça söyle ve sayı/istatistik/maç uydurma.
- Türkçe, net, profesyonel ve kısa ama gerekçeli cevap ver.
"""

@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "mode": "expert"}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "nosyapi_configured": bool(NOSYAPI_KEY),
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "analysis_cache_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
    }

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

def compact_data(value, max_chars=40000):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:max_chars]
    except Exception:
        return str(value)[:max_chars]

def extract_rows(payload):
    """NOSYAPI cevabındaki maç listesini şekilden bağımsız çıkar."""
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
        if key:
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)
    return unique

def match_name(row):
    return str(row.get("Teams") or f"{row.get('Team1', '')} - {row.get('Team2', '')}").strip(" -")

def match_date(row):
    return str(row.get("Date") or row.get("date") or "")

def slim_match(row):
    """Gemini'ye maçın karar vermede işe yarayan gerçek alanlarını ver."""
    preferred = (
        "MatchID", "Date", "Time", "DateTime", "Country", "League", "Teams", "Team1", "Team2",
        "BetCount", "HomeWin", "Draw", "AwayWin", "Under15", "Over15", "Under25", "Over25",
        "Under35", "Over35", "KGVar", "KGYok", "BothTeamsToScore", "FirstHalfHomeWin",
        "FirstHalfDraw", "FirstHalfAwayWin", "FirstHalfUnder05", "FirstHalfOver05",
        "FirstHalfUnder15", "FirstHalfOver15", "HTUnder15", "HTOver15"
    )
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
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    body = {"match_key": str(match_id), "match_id": match_id, "analysis": json.dumps(analysis, ensure_ascii=False)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{SUPABASE_URL}/rest/v1/ai_predictions", headers=headers, params={"on_conflict": "match_key"}, json=body)
    except httpx.HTTPError:
        pass

async def gemini_generate(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable bulunamadı.")

    def generate_sync() -> str:
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
Alanlar: mac_ozeti, takimlarin_durumu, olasi_senaryo, ms_tahmini, kg_tahmini,
alt_ust_tahmini, ilk_yari_tahmini, ht_ft_tahmini, surpriz_ihtimali,
en_guvenilir_tahminler, risk_seviyesi, tahmin_gerekcesi.
Her tahmin nesnesinde tahmin, guven ve risk alanları kullan. Tahmin alanını ASLA nesne olarak tekrar iç içe koyma.
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
Şu anda MAÇ ÖZEL MODUNDASIN. Kullanıcının sorusunu seçili maç bağlamında uzman gibi yanıtla.
SEÇİLİ MAÇ GERÇEK API VERİSİ:
{compact_data(match_data)}
ÖNCEKİ SOHBET:
{compact_data(history, 12000)}
KULLANICI SORUSU:
{message}"""
    return {"reply": await gemini_generate(prompt)}

async def get_weekly_matches(days: int = 7):
    today = datetime.now().date()
    weekly = []
    for offset in range(days):
        date = (today + timedelta(days=offset)).isoformat()
        try:
            weekly.append({"date": date, "matches": await get_matches(date)})
        except HTTPException:
            weekly.append({"date": date, "matches": []})
    return weekly

async def get_today_expert_pool():
    """Genel sohbet için yalnızca bugünün gerçek programını hazırlar ve tekrarları temizler."""
    today = datetime.now().date().isoformat()
    dated = await get_matches(today)
    rows = dedupe_rows(extract_rows(dated))

    # Bazı servis cevaplarında tarih filtresi beklenmedik biçimde dar dönebilir.
    # İkinci çağrı yalnızca aday sayısı yetersizse yapılır; yine MatchID ile tekilleştirilir.
    if len(rows) < 5:
        current = await get_matches()
        current_rows = dedupe_rows(extract_rows(current))
        merged = rows[:]
        known = {match_identity(x) for x in merged if match_identity(x)}
        for row in current_rows:
            key = match_identity(row)
            row_date = match_date(row)
            if key and key in known:
                continue
            if row_date and row_date != today:
                continue
            merged.append(row)
            if key:
                known.add(key)
        rows = dedupe_rows(merged)

    return rows

async def build_expert_candidates(rows, detail_limit=12):
    """Liste oranlarını + sınırlı sayıda tam market detayını tek bir analiz paketi yap."""
    rows = dedupe_rows(rows)
    # Önce gerçek liste verisini kullan. Detay çağrılarını maç sayısını şişirmemek için sınırla.
    candidates = [slim_match(row) for row in rows]
    detail_rows = rows[:detail_limit]

    async def fetch_detail(row):
        key = match_identity(row)
        if not key:
            return None
        try:
            detail = await get_match_detail(int(key))
            return {"MatchID": key, "Teams": match_name(row), "detail": detail}
        except HTTPException:
            return {"MatchID": key, "Teams": match_name(row), "detail": None}

    details = await asyncio.gather(*(fetch_detail(row) for row in detail_rows))
    details = [item for item in details if item]
    return {"today": datetime.now().date().isoformat(), "match_count": len(rows), "matches": candidates, "detailed_market_samples": details}

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
    expert_pool = await build_expert_candidates(today_rows)
    prompt = f"""{EXPERT_CORE}
ŞU ANDA GENEL BAY TAHMİN MODUNDASIN.

Kullanıcı maç detayına girmeden bugünün gerçek programından tahmin istiyor. Önce aşağıdaki aday havuzunu analiz et.
Bu havuz NOSYAPI'nin bugünkü gerçek futbol programından alınmıştır ve MatchID ile tekilleştirilmiştir.
Aynı MatchID'yi ikinci kez önerme.

ÇOK ÖNEMLİ SEÇİM KURALI:
- Kullanıcı sayı belirttiyse ve havuzda yeterli farklı maç varsa TAM OLARAK o sayıda farklı maç döndür.
- Örneğin "bugünün ilk yarı 1,5 üst 5 maçını ver" denirse 5 farklı maçı karşılaştır, en güçlü 5 adayı sırala.
- Önce doğrudan ilgili marketi ara. Tam market detayı verilmişse onu önceliklendir.
- İlgili marketin tam detayı yoksa mevcut gerçek oranlar ve ilişkili marketlerden projeksiyon yap.
- Adayları yalnızca oranı düşük diye seçme; marketler arası tutarlılık, gol sinyalleri, favori dengesi ve mevcut gerçek verilerin birlikte verdiği tabloyu değerlendir.
- Bir maç için güçlü veri yoksa onu üst sıralara zorla koyma.
- Uydurma oran, form, xG veya istatistik ekleme.
- Kullanıcıya teknik sistem açıklaması yapma.
- Sonuçları numaralı ve karşılaştırılabilir ver: Maç | Tahmin | Güven | Risk | Kısa veri gerekçesi.

ÖNCEKİ SOHBET:
{compact_data(history, 12000)}

BUGÜNÜN GERÇEK MAÇ HAVUZU:
{compact_data(expert_pool, 60000)}

KULLANICI SORUSU:
{message}"""
    return {"reply": await gemini_generate(prompt)}
