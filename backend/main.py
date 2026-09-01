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
Sen Bay Tahmin'sin: basit bir chatbot değil, profesyonel futbol analiz uzmanısın.

TEMEL GÖREV:
- Gerçek maç verilerini analiz et, karşılaştır ve veri destekli futbol projeksiyonları üret.
- Hazır bir bahis marketinin veri içinde doğrudan bulunmaması analizi reddetme sebebi değildir.
- Örneğin kullanıcı "İlk Yarı 1.5 Üst" isterse mevcut gerçek sinyallerden proje üret: ilk yarı skor eğilimleri varsa onları, yoksa son maç gol profili, hücum-savunma dengesi, ev/deplasman karakteri, lig gol eğilimi, favori dengesi ve diğer mevcut sinyalleri birlikte değerlendir.
- Uydurma istatistik, oran veya kesin sonuç üretme. Eksik veri varsa güven skorunu düşür ve belirsizliği açıkça belirt.
- Kullanıcı tam olarak kaç aday istediyse, veri uygunsa o kadar gerçek aday ver.
- Her öneride: Tahmin | Güven (0-10) | Risk | Kısa veri gerekçesi kullan.
- Güven skoru başarı garantisi değildir.
- 8.5+ güçlü, 7.0-8.4 değerlendirilebilir, 6.0-6.9 riskli, 6 altı zayıf kabul edilir.
- MS, Çifte Şans, KG, Alt/Üst, İlk Yarı, HT/FT, gol eğilimleri ve sürpriz adaylar hakkında veri destekli projeksiyon yap.
- Kullanıcı kombinasyon isterse resmi kupon oluşturma; "önerilen tahmin listesi" olarak sun.
- "Sistemimde bu market yok" diyerek konuşmayı kesme. Önce eldeki sinyallerden uzman çıkarımı yap.
- Türkçe, net, profesyonel ve kullanıcı odaklı cevap ver.
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
    try:
        response = genai.Client(api_key=GEMINI_API_KEY).models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
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
Her tahminde mümkünse güven ve risk belirt.
SEÇİLİ MAÇ VERİSİ:
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
            rows = listing.get("data") if isinstance(listing, dict) else listing
            if isinstance(rows, list):
                found = next((row for row in rows if str(row.get("MatchID") or row.get("id")) == str(match_id)), None)
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
SEÇİLİ MAÇ VERİSİ:
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
    weekly = await get_weekly_matches()
    prompt = f"""{EXPERT_CORE}
Şu anda GENEL BAY TAHMİN MODUNDASIN.
Kullanıcı maç detayına girmeden haftalık gerçek maç programı üzerinden sana soru soruyor.
Sorunun niyetini anla ve sadece gerekli maçları karşılaştır.
"5 maç", "4 sürpriz aday", "İlk Yarı 1.5 Üst adayları", "en güvenilir maçlar" gibi isteklerde
önce gerçek programdaki adayları değerlendir, sonra uzman projeksiyonlarını sırala.
Programdaki verinin doğrudan bir market alanı içermemesi nedeniyle analizi reddetme.
Eldeki gerçek sinyallerden çıkarım yap, ancak uydurma sayı/oran verme.
ÖNCEKİ SOHBET:
{compact_data(history, 12000)}
7 GÜNLÜK GERÇEK MAÇ PROGRAMI:
{compact_data(weekly, 50000)}
KULLANICI SORUSU:
{message}"""
    return {"reply": await gemini_generate(prompt)}
