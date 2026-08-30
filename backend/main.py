import os
import json
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

NOSYAPI_BASE_URL = os.getenv(
    "NOSYAPI_BASE_URL",
    "https://www.nosyapi.com/apiv2/service"
).rstrip("/")
NOSYAPI_KEY = os.getenv("NOSYAPI_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


@app.get("/")
def root():
    return {"status": "online", "agent": "Bay Tahmin", "message": "Bay Tahmin API çalışıyor."}


@app.get("/health")
def health():
    return {"status": "healthy", "nosyapi_configured": bool(NOSYAPI_KEY), "gemini_configured": bool(GEMINI_API_KEY)}


async def nosy_get(path: str, params: dict):
    if not NOSYAPI_KEY:
        raise HTTPException(status_code=500, detail="NOSYAPI_KEY environment variable bulunamadı.")

    params = {**params, "apiKey": NOSYAPI_KEY}
    url = f"{NOSYAPI_BASE_URL}/{path.lstrip('/') }"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"NOSYAPI bağlantı hatası: {exc}") from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"NOSYAPI isteği başarısız oldu ({response.status_code}): {response.text[:500]}"
        )

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
    # Frontend bu endpointi opsiyonel filtreler için kullanır.
    # NOSYAPI'nin maç programı zaten gerçek lig adını taşıdığı için
    # burada sahte veri üretmiyoruz.
    return []


async def build_ai_analysis(match_id: int):
    if not GEMINI_API_KEY or not gemini_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable bulunamadı.")

    match_data = await get_match_detail(match_id)
    prompt = f"""
Sen "Bay Tahmin AI" adlı profesyonel futbol maç analiz asistanısın.
Aşağıdaki veri gerçek NOSYAPI maç verisidir. Veride bulunmayan bilgiyi uydurma.

MAÇ VERİSİ:
{json.dumps(match_data, ensure_ascii=False, default=str)}

Bu maçı profesyonel şekilde analiz et. MS, KG, Alt/Üst, İlk Yarı,
HT/FT, olası senaryo, en güvenilir tahminler ve risk seviyesini değerlendir.
Kesinlik garantisi verme; yalnızca mevcut veriye dayalı olasılık/risk yorumu yap.

Yanıtı yalnızca JSON olarak ve Türkçe ver.
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return json.loads(response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI analiz servisi hatası: {exc}") from exc


@app.get("/ai/analyze/{match_id}")
async def analyze_match_with_ai(match_id: int):
    analysis = await build_ai_analysis(match_id)
    return {"status": "success", "matchID": match_id, "analysis": analysis}


@app.post("/matches/{match_id}/chat")
async def chat_about_match(match_id: int, request: Request):
    """Seçili maç için Bay Tahmin sohbet endpointi.

    Frontend'in mevcut BayTahminChat bileşeni bu endpointi çağırır.
    Sohbet geçmişi yalnızca bağlam olarak kullanılır; cevap seçili maçın
    gerçek NOSYAPI verisine dayanır.
    """
    if not GEMINI_API_KEY or not gemini_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable bulunamadı.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    message = str(payload.get("message") or "").strip()
    history = payload.get("history") or []
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")

    match_data = await get_match_detail(match_id)
    history_text = json.dumps(history[-8:], ensure_ascii=False, default=str)
    match_text = json.dumps(match_data, ensure_ascii=False, default=str)

    prompt = f"""
Sen Bay Tahmin'in seçili maç için çalışan profesyonel futbol Chat Agent'ısın.
Yalnızca aşağıdaki gerçek maç verisini ve sohbet geçmişini kullan.
Veride olmayan istatistik, sakatlık, oran veya sonucu uydurma.
Kullanıcının sorusuna doğrudan Türkçe cevap ver. Tahmin varsa gerekçesini
ver ve kesinlik garantisi verme.

SEÇİLİ MAÇ ID: {match_id}
GERÇEK MAÇ VERİSİ:
{match_text}

SON SOHBET GEÇMİŞİ:
{history_text}

KULLANICI SORUSU:
{message}
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        reply = (response.text or "").strip()
        if not reply:
            raise ValueError("AI boş yanıt döndürdü.")
        return {"status": "success", "reply": reply, "matchID": match_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Chat AI servisi hatası: {exc}") from exc
