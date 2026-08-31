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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "ai_engine": "Football AI Agent", "message": "Bay Tahmin API çalışıyor."}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "nosyapi_configured": bool(NOSYAPI_KEY),
        "gemini_configured": bool(GEMINI_API_KEY),
        "ai_engine": "Gemini",
        "gemini_model": GEMINI_MODEL,
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
    return {"analysis": analysis}

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
