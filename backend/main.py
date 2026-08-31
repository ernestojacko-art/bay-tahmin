import json
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google import genai

app = FastAPI(title="Bay Tahmin API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

NOSYAPI_BASE_URL = os.getenv("NOSYAPI_BASE_URL", "https://www.nosyapi.com/apiv2/service").rstrip("/")
NOSYAPI_KEY = os.getenv("NOSYAPI_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# 3.5 Flash-Lite is the current cost-efficient GA model for high-volume workloads.
# If an old 2.5 model is still present in Render env, automatically migrate it.
_configured_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_MODEL = "gemini-3.5-flash-lite" if _configured_model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"} else _configured_model
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "ai_engine": "Gemini", "message": "Bay Tahmin API çalışıyor."}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "nosyapi_configured": bool(NOSYAPI_KEY),
        "gemini_configured": bool(GEMINI_API_KEY),
        "ai_engine": "Gemini",
        "gemini_model": GEMINI_MODEL,
        "analysis_cache_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
    }

async def nosy_get(path: str, params: dict):
    if not NOSYAPI_KEY:
        raise HTTPException(status_code=500, detail="NOSYAPI_KEY environment variable bulunamadı.")
    params = {**params, "apiKey": NOSYAPI_KEY}
    url = f"{NOSYAPI_BASE_URL}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(url, params=params)
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
    params = {"type": 1}
    if date:
        params["date"] = date
    return await nosy_get("bettable-matches", params)

@app.get("/mac/{match_id}")
async def get_match_detail(match_id: int):
    return await nosy_get("bettable-matches/details", {"matchID": match_id})

@app.get("/match/{match_id}")
async def get_match_detail_alias(match_id: int):
    return await get_match_detail(match_id)

@app.get("/leagues")
def get_leagues():
    return []

def compact_data(value, max_chars=30000):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:max_chars]
    except Exception:
        return str(value)[:max_chars]

async def cache_get(match_id: int):
    """Return a cached AI analysis, if Supabase cache is configured."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    url = f"{SUPABASE_URL}/rest/v1/ai_predictions"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    params = {"match_id": f"eq.{match_id}", "select": "analysis,created_at", "limit": "1"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers, params=params)
        if response.status_code != 200:
            return None
        rows = response.json()
        if rows and rows[0].get("analysis"):
            raw = rows[0]["analysis"]
            try:
                return json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return {"mac_ozeti": str(raw)}
    except (httpx.HTTPError, ValueError):
        return None
    return None

async def cache_put(match_id: int, analysis: dict):
    """Persist an AI analysis so subsequent users reuse it."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return
    url = f"{SUPABASE_URL}/rest/v1/ai_predictions"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    body = {
        "match_key": str(match_id),
        "match_id": match_id,
        "analysis": json.dumps(analysis, ensure_ascii=False),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, headers=headers, params={"on_conflict": "match_key"}, json=body)
    except httpx.HTTPError:
        # Cache failure must never break an otherwise valid AI response.
        pass

async def gemini_generate(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable bulunamadı.")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            raise HTTPException(status_code=502, detail="Gemini boş yanıt döndürdü.")
        return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini üretim hatası: {exc}") from exc

ANALYSIS_PROMPT = """
Sen Bay Tahmin adlı profesyonel futbol analiz ajanısın.
Sadece aşağıda verilen seçili maçın gerçek verilerini kullan. Veride olmayan istatistikleri uydurma.
Eksik veri varsa açıkça belirt. Kupon oluşturma veya bahis satışı yapma; yalnızca analiz ve futbol projeksiyonları üret.
Yanıtı SADECE geçerli JSON olarak ver.
Şu alanları üret:
mac_ozeti, takimlarin_durumu, olasi_senaryo, ms_tahmini, kg_tahmini, alt_ust_tahmini,
ilk_yari_tahmini, ht_ft_tahmini, surpriz_ihtimali, en_guvenilir_tahminler (liste),
risk_seviyesi (Düşük/Orta/Yüksek), tahmin_gerekcesi.
"""

@app.get("/ai/analyze/{match_id}")
async def analyze_match_with_ai(match_id: int):
    # 1) Reuse cached analysis whenever available.
    cached = await cache_get(match_id)
    if cached is not None:
        return {"analysis": cached, "source": "cache"}

    # 2) Only call Gemini when this match has no cached analysis.
    match_data = await get_match_detail(match_id)
    prompt = ANALYSIS_PROMPT + "\nSEÇİLİ MAÇ VERİSİ:\n" + compact_data(match_data)
    raw = await gemini_generate(prompt)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        analysis = json.loads(cleaned)
    except json.JSONDecodeError:
        analysis = {"mac_ozeti": cleaned, "tahmin_gerekcesi": cleaned}

    # 3) Save the result for the next user/request.
    await cache_put(match_id, analysis)
    return {"analysis": analysis, "source": "gemini"}

@app.post("/matches/{match_id}/chat")
async def chat_about_match(match_id: int, request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    history = payload.get("history") or []
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")

    # Chat is generated only when the user explicitly asks a question.
    match_data = await get_match_detail(match_id)
    prompt = f"""
Sen Bay Tahmin adlı futbol analiz sohbet ajanısın.
Yalnızca seçili maçın aşağıdaki gerçek verilerine dayanarak Türkçe yanıt ver.
Veri içinde olmayan bir bilgiyi uydurma. Kullanıcı tahmin sorarsa gerekçeli futbol projeksiyonu yap ve belirsizliği belirt.
Kupon oluşturma veya bahis satışı yapma.

SEÇİLİ MAÇ VERİSİ:
{compact_data(match_data)}

ÖNCEKİ SOHBET:
{compact_data(history, 12000)}

KULLANICI SORUSU:
{message}
"""
    reply = await gemini_generate(prompt)
    return {"reply": reply}
